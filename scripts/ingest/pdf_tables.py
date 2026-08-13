"""Table extraction for policja.pl report PDFs.

These PDFs are produced by a template that renders header/row labels as
90-degree rotated text and draws grid cells as individually-bordered
rectangles. Generic pdfplumber table extraction (`extract_tables()`)
misreads rotated text character-by-character and misdetects bar charts /
text boxes as tables because of stray rect edges. This module rebuilds
tables purely from character positions and rotation matrices instead of
relying on detected grid lines.
"""

import re

import pdfplumber

_LIST_ITEM_RE = re.compile(r"^(?:[•\-\*–]\s*)?(.+?)\s*[-–]\s*([\d.,]+)\s*$")
_LIST_LABEL_MAX_LEN = 60


_SENTENCE_BREAK_RE = re.compile(r"\.\s+[A-ZŁŚŻŹĆŃÓĄĘ]")


def _looks_like_list_label(label):
    """Reject prose sentences that coincidentally end in "word - number"
    (e.g. a wrapped line like "...w sierpniu - 320. Najwięcej dzieci
    zginęło w sierpniu") -- a real list label is short and single-clause.
    A period followed by a capital letter is a real sentence break; a
    period followed by lowercase is just an abbreviation (e.g. "p. pożarowy").
    """
    if len(label) > _LIST_LABEL_MAX_LEN:
        return False
    if _SENTENCE_BREAK_RE.search(label) or label.endswith("."):
        return False
    return True


def find_label_value_lists(text):
    """Detect bulleted or plain "label - number" lists in extracted page
    text (e.g. statystyka.policja.pl narrative reports that present a
    breakdown as a list rather than a ruled table). Returns a list of
    (heading_or_None, [(label, value), ...]) for runs of >= 2 matching lines.
    """
    lists = []
    cur, cur_heading, pending_heading = [], None, None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = _LIST_ITEM_RE.match(line) if line else None
        label = m.group(1).strip() if m else None
        if m and _looks_like_list_label(label):
            cur.append((label, m.group(2).strip()))
            if cur_heading is None:
                cur_heading = pending_heading
        else:
            if len(cur) >= 2:
                lists.append((cur_heading, cur))
            cur, cur_heading = [], None
            pending_heading = line[:-1].strip() if line.endswith(":") else None
    if len(cur) >= 2:
        lists.append((cur_heading, cur))
    return lists


def _cluster_1d(values, tol):
    """values: list of (coord, payload). Returns list of clusters (lists of payload), sorted by coord."""
    if not values:
        return []
    items = sorted(values, key=lambda v: v[0])
    clusters = [[items[0]]]
    for v in items[1:]:
        if v[0] - clusters[-1][-1][0] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [[p for _, p in cl] for cl in clusters]


def _is_rotated(char):
    return abs(char["matrix"][1]) > 0.5


def _nearest_band(value, bands, tol):
    if not bands:
        return None
    best_i, best_d = None, None
    for i, b in enumerate(bands):
        d = abs(value - b)
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    if best_d is not None and best_d <= tol:
        return best_i
    return None


def _is_data_row(row):
    """A row that contains at least one purely numeric cell (e.g. "55", "1.722")."""
    return any(cell.replace(".", "").replace(",", "").replace(" ", "").isdigit() for cell in row if cell)


def extract_table_lines_based(page, bbox):
    """Fallback for tables with no rotated text: use ruled grid lines, then
    collapse the leading wrapped-header rows (no numeric content yet) into
    one logical header row.
    """
    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "edge_min_length": 3,
    }
    table = page.crop(bbox).find_tables(table_settings=settings)
    if not table:
        return None
    data = table[0].extract()
    rows = [[("" if c is None else str(c).replace("\n", " ").strip()) for c in row] for row in data]
    rows = [r for r in rows if any(r)]
    if not rows:
        return None
    ncols = len(rows[0])
    keep_cols = [j for j in range(ncols) if any(r[j] for r in rows)]
    rows = [[r[j] for j in keep_cols] for r in rows]
    if len(rows) < 2 or len(rows[0]) < 2:
        return None

    split = next((i for i, r in enumerate(rows) if _is_data_row(r)), 1)
    split = max(split, 1)
    header_rows, data_rows = rows[:split], rows[split:]
    header = []
    for j in range(len(rows[0])):
        parts = [r[j] for r in header_rows if r[j]]
        header.append(" ".join(parts))
    return [header] + data_rows


def extract_table(page, bbox, row_tol=8, col_tol=18, frag_tol=4.5):
    """Reconstruct a table within `bbox` (x0, top, x1, bottom) from raw chars.

    Returns None if the region doesn't look like a genuine data table
    (e.g. a decorative text box or a chart with scattered value labels).
    """
    x0, top, x1, bottom = bbox
    chars = [
        c
        for c in page.chars
        if c["text"].strip() and x0 - 1 <= c["x0"] and c["x1"] <= x1 + 1 and top - 1 <= c["top"] and c["bottom"] <= bottom + 1
    ]
    if not chars:
        return None

    # A genuine table always carries some label text (header, row label,
    # or category name). A region with only digits is a chart axis/bars,
    # not a table.
    if not any(c["text"].isalpha() for c in chars):
        return None

    upright = [c for c in chars if not _is_rotated(c)]
    rotated = [c for c in chars if _is_rotated(c)]

    if not rotated:
        return extract_table_lines_based(page, bbox)

    # Row bands come from upright text only -- rotated label fragments span
    # large y-ranges and would smear row boundaries together.
    row_clusters = _cluster_1d([(c["top"], c) for c in upright], row_tol)
    row_bands = []
    for cl in row_clusters:
        center = sum(c["top"] for c in cl) / len(cl)
        row_bands.append(center)
    row_bands.sort()
    if len(row_bands) < 1:
        return None

    col_bands = []
    for cl in _cluster_1d([(c["x0"], c) for c in upright], col_tol):
        col_bands.append(sum(c["x0"] for c in cl) / len(cl))
    col_bands.sort()

    # Reconstruct rotated-text fragments: chars sharing an x-position form a
    # column; within that column, a gap in y separates distinct text runs
    # (e.g. one run per table row), and a run must be read bottom-to-top.
    fragments = []
    for x_cl in _cluster_1d([(c["x0"], c) for c in rotated], frag_tol):
        x_cl_sorted = sorted(x_cl, key=lambda c: c["top"])
        run = [x_cl_sorted[0]]
        runs = [run]
        for c in x_cl_sorted[1:]:
            if c["top"] - run[-1]["top"] > row_tol * 1.5:
                run = [c]
                runs.append(run)
            else:
                run.append(c)
        for run in runs:
            run_sorted = sorted(run, key=lambda c: -c["top"])
            text = "".join(c["text"] for c in run_sorted).strip()
            if not text:
                continue
            fragments.append(
                {
                    "text": text,
                    "x": sum(c["x0"] for c in run_sorted) / len(run_sorted),
                    "top": min(c["top"] for c in run_sorted),
                    "bottom": max(c["bottom"] for c in run_sorted),
                }
            )

    header_top_cutoff = row_bands[0] - row_tol if row_bands else bottom
    header_frags = [f for f in fragments if f["bottom"] <= header_top_cutoff + row_tol]
    rowlabel_frags = [f for f in fragments if f not in header_frags]

    # Extend column bands with columns that only ever carry rotated text
    # (e.g. a category whose data is all-zero/blank, so it never produced an
    # upright digit to anchor a column). Re-cluster everything together with
    # chained tolerance so a multi-word header (e.g. "posesja, pomieszczenie
    # gospodarcze") merges into one column instead of splitting wherever a
    # single word happens to sit just past the tolerance from its neighbor.
    extra_cols = [f["x"] for f in header_frags + rowlabel_frags if _nearest_band(f["x"], col_bands, col_tol) is None]
    col_bands = [
        sum(xs) / len(xs) for xs in _cluster_1d([(x, x) for x in col_bands + extra_cols], col_tol)
    ]
    col_bands.sort()

    if len(col_bands) < 2:
        return None

    nrows = len(row_bands) + 1  # +1 header row
    ncols = len(col_bands)
    grid = [["" for _ in range(ncols)] for _ in range(nrows)]

    def place(row_idx, x, text):
        col_idx = _nearest_band(x, col_bands, col_tol * 1.5)
        if col_idx is None:
            return
        cur = grid[row_idx][col_idx]
        grid[row_idx][col_idx] = (cur + " " + text).strip() if cur else text

    for f in header_frags:
        place(0, f["x"], f["text"])
    for f in rowlabel_frags:
        f_center = (f["top"] + f["bottom"]) / 2
        row_idx = _nearest_band(f_center, row_bands, row_tol * 3)
        if row_idx is None:
            continue
        place(row_idx + 1, f["x"], f["text"])

    # Upright chars: assemble per (row, col) left-to-right by x.
    cell_chars = {}
    for c in upright:
        row_idx = _nearest_band(c["top"], row_bands, row_tol)
        col_idx = _nearest_band(c["x0"], col_bands, col_tol * 1.5)
        if row_idx is None or col_idx is None:
            continue
        cell_chars.setdefault((row_idx + 1, col_idx), []).append(c)
    for (row_idx, col_idx), cs in cell_chars.items():
        cs.sort(key=lambda c: c["x0"])
        text = "".join(c["text"] for c in cs).strip()
        if text:
            cur = grid[row_idx][col_idx]
            grid[row_idx][col_idx] = (cur + " " + text).strip() if cur else text

    if all(not any(row) for row in grid):
        return None
    return grid


def find_table_bboxes(page):
    """Locate candidate table regions using ruled grid lines (rects)."""
    settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "edge_min_length": 3,
    }
    try:
        tables = page.find_tables(table_settings=settings)
    except Exception:
        return []
    return [t.bbox for t in tables]


def extract_tables_from_page(page):
    """Returns a list of grids (list-of-rows) for genuine tables on the page."""
    results = []
    for bbox in find_table_bboxes(page):
        grid = extract_table(page, bbox)
        if grid is not None:
            results.append(grid)
    return results

"""One-off, idempotent patch for a confirmed PDF-extraction defect: `miesiace`
(monthly) tables in several ruch-drogowy reports continue onto a new page
without redrawing their ruled border, so `pdf_tables.py`'s grid-line table
detection finds nothing there and the continuation rows fall through into the
page's plain text instead of a structured table -- silently dropping months.

Confirmed cases (via content.md inspection): Raport2006int_pdf p.53,
Raport2007int_pdf p.13, Raport2008int_pdf p.36, Wypadki2017_pdf p.62. This
script re-derives the dropped rows from each report's own `content.md` (which
already has the orphaned text, just not as a structured table) and appends
them directly to the affected `tables/*.csv` file, rather than re-running the
live download/parse pipeline -- that would risk re-shuffling unrelated
table filenames via rename_tables.py's renumbering. Safe to re-run: a table
that's already complete (ends on Grudzień) is left untouched.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RD = ROOT / "data" / "statystyka-policja" / "ruch-drogowy"

MONTH_SEQUENCE = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
                  "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]
MONTH_LABEL_FIX = {m.lower(): m for m in MONTH_SEQUENCE}
MONTH_LABEL_FIX["październi"] = "Październik"

TABLE_REF_RE = re.compile(r"\(Table extracted from page (\d+) -> tables/(miesiace\S*\.csv), (\d+) rows\)")
PAGE_HEADER_RE = re.compile(r"^## Page (\d+) text$")


def canon_month_label(label: str) -> str:
    return MONTH_LABEL_FIX.get(label.strip().lower(), label.strip())


def tokenize_values(text: str, ncols: int) -> list[str] | None:
    """Split a continuation line's remainder into exactly `ncols` value
    tokens, re-merging a thousands-grouped number's space-separated halves
    (e.g. "4 395" tokenizes as ["4", "395"] but is one value) -- the same
    space-as-thousands-separator convention `parse_num` already handles for
    single pre-split cells, applied here across whitespace-split tokens."""
    raw = text.split()
    values = []
    i = 0
    while i < len(raw) and len(values) < ncols:
        tok = raw[i]
        if "," in tok or not tok.replace(",", "").isdigit():
            values.append(tok)
            i += 1
        elif i + 1 < len(raw) and raw[i + 1].isdigit() and len(raw[i + 1]) == 3:
            values.append(f"{tok} {raw[i + 1]}")
            i += 2
        else:
            values.append(tok)
            i += 1
    if len(values) != ncols or i != len(raw):
        return None
    return values


def find_continuation_rows(content_lines: list[str], table_page: int, next_months: list[str], ncols: int):
    """Look at the page immediately after `table_page` for lines starting
    with the expected next month names, in order. Stops at the first
    non-matching line (e.g. a chart caption) or once `next_months` is
    exhausted."""
    target_page = table_page + 1
    page_start = None
    for i, line in enumerate(content_lines):
        m = PAGE_HEADER_RE.match(line.strip())
        if m and int(m.group(1)) == target_page:
            page_start = i
            break
    if page_start is None:
        return []

    rows = []
    remaining = list(next_months)
    for line in content_lines[page_start + 1:]:
        line = line.strip()
        if not remaining:
            break
        if not line:
            continue
        first_word = line.split(" ", 1)[0]
        if canon_month_label(first_word) != remaining[0]:
            break
        rest = line[len(first_word):].strip()
        values = tokenize_values(rest, ncols)
        if values is None:
            break
        rows.append([remaining[0]] + values)
        remaining.pop(0)
    return rows


def patch_table(csv_path: Path, table_page: int, content_lines: list[str]):
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return None
    header, data_rows = rows[0], rows[1:]
    last_label = canon_month_label(data_rows[-1][0])
    if last_label not in MONTH_SEQUENCE:
        return None  # table doesn't end on a recognized month -- not this defect
    last_idx = MONTH_SEQUENCE.index(last_label)
    if last_idx == 11:
        return None  # already complete through Grudzień
    next_months = MONTH_SEQUENCE[last_idx + 1:]
    ncols = len(data_rows[-1]) - 1

    new_rows = find_continuation_rows(content_lines, table_page, next_months, ncols)
    if not new_rows:
        return None

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(data_rows)
        w.writerows(new_rows)
    return [r[0] for r in new_rows]


def main():
    total_patched = 0
    for content_md in sorted(RD.glob("*/content.md")):
        text = content_md.read_text(encoding="utf-8")
        content_lines = text.splitlines()
        for m in TABLE_REF_RE.finditer(text):
            page, csv_name, _ = int(m.group(1)), m.group(2), int(m.group(3))
            csv_path = content_md.parent / "tables" / csv_name
            if not csv_path.exists():
                continue
            added = patch_table(csv_path, page, content_lines)
            if added:
                total_patched += 1
                print(f"{csv_path.relative_to(ROOT)}: added {', '.join(added)} (page {page + 1})")
    print(f"\n{total_patched} table(s) patched")


if __name__ == "__main__":
    main()

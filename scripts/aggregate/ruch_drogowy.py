"""Melt every data/ruch-drogowy table into long format per topic, using the
crosswalk built by ruch_drogowy_crosswalk.py.

Three table shapes recur across the corpus (confirmed by direct inspection
of representative tables from several topics/years):

1. Single header row, data immediately below (e.g. rodzaj-pojazdu-sprawcy):
   one row per category, one column per metric, all for the report's own
   year -- year = the report folder's year for every row.
2. Two header rows where the second row holds a 4-digit year per column
   (e.g. grupy-wiekowe, wojewodztwo-art-kw-...): these reports show 2
   adjacent years side by side for comparison. Row 1 (metric names) has
   blanks for repeated/merged columns and is forward-filled; row 2 supplies
   the year for that specific column. A handful of trailing columns (e.g.
   "Populacja*") have no year of their own in row 2 -- these are assigned
   the report's own (later) year as a documented default, not a real
   per-column year reconstruction.
3. "lata"/"lata-pojazdy-*" topics: the table itself *is* a multrai-year
   series (one row per year, year in column 0), spanning many reports with
   overlapping years and rebased percent-of-base-year columns. Per
   doc/data-aggregation-plan.md, at least one such table
   (Raport2011int_pdf's `lata-pojazdy-silnikowe-ogolem-w-tym-samochody-
   osobowe-2001-100-...csv`) has its year column blank on every other row
   (2001/2003/2005/2007/2009), a PDF-extraction defect, not a source-data
   gap -- the year is reconstructed by sequential row position (each row is
   the previous row's year + 1) rather than trusted literally.

Rebased-index columns (e.g. "2001=100%") and genuine year-over-year
mismatches between overlapping reports are not reconciled here -- this
script preserves every row with full source_file provenance so that
reconciliation (an analysis step, not an aggregation step) can happen
downstream without having silently overwritten anything.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "ruch-drogowy"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "ruch-drogowy"
CROSSWALK = OUT / "crosswalk.csv"

LATA_TOPICS = {"lata", "lata-pojazdy-silnikowe-ogolem-w-tym-samochody-osobowe-ogolem",
               "dzieci-0-14-ofiary-wypadkow"}

YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def folder_year(folder_name: str) -> int:
    m = re.search(r"(19|20)\d{2}", folder_name)
    return int(m.group())


def parse_num(raw: str):
    s = raw.strip()
    if s in {"", "-", "–", "—", "brak", "*"}:
        return None
    s = s.replace(" ", "").replace("\xa0", "").rstrip("%")
    # "." as thousands separator (confirmed: Raport2002int_pdf's wojewodztwa
    # table has POLSKA Wypadki Ogolem = "53.559", which lines up with 2001's
    # verified 53,799 total -- a plausible one-year decline, not 53.559
    # accidents. Decimals in this corpus always use a comma, per Polish
    # convention, so a "." followed by exactly 3 digits is never a genuine
    # fraction here.)
    if re.match(r"^-?\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")
    s = re.sub(r"^(-?\d+),(\d+)$", r"\1.\2", s)
    m = re.match(r"^-?\d+(?:\.\d+)?$", s)
    return s if m else raw.strip()


def fix_count_metric(metric: str, value):
    """A handful of cells use "," as the thousands separator instead of the
    corpus's usual " " or "." (confirmed: Raport2012int_pdf's rodzaj-wypadku
    table has a "Zabici Ogolem" totals-row value of "3,751", which parse_num
    -- correctly, for the much more common comma-decimal-percentage case --
    converts to "3.751"). A non-percentage metric should never genuinely be
    a 3-decimal-place fraction in this corpus, so re-interpret it as a
    thousands-grouped integer instead."""
    if value is not None and "%" not in metric and re.match(r"^-?\d{1,3}\.\d{3}$", str(value)):
        return str(value).replace(".", "")
    return value


def read_rows(table: Path):
    with table.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def melt_lata_table(table: Path, topic: str, source_file: str, rows_out):
    rows = [r for r in read_rows(table) if any(c.strip() for c in r)]
    header = rows[0]
    # the rebased-index column header (e.g. "2001=100%") gives the base year,
    # which anchors reconstruction if the very first data row's year is also
    # blank (no preceding row to count up from in that case)
    base_year_match = re.search(r"(19|20)\d{2}(?==\s*100)", " ".join(header))
    prev_year = int(base_year_match.group()) - 1 if base_year_match else None
    for row in rows[1:]:
        cell = row[0].strip()
        if YEAR_RE.match(cell):
            year = int(cell)
        elif prev_year is not None:
            year = prev_year + 1  # blank-year PDF-extraction defect: reconstruct sequentially
        else:
            continue
        prev_year = year
        for col_name, raw in zip(header[1:], row[1:]):
            value = fix_count_metric(col_name, parse_num(raw))
            if value is not None:
                rows_out.append((topic, year, "", col_name.strip(), value, source_file))


def forward_fill(header_row):
    out = []
    last = ""
    for c in header_row:
        if c.strip():
            last = c.strip()
        out.append(last)
    return out


EMBEDDED_YEAR_RE = re.compile(r"^(.*?)\s*((?:19|20)\d{2})\s*(?:rok|r\.?)$", re.I)


def split_embedded_year_header(raw_header):
    """Some single-header tables fold the year directly into the column
    name (e.g. "Wypadki 2018 rok", "2019 rok") instead of using a separate
    year sub-row -- confirmed in 56 tables across 7+ topics (naruszony-
    przepis-r-r, grupy-wiekowe, przyczyny, wojewodztwo-stan-po-uzyciu-...,
    rodzaj-uzytkownika-drogi-ofiary, wiek, r-r). Without this, the column's
    embedded year gets discarded and every value is mislabeled with the
    report's own folder year instead. Returns (metric_per_col, year_per_col)
    or None if this table doesn't use the pattern."""
    matches = [EMBEDDED_YEAR_RE.match(c.strip()) for c in raw_header[1:]]
    if sum(1 for m in matches if m) < 2:
        return None
    metrics, years, last_metric = [], [], ""
    for m in matches:
        if m:
            text, yr = m.group(1).strip(), int(m.group(2))
            if text:
                last_metric = text
            metrics.append(last_metric or "value")
            years.append(yr)
        else:
            metrics.append(last_metric or "value")
            years.append(None)
    return metrics, years


MONTH_SEQUENCE = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec",
                   "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]
# OCR/extraction lowercases or truncates some month labels in some reports
# (confirmed: "luty"/"lipiec"/"listopad" in several `miesiace` tables, and
# "Październi" truncated in at least one) -- canonicalize rather than let
# these silently fail to match the proper-case month elsewhere.
MONTH_LABEL_FIX = {m.lower(): m for m in MONTH_SEQUENCE}
MONTH_LABEL_FIX["październi"] = "Październik"


def canon_month_label(label: str) -> str:
    return MONTH_LABEL_FIX.get(label.lower(), label)


def reconstruct_miesiace_dimensions(raw_labels: list[str]) -> list[str | None]:
    """`miesiace` tables have a recurring PDF-extraction defect: every other
    row's month label merged into the previous row during extraction, leaving
    the label cell blank even though the data is real (confirmed two ways:
    against the raw report text, and because the "missing" months' values
    sum, in row order, to exactly the table's own Ogółem total). Which parity
    (odd or even months blank) varies by table, and some tables start on a
    blank row, so this can't be inferred by extending from the previous
    label -- instead, anchor on whatever rows DO have a recognized month
    label, solve for the fixed row-index-to-month offset they imply, and use
    that offset to fill in the blanks. If the anchors don't agree on one
    offset (or there are none), reconstruction is abandoned and blanks are
    left as None (dropped), the same as the old default behavior."""
    resolved = [canon_month_label(label.strip()) if label.strip() else None for label in raw_labels]
    anchors = [(i, MONTH_SEQUENCE.index(lbl)) for i, lbl in enumerate(resolved) if lbl in MONTH_SEQUENCE]
    if not anchors:
        return resolved
    offsets = {i - month_idx for i, month_idx in anchors}
    if len(offsets) != 1:
        return resolved
    offset = offsets.pop()
    for i, label in enumerate(raw_labels):
        if resolved[i] is not None:
            continue
        month_idx = i - offset
        if 0 <= month_idx <= 11:
            resolved[i] = MONTH_SEQUENCE[month_idx]
    return resolved


def melt_standard_table(table: Path, topic: str, year: int, source_file: str, rows_out):
    rows = [r for r in read_rows(table) if any(c.strip() for c in r)]
    if not rows:
        return
    embedded = split_embedded_year_header(rows[0])
    if embedded:
        metrics, years = embedded
        data_rows = rows[1:]
        dimensions = (reconstruct_miesiace_dimensions([r[0] for r in data_rows]) if topic == "miesiace"
                      else [r[0].strip() or None for r in data_rows])
        for row, dimension in zip(data_rows, dimensions):
            if not dimension:
                continue
            for j in range(1, len(row)):
                if j - 1 >= len(metrics):
                    break
                value = fix_count_metric(metrics[j - 1], parse_num(row[j]))
                if value is None:
                    continue
                row_year = years[j - 1] if years[j - 1] is not None else year
                rows_out.append((topic, row_year, dimension, metrics[j - 1], value, source_file))
        return
    header0 = rows[0]
    header1 = forward_fill(rows[0])
    data_start = 1
    col_years = None
    rebased_pct_col = [False] * len(header1)
    if len(rows) > 1:
        candidate = rows[1]
        year_like = sum(1 for c in candidate[1:] if YEAR_RE.match(c.strip()))
        if year_like >= max(1, (len(candidate) - 1) // 2):
            col_years = [int(c.strip()) if YEAR_RE.match(c.strip()) else None for c in candidate]
            data_start = 2
            # A blank header cell forward-filled from e.g. "Wypadki" is fine
            # when it's genuinely the same metric for another year (per the
            # module docstring's shape 2) -- but when the blank column's own
            # sub-header is a percentage marker (either a rebased index like
            # "2003=100%", or a bare "%" share-of-annual-total column), it's
            # a different metric (a ratio, not a count) that happens to share
            # the same forward-filled name; confirmed such columns otherwise
            # collide under one (year, dimension, metric) key with silently
            # different values (e.g. miesiace-wypadki-zabici-ranni.csv tags
            # both a real count and its %/YoY-ratio under metric="Wypadki").
            for j, cell in enumerate(header0):
                if not cell.strip() and j < len(candidate) and "%" in candidate[j]:
                    rebased_pct_col[j] = True
    data_rows = rows[data_start:]
    dimensions = (reconstruct_miesiace_dimensions([r[0] for r in data_rows]) if topic == "miesiace"
                  else [r[0].strip() or None for r in data_rows])
    for row, dimension in zip(data_rows, dimensions):
        if not dimension:
            continue
        for j in range(1, len(row)):
            if j >= len(header1):
                break
            metric = header1[j]
            if j < len(rebased_pct_col) and rebased_pct_col[j]:
                metric = f"{metric} (rebased %)"
            value = fix_count_metric(metric, parse_num(row[j]))
            if value is None:
                continue
            row_year = year
            if col_years is not None and j < len(col_years):
                row_year = col_years[j] if col_years[j] is not None else year
            rows_out.append((topic, row_year, dimension, metric, value, source_file))


def main():
    with CROSSWALK.open(newline="", encoding="utf-8") as f:
        crosswalk = list(csv.DictReader(f))

    by_topic = {}
    for entry in crosswalk:
        if entry["topic"] in {"NOISE"}:
            continue
        by_topic.setdefault(entry["topic"], []).append(entry)

    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for topic, entries in sorted(by_topic.items()):
        rows_out = []
        for entry in entries:
            table = DATA / entry["folder"] / "tables" / entry["filename"]
            source_file = str(table.relative_to(ROOT))
            if topic in LATA_TOPICS:
                melt_lata_table(table, topic, source_file, rows_out)
            else:
                year = folder_year(entry["folder"])
                melt_standard_table(table, topic, year, source_file, rows_out)

        safe_name = topic.replace("/", "-")
        path = OUT / f"{safe_name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["topic", "year", "dimension", "metric", "value", "source_file"])
            for row in rows_out:
                w.writerow(row)
        total += len(rows_out)
        print(f"{topic}: {len(rows_out)} rows -> {path.name}")
    print(f"TOTAL: {total} rows across {len(by_topic)} topics")


if __name__ == "__main__":
    main()

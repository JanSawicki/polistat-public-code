"""Aggregate the two inline-HTML-only `wybrane-statystyki` pages (downloaded
by scripts/ingest/download_html_wybrane_statystyki.py) into the same
long-format CSV shape (`dataset, year, dimension, metric, value, source_file`)
used by every other wybrane-statystyki series. Parsed with the stdlib
`html.parser` rather than a third-party HTML library, since neither lxml nor
bs4 is available in this project's venv and a single well-formed `<table>`
per page doesn't need more than that.

- **maloletni-pod-wplywem** (minors under the influence of alcohol,
  2000-2017): single flat table, one row per year, no row-grouping --
  `revealed` (ujawnieni nietrzezwi) and `transported_total/boys/girls`
  (dowiezieni do izb wytrzezwien) are reported separately; "b.d." (brak
  danych) cells are left out of the long-format output rather than written
  as 0, since `transported_*` is fully missing pre-2009.
- **nietrzezwi-podejrzani-o-popeln** (intoxicated suspects, 1999-2012):
  row-grouped by crime category (zabojstwo, uszczerbek na zdrowiu, bojka
  lub pobicie, zgwalcenie, kradziez cudzej rzeczy, kradziez z wlamaniem,
  rozboj/kradziez rozbojnicza/wymuszenie rozbojnicze) -- HTML rowspan
  collapses the category label onto only the first year's <tr> in each
  group, so the parser must carry the last-seen category label forward
  across rows until the next one appears, not read it per-row.
"""
import csv
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "wybrane-statystyki"


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(text)
            self._in_cell = False
        elif tag == "tr":
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def parse_table(html_path: Path) -> list[list[str]]:
    html = html_path.read_text(encoding="utf-8")
    m = re.search(r"<table.*?</table>", html, re.S)
    p = TableParser()
    p.feed(m.group(0))
    return p.rows


def to_number(s: str):
    s = s.strip()
    if not s or s.lower() in {"b.d", "b.d.", "-", "brak danych"}:
        return None
    s = s.replace("\xa0", "").replace(" ", "").replace(".", "").replace(",", "")
    return int(s)


def aggregate_maloletni():
    rows = parse_table(RAW / "maloletni-pod-wplywem" / "page.html")
    data_rows = [r for r in rows if len(r) == 5 and r[0].isdigit()]
    metric_labels = {
        1: "Liczba ujawnionych przez Policję nietrzeźwych osób do 18 roku życia",
        2: "Liczba osób do 18 roku życia dowiezionych przez Policję do izb wytrzeźwień (ogółem)",
        3: "Liczba osób do 18 roku życia dowiezionych przez Policję do izb wytrzeźwień (chłopcy)",
        4: "Liczba osób do 18 roku życia dowiezionych przez Policję do izb wytrzeźwień (dziewczęta)",
    }
    out_rows = []
    for r in data_rows:
        year = int(r[0])
        for col, metric in metric_labels.items():
            value = to_number(r[col])
            if value is None:
                continue
            out_rows.append({
                "dataset": "maloletni_pod_wplywem", "year": year, "dimension": "ogółem",
                "metric": metric, "value": value,
                "source_file": "data/statystyka-policja/wybrane-statystyki/maloletni-pod-wplywem/page.html",
            })
    write_csv(OUT / "maloletni-pod-wplywem.csv", out_rows)


CATEGORY_PREFIXES = {
    "zabójstwo", "uszczerbek na zdrowiu", "bójka lub pobicie", "zgwałcenie",
    "kradzież cudzej rzeczy", "kradzież z włamaniem",
    "rozbój, kradzież rozbójnicza i wymuszenie rozbójnicze",
}

NIETRZEZWI_METRICS = [
    "podejrzani dorośli ogółem", "podejrzani dorośli z ustaloną trzeźwością",
    "podejrzani dorośli nietrzeźwi", "podejrzani nieletni ogółem",
    "podejrzani nieletni z ustaloną trzeźwością", "podejrzani nieletni nietrzeźwi",
]


def aggregate_nietrzezwi():
    rows = parse_table(RAW / "nietrzezwi-podejrzani-o-popeln" / "page.html")
    out_rows = []
    category = None
    for r in rows:
        if r and r[0].strip().lower() in CATEGORY_PREFIXES:
            category = r[0].strip()
            year_cell, values = r[1], r[2:]
        elif r and r[0].strip().isdigit() and category is not None:
            year_cell, values = r[0], r[1:]
        else:
            continue
        if len(values) != 6:
            continue
        year = int(year_cell)
        for metric, raw_val in zip(NIETRZEZWI_METRICS, values):
            value = to_number(raw_val)
            if value is None:
                continue
            out_rows.append({
                "dataset": "nietrzezwi_podejrzani", "year": year, "dimension": category,
                "metric": metric, "value": value,
                "source_file": "data/statystyka-policja/wybrane-statystyki/nietrzezwi-podejrzani-o-popeln/page.html",
            })
    write_csv(OUT / "nietrzezwi-podejrzani-o-popeln.csv", out_rows)


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["dataset", "year", "dimension", "metric", "value", "source_file"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["dimension"], r["year"], r["metric"])))
    print(f"{len(rows)} rows -> {path}")


def main():
    aggregate_maloletni()
    aggregate_nietrzezwi()


if __name__ == "__main__":
    main()

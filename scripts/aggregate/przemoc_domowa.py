"""Aggregate data/wybrane-statystyki/przemoc-domowa's 4 incompatible
sub-periods into one long table, tagging the framing each row came from
rather than forcing a single shared metric set.

- 1999-2011: 5 separate "interwencje domowe" xlsx files (one per metric
  family: interventions, victims, perpetrators, perpetrators under
  influence, referrals to institutions), wide format with one column per
  year.
- 2012-2022: one xlsx, 11 per-year tables under
  `przemoc-w-rodzinie-2012-2022_xlsx/tables/`, "Niebieska Karta" procedure-
  form framing. The 11 CSVs are named
  `a-liczba-formularzy-wszczynajacych-procedure-<N>.csv` where `<N>` is a
  data value from that year's table (the count of initiating forms), not a
  stable id -- the year is read from the in-cell "Dane za rok YYYY" header,
  never the filename.
- 2023/2024/2025: three separate per-year files in the same Niebieska Karta
  framing, but reflecting the 2023 domestic-violence-law renaming
  ("przemoc w rodzinie" -> "przemoc domowa", with more category rows from
  2024 onward) and an extra leading blank column vs. the 2012-2022 file.
  2024's values are stored as floats (e.g. `59174.0`) and need numeric
  coercion, not string passthrough.

The 1999-2011 and 2012-2025 framings use different metric definitions
(documented in CLAUDE.md as the 2012 domestic-violence registration
change) and are not reconciled into shared metric names -- each row keeps
its own `metric` label as it appears in the source.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki" / "przemoc-domowa"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "wybrane-statystyki"

WIDE_FORMAT_FILES = {
    "DomoweinterwencjePolicjiwlatach1999-2011_xlsx": "interwencje_domowe",
    "Liczbaskierowanychprzezpolicjantowinformacjioujawnionychprzypadkachprzemocydoroz_xlsx": "informacje_skierowane_do_instytucji",
    "Ofiaryprzemocydomowejwlatach1999-2011_xlsx": "ofiary",
    "Sprawcyprzemocydomowejbedacypodwplywemalkoholuwlatach1999-2011_xlsx": "sprawcy_pod_wplywem_alkoholu",
    "Sprawcyprzemocydomowejwlatach1999-2011_xlsx": "sprawcy",
}


def parse_num(raw: str):
    s = raw.strip()
    if s in {"", "-", "brak"}:
        return None
    s = s.replace(" ", "").replace("\xa0", "")
    m = re.match(r"^-?\d+(?:\.0+)?$", s)
    if m:
        return str(int(float(s)))
    return s


def parse_wide_period(rows_out):
    for folder_name, dataset in WIDE_FORMAT_FILES.items():
        tables = list((DATA / folder_name / "tables").glob("*.csv"))
        assert len(tables) == 1, f"expected one table in {folder_name}"
        table = tables[0]
        with table.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        year_row = next(r for r in rows if re.match(r"^\d{4}$", r[1].strip()))
        years = [int(y.strip()) for y in year_row[1:] if y.strip()]
        for row in rows:
            if row is year_row or not row[0].strip():
                continue
            if not any(c.strip() for c in row[1:]):
                continue
            metric = row[0].strip()
            for year, raw in zip(years, row[1:]):
                rows_out.append(("1999-2011", dataset, year, metric, parse_num(raw),
                                  str(table.relative_to(ROOT))))


def parse_niebieska_karta_table(table: Path, rows_out, period_label):
    with table.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    stripped = []
    for row in rows:
        idx = next((i for i, c in enumerate(row) if c.strip()), None)
        stripped.append(row[idx:] if idx is not None else [])
    year_row = next(r for r in stripped if r and r[0].startswith("Dane za rok"))
    year = int(re.search(r"\d{4}", year_row[0]).group())
    for row in stripped:
        if len(row) < 3 or row is year_row:
            continue
        code, label, value = row[0].strip(), row[1].strip(), row[2]
        if not re.match(r"^\d+\.$|^[a-z]\)$", code):
            continue
        rows_out.append((period_label, "niebieska_karta", year, f"{code} {label}",
                          parse_num(value), str(table.relative_to(ROOT))))


def parse_niebieska_karta_2012_2022(rows_out):
    for table in sorted((DATA / "przemoc-w-rodzinie-2012-2022_xlsx" / "tables").glob("*.csv")):
        parse_niebieska_karta_table(table, rows_out, "2012-2022")


def parse_niebieska_karta_per_year(rows_out):
    for folder in ("przemoc-domowa-2023_xlsx", "przemoc-domowa-2024_xls", "przemoc-domowa-2025_xlsx"):
        tables = list((DATA / folder / "tables").glob("*.csv"))
        assert len(tables) == 1, f"expected one table in {folder}"
        parse_niebieska_karta_table(tables[0], rows_out, "2023-2025")


def main():
    rows_out = []
    parse_wide_period(rows_out)
    parse_niebieska_karta_2012_2022(rows_out)
    parse_niebieska_karta_per_year(rows_out)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "przemoc-domowa.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sub_period", "dataset", "year", "metric", "value", "source_file"])
        for row in rows_out:
            w.writerow(["" if v is None else v for v in row])
    print(f"{len(rows_out)} rows -> {path}")


if __name__ == "__main__":
    main()

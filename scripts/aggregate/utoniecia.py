"""Aggregate data/wybrane-statystyki/utoniecia (1998-2025, one PDF per year)
into one long table per topic.

Table filenames drift across years (confirmed non-stable, per
doc/data-aggregation-plan.md): the same topic gets re-slugged over time, and
some years degrade to generic `label-value*.csv` placeholders. This builds
an explicit year+filename -> topic_key crosswalk rather than relying on a
single naming convention.

The degraded `label-value*.csv` filenames were resolved by reading each
year's content.md and matching the table's row values against the
surrounding Polish caption text (the technique that worked for 2022, where
label-value.csv's rows match the "Wiek ofiar utonięć" section verbatim).
Confirmed mappings (not guesses):
- 2022/2024/2025: exactly 3 label-value*.csv files in stable order
  (wiek_ofiar, rodzaj_zbiornika, okolicznosci) -- verified against content.md
  for all three years.
- 2003-2012 (10 years): label-value.csv (13-15 items) is always the
  okolicznosci ("najczestsze okolicznosci") list spilling onto page 2 --
  verified for 2003, 2007, 2010, 2011 directly; 2004-2006/2008-2009 assumed
  by the same page layout (same item counts, same preceding caption text
  "Policjanci uratowali ... osob" / "Sposrod ... uratowanych").
- 1999: label-value.csv (3 items) is the tail of the rodzaj_zbiornika list
  cut by a page break (the named table for that year only captured 10 of the
  ~14 water-body categories); label-value-2.csv (11 items) is the okolicznosci
  list; label-value-3.csv (2 items) is the tail of a one-off
  "najmniej wypadkow w wojewodztwach" (fewest-accidents-by-voivodeship) list
  that has no separate file of its own elsewhere -- the 3rd value
  (m. st. Warszawa) seems to have been dropped by the extractor.
- 2001: label-value.csv (2 items) is the tail of the trzezwosc list (the
  named trzezwosc table doesn't exist that year).
- 1998: label-value.csv (3 items) is the trzezwosc list in full.

1998 also has three one-off tables with no equivalent in any other year
(`osoby-ktore-utonely` combines age brackets with a gender breakdown,
`posiadanie-karty-plywackiej...` and `bezposrednia-przyczyna-utoniecia...`
have no recurring counterpart at all) -- kept as their own topics rather than
forced into a crosswalk that doesn't exist for them.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki" / "utoniecia"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "wybrane-statystyki"

TOPIC_BY_SUBSTRING = [
    ("wiek", "wiek_ofiar"),
    ("zbiornika-wodnego", "rodzaj_zbiornika"),
    ("okolicznosci", "okolicznosci"),
    ("teren-wypadku", "teren_wypadku"),
    ("trzezwosc", "trzezwosc"),
    ("godzina", "godzina_wypadku"),
    ("dzien-tygodnia", "dzien_tygodnia"),
    ("dni-tygodnia", "dzien_tygodnia"),
    ("najwiecej-wypadkow-utoniecia-bylo-w-wojewodztwach", "wojewodztwa_najwiecej"),
    ("bezposrednia-przyczyna", "przyczyna_utoniecia"),
    ("posiadanie-karty-plywackiej", "karta_plywacka_i_towarzystwo"),
    ("osoby-ktore-utonely", "wiek_ofiar"),
]

DEGRADED_OVERRIDES = {
    ("utoniecia-1998_pdf", "label-value.csv"): "trzezwosc",
    ("utoniecia-1999_pdf", "label-value.csv"): "rodzaj_zbiornika",
    ("utoniecia-1999_pdf", "label-value-2.csv"): "okolicznosci",
    ("utoniecia-1999_pdf", "label-value-3.csv"): "wojewodztwa_najmniej",
    ("utoniecia-2001_pdf", "label-value.csv"): "trzezwosc",
}
for y in range(2003, 2013):
    DEGRADED_OVERRIDES[(f"utoniecia-{y}_pdf", "label-value.csv")] = "okolicznosci"
for y in (2022, 2024, 2025):
    DEGRADED_OVERRIDES[(f"utoniecia-{y}_pdf", "label-value.csv")] = "wiek_ofiar"
    DEGRADED_OVERRIDES[(f"utoniecia-{y}_pdf", "label-value-2.csv")] = "rodzaj_zbiornika"
    DEGRADED_OVERRIDES[(f"utoniecia-{y}_pdf", "label-value-3.csv")] = "okolicznosci"


def topic_for(folder_name: str, filename: str) -> str:
    key = (folder_name, filename)
    if key in DEGRADED_OVERRIDES:
        return DEGRADED_OVERRIDES[key]
    for substr, topic in TOPIC_BY_SUBSTRING:
        if substr in filename:
            return topic
    raise ValueError(f"no topic mapping for {folder_name}/{filename}")


def parse_num(raw: str):
    s = raw.strip()
    if s in {"", "brak", "-"}:
        return None
    s = s.replace(" ", "").replace("\xa0", "")
    m = re.match(r"^(-?\d+)\*?$", s)
    return m.group(1) if m else s


def parse_simple_table(csv_path: Path, year: int, topic: str, source_file: str, rows_out):
    """label/value 2-col tables and most year-specific list extractions."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if rows and len(rows[0]) > 1 and rows[0][1].strip().lower() == "value":
        rows = rows[1:]
    for row in rows:
        if not row or not row[0].strip():
            continue
        label = row[0].strip()
        value = parse_num(row[1]) if len(row) > 1 else None
        rows_out.append((topic, year, "ogolem", label, value, source_file))


def main():
    rows_out = []
    for folder in sorted(DATA.iterdir()):
        if not folder.is_dir():
            continue
        year = int(re.search(r"\d{4}", folder.name).group())
        for table in sorted((folder / "tables").glob("*.csv")):
            source_file = str(table.relative_to(ROOT))
            topic = topic_for(folder.name, table.name)
            if table.name == "osoby-ktore-utonely.csv":
                with table.open(newline="", encoding="utf-8") as f:
                    csv_rows = list(csv.reader(f))
                header = [h.strip() for h in csv_rows[0][1:]]
                for row in csv_rows[1:]:
                    dimension = "ogolem" if row[0].strip() == "ogółem" else "w_tym_kobiety"
                    for col_name, raw in zip(header, row[1:]):
                        rows_out.append((topic, year, dimension, col_name, parse_num(raw), source_file))
                continue
            if table.name == "posiadanie-karty-plywackiej-nie-tak-nie-ustalono-przebywanie-nad-woda.csv":
                with table.open(newline="", encoding="utf-8") as f:
                    csv_rows = list(csv.reader(f))
                header = [h.strip() for h in csv_rows[0]]
                for col_name, raw in zip(header, csv_rows[1]):
                    rows_out.append((topic, year, "ogolem", col_name, parse_num(raw), source_file))
                continue
            if table.name == "bezposrednia-przyczyna-utoniecia-nieumiejetnosc-plywania-szok.csv":
                with table.open(newline="", encoding="utf-8") as f:
                    csv_rows = list(csv.reader(f))
                header = [h.strip() for h in csv_rows[0]]
                for col_name, raw in zip(header, csv_rows[1]):
                    rows_out.append((topic, year, "ogolem", col_name, parse_num(raw), source_file))
                continue
            parse_simple_table(table, year, topic, source_file, rows_out)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "utoniecia.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["topic", "year", "dimension", "label", "value", "source_file"])
        for row in rows_out:
            w.writerow(["" if v is None else v for v in row])
    print(f"{len(rows_out)} rows -> {path}")


if __name__ == "__main__":
    main()

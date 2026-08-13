"""Aggregate data/kodeks-karny/Art*_xlsx into one long table.

Each folder has exactly one table CSV with a couple of caption rows, a header
row, then one row per year. We don't bother locating the header: any row
whose first cell is a bare 4-digit year is a data row, everything else
(captions, footnote text, blank rows) is noise and gets skipped.

Three folders (`Art157157a_xlsx`, `Art230i230a_xlsx`, `Art268i268a_xlsx`)
report a joint count for two articles rather than one each; the source data
doesn't separate them, so they're kept as a single combined article id
(`157+157a` etc.) rather than split or dropped.

Known amendment years for articles whose data starts after 1999 (not a
download gap, per doc/data-aggregation-plan.md): stalking/grooming/
trafficking provisions added 2003-2013.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "kodeks-karny"
OUT = ROOT / "data-aggregated" / "statystyka-policja"

YEAR_RE = re.compile(r"^(\d{4})\*?$")
MISSING = {"", "brak", "-"}
FOLDER_RE = re.compile(r"^Art(\d+[a-z]?)(?:i(\d+[a-z]?))?_xlsx$")

KNOWN_AMENDMENT_YEAR = {
    "165a": 2012, "189a": 2012, "190a": 2012, "211a": 2012,
    "191a": 2010, "200a": 2010, "200b": 2010,
    "254a": 2013,
    "250a": 2003, "296a": 2003,
}

# Confirmed source-data defects, not parsing artifacts (both tables otherwise
# strictly list one row per year, 1999-2023):
# - Art244a: a spurious extra row (2012, 423, 3016) is appended after the
#   table's last legitimate row (1999), breaking the descending-year order;
#   a legitimate 2012 row (385, 227) already exists earlier in the table, so
#   this is dropped as stray/duplicated content, not real 2012 data.
# - Art314: "2016" appears twice consecutively (2, 3) and (9, 7), with 2015
#   entirely absent from the table's span -- confirmed not a separate gap
#   (no other article in this corpus has a missing year): the second "2016"
#   row is relabeled to the 2015 it's clearly meant to be, based on position.
ROW_DROPS = {
    ("244a", 2012, 423, 3016),
}
ROW_YEAR_FIXES = {
    ("314", 2016, 9, 7): 2015,
}


COMBINED_FOLDER_OVERRIDE = {
    # no "i" separator in the folder name, unlike Art230i230a/Art268i268a
    "Art157157a_xlsx": "157+157a",
}


def article_id(folder_name: str) -> str:
    if folder_name in COMBINED_FOLDER_OVERRIDE:
        return COMBINED_FOLDER_OVERRIDE[folder_name]
    m = FOLDER_RE.match(folder_name)
    if not m:
        raise ValueError(f"unrecognized kodeks-karny folder name: {folder_name}")
    if m.group(2):
        return f"{m.group(1)}+{m.group(2)}"
    return m.group(1)


def parse_table(csv_path: Path):
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            m = YEAR_RE.match(row[0].strip())
            if not m:
                continue
            year = int(m.group(1))
            wszczete = row[1].strip() if len(row) > 1 else ""
            stwierdzone = row[2].strip() if len(row) > 2 else ""
            rows.append((
                year,
                None if wszczete in MISSING else int(wszczete),
                None if stwierdzone in MISSING else int(stwierdzone),
            ))
    return rows


def main():
    folders = sorted(p for p in DATA.iterdir() if p.is_dir())
    out_rows = []
    notes = []
    for folder in folders:
        tables = list((folder / "tables").glob("*.csv"))
        if len(tables) != 1:
            raise ValueError(f"expected exactly one table in {folder}, found {len(tables)}")
        article = article_id(folder.name)
        data = parse_table(tables[0])
        data = [(year, w, s) for year, w, s in data if (article, year, w, s) not in ROW_DROPS]
        data = [(ROW_YEAR_FIXES.get((article, year, w, s), year), w, s) for year, w, s in data]
        for year, wszczete, stwierdzone in data:
            out_rows.append((article, year, wszczete, stwierdzone))
        min_year = min(y for y, _, _ in data)
        if min_year > 1999:
            notes.append((article, min_year, KNOWN_AMENDMENT_YEAR.get(article, "")))

    out_rows.sort(key=lambda r: (r[0], r[1]))
    OUT.mkdir(exist_ok=True)
    with (OUT / "kodeks-karny.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["article", "year", "postepowania_wszczete", "przestepstwa_stwierdzone"])
        for article, year, wszczete, stwierdzone in out_rows:
            w.writerow([article, year, wszczete if wszczete is not None else "",
                        stwierdzone if stwierdzone is not None else ""])

    with (OUT / "kodeks-karny-notes.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["article", "first_year_in_data", "known_amendment_year"])
        for article, min_year, amend in sorted(notes):
            w.writerow([article, min_year, amend])

    print(f"{len(out_rows)} rows across {len(folders)} articles -> {OUT / 'kodeks-karny.csv'}")
    print(f"{len(notes)} late-starting articles -> {OUT / 'kodeks-karny-notes.csv'}")


if __name__ == "__main__":
    main()

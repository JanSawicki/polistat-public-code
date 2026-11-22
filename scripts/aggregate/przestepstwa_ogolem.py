"""Aggregate data/przestepstwa-ogolem's category tree into one long table.

The category tree has 17 nodes that carry their own data (not just leaves —
e.g. `przestepstwa-kryminalne` has both totals of its own and child
categories). Each such node has two sibling `*_xlsx` folders:

- `postepowania-wszczete-*`: dimension "Jednostka organizacyjna Policji"
  (national total + 17 regional KWP/KSP units + BSW/CBŚP/CBZC special units),
  one metric (postepowania_wszczete).
- the plain counterpart (no prefix): dimension "Jednostka podziału
  administracyjnego" (national total + 16 voivodeships + the KSP
  Warszawa/KWP Radom police-jurisdiction split), three metrics
  (przestepstwa_stwierdzone, przestepstwa_wykryte, pct_wykrycia).

These are two different dimensions, not one shared "unit" axis as a naive
reading of the file names might suggest — kept separate via `dimension_type`
rather than merged.

The `do-20XX` suffix in xlsx folder names is not always the true last year
(confirmed: kradziez-z-wlamaniem's postepowania-wszczete file is named
"do-2024" but its data stops at 2023) — the actual last year is always read
out of the data and recorded per node in category-tree.csv as
`series_end_year`, never assumed from the folder name.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "przestepstwa-ogolem"
OUT = ROOT / "data-aggregated" / "statystyka-policja"

YEAR_RE = re.compile(r"^\d{4}$")

# Confirmed source-data typo, not a parsing artifact: this file's only CBZC/2023
# row is mislabeled "2923" (CBZC otherwise only appears from 2022 onward, and no
# separate 2023 CBZC row exists, so this is the missing 2023 row, fat-fingered).
YEAR_CORRECTIONS = {
    "data/statystyka-policja/przestepstwa-ogolem/przestepstwa-kryminalne/7-wybranych-kategorii-p/uszczerbek-na-zdrowiu/postepowania-wszczete-przestepstwa-uszczerbek-na-zdrowiu-do-2023_xlsx/tables/postepowania-uszczerbek.csv": {
        2923: 2023,
    },
}

POLICE_UNIT_CANON = {
    "jednostki organizacyjne Policji": "national_total",
    "BSW KGP": "bsw",
    "BSWP KGP": "bsw",
    "CBŚ KGP": "cbsp",
    "CBŚ KGP/CBŚP": "cbsp",
    "CBŚP": "cbsp",
    "CBZC": "cbzc",
    "KWP z/s w Radomiu i KSP Warszawa": "kwp_radom_ksp_warszawa_combined",
}

ADMIN_UNIT_CANON = {
    "Polska": "national_total",
}


def canon_unit(dimension_type, raw_unit):
    table = POLICE_UNIT_CANON if dimension_type == "police_unit" else ADMIN_UNIT_CANON
    if raw_unit in table:
        return table[raw_unit]
    return re.sub(r"\s+", "_", raw_unit.strip().lower())


def find_data_pair(folder: Path):
    """Return (postepowania_csv, plain_csv) for a category node, or (None, None)."""
    xlsx_dirs = [c for c in folder.iterdir() if c.is_dir() and c.name.endswith("_xlsx")]
    postepowania = [d for d in xlsx_dirs if d.name.startswith("postepowania-wszczete")]
    plain = [d for d in xlsx_dirs if not d.name.startswith("postepowania-wszczete")]
    if not postepowania and not plain:
        return None, None
    assert len(postepowania) == 1 and len(plain) == 1, f"unexpected xlsx folder count in {folder}"
    pc = list((postepowania[0] / "tables").glob("*.csv"))
    pl = list((plain[0] / "tables").glob("*.csv"))
    assert len(pc) == 1 and len(pl) == 1, f"expected one table each in {folder}"
    return pc[0], pl[0]


def parse_postepowania(csv_path: Path):
    """dimension=police_unit, single metric postepowania_wszczete. Year is col 1."""
    corrections = YEAR_CORRECTIONS.get(str(csv_path.relative_to(ROOT)), {})
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 3 or not YEAR_RE.match(row[1].strip()):
                continue
            unit, year, value = row[0].strip(), int(row[1].strip()), row[2].strip()
            year = corrections.get(year, year)
            rows.append((unit, year, "postepowania_wszczete", value if value else None))
    return rows


def parse_plain(csv_path: Path):
    """dimension=administrative_unit, 3 metrics. Year is col 1."""
    metrics = ["przestepstwa_stwierdzone", "przestepstwa_wykryte", "pct_wykrycia"]
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 5 or not YEAR_RE.match(row[1].strip()):
                continue
            unit, year = row[0].strip(), int(row[1].strip())
            for metric, raw in zip(metrics, row[2:5]):
                raw = raw.strip()
                rows.append((unit, year, metric, raw if raw else None))
    return rows


def walk(folder: Path, category_path: str, parent_path: str, tree_rows, data_rows):
    pc, pl = find_data_pair(folder)
    has_data = pc is not None
    if has_data:
        po_rows = parse_postepowania(pc)
        pl_rows = parse_plain(pl)
        max_year = max(
            [y for _, y, _, _ in po_rows] + [y for _, y, _, _ in pl_rows]
        )
        # BSW/CBŚP rows simply don't exist pre-2013 (folded into whichever
        # regional KWP/KSP garrison they operated in) — the flag therefore
        # applies to every police_unit row pre-2013, not just bsw/cbsp ones,
        # since regional totals in that period are the conflated figures.
        for unit, year, metric, value in po_rows:
            data_rows.append((
                category_path, "police_unit", canon_unit("police_unit", unit), unit,
                year, metric, value, year < 2013,
                str(pc.relative_to(ROOT)),
            ))
        for unit, year, metric, value in pl_rows:
            data_rows.append((
                category_path, "administrative_unit", canon_unit("administrative_unit", unit), unit,
                year, metric, value, year < 2013,
                str(pl.relative_to(ROOT)),
            ))
    else:
        max_year = None

    tree_rows.append((category_path, parent_path, folder.name, has_data, max_year))

    for child in sorted(folder.iterdir()):
        if child.is_dir() and not child.name.endswith("_xlsx"):
            child_path = f"{category_path}/{child.name}"
            walk(child, child_path, category_path, tree_rows, data_rows)


def main():
    tree_rows = []
    data_rows = []
    root_path = DATA.name
    walk(DATA, root_path, "", tree_rows, data_rows)

    OUT.mkdir(exist_ok=True)
    with (OUT / "category-tree.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["category_path", "parent_path", "label", "has_own_data", "series_end_year"])
        for row in tree_rows:
            w.writerow(row)

    with (OUT / "przestepstwa-ogolem.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "category_path", "dimension_type", "unit", "unit_raw", "year", "metric",
            "value", "bsw_cbsp_unseparated", "source_file",
        ])
        for row in data_rows:
            w.writerow([v if v is not None else "" for v in row])

    print(f"{len(tree_rows)} category nodes -> {OUT / 'category-tree.csv'}")
    print(f"{len(data_rows)} data rows -> {OUT / 'przestepstwa-ogolem.csv'}")


if __name__ == "__main__":
    main()

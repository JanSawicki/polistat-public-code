"""Aggregate the "already multi-year, stable-schema" wybrane-statystyki
subfolders: bron, zaginieni, wybrane-ustawy-szczegol, handel-ludzmi-i-przest,
kradzieze-samochodow, przestepczosc-nieletni.

These don't share a schema with each other (weapons permits, missing
persons, named-statute crime counts, car theft, juvenile crime are
topically unrelated) so each gets its own output CSV under
data-aggregated/wybrane-statystyki/<subfolder>.csv, per the plan's "don't
force one pipeline" instruction. Within a subfolder the shape is stable
enough to read and concatenate directly.

przestepczosc-nieletni isn't named in doc/data-aggregation-plan.md's bucket
list, but it lives under wybrane-statystyki and the plan's own cross-cutting
notes reference its 2013 end-of-series gap, so it's handled here as a
same-shape addition to this group.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "wybrane-statystyki"

YEAR_RE = re.compile(r"^(\d{4})\*?$")
MISSING = {"", "brak", "-", "*"}


def parse_num(raw: str):
    s = raw.strip()
    if s in MISSING:
        return None
    s = s.replace(" ", "").replace("\xa0", "")
    if re.match(r"^-?\d+,\d+$", s):
        s = s.replace(",", ".")
    return s


def dedupe_headers(header):
    seen = {}
    out = []
    for h in header:
        h = h.strip() or "col"
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    return out


def read_year_table(csv_path: Path):
    """Generic "Rok, metric1, metric2, ..." table -> list of (year, metric, value)."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    first_data_idx = next(
        i for i, row in enumerate(rows) if row and YEAR_RE.match(row[0].strip())
    )
    header_idx = next(
        i for i in range(first_data_idx - 1, -1, -1) if any(c.strip() for c in rows[i])
    )
    raw_header = rows[header_idx]
    header = dedupe_headers(raw_header)
    out = []
    for row in rows[first_data_idx:]:
        if not row or not YEAR_RE.match(row[0].strip()):
            continue
        year = int(YEAR_RE.match(row[0].strip()).group(1))
        for raw_col_name, col_name, raw in zip(raw_header[1:], header[1:], row[1:]):
            if not raw_col_name.strip():
                continue  # trailing blank-header column, not a real metric
            out.append((year, col_name, parse_num(raw)))
    return out


def write_long(subfolder: str, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{subfolder}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dataset", "year", "dimension", "metric", "value", "source_file"])
        for row in rows:
            w.writerow(["" if v is None else v for v in row])
    print(f"{len(rows)} rows -> {path}")


def simple_year_tables(subfolder, dataset_prefix=None):
    """zaginieni / wybrane-ustawy-szczegol / handel-ludzmi-i-przest / przestepczosc-nieletni:
    each leaf *_xlsx folder has exactly one year-table CSV; the dataset id is
    the leaf folder's name (sans _xlsx suffix)."""
    rows = []
    for leaf in sorted((DATA / subfolder).iterdir()):
        if not leaf.is_dir() or not leaf.name.endswith("_xlsx"):
            continue
        tables = list((leaf / "tables").glob("*.csv"))
        if len(tables) != 1:
            raise ValueError(f"expected one table in {leaf}, found {len(tables)}")
        dataset = leaf.name[: -len("_xlsx")]
        for year, metric, value in read_year_table(tables[0]):
            rows.append((dataset, year, "", metric, value, str(tables[0].relative_to(ROOT))))
    write_long(subfolder, rows)


def bron():
    rows = []
    for leaf in sorted((DATA / "bron").iterdir()):
        if not leaf.is_dir():
            continue
        m = re.search(r"(\d{4})", leaf.name)
        year = int(m.group(1))
        for table in (leaf / "tables").glob("*.csv"):
            dataset = "pozwolenia_ogolem" if "ogolem" in table.stem else "pozwolenia_wydane"
            with table.open(newline="", encoding="utf-8") as f:
                csv_rows = list(csv.reader(f))
            header = dedupe_headers(csv_rows[1])
            for row in csv_rows[2:]:
                if not row or not row[0].strip():
                    continue
                cel = row[0].strip()
                for col_name, raw in zip(header[1:], row[1:]):
                    value = parse_num(raw)
                    if value is None and raw.strip() == "":
                        continue
                    rows.append((dataset, year, cel, col_name, value, str(table.relative_to(ROOT))))
    write_long("bron", rows)


def kradzieze_samochodow():
    rows = []
    leaf = DATA / "kradzieze-samochodow" / "kradziezesamochodow2013-2022_xlsx"
    for table in sorted((leaf / "tables").glob("*.csv")):
        with table.open(newline="", encoding="utf-8") as f:
            csv_rows = [r for r in csv.reader(f) if r and r[0].strip()]
        year = int(re.search(r"ROK", csv_rows[1][0], re.I) and csv_rows[1][1])
        for metric, raw, *_ in csv_rows[2:]:
            rows.append(("kradziez_samochodu", year, "", metric.strip(), parse_num(raw),
                         str(table.relative_to(ROOT))))
    write_long("kradzieze-samochodow", rows)


def main():
    simple_year_tables("zaginieni")
    simple_year_tables("wybrane-ustawy-szczegol")
    simple_year_tables("handel-ludzmi-i-przest")
    simple_year_tables("przestepczosc-nieletni")
    bron()
    kradzieze_samochodow()


if __name__ == "__main__":
    main()

"""Flatten the raw GUS BDL population API responses (cached under
data/statystyka-baza-danych-lokalnych/raw/ by download_bdl.py) into two clean
long-format CSVs for per-capita normalization of the policja.pl series.

Each raw variable carries its meaning in opaque numeric ids; this script
resolves those ids against the cached variable/unit metadata and writes out
human-readable dimension columns instead, so a downstream analysis script
never has to look up a BDL variable id to know what a row means.

Spot-checked for internal consistency before writing (see verify_* below):
voivodeship populations sum to the national total, urban+rural sums to the
overall total, and the 5-year age bands sum to the headline total -- all
exact, no discrepancies found.
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "statystyka-baza-danych-lokalnych" / "raw"
META = ROOT / "data" / "statystyka-baza-danych-lokalnych" / "_meta"
OUT = ROOT / "data-aggregated" / "statystyka-baza-danych-lokalnych"

RESIDENCE_AREA = {"ogółem": "total", "w miastach": "urban", "na wsi": "rural"}
POPULATION_BASIS = {"miejsce zamieszkania": "actual_residence", "stałe miejsce zameldowania": "permanent_registration"}
AS_OF_DATE = {"stan na 31 grudnia": "12-31", "stan na 30 czerwca": "06-30"}
SEX = {"ogółem": "total", "mężczyźni": "male", "kobiety": "female"}

# 5-year bands that are mutually exclusive and exhaustive once "70-74" and
# above appear (2002 onward) -- they sum exactly to the headline total.
DETAILED_BANDS = {
    "0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
    "40-44", "45-49", "50-54", "55-59", "60-64", "65-69",
    "70-74", "75-79", "80-84", "85 i więcej",
}
# Before 2002 the data only has a coarse "70 i więcej" instead of the
# 70-74..85+ split, so it's part of the detailed/exhaustive set only then.
COARSE_70PLUS_ONLY_BEFORE = "70 i więcej"
COARSE_70PLUS_CUTOFF_YEAR = 2002
# Redundant convenience rollups that double-count against the bands above --
# never include these when summing age bands.
ROLLUP_BANDS = {"0-14", "70 i więcej"}


def load_units() -> dict:
    units = json.loads((META / "units.json").read_text(encoding="utf-8"))
    return {u["id"]: u for u in units}


def load_variables(subject_id: str) -> dict:
    variables = json.loads((META / f"variables-{subject_id}.json").read_text(encoding="utf-8"))
    return {v["id"]: v for v in variables}


def write_residence_sex_csv(units: dict):
    variables = load_variables("P1336")
    dest = OUT / "population-by-residence-sex.csv"
    rows = []
    for raw_file in sorted((RAW / "ludnosc-stan-miasto-wies").glob("*.json")):
        unit_id = raw_file.stem
        unit = units[unit_id]
        series_list = json.loads(raw_file.read_text(encoding="utf-8"))
        for series in series_list:
            v = variables[series["id"]]
            for point in series["values"]:
                rows.append((
                    unit_id, unit["name"], unit["level"],
                    int(point["year"]),
                    RESIDENCE_AREA[v["n1"]],
                    POPULATION_BASIS[v["n2"]],
                    AS_OF_DATE[v["n3"]],
                    SEX[v["n4"]],
                    point["val"],
                ))
    OUT.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "unit_name", "unit_level", "year", "residence_area",
                    "population_basis", "as_of_date", "sex", "population"])
        w.writerows(sorted(rows))
    print(f"{len(rows)} rows -> {dest}")


def write_age_group_sex_csv(units: dict):
    variables = load_variables("P2137")
    dest = OUT / "population-by-age-group-sex.csv"
    rows = []
    for raw_file in sorted((RAW / "ludnosc-grupy-wieku-plec").glob("*.json")):
        unit_id = raw_file.stem
        unit = units[unit_id]
        series_list = json.loads(raw_file.read_text(encoding="utf-8"))
        for series in series_list:
            v = variables[series["id"]]
            age_group = v["n1"]
            for point in series["values"]:
                year = int(point["year"])
                if age_group == "ogółem":
                    kind = "total"
                elif age_group in DETAILED_BANDS:
                    kind = "detailed"
                elif age_group == COARSE_70PLUS_ONLY_BEFORE:
                    kind = "detailed" if year < COARSE_70PLUS_CUTOFF_YEAR else "rollup"
                elif age_group in ROLLUP_BANDS:
                    kind = "rollup"
                else:
                    raise ValueError(f"unrecognized age group {age_group!r}")
                rows.append((
                    unit_id, unit["name"], unit["level"],
                    year, age_group, kind,
                    SEX[v["n2"]],
                    point["val"],
                ))
    OUT.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "unit_name", "unit_level", "year", "age_group",
                    "age_group_kind", "sex", "population"])
        w.writerows(sorted(rows))
    print(f"{len(rows)} rows -> {dest}")


def verify_residence_consistency():
    """Spot-check: urban + rural == total, and voivodeships sum to the
    national total, for actual_residence/12-31/total rows."""
    import collections
    rows = csv.DictReader((OUT / "population-by-residence-sex.csv").open(encoding="utf-8"))
    by_key = collections.defaultdict(dict)
    nat_total = {}
    voiv_sum = collections.defaultdict(int)
    for r in rows:
        if r["population_basis"] != "actual_residence" or r["as_of_date"] != "12-31" or r["sex"] != "total":
            continue
        by_key[(r["unit_id"], r["year"])][r["residence_area"]] = int(r["population"])
        if r["unit_id"] == "000000000000":
            nat_total[r["year"]] = by_key[(r["unit_id"], r["year"])].get("total")
        if r["unit_level"] == "2":
            pass  # summed below once all rows seen

    bad = 0
    for (unit_id, year), areas in by_key.items():
        if {"urban", "rural", "total"} <= areas.keys():
            if areas["urban"] + areas["rural"] != areas["total"]:
                bad += 1
                print(f"  ! urban+rural != total for {unit_id}/{year}: "
                      f"{areas['urban']}+{areas['rural']} != {areas['total']}")
    if bad == 0:
        print("  OK: urban + rural == total for every (unit, year)")

    rows2 = csv.DictReader((OUT / "population-by-residence-sex.csv").open(encoding="utf-8"))
    for r in rows2:
        if (r["population_basis"] == "actual_residence" and r["as_of_date"] == "12-31"
                and r["sex"] == "total" and r["residence_area"] == "total" and r["unit_level"] == "2"):
            voiv_sum[r["year"]] += int(r["population"])
    bad = 0
    for year, total in nat_total.items():
        if year in voiv_sum and voiv_sum[year] != total:
            bad += 1
            print(f"  ! voivodeship sum != national total for {year}: {voiv_sum[year]} != {total}")
    if bad == 0:
        print("  OK: voivodeship populations sum to the national total for every year")


def verify_age_group_consistency():
    """Spot-check: detailed age bands sum to the headline total, per (unit, year, sex)."""
    import collections
    rows = csv.DictReader((OUT / "population-by-age-group-sex.csv").open(encoding="utf-8"))
    detailed_sum = collections.defaultdict(int)
    total = {}
    for r in rows:
        key = (r["unit_id"], r["year"], r["sex"])
        if r["age_group_kind"] == "detailed":
            detailed_sum[key] += int(r["population"])
        elif r["age_group_kind"] == "total":
            total[key] = int(r["population"])
    bad = 0
    for key, t in total.items():
        if key in detailed_sum and detailed_sum[key] != t:
            bad += 1
            print(f"  ! detailed age bands != total for {key}: {detailed_sum[key]} != {t}")
    if bad == 0:
        print("  OK: detailed age bands sum to the headline total for every (unit, year, sex)")


def main():
    units = load_units()
    write_residence_sex_csv(units)
    write_age_group_sex_csv(units)
    print("\nConsistency spot checks:")
    verify_residence_consistency()
    verify_age_group_consistency()


if __name__ == "__main__":
    main()

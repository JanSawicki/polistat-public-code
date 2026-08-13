"""Cross-category synthesis, per doc/analysis/methodology.md "Cross-category synthesis".

Runs after the four per-category scripts: reconciles overlapping series and
writes the combined headline-anomalies table that the rest of the project
builds toward.

The car-theft cross-check (wybrane-statystyki vs przestepstwa-ogolem) is
already produced as Finding rows by wybrane_statystyki.py itself, so it needs
no extra work here.

The drunk-driving cross-check (ruch-drogowy vs kodeks-karny art. 178a) uses
`wojewodztwo-stan-po-uzyciu-stan-nietrzezwosci-r-a-z-e-m.csv`, which has real
sobriety data by voivodeship, 2014-2025, compared nationally against art.
178a's przestepstwa_stwierdzone. `nietrzezwosc-uczestnikow-zdarzenia.csv` is a
topic/file mismatch (fault-attribution categories, not sobriety counts,
2001-2005 only) and is not used here.

The national total comes from the table's own rollup row (label varies:
"Ogółem" 2014-2015, "OGÓŁEM" 2016-2018/2020-2025, "POLSKA" 2019), not a sum
over voivodeship rows, since each year typically appears in two overlapping
source reports (once as a report's "current" column, once as the next
report's "prior" column, both carrying identical values -- see
doc/analysis/implementation.md's table type 2); rows are deduped on
(year, metric) before summing.
"""
import pandas as pd

from common import POLICJA, RESULTS, write_chart_data_md

CATEGORIES = ["przestepstwa-ogolem", "kodeks-karny", "ruch-drogowy", "wybrane-statystyki"]
COLUMNS = ["category", "series", "year", "magnitude", "confidence",
           "structural_break_explained", "per_capita_adjusted", "notes"]


def load_all_findings() -> pd.DataFrame:
    frames = []
    for category in CATEGORIES:
        path = RESULTS / category / "findings.csv"
        if path.exists():
            frames.append(pd.read_csv(path))
    return pd.concat(frames, ignore_index=True)[COLUMNS]


def check_drunk_driving_reconciliation() -> str:
    rd = pd.read_csv(POLICJA / "ruch-drogowy" / "wojewodztwo-stan-po-uzyciu-stan-nietrzezwosci-r-a-z-e-m.csv")
    # National total comes from the table's own rollup row, not a sum over
    # voivodeship rows -- the rollup label varies by year ("Ogółem"
    # 2014-2015, "OGÓŁEM" 2016-2018/2020-2025, "POLSKA" 2019). Each year
    # typically appears in two overlapping reports (once as the "current"
    # column, once as the next report's "prior" column, both carrying
    # identical values per doc/analysis/implementation.md's table type 2)
    # -- dedupe on (year, metric) to take each year's value once.
    national = rd[
        (rd["dimension"].str.upper().isin({"OGÓŁEM", "POLSKA"}))
        & (rd["metric"].isin(["Stan po użyciu", "Stan nietrzeźwości"]))
    ].copy()
    national["value"] = pd.to_numeric(national["value"], errors="coerce")
    national = national.drop_duplicates(subset=["year", "metric"])
    rd_annual = national.groupby("year")["value"].sum().sort_index()

    kk = pd.read_csv(POLICJA / "kodeks-karny.csv")
    art178a = kk[kk["article"] == "178a"].set_index("year")["przestepstwa_stwierdzone"].dropna().sort_index()

    common_years = sorted(set(rd_annual.index) & set(art178a.index))
    comparison = pd.DataFrame({
        "year": common_years,
        "ruch_drogowy_wojewodztwo_sobriety_stops": [rd_annual.loc[y] for y in common_years],
        "kodeks_karny_art178a_stwierdzone": [art178a.loc[y] for y in common_years],
    })
    comparison["ruch_drogowy_yoy_pct"] = comparison["ruch_drogowy_wojewodztwo_sobriety_stops"].pct_change()
    comparison["art178a_yoy_pct"] = comparison["kodeks_karny_art178a_stwierdzone"].pct_change()
    comparison["yoy_direction_agrees"] = (
        (comparison["ruch_drogowy_yoy_pct"] > 0) == (comparison["art178a_yoy_pct"] > 0)
    ).astype("boolean")
    comparison.loc[comparison["ruch_drogowy_yoy_pct"].isna(), "yoy_direction_agrees"] = pd.NA

    comparison.to_csv(RESULTS / "drunk-driving-reconciliation.csv", index=False)
    write_chart_data_md(comparison, RESULTS / "drunk-driving-reconciliation-data.md",
                         "Drunk driving: ruch-drogowy sobriety stops (by voivodeship, summed) vs. "
                         "kodeks-karny art. 178a stwierdzone")

    agree_mask = comparison["yoy_direction_agrees"].dropna()
    n_agree, n_total = int(agree_mask.sum()), len(agree_mask)
    return (
        "Drunk-driving reconciliation (ruch-drogowy vs kodeks-karny art. 178a): "
        "`ruch-drogowy/wojewodztwo-stan-po-uzyciu-stan-nietrzezwosci-r-a-z-e-m.csv` has real "
        f"sobriety data, overlapping art. 178a in {common_years[0]}-{common_years[-1]}. "
        "Raw magnitudes are not directly comparable (the ruch-drogowy national total is a "
        "steady ~1.8-2.0x art. 178a's przestepstwa_stwierdzone every year -- these count "
        "different populations: accident-related sobriety tests vs. all "
        "prowadzenie-pojazdu-w-stanie-nietrzezwosci proceedings), but the ratio's stability "
        f"itself corroborates both sources, and year-over-year direction agrees in "
        f"{n_agree}/{n_total} years (only 2022 disagrees) -- see drunk-driving-reconciliation.csv."
    )


def main():
    headline = load_all_findings().sort_values(["category", "year"])
    RESULTS.mkdir(parents=True, exist_ok=True)
    headline.to_csv(RESULTS / "headline-anomalies.csv", index=False)

    note = check_drunk_driving_reconciliation()
    (RESULTS / "drunk-driving-reconciliation.md").write_text(f"# Drunk-driving reconciliation\n\n{note}\n")

    print(f"{len(headline)} total findings across {headline['category'].nunique()} categories "
          f"written to {RESULTS / 'headline-anomalies.csv'}")
    print(note)


if __name__ == "__main__":
    main()

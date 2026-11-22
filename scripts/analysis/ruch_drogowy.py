"""Anomaly/trend analysis for ruch-drogowy, per doc/analysis/methodology.md §3.

This category's raw tables are the least homogeneous in the dataset: the
crosswalk tags several genuinely different per-report tables (different road
types/causes/locations, all reusing the same generic "Wypadki/Zabici/Ranni
Ogółem" column headers) under one topic. The methodology here is
provenance-first: establish the national backbone (1975-2025) first, then use
it as a ground truth to pick the correct source table for the monthly series,
rather than guessing from filenames or averaging conflicting tables.

`lata.csv`: only source tables literally named `lata-*` are the true national
backbone (others reuse the same metric labels for, e.g., a child-victims
breakdown at ~1/10th the value) -- a clean filename rule, confirmed unique
and consistent per year.

`miesiace.csv`: no clean filename rule exists (the "obvious" no-suffix file
is right for some years, wrong for others). Instead, for each year we sum
every candidate 12-month source table and keep whichever one matches the
(now-corrected) annual backbone total, within a 5% tolerance kept as a safety
margin -- in practice every one of the 26 years (2000-2025) now matches
*exactly* (0% error). The apparent mismatches were never real preliminary-
vs-final report revisions; they were three compounding aggregation-layer
defects, all fixed upstream rather than papered over here:
1. Alternating months' labels merged into the previous row during PDF
   extraction, dropping that row entirely (`reconstruct_miesiace_dimensions`
   in `scripts/aggregate/ruch_drogowy.py`).
2. A count column and its adjacent (blank-header) YoY-or-share-of-annual
   percentage column both got the same forward-filled metric name (e.g. both
   tagged "Wypadki"), so summing "wypadki" rows silently included the
   percentage values too (`rebased_pct_col` handling, same file).
3. 2007's table was genuinely truncated across a PDF page break (no ruled
   border on the continuation page), fixed by a one-off patch
   (`scripts/ingest/fix_miesiace_continuation.py`) against the raw text
   already captured in that report's `content.md`.
If a future report shows up as a gap or a non-zero `relative_error` in
`monthly-wypadki-provenance.csv`, check all three before assuming it's a
genuine data limitation.
"""
import re

import pandas as pd
import matplotlib.pyplot as plt

from common import POLICJA, RESULTS, Finding, detect_change_points, rolling_zscore, \
    stl_decompose, write_chart_data_md, write_findings

CATEGORY = "ruch-drogowy"
RD = POLICJA / "ruch-drogowy"
OUT_DIR = RESULTS / CATEGORY

MONTHS = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec", "Lipiec",
          "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"]
MONTH_INDEX = {name: i + 1 for i, name in enumerate(MONTHS)}
MATCH_TOLERANCE = 0.05

# The age-band breakdown (see pick_age_band_source) doesn't partition total
# accidents as cleanly as months do, so its acceptance tolerance is much
# wider -- see the docstring there for why.
AGE_BAND_MATCH_TOLERANCE = 0.30
AGE_BANDS = ["0-6", "7-14", "15-17", "18-24", "25-39", "40-59", "60+"]
REPORT_YEAR_RE = re.compile(r"(19|20)\d{2}")


def canon_age_band(label) -> str | None:
    """Canonicalize a grupy-wiekowe/grupy-wieku age-band label. Returns None
    for anything outside the 7-band scheme (the report's own band scheme
    changed around 2008-2009 from 10-year adult bands -- 18-19/20-29/.../70+
    -- to today's 0-6/7-14/15-17/18-24/25-39/40-59/60+; the two don't roll up
    into each other cleanly, so years using the old scheme are left as a gap
    rather than approximated) and for non-band rows (totals, "b/d" unknown-age)."""
    s = str(label).strip().lower()
    s = re.sub(r"[–—]", "-", s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"lata?$", "", s)
    s = re.sub(r"^0(\d)", r"\1", s)
    return {
        "0-6": "0-6", "7-14": "7-14", "15-17": "15-17", "18-24": "18-24",
        "25-39": "25-39", "40-59": "40-59", "60iwięcej": "60+", "60plus": "60+",
    }.get(s)


def canon_age_metric(metric: str) -> str | None:
    if str(metric).lower().strip().startswith("populacja"):
        return "populacja"
    return canon_metric(metric)


def report_folder_year(source_file: str) -> int | None:
    """Extract the report's own folder year from a source_file path
    (.../ruch-drogowy/<ReportFolder>/tables/<file>.csv)."""
    m = REPORT_YEAR_RE.search(source_file.split("/")[-3])
    return int(m.group()) if m else None


def canon_metric(metric: str) -> str | None:
    m = str(metric).lower().strip()
    if "rebased" in m or "%" in m:
        return None  # disambiguated YoY-ratio column, not a count -- see scripts/aggregate/ruch_drogowy.py
    if "wypadki" in m:
        return "wypadki"
    if "zabici" in m:
        return "zabici"
    if "ranni" in m:
        return "ranni"
    return None


def build_backbone() -> pd.DataFrame:
    legacy = pd.read_csv(RD / "wypadki-1975-2011-legacy.csv")
    legacy["canon"] = legacy["metric"].map(canon_metric)
    legacy["value"] = pd.to_numeric(legacy["value"], errors="coerce")
    legacy_piv = legacy.dropna(subset=["canon", "value"]).pivot_table(
        index="year", columns="canon", values="value", aggfunc="first")

    lata = pd.read_csv(RD / "lata.csv")
    lata = lata[lata["source_file"].str.split("/").str[-1].str.startswith("lata-")]
    lata["canon"] = lata["metric"].map(canon_metric)
    lata["value"] = pd.to_numeric(lata["value"], errors="coerce")
    lata_piv = lata.dropna(subset=["canon", "value"]).pivot_table(
        index="year", columns="canon", values="value", aggfunc="median")

    # lata.csv (1992+) is the more carefully-maintained backbone source where
    # both overlap; legacy only fills in pre-1992 years.
    backbone = lata_piv.combine_first(legacy_piv).sort_index()
    backbone["severity_killed_per_accident"] = backbone["zabici"] / backbone["wypadki"]
    backbone["severity_injured_per_accident"] = backbone["ranni"] / backbone["wypadki"]
    return backbone.reset_index()


def backbone_change_points(backbone: pd.DataFrame) -> list[Finding]:
    findings = []
    for metric in ["wypadki", "zabici", "ranni", "severity_killed_per_accident"]:
        series = backbone.set_index("year")[metric].dropna()
        years = series.index.tolist()
        for year, confidence in detect_change_points(years, series.values.tolist()):
            idx = years.index(year)
            magnitude = float(series.iloc[idx] - series.iloc[max(0, idx - 1)])
            findings.append(Finding(
                category=CATEGORY, series=f"backbone:{metric}", year=year,
                magnitude=magnitude, confidence=confidence,
                structural_break_explained=False,
                notes="backbone series, not yet cross-checked against a dimensional breakdown",
            ))
    return findings


def pick_monthly_source(metric_canon: str, annual_truth: pd.Series) -> pd.DataFrame:
    """For each year, find the `miesiace.csv` source_file whose 12-month sum
    best matches the known-correct annual backbone total. Returns one row per
    year: the matched source_file (or None if no 12-month candidate is within
    tolerance) plus the relative error, for transparency."""
    df = pd.read_csv(RD / "miesiace.csv")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["canon"] = df["metric"].map(canon_metric)
    sub = df[(df["canon"] == metric_canon) & (df["dimension"].isin(MONTHS))]

    rows = []
    for year, ysub in sub.groupby("year"):
        truth = annual_truth.get(year)
        best_src, best_err = None, None
        for src, gsub in ysub.groupby("source_file"):
            # require exactly 12 rows, not just 12 *unique* months -- a table
            # comparing two years side by side (e.g. "wypadki-2013-wypadki-
            # 2014") can have both years' values tagged under one year and
            # still show 12 unique month names with 24 rows.
            if len(gsub) != 12 or gsub["dimension"].nunique() != 12 or truth in (None, 0) or pd.isna(truth):
                continue
            total = gsub["value"].sum()
            rel_err = abs(total - truth) / truth
            if best_err is None or rel_err < best_err:
                best_src, best_err = src, rel_err
        accepted = best_err is not None and best_err <= MATCH_TOLERANCE
        rows.append({"year": year, "matched_source_file": best_src if accepted else None,
                     "relative_error": best_err, "accepted": accepted})
    return pd.DataFrame(rows).sort_values("year")


def build_monthly_series(metric_canon: str, provenance: pd.DataFrame) -> pd.Series:
    df = pd.read_csv(RD / "miesiace.csv")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["canon"] = df["metric"].map(canon_metric)
    sub = df[(df["canon"] == metric_canon) & (df["dimension"].isin(MONTHS))]

    accepted = provenance[provenance["accepted"]].set_index("year")["matched_source_file"]
    rows = []
    for year, source_file in accepted.items():
        ysub = sub[(sub["year"] == year) & (sub["source_file"] == source_file)]
        for _, row in ysub.iterrows():
            rows.append({"year": year, "month": MONTH_INDEX[row["dimension"]], "value": row["value"]})
    monthly = pd.DataFrame(rows).groupby(["year", "month"])["value"].first().sort_index()
    dates = pd.PeriodIndex([f"{y}-{m:02d}" for y, m in monthly.index], freq="M").to_timestamp()
    return pd.Series(monthly.values, index=dates).asfreq("MS")


def load_age_band_tables() -> pd.DataFrame:
    df = pd.concat([
        pd.read_csv(RD / "grupy-wiekowe.csv"),
        pd.read_csv(RD / "grupy-wieku.csv"),
    ], ignore_index=True)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["age_band"] = df["dimension"].map(canon_age_band)
    df["canon_metric"] = df["metric"].map(canon_age_metric)
    df["report_year"] = df["source_file"].map(report_folder_year)
    return df.dropna(subset=["age_band"])


def pick_age_band_source(annual_truth: pd.Series) -> pd.DataFrame:
    """Per year, pick the grupy-wiekowe/grupy-wieku source_file whose 7-band
    Wypadki sum is closest to the national backbone total.

    Two complications beyond pick_monthly_source's pattern:
    - Each report shows the current and prior year side by side in adjacent
      columns (confirmed: Wypadki2015_pdf's table has both a "2014" and a
      "2015" sub-column per age band) -- correctly split by year already at
      the aggregation stage, but it means a given calendar year's data shows
      up in *two* different reports (its own year's report, and the next
      year's report as the "prior year" comparison column). The own-year
      report is preferred as more authoritative; the other report's
      comparison column is only used as a fallback when the own-year report
      is missing a usable table that year.
    - Several tables per report reuse the same age-band labels for an
      unrelated, much smaller sub-population (confirmed: one candidate
      consistently sums to ~5-10% of the national total) -- the same defect
      already documented for lata.csv/miesiace.csv. Picking the closest-to-
      backbone candidate filters these out the same way.
    Even the correct table consistently undershoots the backbone total by
    ~12-27% (and that gap shrinks over time, 2007's ~27% down to 2025's
    ~12%) -- this looks like a real difference in counting basis (e.g. this
    breakdown may only cover accidents with an age-classifiable party)
    rather than a selection error, so the tolerance here is far wider than
    pick_monthly_source's near-exact match."""
    df = load_age_band_tables()
    rows = []
    for year, ysub in df.groupby("year"):
        truth = annual_truth.get(year)
        candidates = ysub[ysub["report_year"] == year]
        if candidates.empty:
            candidates = ysub
        best_src, best_err = None, None
        for src, gsub in candidates.groupby("source_file"):
            if not {"wypadki", "populacja"} <= set(gsub["canon_metric"].dropna()):
                continue
            wyp = gsub[gsub["canon_metric"] == "wypadki"].groupby("age_band")["value"].first()
            if set(wyp.index) != set(AGE_BANDS) or truth in (None, 0) or pd.isna(truth):
                continue
            rel_err = abs(wyp.sum() - truth) / truth
            if best_err is None or rel_err < best_err:
                best_src, best_err = src, rel_err
        accepted = best_err is not None and best_err <= AGE_BAND_MATCH_TOLERANCE
        rows.append({"year": year, "matched_source_file": best_src if accepted else None,
                     "relative_error": best_err, "accepted": accepted})
    return pd.DataFrame(rows).sort_values("year")


def build_age_band_per_capita(provenance: pd.DataFrame) -> pd.DataFrame:
    df = load_age_band_tables()
    accepted = provenance[provenance["accepted"]].set_index("year")["matched_source_file"]
    rows = []
    for year, source_file in accepted.items():
        ysub = df[(df["year"] == year) & (df["source_file"] == source_file)]
        wyp = ysub[ysub["canon_metric"] == "wypadki"].groupby("age_band")["value"].first()
        pop = ysub[ysub["canon_metric"] == "populacja"].groupby("age_band")["value"].first()
        for band in AGE_BANDS:
            if band not in wyp.index or band not in pop.index or not pop.loc[band]:
                continue
            rows.append({"year": year, "age_band": band, "wypadki": wyp.loc[band],
                         "population": pop.loc[band], "rate_per_100k": wyp.loc[band] / pop.loc[band] * 1e5})
    return pd.DataFrame(rows).sort_values(["age_band", "year"])


def age_band_change_points(per_capita: pd.DataFrame) -> list[Finding]:
    findings = []
    for band, bsub in per_capita.groupby("age_band"):
        series = bsub.set_index("year")["rate_per_100k"].sort_index()
        if len(series) < 6:
            continue
        years = series.index.tolist()
        for year, confidence in detect_change_points(years, series.values.tolist()):
            idx = years.index(year)
            magnitude = float(series.iloc[idx] - series.iloc[max(0, idx - 1)])
            findings.append(Finding(
                category=CATEGORY, series=f"age-band:{band}:rate_per_100k", year=year,
                magnitude=magnitude, confidence=confidence,
                structural_break_explained=False,
                per_capita_adjusted=True,
                notes="age band's own report-embedded population, not BDL (band edges don't align)",
            ))
    return findings


def longest_contiguous_run(series: pd.Series) -> pd.Series:
    not_null = series.notna()
    run_id = (not_null != not_null.shift()).cumsum()
    run_lengths = series[not_null].groupby(run_id[not_null]).transform("size")
    best_run = run_lengths.idxmax()
    return series[run_id == run_id.loc[best_run]]


def seasonal_findings(series: pd.Series) -> tuple[list[Finding], object]:
    result = stl_decompose(series, period=12)
    resid_z = rolling_zscore(result.resid, window=12)
    findings = []
    for ts, z in resid_z.dropna().items():
        if abs(z) >= 3:
            findings.append(Finding(
                category=CATEGORY, series="monthly:wypadki", year=ts.year,
                magnitude=float(result.resid.loc[ts]), confidence=float(abs(z)),
                structural_break_explained=False,
                notes=f"residual outlier after STL seasonal decomposition, month={ts.month}",
            ))
    return findings, result


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    backbone = build_backbone()
    backbone.to_csv(OUT_DIR / "backbone.csv", index=False)
    findings = backbone_change_points(backbone)

    fig, ax = plt.subplots()
    ax.plot(backbone["year"], backbone["wypadki"], label="Accidents")
    ax.plot(backbone["year"], backbone["zabici"], label="Killed")
    ax.plot(backbone["year"], backbone["ranni"], label="Injured")
    ax.set_title("Traffic accidents: killed, accidents, and injured, 1975-2025")
    ax.set_xlabel("year")
    ax.legend()
    fig.savefig(OUT_DIR / "backbone.png")
    plt.close(fig)
    write_chart_data_md(backbone, OUT_DIR / "backbone.md",
                         "Traffic accidents: killed, accidents, and injured, 1975-2025")

    annual_truth = backbone.set_index("year")["wypadki"]
    provenance = pick_monthly_source("wypadki", annual_truth)
    provenance.to_csv(OUT_DIR / "monthly-wypadki-provenance.csv", index=False)
    write_chart_data_md(provenance, OUT_DIR / "monthly-wypadki-provenance.md",
                         "Monthly wypadki source-table selection per year (vs. annual backbone)")

    monthly_wypadki = build_monthly_series("wypadki", provenance)
    monthly_wypadki.to_csv(OUT_DIR / "monthly-wypadki.csv", header=["value"])

    stl_span = longest_contiguous_run(monthly_wypadki)
    if len(stl_span) >= 24:
        seasonal_findings_list, seasonal_result = seasonal_findings(stl_span)
        findings.extend(seasonal_findings_list)

        fig, axes = plt.subplots(4, 1, figsize=(8, 8), sharex=True)
        seasonal_result.observed.plot(ax=axes[0], title="observed")
        seasonal_result.trend.plot(ax=axes[1], title="trend")
        seasonal_result.seasonal.plot(ax=axes[2], title="seasonal")
        seasonal_result.resid.plot(ax=axes[3], title="residual")
        fig.tight_layout()
        fig.savefig(OUT_DIR / "monthly-wypadki-stl.png")
        plt.close(fig)
        stl_table = pd.DataFrame({
            "date": seasonal_result.observed.index.strftime("%Y-%m"),
            "observed": seasonal_result.observed.values,
            "trend": seasonal_result.trend.values,
            "seasonal": seasonal_result.seasonal.values,
            "residual": seasonal_result.resid.values,
        })
        write_chart_data_md(
            stl_table, OUT_DIR / "monthly-wypadki-stl.md",
            f"STL decomposition of monthly accident counts, {stl_span.index[0]:%Y-%m} to "
            f"{stl_span.index[-1]:%Y-%m} -- the longest run with a verified source table")

    age_provenance = pick_age_band_source(annual_truth)
    age_provenance.to_csv(OUT_DIR / "age-band-provenance.csv", index=False)
    write_chart_data_md(age_provenance, OUT_DIR / "age-band-provenance.md",
                         "Age-band source-table selection per year (vs. annual backbone, "
                         "30% tolerance -- see pick_age_band_source docstring)")

    age_per_capita = build_age_band_per_capita(age_provenance)
    age_per_capita.to_csv(OUT_DIR / "age-band-per-capita.csv", index=False)
    write_chart_data_md(age_per_capita, OUT_DIR / "age-band-per-capita.md",
                         "Accidents per 100k population by age band (report-embedded population, not BDL)")
    findings.extend(age_band_change_points(age_per_capita))

    fig, ax = plt.subplots()
    for band, bsub in age_per_capita.groupby("age_band"):
        ax.plot(bsub["year"], bsub["rate_per_100k"], label=band, marker="o", markersize=3)
    ax.set_title("Accidents per 100k population by age band")
    ax.set_xlabel("year")
    ax.legend(fontsize="small")
    fig.savefig(OUT_DIR / "age-band-per-capita.png")
    plt.close(fig)

    write_findings(findings, OUT_DIR / "findings.csv")

    n_gap_years = (~provenance["accepted"]).sum()
    n_age_gap_years = (~age_provenance["accepted"]).sum()
    print(f"{len(findings)} candidate findings written to {OUT_DIR / 'findings.csv'}")
    print(f"backbone series: {len(backbone)} years")
    print(f"monthly wypadki series: {monthly_wypadki.notna().sum()} verified months, "
          f"{n_gap_years} years with no reliable source table (see monthly-wypadki-provenance.csv)")
    print(f"STL run on longest contiguous verified span: {stl_span.index[0]:%Y-%m} to {stl_span.index[-1]:%Y-%m}")
    print(f"age-band per-capita series: {len(age_per_capita)} (age band, year) rows, "
          f"{n_age_gap_years} years with no reliable source table (see age-band-provenance.csv)")


if __name__ == "__main__":
    main()

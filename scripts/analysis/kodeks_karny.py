"""Anomaly/trend analysis for kodeks-karny (per-article), per doc/analysis/methodology.md §2.

Per-article jumps are gated against `kodeks-karny-notes.csv` (first year in
data / known amendment year) before being treated as candidate anomalies, and
the 2014 juvenile-exclusion break is flagged once via the structural-break
registry rather than counted as 163 independent findings.

**Added 2026-06-26:** findings not explained by the structural-break registry
are now also checked against `flag_if_amended` (the Dziennik Ustaw amendment
registry, see doc/dziennik-ustaw.md) -- a real legislative change to that
article taking effect in or just before the finding's year is a sourced,
citation-backed explanation, distinct from (and checked in addition to) the
hardcoded structural-break registry.
"""
import pandas as pd
import matplotlib.pyplot as plt

from common import POLICJA, RESULTS, Finding, arima_level_shift, detect_change_points, \
    flag_if_amended, flag_if_near_break, its_level_shift, mann_kendall_test, \
    write_chart_data_md, write_findings

CATEGORY = "kodeks-karny"
OUT_DIR = RESULTS / CATEGORY
METRIC = "przestepstwa_stwierdzone"


def load_data():
    df = pd.read_csv(POLICJA / "kodeks-karny.csv")
    notes = pd.read_csv(POLICJA / "kodeks-karny-notes.csv").set_index("article")
    return df, notes


def gated_change_points(article: str, series: pd.Series, notes: pd.DataFrame) -> tuple[list[Finding], list[dict], list[dict]]:
    """Returns the (Finding, ITS-regression, ARIMA-regression) triple for each
    detected change-point: the Finding for the headline-anomalies table, an
    ITS row (see `its-regression.csv`, written in `main`) that re-estimates
    the same break's level shift via OLS segmented regression (Chen et al.
    2020) instead of the raw 3-point before/after difference
    `detect_change_points` uses for `magnitude`, and an ARIMA-intervention row
    (`arima-regression.csv`) that re-estimates the same level shift again but
    with an ARMA error process instead of OLS's i.i.d.-residuals assumption --
    a robustness check on the ITS estimate's significance, not a replacement
    for it; the two agreeing is stronger evidence than either alone, and a
    disagreement (ITS-significant but ARIMA-not, or vice versa) means OLS's
    i.i.d. assumption was doing real work in that specific case."""
    gate_years = set()
    if article in notes.index:
        gate_years |= {notes.loc[article, "first_year_in_data"], notes.loc[article, "known_amendment_year"]}
    years = series.index.tolist()
    values = series.values.tolist()
    findings, its_rows, arima_rows = [], [], []
    for year, confidence in detect_change_points(years, values):
        if year in gate_years:
            continue
        idx = years.index(year)
        magnitude = float(series.iloc[idx] - series.iloc[max(0, idx - 1)])
        brk = flag_if_near_break(f"{CATEGORY} art. {article}", year) or flag_if_near_break(CATEGORY, year)
        notes_text = brk["nature"] if brk else ""
        explained = brk is not None
        if not explained:
            amendments = flag_if_amended(article, year)
            if amendments:
                cites = "; ".join(f"{a['dziennik_ustaw_ref']} (eff. {a['effective_date']})"
                                   for a in amendments)
                notes_text = f"legislative amendment: {cites}"
                explained = True
        findings.append(Finding(
            category=CATEGORY, series=f"art.{article}:{METRIC}", year=year,
            magnitude=magnitude, confidence=confidence,
            structural_break_explained=explained,
            notes=notes_text,
        ))
        its = its_level_shift(years, values, year)
        if its is not None:
            its_rows.append({"article": article, "series": METRIC, **its})
        arima = arima_level_shift(years, values, year)
        if arima is not None:
            arima_rows.append({"article": article, "series": METRIC, **arima})
    return findings, its_rows, arima_rows


def relative_change_ranking(df: pd.DataFrame, window: int) -> pd.DataFrame:
    rows = []
    for article, sub in df.groupby("article"):
        series = sub.set_index("year")[METRIC].dropna().sort_index()
        if len(series) <= window:
            continue
        last_year = series.index.max()
        baseline_year = last_year - window
        if baseline_year not in series.index:
            continue
        baseline, latest = series.loc[baseline_year], series.loc[last_year]
        if baseline == 0:
            continue
        rows.append({"article": article, "baseline_year": baseline_year, "latest_year": last_year,
                     "baseline": baseline, "latest": latest, "relative_change": (latest - baseline) / baseline})
    return pd.DataFrame(rows).sort_values("relative_change", key=abs, ascending=False)


def detection_ratio_series(df: pd.DataFrame) -> pd.DataFrame:
    national = df.groupby("year")[["postepowania_wszczete", METRIC]].sum(min_count=1)
    national["ratio"] = national[METRIC] / national["postepowania_wszczete"]
    return national.reset_index()


# The cluster identified in doc/analysis/trends.md §2.1 as a sustained 10-year
# rise (not a single-year anomaly): computer fraud, document/certification
# fraud, image-based privacy violation, illegal waste disposal, online
# grooming, money laundering, unauthorized computer access, use of false
# documents.
CYBER_CLUSTER_ARTICLES = ["287", "271", "191a", "183", "200a", "299", "267", "273"]


def cyber_cluster_series(df: pd.DataFrame) -> pd.DataFrame:
    sub = df[df["article"].astype(str).isin(CYBER_CLUSTER_ARTICLES) & (df["year"] >= 2013)]
    return sub[["article", "year", METRIC]].sort_values(["article", "year"])


def cyber_cluster_mann_kendall(cyber: pd.DataFrame) -> pd.DataFrame:
    """Formal test of the "sustained rise, no sign of plateauing" reading of
    the cyber cluster (doc/analysis/methodology.md / trends.md \\S2.1): Mann-Kendall
    trend test + Sen's slope per article, 2013-2023, in place of eyeballing
    "rose every year" off the raw series."""
    rows = []
    for article, sub in cyber.groupby("article"):
        series = sub.sort_values("year")
        mk = mann_kendall_test(series["year"].tolist(), series[METRIC].tolist())
        if mk is not None:
            rows.append({"article": article, **mk})
    return pd.DataFrame(rows).sort_values("article")


def main():
    df, notes = load_data()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    findings, its_rows, arima_rows = [], [], []
    for article, sub in df.groupby("article"):
        series = sub.set_index("year")[METRIC].dropna().sort_index()
        if len(series) < 6:
            continue
        article_findings, article_its, article_arima = gated_change_points(str(article), series, notes)
        findings.extend(article_findings)
        its_rows.extend(article_its)
        arima_rows.extend(article_arima)
    write_findings(findings, OUT_DIR / "findings.csv")

    its_df = pd.DataFrame(its_rows).sort_values(["article", "break_year"])
    its_df.to_csv(OUT_DIR / "its-regression.csv", index=False)
    write_chart_data_md(its_df, OUT_DIR / "its-regression.md",
                         "ITS (segmented-regression) level-shift estimate at each detected change-point")

    arima_df = pd.DataFrame(arima_rows).sort_values(["article", "break_year"])
    arima_df.to_csv(OUT_DIR / "arima-regression.csv", index=False)
    write_chart_data_md(arima_df, OUT_DIR / "arima-regression.md",
                         "ARIMA-intervention (Box-Tiao) level-shift estimate at each detected change-point, "
                         "as a robustness check on its-regression.csv's OLS-based estimate")

    for window, label in ((5, "5yr"), (10, "10yr")):
        ranking = relative_change_ranking(df, window)
        ranking.to_csv(OUT_DIR / f"ranking-{label}.csv", index=False)
        write_chart_data_md(ranking.head(20), OUT_DIR / f"ranking-{label}.md",
                             f"Top 20 articles by absolute relative change ({label})")

    ratio = detection_ratio_series(df)
    ratio.to_csv(OUT_DIR / "detection-ratio.csv", index=False)
    fig, ax = plt.subplots()
    ax.plot(ratio["year"], ratio["ratio"])
    ax.axvline(2013, color="grey", linestyle="--", label="2012-2013 counting-methodology change")
    ax.axvline(2014, color="grey", linestyle=":", label="2014 juvenile exclusion")
    ax.set_title("National crimes-confirmed / proceedings-initiated ratio")
    ax.set_xlabel("year")
    ax.set_ylabel("ratio")
    ax.legend()
    fig.savefig(OUT_DIR / "detection-ratio.png")
    plt.close(fig)
    write_chart_data_md(ratio, OUT_DIR / "detection-ratio.md",
                         "National crimes-confirmed / proceedings-initiated ratio")

    cyber = cyber_cluster_series(df)
    cyber.to_csv(OUT_DIR / "cyber-cluster.csv", index=False)
    fig, ax = plt.subplots()
    for article, sub in cyber.groupby("article"):
        ax.plot(sub["year"], sub[METRIC], label=f"art. {article}")
    ax.set_title("Cyber/digital-adjacent Penal Code article cluster, 2013-2023 (crimes confirmed)")
    ax.set_xlabel("year")
    ax.set_ylabel("crimes confirmed")
    ax.set_yscale("log")
    ax.legend(fontsize="small")
    fig.savefig(OUT_DIR / "cyber-cluster.png", bbox_inches="tight")
    plt.close(fig)
    write_chart_data_md(cyber, OUT_DIR / "cyber-cluster.md",
                         "Cyber/digital-adjacent Penal Code article cluster, 2013-2023")

    cyber_mk = cyber_cluster_mann_kendall(cyber)
    cyber_mk.to_csv(OUT_DIR / "cyber-cluster-mann-kendall.csv", index=False)
    write_chart_data_md(cyber_mk, OUT_DIR / "cyber-cluster-mann-kendall.md",
                         "Mann-Kendall trend test + Sen's slope, cyber cluster, 2013-2023")

    print(f"{len(findings)} candidate change-points written to {OUT_DIR / 'findings.csv'}")
    print(f"{len(its_df)} ITS level-shift estimates written to {OUT_DIR / 'its-regression.csv'}")
    print(f"{len(arima_df)} ARIMA-intervention estimates written to {OUT_DIR / 'arima-regression.csv'}")
    print(f"{len(cyber_mk)} Mann-Kendall trend tests written to {OUT_DIR / 'cyber-cluster-mann-kendall.csv'}")
    print(f"detection ratio series: {len(ratio)} years")


if __name__ == "__main__":
    main()

"""Anomaly/trend analysis for przestepstwa-ogolem, per doc/analysis/methodology.md §1.

The 2012->2013 counting-methodology change is a level shift, not a trend, so
every series here is split into a pre-2013 and a post-2013 segment and each
segment is change-point-tested independently rather than pooling across the
break. Regional (voivodeship) comparisons exclude the 2013-2016 BSW/CBSP
reattribution window.

**Added 2026-06-26:** the przestepstwa-korupcyjne category (corruption
offenses) is a police-defined aggregate, not a single Kodeks Karny article --
our data doesn't record which KK articles compose it. `CORRUPTION_KK_ARTICLES`
is a best-effort list of the well-known bribery/corruption-related articles
(228 lapownictwo, 229 lapownictwo czynne, 230/230a handel wplywami, 250a
lapownictwo wyborcze, 296a lapownictwo menedzerskie, 305 zmowa przetargowa),
used only to check for a *corroborating* legislative-amendment signal on this
one category -- flagged in notes as best-effort, not claimed as a confirmed
article-level mapping the way the kodeks-karny.py per-article check is.
"""
import pandas as pd
import matplotlib.pyplot as plt

from common import POLICJA, RESULTS, Finding, detect_change_points, flag_if_amended, \
    flag_if_near_break, load_population_total, normalize_voivodeship, write_chart_data_md, \
    write_findings

CATEGORY = "przestepstwa-ogolem"
OUT_DIR = RESULTS / CATEGORY
NATIONAL_METRICS = ["postepowania_wszczete", "przestepstwa_stwierdzone", "przestepstwa_wykryte"]
CORRUPTION_KK_ARTICLES = ["228", "229", "230", "230a", "250a", "296a", "305"]


def load_data():
    df = pd.read_csv(POLICJA / "przestepstwa-ogolem.csv")
    tree = pd.read_csv(POLICJA / "category-tree.csv")
    return df, tree


def national_series(df: pd.DataFrame, category_path: str, metric: str) -> pd.Series:
    # dimension_type isn't filtered here: postepowania_wszczete is sourced
    # from the police_unit-dimensioned file while przestepstwa_stwierdzone/
    # wykryte/pct_wykrycia come from the administrative_unit-dimensioned one,
    # but each metric belongs to exactly one dimension_type, and both carry
    # their own "national_total" row, so filtering on metric+unit alone is
    # unambiguous and picks up the right source for either.
    sub = df[
        (df["category_path"] == category_path)
        & (df["unit"] == "national_total")
        & (df["metric"] == metric)
    ]
    values = pd.to_numeric(sub.set_index("year")["value"], errors="coerce").dropna()
    return values.sort_index()


def national_rate_series(series: pd.Series) -> pd.Series:
    """Per-100k-population version of a national count series, used to confirm
    whether a raw-count change-point survives normalization (methodology.md
    "what counts as a finding" criterion 2) rather than being purely a
    population-decline/growth denominator effect."""
    return pd.Series({
        year: value / load_population_total("national", int(year)) * 1e5
        for year, value in series.items()
    }).sort_index()


def analyze_subtree(category_path: str, df: pd.DataFrame) -> list[Finding]:
    findings = []
    for metric in NATIONAL_METRICS:
        series = national_series(df, category_path, metric)
        if series.empty:
            continue
        rate_series = national_rate_series(series)
        for segment_name, segment in (
            ("pre-2013", series[series.index < 2013]),
            ("post-2013", series[series.index >= 2013]),
        ):
            if len(segment) < 6:
                continue
            years = segment.index.tolist()
            rate_segment = rate_series.loc[years]
            rate_cp_years = {y for y, _ in detect_change_points(years, rate_segment.values.tolist())}
            for year, confidence in detect_change_points(years, segment.values.tolist()):
                brk = flag_if_near_break(category_path, year)
                # A raw-count change-point only "survives" per-capita normalization
                # if the per-100k rate series shows a comparable shift nearby --
                # otherwise it's a candidate population-driven denominator effect,
                # tagged in `notes` rather than dropped (per project convention of
                # surfacing caveats instead of silently excluding rows).
                per_capita_confirmed = any(abs(year - ry) <= 1 for ry in rate_cp_years)
                notes = brk["nature"] if brk else ""
                explained = brk is not None
                if not explained and "korupcyjne" in category_path:
                    amendments = [a for art in CORRUPTION_KK_ARTICLES
                                  for a in (flag_if_amended(art, year) or [])]
                    if amendments:
                        cites = "; ".join(f"art.{a['article']} {a['dziennik_ustaw_ref']} "
                                          f"(eff. {a['effective_date']})" for a in amendments)
                        notes = (f"best-effort corroborating signal, not a confirmed article "
                                 f"mapping (see CORRUPTION_KK_ARTICLES docstring): {cites}")
                        explained = True
                if not per_capita_confirmed:
                    denom_note = ("raw-count change-point not confirmed by per-capita "
                                  "normalization -- may be a population-driven denominator "
                                  "effect rather than a genuine rate change")
                    notes = f"{notes}; {denom_note}" if notes else denom_note
                findings.append(Finding(
                    category=CATEGORY,
                    series=f"{category_path}:{metric}:{segment_name}",
                    year=year,
                    magnitude=float(series.loc[year] - series.loc[years[max(0, years.index(year) - 1)]]),
                    confidence=confidence,
                    structural_break_explained=explained,
                    per_capita_adjusted=per_capita_confirmed,
                    notes=notes,
                ))
    return findings


def national_per_capita(df: pd.DataFrame) -> pd.DataFrame:
    series = national_series(df, CATEGORY, "przestepstwa_stwierdzone")
    rows = []
    for year, value in series.items():
        pop = load_population_total("national", year)
        rows.append({"year": year, "value": int(value), "population": pop,
                     "rate_per_100k": round(value / pop * 1e5, 1)})
    return pd.DataFrame(rows)


def regional_per_capita(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Voivodeship rates per 100k for one year. Every row carries a
    `bsw_cbsp_caveat` rather than silently excluding any year, because the two
    project docs disagree on scope: doc/analysis/methodology.md treats only
    2013-2016 as unsafe, but the original source note (site-map/README.md:27)
    says pre-2013 regional figures attribute BSW/CBSP crimes to wherever the
    bureau's office physically sat, not where the crime occurred -- "affects
    regional comparisons across the 1999-2025 span," not just 2013-2016.
    Left for the reader to decide which years to trust for a given comparison."""
    sub = df[
        (df["category_path"] == CATEGORY)
        & (df["dimension_type"] == "administrative_unit")
        & (df["year"] == year)
        & (df["metric"] == "przestepstwa_stwierdzone")
        & (df["unit"] != "national_total")
        & (~df["unit"].isin(["kwp_radom_ksp_warszawa_combined"]))
        & (df["unit_raw"].str.startswith("woj."))
    ]
    rows = []
    for _, row in sub.iterrows():
        value = pd.to_numeric(row["value"], errors="coerce")
        if pd.isna(value):
            continue
        voivodeship = normalize_voivodeship(row["unit_raw"])
        pop = load_population_total(voivodeship, year)
        if year < 2013:
            caveat = "pre-2013: BSW/CBSP crimes attributed to bureau's garrison, not crime location"
        elif year <= 2016:
            caveat = "2013-2016: BSW/CBSP retroactive reattribution to voivodeship transitioning"
        else:
            caveat = "post-2016: BSW/CBSP geographically reattributed"
        rows.append({
            "voivodeship": voivodeship, "year": year, "value": value,
            "population": pop, "rate_per_100k": value / pop * 1e5,
            "bsw_cbsp_caveat": caveat,
        })
    return pd.DataFrame(rows)


CATEGORY_DIVERGENCE_PATHS = {
    "przestepstwa-ogolem/przestepstwa-kryminalne": "Violent/property offenses",
    "przestepstwa-ogolem/przestepstwa-gospodarcz": "Economic offenses",
    "przestepstwa-ogolem/przestepstwa-gospodarcz/przestepstwa-korupcyjne": "Corruption offenses",
}


def category_divergence(df: pd.DataFrame) -> pd.DataFrame:
    """National przestepstwa_stwierdzone for the three subtrees discussed in
    trends.md §1.2 (kryminalne vs. gospodarcze vs. korupcyjne), indexed to
    each series' first available year = 100, so the divergence is visible on
    one axis despite the underlying counts differing by two orders of
    magnitude."""
    frames = []
    for category_path, label in CATEGORY_DIVERGENCE_PATHS.items():
        series = national_series(df, category_path, "przestepstwa_stwierdzone")
        if series.empty:
            continue
        indexed = series / series.iloc[0] * 100
        frames.append(pd.DataFrame({
            "year": series.index, "category_path": category_path, "label": label,
            "value": series.values, "index_100": indexed.values,
        }))
    return pd.concat(frames, ignore_index=True)


def main():
    df, tree = load_data()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    findings = []
    for category_path in tree[tree["has_own_data"]]["category_path"]:
        findings.extend(analyze_subtree(category_path, df))
    write_findings(findings, OUT_DIR / "findings.csv")

    per_capita = national_per_capita(df)
    per_capita.to_csv(OUT_DIR / "national-per-capita.csv", index=False)

    fig, ax = plt.subplots()
    ax.plot(per_capita["year"], per_capita["rate_per_100k"])
    ax.axvline(2013, color="grey", linestyle="--", label="2013 counting-methodology change")
    ax.set_title("National crime rate per 100,000 population")
    ax.set_xlabel("year")
    ax.set_ylabel("crimes confirmed per 100,000 population")
    ax.legend()
    fig.savefig(OUT_DIR / "national-per-capita.png")
    plt.close(fig)
    write_chart_data_md(per_capita, OUT_DIR / "national-per-capita.md",
                         "National crime rate per 100,000 population")

    # Every year is included; bsw_cbsp_caveat (see regional_per_capita) tells
    # the reader which years are safe to compare for a given purpose, rather
    # than this script silently excluding any of them.
    regional = pd.concat([regional_per_capita(df, year) for year in sorted(df["year"].unique())], ignore_index=True)
    regional.to_csv(OUT_DIR / "regional-per-capita.csv", index=False)

    divergence = category_divergence(df)
    divergence.to_csv(OUT_DIR / "category-divergence.csv", index=False)
    fig, ax = plt.subplots()
    for category_path, label in CATEGORY_DIVERGENCE_PATHS.items():
        sub = divergence[divergence["category_path"] == category_path]
        ax.plot(sub["year"], sub["index_100"], label=label)
    ax.axvline(2013, color="grey", linestyle="--", label="2013 counting-methodology change")
    ax.set_title("Crimes confirmed by offense-category subtree (first year = 100)")
    ax.set_xlabel("year")
    ax.set_ylabel("index (first year = 100)")
    ax.legend()
    fig.savefig(OUT_DIR / "category-divergence.png")
    plt.close(fig)
    write_chart_data_md(divergence, OUT_DIR / "category-divergence.md",
                         "Crimes confirmed by offense-category subtree (first year = 100)")

    print(f"{len(findings)} candidate change-points written to {OUT_DIR / 'findings.csv'}")
    print(f"national per-capita series: {len(per_capita)} years")
    print(f"regional per-capita rows (all years, see bsw_cbsp_caveat column): {len(regional)}")


if __name__ == "__main__":
    main()

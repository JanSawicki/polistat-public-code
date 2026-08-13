"""Anomaly/trend analysis for the 12 wybrane-statystyki series, per doc/analysis/methodology.md §4.

Each sub-dataset has its own schema and its own judgment call about what's even
statistically testable (per the methodology doc) -- most are descriptive-only
given short/sparse series; a few get a specific treatment:
- przemoc-domowa: pre/post-2012 are two separate trend lines, never pooled.
- zamachy-samobojcze: three independent trend lines (1999-2012, 2013-2016, 2017+).
- przestepczosc-nieletni / zaginieni (minors): per-capita against a juvenile
  population proxy, with the BDL/source age-band mismatch flagged rather than
  silently glossed over.
- kradzieze-samochodow: cross-checked against the car-theft series inside
  przestepstwa-ogolem; a mismatch is a data-quality finding in its own right.
- handel-ludzmi-i-przest: treated as a small-count Poisson process, not a
  continuous series.
- maloletni-pod-wplywem / nietrzezwi-podejrzani: inline-HTML-only sources
  (no downloadable file -- see scripts/ingest/download_html_wybrane_statystyki.py),
  added after the rest of this dataset since they were never picked up by
  the regular download_and_parse.py pipeline.
"""
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import poisson

from common import POLICJA, RESULTS, Finding, detect_change_points, flag_if_near_break, \
    load_population_age_band, mann_kendall_test, write_chart_data_md, write_findings

WS = POLICJA / "wybrane-statystyki"
CATEGORY = "wybrane-statystyki"
OUT_DIR = RESULTS / CATEGORY

# BDL has no exact "juvenile" band; 0-19 is the closest available proxy to the
# under-18 populations these source series use. Documented, not hidden.
JUVENILE_BDL_BANDS = ["0-4", "5-9", "10-14", "15-19"]


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def bron(findings: list[Finding]) -> None:
    df = pd.read_csv(WS / "bron.csv")
    # "pozwolenia_ogolem" is a separate dataset (stock of permits in force, not
    # newly issued); within "pozwolenia_wydane" the "ogółem" dimension row is
    # already the yearly total -- summing the per-purpose rows too would double it.
    sub = df[(df["dataset"] == "pozwolenia_wydane") & (df["dimension"] == "ogółem")]
    annual = numeric(sub["value"]).groupby(sub["year"]).sum().sort_index().reset_index(name="permits_issued")
    out = OUT_DIR / "bron"
    out.mkdir(parents=True, exist_ok=True)
    annual.to_csv(out / "annual.csv", index=False)
    fig, ax = plt.subplots()
    ax.plot(annual["year"], annual["permits_issued"])
    ax.axvspan(2020, 2021, color="grey", alpha=0.2, label="COVID era")
    ax.set_title("Weapon permits issued (descriptive only, span too short for change-point testing)")
    ax.legend()
    fig.savefig(out / "annual.png")
    plt.close(fig)
    write_chart_data_md(annual, out / "annual.md", "Weapon permits issued, by year")

    # Span is too short for change-point detection (see module docstring), but
    # long enough (11 years) for a formal monotonic-trend test -- replaces an
    # eyeballed "accelerating rather than leveling off" reading of the raw series.
    mk = mann_kendall_test(annual["year"].tolist(), annual["permits_issued"].tolist())
    if mk is not None:
        pd.DataFrame([mk]).to_csv(out / "mann-kendall.csv", index=False)


def handel_ludzmi(findings: list[Finding]) -> None:
    # Datasets here are per-statute (art. 189a, 203, 204 §1-3, 204 §3); the
    # §3 dataset looks like it's "including minors" within the §1-3 figures
    # rather than a disjoint offense, so each dataset's Poisson test is run
    # on its own series rather than summed into one blended count.
    df = pd.read_csv(WS / "handel-ludzmi-i-przest.csv")
    out = OUT_DIR / "handel-ludzmi-i-przest"
    out.mkdir(parents=True, exist_ok=True)
    sub = df[df["metric"] == "Postępowania wszczęte"]
    for dataset, dsub in sub.groupby("dataset"):
        annual = numeric(dsub["value"]).groupby(dsub["year"]).sum().sort_index()
        if annual.empty:
            continue
        annual.reset_index(name="cases").to_csv(out / f"{dataset}.csv", index=False)
        write_chart_data_md(annual.reset_index(name="cases"), out / f"{dataset}.md",
                             f"{dataset}: proceedings opened, by year")
        mean_rate = annual.mean()
        for year, count in annual.items():
            # Two-sided exact Poisson test against the series' own mean rate --
            # appropriate for small counts where a continuous-series test isn't.
            p = 2 * min(poisson.cdf(count, mean_rate), 1 - poisson.cdf(count - 1, mean_rate))
            if p < 0.05:
                findings.append(Finding(
                    category=CATEGORY, series=f"handel-ludzmi-i-przest:{dataset}", year=int(year),
                    magnitude=float(count - mean_rate), confidence=float(1 - p),
                    structural_break_explained=False,
                    notes=f"Poisson exact test vs. series mean rate {mean_rate:.1f}, p={p:.3f}",
                ))


def kradzieze_samochodow_crosscheck(findings: list[Finding]) -> None:
    ws = pd.read_csv(WS / "kradzieze-samochodow.csv")
    ws_annual = numeric(ws[ws["metric"] == "Postępowania wszczęte"]["value"]).groupby(
        ws[ws["metric"] == "Postępowania wszczęte"]["year"]).sum()

    po = pd.read_csv(POLICJA / "przestepstwa-ogolem.csv")
    po_sub = po[
        (po["category_path"] == "przestepstwa-ogolem/przestepstwa-kryminalne/7-wybranych-kategorii-p"
                                  "/kradziez-cudzej-rzeczy/kradziez-samochodu")
        & (po["dimension_type"] == "police_unit")
        & (po["unit"] == "national_total")
        & (po["metric"] == "postepowania_wszczete")
    ]
    po_annual = numeric(po_sub.set_index("year")["value"])

    out = OUT_DIR / "kradzieze-samochodow"
    out.mkdir(parents=True, exist_ok=True)
    common_years = sorted(set(ws_annual.index) & set(po_annual.index))
    rows = []
    for year in common_years:
        a, b = ws_annual.loc[year], po_annual.loc[year]
        rel_diff = (a - b) / b if b else float("nan")
        rows.append({"year": year, "wybrane_statystyki": a, "przestepstwa_ogolem": b, "relative_diff": rel_diff})
        if abs(rel_diff) > 0.05:
            findings.append(Finding(
                category=CATEGORY, series="kradzieze-samochodow:crosscheck", year=int(year),
                magnitude=float(a - b), confidence=float(abs(rel_diff)),
                structural_break_explained=False, notes="independently-collected measures disagree by >5%",
            ))
    comparison = pd.DataFrame(rows)
    comparison.to_csv(out / "crosscheck.csv", index=False)
    write_chart_data_md(comparison, out / "crosscheck.md",
                         "Vehicle theft: Selected Statistics vs. Overall Crime proceedings-initiated")
    fig, ax = plt.subplots()
    ax.plot(comparison["year"], comparison["wybrane_statystyki"], label="Selected Statistics")
    ax.plot(comparison["year"], comparison["przestepstwa_ogolem"], label="Overall Crime")
    ax.set_title("Vehicle theft: two independently-collected measures")
    ax.legend()
    fig.savefig(out / "crosscheck.png")
    plt.close(fig)


def przemoc_domowa(findings: list[Finding]) -> None:
    # Each sub_period's file packs several non-additive datasets together
    # (interventions, victims, perpetrators, perpetrators-under-influence,
    # children-removed, ...) plus, within a dataset, "ogółem" totals
    # alongside their own lettered sub-breakdowns -- summing blindly over a
    # whole sub_period mixes incompatible counts and double-counts subtotals.
    # Pick the one series that tracks "interventions" continuously across
    # the registration-system change: "interwencje_domowe" pre-2012, then
    # the Niebieska Karta-A form count (its closest post-2012 analogue).
    SUB_PERIOD_SERIES = {
        "1999-2011": ("interwencje_domowe", "Interwencje domowe ogółem"),
        "2012-2022": ("niebieska_karta", "1. Liczba wypełnionych formularzy „Niebieska Karta – A” (ogółem), w tym:"),
        "2023-2025": ("niebieska_karta", "1. Liczba wypełnionych formularzy „Niebieska Karta – A” (ogółem), w tym:"),
    }
    df = pd.read_csv(WS / "przemoc-domowa.csv")
    out = OUT_DIR / "przemoc-domowa"
    out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots()
    all_rows = []
    for sub_period, (dataset, metric) in SUB_PERIOD_SERIES.items():
        sub = df[(df["sub_period"] == sub_period) & (df["dataset"] == dataset) & (df["metric"] == metric)]
        annual = numeric(sub["value"]).groupby(sub["year"]).first().sort_index()
        ax.plot(annual.index, annual.values, label=sub_period, marker="o")
        all_rows.append(annual.reset_index(name="value").assign(sub_period=sub_period))
        if len(annual) >= 6:
            for year, confidence in detect_change_points(annual.index.tolist(), annual.values.tolist()):
                findings.append(Finding(
                    category=CATEGORY, series=f"przemoc-domowa:{sub_period}", year=year,
                    magnitude=float(annual.loc[year]), confidence=confidence,
                    structural_break_explained=True,
                    notes="within a single registration-regime sub-period, but still worth surfacing",
                ))
    ax.set_title("Domestic violence interventions -- never pooled across the 2012 registration-system change")
    ax.legend()
    fig.savefig(out / "by-subperiod.png")
    plt.close(fig)
    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_csv(out / "by-subperiod.csv", index=False)
    write_chart_data_md(combined, out / "by-subperiod.md", "Domestic violence interventions, by sub-period")


def przestepczosc_nieletni(findings: list[Finding]) -> None:
    df = pd.read_csv(WS / "przestepczosc-nieletni.csv")
    # nieletni-czyny-zabronione1992-2013 only lists 6 selected offense types
    # (not exhaustive) and nieletni1990-2013 mixes totals with percentage rows
    # -- summing either across all their rows double-counts or mixes units.
    # The one row that's the actual national total juvenile-offenses count:
    sub = df[(df["dataset"] == "nieletni1990-2013") & (df["metric"] == "- w tym czyny karalne nieletnich")]
    annual = numeric(sub["value"]).groupby(sub["year"]).sum().sort_index()
    out = OUT_DIR / "przestepczosc-nieletni"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for year, value in annual.items():
        try:
            pop = load_population_age_band("national", int(year), JUVENILE_BDL_BANDS)
        except ValueError:
            continue
        rows.append({"year": year, "value": value, "juvenile_population_proxy_0_19": pop,
                     "rate_per_100k": value / pop * 1e5})
    per_capita = pd.DataFrame(rows)
    per_capita.to_csv(out / "per-capita.csv", index=False)
    write_chart_data_md(
        per_capita, out / "per-capita.md",
        "Juvenile offending per 100k juvenile population (proxy: BDL 0-19 band; "
        "source series' own age cutoffs don't align exactly -- see doc/analysis/methodology.md §4)")
    fig, ax = plt.subplots()
    ax.plot(per_capita["year"], per_capita["rate_per_100k"])
    ax.set_title("Juvenile offending per 100k (0-19 population proxy), ends 2013")
    fig.savefig(out / "per-capita.png")
    plt.close(fig)

    # Change-point detection on the rate itself (not the raw count) -- this
    # series is only ever meaningful per-capita given the demographic decline
    # over the same period (per doc/analysis/methodology.md §4), so there's no
    # separate raw-count Finding to gate here.
    if len(per_capita) >= 6:
        for year, confidence in detect_change_points(per_capita["year"].tolist(), per_capita["rate_per_100k"].tolist()):
            brk = flag_if_near_break("przestepczosc-nieletni", year)
            note = "rate vs. 0-19 population proxy, not raw count"
            findings.append(Finding(
                category=CATEGORY, series="przestepczosc-nieletni:rate_per_100k", year=int(year),
                magnitude=float(per_capita.set_index("year").loc[year, "rate_per_100k"]), confidence=confidence,
                structural_break_explained=brk is not None,
                per_capita_adjusted=True,
                notes=f"{brk['nature']}; {note}" if brk else note,
            ))


def utoniecia(findings: list[Finding]) -> None:
    df = pd.read_csv(WS / "utoniecia.csv")
    sub = df[df["topic"] == "przyczyna_utoniecia"]
    annual = numeric(sub["value"]).groupby(sub["year"]).sum().sort_index()
    out = OUT_DIR / "utoniecia"
    out.mkdir(parents=True, exist_ok=True)
    annual.reset_index(name="drownings").to_csv(out / "annual.csv", index=False)
    write_chart_data_md(
        annual.reset_index(name="drownings"), out / "annual.md",
        "Drownings by year, summed across cause categories (no month/date field in the "
        "aggregated source -- within-year seasonality can't be tested from this table)")
    fig, ax = plt.subplots()
    ax.plot(annual.index, annual.values)
    ax.set_title("Drownings, annual total (seasonality not testable -- no month field in source)")
    fig.savefig(out / "annual.png")
    plt.close(fig)


def wybrane_ustawy(findings: list[Finding]) -> None:
    df = pd.read_csv(WS / "wybrane-ustawy-szczegol.csv")
    out = OUT_DIR / "wybrane-ustawy-szczegol"
    out.mkdir(parents=True, exist_ok=True)
    sub = df[df["metric"] == "Przestępstwa stwierdzone"]
    for dataset, dsub in sub.groupby("dataset"):
        annual = numeric(dsub["value"]).groupby(dsub["year"]).sum().sort_index().reset_index(name="value")
        annual.to_csv(out / f"{dataset}.csv", index=False)
        write_chart_data_md(annual, out / f"{dataset}.md", f"{dataset} (descriptive only, too sparse for formal testing)")


def zaginieni(findings: list[Finding]) -> None:
    df = pd.read_csv(WS / "zaginieni.csv")
    out = OUT_DIR / "zaginieni"
    out.mkdir(parents=True, exist_ok=True)

    total = df[(df["dataset"] == "zaginieciaogolem1997-2024") & (df["metric"] == "Ogółem")]
    total_annual = numeric(total["value"]).groupby(total["year"]).sum().sort_index().reset_index(name="missing_persons")
    total_annual.to_csv(out / "annual-total.csv", index=False)
    write_chart_data_md(total_annual, out / "annual-total.md", "Missing persons reported, total, by year")

    # This dataset has no precomputed "Ogółem" row (unlike zaginieciaogolem
    # above) -- it's split into three mutually-exclusive age sub-bands
    # ("Do 7 lat" / "7-13 lat" / "14-17 lat") covering 0-17, so summing all
    # three per year gives the real total (confirmed by spot-check: filtering
    # for a literal "Ogółem" metric here silently returned zero rows).
    minors = df[df["dataset"] == "zaginieciamaloletni1997-2024"]
    minors_annual = numeric(minors["value"]).groupby(minors["year"]).sum().sort_index()
    rows = []
    for year, value in minors_annual.items():
        try:
            pop = load_population_age_band("national", int(year), JUVENILE_BDL_BANDS)
        except ValueError:
            continue
        rows.append({"year": year, "value": value, "juvenile_population_proxy_0_19": pop,
                     "rate_per_100k": value / pop * 1e5})
    minors_per_capita = pd.DataFrame(rows)
    minors_per_capita.to_csv(out / "minors-per-capita.csv", index=False)
    write_chart_data_md(
        minors_per_capita, out / "minors-per-capita.md",
        "Missing minors per 100k juvenile population (proxy: BDL 0-19 band; source's "
        "Do 7 / 7-13 / 14-17 cutoffs don't align exactly with BDL 5-year bands)")

    # Same rationale as przestepczosc_nieletni above: demographic decline over
    # this span means the raw count is not interpretable on its own, so
    # change-point detection runs on the rate, not the count.
    if len(minors_per_capita) >= 6:
        for year, confidence in detect_change_points(
                minors_per_capita["year"].tolist(), minors_per_capita["rate_per_100k"].tolist()):
            findings.append(Finding(
                category=CATEGORY, series="zaginieni:minors_rate_per_100k", year=int(year),
                magnitude=float(minors_per_capita.set_index("year").loc[year, "rate_per_100k"]),
                confidence=confidence, structural_break_explained=False,
                per_capita_adjusted=True, notes="rate vs. 0-19 population proxy, not raw count",
            ))


def zamachy_samobojcze(findings: list[Finding]) -> None:
    # The "ogółem" total's exact metric label changes wording with each
    # reporting-form change (confirmed by inspecting the file's own `fatal`
    # column: each period's general -- fatal=False -- total uses a different
    # string). The file also re-reports that same national total once per
    # breakdown topic (sposob_powod, wiek, miejsce_popelnienia, ...) -- 4-6
    # duplicate rows per (period, year), all carrying the identical value, so
    # summing across topics inflates the total several-fold; deduplicate to
    # one row per year instead.
    PERIOD_TOTAL_METRIC = {
        "od-1999-do-2012": "Liczba zamachów ogółem",
        "od-2013-do-2016": "Liczba osób w zamachach samobójczych ogółem",
        "od-2017": "Liczba osób w zamachach samobójczych ogółem (PRÓBY I ZAKOŃCZONE ZGONEM)",
    }
    PERIOD_DISPLAY_LABEL = {
        "od-1999-do-2012": "1999-2012",
        "od-2013-do-2016": "2013-2016",
        "od-2017": "2017-2025",
    }
    df = pd.read_csv(WS / "zamachy-samobojcze.csv")
    df = df[(df["unit"] == "Polska") & (df["fatal"] == False)]  # noqa: E712
    out = OUT_DIR / "zamachy-samobojcze"
    out.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots()
    all_rows, mk_rows = [], []
    for period, total_metric in PERIOD_TOTAL_METRIC.items():
        psub = df[(df["period"] == period) & (df["metric"] == total_metric)]
        annual = numeric(psub["value"]).groupby(psub["year"]).first().sort_index()
        ax.plot(annual.index, annual.values, label=PERIOD_DISPLAY_LABEL[period], marker="o")
        all_rows.append(annual.reset_index(name="value").assign(period=period))
        if len(annual) >= 6:
            for year, confidence in detect_change_points(annual.index.tolist(), annual.values.tolist()):
                findings.append(Finding(
                    category=CATEGORY, series=f"zamachy-samobojcze:{period}", year=year,
                    magnitude=float(annual.loc[year]), confidence=confidence,
                    structural_break_explained=True,
                    notes="within a single reporting-form period, but still worth surfacing",
                ))
        # Per-period Mann-Kendall, never pooled across periods (same
        # never-pool convention as the structural-break gating above) --
        # replaces an eyeballed "essentially monotonically" reading.
        mk = mann_kendall_test(annual.index.tolist(), annual.values.tolist())
        if mk is not None:
            mk_rows.append({"period": period, **mk})
    ax.set_title("Suicide attempts -- three independent trend lines (2013, 2017 reporting-form changes)")
    ax.legend()
    fig.savefig(out / "by-period.png")
    plt.close(fig)
    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_csv(out / "by-period.csv", index=False)
    write_chart_data_md(combined, out / "by-period.md", "Suicide attempts (national), by reporting-form period")
    pd.DataFrame(mk_rows).to_csv(out / "mann-kendall.csv", index=False)


def maloletni_pod_wplywem(findings: list[Finding]) -> None:
    # Inline-HTML-only source (no downloadable file -- see
    # scripts/ingest/download_html_wybrane_statystyki.py), 2000-2017. The
    # "ujawnieni" (revealed) series has full 18-year coverage; the
    # "dowiezieni" (transported to sobering facilities) breakdown only runs
    # 2000-2009 in the source itself (post-2009 cells are "b.d.", dropped at
    # aggregation, not zero), so the two are charted together but only the
    # full-coverage series is run through change-point/trend testing.
    df = pd.read_csv(WS / "maloletni-pod-wplywem.csv")
    out = OUT_DIR / "maloletni-pod-wplywem"
    out.mkdir(parents=True, exist_ok=True)

    revealed = df[df["metric"] == "Liczba ujawnionych przez Policję nietrzeźwych osób do 18 roku życia"]
    revealed_annual = numeric(revealed["value"]).groupby(revealed["year"]).sum().sort_index()
    transported = df[df["metric"] == "Liczba osób do 18 roku życia dowiezionych przez Policję do izb wytrzeźwień (ogółem)"]
    transported_annual = numeric(transported["value"]).groupby(transported["year"]).sum().sort_index()

    combined = pd.DataFrame({"revealed": revealed_annual, "transported_total": transported_annual}).reset_index(
        names="year")
    combined.to_csv(out / "annual.csv", index=False)
    write_chart_data_md(
        combined, out / "annual.md",
        "Minors (under 18) found intoxicated by police, by year -- 'revealed' has full "
        "2000-2017 coverage, 'transported to sobering facilities' only 2000-2009 in the source")
    fig, ax = plt.subplots()
    ax.plot(combined["year"], combined["revealed"], label="Revealed intoxicated", marker="o")
    ax.plot(combined["year"], combined["transported_total"], label="Transported to sobering facility", marker="o")
    ax.set_title("Minors under the influence of alcohol, 2000-2017")
    ax.legend()
    fig.savefig(out / "annual.png")
    plt.close(fig)

    years, values = revealed_annual.index.tolist(), revealed_annual.values.tolist()
    if len(years) >= 6:
        for year, confidence in detect_change_points(years, values):
            findings.append(Finding(
                category=CATEGORY, series="maloletni-pod-wplywem:revealed", year=int(year),
                magnitude=float(revealed_annual.loc[year]), confidence=confidence,
                structural_break_explained=False,
                notes="series ends 2017 in the source; no later data available to check persistence",
            ))
    mk = mann_kendall_test(years, values)
    if mk is not None:
        pd.DataFrame([mk]).to_csv(out / "mann-kendall.csv", index=False)


def nietrzezwi_podejrzani(findings: list[Finding]) -> None:
    # Inline-HTML-only source, 1999-2012, 7 crime categories x 14 years. Per
    # category: track "podejrzani dorośli nietrzeźwi" (confirmed-intoxicated
    # adult suspects) as the headline count, and separately the share of
    # suspects-with-established-sobriety who were intoxicated (a rate less
    # sensitive to the category's own raw caseload trend) -- never pooled
    # across categories, since they're different offense types with
    # different base rates, not one series.
    df = pd.read_csv(WS / "nietrzezwi-podejrzani-o-popeln.csv")
    out = OUT_DIR / "nietrzezwi-podejrzani"
    out.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots()
    all_rows, mk_rows = [], []
    for category, csub in df.groupby("dimension"):
        intox = csub[csub["metric"] == "podejrzani dorośli nietrzeźwi"].set_index("year")["value"].sort_index()
        established = csub[csub["metric"] == "podejrzani dorośli z ustaloną trzeźwością"].set_index("year")["value"].sort_index()
        rate = (intox / established).rename("intoxicated_share_of_established")
        rows = pd.DataFrame({"intoxicated_count": intox, "intoxicated_share_of_established": rate}).reset_index(
            names="year").assign(category=category)
        all_rows.append(rows)
        ax.plot(rows["year"], rows["intoxicated_share_of_established"], label=category, marker="o")

        years, values = intox.index.tolist(), intox.values.tolist()
        if len(years) >= 6:
            for year, confidence in detect_change_points(years, values):
                findings.append(Finding(
                    category=CATEGORY, series=f"nietrzezwi-podejrzani:{category}:count", year=int(year),
                    magnitude=float(intox.loc[year]), confidence=confidence,
                    structural_break_explained=False,
                    notes="adult intoxicated-suspect count; series ends 2012 in the source",
                ))
        mk = mann_kendall_test(years, intox.values.tolist())
        if mk is not None:
            mk_rows.append({"category": category, **mk})

    ax.set_title("Share of adult suspects (with established sobriety) who were intoxicated, by offense type, 1999-2012")
    ax.legend(fontsize="small")
    fig.savefig(out / "intoxicated-share-by-category.png")
    plt.close(fig)
    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_csv(out / "by-category.csv", index=False)
    write_chart_data_md(combined, out / "by-category.md",
                         "Adult intoxicated-suspect count and share of established-sobriety suspects, by offense "
                         "category, 1999-2012 (series ends 2012 in the source)")
    pd.DataFrame(mk_rows).to_csv(out / "mann-kendall.csv", index=False)


def zgony_z_powodu_wychlodzenia(findings: list[Finding]) -> None:
    # Excluding month=="SUMA" alone isn't enough: each individual month block
    # in the location-breakdown datasets *also* carries its own metric=="SUMA"
    # row (a within-month total across the 10 location categories) -- without
    # also excluding that, summing double-counts every month (verified
    # against the table's own SUMA row: e.g. Listopad 2018's 10 categories
    # sum to 14, matching its SUMA row of 14 exactly -- so including SUMA too
    # gives 28). NaN category values genuinely mean zero deaths in that
    # location that month (also confirmed against SUMA rows), not missing
    # data, so summing with skipna is correct once SUMA itself is excluded.
    df = pd.read_csv(WS / "zgony-z-powodu-wychlodzenia.csv")
    out = OUT_DIR / "zgony-z-powodu-wychlodzenia"
    out.mkdir(parents=True, exist_ok=True)
    df = df[~df["month"].isin(["SUMA", "Osoba bezdomna wymagająca pomocy"]) & (df["metric"] != "SUMA")]
    df["value"] = numeric(df["value"])
    by_season = df.groupby("season_start_year")["value"].sum().sort_index().reset_index(name="deaths")
    by_season["season_over_season_change"] = by_season["deaths"].diff()
    by_season.to_csv(out / "by-season.csv", index=False)
    write_chart_data_md(
        by_season, out / "by-season.md",
        "Hypothermia deaths by winter season (Nov-Mar); too short/seasonal for trend "
        "methods -- season-over-season comparison only, ideally against winter-severity data (not yet sourced)")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    findings: list[Finding] = []
    bron(findings)
    handel_ludzmi(findings)
    kradzieze_samochodow_crosscheck(findings)
    maloletni_pod_wplywem(findings)
    nietrzezwi_podejrzani(findings)
    przemoc_domowa(findings)
    przestepczosc_nieletni(findings)
    utoniecia(findings)
    wybrane_ustawy(findings)
    zaginieni(findings)
    zamachy_samobojcze(findings)
    zgony_z_powodu_wychlodzenia(findings)
    write_findings(findings, OUT_DIR / "findings.csv")
    print(f"{len(findings)} candidate findings written to {OUT_DIR / 'findings.csv'}")


if __name__ == "__main__":
    main()

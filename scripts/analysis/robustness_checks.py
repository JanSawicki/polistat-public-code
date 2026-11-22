"""One-off robustness checks for paper/paper.tex's per-article anomaly table
(Table tab:anomalies / tab:its), requested during peer-review response:
1. PELT penalty sensitivity -- does each of the 13 major anomalies' break year
   still get detected under a range of penalty values, not just pen=3.0?
2. Major-anomaly-threshold sensitivity -- how does the anomaly count and
   attribution rate change across a small grid of (pct, base) thresholds,
   instead of just the 30%/100 pair used in the paper?
3. Curation audit -- every PELT candidate passing the (30%, 100) threshold with
   an explicit disposition at each curation step, showing the full 23→13 path
   from all threshold-passing candidates to the 13 reported major anomalies.
   Writes results/kodeks-karny/robustness-curation-audit.csv.
4. Small-count GLM refit (Poisson / NB) for the three lowest-base anomalies.

Not part of the regular pipeline -- run on demand, writes results/kodeks-karny/robustness-*.csv.
"""
import pandas as pd
import numpy as np
import ruptures as rpt
import statsmodels.api as sm

from common import POLICJA, RESULTS, detect_change_points

OUT_DIR = RESULTS / "kodeks-karny"
METRIC = "przestepstwa_stwierdzone"

# The 13 major anomalies as reported in paper.tex Table tab:anomalies/tab:its.
MAJOR_ANOMALIES = [
    ("268+268a", 2009), ("299", 2019), ("267", 2019), ("202", 2009),
    ("303", 2004), ("230+230a", 2004), ("220", 2004), ("273", 2019),
    ("226", 2004), ("300", 2009), ("258", 2004), ("244", 2004), ("174", 2009),
]

ATTRIBUTED = {  # per Table tab:anomalies' "Cause" column
    "268+268a": True, "299": True, "267": False, "202": True, "303": True,
    "230+230a": True, "220": True, "273": False, "226": True, "300": True,
    "258": True, "244": True, "174": True,
}


def load_series(article):
    df = pd.read_csv(POLICJA / "kodeks-karny.csv")
    sub = df[df["article"].astype(str) == article].set_index("year")[METRIC].dropna().sort_index()
    return sub.index.tolist(), sub.values.tolist()


def pelt_breaks_at_penalty(years, values, pen):
    arr = np.asarray(values, dtype=float)
    algo = rpt.Pelt(model="rbf", min_size=3).fit(arr)
    bps = [b for b in algo.predict(pen=pen) if b < len(arr)]
    return [years[b] for b in bps]


def penalty_sensitivity():
    rows = []
    for article, break_year in MAJOR_ANOMALIES:
        years, values = load_series(article)
        for pen in (1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0):
            detected = pelt_breaks_at_penalty(years, values, pen)
            hit = any(abs(d - break_year) <= 1 for d in detected)
            rows.append({"article": article, "break_year": break_year, "penalty": pen,
                         "n_breaks_detected": len(detected), "anomaly_still_detected": hit})
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "robustness-pelt-penalty.csv", index=False)
    summary = out.groupby("penalty")["anomaly_still_detected"].mean().rename("fraction_of_13_still_detected")
    summary.to_csv(OUT_DIR / "robustness-pelt-penalty-summary.csv")
    print("PELT penalty sensitivity (fraction of 13 anomalies still detected within +-1yr):")
    print(summary.to_string())
    return out


def pelt_candidates():
    """Same candidate-generation step the paper actually uses (PELT, pen=3.0,
    same as production `detect_change_points`) -- NOT a brute-force scan of
    every consecutive year pair, which would include many single-year blips
    PELT itself never flags as a change-point. Returns one row per (article,
    break year, immediate before/after value), matching the before/after
    definition `kodeks_karny.gated_change_points` uses for `magnitude`."""
    df = pd.read_csv(POLICJA / "kodeks-karny.csv")
    rows = []
    for article, sub in df.groupby("article"):
        series = sub.set_index("year")[METRIC].dropna().sort_index()
        if len(series) < 6:
            continue
        years, values = series.index.tolist(), series.values.tolist()
        for year, _confidence in detect_change_points(years, values):
            idx = years.index(year)
            before, after = values[max(0, idx - 1)], values[idx]
            rows.append({"article": str(article), "year": year, "before": before, "after": after})
    return pd.DataFrame(rows)


def threshold_sensitivity():
    candidates = pelt_candidates()
    rows = []
    for pct in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
        for base in (50, 100, 150, 200):
            sub = candidates[
                (candidates["before"] > 0)
                & (candidates[["before", "after"]].min(axis=1) >= base)
                & ((candidates["after"] - candidates["before"]).abs() / candidates["before"] >= pct)
            ]
            rows.append({"pct_threshold": pct, "base_threshold": base,
                         "n_major_anomalies": len(sub)})
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "robustness-threshold-grid.csv", index=False)
    print(f"\nPELT-detected candidate change-points (all articles, before threshold filter): {len(candidates)}")
    print("Major-anomaly count across (pct, base) threshold grid, applied to PELT candidates only:")
    print(out.pivot(index="pct_threshold", columns="base_threshold", values="n_major_anomalies").to_string())
    return out


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Standard BH step-up FDR procedure. Returns a same-length list of
    booleans: True if that p-value survives FDR control at `alpha`."""
    n = len(pvalues)
    order = np.argsort(pvalues)
    sorted_p = np.asarray(pvalues)[order]
    thresholds = (np.arange(1, n + 1) / n) * alpha
    below = sorted_p <= thresholds
    if not below.any():
        cutoff_rank = 0
    else:
        cutoff_rank = np.max(np.where(below)[0]) + 1
    sig_sorted = np.zeros(n, dtype=bool)
    sig_sorted[:cutoff_rank] = True
    sig = np.zeros(n, dtype=bool)
    sig[order] = sig_sorted
    return sig.tolist()


def fdr_correction():
    its = pd.read_csv(OUT_DIR / "its-regression.csv")
    arima = pd.read_csv(OUT_DIR / "arima-regression.csv")

    def subset_major(d):
        keys = {(a, y) for a, y in MAJOR_ANOMALIES}
        d = d.copy()
        d["key"] = list(zip(d["article"].astype(str), d["break_year"].astype(int)))
        return d[d["key"].isin(keys)].drop(columns="key")

    its_major = subset_major(its)
    arima_major = subset_major(arima)
    its_major["bh_significant"] = benjamini_hochberg(its_major["pvalue"].tolist())
    arima_major["bh_significant"] = benjamini_hochberg(arima_major["pvalue"].tolist())
    its_major.to_csv(OUT_DIR / "robustness-its-fdr.csv", index=False)
    arima_major.to_csv(OUT_DIR / "robustness-arima-fdr.csv", index=False)
    print("\nITS: raw-alpha-significant vs BH-FDR-significant (13 major anomalies):")
    print(its_major[["article", "break_year", "pvalue", "bh_significant"]]
          .assign(raw_significant=lambda d: d["pvalue"] < 0.05)
          .to_string(index=False))
    print("\nARIMA: raw-alpha-significant vs BH-FDR-significant (13 major anomalies):")
    print(arima_major[["article", "break_year", "pvalue", "bh_significant"]]
          .assign(raw_significant=lambda d: d["pvalue"] < 0.05)
          .to_string(index=False))
    return its_major, arima_major


SMALL_COUNT_ARTICLES = [("174", 2009), ("220", 2004), ("230+230a", 2004)]


def glm_level_shift(years, values, break_year, model_cls):
    """Same segmented-regression design as `common.its_level_shift` (t centered
    on break_year, post-break indicator D, D*t), refit as a count model
    (Poisson or negative-binomial with a log link, dispersion estimated by
    MLE for NB rather than fixed) instead of OLS -- appropriate for these
    series since they're annual counts, some in the low hundreds, where OLS's
    constant-variance assumption is a worse fit than a count model's
    mean-scaled variance. Returns the level shift on the original count scale
    at the break (exp(b0)*(exp(b2)-1), i.e. the model's fitted jump from just
    before to just after t=0) alongside the model's own log-scale coefficient
    and its p-value, so it's comparable to its-regression.csv's level_shift."""
    years_arr = np.asarray(years, dtype=float)
    values_arr = np.asarray(values, dtype=float)
    t = years_arr - break_year
    d = (years_arr >= break_year).astype(float)
    design = sm.add_constant(np.column_stack([t, d, d * t]))
    model = model_cls(values_arr, design).fit(disp=False)
    b0, b2 = model.params[0], model.params[2]
    level_shift_count_scale = float(np.exp(b0) * (np.exp(b2) - 1))
    ci_low, ci_high = model.conf_int()[2]
    return {
        "log_scale_coef": float(b2), "log_scale_pvalue": float(model.pvalues[2]),
        "level_shift_count_scale": level_shift_count_scale,
        "log_scale_ci_low": float(ci_low), "log_scale_ci_high": float(ci_high),
    }


def small_count_glm_refit():
    import statsmodels.discrete.discrete_model as dm

    its = pd.read_csv(OUT_DIR / "its-regression.csv")
    rows = []
    for article, break_year in SMALL_COUNT_ARTICLES:
        years, values = load_series(article)
        poisson = glm_level_shift(years, values, break_year, dm.Poisson)
        try:
            nb = glm_level_shift(years, values, break_year, dm.NegativeBinomial)
        except Exception:
            nb = {k: None for k in poisson}
        ols_row = its[(its["article"] == article) & (its["break_year"] == break_year)]
        ols_shift = float(ols_row["level_shift"].iloc[0]) if not ols_row.empty else None
        ols_p = float(ols_row["pvalue"].iloc[0]) if not ols_row.empty else None
        rows.append({"article": article, "break_year": break_year,
                     "ols_its_level_shift": ols_shift, "ols_its_pvalue": ols_p,
                     "poisson_level_shift": poisson["level_shift_count_scale"],
                     "poisson_pvalue": poisson["log_scale_pvalue"],
                     "nb_level_shift": nb["level_shift_count_scale"],
                     "nb_pvalue": nb["log_scale_pvalue"]})
    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "robustness-small-count-glm.csv", index=False)
    print("\nSmall-count articles: OLS-ITS vs Poisson-GLM vs NB-GLM level-shift estimates:")
    print(out.to_string(index=False))
    return out


def curation_audit():
    """Show every PELT candidate passing the production (30%, 100) threshold
    with a disposition column explaining why each one is retained in the 13
    reported major anomalies or excluded at a specific curation step.

    Curation steps applied in order:
      1. Gated year: the candidate's break year equals the article's
         first_year_in_data or known_amendment_year in kodeks-karny-notes.csv.
         A break at the first data year is an artifact of the series starting,
         not a genuine level shift; a break at a known-amendment year is already
         accounted for by the notes gate and excluded from findings.csv.
      2. Registry-break-batch: the break year coincides with a global entry in
         the structural-break registry (Table tab:breaks) that applies to many
         articles simultaneously -- specifically the 2014 juvenile-exclusion
         break. These are counted as one registry entry, not as N individual
         anomaly findings.
      3. Retained: the (article, year) pair is in MAJOR_ANOMALIES; it passed
         the threshold, was not gated, and is not subsumed by a batch registry
         entry.

    Writes results/kodeks-karny/robustness-curation-audit.csv."""
    notes = pd.read_csv(POLICJA / "kodeks-karny-notes.csv").set_index("article")

    findings_path = RESULTS / "kodeks-karny" / "findings.csv"
    findings_df = pd.read_csv(findings_path) if findings_path.exists() else pd.DataFrame()
    if not findings_df.empty:
        findings_df["_article"] = findings_df["series"].str.extract(r"art\.(.+?):")

    candidates = pelt_candidates()

    filtered = candidates[
        (candidates["before"] > 0)
        & (candidates[["before", "after"]].min(axis=1) >= 100)
        & ((candidates["after"] - candidates["before"]).abs() / candidates["before"] >= 0.30)
    ].copy()
    filtered["pct_change"] = (filtered["after"] - filtered["before"]) / filtered["before"]

    major_set = {(a, y) for a, y in MAJOR_ANOMALIES}

    def disposition(row):
        article, year = str(row["article"]), int(row["year"])
        # Step 1: gated year
        if article in notes.index:
            fyd = notes.loc[article, "first_year_in_data"]
            kay = notes.loc[article, "known_amendment_year"]
            gate = {v for v in (fyd, kay) if pd.notna(v) and v == int(v)}
            if year in gate:
                return "excluded: gated year (first_year_in_data or known_amendment_year)"
        # Step 2: retained in the 13
        if (article, year) in major_set:
            return "retained"
        # Step 3: registry-break-batch or unattributed exclusion
        if not findings_df.empty:
            match = findings_df[
                (findings_df["_article"] == article) & (findings_df["year"] == year)
            ]
            if not match.empty and match["structural_break_explained"].iloc[0]:
                note = str(match["notes"].iloc[0]) if pd.notna(match["notes"].iloc[0]) else ""
                return f"excluded: registry-break-batch ({note[:80]})"
        return "excluded: not in reported 13 (structural break or below attribution threshold)"

    filtered["disposition"] = filtered.apply(disposition, axis=1)
    out = filtered.sort_values(["year", "article"]).reset_index(drop=True)
    out.to_csv(OUT_DIR / "robustness-curation-audit.csv", index=False)

    retained = (out["disposition"] == "retained").sum()
    print(f"\nCuration audit: {len(out)} PELT candidates pass (30%, 100) threshold → "
          f"{retained} retained as major anomalies")
    print("\nDisposition breakdown:")
    print(out["disposition"].value_counts().to_string())
    print("\nFull candidate table (article, year, before, after, pct_change, disposition):")
    print(out[["article", "year", "before", "after", "pct_change", "disposition"]]
          .to_string(index=False))
    return out


if __name__ == "__main__":
    penalty_sensitivity()
    threshold_sensitivity()
    curation_audit()
    small_count_glm_refit()

"""Shared utilities for the per-category analysis scripts.

See `doc/analysis/methodology.md` for what these encode and why, and
`doc/analysis/implementation.md` §3 for the interface this module commits to.
"""
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

import warnings

import numpy as np
import pandas as pd
import ruptures as rpt
import statsmodels.api as sm
from scipy.stats import norm
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX

ROOT = Path(__file__).resolve().parent.parent.parent
POLICJA = ROOT / "data-aggregated" / "statystyka-policja"
BDL = ROOT / "data-aggregated" / "statystyka-baza-danych-lokalnych"
RESULTS = ROOT / "results"

NATIONAL_UNIT_ID = "000000000000"

# Encodes the structural-break registry from doc/analysis/methodology.md.
# `year`/`year_end` describe the affected span (year_end=None for a single-year
# break); `affects` lists the series/category substrings it applies to.
STRUCTURAL_BREAKS = [
    {"year": 2012, "year_end": None, "affects": ["przemoc-domowa"],
     "nature": "registration system change (Niebieskie Karty)"},
    {"year": 2012, "year_end": 2013, "affects": ["przestepstwa-ogolem", "kodeks-karny"],
     "nature": "police-led vs. police+prosecutor counting"},
    {"year": 2013, "year_end": 2016, "affects": ["wojewodztwo"],
     "nature": "BSW/CBSP regional reattribution"},
    {"year": 2013, "year_end": None, "affects": ["zamachy-samobojcze"],
     "nature": "reporting form change (KSIP 10)"},
    {"year": 2014, "year_end": None, "affects": ["kodeks-karny", "przestepczosc-nieletni"],
     "nature": "juvenile offenses excluded entirely"},
    {"year": 2014, "year_end": None, "affects": ["art. 197", "art. 178a"],
     "nature": "prosecution-mode / statute changes"},
    {"year": 2017, "year_end": None, "affects": ["zamachy-samobojcze"],
     "nature": "reporting form change #2"},
]


def _token_specificity(token: str) -> int:
    """Per-article tokens (e.g. "art. 178a") are more specific than a
    category-wide name (e.g. "kodeks-karny") even though the latter is a
    longer string -- rank by what the token actually targets, not length."""
    return 1 if token.startswith("art.") else 0


def flag_if_near_break(series_name: str, year: int, tolerance: int = 1):
    """Return the matching structural-break dict if `year` falls in (or within
    `tolerance` of) a break window that applies to `series_name`, else None.
    When more than one break matches (e.g. a per-article break like "art.
    178a" and the broader "kodeks-karny" both apply to the same finding),
    prefers the more specific one so a generic category-wide break doesn't
    silently shadow a more informative specific one. Ties in specificity
    (e.g. "kodeks-karny" matches both the 2012-2013 counting break and the
    2014 juvenile-exclusion break) are broken by picking whichever break's
    *untolerated* window is actually closest to `year` -- otherwise the tie
    silently fell back to list order, mislabeling e.g. every 2014 kodeks-karny
    finding with the 2012-2013 break's note instead of the correct 2014 one."""
    candidates = []
    for brk in STRUCTURAL_BREAKS:
        matching_tokens = [t for t in brk["affects"] if t in series_name]
        if not matching_tokens:
            continue
        core_start, core_end = brk["year"], brk["year_end"] or brk["year"]
        start, end = core_start - tolerance, core_end + tolerance
        if start <= year <= end:
            distance = max(0, core_start - year, year - core_end)
            specificity = max(_token_specificity(t) for t in matching_tokens)
            candidates.append((specificity, -distance, brk))
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c[0], c[1]))[2]


DZIENNIK_USTAW = ROOT / "data-aggregated" / "dziennik-ustaw"


@lru_cache(maxsize=1)
def load_kk_amendments() -> pd.DataFrame:
    """Kodeks Karny's per-article amendment registry, sourced from Dziennik
    Ustaw via scripts/aggregate/dziennik_ustaw.py -- see doc/dziennik-ustaw.md.
    Extends (does not replace) STRUCTURAL_BREAKS: that registry hardcodes a
    handful of known, broad methodology changes; this one is a sourced
    per-article amendment history checked against Findings individually."""
    df = pd.read_csv(DZIENNIK_USTAW / "kodeks-karny-amendments.csv")
    df["effective_year"] = pd.to_numeric(df["effective_date"].str[:4], errors="coerce")
    return df


def flag_if_amended(article: str, year: int, tolerance: int = 1):
    """Return the matching amendment row(s) (as a list of dicts) if `year` is
    within `tolerance` years *after* an amendment affecting `article` (an
    amendment can only explain a finding in the same year or later, never
    retroactively -- a still-future amendment is not a candidate cause), else
    None. `article` may be a combined id like "230+230a" (kodeks-karny.csv's
    convention for articles whose source data isn't split) -- checked against
    each half independently, since the registry tracks them as separate
    articles, the way Dziennik Ustaw itself does."""
    amendments = load_kk_amendments()
    sub_articles = article.split("+")
    matches = amendments[
        amendments["article"].isin(sub_articles)
        & amendments["effective_year"].notna()
        & (amendments["effective_year"] >= year - tolerance)
        & (amendments["effective_year"] <= year)
    ]
    if matches.empty:
        return None
    return matches.drop_duplicates(subset=["article", "eli_id"]).to_dict("records")


def normalize_voivodeship(name: str) -> str:
    """Canonicalize a voivodeship name for joining policja.pl data (mixed-case,
    inconsistent hyphen spacing, sometimes a "woj." prefix) against BDL's
    upper-case ASCII-diacritics unit_name column."""
    name = name.upper().strip()
    name = re.sub(r"^WOJ\.?\s*", "", name)
    name = re.sub(r"\s*\(.*\)\s*$", "", name)  # e.g. "MAZOWIECKIE (KWP Z/S W RADOMIU I KSP WARSZAWA)"
    name = re.sub(r"[‐-―]", "-", name)  # en/em-dash variants (confirmed in ruch-drogowy's "Kujawsko – pomorskie")
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"\s+", " ", name)
    return name


@lru_cache(maxsize=1)
def _residence_df() -> pd.DataFrame:
    return pd.read_csv(BDL / "population-by-residence-sex.csv", dtype={"unit_id": str})


@lru_cache(maxsize=1)
def _age_group_df() -> pd.DataFrame:
    return pd.read_csv(BDL / "population-by-age-group-sex.csv", dtype={"unit_id": str})


def _unit_mask(df: pd.DataFrame, unit: str) -> pd.Series:
    if unit in ("national", "POLSKA", NATIONAL_UNIT_ID):
        return df["unit_id"] == NATIONAL_UNIT_ID
    return df["unit_name"] == normalize_voivodeship(unit)


def load_population_total(unit: str, year: int, *, sex: str = "total",
                           residence_area: str = "total",
                           population_basis: str = "actual_residence",
                           as_of_date: str = "12-31") -> int:
    """Total population for `unit` (national, or a voivodeship name in either
    policja.pl or BDL casing) in `year`, under the default per-capita filters
    from doc/analysis/implementation.md §1."""
    df = _residence_df()
    rows = df[
        _unit_mask(df, unit)
        & (df["year"] == year)
        & (df["residence_area"] == residence_area)
        & (df["population_basis"] == population_basis)
        & (df["as_of_date"] == as_of_date)
        & (df["sex"] == sex)
    ]
    if rows.empty:
        raise ValueError(f"no population row for unit={unit!r} year={year}")
    if len(rows) > 1:
        raise ValueError(f"ambiguous population rows for unit={unit!r} year={year}")
    return int(rows["population"].iloc[0])


def load_population_age_band(unit: str, year: int, bands: list[str], *,
                              sex: str = "total") -> int:
    """Sum population over `bands` (must be exact `age_group` values from the
    `detailed` rows). Raises rather than silently summing rollup rows, which
    would double-count per doc/bdl-population.md."""
    df = _age_group_df()
    sub = df[
        _unit_mask(df, unit)
        & (df["year"] == year)
        & (df["sex"] == sex)
        & (df["age_group_kind"] == "detailed")
    ]
    missing = set(bands) - set(sub["age_group"])
    if missing:
        raise ValueError(
            f"age bands {missing} not found among detailed rows for unit={unit!r} year={year}"
        )
    return int(sub[sub["age_group"].isin(bands)]["population"].sum())


def rolling_zscore(values: pd.Series, window: int = 5) -> pd.Series:
    """Z-score of each point against the trailing `window` points (excluding itself)."""
    mean = values.shift(1).rolling(window).mean()
    std = values.shift(1).rolling(window).std()
    return (values - mean) / std


def detect_change_points(years: list[int], values: list[float],
                          model: str = "rbf", pen: float = 3.0) -> list[tuple[int, float]]:
    """Ruptures-based change-point detection. Returns (year, confidence) pairs,
    where confidence is the normalized magnitude of the level shift at that point."""
    arr = np.asarray(values, dtype=float)
    if len(arr) < 6:
        return []
    algo = rpt.Pelt(model=model, min_size=3).fit(arr)
    breakpoints = [b for b in algo.predict(pen=pen) if b < len(arr)]
    spread = arr.std() or 1.0
    out = []
    for bp in breakpoints:
        before = arr[max(0, bp - 3):bp].mean()
        after = arr[bp:bp + 3].mean()
        confidence = abs(after - before) / spread
        out.append((years[bp], float(confidence)))
    return out


def its_level_shift(years: list[int], values: list[float], break_year: int,
                     min_side_n: int = 3) -> dict | None:
    """Interrupted-time-series (segmented regression) estimate of the level
    shift at `break_year`, per Chen et al. 2020's joinpoint-style alternative
    to hard before/after segmentation: y = b0 + b1*t + b2*D + b3*D*t, with t
    centered on break_year (t=0 at the break) so b2 is the model's estimate
    of the jump *at* the break itself, net of the pre- and post-break linear
    trends, rather than a raw difference of means. Returns None if either
    side of the break has fewer than `min_side_n` points -- not enough to fit
    a trend on that side, the likeliest failure mode for breaks near a
    series' start or end (e.g. the 2023 theft-threshold check, one year from
    the kodeks-karny series' own end)."""
    years_arr = np.asarray(years, dtype=float)
    values_arr = np.asarray(values, dtype=float)
    n_before = int((years_arr < break_year).sum())
    n_after = int((years_arr >= break_year).sum())
    if n_before < min_side_n or n_after < min_side_n:
        return None
    t = years_arr - break_year
    d = (years_arr >= break_year).astype(float)
    design = sm.add_constant(np.column_stack([t, d, d * t]))
    model = sm.OLS(values_arr, design).fit()
    ci_low, ci_high = model.conf_int()[2]
    return {
        "break_year": break_year,
        "level_shift": float(model.params[2]),
        "se": float(model.bse[2]),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "pvalue": float(model.pvalues[2]),
        "n_before": n_before,
        "n_after": n_after,
    }


def arima_level_shift(years: list[int], values: list[float], break_year: int,
                       order: tuple[int, int, int] = (1, 0, 0),
                       min_side_n: int = 3) -> dict | None:
    """ARIMA-intervention (Box-Tiao style) robustness check on the same level
    shift `its_level_shift` estimates by OLS: same regressors (t centered on
    break_year, post-break indicator D, and D*t), but fit jointly with an
    ARMA(`order`) error process via SARIMAX instead of assuming i.i.d.
    residuals -- OLS standard errors are understated whenever a series is
    genuinely autocorrelated, which year-over-year crime counts often are.
    Returns None under the same insufficient-data condition as
    `its_level_shift`, or if the ARMA fit fails to converge at all (falls
    back to ARMA(0,0,0), i.e. plain OLS-with-MLE, before giving up)."""
    years_arr = np.asarray(years, dtype=float)
    values_arr = np.asarray(values, dtype=float)
    n_before = int((years_arr < break_year).sum())
    n_after = int((years_arr >= break_year).sum())
    if n_before < min_side_n or n_after < min_side_n:
        return None
    t = years_arr - break_year
    d = (years_arr >= break_year).astype(float)
    exog = np.column_stack([t, d, d * t])
    for try_order in (order, (0, 0, 0)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                fit = SARIMAX(values_arr, exog=exog, order=try_order, trend="c").fit(disp=False)
            except Exception:
                continue
        if fit.mle_retvals.get("converged", True):
            break
    else:
        return None
    idx = list(fit.param_names).index("x2")  # x1=t, x2=D, x3=D*t, per the exog column order above
    ci_low, ci_high = fit.conf_int()[idx]
    return {
        "break_year": break_year,
        "order": try_order,
        "level_shift": float(fit.params[idx]),
        "se": float(fit.bse[idx]),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "pvalue": float(fit.pvalues[idx]),
        "n_before": n_before,
        "n_after": n_after,
    }


def mann_kendall_test(years: list[int], values: list[float]) -> dict | None:
    """Mann-Kendall test for a monotonic trend (Mann 1945; Kendall 1975),
    with the Theil-Sen slope estimator (Sen 1968) as the accompanying effect
    size -- the standard nonparametric pairing for "is there a trend" (the
    test) plus "how steep is it" (the slope), used here in place of an
    eyeballed "rose every year" reading of a raw series. Distribution-free and
    robust to outliers, unlike a fitted linear-regression slope. Returns None
    for fewer than 4 points, the same floor `detect_change_points` effectively
    requires (Kendall's S statistic is degenerate below that)."""
    years_arr = np.asarray(years, dtype=float)
    values_arr = np.asarray(values, dtype=float)
    n = len(values_arr)
    if n < 4:
        return None
    diffs = values_arr[None, :] - values_arr[:, None]  # diffs[i, j] = x_j - x_i (later minus earlier)
    s = float(np.sign(np.triu(diffs, k=1)).sum())
    _, tie_counts = np.unique(values_arr, return_counts=True)
    tie_term = sum(int(c) * (c - 1) * (2 * c + 5) for c in tie_counts if c > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18
    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        z = 0.0
    pvalue = float(2 * (1 - norm.cdf(abs(z))))
    i_idx, j_idx = np.triu_indices(n, k=1)
    slopes = (values_arr[j_idx] - values_arr[i_idx]) / (years_arr[j_idx] - years_arr[i_idx])
    sen_slope = float(np.median(slopes))
    trend = "no trend"
    if pvalue < 0.05:
        trend = "increasing" if s > 0 else "decreasing"
    return {"s": s, "z": float(z), "pvalue": pvalue, "sen_slope": sen_slope, "trend": trend, "n": n}


def stl_decompose(values: pd.Series, period: int):
    """STL seasonal decomposition wrapper for the ruch-drogowy within-year series."""
    return STL(values, period=period, robust=True).fit()


@dataclass
class Finding:
    category: str
    series: str
    year: int
    magnitude: float
    confidence: float
    structural_break_explained: bool
    per_capita_adjusted: bool = False
    notes: str = ""


def write_findings(findings: list[Finding], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(f) for f in findings])
    df.sort_values(["category", "year"]).to_csv(path, index=False)


def write_chart_data_md(df: pd.DataFrame, path: Path, title: str) -> None:
    """Write the data behind a chart as a Markdown table, alongside its PNG
    (same base name, `.md` extension) so the numbers survive without
    re-running the script."""
    path.parent.mkdir(parents=True, exist_ok=True)
    formatted = df.copy()
    for col in formatted.select_dtypes(include="number").columns:
        if col == "year":
            continue
        formatted[col] = formatted[col].map(lambda v: f"{v:,}" if float(v).is_integer() else f"{v:,.2f}")
    with open(path, "w") as f:
        f.write(f"# {title}\n\n")
        f.write(formatted.to_markdown(index=False))
        f.write("\n")

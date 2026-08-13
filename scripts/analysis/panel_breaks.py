"""Panel common-break detection, per doc/analysis/panel-break-detection.md.

Formalizes the project's central by-eye inference -- a change-point landing on
the *same year across several substantively unrelated article series* is the
signature of a recording/classification-rule change (a measurement artifact),
not N independent real-world events -- as a permutation test with an
FWER-controlled null, an artifact-vs-substance decomposition of every detected
break, and a supervised-style evaluation against the sourced ground-truth
benchmark in `benchmark/system-break-labels.csv`.

Primary method (this file): the permutation common-break test of
`permutation_common_breaks`. It reuses `common.detect_change_points` (PELT) for
per-series candidate breaks, so results stay comparable to the univariate
analysis in `kodeks_karny.py`. The heavier hierarchical-Bayesian confirmatory
layer (doc §3) is left for the HPC and is not implemented here.

The statistical core (`permutation_common_breaks`, `decompose_breaks`,
`evaluate_against_labels`) is deliberately decoupled from both PELT and the
project's data: it takes a plain `{series_id: [break_years]}` mapping, so it is
dataset-agnostic (the "reusable detector" of doc §4.3) and verifiable without
the gitignored `data-aggregated/` via `--self-test`.

Usage:
    python panel_breaks.py --self-test     # synthetic panel, no real data needed
    python panel_breaks.py                 # production run on kodeks-karny.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
BENCHMARK = ROOT / "benchmark" / "system-break-labels.csv"
# Breadth is counted at the EXACT year: the documented coincidences in this
# dataset (2004, 2009, 2019) share a year exactly, and 2013 vs 2014 are two
# genuinely distinct adjacent system years that must not be merged -- a +/-1
# breadth window would both smear each break across its neighbours (inflating
# the null) and conflate 2013 with 2014. The +/-1 tolerance is used only for
# MATCHING a detected year against a benchmark label / a system year, to absorb
# PELT's occasional one-year placement jitter.
BREADTH_TOL = 0
MATCH_TOL = 1
MIN_SIZE = 3     # matches common.detect_change_points' PELT min_size


# --------------------------------------------------------------------------- #
# Statistical core -- numpy only, decoupled from PELT and from this dataset.   #
# --------------------------------------------------------------------------- #

def _year_grid(panel_years: dict[str, list[int]]) -> np.ndarray:
    years = sorted({y for ys in panel_years.values() for y in ys})
    return np.asarray(years, dtype=int)


def _covered_mask(break_years, grid: np.ndarray, tol: int) -> np.ndarray:
    """Boolean over `grid`: True at each grid year within `tol` of ANY break.
    A series with two breaks near the same year still contributes once (the
    breadth statistic counts series, not breaks)."""
    covered = np.zeros(grid.shape, dtype=bool)
    for b in break_years:
        covered |= np.abs(grid - b) <= tol
    return covered


def _breadth(breaks_per_series: dict[str, list[int]], grid: np.ndarray, tol: int) -> np.ndarray:
    """Breadth W(t): number of series with a break within `tol` of grid year t."""
    breadth = np.zeros(grid.shape, dtype=int)
    for years in breaks_per_series.values():
        if years:
            breadth += _covered_mask(years, grid, tol).astype(int)
    return breadth


def permutation_common_breaks(
    panel_years: dict[str, list[int]],
    breaks_per_series: dict[str, list[int]],
    *,
    tol: int = BREADTH_TOL,
    min_size: int = MIN_SIZE,
    n_perm: int = 5000,
    alpha: float = 0.05,
    seed: int = 0,
) -> pd.DataFrame:
    """Test which years carry a system-wide (cross-series) break.

    For each grid year t the observed breadth W(t) is compared against a null
    in which each series' breaks are relocated uniformly among its own
    detectable interior years -- preserving every series' break *count* and
    *span* but destroying cross-series synchrony. Each year gets a permutation
    p-value against *its own* null breadth distribution, and the year grid is
    then FDR-controlled with Benjamini-Hochberg (`qvalue`); a year is flagged
    system-wide at `qvalue < alpha`. A year-specific null with FDR control is
    what keeps the test powered to find a narrow real cluster (a handful of
    coincident articles) sitting in the same panel as a broad one -- the
    grid-wide-maximum (FWER) alternative lets one very broad break inflate the
    bar until narrower-but-real system years fall under it, so it is reported
    only as a conservative secondary (`fwer_threshold`).

    Returns one row per grid year: observed breadth, raw permutation p-value,
    BH q-value, the system-wide flag, and the FWER reference threshold.
    """
    grid = _year_grid(panel_years)
    rng = np.random.default_rng(seed)

    # Per-series eligible interior years (where PELT could place a break) and
    # the number of breaks to relocate for that series.
    eligible: dict[str, np.ndarray] = {}
    n_breaks: dict[str, int] = {}
    for sid, years in panel_years.items():
        ys = np.asarray(sorted(years), dtype=int)
        interior = ys[min_size: len(ys) - min_size] if len(ys) > 2 * min_size else ys[:0]
        eligible[sid] = interior
        obs = breaks_per_series.get(sid, [])
        n_breaks[sid] = min(len(obs), len(interior))

    observed = _breadth(breaks_per_series, grid, tol)

    # Full null breadth matrix (n_perm x n_years), not just its row-max: a
    # year-specific null is what gives the test power to find a narrow real
    # cluster (a handful of coincident articles) in the same panel as a broad
    # one. Controlling on the grid-wide MAX breadth alone (the FWER variant,
    # kept below as a conservative secondary) lets one very broad break inflate
    # the bar until narrower-but-real system years fall under it.
    null = np.empty((n_perm, grid.size), dtype=int)
    for r in range(n_perm):
        breadth = np.zeros(grid.shape, dtype=int)
        for sid, interior in eligible.items():
            k = n_breaks[sid]
            if k == 0:
                continue
            placed = rng.choice(interior, size=k, replace=False)
            breadth += _covered_mask(placed, grid, tol).astype(int)
        null[r] = breadth

    # Per-year permutation p-value against that year's own null, then
    # Benjamini-Hochberg FDR control across the year grid (BH 1995) -- the
    # primary flag. The FWER max-statistic threshold is retained for reference.
    pvalues = np.array([(1 + int((null[:, j] >= observed[j]).sum())) / (n_perm + 1)
                        for j in range(grid.size)])
    qvalues = _benjamini_hochberg(pvalues)
    null_max = null.max(axis=1)
    fwer_threshold = float(np.quantile(null_max, 1 - alpha))

    return pd.DataFrame({
        "year": grid,
        "breadth": observed,
        "pvalue": pvalues,
        "qvalue": qvalues,
        "system_wide": (qvalues < alpha) & (observed > 0),
        "fwer_threshold": fwer_threshold,
    })


def _benjamini_hochberg(pvalues: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR-adjusted p-values (q-values), numpy-only."""
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]      # enforce monotonicity
    q = np.empty(m, dtype=float)
    q[order] = np.clip(ranked, 0, 1)
    return q


def weighted_breadth(breaks_with_mag: dict[str, list[tuple[int, float]]],
                     grid: np.ndarray, tol: int = BREADTH_TOL) -> np.ndarray:
    """Descriptive magnitude-weighted breadth: for each grid year, sum over
    series of that series' largest break magnitude within `tol` (0 if none).
    Not permutation-tested -- reported alongside the count breadth as a sense
    of how *large* the coincident shifts are, not just how many."""
    wb = np.zeros(grid.shape, dtype=float)
    for pairs in breaks_with_mag.values():
        if not pairs:
            continue
        per_series = np.zeros(grid.shape, dtype=float)
        for b, mag in pairs:
            near = np.abs(grid - b) <= tol
            per_series = np.maximum(per_series, near * float(mag))
        wb += per_series
    return wb


def decompose_breaks(breaks_with_mag: dict[str, list[tuple[int, float]]],
                     system_years: list[int], tol: int = MATCH_TOL) -> pd.DataFrame:
    """Tag every detected break as shared (within `tol` of a flagged
    system-wide year -> artifact channel) or idiosyncratic (the substantive
    channel). One row per (series, break)."""
    sys_arr = np.asarray(sorted(system_years), dtype=int)
    rows = []
    for sid, pairs in breaks_with_mag.items():
        for b, mag in pairs:
            if sys_arr.size:
                dist = int(np.abs(sys_arr - b).min())
                nearest = int(sys_arr[np.abs(sys_arr - b).argmin()])
            else:
                dist, nearest = 10**6, -1
            shared = dist <= tol
            rows.append({
                "series": sid, "break_year": b, "magnitude": float(mag),
                "channel": "shared" if shared else "idiosyncratic",
                "nearest_system_year": nearest if shared else "",
            })
    return pd.DataFrame(rows).sort_values(["break_year", "series"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Evaluation against the sourced benchmark (applied-data-science layer).       #
# --------------------------------------------------------------------------- #

def evaluate_against_labels(flagged_years: list[int], labels: pd.DataFrame,
                            family: str, *, tol: int = MATCH_TOL) -> dict:
    """Score the detector's system-wide year calls against the ground-truth
    labels in `benchmark/system-break-labels.csv`.

    `system-wide` labels are the positive class (precision/recall/F1, +/-tol
    match). `unresolved` labels (e.g. 2019 art. 267/273) are HELD OUT of the
    arithmetic: a flag there is reported separately as an out-of-sample
    prediction of an undocumented recording break, not scored. Years labeled
    only `article-specific`/`narrow` that also coincide with a `system-wide`
    year are a documented confound (a real amendment landing on a
    recording-change year cannot be separated from it by timing alone) and are
    noted, not counted as false positives.
    """
    fam = labels[labels["series_family"] == family]
    positives = sorted(set(fam[fam["scope"] == "system-wide"]["break_year"]))
    unresolved = sorted(set(fam[fam["scope"] == "unresolved"]["break_year"]))
    flagged = sorted(set(flagged_years))

    def near(y, pool):
        return any(abs(y - p) <= tol for p in pool)

    tp = [y for y in flagged if near(y, positives) and not near(y, unresolved)]
    fn = [p for p in positives if not near(p, flagged)]
    predictions = [y for y in flagged if near(y, unresolved)]      # held-out hits
    fp = [y for y in flagged if not near(y, positives) and not near(y, unresolved)]

    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else float("nan")
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall and not (np.isnan(precision) or np.isnan(recall)) else float("nan"))

    return {
        "positives": positives, "unresolved": unresolved, "flagged": flagged,
        "true_positive_years": tp, "false_positive_years": fp, "false_negative_years": fn,
        "held_out_predictions": predictions,
        "precision": precision, "recall": recall, "f1": f1,
    }


def threshold_sweep(breadth_df: pd.DataFrame, labels: pd.DataFrame, family: str,
                    *, tol: int = MATCH_TOL) -> pd.DataFrame:
    """ROC-style sweep of the breadth threshold: at each cutoff, how many
    labeled system-wide years are recovered (TPR) vs. how many candidate
    non-system years are flagged (FPR). Shows the separability of system years
    from idiosyncratic ones as a curve, not a single operating point."""
    fam = labels[labels["series_family"] == family]
    positives = sorted(set(fam[fam["scope"] == "system-wide"]["break_year"]))
    unresolved = sorted(set(fam[fam["scope"] == "unresolved"]["break_year"]))

    def near(y, pool):
        return any(abs(y - p) <= tol for p in pool)

    candidate_years = breadth_df[breadth_df["breadth"] > 0]["year"].tolist()
    neg_years = [y for y in candidate_years if not near(y, positives) and not near(y, unresolved)]

    rows = []
    for thr in range(1, int(breadth_df["breadth"].max()) + 1):
        flagged = breadth_df[breadth_df["breadth"] >= thr]["year"].tolist()
        tp = sum(1 for p in positives if near(p, flagged))
        fp = sum(1 for y in neg_years if near(y, flagged))
        rows.append({
            "breadth_threshold": thr,
            "tpr": tp / len(positives) if positives else float("nan"),
            "fpr": fp / len(neg_years) if neg_years else 0.0,
            "n_flagged": len(flagged),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Figures (matplotlib imported lazily so the core/self-test needs no plotting).#
# --------------------------------------------------------------------------- #

def plot_break_heatmap(breaks_with_mag, grid, system_years, out_png: Path) -> None:
    import matplotlib.pyplot as plt
    series_ids = sorted(breaks_with_mag)
    mat = np.zeros((len(series_ids), len(grid)), dtype=float)
    year_idx = {int(y): j for j, y in enumerate(grid)}
    for i, sid in enumerate(series_ids):
        for b, mag in breaks_with_mag[sid]:
            if b in year_idx:
                mat[i, year_idx[b]] = max(mat[i, year_idx[b]], float(mag))
    fig, ax = plt.subplots(figsize=(10, max(4, len(series_ids) * 0.12)))
    im = ax.imshow(mat, aspect="auto", cmap="magma",
                   extent=[grid.min() - 0.5, grid.max() + 0.5, len(series_ids) - 0.5, -0.5])
    for y in system_years:
        ax.axvline(y, color="cyan", linewidth=0.8, alpha=0.6)
    ax.set_xlabel("year")
    ax.set_ylabel("series (article)")
    ax.set_yticks(range(len(series_ids)))
    ax.set_yticklabels(series_ids, fontsize=4)
    ax.set_title("Per-series break intensity; vertical lines = flagged system-wide years")
    fig.colorbar(im, ax=ax, label="normalized break magnitude")
    fig.savefig(out_png, bbox_inches="tight", dpi=150)
    plt.close(fig)


def plot_breadth(breadth_df: pd.DataFrame, system_years, predictions, out_png: Path) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar(breadth_df["year"], breadth_df["breadth"], color="#4c78a8")
    thr = breadth_df["fwer_threshold"].iloc[0]
    ax.axhline(thr, color="grey", linestyle="--", label=f"permutation threshold ({thr:.1f})")
    for y in system_years:
        tag = "prediction" if y in predictions else "documented"
        color = "crimson" if y in predictions else "seagreen"
        ax.annotate(f"{y}\n{tag}", (y, breadth_df.set_index("year").loc[y, "breadth"]),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    fontsize=7, color=color)
    ax.set_xlabel("year")
    ax.set_ylabel("breadth (series breaking)")
    ax.set_title("Cross-series break breadth by year")
    ax.legend()
    fig.savefig(out_png, bbox_inches="tight", dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------- #
# Production run (needs data-aggregated/, gitignored).                         #
# --------------------------------------------------------------------------- #

def _load_panel_kodeks_karny(metric: str):
    from common import POLICJA  # local import: pulls ruptures/statsmodels
    df = pd.read_csv(POLICJA / "kodeks-karny.csv")
    panel_years, panel_values = {}, {}
    for article, sub in df.groupby("article"):
        s = sub.set_index("year")[metric].dropna().sort_index()
        if len(s) < 6:                       # PELT floor, matches kodeks_karny.py
            continue
        sid = f"art.{article}"
        panel_years[sid] = s.index.tolist()
        panel_values[sid] = s.values.tolist()
    return panel_years, panel_values


def _detect_all(panel_years, panel_values):
    from common import detect_change_points
    breaks_with_mag = {}
    for sid in panel_years:
        breaks_with_mag[sid] = [
            (int(y), float(c))
            for y, c in detect_change_points(panel_years[sid], panel_values[sid])
        ]
    return breaks_with_mag


def main():
    from common import RESULTS, write_chart_data_md
    metric = "przestepstwa_stwierdzone"
    out_dir = RESULTS / "panel-breaks"
    out_dir.mkdir(parents=True, exist_ok=True)

    panel_years, panel_values = _load_panel_kodeks_karny(metric)
    breaks_with_mag = _detect_all(panel_years, panel_values)
    breaks_years = {sid: [y for y, _ in pairs] for sid, pairs in breaks_with_mag.items()}

    breadth_df = permutation_common_breaks(panel_years, breaks_years)
    grid = breadth_df["year"].to_numpy()
    breadth_df["weighted_breadth"] = weighted_breadth(breaks_with_mag, grid)
    system_years = breadth_df[breadth_df["system_wide"]]["year"].tolist()

    labels = pd.read_csv(BENCHMARK)
    ev = evaluate_against_labels(system_years, labels, "kodeks-karny")
    decomp = decompose_breaks(breaks_with_mag, system_years)
    sweep = threshold_sweep(breadth_df, labels, "kodeks-karny")

    breadth_df.to_csv(out_dir / "breadth-by-year.csv", index=False)
    write_chart_data_md(breadth_df.drop(columns=["fwer_threshold"]),
                        out_dir / "breadth-by-year.md",
                        "Cross-series break breadth and permutation p-value by year")
    decomp.to_csv(out_dir / "break-decomposition.csv", index=False)
    sweep.to_csv(out_dir / "threshold-sweep.csv", index=False)
    pd.DataFrame([{
        "precision": ev["precision"], "recall": ev["recall"], "f1": ev["f1"],
        "true_positives": ";".join(map(str, ev["true_positive_years"])),
        "false_positives": ";".join(map(str, ev["false_positive_years"])),
        "false_negatives": ";".join(map(str, ev["false_negative_years"])),
        "held_out_predictions": ";".join(map(str, ev["held_out_predictions"])),
    }]).to_csv(out_dir / "evaluation.csv", index=False)

    plot_break_heatmap(breaks_with_mag, grid, system_years, out_dir / "break-heatmap.png")
    plot_breadth(breadth_df, system_years, ev["held_out_predictions"], out_dir / "breadth.png")

    print(f"panel: {len(panel_years)} article series, {sum(map(len, breaks_years.values()))} candidate breaks")
    print(f"system-wide years flagged: {system_years}")
    print(f"benchmark eval: precision={ev['precision']:.2f} recall={ev['recall']:.2f} f1={ev['f1']:.2f}")
    print(f"held-out predictions (flagged but undocumented): {ev['held_out_predictions']}")
    print(f"-> {out_dir}")


# --------------------------------------------------------------------------- #
# Self-test: synthetic panel, verifies the core with numpy/pandas only.        #
# --------------------------------------------------------------------------- #

def self_test():
    """Inject common breaks at 2008 and 2015 into a subset of otherwise-noisy
    series and confirm the permutation test recovers exactly those years and
    rejects scattered idiosyncratic breaks. Uses candidate breaks directly (no
    PELT), so it runs with numpy/pandas alone and isolates the novel core."""
    rng = np.random.default_rng(42)
    years = list(range(2000, 2025))
    n_series = 60
    panel_years = {f"s{i}": list(years) for i in range(n_series)}
    breaks_with_mag: dict[str, list[tuple[int, float]]] = {f"s{i}": [] for i in range(n_series)}

    # System-wide breaks: 2008 in 18 series, 2015 in 15 series (unrelated sets).
    for i in rng.choice(n_series, size=18, replace=False):
        breaks_with_mag[f"s{i}"].append((2008, float(rng.uniform(1.0, 2.5))))
    for i in rng.choice(n_series, size=15, replace=False):
        breaks_with_mag[f"s{i}"].append((2015, float(rng.uniform(1.0, 2.5))))
    # Idiosyncratic scatter: each series independently, one break at a random
    # interior year -- no year should accumulate enough to look system-wide.
    for i in range(n_series):
        yr = int(rng.integers(2004, 2021))
        breaks_with_mag[f"s{i}"].append((yr, float(rng.uniform(0.8, 1.5))))

    breaks_years = {s: [y for y, _ in p] for s, p in breaks_with_mag.items()}
    breadth_df = permutation_common_breaks(panel_years, breaks_years, n_perm=2000, seed=1)
    flagged = set(breadth_df[breadth_df["system_wide"]]["year"])

    print(breadth_df[breadth_df["breadth"] > 0].to_string(index=False))
    print(f"\nflagged system-wide: {sorted(flagged)}")

    assert {2008, 2015} <= flagged, f"failed to recover injected common breaks: {sorted(flagged)}"
    spurious = flagged - {2008, 2015}
    assert not spurious, f"flagged idiosyncratic years as system-wide: {sorted(spurious)}"

    # Decomposition + evaluation smoke test against a tiny synthetic label set.
    decomp = decompose_breaks(breaks_with_mag, [2008, 2015])
    shared = (decomp["channel"] == "shared").sum()
    idio = (decomp["channel"] == "idiosyncratic").sum()
    synth_labels = pd.DataFrame([
        {"series_family": "synthetic", "break_year": 2008, "scope": "system-wide"},
        {"series_family": "synthetic", "break_year": 2015, "scope": "system-wide"},
        {"series_family": "synthetic", "break_year": 2099, "scope": "unresolved"},
    ])
    ev = evaluate_against_labels(sorted(flagged), synth_labels, "synthetic")
    assert ev["recall"] == 1.0 and ev["precision"] == 1.0, ev
    print(f"decomposition: {shared} shared, {idio} idiosyncratic breaks")
    print(f"evaluation: precision={ev['precision']:.2f} recall={ev['recall']:.2f} f1={ev['f1']:.2f}")
    print("\nself-test PASSED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="run the synthetic-panel self-test (no real data needed)")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        main()

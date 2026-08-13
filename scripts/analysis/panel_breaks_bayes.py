"""Hierarchical Bayesian common-changepoint layer -- the confirmatory
cross-check for the permutation test in `panel_breaks.py`, per
doc/analysis/panel-break-detection.md §3.

It is the *confirmatory* layer, not the primary: the permutation test
(`panel_breaks.py`) is what flags system-wide years; this puts a posterior
probability on the same question so that agreement between the two is stronger
evidence than either alone, and a disagreement is reported as such. Per doc §3
it is gated behind "the permutation result holds up" (it does: 2004/2009/2014
flagged, 2019 not) and must never block the paper on the sampler converging.

Model (noisy-OR of a shared and an idiosyncratic break hazard). For every
(series i, year t) cell where series i *could* break -- its PELT-eligible
interior years, exactly the cells the permutation null relocates within -- the
observed break indicator z_{i,t} in {0,1} (1 iff series i has a PELT break at t)
is modelled as

    z_{i,t} ~ Bernoulli( pi_t + (1 - pi_t) * rho_i ),
    pi_t   ~ Beta(A_PI,  B_PI)     # year-level *system* break hazard (sparse)
    rho_i  ~ Beta(A_RHO, B_RHO)    # series-level *idiosyncratic* hazard (sparse)

so a break happens either for a system reason (prob pi_t, shared across all
series that year) or idiosyncratically (prob rho_i): P(break) = 1 - (1-pi_t)(1-rho_i)
= pi_t + (1-pi_t) rho_i. The posterior on pi_t is a direct probability that year
t carries a system-wide break, with a credible interval -- the Bayesian analog
of the permutation p-value.

The priors are deliberately sparse (both hazards small a priori: system and
idiosyncratic breaks are rare per year/series) and are exposed as constants with
a reported sensitivity note rather than tuned to the answer. Reading of the
posterior is *relative*: a genuine system year's pi_t concentrates well above
the ~0 that data-free/null years revert to, not above any absolute 0.5-style
bar (breadth 17-48 of ~150 eligible series puts even the clearest system year's
pi_t in the low tenths).

Usage:
    python panel_breaks_bayes.py --self-test   # synthetic panel, no real data
    python panel_breaks_bayes.py               # production run on kodeks-karny.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import panel_breaks as pb

# Sparse Beta priors (mean pi ~= 0.11, mean rho ~= 0.048): breaks are rare both
# per-year-system-wide and per-series-idiosyncratically. Reported with a
# sensitivity check, not tuned to recover a target year.
A_PI, B_PI = 1.0, 8.0
A_RHO, B_RHO = 1.0, 20.0
# "Non-negligible shared hazard" reference: a year is called Bayes-confirmed
# system-wide only when the posterior is confident (PROB_MASS) that pi_t exceeds
# this floor -- i.e. that at least ~5% of series break there for a *shared*
# reason, above the per-series idiosyncratic level. A lower floor (e.g. 0.02)
# cannot separate a genuine system year from a year that merely accumulates a
# few coincident idiosyncratic breaks, because the model's pi/rho split is only
# weakly identified at small hazards; 0.05 is the level at which the synthetic
# self-test's injected system years separate cleanly from idiosyncratic scatter.
PI_NULL = 0.05
PROB_MASS = 0.95           # P(pi_t > PI_NULL) needed to call a year confirmed
N_DRAWS, N_TUNE, N_CHAINS = 2000, 1000, 4
SEED = 0


# --------------------------------------------------------------------------- #
# Design matrix: flat (series, year) cells over each series' eligible interior. #
# Decoupled from PELT and this dataset, like panel_breaks' core.               #
# --------------------------------------------------------------------------- #

def build_cells(panel_years: dict[str, list[int]],
                breaks_per_series: dict[str, list[int]],
                *, min_size: int = pb.MIN_SIZE, tol: int = pb.BREADTH_TOL):
    """Return (grid, series_ids, s_idx, t_idx, z) where each of s_idx/t_idx/z is
    one entry per eligible (series, year) cell: s_idx into series_ids, t_idx into
    grid, z in {0,1} for whether that series has a break at that year (within
    `tol`, matching the breadth statistic). Only a series' PELT-eligible interior
    years contribute -- the same cells the permutation null is free to relocate
    breaks into -- so a break can never be "expected" where PELT could not place
    one."""
    grid = pb._year_grid(panel_years)
    year_pos = {int(y): j for j, y in enumerate(grid)}
    series_ids = list(panel_years.keys())
    s_idx, t_idx, z = [], [], []
    for si, sid in enumerate(series_ids):
        ys = np.asarray(sorted(panel_years[sid]), dtype=int)
        interior = ys[min_size: len(ys) - min_size] if len(ys) > 2 * min_size else ys[:0]
        if interior.size == 0:
            continue
        breaks = breaks_per_series.get(sid, [])
        covered = pb._covered_mask(breaks, interior, tol)   # break at that year?
        for yr, hit in zip(interior, covered):
            s_idx.append(si)
            t_idx.append(year_pos[int(yr)])
            z.append(int(hit))
    return (grid, series_ids,
            np.asarray(s_idx), np.asarray(t_idx), np.asarray(z, dtype=int))


def _hdi(samples: np.ndarray, prob: float = 0.94) -> tuple[float, float]:
    """Shortest interval containing `prob` of a 1-D posterior sample."""
    s = np.sort(samples)
    n = s.size
    k = max(1, int(np.floor(prob * n)))
    widths = s[k:] - s[:n - k]
    i = int(np.argmin(widths))
    return float(s[i]), float(s[i + k])


def _split_rhat(arr: np.ndarray) -> np.ndarray:
    """Split-R-hat per parameter for arr shaped (chains, draws, params)."""
    n_chains, n_draws, _ = arr.shape
    half = n_draws // 2
    x = np.concatenate([arr[:, :half, :], arr[:, half:2 * half, :]], axis=0)
    m, n = x.shape[0], x.shape[1]
    chain_means = x.mean(axis=1)
    W = x.var(axis=1, ddof=1).mean(axis=0)
    B = n * chain_means.var(axis=0, ddof=1)
    var_hat = (n - 1) / n * W + B / n
    return np.sqrt(np.where(W > 0, var_hat / W, 1.0))


def fit_bayes(panel_years, breaks_per_series, *,
              draws=N_DRAWS, tune=N_TUNE, chains=N_CHAINS, seed=SEED,
              progressbar=False) -> pd.DataFrame:
    """Sample the hierarchical model and summarize the posterior on each year's
    system-break hazard pi_t. Returns one row per grid year: posterior mean, 94%
    HDI, P(pi_t > PI_NULL), the Bayes-confirmed flag, and the number of eligible
    series that year (its data support). Max split-R-hat is attached as
    `df.attrs["rhat_max"]` -- a reported convergence guard, never an assertion
    (doc §3: do not block on the sampler)."""
    import pymc as pm

    grid, series_ids, s_idx, t_idx, z = build_cells(panel_years, breaks_per_series)
    n_years, n_series = grid.size, len(series_ids)

    with pm.Model():
        pi = pm.Beta("pi", alpha=A_PI, beta=B_PI, shape=n_years)
        rho = pm.Beta("rho", alpha=A_RHO, beta=B_RHO, shape=n_series)
        p = pi[t_idx] + (1.0 - pi[t_idx]) * rho[s_idx]
        pm.Bernoulli("z", p=p, observed=z)
        idata = pm.sample(draws=draws, tune=tune, chains=chains, cores=chains,
                          random_seed=seed, progressbar=progressbar,
                          target_accept=0.9)

    pi_arr = np.asarray(idata.posterior["pi"].values)   # (chains, draws, n_years)
    pi_flat = pi_arr.reshape(-1, n_years)               # (samples, n_years)
    hdis = np.array([_hdi(pi_flat[:, j]) for j in range(n_years)])
    prob_gt = (pi_flat > PI_NULL).mean(axis=0)
    support = np.bincount(t_idx, minlength=n_years)

    df = pd.DataFrame({
        "year": grid,
        "pi_mean": pi_flat.mean(axis=0),
        "pi_hdi_low": hdis[:, 0],
        "pi_hdi_high": hdis[:, 1],
        "prob_pi_gt_null": prob_gt,
        "bayes_system_wide": prob_gt >= PROB_MASS,
        "n_series_eligible": support,
    })
    df.attrs["rhat_max"] = float(_split_rhat(pi_arr).max()) if pi_arr.shape[0] > 1 else float("nan")
    return df


# --------------------------------------------------------------------------- #
# Production run (needs data-aggregated/, gitignored).                         #
# --------------------------------------------------------------------------- #

def _agreement(bayes_df: pd.DataFrame, perm_df: pd.DataFrame) -> pd.DataFrame:
    """Join the Bayesian posterior call to the permutation call per year; the
    agreement (or disagreement) between the two is the point of this layer."""
    m = perm_df[["year", "breadth", "qvalue", "system_wide"]].merge(
        bayes_df, on="year", how="outer").sort_values("year")
    m = m.rename(columns={"system_wide": "perm_system_wide"})
    m["agree"] = m["perm_system_wide"] == m["bayes_system_wide"]
    return m


def plot_posterior(bayes_df: pd.DataFrame, perm_system_years, out_png: Path) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4))
    x = bayes_df["year"].to_numpy()
    lo = bayes_df["pi_mean"] - bayes_df["pi_hdi_low"]
    hi = bayes_df["pi_hdi_high"] - bayes_df["pi_mean"]
    colors = ["seagreen" if y in set(perm_system_years) else "#4c78a8" for y in x]
    ax.errorbar(x, bayes_df["pi_mean"], yerr=[lo, hi], fmt="o", ecolor="lightgrey",
                capsize=2, markersize=4)
    for xi, yi, c in zip(x, bayes_df["pi_mean"], colors):
        ax.plot(xi, yi, "o", color=c, markersize=5)
    ax.axhline(PI_NULL, color="grey", linestyle="--", linewidth=0.8,
               label=f"negligible-hazard reference ({PI_NULL})")
    ax.set_xlabel("year")
    ax.set_ylabel(r"posterior $\pi_t$ (system-break hazard)")
    ax.set_title("Bayesian per-year system-break hazard (94% HDI); green = permutation-flagged")
    ax.legend()
    fig.savefig(out_png, bbox_inches="tight", dpi=150)
    plt.close(fig)


def main():
    from common import RESULTS, write_chart_data_md
    metric = "przestepstwa_stwierdzone"
    out_dir = RESULTS / "panel-breaks-bayes"
    out_dir.mkdir(parents=True, exist_ok=True)

    panel_years, panel_values = pb._load_panel_kodeks_karny(metric)
    breaks_with_mag = pb._detect_all(panel_years, panel_values)
    breaks_years = {sid: [y for y, _ in pairs] for sid, pairs in breaks_with_mag.items()}

    perm_df = pb.permutation_common_breaks(panel_years, breaks_years)
    perm_system_years = perm_df[perm_df["system_wide"]]["year"].tolist()

    bayes_df = fit_bayes(panel_years, breaks_years)
    agree = _agreement(bayes_df, perm_df)

    bayes_df.to_csv(out_dir / "pi-posterior-by-year.csv", index=False)
    agree.to_csv(out_dir / "bayes-vs-permutation.csv", index=False)
    write_chart_data_md(
        bayes_df, out_dir / "pi-posterior-by-year.md",
        "Posterior system-break hazard pi_t by year (hierarchical Bayesian layer)")
    plot_posterior(bayes_df, perm_system_years, out_dir / "pi-posterior.png")

    bayes_years = bayes_df[bayes_df["bayes_system_wide"]]["year"].tolist()
    disagree = agree[~agree["agree"]]["year"].tolist()
    print(f"max split-R-hat (pi): {bayes_df.attrs.get('rhat_max', float('nan')):.3f} "
          f"(convergence guard, not a gate)")
    print(f"permutation-flagged system-wide: {perm_system_years}")
    print(f"Bayes-confirmed system-wide (P(pi>{PI_NULL})>={PROB_MASS}): {bayes_years}")
    print(f"years where the two disagree: {disagree}")
    for _, r in bayes_df.iterrows():
        if r["year"] in set(perm_system_years) | set(bayes_years) or r["prob_pi_gt_null"] > 0.5:
            print(f"  {int(r['year'])}: pi_mean={r['pi_mean']:.3f} "
                  f"HDI[{r['pi_hdi_low']:.3f},{r['pi_hdi_high']:.3f}] "
                  f"P(pi>{PI_NULL})={r['prob_pi_gt_null']:.2f}")
    print(f"-> {out_dir}")


# --------------------------------------------------------------------------- #
# Self-test: synthetic panel, verifies the layer recovers injected system years.#
# --------------------------------------------------------------------------- #

def self_test():
    """Inject common breaks at 2008 and 2015 into a subset of otherwise-noisy
    series (mirroring panel_breaks.self_test) and confirm the posterior pi_t is
    clearly elevated at exactly those years and near the null elsewhere."""
    rng = np.random.default_rng(42)
    years = list(range(2000, 2025))
    n_series = 60
    panel_years = {f"s{i}": list(years) for i in range(n_series)}
    breaks = {f"s{i}": [] for i in range(n_series)}
    for i in rng.choice(n_series, size=18, replace=False):
        breaks[f"s{i}"].append(2008)
    for i in rng.choice(n_series, size=15, replace=False):
        breaks[f"s{i}"].append(2015)
    for i in range(n_series):
        breaks[f"s{i}"].append(int(rng.integers(2004, 2021)))

    bayes_df = fit_bayes(panel_years, breaks, draws=1000, tune=1000, chains=2, seed=1)
    print(bayes_df.to_string(index=False))
    flagged = set(bayes_df[bayes_df["bayes_system_wide"]]["year"])
    print(f"\nBayes-confirmed system-wide: {sorted(flagged)}")

    assert {2008, 2015} <= flagged, f"failed to recover injected system years: {sorted(flagged)}"
    spurious = flagged - {2008, 2015}
    assert not spurious, f"flagged idiosyncratic-scatter years as system-wide: {sorted(spurious)}"
    injected_pi = bayes_df.set_index("year").loc[[2008, 2015], "pi_mean"].min()
    other_pi = bayes_df[~bayes_df["year"].isin([2008, 2015])]["pi_mean"].max()
    assert injected_pi > other_pi, (
        f"injected years' pi_t ({injected_pi:.3f}) not clearly above the rest "
        f"({other_pi:.3f})")
    print(f"injected pi_t (min {injected_pi:.3f}) > all other years (max {other_pi:.3f})")
    print(f"max split-R-hat (pi): {bayes_df.attrs.get('rhat_max', float('nan')):.3f}")
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

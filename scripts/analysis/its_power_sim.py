"""Simulation-based statistical power check for `common.its_level_shift` under
the actual pre/post sample sizes of the 2004 Penal Code anomaly cluster
(n_before=5, 1999-2003; n_after=20, 2004-2023, per kodeks-karny.csv).

For each of the 2004-cluster articles, fits a pre-break linear trend to the
real 1999-2003 data, takes the residual SD off that fit as the noise scale,
then simulates y = b0 + b1*t + (level_shift) over n_after additional points
with that noise, refits via `its_level_shift`, and records whether the
simulated shift is detected at alpha=0.05. Sweeps level_shift as a percentage
of the article's own pre-break mean to find the 80%-power MDE, and reports
achieved power at the actually observed 1999-2003-mean -> 2004 jump size.

Not part of the regular pipeline -- run on demand.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import POLICJA, its_level_shift

METRIC = "przestepstwa_stwierdzone"
ARTICLES = ["220", "226", "244", "258", "303", "230+230a"]
BREAK_YEAR = 2004
N_BEFORE = 5
N_AFTER = 20
N_REPS = 2000
ALPHA = 0.05
RNG = np.random.default_rng(20260630)


def load_series(article):
    df = pd.read_csv(POLICJA / "kodeks-karny.csv")
    sub = df[df["article"].astype(str) == article].set_index("year")[METRIC].dropna().sort_index()
    return sub.index.values.astype(float), sub.values.astype(float)


def pre_break_fit(years, values, break_year):
    mask = years < break_year
    t = years[mask] - break_year
    design = sm.add_constant(t)
    model = sm.OLS(values[mask], design).fit()
    resid_sd = float(np.std(model.resid, ddof=2))
    b0, b1 = model.params
    pre_mean = float(values[mask].mean())
    return b0, b1, resid_sd, pre_mean


def simulate_power(b0, b1, resid_sd, level_shift_abs, n_before=N_BEFORE, n_after=N_AFTER,
                    break_year=BREAK_YEAR, n_reps=N_REPS):
    years_before = np.arange(break_year - n_before, break_year)
    years_after = np.arange(break_year, break_year + n_after)
    years = np.concatenate([years_before, years_after])
    t = years - break_year
    d = (years >= break_year).astype(float)
    mean = b0 + b1 * t + d * level_shift_abs
    detections = 0
    for _ in range(n_reps):
        y = mean + RNG.normal(0, resid_sd, size=len(years))
        res = its_level_shift(years.tolist(), y.tolist(), break_year)
        if res is not None and res["pvalue"] < ALPHA:
            detections += 1
    return detections / n_reps


def find_mde(b0, b1, resid_sd, pre_mean, break_year=BREAK_YEAR, target_power=0.80):
    """Bisection search over level-shift-as-%-of-pre-break-mean for 80% power."""
    lo, hi = 0.0, 5.0  # 0% to 500% of pre-break mean
    for _ in range(18):
        mid = (lo + hi) / 2
        shift_abs = mid * pre_mean
        power = simulate_power(b0, b1, resid_sd, shift_abs, n_reps=400)
        if power < target_power:
            lo = mid
        else:
            hi = mid
    final_power = simulate_power(b0, b1, resid_sd, hi * pre_mean, n_reps=N_REPS)
    return hi, final_power


def main():
    rows = []
    for article in ARTICLES:
        years, values = load_series(article)
        b0, b1, resid_sd, pre_mean = pre_break_fit(years, values, BREAK_YEAR)

        observed_2004 = values[years == BREAK_YEAR][0]
        observed_shift_abs = observed_2004 - (b0 + b1 * 0)  # predicted at t=0 pre-trend vs actual
        observed_pct = observed_shift_abs / pre_mean if pre_mean else np.nan
        power_at_observed = simulate_power(b0, b1, resid_sd, observed_shift_abs, n_reps=N_REPS)

        mde_pct, mde_power = find_mde(b0, b1, resid_sd, pre_mean)

        rows.append({
            "article": article, "pre_break_mean": pre_mean, "resid_sd_pre": resid_sd,
            "observed_2004_shift_abs": observed_shift_abs, "observed_2004_shift_pct": observed_pct * 100,
            "power_at_observed_shift": power_at_observed,
            "mde_pct_of_pre_mean": mde_pct * 100, "power_at_mde": mde_power,
        })
        print(f"{article}: pre_mean={pre_mean:.1f} resid_sd={resid_sd:.1f} "
              f"observed_shift={observed_shift_abs:.1f} ({observed_pct*100:.0f}%) "
              f"power={power_at_observed:.3f} | MDE={mde_pct*100:.0f}% power={mde_power:.3f}")

    out = pd.DataFrame(rows)
    out_path = Path(__file__).resolve().parent.parent.parent / "results" / "kodeks-karny" / "robustness-its-power-sim.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")
    return out


if __name__ == "__main__":
    main()

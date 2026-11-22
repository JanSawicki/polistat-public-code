"""Reproducible PELT / SeedBS benchmark for short annual administrative series.

The simulation uses known Gaussian mean shifts so that both series-level and
panel-level false-positive and false-negative rates have explicit denominators.
The real Penal Code panel is also scored against the curated system-year labels,
but these sparse labels are not treated as exhaustive series-level ground truth.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import ruptures as rpt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmark" / "changepoint-comparison-results"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel_breaks as panel  # noqa: E402

SEED = 20261121
N = 25                         # Penal Code panel: 1999--2023
MIN_SIZE = 3
MATCH_TOL = 1
N_REP = 300
N_PANEL_REP = 50
N_SERIES = 40
N_PERM = 999


def seeded_intervals(n: int, decay: float = np.sqrt(2)) -> list[tuple[int, int]]:
    """Deterministic seeded intervals (0-based, half-open), Kovacs et al. (2023)."""
    intervals = [(0, n)]
    depth = int(np.ceil(np.log(n) / np.log(decay)))
    for layer in range(1, depth):
        length = n * decay ** (-layer)
        n_intervals = int(np.ceil(round(n / length, 14))) * 2 - 1
        starts = np.floor(np.linspace(0, n - length, n_intervals)).astype(int)
        ends = np.ceil(np.linspace(length, n, n_intervals)).astype(int)
        intervals.extend((int(s), int(e)) for s, e in zip(starts, ends) if e - s >= 2)
    return list(dict.fromkeys(intervals))


def _best_mean_split(x: np.ndarray, left: int, right: int) -> tuple[int, float]:
    """Return the split and Gaussian mean-change SSE gain on [left,right)."""
    z = x[left:right]
    m = len(z)
    cs = np.r_[0.0, np.cumsum(z)]
    k = np.arange(MIN_SIZE, m - MIN_SIZE + 1)
    if not len(k):
        return -1, 0.0
    gain = (cs[k] - k * cs[m] / m) ** 2 / (k * (m - k) / m)
    j = int(np.argmax(gain))
    return left + int(k[j]), float(gain[j])


def seedbs(x: np.ndarray, threshold: float) -> list[int]:
    """Greedy SeedBS with deterministic seeded intervals and mean-SSE gain.

    At each recursive step, candidates are the best mean split in each seeded
    interval wholly contained in the current segment. The largest gain is
    accepted if it exceeds the calibrated threshold, then both sides recurse.
    """
    x = np.asarray(x, dtype=float).copy()
    # Express split gains in estimated noise-variance units so the same
    # threshold applies to crime counts with different scales. The MAD of
    # first differences is robust to a small number of level shifts.
    differences = np.diff(x)
    sigma = np.median(np.abs(differences - np.median(differences))) / (0.67448975 * np.sqrt(2))
    if not np.isfinite(sigma) or sigma <= np.finfo(float).eps:
        sigma = float(np.std(x))
    if sigma <= np.finfo(float).eps:
        return []
    x /= sigma
    intervals = seeded_intervals(len(x))
    out: list[int] = []

    def recurse(left: int, right: int) -> None:
        best_cp, best_gain = -1, -np.inf
        for a, b in intervals:
            if a < left or b > right or b - a < 2 * MIN_SIZE:
                continue
            cp, gain = _best_mean_split(x, a, b)
            if gain > best_gain:
                best_cp, best_gain = cp, gain
        if best_cp >= 0 and best_gain > threshold:
            out.append(best_cp)
            recurse(left, best_cp)
            recurse(best_cp, right)

    recurse(0, len(x))
    return sorted(out)


def pelt(x: np.ndarray, penalty: float) -> list[int]:
    algo = rpt.Pelt(model="rbf", min_size=MIN_SIZE).fit(np.asarray(x)[:, None])
    return [int(b) for b in algo.predict(pen=penalty) if b < len(x)]


def calibrate_pelt(target_fpr: float = .05) -> tuple[float, float]:
    rng = np.random.default_rng(SEED + 123)
    null = [rng.normal(size=N) for _ in range(500)]
    grid = np.linspace(.5, 15, 60)
    rates = [sum(bool(pelt(x, pen)) for x in null) / len(null) for pen in grid]
    eligible = [i for i, rate in enumerate(rates) if rate <= target_fpr]
    idx = eligible[0] if eligible else len(grid)-1
    return float(grid[idx]), float(rates[idx])


def seeded_threshold(target_fpr: float = .05) -> tuple[float, float]:
    rng = np.random.default_rng(SEED + 124)
    null = [rng.normal(size=N) for _ in range(1200)]
    grid = np.linspace(2.0, 30.0, 141)
    rates = [sum(bool(seedbs(x, t)) for x in null) / len(null) for t in grid]
    eligible = [i for i, rate in enumerate(rates) if rate <= target_fpr]
    idx = eligible[0] if eligible else len(grid)-1
    return float(grid[idx]), float(rates[idx])


def match_counts(detected: list[int], truth: list[int], tol: int = MATCH_TOL) -> tuple[int, int, int]:
    unmatched = set(range(len(truth)))
    tp = fp = 0
    for d in sorted(detected):
        matches = [i for i in unmatched if abs(d - truth[i]) <= tol]
        if matches:
            best = min(matches, key=lambda i: abs(d - truth[i]))
            unmatched.remove(best)
            tp += 1
        else:
            fp += 1
    return tp, fp, len(unmatched)


def one_d_benchmark(penalty: float, threshold: float) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    rows = []
    for breaks in (0, 1, 2):
        for magnitude in ((0.0,) if breaks == 0 else (1.0, 1.5, 2.0, 3.0)):
            agg = {m: [0, 0, 0] for m in ("PELT", "SeedBS")}
            negative_slots_total = 0
            for _ in range(N_REP):
                truth = []
                x = rng.normal(size=N)
                if breaks == 1:
                    truth = [int(rng.integers(8, 20))]
                elif breaks == 2:
                    first = int(rng.integers(7, 15))
                    truth = [first, int(rng.integers(first + 5, 22))]
                eligible_years = range(MIN_SIZE, N-MIN_SIZE+1)
                negative_slots_total += sum(
                    not any(abs(y-cp) <= MATCH_TOL for cp in truth)
                    for y in eligible_years
                )
                for cp in truth:
                    x[cp:] += magnitude * rng.choice((-1.0, 1.0))
                for name, det in (("PELT", pelt(x, penalty)), ("SeedBS", seedbs(x, threshold))):
                    tp, fp, fn = match_counts(det, truth)
                    for i, value in enumerate((tp, fp, fn)):
                        agg[name][i] += value
            for method, (tp, fp, fn) in agg.items():
                # FPR denominator is eligible candidate years outside each
                # replicate's +/-1 positive windows; FNR is event-level.
                tn = max(0, negative_slots_total - fp)
                rows.append({"breaks": breaks, "shift_sd": magnitude, "method": method,
                             "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                             "fpr": fp / (fp + tn) if fp + tn else 0.0,
                             "fnr": fn / (fn + tp) if fn + tp else 0.0,
                             "replicates": N_REP})
    return pd.DataFrame(rows)


def panel_benchmark(penalty: float, threshold: float) -> pd.DataFrame:
    """Known-truth panel simulations for detector + panel decomposition."""
    rng = np.random.default_rng(SEED + 5)
    rows = []
    years = list(range(1999, 1999 + N))
    truth_years = {2008, 2017}
    eligible_years = set(years[MIN_SIZE:N-MIN_SIZE+1])
    for magnitude in (1.0, 2.0, 3.0):
        for shared_fraction in (.25, .50):
            for method in ("PELT", "SeedBS"):
                tp = fp = fn = 0
                negative_slots_total = 0
                for rep in range(N_PANEL_REP):
                    panel_years = {f"s{i}": years for i in range(N_SERIES)}
                    bp: dict[str, list[int]] = {}
                    shared_ids = set(rng.choice(N_SERIES, size=int(N_SERIES*shared_fraction), replace=False))
                    for i in range(N_SERIES):
                        x = rng.normal(size=N)
                        truth: list[int] = []
                        if i in shared_ids:
                            truth = [years.index(y) for y in sorted(truth_years)]
                        # one idiosyncratic break on a random, non-shared year
                        choices = [j for j in range(6, N-3) if years[j] not in truth_years]
                        truth.append(int(rng.choice(choices)))
                        for cp in truth:
                            x[cp:] += magnitude * rng.choice((-1.0, 1.0))
                        det = pelt(x, penalty) if method == "PELT" else seedbs(x, threshold)
                        bp[f"s{i}"] = [years[j] for j in det]
                    result = panel.permutation_common_breaks(panel_years, bp, n_perm=N_PERM,
                                                             seed=SEED + rep, alpha=.05)
                    called = set(result.loc[result.system_wide, "year"].astype(int))
                    tpi, fpi, fni = match_counts(sorted(called), sorted(truth_years))
                    tp += tpi
                    fp += fpi
                    fn += fni
                    negative_slots_total += sum(
                        not any(abs(y-t) <= MATCH_TOL for t in truth_years)
                        for y in eligible_years
                    )
                tn = max(0, negative_slots_total - fp)
                rows.append({"shift_sd": magnitude, "shared_fraction": shared_fraction,
                             "method": method, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                             "fpr": fp/(fp+tn) if fp+tn else 0.0,
                             "fnr": fn/(fn+tp) if fn+tp else 0.0,
                             "replicates": N_PANEL_REP, "series": N_SERIES,
                             "permutations": N_PERM})
    return pd.DataFrame(rows)


def real_panel(penalty: float, threshold: float) -> pd.DataFrame:
    data = pd.read_csv(ROOT / "data-aggregated/statystyka-policja/kodeks-karny.csv")
    labels = pd.read_csv(ROOT / "benchmark/system-break-labels.csv")
    panel_years, panel_values = {}, {}
    for article, sub in data.groupby("article"):
        s = sub.set_index("year")["przestepstwa_stwierdzone"].dropna().sort_index()
        if len(s) >= 6:
            sid = f"art.{article}"
            panel_years[sid] = s.index.astype(int).tolist()
            panel_values[sid] = s.to_numpy(float)
    out = []
    for method in ("PELT", "SeedBS"):
        bp = {}
        for sid, x in panel_values.items():
            ds = pelt(x, penalty) if method == "PELT" else seedbs(x, threshold)
            bp[sid] = [panel_years[sid][j] for j in ds]
        result = panel.permutation_common_breaks(panel_years, bp, n_perm=5000, seed=0)
        flagged = sorted(result.loc[result.system_wide, "year"].astype(int).tolist())
        ev = panel.evaluate_against_labels(flagged, labels, "kodeks-karny")
        out.append({"method": method, "series": len(panel_values),
                    "single_series_breaks": sum(map(len, bp.values())),
                    "system_years": ";".join(map(str, flagged)),
                    "documented_positive_years": ";".join(map(str, ev["positives"])),
                    "unresolved_held_out": ";".join(map(str, ev["held_out_predictions"])),
                    "precision": ev["precision"], "recall": ev["recall"], "f1": ev["f1"],
                    "false_positive_years": ";".join(map(str, ev["false_positive_years"])),
                    "false_negative_years": ";".join(map(str, ev["false_negative_years"]))})
    return pd.DataFrame(out)


def main(synthetic_only: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pen, pelt_null_rate = calibrate_pelt()
    thr, seedbs_null_rate = seeded_threshold()
    pd.DataFrame([{
        "seed": SEED, "series_length": N, "series_replicates": N_REP,
        "panel_replicates": N_PANEL_REP, "panel_series": N_SERIES,
        "panel_permutations": N_PERM, "match_tolerance_years": MATCH_TOL,
        "null_target_series_fwer": .05, "pelt_rbf_penalty": pen,
        "pelt_null_call_rate": pelt_null_rate,
        "seedbs_gain_threshold_noise_variance_units": thr,
        "seedbs_null_call_rate": seedbs_null_rate,
        "pelt_null_calibration_replicates": 500,
        "seedbs_null_calibration_replicates": 1200,
    }]).to_csv(OUT / "experiment-config.csv", index=False)
    print(f"Calibrated PELT RBF penalty: {pen:.3f} (null call rate={pelt_null_rate:.3f}); "
          f"SeedBS gain threshold: {thr:.3f} (null call rate={seedbs_null_rate:.3f})")
    one = one_d_benchmark(pen, thr)
    one.to_csv(OUT / "single-series-simulation.csv", index=False)
    pan = panel_benchmark(pen, thr)
    pan.to_csv(OUT / "panel-simulation.csv", index=False)
    real = None
    if not synthetic_only:
        real = real_panel(pen, thr)
        real.to_csv(OUT / "curated-panel-evaluation.csv", index=False)
    print("\nSingle-series simulation (pooled by break count, effect):")
    print(one.groupby(["breaks", "shift_sd", "method"])[["fpr", "fnr"]].mean().to_string())
    print("\nPanel simulation:")
    print(pan.groupby(["shift_sd", "shared_fraction", "method"])[["fpr", "fnr"]].mean().to_string())
    if real is not None:
        print("\nCurated panel labels:")
        print(real.to_string(index=False))
    print(f"\nResults: {OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true",
                        help="run simulations without reading private aggregated data")
    main(synthetic_only=parser.parse_args().synthetic_only)

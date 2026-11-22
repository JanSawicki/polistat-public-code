"""Independently-varied threshold-sensitivity grid for the major-anomaly
definition (pct_threshold, base_threshold) used in paper.tex's anomaly table.

`robustness_checks.threshold_sensitivity` varies (pct, base) jointly over a
small grid. This holds one fixed at the paper's actual value while sweeping
the other, against the same PELT-detected (pen=3.0) candidate change-points
`robustness_checks.pelt_candidates` generates -- isolates each threshold's
marginal effect on the major-anomaly count instead of conflating both.

Not part of the regular pipeline -- run on demand.
"""
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from robustness_checks import pelt_candidates, OUT_DIR

PAPER_PCT = 0.30
PAPER_BASE = 100


def count_major(candidates, pct, base):
    sub = candidates[
        (candidates["before"] > 0)
        & (candidates[["before", "after"]].min(axis=1) >= base)
        & ((candidates["after"] - candidates["before"]).abs() / candidates["before"] >= pct)
    ]
    return len(sub)


def main():
    candidates = pelt_candidates()
    print(f"Total PELT-detected candidate change-points (all articles): {len(candidates)}\n")

    pct_rows = []
    for pct in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
        n = count_major(candidates, pct, PAPER_BASE)
        pct_rows.append({"pct_threshold": pct, "base_threshold": PAPER_BASE, "n_major_anomalies": n})
    pct_df = pd.DataFrame(pct_rows)
    pct_df.to_csv(OUT_DIR / "robustness-threshold-pct-only.csv", index=False)
    print(f"Varying pct_threshold, base_threshold={PAPER_BASE} fixed:")
    print(pct_df.to_string(index=False))

    base_rows = []
    for base in (50, 75, 100, 150, 200):
        n = count_major(candidates, PAPER_PCT, base)
        base_rows.append({"pct_threshold": PAPER_PCT, "base_threshold": base, "n_major_anomalies": n})
    base_df = pd.DataFrame(base_rows)
    base_df.to_csv(OUT_DIR / "robustness-threshold-base-only.csv", index=False)
    print(f"\nVarying base_threshold, pct_threshold={PAPER_PCT} fixed:")
    print(base_df.to_string(index=False))

    return pct_df, base_df


if __name__ == "__main__":
    main()

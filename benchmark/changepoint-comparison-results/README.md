# PELT–SeedBS comparison benchmark

This directory contains the frozen simulation configuration and result tables for the
comparison implemented in `scripts/analysis/changepoint_comparison.py`.

The comparison uses 25 annual observations; single-series simulations include 0, 1,
or 2 mean shifts, 300 replicates per setting, and shifts of 1, 1.5, 2, or 3 noise SD.
PELT's RBF penalty and SeedBS's gain threshold are calibrated separately on no-break
Gaussian series to a 5% series-level false-alarm target, then held fixed. Breaks are
matched one-to-one within ±1 year. Panel simulations contain 40 series, two injected
shared years plus one idiosyncratic break per series, 50 replicates per setting, and
999 permutations with BH FDR α=0.05. Panel FPR and FNR are reported separately.

Run the fully labeled synthetic benchmark without private data:

```sh
.venv/bin/python scripts/analysis/changepoint_comparison.py --synthetic-only
```

For panel simulation, breadth is counted at the exact calendar year; the ±1-year
tolerance is used separately when matching detected events to benchmark episodes.
Adjacent significant years with overlapping matching windows (gap no greater than
two years) are summarized as one event, anchored at the lower median year.

The curated Penal Code application is a separate limited check. Its year labels are
incomplete, so unlabeled years are not treated as true negatives and exhaustive
real-panel FPR/FNR are not reported. Reproducing that real-data check also requires
the compatible aggregated Penal Code input.

# `scripts/analysis/`

Analysis scripts read harmonized inputs under `data-aggregated/` and write derived outputs under
`results/` (the full police data and generic results are distributed in the companion
[`polistat-dataset`](https://github.com/JanSawicki/polistat-dataset) repository). The two scripts
listed below as self-contained experiments run without that dataset.

## Self-contained experiments

| Script | Inputs | Reproduce |
|---|---|---|
| `changepoint_comparison.py` | Seeded Gaussian simulations and frozen configuration in `benchmark/changepoint-comparison-results/` | `.venv/bin/python scripts/analysis/changepoint_comparison.py --synthetic-only` |
| `domain_panel_comparison.py` | Cached BDL education, budget, population, and HICP inputs under `benchmark/domain-panel-comparison/input/` | `.venv/bin/python scripts/analysis/domain_panel_comparison.py` |

Read the corresponding benchmark README before rerunning: the synthetic command recalibrates
thresholds and rewrites simulation output, while the domain command rewrites its result CSVs.
Both scripts record seeds and detector settings in their configuration or output tables.

## Police-data analysis

| Script | Purpose |
|---|---|
| `common.py` | Shared registry, normalization, change-point, trend, and output helpers |
| `przestepstwa_ogolem.py`, `kodeks_karny.py`, `ruch_drogowy.py`, `wybrane_statystyki.py` | Per-family analysis; require the corresponding harmonized data |
| `panel_breaks.py` | Permutation test and FDR control for years with synchronized candidate breaks |
| `panel_breaks_bayes.py` | Bayesian check of cross-series synchrony; requires `pymc` |
| `synthesis.py` | Summarizes the generated per-family analysis results |

Typical environment for the police analysis and self-contained runs:

```sh
python -m venv .venv
.venv/bin/pip install numpy pandas scipy statsmodels ruptures matplotlib tabulate
.venv/bin/pip install pymc  # only for panel_breaks_bayes.py
```

The panel test describes timing synchrony. The output labels `shared` and `idiosyncratic` in
legacy decomposition tables are statistical tags, not causal findings. A measurement-artifact
attribution requires an independent source match for date and scope. Since real benchmark labels
are incomplete, do not count unlabeled years as verified negatives or report exhaustive real-panel
FPR/FNR.

# scripts/analysis/

Anomaly/trend analysis, per `doc/analysis/methodology.md` and `doc/analysis/implementation.md`. Reads `data-aggregated/`, writes to `results/` (gitignored).

| Script | Reads | Writes |
|---|---|---|
| `common.py` | — | shared helpers: structural-break registry, voivodeship-name crosswalk, BDL population loaders, change-point/STL/Poisson wrappers, `Finding` record, `write_findings`/`write_chart_data_md` |
| `przestepstwa_ogolem.py` | `przestepstwa-ogolem.csv`, `category-tree.csv` | `results/przestepstwa-ogolem/` |
| `kodeks_karny.py` | `kodeks-karny.csv`, `kodeks-karny-notes.csv` | `results/kodeks-karny/` |
| `ruch_drogowy.py` | `ruch-drogowy/lata.csv`, `wypadki-1975-2011-legacy.csv`, `miesiace.csv` | `results/ruch-drogowy/` |
| `wybrane_statystyki.py` | `wybrane-statystyki/*.csv` (10 files) | `results/wybrane-statystyki/` |
| `panel_breaks.py` | `kodeks-karny.csv`, `../../benchmark/system-break-labels.csv` | `results/panel-breaks/` (breadth+FDR q-values by year, shared-vs-idiosyncratic break decomposition, benchmark evaluation, ROC-style threshold sweep, article×year heatmap + breadth chart) |
| `panel_breaks_bayes.py` | `kodeks-karny.csv` (via `panel_breaks.py`) | `results/panel-breaks-bayes/` (per-year posterior system-break hazard π_t with 94% HDI, Bayes-vs-permutation agreement table, π_t posterior plot) |
| `synthesis.py` | every `results/<category>/findings.csv` above | `results/headline-anomalies.csv`, `results/drunk-driving-reconciliation.md` |

Run order: the four category scripts in any order, then `panel_breaks.py` (independent, after `kodeks_karny.py`), then `synthesis.py` last.

`panel_breaks.py` is the panel common-break detector of `doc/analysis/panel-break-detection.md` — the "turn the registry into a method" work (permutation test, primary). Its statistical core is decoupled from the data; run `python panel_breaks.py --self-test` to verify it on a synthetic panel without needing `data-aggregated/`.

`panel_breaks_bayes.py` is the hierarchical-Bayesian confirmatory layer (doc §3): a noisy-OR of a per-year shared hazard π_t and a per-series idiosyncratic hazard ρ_i, fit with PyMC on the same PELT breaks, whose posterior on π_t is the Bayesian analog of the permutation p-value. It agrees with the permutation test on the strong system years and on excluding 2019 — see `doc/analysis/panel-break-results.md`. It also has a `--self-test`, and needs `pymc` on top of the deps below. On the HPC, `sbatch panel_breaks_bayes.slurm`; the same command runs locally in the `.venv`. Per doc §3 its convergence (max split-R-hat) is reported, never a gate.

Every chart PNG has a sibling `.md` with the same base name containing the underlying data as a Markdown table (via `common.write_chart_data_md`).

Needs `pandas`, `scipy`, `statsmodels`, `ruptures`, `matplotlib`, `tabulate` — not in the base environment (plus `pymc` for `panel_breaks_bayes.py` only). A venv works since the system has no `venv` module (no sudo to install `python3-venv`):

```
python3 -m virtualenv .venv
.venv/bin/pip install pandas scipy statsmodels ruptures matplotlib tabulate
.venv/bin/pip install pymc            # only for panel_breaks_bayes.py
.venv/bin/python scripts/analysis/przestepstwa_ogolem.py
```

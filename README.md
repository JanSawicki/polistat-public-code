# polistat-public-code

Public ingestion, harmonization, and analysis code for long-running Polish administrative
statistics. The companion [polistat-dataset repository](https://github.com/JanSawicki/polistat-dataset)
contains the source extracts and harmonized police-statistics tables. This repository contains
the code, sourced break benchmark, synthetic PELT–SeedBS comparison, and an exploratory
application to public education and local-budget data from GUS's Bank Danych Lokalnych (BDL).

## Start here

| If you want to… | Start with… |
|---|---|
| Reproduce the labelled simulation comparison without private data | [`benchmark/changepoint-comparison-results/README.md`](benchmark/changepoint-comparison-results/README.md) |
| Reproduce the exploratory BDL finance and education panels | [`benchmark/domain-panel-comparison/README.md`](benchmark/domain-panel-comparison/README.md) |
| Run the police-statistics analysis | [`scripts/analysis/README.md`](scripts/analysis/README.md) and the companion dataset repo |
| Understand source acquisition or the KGP internal-gazette search | [`scripts/ingest/README.md`](scripts/ingest/README.md), [`scripts/aggregate/README.md`](scripts/aggregate/README.md) |
| Read an interactive, plain-language guide to the IJDATS paper | [Paper digest](https://jansawicki.github.io/polistat-public-code/v1/) |
| Orient a coding agent | [`AGENTS.md`](AGENTS.md) |

## Reproduce the self-contained experiments

Use Python 3.10 or newer in a virtual environment. For the two self-contained analyses,
install `numpy`, `pandas`, and `ruptures` (the panel code uses no private police data):

```sh
python -m venv .venv
.venv/bin/pip install numpy pandas ruptures
.venv/bin/python scripts/analysis/domain_panel_comparison.py
.venv/bin/python scripts/analysis/changepoint_comparison.py --synthetic-only
```

The BDL run reads its cached public inputs from `benchmark/domain-panel-comparison/input/` and
rewrites that directory's result tables. The synthetic comparison calibrates detector thresholds
and runs its simulations; it takes longer than the BDL run and rewrites the result CSVs in
`benchmark/changepoint-comparison-results/`. Seeds, thresholds, panel settings, and current
summary results are recorded in those directories.

The full police analysis additionally needs the `data/` and `data-aggregated/` files from the
companion dataset repository at the paths expected by the scripts. From the code repository root,
one way to make those paths available is:

```sh
git clone https://github.com/JanSawicki/polistat-dataset.git ../polistat-dataset
ln -s ../polistat-dataset/data data
ln -s ../polistat-dataset/data-aggregated data-aggregated
```

These links are only needed for police-data scripts; they are not required to run the synthetic
benchmark or BDL experiment.

## Repository map

- `scripts/ingest/` downloads public source material and extracts raw tables.
- `scripts/aggregate/` reconciles raw files into analysis-ready tables and searches the KGP
  internal gazette for candidate counting-rule records.
- `scripts/analysis/` contains single-series analysis, panel synchrony testing, Bayesian
  confirmation, and simulation/domain comparisons.
- `benchmark/` contains the curated Polish system-year labels and reproducible benchmark inputs
  and outputs.

## Interpretation and data limits

The panel test identifies statistical synchrony among candidate breaks. Synchrony prioritizes a
year for provenance review; it does not establish that a measurement rule changed or identify a
cause. Attribute a break only when independent dated evidence matches its timing and scope.
Unlabeled real-panel years are not verified negatives, so exhaustive real-panel FPR/FNR are not
reported.

The BDL panels are exploratory and incomplete as ground truth. The education PELT signal at 2009
coincides with the documented school-data source transition, but an adjacent 2008 call and the
SeedBS non-replication make it method-sensitive. The selected budget panel does not flag its
2010 classification benchmark. See the domain benchmark README before interpreting these results.

Source documents retain their original terms. The companion dataset repository's CC0 dedication
covers its research artifacts but does not override conditions attached to source material by
originating public institutions. This code mirror currently has no root `LICENSE` file, so do not
assume the dataset license applies to the code.

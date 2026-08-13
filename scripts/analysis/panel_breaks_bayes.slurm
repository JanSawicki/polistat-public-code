#!/bin/bash
#SBATCH --account ganzha_23
#SBATCH --nodes 1
#SBATCH --cpus-per-task 4
#SBATCH --partition short,long,experimental
#SBATCH --mem=8G
#SBATCH --gpus=0
#SBATCH --time 00:30:00
#SBATCH --job-name panel-breaks-bayes
#SBATCH --output ./panel-breaks-bayes.slurm.log

# Hierarchical Bayesian common-changepoint confirmatory layer
# (doc/analysis/panel-break-detection.md §3). The model is small (163 series x
# ~26 years, all-continuous after the observed break indicators), so 4 CPU
# chains finish in well under a minute -- the SBATCH resources are deliberately
# modest. Provided per the plan's "implemented on the HPC (slurm.job)" note;
# the same command runs locally in the project .venv.
#
# Requires pymc in the environment (pip install pymc into the analysis venv).
# Writes results/panel-breaks-bayes/ (gitignored), regenerable.

set -euo pipefail
export PYTHONUNBUFFERED=1

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"

cd "$REPO/scripts/analysis"
"$PYTHON" panel_breaks_bayes.py "$@"

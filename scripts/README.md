# `scripts/`

| Directory | Purpose | Read first |
|---|---|---|
| `ingest/` | Download public records and extract raw source tables | [`ingest/README.md`](ingest/README.md) |
| `aggregate/` | Reconcile source tables, including legislative and internal-gazette provenance | [`aggregate/README.md`](aggregate/README.md) |
| `analysis/` | Run trend, change-point, panel, simulation, and BDL domain analyses | [`analysis/README.md`](analysis/README.md) |

The full police-statistics workflows read `data/` and `data-aggregated/` from the companion
[`polistat-dataset`](https://github.com/JanSawicki/polistat-dataset) repository. The synthetic
PELT–SeedBS benchmark and the BDL education/budget experiment run without those police data; see
the root README for commands.

The panel break statistic describes synchrony in detected candidate dates. Source attribution is
a separate evidence step and requires dated documentary provenance. See `AGENTS.md` for
instructions to coding agents.

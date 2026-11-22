# Exploratory BDL domain-panel comparison

This exploratory rerun applies the calibrated PELT and SeedBS detectors from
`benchmark/changepoint-comparison-results/experiment-config.csv` to a Polish
local-government budget panel and an education panel. It is a domain
application, not a new calibration or a complete replication of the synthetic
benchmark.

## Settings

- PELT RBF penalty: 1.9745762711864407; SeedBS gain threshold: 17.0.
- Panel test: exact-year breadth, 999 permutations, Benjamini–Hochberg FDR alpha 0.05. Event-date matching tolerance is ±1 year.
- Adjacent significant years whose matching windows overlap (gap <= 2 years) are summarized as one event, anchored at the lower median year.
- Event-date matching tolerance: ±1 year.
- Real-panel FPR/FNR are not calculated because neither panel has exhaustive
  ground-truth labels. A match to a documented event is reported as a hit
  check, not as a complete accuracy score.

## Panels and transformations

- **Budget:** 5 BDL expenditure variables × 16 voivodeships = 80 series,
  2001–2025. Nominal PLN is deflated by the cached annual HICP (2015=100),
  divided by population, then transformed with `asinh`. BDL data, the Eurostat
  annual all-items HICP index (2015=100), and the selected voivodeship population
  denominators are cached in `input/`. The population extract uses BDL's
  residence-based total, actual-residence population at 31 December. The 2010 benchmark is the dated budget-classification
  regulation, but the selected chapters need a legal crosswalk before a
  detected date could be attributed to that regulation.
- **Education:** 4 BDL variables × 16 voivodeships = 64 series: primary-school
  counts and pupils (1998–2025), and lower-secondary-school counts and pupils
  (1999–2018). Counts are expressed per 100,000 residents. The benchmark is
  2009, mapping the documented GUS reporting-source transition in school year
  2008/09 to the BDL year index.

## Outputs

- `panel-summary.csv`: annual significant-year windows, event anchors, and benchmark hit check.
- `individual-series-results.csv`: detected years by series and method.
- `*-panel-year-tests.csv`: annual breadth, permutation p-value, BH q-value,
  and system-wide flag for each domain/method.
- `input/`: cached BDL API responses and HICP values used by the script.

Reproduce with `.venv/bin/python scripts/analysis/domain_panel_comparison.py`.
The script reads the frozen thresholds and writes the CSV outputs here.

## Reading the exploratory results

Education is the more promising candidate: exact-year PELT calls are 2003, 2004, 2008, 2009, and 2018, grouped to event anchors 2003, 2008, and 2018. The 2008--2009 calls include the documented 2009 source transition, and the event anchor is within the ±1-year matching tolerance. SeedBS does not flag an event within one year of 2009,
so support is method-sensitive.

The budget panel does not flag 2010 or 2011 under either method. PELT exact-year calls are
2006, 2016, and 2021; SeedBS calls 2023. This does not support the proposed
classification-transition interpretation. In education, SeedBS also produces
more single-series calls than PELT (218 versus 109); its flagged 2023 year has
only one series contributing, so it should not be read as a broad panel event.

These findings are exploratory. Region series are dependent, panel composition
and coverage vary by year, the SeedBS threshold was calibrated for 25-point
Gaussian null series, and the budget regulation date has not been linked
chapter-by-chapter to these specific variables. The results are not evidence
that the education source transition caused the 2009 cluster.

# scripts/aggregate/

Flattens the raw per-source tables under `data/` into clean long-format CSVs under `data-aggregated/` (gitignored). Each script is independent (no shared run order) and owns one policja.pl category, except `bdl_population.py` which is the population-data side:

| Script | Reads | Writes |
|---|---|---|
| `kodeks_karny.py` | `data/statystyka-policja/kodeks-karny/Art*_xlsx` | `data-aggregated/statystyka-policja/kodeks-karny.csv` (+ `-notes.csv`) |
| `przestepstwa_ogolem.py` | `data/statystyka-policja/przestepstwa-ogolem`'s category tree | `data-aggregated/statystyka-policja/przestepstwa-ogolem.csv` (+ `category-tree.csv`) |
| `ruch_drogowy_crosswalk.py` | `data/statystyka-policja/ruch-drogowy` (26 yearly report folders, unstable filenames) | `data-aggregated/statystyka-policja/ruch-drogowy/crosswalk.csv` — must run before `ruch_drogowy.py` |
| `ruch_drogowy.py` | same, using the crosswalk above | one CSV per topic under `data-aggregated/statystyka-policja/ruch-drogowy/` |
| `ruch_drogowy_legacy_doc.py` | the legacy 1975-2011 `.doc` report (recovered via `strings`, since no docx/olefile/libreoffice/antiword is available here) | `ruch-drogowy/wypadki-1975-2011-legacy.csv` |
| `przemoc_domowa.py` | `wybrane-statystyki/przemoc-domowa`'s 4 incompatible sub-period schemas | `wybrane-statystyki/przemoc-domowa.csv`, tagged by source framing rather than forced into one metric set |
| `utoniecia.py` | `wybrane-statystyki/utoniecia` (1998-2025, one PDF per year, drifting filenames) | `wybrane-statystyki/utoniecia.csv` |
| `wybrane_statystyki_group_a.py` | the stable-schema `wybrane-statystyki` subfolders: bron, zaginieni, wybrane-ustawy-szczegol, handel-ludzmi-i-przest, kradzieze-samochodow, przestepczosc-nieletni | one CSV per subfolder |
| `zamachy_i_wychlodzenia.py` | the two remaining `wybrane-statystyki` subfolders: zamachy-samobojcze, zgony-z-powodu-wychlodz | one CSV per subfolder |
| `wybrane_statystyki_html.py` | `wybrane-statystyki/{maloletni-pod-wplywem,nietrzezwi-podejrzani-o-popeln}/page.html` (raw HTML, via `scripts/ingest/download_html_wybrane_statystyki.py` — these two have no downloadable file) | one CSV per subfolder |
| `bdl_population.py` | raw BDL API JSON under `data/statystyka-baza-danych-lokalnych/raw/` | `data-aggregated/statystyka-baza-danych-lokalnych/population-by-{residence,age-group}-sex.csv`; runs its own consistency spot-checks on completion |
| `dziennik_ustaw.py` | `data/dziennik-ustaw/amendments/*.html` (Kodeks Karny amending acts) | `data-aggregated/dziennik-ustaw/kodeks-karny-amendments.csv` (+ a `notes-vs-source-mismatches.csv` flagging disagreements with the hand-curated `kodeks-karny-notes.csv`) |
| `dziennik_urzedowy_kgp.py` | `data/dziennik-urzedowy-kgp/index/` + `raw/` (built by `scripts/ingest/download_kgp_gazette.py`) | `data-aggregated/dziennik-urzedowy-kgp/counting-rule-candidates.csv` — keyword-matched candidate crime-statistics counting/catalog regulations across every indexed year, 2001-2025, for human review before promotion into `benchmark/system-break-labels.csv` |

See `doc/data-aggregation-plan.md` for the per-category join-problem rationale, `doc/analysis/methodology.md` for the structural breaks every output here has to be read against, `doc/dziennik-ustaw.md` for the legislative-amendment registry's own parsing approach and limitations, and `doc/kgp-gazette.md` for the internal-gazette census.

# scripts/ingest/

Downloads raw source files and extracts their tables into `data/` (gitignored). Run in this order for the policja.pl side:

1. **`build_manifest.py`** — crawls `site-map/` and writes `scripts/manifest.json` (gitignored), the list of every policja.pl file to download.
2. **`download_and_parse.py`** — downloads each manifest entry into `data/statystyka-policja/`, parses it (xlsx/xls/pdf/docx — see `pdf_tables.py` for the PDF table-extraction logic), and writes `tables/*.csv` + `content.md`/`metadata.md` per source. Restartable: checkpoints to `data/statystyka-policja/progress.json`, skips entries already marked done.
3. **`rename_tables.py`** — one-off pass giving `tables/*.csv` files descriptive names (derived from sheet names, PDF headings, or the CSV's own header row) instead of positional names, rewriting `content.md` references to match.

**`download_bdl.py`** is the independent population-data pipeline: pulls GUS Bank Danych Lokalnych population series (by voivodeship, sex, age band) into `data/statystyka-baza-danych-lokalnych/`. Also restartable via its own `progress.json`. See `doc/bdl-population.md` for what it pulls and why; note the BDL API's anonymous rate limit is often exhausted by other traffic on this cluster's shared egress IP — the script retries with backoff, but a `BDL_API_KEY` env var (registered client UUID) raises the limit if it stalls.

**`download_dziennik_ustaw.py`** is the independent legislative-amendment-history pipeline: pulls
Kodeks Karny's full amendment list and every amending act's metadata+text from the Sejm ELI API
(`api.sejm.gov.pl`) into `data/dziennik-ustaw/`. Restartable via its own `progress.json`. See
`doc/dziennik-ustaw.md` for why (checking whether a kodeks-karny anomaly coincides with a real
legislative change) and the API endpoints used.

**`fix_miesiace_continuation.py`** is a one-off, idempotent patch (not part of the main run order) for a confirmed `pdf_tables.py` defect: some ruch-drogowy `miesiace` (monthly) tables continue onto a new PDF page without redrawing their ruled border, so the grid-line table detector misses the continuation rows entirely and they fall through into that page's plain text. Re-derives the dropped rows directly from each report's own `content.md` and appends them to the affected `tables/*.csv`, rather than re-running the live download/parse pipeline (which would risk `rename_tables.py` renumbering unrelated tables in the same report). Safe to re-run if new reports are added.

**`download_kgp_gazette.py`** is the independent KGP internal-gazette census pipeline: indexes and
downloads every issue of the Police Headquarters internal regulatory gazette, 2001-2025 (API for
2010-2025, scraped BIP index + full-issue download for 2001-2009), into
`data/dziennik-urzedowy-kgp/`. Restartable via its own `progress.json`. Built to replace an earlier
one-off two-file archival of just the 2004/2009 issues already known from the structural-break
registry — that reactive-only sourcing was a real gap (see `benchmark/README.md`'s sourcing section).
See `doc/kgp-gazette.md` for the site structure and known limitations (no source found for 1999-2000).

**`download_html_wybrane_statystyki.py`** is a one-off, two-page archival script (not part of the main run order): the two `wybrane-statystyki` pages with no downloadable file at all (data only as an inline HTML table — `build_manifest.py`'s crawl only captures bullets ending in a downloadable-file extension, so these were never picked up by the regular pipeline) are fetched directly into `data/statystyka-policja/wybrane-statystyki/{topic}/page.html`, which `scripts/aggregate/wybrane_statystyki_html.py` then parses.

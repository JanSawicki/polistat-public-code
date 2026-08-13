"""Download population data from GUS's Bank Danych Lokalnych (BDL) API for
population-adjustment (per-capita) normalization of the policja.pl crime/
incident series.

API docs: https://api.stat.gov.pl/Home/BdlApi. Base URL
https://bdl.stat.gov.pl/api/v1/. Anonymous callers are capped at 100
requests/15min, but on this cluster the egress IP is shared and that cap is
routinely exhausted by other traffic well before we make 100 calls ourselves
-- the API still returns HTTP 200 when throttled, with a JSON body like
{"errorResult": "Przekroczono limit wywołań API! ..."} instead of results, so
that body has to be detected and retried rather than relying on the status
code. Set BDL_API_KEY (a client UUID from registering at the docs page above)
to raise the limit if this keeps stalling.

We pull two subjects under LUDNOŚĆ > STAN LUDNOŚCI:
- P1336: ludność wg miejsca zamieszkania i płci w podziale na miasto i wieś
  (total population by sex, urban/rural split) -- the core per-capita
  denominator.
- P2137: ludność wg grup wieku i płci (population by age band and sex) --
  needed for age-banded rates (e.g. juvenile population for
  przestepczosc-nieletni, age-group victim rates in ruch-drogowy).

Geographic granularity: country total (unit level 0) + the 16 voivodeships
(unit level 2), matching the finest regional breakdown actually used in the
aggregated policja.pl data (wojewodztwo-level, no powiat-level series there).

This script only downloads and caches raw API responses under
data/statystyka-baza-danych-lokalnych/ (one JSON per subject+unit, plus
variable/unit metadata) -- restartable via progress.json, same pattern as
download_and_parse.py. Flattening into a long-format CSV under
data-aggregated/statystyka-baza-danych-lokalnych/ is a separate aggregate/
step, once the raw response shape is confirmed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-baza-danych-lokalnych"
META = DATA / "_meta"
RAW = DATA / "raw"
PROGRESS_FILE = DATA / "progress.json"

BASE = "https://bdl.stat.gov.pl/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0 (research data collection; polistats project)"}
if os.environ.get("BDL_API_KEY"):
    HEADERS["X-ClientId"] = os.environ["BDL_API_KEY"]

SUBJECTS = {
    "ludnosc-stan-miasto-wies": "P1336",
    "ludnosc-grupy-wieku-plec": "P2137",
}
UNIT_LEVELS = [0, 2]

MAX_ATTEMPTS = 40
BASE_BACKOFF = 15
MAX_BACKOFF = 120


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}


def save_progress(progress: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def api_get(path: str, params: dict) -> dict:
    """GET against the BDL API, retrying on network errors and on the
    in-band rate-limit error body (HTTP 200 with no "results" key)."""
    qs = urllib.parse.urlencode({**params, "format": "json"}, doseq=True)
    url = f"{BASE}{path}?{qs}"
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
            if "results" in body or "result" in body:
                return body
            last_err = body.get("errorResult", body)
        except Exception as e:
            last_err = e
        wait = min(MAX_BACKOFF, BASE_BACKOFF * (1 + attempt // 3))
        print(f"  ! {path} attempt {attempt+1}/{MAX_ATTEMPTS} failed ({last_err}), retrying in {wait}s",
              file=sys.stderr, flush=True)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} after {MAX_ATTEMPTS} attempts: {last_err}")


def paginate(path: str, params: dict) -> list:
    results = []
    page = 0
    while True:
        body = api_get(path, {**params, "page": page, "page-size": 100})
        page_results = body.get("results", [])
        results.extend(page_results)
        total = body.get("totalRecords", len(results))
        page += 1
        if len(results) >= total or not page_results:
            break
    return results


def fetch_units() -> list:
    dest = META / "units.json"
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    units = []
    for level in UNIT_LEVELS:
        units.extend(paginate("/units", {"level": level}))
    META.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(units, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cached {len(units)} units -> {dest}")
    return units


def fetch_variables(subject_id: str) -> list:
    dest = META / f"variables-{subject_id}.json"
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    variables = paginate("/variables", {"subject-id": subject_id})
    META.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(variables, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cached {len(variables)} variables for {subject_id} -> {dest}")
    return variables


def fetch_unit_data(subject_name: str, unit_id: str, var_ids: list, progress: dict):
    key = f"{subject_name}/{unit_id}"
    if progress.get(key) == "done":
        return
    dest = RAW / subject_name / f"{unit_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    results = paginate(f"/data/by-unit/{unit_id}", {"var-id": var_ids})
    dest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    progress[key] = "done"
    save_progress(progress)
    print(f"  {key}: {len(results)} variable-series -> {dest}")
    time.sleep(1.0)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    print("Resolving units (country + voivodeships)...")
    units = fetch_units()
    unit_ids = [u["id"] for u in units]
    print(f"{len(unit_ids)} units total")

    for subject_name, subject_id in SUBJECTS.items():
        print(f"\nResolving variables for {subject_name} ({subject_id})...")
        variables = fetch_variables(subject_id)
        var_ids = [v["id"] for v in variables]
        print(f"{len(var_ids)} variables for {subject_name}")

        print(f"Fetching data for {subject_name} across {len(unit_ids)} units...")
        for n, unit_id in enumerate(unit_ids):
            print(f"[{n+1}/{len(unit_ids)}] {subject_name}/{unit_id}", flush=True)
            fetch_unit_data(subject_name, unit_id, var_ids, progress)

    print("\nDone. Raw responses cached under", RAW)


if __name__ == "__main__":
    main()

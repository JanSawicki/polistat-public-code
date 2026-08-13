"""Download Kodeks Karny's amendment history from Dziennik Ustaw (Poland's
official Journal of Laws) via the Sejm ELI API, for cross-referencing
kodeks-karny anomalies against actual legislative changes
(see doc/dziennik-ustaw.md and doc/analysis/anomalies.md).

isap.sejm.gov.pl's own web UI is CAPTCHA-gated against automated access, but
it documents the underlying open REST API at api.sejm.gov.pl (no robots.txt,
no auth, returns clean JSON/HTML) -- confirmed working by hand before writing
this script. Two calls per amending act:

1. GET /eli/acts/DU/1997/553 -- the consolidated Kodeks Karny act itself
   (Dz.U. 1997 nr 88 poz. 553). Its `references` field lists every amending
   act ("Akty zmieniające") with an ELI id and effective date.
2. For each amending act, GET /eli/acts/DU/{year}/{pos} for metadata and
   /eli/acts/DU/{year}/{pos}/text.html for the full text -- the latter is
   what scripts/aggregate/dziennik_ustaw.py greps for per-article change
   clauses ("art. X otrzymuje brzmienie", etc).

~80 total requests for the whole Kodeks Karny amendment history -- a small,
one-time, restartable crawl, not a recurring pipeline.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "dziennik-ustaw"
AMENDMENTS = DATA / "amendments"
PROGRESS_FILE = DATA / "progress.json"

BASE = "https://api.sejm.gov.pl/eli/acts"
KODEKS_KARNY = ("DU", "1997", "553")
HEADERS = {"User-Agent": "Mozilla/5.0 (research data collection; polistats project)"}

MAX_ATTEMPTS = 8
BASE_BACKOFF = 5
REQUEST_DELAY = 0.75


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}


def save_progress(progress: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def api_get(url: str, as_json: bool = True):
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            return json.loads(body) if as_json else body.decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
        wait = BASE_BACKOFF * (attempt + 1)
        print(f"  ! {url} attempt {attempt+1}/{MAX_ATTEMPTS} failed ({last_err}), retrying in {wait}s",
              file=sys.stderr, flush=True)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} after {MAX_ATTEMPTS} attempts: {last_err}")


def fetch_kk_act() -> dict:
    publisher, year, pos = KODEKS_KARNY
    dest = DATA / "kodeks-karny-act.json"
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    body = api_get(f"{BASE}/{publisher}/{year}/{pos}")
    DATA.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"cached Kodeks Karny act metadata -> {dest}")
    return body


def extract_amending_acts(kk_act: dict) -> list[dict]:
    """The `references` field groups related acts by relationship type; the
    amending-acts list is keyed by a label containing "zmieniając" (seen in
    the live response as "Akty zmieniające"). Each entry carries at least an
    ELI id; effective date may live under a few different key names
    depending on API version, so check all the plausible ones and keep
    whatever is present rather than assuming one name."""
    refs = kk_act.get("references", {})
    amending = []
    for label, entries in refs.items():
        if "zmieniając" not in label.lower():
            continue
        if isinstance(entries, list):
            amending.extend(entries)
    return amending


def parse_eli_id(entry: dict) -> tuple[str, str] | None:
    """Each reference entry should resolve to a (year, pos) pair for the
    /DU/{year}/{pos} path. The exact field name varies by API response shape
    (seen: a nested `act` dict, or top-level `year`/`pos`, or an `id`/`ELI`
    string like "DU/2008/782") -- try each in turn rather than assuming one."""
    act = entry.get("act", entry)
    year = act.get("year")
    pos = act.get("pos") or act.get("position")
    if year and pos:
        return str(year), str(pos)
    for key in ("id", "ELI", "eli"):
        raw = act.get(key)
        if raw and "/" in str(raw):
            parts = str(raw).split("/")
            if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
                return parts[-2], parts[-1]
    return None


def fetch_amendment(year: str, pos: str, progress: dict):
    key = f"{year}/{pos}"
    if progress.get(key) == "done":
        return
    AMENDMENTS.mkdir(parents=True, exist_ok=True)
    meta_dest = AMENDMENTS / f"{year}-{pos}.json"
    text_dest = AMENDMENTS / f"{year}-{pos}.html"

    if not meta_dest.exists():
        meta = api_get(f"{BASE}/DU/{year}/{pos}")
        meta_dest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(REQUEST_DELAY)

    if not text_dest.exists():
        try:
            text = api_get(f"{BASE}/DU/{year}/{pos}/text.html", as_json=False)
        except RuntimeError as e:
            print(f"  ! no text.html for {key}, skipping text ({e})", file=sys.stderr)
            text = ""
        text_dest.write_text(text, encoding="utf-8")
        time.sleep(REQUEST_DELAY)

    progress[key] = "done"
    save_progress(progress)
    print(f"  {key}: cached -> {meta_dest.name}, {text_dest.name}")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    print("Fetching Kodeks Karny act metadata...")
    kk_act = fetch_kk_act()

    amending_entries = extract_amending_acts(kk_act)
    print(f"Found {len(amending_entries)} amending-act references")

    eli_ids = []
    unresolved = 0
    for entry in amending_entries:
        parsed = parse_eli_id(entry)
        if parsed:
            eli_ids.append(parsed)
        else:
            unresolved += 1
    if unresolved:
        print(f"  ! {unresolved} amending-act references could not be resolved to a year/pos "
              f"-- inspect data/dziennik-ustaw/kodeks-karny-act.json's `references` field by hand",
              file=sys.stderr)

    print(f"Fetching {len(eli_ids)} amending acts...")
    for n, (year, pos) in enumerate(eli_ids):
        print(f"[{n+1}/{len(eli_ids)}] DU/{year}/{pos}", flush=True)
        fetch_amendment(year, pos, progress)

    print(f"\nDone. {len(eli_ids)} amending acts cached under {AMENDMENTS}")


if __name__ == "__main__":
    main()

"""Index and download the full run of the Police Headquarters (KGP) internal
regulatory gazette (Dziennik Urzędowy Komendy Głównej Policji), replacing the
prior one-time two-file archival of just the 2004/2009 issues already known
from the structural-break registry.

Why: the benchmark's `system-wide` labels (doc/analysis/panel-break-detection.md
§4.1, benchmark/system-break-labels.csv) were previously sourced from this
gazette only for years where a break had *already* been spotted by eye --
i.e. the search was seeded by the answer, not an independent census. This
script builds the independent census: every issue's index, 2001-2025, so
scripts/aggregate/dziennik_urzedowy_kgp.py can search for crime-statistics
counting/catalog regulations across every year, not just the ones already
suspected.

Two site eras, confirmed by hand before writing this script (2026-08-12):

1. **2010-2025**: `edziennik.policja.gov.pl`'s JSON API
   (`/api/eli/acts/DU_KGP/{year}`) returns every issue's full title text
   directly -- no PDF download needed to filter candidates. Confirmed working
   back to 2010 (80 items); 2009 and earlier return empty via this API.
2. **2001-2009**: no API. `kgp.bip.policja.gov.pl/kgp/dziennik-urzedowy-kgp`
   lists a per-year index page (`.../<oid>,<year>.html`, oid varies per year --
   scraped from the landing page, not hardcoded); each year page links a
   `skorowidz_<year>.pdf` (an alphabetical subject index covering every issue
   of that year -- "Nr N, poz. M" plus a one-line subject) and the individual
   `dziennik_NN_<year>.pdf` issue files. The skorowidz is what makes a
   pre-API year searchable without downloading every issue.

**Known gap**: no index for 1999-2000 was found (the BIP landing page's
earliest year is 2001; the panel's own detected breadth is 0 in 1999-2003
regardless, so this is a documented limitation, not a blocker -- see
doc/kgp-gazette.md).

Restartable via progress.json, like download_dziennik_ustaw.py.
"""
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "dziennik-urzedowy-kgp"
INDEX = DATA / "index"
RAW = DATA / "raw"
PROGRESS_FILE = DATA / "progress.json"

API_BASE = "https://edziennik.policja.gov.pl/api/eli/acts/DU_KGP"
BIP_LANDING = "http://kgp.bip.policja.gov.pl/kgp/dziennik-urzedowy-kgp"
HEADERS = {"User-Agent": "Mozilla/5.0 (research data collection; polistats project)"}

# bip.kgp.policja.gov.pl's cert chain hits a missing-intermediate-CA gap in
# this environment's trust store, not an invalid certificate (`curl -k`
# connects fine; confirmed by download_kgp_gazette.py's prior version and
# doc/analysis/methodology.md's "reached via a workaround" note) -- skip
# verification for this host only, since its download.php links 301-redirect
# http -> https and urllib follows the redirect.
_UNVERIFIED_CTX = ssl._create_unverified_context()

API_YEARS = range(2010, 2026)
BIP_YEARS = range(2001, 2010)  # no index found for 1999-2000; see module docstring

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


def fetch(url: str, as_json: bool = True):
    last_err = None
    ctx = _UNVERIFIED_CTX if "bip.kgp.policja.gov.pl" in url else None
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                body = resp.read()
            return json.loads(body) if as_json else body
        except Exception as e:
            last_err = e
        wait = BASE_BACKOFF * (attempt + 1)
        print(f"  ! {url} attempt {attempt+1}/{MAX_ATTEMPTS} failed ({last_err}), retrying in {wait}s",
              file=sys.stderr, flush=True)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url} after {MAX_ATTEMPTS} attempts: {last_err}")


def fetch_api_year(year: int, progress: dict):
    key = f"api/{year}"
    dest = INDEX / f"{year}.json"
    if progress.get(key) == "done" and dest.exists():
        return
    body = fetch(f"{API_BASE}/{year}")
    INDEX.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(body.get("items", []))
    progress[key] = "done"
    save_progress(progress)
    print(f"  {year}: {n} items -> {dest.name}")
    time.sleep(REQUEST_DELAY)


def fetch_bip_landing_year_urls() -> dict[int, str]:
    """The BIP landing page lists each year's index-page URL under a
    year-specific OID that isn't derivable from the year number -- scrape it
    fresh rather than hardcoding OIDs that could change."""
    body = fetch(BIP_LANDING, as_json=False).decode("utf-8", errors="replace")
    year_urls = {}
    for m in re.finditer(r'href="(/kgp/dziennik-urzedowy-kgp/\d+,(\d{4})\.html)"', body):
        path, year = m.group(1), int(m.group(2))
        year_urls[year] = "http://kgp.bip.policja.gov.pl" + path
    return year_urls


LINK_RE = re.compile(
    r'<a\b[^>]*\bhref="(http://(?:www\.policja\.pl/ftp/dzienniki_urzedowe/\d{4}/[^"]+\.pdf'
    r'|bip\.kgp\.policja\.gov\.pl/download\.php\?s=18&amp;id=\d+))"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _clean_label(html_fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_fragment)).strip()


def fetch_bip_year(year: int, year_url: str, progress: dict):
    """Two link styles coexist across years (and sometimes within one year):
    static `.../ftp/dzienniki_urzedowe/<year>/dziennik_NN_<year>.pdf` files,
    and `download.php?s=18&id=NNNN` redirects with no descriptive filename --
    the latter need the anchor's own text (e.g. "... nr 3 z dnia 27 lutego
    2009 r.") kept alongside the URL to identify the issue."""
    key = f"bip/{year}"
    issues_dest = INDEX / f"{year}_issues.json"
    skorowidz_dest = INDEX / f"{year}_skorowidz.pdf"
    if progress.get(key) == "done" and issues_dest.exists():
        return

    page = fetch(year_url, as_json=False).decode("utf-8", errors="replace")
    links = [(m.group(1).replace("&amp;", "&"), _clean_label(m.group(2))) for m in LINK_RE.finditer(page)]
    seen = set()
    issues, skorowidz_urls = [], []
    for url, label in links:
        if url in seen:
            continue
        seen.add(url)
        if "skorowidz" in url.lower() or "skorowidz" in label.lower():
            skorowidz_urls.append(url)
        else:
            issues.append({"label": label, "url": url})

    INDEX.mkdir(parents=True, exist_ok=True)
    issues_dest.write_text(json.dumps({"year": year, "issues": issues}, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    if skorowidz_urls and not skorowidz_dest.exists():
        pdf_bytes = fetch(skorowidz_urls[0], as_json=False)
        skorowidz_dest.write_bytes(pdf_bytes)
        time.sleep(REQUEST_DELAY)

    progress[key] = "done"
    save_progress(progress)
    print(f"  {year}: {len(issues)} issues, skorowidz={'yes' if skorowidz_urls else 'NO'} -> {issues_dest.name}")
    time.sleep(REQUEST_DELAY)


def download_bip_issues(progress: dict):
    """Full-text download of every 2001-2009 issue (~167 small PDFs) rather
    than relying on the skorowidz alone: only 5 of the 9 years have a
    skorowidz link, and this era's issue count is small enough that
    downloading everything is more reliable than trusting a possibly
    incomplete subject index."""
    RAW.mkdir(parents=True, exist_ok=True)
    total = 0
    for year in BIP_YEARS:
        issues_path = INDEX / f"{year}_issues.json"
        if not issues_path.exists():
            continue
        issues = json.loads(issues_path.read_text(encoding="utf-8"))["issues"]
        for i, issue in enumerate(issues, start=1):
            key = f"bip-pdf/{year}/{i}"
            dest = RAW / f"{year}_{i:02d}.pdf"
            if progress.get(key) == "done" and dest.exists():
                continue
            pdf_bytes = fetch(issue["url"], as_json=False)
            dest.write_bytes(pdf_bytes)
            progress[key] = "done"
            save_progress(progress)
            total += 1
            time.sleep(REQUEST_DELAY)
    print(f"  downloaded {total} new issue PDFs -> {RAW}")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    progress = load_progress()

    print(f"Indexing {len(list(API_YEARS))} years via edziennik.policja.gov.pl API (2010-2025)...")
    for year in API_YEARS:
        fetch_api_year(year, progress)

    print(f"\nIndexing {len(list(BIP_YEARS))} years via kgp.bip.policja.gov.pl (2001-2009)...")
    year_urls = fetch_bip_landing_year_urls()
    missing = [y for y in BIP_YEARS if y not in year_urls]
    if missing:
        print(f"  ! no BIP index page found for years {missing} -- check {BIP_LANDING} by hand", file=sys.stderr)
    for year in BIP_YEARS:
        if year in year_urls:
            fetch_bip_year(year, year_urls[year], progress)

    print(f"\nDownloading full text of every 2001-2009 issue...")
    download_bip_issues(progress)

    print(f"\nDone. Indexed under {INDEX}, issue PDFs under {RAW}. "
          f"Known gap: no source found for 1999-2000 (see module docstring).")


if __name__ == "__main__":
    main()

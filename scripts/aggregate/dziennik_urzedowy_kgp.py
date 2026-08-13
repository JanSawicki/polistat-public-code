"""Aggregate data/dziennik-urzedowy-kgp/ (built by
scripts/ingest/download_kgp_gazette.py) into a candidate registry of KGP
internal regulations touching crime-statistics counting/catalog rules, per
doc/analysis/panel-break-detection.md's ground-truth section.

Why this exists: benchmark/system-break-labels.csv's `system-wide` rows for
2004 and 2009 were previously sourced from this same gazette, but only found
by searching a narrow date window *around a break the eyeball process had
already spotted* -- 2013/2014 carried no citation at all
(`kind: documented-methodology`, source_kind: none/methodology-only). This
script is the independent census that search should have been: scan every
indexed issue 2001-2025 for the base regulation's lineage and its likely
successors, so a candidate the eyeball process never went looking for still
turns up.

Two eras, matching the ingest script:

- **2010-2025**: titles come straight from the API index JSON
  (data/dziennik-urzedowy-kgp/index/<year>.json) -- no PDF needed to filter,
  Polish legal-document titles are descriptive enough on their own. A PDF is
  fetched only for matched candidates, to extract a text snippet.
- **2001-2009**: every issue was downloaded whole
  (data/dziennik-urzedowy-kgp/raw/<year>_<NN>.pdf) since only some years had
  a skorowidz link; each PDF is a multi-act omnibus issue (like a Dziennik
  Ustaw omnibus act), so it's pdftotext'd and searched directly, with the
  nearest preceding "ZARZĄDZENIE/DECYZJA/WYTYCZNE NR N ... z dnia D r." line
  kept as the citation.

Precision-first, not exhaustive: KEYWORDS is a deliberately wide net (a title
just needs to plausibly be about crime-statistics collection, counting
rules, or the catalog/classification apparatus) because a human is meant to
read every row of the output CSV before it's promoted into
benchmark/system-break-labels.csv -- see benchmark/README.md's "Extending
the benchmark" section. A missed candidate is a bigger problem here than an
over-inclusive one, the opposite trade-off from dziennik_ustaw.py's
per-article extraction (which feeds an automated `flag_if_amended` check, not
a human read), so KEYWORDS is broader on purpose -- with one exception: an
earlier version included a bare "KSIP" (Krajowy System Informacyjny Policji)
keyword, which alone produced 462 of 634 first-pass hits. KSIP is the
general-purpose police records database referenced in routine, unrelated
regulations (weapons registries, fingerprint records, personnel access
grants, ...), not specific to crime-*counting* rules -- dropped as
non-discriminative rather than kept "for recall", since 462 near-duplicate
rows from one over-broad term would have buried the real candidates, not
just added a few noisy ones.
"""
import csv
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "dziennik-urzedowy-kgp"
INDEX = DATA / "index"
RAW = DATA / "raw"
OUT = ROOT / "data-aggregated" / "dziennik-urzedowy-kgp"

API_YEARS = range(2010, 2026)
BIP_YEARS = range(2001, 2010)
API_BASE = "https://edziennik.policja.gov.pl"
HEADERS = {"User-Agent": "Mozilla/5.0 (research data collection; polistats project)"}
REQUEST_DELAY = 0.75

# Deliberately wide net -- see module docstring. Grouped so the matched group
# is recorded (helps a reviewer see WHY something matched without re-reading
# the whole snippet).
KEYWORDS = {
    "statystyka-przestepczosci": re.compile(
        r"dan(?:e|ych) statystyczn\w* o przest[eę]pczo|statystyk\w* przest[eę]pczo", re.I),
    "katalog-symboli": re.compile(r"katalog\w* symboli cyfrowych|symboli cyfrowych do formularzy", re.I),
    "sprawozdawczosc-policji": re.compile(r"sprawozdawczo[śs]ci\w*(?:\s+\w+){0,3}\s+(?:policj|pracy policji)", re.I),
    "rejestracja-przestepstw": re.compile(r"rejestr(?:acj|owani)\w* przest[eę]pstw|klasyfikacj\w* przest[eę]pstw", re.I),
    "postepowania-przygotowawcze": re.compile(
        r"postepowa\w* przygotowawcz\w*(?:\s+\w+){0,4}\s+(?:statysty|sprawozdaw|rejestr|ewidencj)", re.I),
    "nieletni-statystyka": re.compile(
        r"nieletni\w*(?:\s+\w+){0,4}\s+(?:statysty|sprawozdaw|rejestr|ewidencj)"
        r"|(?:statysty|sprawozdaw|rejestr|ewidencj)\w*(?:\s+\w+){0,4}\s+nieletni", re.I),
}

HEADER_RE = re.compile(
    r"(ZARZ[ĄA]DZENIE|DECYZJA|WYTYCZNE|OBWIESZCZENIE)\s+NR\s+\d+[a-zA-Z]?"
    r"\s+KOMENDANTA\s+G[ŁL][ÓO]WNEGO\s+POLICJI\s*\n?\s*z\s+dnia\s+[^\n]{5,60}",
    re.I,
)
SNIPPET_WINDOW = 200


def fetch(url: str) -> bytes:
    last_err = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}: {last_err}")


def pdf_to_text(pdf_path: Path) -> str:
    result = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                             capture_output=True, text=True, timeout=60)
    return result.stdout


def normalize_ws(text: str) -> str:
    """The API's title text uses non-breaking spaces (U+00A0) between some
    words (e.g. "danych statystycznych o\xa0przestępczości") -- a literal ' '
    in KEYWORDS silently never matches those, which is exactly how the 2013
    base-regulation repeal act (Zarządzenie nr 5) was missed on the first
    pass despite an otherwise-correct pattern. Collapse all whitespace
    variants before matching, everywhere text is searched."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " "))


def match_keywords(text: str) -> list[tuple[str, str]]:
    """Returns [(keyword_group, matched_substring), ...] for every KEYWORDS hit."""
    text = normalize_ws(text)
    hits = []
    for name, pattern in KEYWORDS.items():
        for m in pattern.finditer(text):
            hits.append((name, m.group(0)))
    return hits


def scan_api_years() -> list[dict]:
    rows = []
    for year in API_YEARS:
        index_path = INDEX / f"{year}.json"
        if not index_path.exists():
            continue
        items = json.loads(index_path.read_text(encoding="utf-8"))["items"]
        for item in items:
            title = normalize_ws(item.get("title", ""))
            hits = match_keywords(title)
            if not hits:
                continue
            eli = item["eli"]
            pdf_dest = RAW / f"api_{year}_{item['pos']}.pdf"
            snippet = title
            if not pdf_dest.exists():
                try:
                    pdf_bytes = fetch(f"{API_BASE}/{eli}/akt.pdf")
                    pdf_dest.write_bytes(pdf_bytes)
                    time.sleep(REQUEST_DELAY)
                except RuntimeError as e:
                    print(f"  ! could not fetch PDF for {eli}: {e}", file=sys.stderr)
                    pdf_dest = None
            if pdf_dest and pdf_dest.exists():
                text = pdf_to_text(pdf_dest)
                if text.strip():
                    snippet = text.strip()[:SNIPPET_WINDOW]
            for keyword, matched in hits:
                rows.append({
                    "year": year,
                    "era": "api",
                    "citation": f"Dz.Urz. KGP {item['displayAddress']}".replace("DZ. URZ. ", ""),
                    "eli_or_ref": eli,
                    "act_type": item.get("type", ""),
                    "date": item.get("promulgation", "")[:10],
                    "title": title,
                    "keyword_matched": keyword,
                    "matched_text": matched,
                    "raw_snippet": snippet.replace("\n", " ").strip(),
                    "pdf_path": str(pdf_dest.relative_to(ROOT)) if pdf_dest and pdf_dest.exists() else "",
                })
    return rows


def scan_bip_years() -> list[dict]:
    rows = []
    for year in BIP_YEARS:
        issues_path = INDEX / f"{year}_issues.json"
        if not issues_path.exists():
            continue
        issues = json.loads(issues_path.read_text(encoding="utf-8"))["issues"]
        for i, issue in enumerate(issues, start=1):
            pdf_path = RAW / f"{year}_{i:02d}.pdf"
            if not pdf_path.exists():
                continue
            text = pdf_to_text(pdf_path)
            if not text.strip():
                continue
            headers = list(HEADER_RE.finditer(text))
            for name, pattern in KEYWORDS.items():
                for m in pattern.finditer(text):
                    preceding = [h for h in headers if h.start() <= m.start()]
                    citation = re.sub(r"\s+", " ", preceding[-1].group(0)).strip() if preceding else "(no header found before match)"
                    start = max(0, m.start() - SNIPPET_WINDOW // 2)
                    end = min(len(text), m.end() + SNIPPET_WINDOW // 2)
                    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
                    rows.append({
                        "year": year,
                        "era": "bip",
                        "citation": f"Dz.Urz. KGP {year} nr {i} -- {citation}",
                        "eli_or_ref": issue["url"],
                        "act_type": "",
                        "date": "",
                        "title": issue.get("label", ""),
                        "keyword_matched": name,
                        "matched_text": m.group(0),
                        "raw_snippet": snippet,
                        "pdf_path": str(pdf_path.relative_to(ROOT)),
                    })
    return rows


def main():
    print("Scanning 2010-2025 (API era) titles for candidate crime-statistics regulations...")
    api_rows = scan_api_years()
    print(f"  {len(api_rows)} keyword hits across {len(set((r['year'], r['eli_or_ref']) for r in api_rows))} acts")

    print("Scanning 2001-2009 (BIP era) full issue text...")
    bip_rows = scan_bip_years()
    print(f"  {len(bip_rows)} keyword hits across {len(set((r['year'], r['eli_or_ref']) for r in bip_rows))} issues")

    rows = api_rows + bip_rows
    rows.sort(key=lambda r: (r["year"], r["date"]))

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "counting-rule-candidates.csv"
    fieldnames = ["year", "era", "citation", "eli_or_ref", "act_type", "date", "title",
                  "keyword_matched", "matched_text", "raw_snippet", "pdf_path"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    years_hit = sorted(set(r["year"] for r in rows))
    print(f"\n{len(rows)} candidate rows -> {out_path}")
    print(f"years with at least one candidate: {years_hit}")
    print("Every row needs a human read before being promoted into "
          "benchmark/system-break-labels.csv -- see benchmark/README.md.")


if __name__ == "__main__":
    main()

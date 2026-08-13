"""Aggregate data/dziennik-ustaw/amendments/*.html into a per-article
amendment registry for Kodeks Karny, per doc/dziennik-ustaw.md.

Each amending act's text.html is an omnibus document: it can amend many
unrelated acts in one go (Kodeks Karny, Kodeks Wykroczeń, Kodeks postępowania
karnego, ...), each under its own top-level "Art. N." section (the amending
act's own numbering, not the target code's article numbers). Confirmed by
direct inspection of several acts (e.g. DU/2022/2600 touches 20 different
acts across "Art. 1." through "Art. 33.") that "Kodeks karny" as a bare
substring search is not enough to isolate the right section -- "Kodeks karny
wykonawczy", "Kodeks karny skarbowy", and "Przepisy wprowadzające Kodeks
karny" are separate acts, confusingly enacted on the exact same date
(6 czerwca 1997), and some acts amend a *prior amending act* whose own title
contains "Kodeks karny oraz niektórych innych ustaw" without amending the
penal code itself at all.

Strategy, validated against two known cases before trusting it on the full
corpus (DU/1999/729, confirmed by hand to touch articles 149/152/153/+157a;
DU/2022/2600, the 2022-2023 KK reform, ~140 article-level changes):

1. Strip HTML tags to plain text.
2. Find each top-level "Art. N." heading (capital A) by requiring the
   matched numbers form a strictly increasing run starting at 1 -- this is
   how an amending act numbers its own articles, and distinguishes a real
   section heading from any other capitalized "Art. N." occurring in prose
   (e.g. a citation).
3. Keep only the section(s) whose lead-in text (first ~300 chars) contains
   the literal phrase "6 czerwca 1997 r. - Kodeks karny" with nothing
   (in particular not "wykonawczy"/"skarbowy") immediately following --
   this is specific enough to exclude all three confusable acts above.
4. Within a kept section, extract article numbers three ways, checked in
   this order so a more specific pattern claims its match before a generic
   one can: (a) "dodaje się art. M" -- a wholly new article M, so the
   unrelated anchor article in "po art. N dodaje się art. M" doesn't get
   credited instead of M; (b) "w art. N:" -- the common batch-change idiom
   (several paragraphs of article N change at once, enumerated below the
   colon) is an unambiguous signal on its own, no verb needed; (c) any other
   "art. N" followed within ~250 chars by a change verb (otrzymuje/otrzymują
   brzmienie / uchyla się / uchylają się / skreśla się / skreślają się /
   traci moc / zastępuje się wyrazami/wyrazem). Verified against DU/2008/1344
   that the batch idiom and plural verb forms matter: a first-pass version
   of this regex missed art. 202 and art. 267 amendments effective
   2008-12-18 entirely (both phrased as "w art. 202: a) ... otrzymują
   brzmienie") -- caught after manually grepping a sample of "supposedly
   unamended" target articles for false negatives before trusting the
   "no amendment found" conclusion on any of them.

False-negative risk: a handful of unusual phrasings this regex set doesn't
recognize will be missed silently (this is a precision-first approach, not
exhaustive parsing of Polish legislative grammar) -- `raw_snippet` is kept
per match so any result can be manually re-verified, and a finding that
doesn't match anything here is reported as "no matching amendment found",
not "definitely unamended".
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "dziennik-ustaw"
AMENDMENTS = DATA / "amendments"
OUT = ROOT / "data-aggregated" / "dziennik-ustaw"
POLICJA = ROOT / "data-aggregated" / "statystyka-policja"

TOP_LEVEL_ARTICLE_RE = re.compile(r"\bArt\.\s*(\d{1,3})\.")
KK_TARGET_RE = re.compile(r"6 czerwca 1997 r\.\s*-\s*Kodeks karny(?!\s+(wykonawczy|skarbowy))\b")
ADD_NEW_RE = re.compile(r"dodaje się art\.\s*(\d{1,3}[a-z]?)\b")
BATCH_RE = re.compile(r"\bw art\.\s*(\d{1,3}[a-z]?)\s*:")
GENERIC_ARTICLE_RE = re.compile(r"art\.\s*(\d{1,3}[a-z]?)\b")
CHANGE_VERBS_RE = re.compile(
    r"(?:otrzymuje brzmienie|otrzymują brzmienie|uchyla się|uchylają się|"
    r"skreśla się|skreślają się|traci moc|zastępuje się wyrazami|"
    r"zastępuje się wyrazem)"
)
# A semicolon followed by the next enumeration marker ("; 2)", "; a)") is how
# Polish amendment lists separate clauses -- without stopping the verb search
# there, an "art. N" mentioned only as a cross-reference inside a *different*
# clause's new wording can pick up that next clause's verb and be credited
# with an amendment that isn't really to N. Confirmed as a real bug, not a
# hypothetical: an early version of this regex grabbed "art. 258" mentioned
# inside art. 65's amended text (a cross-reference, "do sprawcy z art. 258
# stosuje się ...") and matched it to "art. 110 otrzymuje brzmienie" from the
# *next* enumerated point, producing a misleading raw_snippet (the conclusion
# that art. 258 was genuinely amended by that same act happened to still be
# correct -- it has its own separate, real "art. 258 otrzymuje brzmienie"
# clause later in the same text -- but the evidence captured for it was the
# wrong occurrence, a dedup collision waiting to hide a real false positive
# elsewhere). Caught by manually reading the snippet behind every "resolved"
# finding before trusting it.
CLAUSE_BOUNDARY_RE = re.compile(r";\s*(?:\d{1,3}|[a-ząęńłżźćś])\)")
VERB_WINDOW = 250
SNIPPET_WINDOW = 160


def html_to_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&oacute;", "ó", text)
    text = re.sub(r"\s+", " ", text)
    return text


def find_top_level_articles(text: str) -> list[tuple[int, int]]:
    kept = []
    expected = 1
    for m in TOP_LEVEL_ARTICLE_RE.finditer(text):
        num = int(m.group(1))
        if num == expected:
            kept.append((m.start(), num))
            expected += 1
    return kept


def kk_segments(text: str) -> list[str]:
    arts = find_top_level_articles(text)
    spans = []
    for i, (pos, _num) in enumerate(arts):
        end = arts[i + 1][0] if i + 1 < len(arts) else len(text)
        segment = text[pos:end]
        if KK_TARGET_RE.search(segment[:300]):
            spans.append(segment)
    return spans


def extract_article_changes(segment: str) -> list[tuple[str, str, str]]:
    """Returns (article, change_type, raw_snippet) tuples."""
    results = []
    claimed = []
    for m in ADD_NEW_RE.finditer(segment):
        article = m.group(1)
        snippet = segment[max(0, m.start() - 40):m.end() + 40].strip()
        results.append((article, "dodaje się (nowy artykuł)", snippet))
        claimed.append((m.start(), m.end()))
    for m in BATCH_RE.finditer(segment):
        article = m.group(1)
        snippet = segment[m.start():m.end() + 120].strip()
        results.append((article, "w art. X: (batch change)", snippet[:SNIPPET_WINDOW]))
        claimed.append((m.start(), m.end()))
    for m in GENERIC_ARTICLE_RE.finditer(segment):
        if any(s <= m.start() < e for s, e in claimed):
            continue
        boundary_m = CLAUSE_BOUNDARY_RE.search(segment, m.end())
        limit = min(m.end() + VERB_WINDOW, boundary_m.start() if boundary_m else len(segment))
        window = segment[m.end():limit]
        verb_m = CHANGE_VERBS_RE.search(window)
        if verb_m:
            article = m.group(1)
            snippet = segment[m.start():m.end() + verb_m.end()].strip()
            results.append((article, verb_m.group(0), snippet[:SNIPPET_WINDOW]))
    return results


def load_amendment_metadata(year: str, pos: str) -> dict:
    meta_path = AMENDMENTS / f"{year}-{pos}.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def main():
    html_files = sorted(AMENDMENTS.glob("*.html"))
    rows = []
    acts_with_kk_changes = 0
    for html_path in html_files:
        year, pos = html_path.stem.split("-", 1)
        html = html_path.read_text(encoding="utf-8")
        if not html.strip():
            continue  # no text.html was available for this act (see ingest script)
        text = html_to_text(html)
        segments = kk_segments(text)
        if not segments:
            continue
        acts_with_kk_changes += 1
        meta = load_amendment_metadata(year, pos)
        effective_date = meta.get("entryIntoForce") or meta.get("promulgation") or ""
        display_address = meta.get("displayAddress", f"Dz.U. {year} poz. {pos}")
        seen = set()
        for segment in segments:
            for article, change_type, snippet in extract_article_changes(segment):
                key = (article, year, pos, change_type)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "article": article,
                    "dziennik_ustaw_ref": display_address,
                    "eli_id": f"DU/{year}/{pos}",
                    "announcement_date": meta.get("announcementDate", ""),
                    "effective_date": effective_date,
                    "change_type": change_type,
                    "raw_snippet": snippet,
                })

    rows.sort(key=lambda r: (r["article"], r["effective_date"]))

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = OUT / "kodeks-karny-amendments.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["article", "dziennik_ustaw_ref", "eli_id",
                                          "announcement_date", "effective_date",
                                          "change_type", "raw_snippet"])
        w.writeheader()
        w.writerows(rows)

    print(f"{acts_with_kk_changes}/{len(html_files)} amending acts had a Kodeks Karny section")
    print(f"{len(rows)} article-amendment rows -> {out_path}")

    # Cross-check against the existing hand-curated kodeks-karny-notes.csv --
    # flag disagreements rather than silently overwriting (that file is out
    # of scope to rewrite in this pass).
    notes_path = POLICJA / "kodeks-karny-notes.csv"
    mismatches = []
    if notes_path.exists():
        amendment_years = {}
        for r in rows:
            if r["effective_date"]:
                amendment_years.setdefault(r["article"], set()).add(r["effective_date"][:4])
        with notes_path.open(newline="", encoding="utf-8") as f:
            for note in csv.DictReader(f):
                article = note["article"]
                manual_year = note["known_amendment_year"]
                sourced_years = amendment_years.get(article, set())
                if manual_year and manual_year not in sourced_years:
                    mismatches.append({
                        "article": article,
                        "manual_known_amendment_year": manual_year,
                        "sourced_amendment_years": ";".join(sorted(sourced_years)) or "(none found)",
                    })
    if mismatches:
        mismatch_path = OUT / "notes-vs-source-mismatches.csv"
        with mismatch_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["article", "manual_known_amendment_year",
                                              "sourced_amendment_years"])
            w.writeheader()
            w.writerows(mismatches)
        print(f"{len(mismatches)} disagreements between kodeks-karny-notes.csv and the sourced "
              f"registry -> {mismatch_path}")
    else:
        print("kodeks-karny-notes.csv's known_amendment_year values all agree with the sourced registry")


if __name__ == "__main__":
    main()

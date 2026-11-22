"""Build the year+filename -> topic_key crosswalk for data/ruch-drogowy.

26 yearly report folders, each PDF auto-split into 38-105 CSVs with
filenames generated from table content -- not stable across years (typos,
hyphenation drift, and in several years outright character-interleaving
corruption from the PDF extractor, e.g. "oswietleni-owleympadki-
olzemabici-olermanni" for what should be "oswietlenie-wypadki-ogolem-
zabici-ogolem-ranni-ogolem").

Strategy, in order:
1. Strip digits and collapse the filename to a normalized slug.
2. Generic noise filter: `page\\d+-table\\d+` / `label-value` are PDF
   extraction fragments (split/repeated table pieces), not real topics.
3. Exact match on the slug's prefix (everything before the first
   wypadki/zabici/ranni/kolizje token) against a canonical topic list built
   from every prefix that recurs across >=3 source tables.
4. A small override table for known single-row leaks: a Polish month name,
   voivodeship name, or specific cause phrase that in some year got
   extracted as its own one-row "table" instead of being folded into the
   parent breakdown table (miesiace / wojewodztwa / przyczyny respectively).
5. Fuzzy match (difflib ratio over the whole normalized string, threshold
   0.45) against all canonical exemplars, to catch the character-
   interleaving corruption -- SequenceMatcher's longest-matching-blocks
   approach is tolerant of that kind of corruption since most of the
   character sequence survives, just reordered/interspersed.
6. The handful (8 of 1885) that even fuzzy matching couldn't place were
   resolved by hand after reading the actual CSV content -- mostly
   variants of the "Art. 87 KW / Art. 178a KK" drink-driving-article-count
   family (naruszony_przepis) and one age-bracket leak ("7-14-l-at.csv").

This produces data-aggregated/ruch-drogowy/crosswalk.csv listing every
table with its assigned topic and match method/confidence, so low-
confidence fuzzy matches stay auditable rather than silently trusted.
"""
import csv
import difflib
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "ruch-drogowy"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "ruch-drogowy"

NOISE_RE = [re.compile(r"^page-table$"), re.compile(r"^label-value\d*$")]
YEAR_RE = re.compile(r"^(19|20)\d{2}$")

MONTHS = {"styczen", "luty", "marzec", "kwiecien", "maj", "czerwiec", "lipiec",
          "sierpien", "wrzesien", "pazdziernik", "listopad", "grudzien"}
VOIV = {"dolnoslaskie", "kujawsko-pomorskie", "lubelskie", "lubuskie", "lodzkie",
        "malopolskie", "mazowieckie", "opolskie", "podkarpackie", "podlaskie",
        "pomorskie", "slaskie", "swietokrzyskie", "warminsko-mazurskie",
        "wielkopolskie", "zachodniopomorskie", "ksp-warszawa"}
VEHICLE_WORDS = {"motocykl", "tramwaj", "autobus", "samochod-osobowy"}
ROAD_WORDS = {"autostrada", "droga-krajowa", "droga-wojewodzka"}
CAUSE_KEYWORDS = ["niedostosowanie-predkosci", "nieprawidlow", "nieustap",
                  "na-bariere-ochronna", "na-slup-znak", "skrzyzowanie-pierwszens",
                  "skrzyzowanie-erwszenstwe", "wskakiwanie-do-pojazdu",
                  "jazda-bez-wymaganego-oswietlenia", "inne-przyczyny", "niedostos"]

# Recurring (>=3 occurrences) but garbled prefixes whose content was read
# directly and confirmed to be the same topic as the target -- merged here
# rather than left as their own bogus canonical topic, which a frequency
# threshold alone can't distinguish from a real one.
PREFIX_REMAP = {
    "oswietlen-owleympadki-olzemabici-olermanni": "oswietlenie",
    "oswietle-golzeambici": "oswietlenie",
    "dki": "rodzaj-drogi",
    "p-iogolezm": "przyczyny",
    "niedost-waniepred": "przyczyny",
    "miejscezda": "miejsce-zdarzenia",
    "miejsc-ezdarzenia": "miejsce-zdarzenia",
    "miejscezd": "miejsce-zdarzenia",
    "rodzajdro-olwemypadki-lemzabici-lemranni": "rodzaj-uzytkownika-drogi-ofiary",
    "rod-juzytkowni-oogfoialermy": "rodzaj-uzytkownika-drogi-ofiary",
    "rodz-ajuzytkownika-ofiaryogolem": "rodzaj-uzytkownika-drogi-ofiary",
    "rodzaj-uzytkownika-drogi-ofiary-ogolem": "rodzaj-uzytkownika-drogi-ofiary",
    "ro-dzajuzytkownika-ofiaryogolem": "rodzaj-uzytkownika-drogi-ofiary",
    "nieustap-du": "przyczyny",
    "grupy-wieku-na-milion-osob": "grupy-wieku",
    "dzien-tygodnia": "dni-tygodnia",
    "niedziela-dni-tygodnia": "dni-tygodnia",
}

# Resolved by hand after reading the actual CSV (see module docstring point 6).
MANUAL_OVERRIDES = {
    "styczengrudzien-i-kw-oraz-a-i-kk": "naruszony-przepis",
    "czasookres-i-kwalifikacja-prawna-czynu-liczba-ujawnionych-osob": "naruszony-przepis",
    "art-kw": "naruszony-przepis",
    "przycz-o": "przyczyny",
    "l-at": "wiek",
    "ojeddnwoo": "rodzaj-drogi",
    "niedosto-arunkow": "przyczyny",
    "niedosto-warunkow": "przyczyny",
    "niedost-runkow": "przyczyny",
    "niedosto-epredkoscid": "przyczyny",
    "niedost-waniepre": "przyczyny",
    "nieust-zejazdu": "przyczyny",
    "przy-i-ogolez": "przyczyny",
    "p-ogolew": "przyczyny",
    # Found by consistency check (2026-06-24): these fuzzy-matched into
    # "wiek-ogolem"/"dzieci-i-mlodziez-ofiary" purely on character overlap
    # with the corrupted/short normalized names, despite being unrelated
    # topics -- confirmed by reading each raw CSV's actual header/content.
    "r-ownikadro-ofiary-zabici-ranni": "rodzaj-uzytkownika-drogi-ofiary",  # header "R,ownikadro,Ofiary,Zabici,Ranni" -> Piesi/Kierujący/... (participant role)
    "przyc-golzemabici": "przyczyny",  # "Niedostosowanie prędkości..." cause-of-accident table, garbled filename
    "przyc-ogolezmabici": "przyczyny",  # same cause-of-accident table family, different year's corruption
    "padkizwin-golzemabici": "przyczyny",  # same
    "prz-ki-ogolemza": "przyczyny",  # same
    "lenie-golemzabici-ogolemranni": "przyczyny",  # same
    "grupy-wieku-na-milion-osob-populacji-zabici-ranni": "grupy-wieku",  # per-million-population age-band rate, same family as the already-remapped "grupy-wieku-na-milion-osob" prefix, just with "populacji" not stripped by prefix_of
    "miesiace-wiek-ofiary-liczba-ofiar-zabici-ranni": "miesiace-wiek-ofiary",  # month x age-band cross table -- distinct dimension from both "miesiace" (no age split) and "dzieci-i-mlodziez-ofiary" (participant role, not age band); isolated rather than forced into either
    "miesiace-wiek-ofiary-zabici-ranni": "miesiace-wiek-ofiary",
    "wiek-sprawcy-wypadki-wypadkow-zabici-zabitych-ranni-rannych": "wiek-sprawcy",  # culprit's AGE breakdown, was fuzzy-matched into "pojazd-sprawcy" (culprit's VEHICLE TYPE breakdown) on character overlap alone -- different dimension entirely, given its own topic
    "obszar-zgony-na-miejscu-zdarzenia-wypadki-zabici-zgony-w-ciagu-dni": "obszar",  # urban/rural breakdown of death-at-scene-vs-within-30-days, was fuzzy-matched into "miejsce-zdarzenia" (location TYPE, e.g. pedestrian crossing) -- belongs with the existing "obszar" topic instead
    "wojewodztwa-zgony-na-miejscu-zdarzenia-wypadki-zabici-zgony-w-ciagu": "wojewodztwa",  # same stat, voivodeship breakdown -- belongs with the existing "wojewodztwa" topic instead
    "pr-golewmypadki-golemzabici": "przyczyny",  # garbled "Przyczyny..." (Niedostosowanie prędkości) cause-of-accident table, was fuzzy-matched into "rodzaj-wypadku" (collision type: czołowe/boczne) on character overlap alone
}

FUZZY_THRESHOLD = 0.45


def normalize(fname: str) -> str:
    s = fname[:-4]
    s = re.sub(r"\d+", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    # confirmed extraction defect: some years drop the leading "w" of "wypadki"
    s = re.sub(r"(^|-)ypadki(-|$)", r"\1wypadki\2", s)
    return s


def prefix_of(norm: str) -> str:
    stop = {"wypadki", "zabici", "ranni", "kolizje"}
    out = []
    for tok in norm.split("-"):
        if tok in stop:
            break
        out.append(tok)
    # no breakdown dimension at all -> nationwide yearly totals, a real and
    # important topic in its own right, not noise to be excluded from canon
    return "-".join(out) or "rok-ogolem"


def is_noise(norm: str) -> bool:
    return any(p.match(norm) for p in NOISE_RE)


def override_topic(norm: str):
    if norm in MONTHS:
        return "miesiace"
    if norm in VOIV:
        return "wojewodztwa"
    if norm in VEHICLE_WORDS:
        return "pojazd"
    if norm in ROAD_WORDS:
        return "rodzaj-drogi"
    for kw in CAUSE_KEYWORDS:
        if kw in norm:
            return "przyczyny"
    if norm in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[norm]
    return None


def build_crosswalk():
    all_tables = []
    for folder in sorted(DATA.iterdir()):
        if not folder.is_dir() or folder.name.endswith("_doc"):
            continue
        tables_dir = folder / "tables"
        if not tables_dir.is_dir():
            continue
        for table in sorted(tables_dir.glob("*.csv")):
            all_tables.append((folder.name, table))

    groups = defaultdict(list)
    for folder_name, table in all_tables:
        norm = normalize(table.name)
        if is_noise(norm) or override_topic(norm):
            continue
        pre = PREFIX_REMAP.get(prefix_of(norm), prefix_of(norm))
        groups[pre].append(norm)

    canon = {}
    for pre, norms in groups.items():
        if len(norms) < 3:
            continue
        canon[pre] = Counter(norms).most_common(1)[0][0]

    rows = []
    for folder_name, table in all_tables:
        norm = normalize(table.name)
        if is_noise(norm):
            rows.append((folder_name, table.name, "NOISE", "noise", 1.0))
            continue
        ov = override_topic(norm)
        if not ov:
            pre = PREFIX_REMAP.get(prefix_of(norm), prefix_of(norm))
            if pre in canon:
                rows.append((folder_name, table.name, pre, "exact-prefix", 1.0))
                continue
        if ov:
            method = "manual-override" if norm in MANUAL_OVERRIDES else "override"
            rows.append((folder_name, table.name, ov, method, 1.0))
            continue
        best_topic, best_ratio = None, 0.0
        for topic, exemplar in canon.items():
            r = difflib.SequenceMatcher(None, norm, exemplar).ratio()
            if r > best_ratio:
                best_ratio, best_topic = r, topic
        if best_ratio >= FUZZY_THRESHOLD:
            rows.append((folder_name, table.name, best_topic, "fuzzy", round(best_ratio, 3)))
        else:
            rows.append((folder_name, table.name, "UNCLASSIFIED", "fuzzy-low", round(best_ratio, 3)))

    return rows


PARTICIPANT_ROLE_KW = ("kierując", "pasażer", "pieszy")
CULPABILITY_KW = ("z winy",)
EVENT_KW = ("zderzeni", "czołow", "boczn", "tyln", "wywróceni", "najechani")
VOIV_KW = ("dolnoślą", "kujawsko", "lubelsk", "lubusk", "łódzk", "małopolsk",
           "mazowieck", "opolsk", "podkarpack", "podlask", "pomorsk", "śląsk",
           "świętokrzysk", "warmińsko", "wielkopolsk", "zachodniopomorsk")
ROAD_TYPE_KW = ("autostrad", "droga ", "jednokierun", "dwukierun", "odwó", "jednojezdn")
MONTH_HEADER_KW = ("miesiąc", "styczeń")


def sniff_content_topic(table: Path):
    """Re-derive the topic from row content for tables whose column-derived
    filename carries no dimension keyword at all (the rok-ogolem bucket is
    the catch-all for these -- the dimension is a row label, never a column
    header, so it's invisible to filename-based matching)."""
    with table.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    if not rows:
        return None
    # A multi-year, year-led table is only the national "lata" backbone if its
    # filename carries the "lata-" prefix. Several reports also extract a
    # same-shaped, same-header ("Wypadki Ogółem"/"Zabici Ogółem"/"Ranni
    # Ogółem") table for accidents involving children aged 0-14 specifically
    # (captioned "...wśród dzieci w wieku 0-14 lat" in content.md) under a
    # filename missing the "lata-" prefix, at ~1/8-1/10th the magnitude --
    # confirmed by comparing e.g. Wypadki2017_pdf's
    # lata-wypadki-ogolem-2008-100-...csv (49,054 accidents in 2008) against
    # its sibling wypadki-ogolem-2008-100-...csv (5,755 accidents in 2008,
    # the children-only count). Without the prefix check these were silently
    # contaminating data-aggregated/ruch-drogowy/lata.csv with the wrong
    # series under the same metric names.
    if table.name.startswith("lata-") and YEAR_RE.match(rows[0][0].strip()):
        return "lata"
    if table.name.startswith("lata-") and rows[0] and rows[0][0].strip().lower() == "rok":
        return "lata"
    if rows[0] and YEAR_RE.match(rows[0][0].strip()):
        return "dzieci-0-14-ofiary-wypadkow"
    if rows[0] and rows[0][0].strip().lower() == "rok":
        return "dzieci-0-14-ofiary-wypadkow"
    first_cells = [r[0].strip().lower() for r in rows if r and r[0].strip()]
    joined = " ".join(first_cells)
    if any(kw in joined for kw in MONTH_HEADER_KW):
        return "miesiace"
    if any(kw in joined for kw in CULPABILITY_KW):
        return "sprawstwo-wypadkow"
    if any(kw in joined for kw in PARTICIPANT_ROLE_KW):
        return "rodzaj-uzytkownika-drogi-ofiary"
    if any(kw in joined for kw in VOIV_KW):
        return "wojewodztwa"
    if any(kw in joined for kw in EVENT_KW):
        return "rodzaj-zdarzenia"
    if any(kw in joined for kw in ROAD_TYPE_KW):
        return "rodzaj-drogi"
    if len(first_cells) <= 3:
        return None  # genuinely looks like a small no-breakdown total -- leave as rok-ogolem
    return "UNCLEAR_TOTAL"  # content too garbled to identify -- flagged, not guessed


def refine_rok_ogolem(rows):
    refined = []
    counts = Counter()
    for folder_name, fname, topic, method, conf in rows:
        if topic != "rok-ogolem":
            refined.append((folder_name, fname, topic, method, conf))
            continue
        new_topic = sniff_content_topic(DATA / folder_name / "tables" / fname)
        if new_topic and new_topic != "UNCLEAR_TOTAL":
            refined.append((folder_name, fname, new_topic, "content-sniff", 1.0))
            counts["reclassified"] += 1
        elif new_topic == "UNCLEAR_TOTAL":
            refined.append((folder_name, fname, "UNCLEAR_TOTAL", "content-sniff-low", 0.0))
            counts["unclear"] += 1
        else:
            refined.append((folder_name, fname, topic, method, conf))
            counts["kept"] += 1
    print(f"rok-ogolem refinement: {dict(counts)}")
    return refined


def main():
    rows = build_crosswalk()
    rows = refine_rok_ogolem(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "crosswalk.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder", "filename", "topic", "method", "confidence"])
        for row in rows:
            w.writerow(row)

    unclassified = [r for r in rows if r[2] == "UNCLASSIFIED"]
    noise = [r for r in rows if r[2] == "NOISE"]
    print(f"{len(rows)} tables -> {path}")
    print(f"{len(noise)} noise, {len(unclassified)} unclassified")
    for r in unclassified:
        print("  UNCLASSIFIED:", r)


if __name__ == "__main__":
    main()

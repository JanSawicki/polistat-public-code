"""Aggregate the two remaining wybrane-statystyki subfolders that don't fit
any other group: zamachy-samobojcze (suicide attempts) and
zgony-z-powodu-wychlodz (hypothermia deaths).

zamachy-samobojcze: 3 non-overlapping period folders, each topic exported as
both .xls/.xlsx and .pdf of the same table. The xls/xlsx version is
preferred (parses more reliably, no header text mixed into rows); pdf is
only used when the xls is missing/broken. One confirmed broken case (see
doc/data.md): `zamachysamobojczesposobpopelnieniapowod19992012_xls` has no
`tables/` dir at all (download was skipped -- server served a truncated
file) -- this falls back to the pdf automatically because the xls source has
no CSVs to read. The pdf-derived extraction also yields generic
`page\\d+-table\\d+.csv` files alongside the real table; these are
extraction noise (split/repeated fragments) and are filtered out rather than
treated as extra topics.

Topic identity is mapped explicitly per period rather than by stripping
year suffixes from folder names, because the "zrodlo utrzymania" topic
genuinely changed definition in 2017 (re-emphasised from "przeszlosc karna"
to "stan zdrowia / kontakt z..." and gained a fatal/non-fatal split it didn't
have in 1999-2016) -- kept as a separate topic_key (`zrodlo_utrzymania_v2`)
rather than merged with the pre-2017 version, per CLAUDE.md's instruction to
propagate structural breaks as metadata rather than silently absorb them.

zgony-z-powodu-wychlodz: one file per winter season (not calendar year),
with inconsistent folder-name year formats (`sezon2017-18`,
`zasezon19-2020`, `zasezon20-2021`, `2025-26nastrone`, ...). The season is
parsed out by regex on the embedded year(s), normalizing 2-digit years to
4-digit by century rather than literal string matching on the folder name.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "wybrane-statystyki"

PAGE_TABLE_NOISE_RE = re.compile(r"^page\d+-table\d+\.csv$")

# folder base name (suffix _xls/_xlsx/_pdf stripped) -> (topic_key, fatal)
ZAMACHY_TOPIC_MAP = {
    "zamachysamobojczegrupawiekowadzientygodnia19992012": ("age_dzien_tygodnia", False),
    "zamachysamobojczemiejscapopelnienia19992012": ("miejsce_popelnienia", False),
    "zamachysamobojczesposobpopelnieniapowod19992012": ("sposob_powod", False),
    "zamachysamobojczestancywilnywyksztalcenieinfoopracanauka19992012": ("stan_cywilny_wyksztalcenie_praca_nauka", False),
    "zamachysamobojczezakonczonezgonemgrupawiekowadzientygodnia19992012": ("age_dzien_tygodnia", True),
    "zamachysamobojczezakonczonezgonemmiejscapopelnienia19992012": ("miejsce_popelnienia", True),
    "zamachysamobojczezakonczonezgonemsposobpopelnieniapowod19992012": ("sposob_powod", True),
    "zamachysamobojczezakonczonezgonemstancywilnywyksztalcenieinfoopracanauka19992012": ("stan_cywilny_wyksztalcenie_praca_nauka", True),
    "zamachysamobojczezrodloutrzymaniastanswiadomoscistanpsychicznyprzeszlosckarna199": ("zrodlo_utrzymania_v1", False),

    "zamachysamobojczegrupawiekowadzientygodnia20132016": ("age_dzien_tygodnia", False),
    "zamachysamobojczemiejscapopelnienia20132016": ("miejsce_popelnienia", False),
    "zamachysamobojczesposobpopelnieniapowod20132016": ("sposob_powod", False),
    "zamachysamobojczestancywilnywyksztalcenieinfoopracanauka20132016": ("stan_cywilny_wyksztalcenie_praca_nauka", False),
    "zamachysamobojczezakonczoneZGONEMgrupawiekowadzientygodnia20132016": ("age_dzien_tygodnia", True),
    "zamachysamobojczezakonczonezgonemmiejscapopelnienia20132016": ("miejsce_popelnienia", True),
    "zamachysamobojczezakonczonezgonemsposobpopelnieniapowod20132016": ("sposob_powod", True),
    "zamachysamobojczezakonczonezgonemstancywilnywyksztalcenieinfoopracanauka20132016": ("stan_cywilny_wyksztalcenie_praca_nauka", True),
    "zamachysamobojczezrodloutrzymaniastanswiadomoscistanpsychicznyprzeszlosckarna201": ("zrodlo_utrzymania_v1", False),

    "zamachysamobojczegrupawiekowadzientygodnia2017-2025": ("age_dzien_tygodnia", False),
    "zamachysamobojczemiejscapopelnienia2017-2025": ("miejsce_popelnienia", False),
    "zamachysamobojczesposobpopelnieniapowod2017-2025": ("sposob_powod", False),
    "zamachysamobojczestancywilnywyksztalcenieinfoopracanauka2017-2025": ("stan_cywilny_wyksztalcenie_praca_nauka", False),
    "zamachysamobojczezakonczoneZGONEMgrupawiekowadzientygodnia2017-2025": ("age_dzien_tygodnia", True),
    "zamachysamobojczezakonczoneZGONEMmiejscapopelnienia2017-2025": ("miejsce_popelnienia", True),
    "zamachysamobojczezakonczoneZGONEMsposobpopelnieniapowod2017-2025": ("sposob_powod", True),
    "zamachysamobojczezakonczoneZGONEMstancywilnywyksztpracanauka2017-2025": ("stan_cywilny_wyksztalcenie_praca_nauka", True),
    "zamachysamobojczezakonczoneZGONEMzrodutrzymstanswiadzdrowkont2017-2025": ("zrodlo_utrzymania_v2", True),
    "zamachysamobojczezrodloutrzymaniastanswiadomoscistanzdrowiakontaktz2017-2025": ("zrodlo_utrzymania_v2", False),
}

FORMAT_SUFFIXES = ("_xlsx", "_xls", "_pdf")


def parse_num(raw: str):
    s = raw.strip()
    if s in {"", "-", "brak"}:
        return None
    s = s.replace(" ", "").replace("\xa0", "")
    # thousands-grouped with "." (confirmed in zgony-z-powodu-wychlodz's
    # kategorie-zagrozen tables: 2.270 / 1.722 / 1.035 / 1.121 sit in an
    # otherwise-plain-integer column alongside unseparated values like 1324
    # and 1380 from other seasons -- not real 3-decimal-place fractions)
    if re.match(r"^-?\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")
    m = re.match(r"^-?\d+(?:\.0+)?$", s)
    if m:
        return str(int(float(s)))
    return s


def split_suffix(name: str):
    for suf in FORMAT_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)], suf[1:]
    return None, None


def real_tables(folder: Path):
    if not (folder / "tables").is_dir():
        return []
    return [t for t in sorted((folder / "tables").glob("*.csv")) if not PAGE_TABLE_NOISE_RE.match(t.name)]


def melt_wide_table(table: Path, topic, fatal, period_label, rows_out):
    with table.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    for row in rows[1:]:
        if not row or not re.match(r"^\d+(\.0+)?$", row[0].strip()):
            continue
        year = int(float(row[0].strip()))
        unit = row[1].strip()
        for col_name, raw in zip(header[2:], row[2:]):
            rows_out.append((period_label, topic, fatal, year, unit, col_name, parse_num(raw),
                              str(table.relative_to(ROOT))))


def zamachy_samobojcze(rows_out):
    base = DATA / "zamachy-samobojcze"
    for period_dir in sorted(base.iterdir()):
        if not period_dir.is_dir():
            continue
        period_label = period_dir.name
        grouped = {}
        for child in period_dir.iterdir():
            if not child.is_dir() or "uwagiiwyjasnienia" in child.name:
                continue
            folder_base, fmt = split_suffix(child.name)
            grouped.setdefault(folder_base, {})[fmt] = child

        for folder_base, formats in grouped.items():
            topic, fatal = ZAMACHY_TOPIC_MAP[folder_base]
            source = formats.get("xlsx") or formats.get("xls")
            tables = real_tables(source) if source else []
            if not tables:
                source = formats["pdf"]
                tables = real_tables(source)
            assert tables, f"no usable table for {folder_base} in {period_dir}"
            for table in tables:
                melt_wide_table(table, topic, fatal, period_label, rows_out)


SEASON_RE = re.compile(r"(\d{2,4})-(\d{2,4})")


def normalize_year(s: str) -> int:
    n = int(s)
    return 2000 + n if n < 100 else n


def zgony_wychlodzenie(rows_out):
    base = DATA / "zgony-z-powodu-wychlodz"
    for folder in sorted(base.iterdir()):
        if not folder.is_dir():
            continue
        m = SEASON_RE.search(folder.name)
        a, b = normalize_year(m.group(1)), normalize_year(m.group(2))
        season_start_year = min(a, b)
        for table in real_tables(folder):
            dataset = table.stem
            with table.open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            header = rows[0]
            for row in rows[1:]:
                month_label = row[0].strip()
                if not month_label:
                    continue
                for col_name, raw in zip(header[1:], row[1:]):
                    rows_out.append((season_start_year, dataset, month_label, col_name.strip(),
                                      parse_num(raw), str(table.relative_to(ROOT))))


def main():
    zamachy_rows = []
    zamachy_samobojcze(zamachy_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "zamachy-samobojcze.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["period", "topic", "fatal", "year", "unit", "metric", "value", "source_file"])
        for row in zamachy_rows:
            w.writerow(["" if v is None else v for v in row])
    print(f"{len(zamachy_rows)} rows -> {path}")

    wychlodz_rows = []
    zgony_wychlodzenie(wychlodz_rows)
    path = OUT / "zgony-z-powodu-wychlodzenia.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["season_start_year", "dataset", "month", "metric", "value", "source_file"])
        for row in wychlodz_rows:
            w.writerow(["" if v is None else v for v in row])
    print(f"{len(wychlodz_rows)} rows -> {path}")


if __name__ == "__main__":
    main()

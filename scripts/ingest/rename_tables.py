"""One-off script: give descriptive names to tables/*.csv files under data/.

Reads each leaf folder's content.md to recover context (sheet names, explicit
PDF list headings) and otherwise derives a title straight from the CSV's own
header/title row. Renames the CSV files in place and rewrites content.md's
tables/<name>.csv references to match. Where no reliable title can be found,
the original filename is kept rather than guessing.
"""
import csv
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "statystyka-policja"

TRANSLIT = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s",
    "ź": "z", "ż": "z", "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
    "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z",
})

GENERIC_SHEET_NAMES = {"arkusz1", "arkusz", "sheet", "sheet1", "ustawa", "zamachysamobojcze"}
HEADER_MARKER_WORDS = {"rok", "lp", "lp.", "wyszczególnienie", "miesiąc", "miesiace"}

TABLE_MARKER_RE = re.compile(
    r'\(Table extracted from page (\d+) -> tables/([^,]+?\.csv), \d+ rows\)'
)
LIST_MARKER_RE = re.compile(
    r'\(List(?: "([^"]*)")? extracted from page (\d+) -> tables/([^,]+?\.csv), \d+ items\)'
)
SHEET_RE = re.compile(r'^## Sheet: (.+?) \(\d+ rows\)$', re.MULTILINE)


def slugify(text: str, maxlen: int = 70) -> str:
    text = text.translate(TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    if len(text) > maxlen:
        cut = text[:maxlen].rsplit("-", 1)[0]
        text = cut if len(cut) > 10 else text[:maxlen]
    return text


def _read_rows(csv_path: Path, n: int = 6) -> list[list[str]]:
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                if i >= n:
                    break
                rows.append([(c or "").strip() for c in row])
            return rows
    except Exception:
        return []


def header_row_title(csv_path: Path) -> str | None:
    rows = _read_rows(csv_path)
    if not rows:
        return None

    # 1. A row with exactly one non-empty cell that reads as a full title
    #    (e.g. "Rozbój, kradzież rozbójnicza... - przestępstwa stwierdzone...").
    for row in rows:
        nonempty = [c for c in row if c]
        if len(nonempty) == 1 and len(nonempty[0]) >= 25 and len(nonempty[0].split()) >= 3:
            return nonempty[0]

    # 2. A genuine header row: >=2 non-empty cells, not a data row (first cell
    #    isn't a bare 4-digit year), containing a known header marker or
    #    mostly non-numeric content.
    for row in rows:
        nonempty = [c for c in row if c]
        if len(nonempty) < 2:
            continue
        if re.fullmatch(r"\d{4}", row[0] if row else ""):
            continue
        has_marker = any(c.lower().rstrip(".") in HEADER_MARKER_WORDS for c in nonempty)
        numeric_cells = sum(1 for c in nonempty if re.fullmatch(r"[\d\s.,%-]+", c))
        if has_marker or numeric_cells <= len(nonempty) // 2:
            parts = [c for c in nonempty if c.lower().rstrip(".") not in HEADER_MARKER_WORDS]
            if parts:
                return " - ".join(dict.fromkeys(parts))

    # 3. Any single informative cell, lower bar.
    for row in rows[:3]:
        if row and re.fullmatch(r"\d{4}", row[0]):
            continue
        for cell in row:
            if len(cell) >= 4 and re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3,}", cell):
                if cell.lower().rstrip(".") not in HEADER_MARKER_WORDS and not re.fullmatch(r"\d{4}", cell):
                    return cell
    return None


def build_renames_for_folder(folder: Path) -> dict[str, str]:
    content_path = folder / "content.md"
    tables_dir = folder / "tables"
    if not tables_dir.is_dir():
        return {}
    csv_files = sorted(tables_dir.glob("*.csv"))
    if not csv_files:
        return {}

    text = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    proposals: dict[str, str] = {}

    # 1. Sheet-based (xlsx/xls).
    for m in SHEET_RE.finditer(text):
        sheet = m.group(1)
        sheet_slug_guess = re.sub(r"[^A-Za-z0-9_\-]+", "_", sheet.strip()) or "sheet"
        fn = f"{sheet_slug_guess}.csv"
        if not (tables_dir / fn).exists():
            continue
        if sheet.strip().lower() in GENERIC_SHEET_NAMES or re.fullmatch(r"\d+", sheet.strip()):
            title = header_row_title(tables_dir / fn)
            if title:
                proposals[fn] = title
        else:
            proposals[fn] = sheet

    # 2. PDF list/table markers.
    for line in text.splitlines():
        line = line.strip()
        list_m = LIST_MARKER_RE.match(line)
        if list_m:
            heading, _page, fn = list_m.groups()
            title = heading.strip() if heading and heading.strip() else header_row_title(tables_dir / fn)
            if title:
                proposals.setdefault(fn, title)
            continue
        table_m = TABLE_MARKER_RE.match(line)
        if table_m:
            _page, fn = table_m.groups()
            title = header_row_title(tables_dir / fn)
            if title:
                proposals.setdefault(fn, title)

    # 3. Slugify + dedupe within this folder.
    renames: dict[str, str] = {}
    used: set[str] = set()
    for f in csv_files:
        old_name = f.name
        title = proposals.get(old_name)
        if not title:
            continue
        slug = slugify(title)
        if not slug or slug == old_name[:-4]:
            continue
        candidate = slug
        i = 2
        while candidate in used or (candidate + ".csv") == old_name:
            candidate = f"{slug}-{i}"
            i += 1
        used.add(candidate)
        renames[old_name] = candidate + ".csv"
    return renames


def apply_renames(folder: Path, renames: dict[str, str]):
    tables_dir = folder / "tables"
    content_path = folder / "content.md"
    text = content_path.read_text(encoding="utf-8") if content_path.exists() else None

    for old, new in renames.items():
        src = tables_dir / old
        dst = tables_dir / new
        if not src.exists() or dst.exists():
            continue
        src.rename(dst)
        if text is not None:
            text = text.replace(f"tables/{old}", f"tables/{new}")

    if text is not None:
        content_path.write_text(text, encoding="utf-8")


def main():
    dry_run = "--dry-run" in sys.argv
    total_renamed = 0
    total_folders = 0
    total_csv = 0
    for tables_dir in sorted(DATA.rglob("tables")):
        if not tables_dir.is_dir():
            continue
        folder = tables_dir.parent
        total_csv += len(list(tables_dir.glob("*.csv")))
        renames = build_renames_for_folder(folder)
        if not renames:
            continue
        total_folders += 1
        total_renamed += len(renames)
        if dry_run:
            for old, new in renames.items():
                print(f"{folder.relative_to(DATA)}: {old} -> {new}")
        else:
            apply_renames(folder, renames)
    print(f"\n{total_renamed}/{total_csv} files renamed across {total_folders} folders.", file=sys.stderr)


if __name__ == "__main__":
    main()

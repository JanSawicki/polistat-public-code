"""Recover data/ruch-drogowy/Wypadki1975-2011_doc/Wypadki1975-2011.doc.

It's a legacy binary (pre-OOXML) .doc -- no docx/olefile/libreoffice/antiword
is available in this environment and there's no network access to install
one. `strings -e l` (the doc's text is stored as little-endian UTF-16)
recovers the document's text cleanly because the file is tiny (31KB, one
table, no embedded objects) -- confirmed by inspecting the raw output: the
table header ("Wypadki", "Zabici", "Ranni", "Kolizje") and all 37 yearly
rows come through intact and in order.

The table has 3 columns (Wypadki/accidents, Zabici/killed, Ranni/injured)
for every year 1975-2011, plus a 4th column (Kolizje/collisions) that's
genuinely absent 1975-1989 (collision counts weren't tracked yet) and also
missing for 1997-1998 specifically (a real gap in the source, not a parsing
artifact -- the surrounding years 1996 and 1999 both have a 4th value, only
1997/1998 don't).
"""
import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DOC = ROOT / "data" / "statystyka-policja" / "ruch-drogowy" / "Wypadki1975-2011_doc" / "Wypadki1975-2011.doc"
OUT = ROOT / "data-aggregated" / "statystyka-policja" / "ruch-drogowy"

METRICS = ["wypadki", "zabici", "ranni", "kolizje"]


def main():
    text = subprocess.run(["strings", "-e", "l", str(DOC)], capture_output=True, text=True, check=True).stdout
    lines = [l.strip() for l in text.splitlines()]

    start = lines.index("Kolizje") + 1
    end = lines.index("Root Entry", start)
    body = [l for l in lines[start:end] if l != ""]

    rows = []
    i = 0
    while i < len(body):
        year = int(body[i])
        i += 1
        values = []
        while i < len(body) and not re.match(r"^(19|20)\d{2}$", body[i]):
            values.append(int(body[i].replace(" ", "").replace("\xa0", "")))
            i += 1
        for metric, value in zip(METRICS, values):
            rows.append((year, metric, value))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "wypadki-1975-2011-legacy.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "metric", "value", "source_file"])
        for year, metric, value in rows:
            w.writerow([year, metric, value, "data/statystyka-policja/ruch-drogowy/Wypadki1975-2011_doc/Wypadki1975-2011.doc"])
    print(f"{len(rows)} rows across {1 + (rows[-1][0] - rows[0][0])} years -> {path}")


if __name__ == "__main__":
    main()

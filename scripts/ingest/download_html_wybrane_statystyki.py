"""Download the two `wybrane-statystyki` pages that, unlike every other source
in this category, have no downloadable XLSX/PDF files at all -- the data only
exists as an inline HTML table on the page itself (see `doc/data.md`'s note
and `site-map/wybrane-statystyki/maloletni-pod-wplywem.md` /
`nietrzezwi-podejrzani-o-popeln.md`). `build_manifest.py`'s crawl only
captures bullets ending in a downloadable-file extension, so these two pages
were never picked up by the regular `download_and_parse.py` pipeline.

One-time, two-page archival -- not part of the recurring pipeline. Run to
(re)populate data/statystyka-policja/wybrane-statystyki/{topic}/page.html,
which scripts/aggregate/wybrane_statystyki_html.py reads.
"""
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "statystyka-policja" / "wybrane-statystyki"
HEADERS = {"User-Agent": "Mozilla/5.0 (research data collection; polistats project)"}

PAGES = [
    ("maloletni-pod-wplywem", "https://statystyka.policja.pl/st/wybrane-statystyki/maloletni-pod-wplywem"),
    ("nietrzezwi-podejrzani-o-popeln", "https://statystyka.policja.pl/st/wybrane-statystyki/nietrzezwi-podejrzani-o-popeln"),
]


def main():
    for topic, url in PAGES:
        dest_dir = OUT / topic
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "page.html"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        dest.write_bytes(data)
        print(f"{topic}: {len(data)} bytes -> {dest}")


if __name__ == "__main__":
    main()

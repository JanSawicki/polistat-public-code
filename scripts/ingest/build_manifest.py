import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SITEMAP = ROOT / "site-map"

EXCLUDE_DIRS = {"raporty", "opinia-publiczna"}
EXCLUDE_FILES = {SITEMAP / "wybrane-statystyki" / "dyscyplina-w-policji.md"}

PAGE_RE = re.compile(r'(?:Page URL|Redirects to):\s*(https?://\S+)')
BULLET_RE = re.compile(r'^[-#]*\s*-\s+(?P<label>.+?)\s+—\s+(?P<url>https?://\S+\.(?:xlsx|xls|pdf|docx?|csv))\s*$')

def slugify(s):
    s = s.lower()
    s = re.sub(r'[^a-z0-9.]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s

entries = []
seen_urls = set()

for md in sorted(SITEMAP.rglob("*.md")):
    rel = md.relative_to(SITEMAP)
    if rel.name == "README.md":
        continue
    top = rel.parts[0]
    if top in EXCLUDE_DIRS:
        continue
    if md in EXCLUDE_FILES:
        continue
    category = str(rel.parent) if str(rel.parent) != "." else top

    current_page = None
    text = md.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = PAGE_RE.search(line)
        if m:
            current_page = m.group(1)
            continue
        m = BULLET_RE.match(line)
        if m:
            url = m.group("url")
            label = m.group("label")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            filename = url.rsplit("/", 1)[-1]
            entries.append({
                "category": category,
                "source_md": str(rel),
                "page_url": current_page,
                "label": label,
                "url": url,
                "filename": filename,
            })

print(f"Found {len(entries)} unique download entries", file=sys.stderr)
(ROOT / "scripts" / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

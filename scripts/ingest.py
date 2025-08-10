# -*- coding: utf-8 -*-
import os, json, hashlib, datetime, re, pathlib, yaml, time, urllib.parse, unicodedata
import feedparser, trafilatura, requests
from bs4 import BeautifulSoup

# Detecta la raíz del repo (GitHub Actions expone GITHUB_WORKSPACE)
REPO_ROOT = pathlib.Path(os.getenv("GITHUB_WORKSPACE") or pathlib.Path(__file__).resolve().parents[1]).resolve()
CONTENT = REPO_ROOT / "src" / "content" / "blog"
DATA = REPO_ROOT / "data"; DATA.mkdir(parents=True, exist_ok=True)
SEEN = DATA / "seen.json"
FEEDS_FILE = DATA / "feeds.txt"

print("USANDO RAIZ:", REPO_ROOT)
print("CONTENIDO EN:", CONTENT)
print("FEEDS_FILE:", FEEDS_FILE)


MAX_NEW = int(os.getenv("MAX_NEW", "8"))
TIMEOUT = 15

seen = set()
if SEEN.exists():
    try:
        seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
    except Exception:
        seen = set()

def slugify(s):
    # quita acentos/diacríticos (á -> a, ñ -> n)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # deja letras, números, espacio y guion
    s = re.sub(r"[^a-zA-Z0-9\- ]+", "", s)
    s = s.strip().lower().replace(" ", "-")
    return re.sub(r"-+", "-", s)[:90]

def h(text): return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

def get_html(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200: return r.text
    except Exception:
        return None

def discover_feed(url):
    if any(x in url for x in ["/feed", ".xml", "rss", "atom"]):
        return url
    html = get_html(url)
    if not html: return None
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
        t = (link.get("type") or "").lower()
        if "rss" in t or "atom" in t or "xml" in t:
            href = link.get("href")
            if not href: continue
            return urllib.parse.urljoin(url, href)
    return None

def extract_article(url):
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        text = trafilatura.extract(downloaded) or ""
        return text.strip()
    except Exception:
        return ""

def parse_feed(feed_url, limit=8):
    items = []
    d = feedparser.parse(feed_url)
    for e in d.entries[:limit]:
        title = (e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title or not link: continue
        items.append((title, link))
    return items

def discover_articles_from_home(home_url, limit=5):
    html = get_html(home_url)
    if not html: return []
    base = urllib.parse.urlparse(home_url).netloc
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(home_url, a["href"])
        u = urllib.parse.urlparse(href)
        if u.netloc != base: continue
        if any(seg in href.lower() for seg in ["econom", "finan", "negocio", "notic", "colombia"]):
            title = a.get_text(strip=True)[:120] or u.path
            links.append((title, href))
        if len(links) >= limit: break
    return links

def write_md(title, link, body):
    today = datetime.date.today().isoformat()
    slug = f"{today}-{slugify(title)}"
    description = (body[:300] + "...") if len(body) > 300 else body
    fm = {
        "title": title,
        "description": description or "Resumen pendiente.",
        "pubDate": today,
        "tags": ["pagos","LATAM"],
        "status": "draft",
        "risk": "bajo",
        "action": "Evaluar impacto en comisiones/operación.",
        "sources": [{"name": "Fuente", "url": link}],
    }
    CONTENT.mkdir(parents=True, exist_ok=True)
    md = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + (body or "Contenido por revisar.")
    (CONTENT / f"{slug}.md").write_text(md, encoding="utf-8")

def run():
    urls = [u.strip() for u in FEEDS_FILE.read_text(encoding="utf-8").splitlines() if u.strip() and not u.strip().startswith("#")]
    new_items = 0
    for url in urls:
        if new_items >= MAX_NEW: break
        feed = discover_feed(url)
        candidates = parse_feed(feed, limit=6) if feed else discover_articles_from_home(url, limit=4)
        for title, link in candidates:
            if new_items >= MAX_NEW: break
            key = h(title + link)
            if key in seen: continue
            body = extract_article(link)
            if not body or len(body) < 300:
                continue
            write_md(title, link, body)
            seen.add(key)
            new_items += 1
            time.sleep(1)
    SEEN.write_text(json.dumps(sorted(list(seen))), encoding="utf-8")
    print(f"Drafts creados: {new_items}")

if __name__ == "__main__":
    run()

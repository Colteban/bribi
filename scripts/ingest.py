# -*- coding: utf-8 -*-
import os, json, hashlib, datetime, re, pathlib, yaml, time, urllib.parse, unicodedata
import feedparser, trafilatura, requests
from bs4 import BeautifulSoup

import google.generativeai as genai
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    GEMINI_MODEL = genai.GenerativeModel("gemini-1.5-flash")
else:
    GEMINI_MODEL = None


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

# extraer canonical y og:image
def extract_meta(url, html):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v.lower())
    ogimg = soup.find("meta", property="og:image")
    return (
        canonical.get("href") if canonical else url,
        ogimg.get("content") if ogimg else ""
    )


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
    s = re.sub(r"[^a-zA-Z0-9\- ]+", "", s)
    s = s.strip().lower().replace(" ", "-")
    return re.sub(r"-+", "-", s)[:90]

SEP_PAT = re.compile(r"\s*(\||-|—|–|·|•|:|::)\s*")

def clean_title(raw):
    if not raw:
        return "Actualización"
    # corta en separadores comunes y quédate con la parte más “noticiosa”
    parts = SEP_PAT.split(raw)
    if len(parts) >= 3:
        raw = parts[0]
    # quita espacios raros y dupes
    raw = re.sub(r"\s+", " ", raw).strip()
    # capitaliza solo la primera letra (evita gritos)
    return raw[:140].strip().capitalize()

# Frases/fragmentos a eliminar (en minúsculas)
STOP_PHRASES = [
    # botones/ruido
    "compartir el código iframe se ha copiado en el portapapeles",
    "publicidad", "anuncio", "síguenos en", "newsletter", "suscríbete", "suscribete",
    "le puede interesar", "también le puede interesar", "te puede interesar",
    "haz clic aquí", "haga clic aquí", "ver más", "ver mas", "leer más", "leer mas",
    "continúe leyendo", "política de tratamiento de datos", "términos y condiciones",
    "comentarios", "deja tu comentario",
]

# Patrones que suelen ser LICENCIAS/DISCLAIMERS (bloques enteros)
STOP_REGEXES = [
    r"creative\s*commons", r"\bcc\s*by(-| )?nc(-| )?sa\b", r"licencia\s+internacional",
    r"esta\s+revista\s+est[áa]\s+autorizada", r"el\s+contenido\s+de\s+los\s+art[íi]culos\s+es\s+responsabilidad",
    r"no\s+puede\s+ser\s+utilizada\s+con\s+fines\s+comerciales",
]
CUT_AFTER_MARKERS = [
    "referencias", "bibliografía", "bibliografia", "licencia", "copyright",
    "creative commons", "nota del editor", "créditos", "creditos",
]

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    t = txt

    # Normaliza puntos suspensivos y espacios
    t = t.replace("…", "...")
    t = re.sub(r"\.{3,}", ".", t)               # "....." -> "."
    t = re.sub(r"[ \t]+", " ", t)               # espacios repetidos
    t = re.sub(r"\n{3,}", "\n\n", t)            # saltos excesivos

    # Parte en líneas, filtra ruido y LICENCIAS
    out, seen = [], set()
    for raw_ln in t.splitlines():
        ln = raw_ln.strip()
        if not ln or len(ln) < 3:
            continue
        low = ln.lower()

        # corta si aparece un marcador de "fin útil" (referencias/licencia/etc.)
        if any(m in low for m in CUT_AFTER_MARKERS):
            break

        # descarta líneas con frases basura o licencias/disclaimers
        if any(p in low for p in STOP_PHRASES):
            continue
        if any(re.search(rx, low) for rx in STOP_REGEXES):
            continue

        # evita párrafos duplicados exactos (y citas repetidas)
        norm = re.sub(r'["“”«»]+', "", low)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(ln)

    body = "\n\n".join(out).strip()

    # Limpieza final: más de 2 puntos seguidos otra vez por si quedaron
    body = re.sub(r"\.{3,}", ".", body)
    return body



def h(text): return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

def get_html(url):
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            return r.text, r.url
    except Exception:
        return None, url
    return None, url


def discover_feed(url):
    # si ya parece feed, úsalo tal cual
    if any(x in url for x in ["/feed", ".xml", "rss", "atom"]):
        return url
    html, final_url = get_html(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in v.lower()):
        t = (link.get("type") or "").lower()
        if any(k in t for k in ("rss", "atom", "xml")):
            href = link.get("href")
            if not href:
                continue
            return urllib.parse.urljoin(final_url, href)
    return None


def extract_article(url):
    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=True)
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            target_language="es",
        ) or ""
        return text.strip()
    except Exception:
        return ""

def summarize_with_gemini(text, url):
    """
    Devuelve dict con: {title, summary, article} o None si falla.
    - title: 6–12 palabras, sin el nombre del medio.
    - summary: 150–250 palabras (3–4 frases), claro y accionable.
    - article: 300–600 palabras; termina con bloque "Qué vigilar" (3 bullets).
    """
    if not GEMINI_MODEL or not text:
        return None

    prompt = f"""
Eres editor económico para comercios en Colombia/LATAM.
Reescribe SIN copiar textual. Tono neutro. Entrega JSON: title, summary, article.

Reglas:
- No incluyas licencias, avisos legales, ni disclaimers editoriales (Creative Commons, etc.).
- Evita citas largas; máximo una breve si aporta valor.
- Puedes mencionar fuentes, de forma conversacional y periodística, profesional y a la vez fluida.
- Título: 6–12 palabras, sin “| Nombre del medio”.
- Summary: 150–250 palabras, 3–4 frases, con implicaciones prácticas.
- Article: 300–600 palabras, 4–7 párrafos, cierra con bloque:
  "Qué vigilar" (3 bullets accionables).
- No inventes datos; si falta info, dilo sin suponer.
- Puedes mencionar fuentes, de forma conversacional y periodística, profesional y a la vez fluida.
- Español (Colombia). Fuente: {url}

TEXTO LIMPIO (parcial si es largo):
{text[:9000]}
"""
    try:
        r = GEMINI_MODEL.generate_content(prompt)
        raw = (r.text or "").strip()
        import json
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j != -1:
            data = json.loads(raw[i:j+1])
            t = (data.get("title") or "").strip()
            s = (data.get("summary") or "").strip()
            a = (data.get("article") or "").strip()
            # mínimos de calidad
            if len(s) >= 140 and len(a) >= 300:
                return {"title": t, "summary": s, "article": a}
    except Exception:
        return None
    return None

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
    html, final_url = get_html(home_url)
    if not html:
        return []
    base = urllib.parse.urlparse(final_url).netloc
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(final_url, a["href"])
        u = urllib.parse.urlparse(href)
        if u.netloc != base:
            continue
        if any(seg in href.lower() for seg in ["econom", "finan", "negocio", "notic", "colombia"]):
            title = a.get_text(strip=True)[:120] or u.path
            links.append((title, href))
        if len(links) >= limit:
            break
    return links


def write_md(title, link, body, og_image="", ai=None, status="draft"):
    today = datetime.date.today().isoformat()

    # Si hay IA, úsala; si no, usa lo que ya tenías
    title = clean_title(ai["title"] if ai and ai.get("title") else title)
    if ai and ai.get("summary"):
        summary = ai["summary"].strip()
    else:
        trimmed = body[:300]
        trimmed = re.sub(r"\s+\S*$", "", trimmed)   # corta en el límite de palabra
        summary = trimmed if len(body) <= 300 else trimmed + "…"   # solo UNA elipsis
    article_md = ai["article"] if ai and ai.get("article") else body

    # mínimo de calidad
    if not article_md or len(article_md) < 200:
        return
    base_slug = slugify(title)
    slug = f"{today}-{base_slug}"
    # si existe archivo con mismo nombre, agrega hash corto
    p = CONTENT / f"{slug}.md"
    if p.exists():
        slug = f"{today}-{base_slug}-{h(title)[:6]}"
        p = CONTENT / f"{slug}.md"

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
    if og_image:
        fm["image"] = {"src": og_image, "alt": title}

    CONTENT.mkdir(parents=True, exist_ok=True)
    md = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + article_md.strip()
    p.write_text(md, encoding="utf-8")

def run():
    urls = [u.strip() for u in FEEDS_FILE.read_text(encoding="utf-8").splitlines() if u.strip() and not u.strip().startswith("#")]
    new_items = 0
    for url in urls:
        if new_items >= MAX_NEW: break
        feed = discover_feed(url)
        candidates = parse_feed(feed, limit=6) if feed else discover_articles_from_home(url, limit=4)
        
        
        for title, link in candidates:
            if new_items >= MAX_NEW: 
                break
            html, final_url = get_html(link)
            canon = final_url
            og_image = ""

            if html:
                canon, og_image = extract_meta(final_url, html)
                canon = urllib.parse.urljoin(final_url, canon) if canon else final_url
                if og_image:
                    og_image = urllib.parse.urljoin(final_url, og_image)

            key = h(title + canon)
            if key in seen:
                continue


            source_url = canon or final_url
            raw = extract_article(source_url)   # texto crudo
            body = clean_text(raw)              # 👈 LIMPIA AQUÍ
            if not body or len(body) < 200:
                continue

            ai = summarize_with_gemini(body, source_url)  # la IA ya recibe texto limpio
            write_md(title, source_url, body, og_image=og_image, ai=ai, status="draft")
            seen.add(key)
            new_items += 1
            time.sleep(1)

    SEEN.write_text(json.dumps(sorted(list(seen))), encoding="utf-8")
    print(f"Drafts creados: {new_items}")

if __name__ == "__main__":
    run()

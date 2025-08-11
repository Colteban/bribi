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

SEP_PAT = re.compile(r"\s*(\||-|—|–|·|•|:|::)\s*")

def clean_title(raw):
    if not raw:
        return "Actualización"
    # quita prefijos tipo OPINIÓN:
    raw = re.sub(r"^\s*opini[oó]n\s*:\s*", "", raw, flags=re.I)
    parts = SEP_PAT.split(raw)
    if len(parts) >= 3:
        raw = parts[0]
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:140].strip().capitalize()


# Frases/fragmentos a eliminar (en minúsculas)
# ===== Limpieza potente =====
MONTHS = r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)"

STOP_PHRASES = [
    "compartir el código iframe se ha copiado en el portapapeles",
    "publicidad", "anuncio", "síguenos en", "newsletter", "suscríbete", "suscribete",
    "le puede interesar", "también le puede interesar", "te puede interesar",
    "haz clic aquí", "haga clic aquí", "ver más", "ver mas", "leer más", "leer mas",
    "continúe leyendo", "política de tratamiento de datos", "términos y condiciones",
    "comentarios", "deja tu comentario", "menú", "buscar"
]

STOP_REGEXES = [
    r"\bcreative\s*commons\b", r"\bcc\s*by(-| )?nc(-| )?sa\b", r"\blicencia\b",
    r"esta\s+revista\s+est[áa]\s+autorizada",
    r"el\s+contenido\s+de\s+los\s+art[íi]culos\s+es\s+responsabilidad",
    r"no\s+puede\s+ser\s+utilizada\s+con\s+fines\s+comerciales",
    rf"^\s*(lun(es)?|mar(tes)?|mi[eé]rcoles|jue(ves)?|vie(rnes)?|s[áa]b(ado)?|dom(ingo)?)\s*,?\s*\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}",
    rf"^\s*\d{{1,2}}\s+de\s+{MONTHS}\s+de\s+\d{{4}}\s*$",
    r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$",
    r"^\s*\d{1,2}:\d{2}\s*$",
    r"^\s*lo\s+m[aá]s\s+visto\s*$",
]

CUT_AFTER_MARKERS = [
    "lo más visto", "referencias", "bibliografía", "bibliografia", "licencia",
    "copyright", "nota del editor", "créditos", "creditos"
]

def _looks_like_js(line: str) -> bool:
    l = line.strip()
    if l.startswith(("//", "/*", "*")): return True
    if "$(" in l or "function(" in l or "var " in l or "let " in l or "const " in l: return True
    if "</script>" in l.lower() or "<script" in l.lower(): return True
    symbols = sum(ch in "{}[]();$<>=*#|%\\" for ch in l)
    return symbols > max(6, len(l)//6)

def clean_text(txt: str) -> str:
    if not txt: return ""
    t = unicodedata.normalize("NFKC", txt)
    t = t.replace("…", "...")
    t = re.sub(r"\.{3,}", ".", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)

    out, seen = [], set()
    cutting = False
    for raw in t.splitlines():
        ln = raw.strip()
        if not ln or len(ln) < 3: 
            continue
        low = ln.lower()

        if cutting:
            continue
        if any(m in low for m in CUT_AFTER_MARKERS):
            cutting = True
            continue

        if _looks_like_js(ln): 
            continue
        if any(p in low for p in STOP_PHRASES): 
            continue
        if any(re.search(rx, low) for rx in STOP_REGEXES): 
            continue

        norm = re.sub(r'[“”"«»]+', "", low)
        if norm in seen: 
            continue
        seen.add(norm)
        out.append(ln)

    body = "\n\n".join(out).strip()
    body = re.sub(r"\.{3,}", ".", body)
    return body

def looks_like_directory(text: str) -> bool:
    """True si parece listado/índice (no artículo)."""
    if not text: 
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3: 
        return True
    short = sum(1 for ln in lines if len(ln) < 60 and not ln.endswith("."))
    if short / max(1, len(lines)) > 0.6: 
        return True
    verbs = [" es ", " son ", " fue ", " fueron ", " tiene ", " tienen ", " anunció", " anuncia", " publicó", " regula", " aprueba", " modifica"]
    if sum(1 for v in verbs if v in " " + text.lower() + " ") < 1: 
        return True
    return False


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
    Devuelve {title, summary, article} o None.
    """
    if not GEMINI_MODEL or not text:
        return None

    prompt = f"""
Eres editor económico para comercios en Colombia/LATAM.
Reescribe SIN copiar textual. Tono neutro. Entrega JSON: title, summary, article.

Reglas:
- No incluyas licencias, avisos legales, ni disclaimers editoriales (Creative Commons, etc.).
- No pongas fechas/horas en el título ni en el summary (ej.: 'Domingo, 10.08.2025/21:36').
- Evita 'OPINIÓN:' en el título; si es opinión, ajusta a informativo neutral.
- Evita citas largas; máximo una breve si aporta valor.
- Puedes mencionar fuentes, de forma conversacional, parafraseada y periodística, profesional y a la vez fluida.
- Título: 6–12 palabras, sin “| Nombre del medio”.
- Summary: 150–250 palabras, 3–4 frases, con implicaciones prácticas.
- Article: 300–600 palabras, 4–7 párrafos; cierra con:
  "Qué vigilar" (3 bullets accionables).
- No inventes datos; si falta, dilo sin suponer.
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
            if len(s) >= 140 and len(a) >= 300:
                return {"title": t, "summary": s, "article": a}
    except Exception:
        return None
    return None

def guess_tags_from_url(url: str) -> list:
    u = url.lower()
    tags = set()
    if "ambitojuridico" in u:
        tags.add("normativa")
        if "tipo-civil" in u: tags.update(["civil"])
        if "derecho-mercantil" in u or "comercial" in u: tags.update(["comercial","pyme"])
    if "camaramedellin" in u: tags.update(["pyme","empresas"])
    if "banrep" in u or "minhacienda" in u: tags.add("economía")
    if "pagos" in u or "pos" in u or "billetera" in u: tags.add("pagos")
    return list(tags) or ["economía"]


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

    title = clean_title(ai["title"] if ai and ai.get("title") else title)

    # summary limpio (sin cortar palabras y sin "..." extra)
    if ai and ai.get("summary"):
        summary = ai["summary"].strip()
    else:
        trimmed = body[:300]
        trimmed = re.sub(r"\s+\S*$", "", trimmed)   # corta en palabra
        summary = trimmed if len(body) <= 300 else trimmed + "…"  # una sola elipsis

    article_md = ai["article"] if ai and ai.get("article") else body
    if not article_md or len(article_md) < 200:
        return

    base_slug = slugify(title)
    slug = f"{today}-{base_slug}"
    p = CONTENT / f"{slug}.md"
    if p.exists():
        slug = f"{today}-{base_slug}-{h(title)[:6]}"
        p = CONTENT / f"{slug}.md"

    auto_tags = guess_tags_from_url(link)

    fm = {
        "title": title,
        "description": summary,
        "pubDate": today,
        "tags": auto_tags,
        "status": status,
        "risk": "bajo",
        "action": "Evaluar impacto en comisiones/operación.",
        "sources": [{"name": "Fuente", "url": link}],
    }
    if og_image:
        fm["image"] = {"src": og_image, "alt": title}

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
            body = clean_text(raw)              # LIMPIEZA ANTES DE IA
            if not body or len(body) < 200:
                continue
            if looks_like_directory(body):      # SALTA índices/listados
                continue

            ai = summarize_with_gemini(body, source_url)  # IA sobre texto limpio
            write_md(title, source_url, body, og_image=og_image, ai=ai, status="draft")

            seen.add(key)
            new_items += 1
            time.sleep(1)

    SEEN.write_text(json.dumps(sorted(list(seen))), encoding="utf-8")
    print(f"Drafts creados: {new_items}")

if __name__ == "__main__":
    run()

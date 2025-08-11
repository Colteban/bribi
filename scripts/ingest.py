# -*- coding: utf-8 -*-
import os, json, hashlib, datetime, re, pathlib, yaml, time, urllib.parse, unicodedata
import feedparser, trafilatura, requests
from collections import defaultdict
from bs4 import BeautifulSoup

import google.generativeai as genai
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    GEMINI_MODEL = genai.GenerativeModel("gemini-1.5-flash")
else:
    GEMINI_MODEL = None

GEMINI_BUDGET = int(os.getenv("GEMINI_BUDGET", "12"))  # máx. llamados IA por corrida (puedes poner 10 si quieres)



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

STYLES = {
    # Estilo crítico/organizado para top CO y WORLD y silencios:
    "critico": (
        "Organiza y narra cronológica o temáticamente. "
        "Incluye análisis crítico (causas, efectos, riesgos, oportunidades) y contrastación. "
        "Menciona la fuente principal una sola vez de forma profesional (p. ej., 'según <dominio>')."
    ),
    # Emprendimiento:
    "emprende": (
        "Organiza con foco en emprendedores/pyme: costos, trámites, beneficios, pasos. "
        "Menciona la fuente principal una sola vez."
    ),
    # Finanzas:
    "finanzas": (
        "Organiza temáticamente: tasas, liquidez, cartera, riesgo, indicadores, efectos para comercios. "
        "Menciona la fuente principal una sola vez."
    ),
    # Economía:
    "economia": (
        "Organiza temáticamente: crecimiento, empleo, inflación, política monetaria/fiscal. "
        "Menciona la fuente principal una sola vez."
    ),
    # Pagos/fintech:
    "pagos": (
        "Organiza temáticamente: rails (PIX, UPI, CBDC), redes (Visa, Mastercard, Redeban), wallets (Nequi), liquidación, interoperabilidad. "
        "Menciona la fuente principal una sola vez."
    ),
    # Proyectos/Inversiones:
    "proyectos": (
        "Organiza temáticamente: monto, fuente de recursos, cronograma, actores, riesgos, estado. "
        "Menciona la fuente principal una sola vez."
    ),
    # Boletín de respaldo:
    "boletin": (
        "Boletín ejecutivo: 4–6 frases claras y 3 bullets accionables. "
        "Menciona la fuente principal una sola vez."
    ),
}


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
    raw = re.sub(r"^\s*opini[oó]n\s*:\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*(\||—|–|:|·|•)\s*.*$", "", raw)  # corta cola tras separadores
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > 65:
        cut = raw[:65]
        cut = re.sub(r"\s+\S*$", "", cut)  # no cortar palabra
        raw = cut
    return raw[0].upper() + raw[1:]



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

def quality_ok(text: str) -> bool:
    if not text:
        return False
    if len(text) < 500:  # exige un mínimo real
        return False
    # ratio de líneas únicas (evita repeticiones/ruido)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    uniq = len(set(lines))
    return (uniq / max(1, len(lines))) >= 0.7


def domain_of(url: str) -> str:
    try:
        import urllib.parse
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc.split(":")[0]
    except Exception:
        return ""

BIG_MEDIA = {
    "semana.com","eltiempo.com","elespectador.com","larepublica.co",
    "portafolio.co","wradio.com.co","caracol.com.co","bluradio.com",
}

CO_HINTS = ["colombia","bogotá","bogota","medellín","medellin","antioquia","cali",".co/"]
LATAM_COUNTRIES = [
    "argentina","bolivia","brasil","brasil","chile","colombia","costa rica","cuba","ecuador",
    "el salvador","guatemala","honduras","méxico","mexico","nicaragua","panamá","panama","paraguay",
    "perú","peru","república dominicana","uruguay","venezuela"
]

def infer_region(url: str, text: str) -> str:
    u = url.lower(); t = text[:800].lower()
    if u.endswith(".co") or any(h in u for h in CO_HINTS) or any(h in t for h in CO_HINTS):
        return "CO"
    if any(c in u for c in LATAM_COUNTRIES) or any(c in t for c in LATAM_COUNTRIES):
        return "LATAM"
    return "WORLD"

TOPIC_KEYWORDS = {
    "economia":  ["economía","economia","inflación","pib","crecimiento","empleo","banrep","minhacienda","macro"],
    "finanzas":  ["tasa","crédito","cartera","banca","liquidez","usura","morosidad","riesgo","financiero"],
    "pagos":     ["pagos","pos","qr","billetera","pse","adquirente","pasarela","pix","upi","cbdc","cdbc","visa","mastercard","redeban","nequi","swift"],
    "emprende":  ["emprend","startup","pyme","cámara de comercio","camara de comercio","registro mercantil","rueda de negocios","aceleradora","incubadora"],
    "proyectos": ["proyecto","inversión","inversion","capex","obra","megaproyecto","concesión","ani","fdn","publica privada","app "],
    "judicial":  ["corte","tribunal","sentencia","tutela","demanda","sanción","sancion","proceso judicial"],
    "tecnologia":["tecnología","tecnologia","ia","inteligencia artificial","nube","cloud","ciberseguridad","blockchain"],
    "cripto":    ["bitcoin","btc","cripto","crypto","ethereum","eth","stablecoin"],
    "politica":  ["congreso","decreto","ley","reglamenta","reforma","política pública","politica publica"],
    "negocios":  ["negocio","adquisición","adquisicion","fusiones","alianza","joint venture","expansión","expansion"],
    "cooperativas":["cooperativa","mutual","solidaria","finanzas solidarias"],
    "comunidades":["comunidad","local","barrio","asociación","asociacion"],
}

def infer_topics(url: str, text: str) -> list:
    u = url.lower(); t = text.lower()
    found = set()
    for k, kws in TOPIC_KEYWORDS.items():
        if any(w in u for w in kws) or any(w in t for w in kws):
            found.add(k)
    if not found:
        found.add("economia")
    return list(found)

def pretty_tags(topics: list, region: str) -> list:
    mapping = {
        "economia":"Economía","finanzas":"Finanzas","pagos":"Pagos","emprende":"Emprendimiento",
        "proyectos":"Proyectos","judicial":"Judicial","tecnologia":"Tecnología","cripto":"Cripto",
        "politica":"Política","negocios":"Negocios","cooperativas":"Cooperativas","comunidades":"Comunidades",
    }
    tags = [mapping.get(t, t.title()) for t in topics]
    if region == "CO": tags.append("Colombia")
    elif region == "LATAM": tags.append("América Latina")
    else: tags.append("Mundo")
    # quita duplicados preservando orden
    seen=set(); out=[]
    for x in tags:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

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

def summarize_with_gemini(text, url, style="critico", main_domain=None):
    if not GEMINI_MODEL or not text:
        return None
    style_rules = STYLES.get(style, STYLES["critico"])
    src = main_domain or (url.split("//")[-1].split("/")[0])

    prompt = f"""
Eres editor económico para comercios en Colombia/LATAM.
Reescribe SIN copiar textual. Tono neutro. Entrega JSON: title, summary, article.

Estilo: {style_rules}

Reglas:
- No incluyas licencias ni disclaimers (Creative Commons, etc.).
- No pongas fechas/horas en título ni summary.
- Evita 'OPINIÓN:' en el título; usa tono informativo.
- Evita citas largas; una corta si aporta valor.
- Título: 6–12 palabras, sin “| Nombre del medio”.
- Summary: 150–250 palabras, 3–4 frases, claras y accionables.
- Article: 400–700 palabras, 5–8 párrafos; cierra con:
  "Qué vigilar" (3 bullets accionables).
- Cita la fuente principal UNA vez de forma profesional, por ejemplo: "según {src}".
- No inventes datos; si falta info, dilo sin suponer.
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
            if len(s) >= 140 and len(a) >= 350:
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

    # título (SEO, usando IA si hay)
    title = clean_title(ai["title"] if ai and ai.get("title") else title)

    # summary limpio (sin cortar palabra y sin "..." extra)
    if ai and ai.get("summary"):
        summary = ai["summary"].strip()
    else:
        trimmed = body[:300]
        trimmed = re.sub(r"\s+\S*$", "", trimmed)   # corta en palabra
        summary = trimmed if len(body) <= 300 else trimmed + "…"

    # contenido (IA si hay, si no el body limpio)
    article_md = ai["article"] if ai and ai.get("article") else body
    if not article_md or len(article_md) < 200:
        return

    # slug único
    base_slug = slugify(title)
    slug = f"{today}-{base_slug}"
    p = CONTENT / f"{slug}.md"
    if p.exists():
        slug = f"{today}-{base_slug}-{h(title)[:6]}"
        p = CONTENT / f"{slug}.md"

    # tags bonitos desde topics/region (si no hay IA, usa defaults)
    topics = (ai.get("topics") if ai and ai.get("topics") else [])
    region = (ai.get("region") if ai and ai.get("region") else "WORLD")
    auto_tags = pretty_tags(topics, region)

    # canonical siempre al link de la fuente (ya lo calculaste al procesar)
    canonical = link

    # FRONTMATTER — ojo a comas y llaves
    fm = {
        "title": title,
        "description": summary,
        "pubDate": today,
        "tags": auto_tags,
        "status": status,
        "risk": "bajo",
        "action": "Evaluar impacto en comisiones/operación.",
        "sources": [{"name": "Fuente", "url": link}],
        "canonicalUrl": canonical,
    }
    if og_image:
        fm["image"] = {"src": og_image, "alt": title}

    md = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + article_md.strip()
    p.write_text(md, encoding="utf-8")



def run():
    import os, urllib.parse, datetime, time
    from collections import defaultdict

    ROOT = Path(__file__).resolve().parents[1]
    CONTENT = ROOT / "src" / "content" / "blog"
    FEEDS_FILE = ROOT / "data" / "feeds.txt"
    print("USANDO RAIZ:", ROOT)
    print("CONTENIDO EN:", CONTENT)
    print("FEEDS_FILE:", FEEDS_FILE)

    CONTENT.mkdir(parents=True, exist_ok=True)

    # 1) SIEMPRE declara POOL ANTES del bucle
    POOL = []

    # (carga feeds, construye 'candidates' como ya lo haces)
    candidates = discover_candidates(FEEDS_FILE)  # <- tu lógica existente

    seen = load_seen()  # si tienes deduplicación
    for title, link in candidates:
        # --- tu deduplicación previa ---
        # if h(title+link) in seen: continue

        html, final_url = get_html(link)
        canon = final_url
        og_image = ""  # no reusar OG

        if html:
            c, _ = extract_meta(final_url, html)  # ignoramos OG
            if c:
                canon = urllib.parse.urljoin(final_url, c)

        raw = extract_article(canon or final_url)
        body = clean_text(raw)
        if not body or len(body) < 200:
            continue
        if not quality_ok(body):
            continue

        dom    = domain_of(canon)
        region = infer_region(canon, body)
        topics = infer_topics(canon, body)
        is_big = dom in BIG_MEDIA

        # 2) AÑADE al POOL dentro del bucle
        POOL.append({
            "title": title,
            "url": canon,
            "body": body,
            "domain": dom,
            "region": region,   # "CO", "LATAM", "WORLD"
            "topics": topics,   # ej. ["economia","finanzas"]
            "is_big": is_big,
        })
        time.sleep(0.4)

    # 3) SI POOL ESTÁ VACÍO, salir sin error
    if not POOL:
        print("No hubo candidatos válidos en esta corrida.")
        return

    # === Selección priorizada (12) ===
    MAX_NEW = int(os.getenv("MAX_NEW", "12"))
    by_region_topic = defaultdict(list)
    by_topic = defaultdict(list)
    non_big_by_topic_CO = defaultdict(list)
    for it in POOL:
        for tp in it["topics"]:
            by_topic[tp].append(it)
            by_region_topic[(it["region"], tp)].append(it)
            if it["region"] == "CO" and not it["is_big"]:
                non_big_by_topic_CO[tp].append(it)

    def pick_one(lst, used):
        for it in lst:
            if it["url"] not in used:
                used.add(it["url"])
                return it
        return None

    selected, used = [], set()
    # … (aquí tu lógica de selección de 12 tal como la pegaste) …

    # === Escribir (respetando budget de IA) ===
    GEMINI_BUDGET = int(os.getenv("GEMINI_BUDGET", "12"))  # o "10" si quieres 10 con IA
    calls = 0
    for style, it in selected:
        ai = None
        if calls < GEMINI_BUDGET:
            ai = summarize_with_gemini(it["body"], it["url"], style=style, main_domain=it["domain"])
            calls += 1
            if ai:
                ai["topics"] = it["topics"]
                ai["region"] = it["region"]

        write_md(
            it["title"],
            it["url"],   # ya es canónica
            it["body"],
            og_image="", # nunca reusamos OG
            ai=ai,
            status="draft"
        )

    # (opcional) save_seen(seen) si actualizas tu set de vistos


MAX_NEW = int(os.getenv("MAX_NEW", "12"))

# Índices
by_region_topic = defaultdict(list)
by_topic = defaultdict(list)
non_big_by_topic_CO = defaultdict(list)
for it in POOL:
    for tp in it["topics"]:
        by_topic[tp].append(it)
        by_region_topic[(it["region"], tp)].append(it)
        if it["region"] == "CO" and not it["is_big"]:
            non_big_by_topic_CO[tp].append(it)

def pick_one(lst, used):
    for it in lst:
        if it["url"] not in used:
            used.add(it["url"])
            return it
    return None

selected = []
used = set()

# 1) 2 temas con más publicaciones detectadas en CO (estilo crítico)
top_CO_topics = sorted(
    [(tp, len(by_region_topic[("CO", tp)])) for tp in by_topic.keys()],
    key=lambda x: x[1], reverse=True
)
for tp, _n in top_CO_topics[:2]:
    it = pick_one(by_region_topic[("CO", tp)], used)
    if it: selected.append(("critico", it))
    if len(selected) >= MAX_NEW: break

# 2) 1 tema con más publicaciones en WORLD (estilo crítico)
if len(selected) < MAX_NEW:
    top_WORLD_topics = sorted(
        [(tp, len(by_region_topic[("WORLD", tp)])) for tp in by_topic.keys()],
        key=lambda x: x[1], reverse=True
    )
    if top_WORLD_topics:
        tp, _n = top_WORLD_topics[0]
        it = pick_one(by_region_topic[("WORLD", tp)], used)
        if it: selected.append(("critico", it))

# 3) 2 “silencios” en CO: ≥2 notas de no-grandes y 0 de grandes (estilo crítico)
if len(selected) < MAX_NEW:
    silencios = []
    for tp, lst in non_big_by_topic_CO.items():
        if len(lst) >= 2:
            big_count = sum(1 for x in by_region_topic[("CO", tp)] if x["is_big"])
            if big_count == 0:
                silencios.append((tp, len(lst)))
    for tp, _ in sorted(silencios, key=lambda x: x[1], reverse=True)[:2]:
        it = pick_one(non_big_by_topic_CO[tp], used)
        if it: selected.append(("critico", it))
        if len(selected) >= MAX_NEW: break

# 4) 1 Emprendimiento en CO
if len(selected) < MAX_NEW:
    it = pick_one(by_region_topic.get(("CO","emprende"), []), used)
    if it: selected.append(("emprende", it))

# 5) 1 Finanzas en CO
if len(selected) < MAX_NEW:
    it = pick_one(by_region_topic.get(("CO","finanzas"), []), used)
    if it: selected.append(("finanzas", it))

# 6) 1 Economía en CO
if len(selected) < MAX_NEW:
    it = pick_one(by_region_topic.get(("CO","economia"), []), used)
    if it: selected.append(("economia", it))

# 7) 1 Pagos/Fintech (CO si hay, si no LATAM/WORLD)
if len(selected) < MAX_NEW:
    it = (pick_one(by_region_topic.get(("CO","pagos"), []), used)
          or pick_one(by_region_topic.get(("LATAM","pagos"), []), used)
          or pick_one(by_region_topic.get(("WORLD","pagos"), []), used))
    if it: selected.append(("pagos", it))

# 8) 1 Proyectos/Inversiones en CO
if len(selected) < MAX_NEW:
    it = pick_one(by_region_topic.get(("CO","proyectos"), []), used)
    if it: selected.append(("proyectos", it))

# 9) + 2 slots extra para completar 12 (prioriza Tecnología CO y Judicial CO; luego CO generales)
EXTRA_TARGETS = [("tecnologia","CO"), ("judicial","CO")]
for tp, rg in EXTRA_TARGETS:
    if len(selected) >= MAX_NEW: break
    it = pick_one(by_region_topic.get((rg, tp), []), used)
    if it: selected.append(("critico", it))

# Relleno si aún faltan (prefiere CO, luego LATAM, luego WORLD)
if len(selected) < MAX_NEW:
    resto = [it for it in POOL if it["url"] not in used]
    # ordena por preferencia región
    order = {"CO":0, "LATAM":1, "WORLD":2}
    resto.sort(key=lambda x: order.get(x["region"], 3))
    for it in resto:
        selected.append(("boletin", it))
        used.add(it["url"])
        if len(selected) >= MAX_NEW:
            break

# === Escribir: respeta budget de IA ===
calls = 0
for style, it in selected:
    topics = it["topics"]; region = it["region"]; dom = it["domain"]
    ai = None
    if calls < GEMINI_BUDGET:
        ai = summarize_with_gemini(it["body"], it["url"], style=style, main_domain=dom)
        calls += 1
        # añade topics/region a lo que pasa a write_md (para tags bonitos)
        if ai is not None:
            ai["topics"] = topics
            ai["region"] = region
    # sin imagen OG
    write_md(
        it["title"],
        it["url"],          # <- ya guardamos la canónica en "url" al llenar el POOL
        it["body"],
        og_image="",        # <- no reutilizamos OG
        ai=ai,
        status="draft"
    )


if __name__ == "__main__":
    run()

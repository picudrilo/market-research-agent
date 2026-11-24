# agents/scraper.py
"""
Agente 0 — Verificación de frescura y scraping híbrido para Amazon México.

Prioridad de datos:
  'fresco'        — PostgreSQL tiene datos < 7 días para este mercado → no scrapea
  'desactualizado'— datos en BD > 7 días → scraping para actualizar precios/BSR
  'sin_datos'     — sin datos para este mercado → scraping completo

Salida: CSVs en data/raw/auto/ en formato compatible con ingesta.py (detección automática).

Variables .env:
  SCRAPERAPI_KEY  (opcional) — si no existe, intenta requests directo
  KEEPA_API_KEY   (opcional, no implementado aún)
"""
import os
import re
import csv
import json
import time
import random
import unicodedata
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# ── Configuración ─────────────────────────────────────────────────────────────

RAW_DIR  = Path("data/raw")
AUTO_DIR = Path("data/raw/auto")

SCRAPERAPI_KEY       = os.getenv("SCRAPERAPI_KEY", "")
UMBRAL_FRESCURA_DIAS = 7
MAX_DETALLES_ASIN    = 20      # máx páginas de producto individuales por análisis
DELAY_MIN, DELAY_MAX = 3, 6   # segundos entre requests directos (sin ScraperAPI)
UMBRAL_RESCATE_IA    = 5       # si el filtro A deja menos de esto, se invoca el fallback IA (B)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

AMAZON_MX    = "https://www.amazon.com.mx"
AUTOCOMPLETE = "https://completion.amazon.com/search/complete"


def _slug(texto: str) -> str:
    """Normaliza texto para usar en nombres de archivo y comparaciones."""
    return re.sub(r"[^\w]", "_", texto.lower().strip())


# ── Filtro de relevancia ──────────────────────────────────────────────────────

_STOPWORDS_REL = {
    "para", "de", "del", "la", "el", "los", "las", "con", "sin", "en", "y",
    "por", "un", "una", "al", "a", "mejor", "comprar", "kit", "set",
}


def _normalizar_rel(texto: str) -> str:
    """Minúsculas sin acentos."""
    s = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return s.lower()


def _tokens_significativos(texto: str) -> list:
    """Tokens alfanuméricos relevantes (sin stopwords, > 2 caracteres)."""
    toks = re.findall(r"[a-z0-9]+", _normalizar_rel(texto))
    return [t for t in toks if t not in _STOPWORDS_REL and len(t) > 2]


def _stem_es(palabra: str) -> str:
    """Stem ligero para español: quita plural (-es, -s) para unificar variantes
    como cerveza/cervezas, termo/termos, capsula/capsulas."""
    for suf in ("es", "s"):
        if palabra.endswith(suf) and len(palabra) - len(suf) >= 4:
            return palabra[:-len(suf)]
    return palabra


def _prefijo_comun(a: str, b: str) -> int:
    """Longitud del prefijo común entre dos palabras."""
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _palabra_matchea(token: str, palabra: str) -> bool:
    """Un token del query coincide con una palabra del título si es substring, o si
    comparten un prefijo largo. El prefijo común tolera variantes morfológicas que el
    stem simple no capta: cerveza/cervecero, termo/térmica, monohidrato/monohidratada."""
    if token in palabra or palabra in token:
        return True
    pc = _prefijo_comun(token, palabra)
    return pc >= 4 and pc >= 0.6 * min(len(token), len(palabra))


def _contar_matches(tokens_q_stem: list, titulo: str) -> int:
    """Cuenta cuántos tokens del query aparecen en el título (con matching morfológico)."""
    palabras = re.findall(r"[a-z0-9]+", _normalizar_rel(titulo))
    return sum(1 for st in tokens_q_stem if any(_palabra_matchea(st, w) for w in palabras))


def filtrar_por_relevancia(productos: list, mercado: str) -> tuple:
    """Descarta productos cuyo título no corresponde al mercado buscado (FILTRO A).

    Amazon devuelve resultados 'relacionados' amplios: buscar 'termo para cerveza'
    puede traer botellas de agua o accesorios de hidratación. Sin este filtro, los
    agentes analizan productos que no son lo que el usuario pidió.

    Regla: un producto es relevante si su título contiene AL MENOS UNO de los tokens
    significativos del mercado (con matching morfológico por prefijo). Esto mantiene a los
    competidores reales del tipo de producto —'termo para cerveza' conserva todos los termos
    (incluidos genéricos como Stanley, que sí compiten)— y descarta lo que no es del tipo:
    accesorios de botella de agua, sensores, popotes (no dicen 'termo' ni 'cerveza').
    Se ordena por número de coincidencias, así los que combinan ambos términos (más
    específicos, ej. termos de cerveza) quedan primero. El fallback IA (B) rescata productos
    relevantes que usan sinónimos no cubiertos por el keyword. Retorna (relevantes, descartados).
    """
    tokens_q = _tokens_significativos(mercado)
    if not tokens_q:
        return productos, []
    tokens_q_stem = [_stem_es(t) for t in tokens_q]

    umbral = 1  # basta el token del tipo de producto; el orden prioriza los más específicos

    relevantes, descartados = [], []
    for p in productos:
        matches = _contar_matches(tokens_q_stem, p.get("titulo", ""))
        if matches >= umbral:
            p["_relevancia"] = matches
            relevantes.append(p)
        else:
            descartados.append(p)

    relevantes.sort(key=lambda x: x.get("_relevancia", 0), reverse=True)
    return relevantes, descartados


def rescatar_con_ia(descartados: list, mercado: str, limite: int = 15) -> list:
    """Fallback IA (FILTRO B): cuando el filtro por palabras deja muy pocos productos,
    pregunta a Claude cuáles de los descartados SÍ corresponden al mercado. Cubre
    sinónimos y variantes que el stemming no captura (ej: cerveza→cervecero).
    Usa Haiku porque es clasificación simple. Solo se invoca cuando hace falta."""
    if not descartados:
        return []
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return []

    muestra = descartados[:limite]
    lista = "\n".join(f"{i}. {p.get('titulo', '')[:120]}" for i, p in enumerate(muestra))
    prompt = (
        f'Mercado buscado: "{mercado}"\n\n'
        f"Productos candidatos:\n{lista}\n\n"
        "Indica cuáles corresponden al MISMO tipo de producto que el mercado buscado "
        "(no accesorios de otra categoría ni productos distintos).\n"
        'Responde SOLO con JSON: {"relevantes": [0, 2, 5]}'
    )
    try:
        from anthropic import Anthropic
        from agents.memoria import parsear_json_claude
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system="Clasificas relevancia de productos. Respondes solo con JSON válido.",
            messages=[{"role": "user", "content": prompt}],
        )
        data = parsear_json_claude(resp.content[0].text, "rescate_relevancia")
        idxs = data.get("relevantes", [])
        rescatados = [muestra[i] for i in idxs
                      if isinstance(i, int) and 0 <= i < len(muestra)]
        for p in rescatados:
            p["_relevancia"] = 1
        return rescatados
    except Exception as e:
        print(f"  [scraper] Rescate IA falló: {e}")
        return []


# ── Bloque 1: Frescura de datos ───────────────────────────────────────────────

def verificar_frescura_datos(mercado: str, engine=None) -> str:
    """
    Consulta PostgreSQL para ver si hay datos recientes del mercado.
    Retorna: 'fresco' | 'desactualizado' | 'sin_datos'
    """
    if engine is None:
        try:
            from sqlalchemy import create_engine as _ce
            db_url = os.getenv("DATABASE_URL", "")
            if db_url:
                engine = _ce(db_url)
        except Exception:
            pass

    if engine is None:
        print("  [scraper] Sin conexión a BD — asumiendo sin_datos")
        return "sin_datos"

    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT MAX(fecha_captura), COUNT(*) FROM productos WHERE mercado = :m"),
                {"m": mercado},
            ).fetchone()
    except Exception as e:
        print(f"  [scraper] Error BD: {e}")
        return "sin_datos"

    if not row or row[1] == 0 or row[0] is None:
        return "sin_datos"

    ultima = row[0]
    if hasattr(ultima, "date"):
        ultima = ultima.date()
    dias = (date.today() - ultima).days
    print(f"  [scraper] '{mercado}': último dato {ultima} ({dias} días)")
    return "fresco" if dias < UMBRAL_FRESCURA_DIAS else "desactualizado"


# ── Bloque 2: HTTP ────────────────────────────────────────────────────────────

def _fetch(url: str) -> "str | None":
    """
    Descarga HTML. Usa ScraperAPI si SCRAPERAPI_KEY está en .env.
    Sin key, intenta request directo con headers de browser (puede fallar por anti-bot).
    """
    if SCRAPERAPI_KEY:
        api_url = (
            f"http://api.scraperapi.com/"
            f"?api_key={SCRAPERAPI_KEY}"
            f"&url={urllib.parse.quote(url, safe='')}"
            f"&country_code=mx&render=false"
        )
        try:
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    return resp.read().decode("utf-8", errors="replace")
                print(f"  [scraper] ScraperAPI HTTP {resp.status}")
        except Exception as e:
            print(f"  [scraper] ScraperAPI error: {e}")
        return None

    # Sin ScraperAPI — intento directo
    try:
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()

        snippet = raw[:400].decode("utf-8", errors="replace").lower()
        if "captcha" in snippet or "robot check" in snippet or "automated access" in snippet:
            print("  [scraper] Amazon detectó bot (CAPTCHA) — agrega SCRAPERAPI_KEY para evitarlo")
            return None

        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [scraper] Request directo falló: {e}")
        return None


# ── Bloque 3: Amazon Autocomplete (gratis) ────────────────────────────────────

def obtener_keywords_autocomplete(mercado: str) -> list:
    """
    Amazon Autocomplete API — gratis, sin autenticación, sin límite práctico.
    Genera 6 variaciones del query para obtener sugerencias diversas.
    Retorna lista de dicts compatibles con insertar_keywords() de ingesta.py.
    """
    variaciones = [
        mercado,
        f"{mercado} precio",
        f"{mercado} marca",
        f"{mercado} natural",
        f"comprar {mercado}",
        f"mejor {mercado}",
    ]

    vistas: set = set()
    resultado = []

    for q in variaciones:
        try:
            params = urllib.parse.urlencode({
                "q":            q,
                "search-alias": "aps",
                "client":       "amazon-search-ui",
            })
            req = urllib.request.Request(
                f"{AUTOCOMPLETE}?{params}",
                headers={"User-Agent": HEADERS["User-Agent"]},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            sugerencias = data[1] if len(data) > 1 else []
            for kw in sugerencias:
                kw = kw.strip().lower()
                if kw and len(kw) >= 3 and kw not in vistas:
                    vistas.add(kw)
                    resultado.append({
                        "keyword":                kw,
                        "volumen_busqueda":       0,
                        "cerebro_iq_score":       0,
                        "title_density":          0,
                        "productos_competidores": 0,
                        "tendencia_30d":          0,
                    })
            time.sleep(0.5)
        except Exception as e:
            print(f"  [scraper] Autocomplete '{q}': {e}")

    print(f"  [scraper] Keywords autocomplete: {len(resultado)}")
    return resultado


# ── Bloque 4: Parseo HTML — resultados de búsqueda ───────────────────────────

def _parsear_busqueda(html: str, mercado: str) -> list:
    """Extrae productos de una página de resultados de Amazon MX."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [scraper] FALTA: pip install beautifulsoup4 lxml")
        return []

    soup = BeautifulSoup(html, "lxml")
    productos = []
    vistos: set = set()

    # Selector principal: cada resultado tiene data-component-type="s-search-result"
    divs = soup.find_all(
        "div",
        attrs={"data-asin": True, "data-component-type": "s-search-result"},
    )
    if not divs:
        # Fallback: cualquier div con data-asin de 10 caracteres
        divs = [
            d for d in soup.find_all("div", attrs={"data-asin": True})
            if len(d.get("data-asin", "")) == 10
        ]

    for div in divs:
        asin = div.get("data-asin", "").strip()
        if not asin or len(asin) != 10 or asin in vistos:
            continue
        vistos.add(asin)

        # Título — varios selectores por versión de página
        titulo = ""
        for sel in [
            "h2 a span",
            "h2 span.a-size-medium",
            "h2 span",
            ".a-size-medium.a-color-base.a-text-normal",
        ]:
            tag = div.select_one(sel)
            if tag and tag.get_text(strip=True):
                titulo = tag.get_text(strip=True)
                break

        # Precio
        precio = None
        whole = div.select_one("span.a-price-whole")
        if whole:
            try:
                w = re.sub(r"[^\d]", "", whole.get_text())
                frac_tag = div.select_one("span.a-price-fraction")
                f = re.sub(r"[^\d]", "", frac_tag.get_text() if frac_tag else "00")
                precio = float(f"{w}.{f[:2].ljust(2, '0')}")
            except Exception:
                pass

        # Rating (4.3 de 5 estrellas)
        rating = None
        for sel in ["span.a-icon-alt", "i.a-icon-star span.a-icon-alt",
                    "i.a-icon-star-small span.a-icon-alt"]:
            tag = div.select_one(sel)
            if tag:
                m = re.search(r"(\d+)[,.](\d+)", tag.get_text())
                if m:
                    rating = float(f"{m.group(1)}.{m.group(2)}")
                    break

        # Reviews count
        reviews = None
        for sel in [
            "span[aria-label*='calificacion']",
            "span[aria-label*='calificación']",
            "span[aria-label*='rating']",
            "a[href*='customerReviews'] span",
        ]:
            tag = div.select_one(sel)
            if tag:
                try:
                    reviews = int(re.sub(r"[^\d]", "", tag.get_text()))
                    if reviews > 0:
                        break
                except Exception:
                    pass

        # Sponsored
        sponsored = bool(div.select_one(
            ".s-sponsored-label-info-icon, "
            "[data-component-type='sp-sponsored-result'], "
            ".puis-sponsored-label-text"
        ))

        # Imagen principal
        imagen = ""
        img = div.select_one("img.s-image")
        if img:
            imagen = img.get("src", "")

        if asin and titulo:
            productos.append({
                "asin":          asin,
                "titulo":        titulo[:500],
                "precio":        precio,
                "rating":        rating,
                "reviews_count": reviews,
                "sponsored":     sponsored,
                "imagen_url":    imagen,
                "brand":         "",
                "bsr":           None,
                "fba":           False,
                "categoria":     "",
                "mercado":       mercado,
            })

    return productos


# ── Bloque 5: Parseo HTML — página de producto individual ─────────────────────

def _parsear_producto(html: str, asin: str) -> dict:
    """Extrae BSR, brand, FBA y categoría de la página de un producto."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {}

    soup = BeautifulSoup(html, "lxml")
    datos: dict = {"asin": asin, "bsr": None, "brand": "", "fba": False, "categoria": ""}

    # BSR — Amazon usa al menos 3 formatos distintos según versión de página
    bsr = None

    # Formato 1: texto que contiene "Clasificación en los más vendidos"
    for nodo in soup.find_all(string=re.compile(r"Clasificaci[oó]n.*m[aá]s vendidos", re.I)):
        texto = nodo.parent.get_text(separator=" ")
        m = re.search(r"#\s*([\d,.]+)", texto)
        if m:
            try:
                bsr = int(re.sub(r"[^\d]", "", m.group(1)))
                break
            except Exception:
                pass

    # Formato 2: tabla de detalles (th → td)
    if not bsr:
        for th in soup.find_all("th"):
            if "más vendido" in th.get_text().lower() or "best seller" in th.get_text().lower():
                td = th.find_next_sibling("td")
                if td:
                    m = re.search(r"#\s*([\d,.]+)", td.get_text())
                    if m:
                        try:
                            bsr = int(re.sub(r"[^\d]", "", m.group(1)))
                        except Exception:
                            pass

    # Formato 3: bullet list en detailBullets
    if not bsr:
        for li in soup.select("#detailBullets_feature_div li, #productDetails_detailBullets_sections1 tr"):
            texto = li.get_text(separator=" ")
            if "más vendido" in texto.lower() or "best seller" in texto.lower():
                m = re.search(r"#\s*([\d,.]+)", texto)
                if m:
                    try:
                        bsr = int(re.sub(r"[^\d]", "", m.group(1)))
                    except Exception:
                        pass

    datos["bsr"] = bsr

    # Brand
    for sel in ["#bylineInfo", "a#bylineInfo", "span#bylineInfo", ".po-brand .po-break-word"]:
        tag = soup.select_one(sel)
        if tag:
            brand = tag.get_text(strip=True)
            brand = re.sub(r"^(Visitar la tienda de|Marca:|Brand:)\s*", "", brand, flags=re.I)
            datos["brand"] = brand[:255]
            break

    # FBA — varios lugares donde Amazon muestra el fulfillment
    for sel in ["#merchant-info", "#tabular-buybox-truncate-0", "#mir-layout-DELIVERY_BLOCK"]:
        tag = soup.select_one(sel)
        if tag:
            texto = tag.get_text().lower()
            datos["fba"] = "amazon" in texto and (
                "envía" in texto or "enviado" in texto or "fulfilled" in texto
            )
            break

    # Categoría (breadcrumb de navegación)
    breadcrumb = soup.select_one("#wayfinding-breadcrumbs_container ul")
    if breadcrumb:
        items = [li.get_text(strip=True) for li in breadcrumb.find_all("li")]
        datos["categoria"] = " > ".join(i for i in items if i and "›" not in i)[:255]

    return datos


# ── Bloque 6: Scraping principal ──────────────────────────────────────────────

def scraping_busqueda_amazon(mercado: str, paginas: int = 3) -> list:
    """
    Scrapea hasta `paginas` páginas de resultados de búsqueda de Amazon MX.
    Retorna lista de productos con: asin, titulo, precio, rating, reviews, etc.
    """
    todos: list = []
    vistos: set = set()
    kw = urllib.parse.quote(mercado)

    for pag in range(1, paginas + 1):
        url = f"{AMAZON_MX}/s?k={kw}" if pag == 1 else f"{AMAZON_MX}/s?k={kw}&page={pag}"
        print(f"  [scraper] Búsqueda pág {pag}...", end=" ", flush=True)

        html = _fetch(url)
        if not html:
            print("sin respuesta — deteniendo")
            break

        nuevos = [p for p in _parsear_busqueda(html, mercado) if p["asin"] not in vistos]
        vistos.update(p["asin"] for p in nuevos)
        todos.extend(nuevos)
        print(f"{len(nuevos)} nuevos (total: {len(todos)})")

    return todos


def _parsear_resenas(html: str, asin: str, mercado: str) -> list:
    """Extrae las reseñas visibles en la página del producto (~8 más recientes).
    No cuesta peticiones extra: reutiliza el HTML ya descargado para BSR/marca."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html, "lxml")
    resenas = []
    for rev in soup.select('div[data-hook="review"], div[data-hook="cmps-review"]'):
        # Rating: texto tipo "4.0 de 5 estrellas"
        rating = None
        rt = rev.select_one('[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"]')
        if rt:
            m = re.search(r"([0-5](?:[.,]\d)?)", rt.get_text())
            if m:
                try:
                    rating = int(round(float(m.group(1).replace(",", "."))))
                    rating = min(5, max(1, rating))
                except Exception:
                    rating = None

        cuerpo = ""
        bt = rev.select_one('[data-hook="review-body"]')
        if bt:
            cuerpo = bt.get_text(" ", strip=True)[:2000]

        titulo = ""
        tt = rev.select_one('[data-hook="review-title"]')
        if tt:
            # El título a veces incluye el texto del rating; tomar la última línea limpia.
            partes = [x.strip() for x in tt.get_text("\n", strip=True).split("\n") if x.strip()]
            titulo = (partes[-1] if partes else "")[:255]

        verificada = bool(rev.select_one('[data-hook="avp-badge"]'))

        if cuerpo and rating:
            resenas.append({
                "asin":          asin,
                "titulo_resena": titulo or None,
                "cuerpo":        cuerpo,
                "rating":        rating,
                "verificada":    verificada,
                "mercado":       mercado,
            })
    return resenas


def scraping_detalle_asin(asin: str, mercado: str = "") -> tuple:
    """Scrapea la página de producto. Retorna (detalle, resenas):
    detalle = BSR, brand, FBA, categoría; resenas = reseñas visibles en la página."""
    html = _fetch(f"{AMAZON_MX}/dp/{asin}")
    if not html:
        return {"asin": asin}, []
    return _parsear_producto(html, asin), _parsear_resenas(html, asin, mercado)


# ── Bloque 7: Guardar CSV compatible con ingesta.py ──────────────────────────

def guardar_como_csv(productos: list, keywords: list, mercado: str) -> tuple:
    """
    Guarda los datos scrapeados en data/raw/auto/ en formato que ingesta.py detecta:
      - Productos → columnas xray  (detectadas por: ASIN Sales, BSR, Active Sellers…)
      - Keywords  → columnas xray_keyword (detectadas por: Cerebro IQ Score, Search Volume…)

    'ASIN Sales' se estima como reviews_count // 10 (heurística aproximada).
    La columna fuente='scraper' en PostgreSQL distingue estos registros de Helium 10.

    Retorna: (path_productos, path_keywords) — None si la lista estaba vacía.
    """
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(mercado)
    hoy  = date.today().isoformat()

    path_prod = path_kw = None

    # — Productos (formato xray)
    if productos:
        path_prod = AUTO_DIR / f"{slug}_{hoy}_productos.csv"
        campos = [
            "ASIN", "Product Details", "Brand", "BSR", "Price MX$", "Fees MX$",
            "Review Count", "Ratings", "ASIN Sales", "Parent Level Sales",
            "Active Sellers", "ASIN Revenue", "Fulfillment", "Image URL", "Category",
        ]
        with open(path_prod, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            writer.writeheader()
            for p in productos:
                rev = p.get("reviews_count") or 0
                ventas_est = max(1, rev // 10) if rev else ""
                writer.writerow({
                    "ASIN":               p.get("asin", ""),
                    "Product Details":    p.get("titulo", ""),
                    "Brand":              p.get("brand", ""),
                    "BSR":                p.get("bsr") or "",
                    "Price MX$":          p.get("precio") or "",
                    "Fees MX$":           "",
                    "Review Count":       rev or "",
                    "Ratings":            p.get("rating") or "",
                    "ASIN Sales":         ventas_est,
                    "Parent Level Sales": ventas_est,
                    "Active Sellers":     1,
                    "ASIN Revenue": (
                        round(p["precio"] * ventas_est, 2)
                        if p.get("precio") and ventas_est else ""
                    ),
                    "Fulfillment":        "FBA" if p.get("fba") else "FBM",
                    "Image URL":          p.get("imagen_url", ""),
                    "Category":           p.get("categoria", ""),
                })
        print(f"  [scraper] Guardado: {path_prod} ({len(productos)} productos)")

    # — Keywords (formato xray_keyword)
    if keywords:
        path_kw = AUTO_DIR / f"{slug}_{hoy}_keywords.csv"
        campos_kw = [
            "Keyword Phrase", "Search Volume", "Cerebro IQ Score",
            "Title Density", "Competing Products", "Search Volume Trend",
        ]
        with open(path_kw, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=campos_kw)
            writer.writeheader()
            for kw in keywords:
                writer.writerow({
                    "Keyword Phrase":      kw.get("keyword", ""),
                    "Search Volume":       kw.get("volumen_busqueda") or 0,
                    "Cerebro IQ Score":    kw.get("cerebro_iq_score") or 0,
                    "Title Density":       kw.get("title_density") or 0,
                    "Competing Products":  kw.get("productos_competidores") or 0,
                    "Search Volume Trend": kw.get("tendencia_30d") or 0,
                })
        print(f"  [scraper] Guardado: {path_kw} ({len(keywords)} keywords)")

    return path_prod, path_kw


def guardar_resenas_bd(resenas: list, mercado: str, engine=None) -> int:
    """Inserta las reseñas scrapeadas en la tabla `resenas`. Reemplaza las previas
    del mismo mercado (la tabla solo la puebla el scraper, así que es seguro)."""
    if not resenas:
        return 0
    from sqlalchemy import create_engine as _ce, text as _text
    if engine is None:
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url:
            return 0
        engine = _ce(db_url)

    cols = ["asin", "titulo_resena", "cuerpo", "rating", "verificada", "mercado"]
    sql = _text(
        "INSERT INTO resenas (asin, titulo_resena, cuerpo, rating, verificada, mercado) "
        "VALUES (:asin, :titulo_resena, :cuerpo, :rating, :verificada, :mercado)"
    )
    n = 0
    with engine.begin() as conn:
        conn.execute(_text("DELETE FROM resenas WHERE mercado = :m"), {"m": mercado})
        for r in resenas:
            conn.execute(sql, {k: r.get(k) for k in cols})
            n += 1
    return n


# ── Bloque 8: Punto de entrada ────────────────────────────────────────────────

def ejecutar(mercado: str, engine=None) -> str:
    """
    Verifica frescura de datos y ejecuta scraping si es necesario.
    Retorna: 'csv' | 'scraping' | 'hibrido'
      'csv'      → datos frescos en BD, no se tocó nada
      'scraping' → scraping completo porque no había datos
      'hibrido'  → había datos viejos, se complementaron con scraping
    """
    print(f"\n{'='*50}")
    print("AGENTE 0: VERIFICACIÓN Y OBTENCIÓN DE DATOS")
    print(f"{'='*50}")
    print(f"  Mercado:   {mercado}")
    print(f"  ScraperAPI: {'configurado' if SCRAPERAPI_KEY else 'SIN KEY — requests directos'}")

    frescura = verificar_frescura_datos(mercado, engine)
    print(f"  Estado datos: {frescura.upper()}")

    if frescura == "fresco":
        print("  Datos frescos — pipeline usa BD directamente, sin scraping")
        return "csv"

    modo = "sin_datos" if frescura == "sin_datos" else "hibrido"
    if modo == "sin_datos":
        print("  Sin datos para este mercado — iniciando scraping completo")
    else:
        print("  Datos desactualizados — actualizando con scraping")

    # Keywords (gratis, siempre funciona)
    keywords = obtener_keywords_autocomplete(mercado)

    # Páginas de búsqueda
    productos = scraping_busqueda_amazon(mercado, paginas=3)

    # Filtro de relevancia: Amazon devuelve resultados amplios. Sin esto, los agentes
    # analizan productos que no corresponden a lo buscado (ej: 'termo para cerveza'
    # trayendo botellas de agua) y las conclusiones salen desviadas.
    if productos:
        # A) Filtro por palabras + stemming (gratis)
        productos, descartados = filtrar_por_relevancia(productos, mercado)
        if descartados:
            print(f"  [scraper] {len(descartados)} productos descartados por no coincidir con '{mercado}'")

        # B) Fallback IA: solo si A dejó muy pocos y hay descartados que revisar.
        #    Rescata sinónimos/variantes que el stemming no captura.
        if len(productos) < UMBRAL_RESCATE_IA and descartados:
            print(f"  [scraper] Pocos relevantes ({len(productos)}) — revisando descartados con IA...")
            rescatados = rescatar_con_ia(descartados, mercado)
            if rescatados:
                print(f"  [scraper] IA rescató {len(rescatados)} producto(s) relevante(s) adicional(es)")
                productos.extend(rescatados)

        if 0 < len(productos) < 3:
            print(f"  [scraper] ADVERTENCIA: solo {len(productos)} productos relevantes para '{mercado}'.")
            print(f"  El análisis será limitado. Considera una búsqueda más específica o subir un CSV de Helium 10.")

    if not productos:
        print(f"  [scraper] Sin productos relevantes para '{mercado}' — guardando solo keywords")
        guardar_como_csv([], keywords, mercado)
        return "scraping"

    # Enriquecer con detalle de página (BSR, brand, FBA)
    # Solo orgánicos para no gastar peticiones en anuncios
    asins_organicos = [p["asin"] for p in productos if not p.get("sponsored")]
    asins_detallar  = asins_organicos[:MAX_DETALLES_ASIN]

    print(f"  [scraper] Enriqueciendo {len(asins_detallar)} ASINs orgánicos...")
    resenas_todas = []
    for i, asin in enumerate(asins_detallar):
        print(f"    {i+1}/{len(asins_detallar)} {asin}", end=" ", flush=True)
        detalle, resenas_asin = scraping_detalle_asin(asin, mercado)
        resenas_todas.extend(resenas_asin)
        for p in productos:
            if p["asin"] == asin:
                if detalle.get("bsr"):
                    p["bsr"] = detalle["bsr"]
                if detalle.get("brand"):
                    p["brand"] = detalle["brand"]
                if detalle.get("fba") is not None:
                    p["fba"] = detalle["fba"]
                p["categoria"] = detalle.get("categoria", "")
                break
        bsr_txt = f"BSR #{detalle['bsr']}" if detalle.get("bsr") else "sin BSR"
        n_rev = f", {len(resenas_asin)} reseñas" if resenas_asin else ""
        print(f"→ {bsr_txt}{n_rev}")

    guardar_como_csv(productos, keywords, mercado)

    # Guardar reseñas scrapeadas en la BD para que el agente de reseñas las use.
    if resenas_todas:
        try:
            guardar_resenas_bd(resenas_todas, mercado, engine)
            print(f"  [scraper] {len(resenas_todas)} reseñas guardadas en BD")
        except Exception as e:
            print(f"  [scraper] No se pudieron guardar reseñas: {e}")

    con_bsr = sum(1 for p in productos if p.get("bsr"))
    peticiones = 3 + len(asins_detallar)
    costo_usd  = peticiones * 0.00049
    print(
        f"\n  Scraping completado: {len(productos)} productos, "
        f"{con_bsr} con BSR, {len(keywords)} keywords, {len(resenas_todas)} reseñas"
    )
    if SCRAPERAPI_KEY:
        print(f"  Peticiones ScraperAPI: ~{peticiones} (~${costo_usd:.3f} USD)")

    return "scraping" if modo == "sin_datos" else "hibrido"


if __name__ == "__main__":
    import sys
    m = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "sal marina artesanal"
    ejecutar(m)

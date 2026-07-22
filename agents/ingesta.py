# agents/ingesta.py
import os
import re
import unicodedata
import pandas as pd
from pathlib import Path
from datetime import date, datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

RAW_DIR     = Path("data/raw")
AUTO_DIR    = Path("data/raw/auto")
REPORTS_DIR = Path("reports")

# Columnas clave para identificar el tipo de CSV por su contenido.
# Xray MX exporta "Fees MX$"; versiones USD antiguas usan "Fees $" —
# usamos columnas estables que no cambian entre regiones.
DETECTORES = {
    "xray":         ["ASIN Sales", "Parent Level Sales", "Active Sellers", "BSR"],
    "xray_keyword": ["Cerebro IQ Score", "Search Volume", "Title Density"],
    "asin_grabber": ["Price MX$", "Ratings", "Review Count", "Origin"],
    "inventory":    ["Fulfillment", "Stock", "Seller Rating", "Positive Feedback %"],
}


# ─────────────────────────────────────────────
# SELECCIÓN DE ARCHIVOS POR MERCADO
# ─────────────────────────────────────────────

# Palabras que no aportan a la identidad del mercado. Se ignoran al comparar
# la categoría pedida contra el nombre del archivo, para que "aceite de argán"
# haga match con "aceite argan" sin exigir la preposición.
_STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "para", "con", "sin", "en", "y",
    "por", "un", "una", "al", "a",
}


def _tokenizar(texto: str) -> set:
    """Normaliza a minúsculas sin acentos y devuelve el set de tokens
    alfanuméricos significativos (sin stopwords)."""
    s = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[a-z0-9]+", s.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _keyword_de_archivo(nombre: str) -> str:
    """Extrae la parte descriptiva del nombre de un CSV de Helium 10,
    quitando el prefijo del exportador, la fecha y sufijos de versión.

    Ej: 'Helium_10_Xray_2026-05-05-creatina monohidrato.csv' → 'creatina monohidrato'
        'Helium_10_Xray_2026-05-06 - guantes de box.csv'      → 'guantes de box'
    """
    base = Path(nombre).stem
    base = re.sub(r"helium[_ ]*10[_ ]*xray", " ", base, flags=re.I)
    base = re.sub(r"asingrabber", " ", base, flags=re.I)
    base = re.sub(r"helium[_ ]*10[_ ]*inventory[_ ]*levels", " ", base, flags=re.I)
    base = re.sub(r"\d{4}-\d{2}-\d{2}", " ", base)   # fecha
    base = re.sub(r"_\d{1,2}\b", " ", base)          # sufijo de versión _02, _3
    return base.strip(" -_")


def seleccionar_archivos_por_mercado(mercado: str, archivos: list) -> list:
    """Filtra los CSVs cuyo nombre corresponde a la categoría pedida.

    Regla: todos los tokens significativos del mercado deben estar presentes
    en el nombre del archivo. Así 'creatina' trae todos los CSVs de creatina,
    y 'creatina monohidrato' solo los que combinan ambos términos — sin
    arrastrar las otras ~500 categorías del directorio.
    """
    tokens_mercado = _tokenizar(mercado)
    if not tokens_mercado:
        return []

    seleccionados = []
    for path in archivos:
        tokens_archivo = _tokenizar(_keyword_de_archivo(path.name))
        if tokens_mercado.issubset(tokens_archivo):
            seleccionados.append(path)
    return seleccionados


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def get_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL no está definida en .env")
    return create_engine(url)


def normalizar_columnas(df):
    """Limpia nombres de columnas: strip y colapsa espacios múltiples."""
    df.columns = [re.sub(r"\s+", " ", c).strip() for c in df.columns]
    return df


def limpiar_numero(val):
    """Convierte '1,435', 'MX$181.22', 'N/A', '-' a float o None."""
    if pd.isna(val):
        return None
    s = re.sub(r"[^\d.]", "", str(val))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def limpiar_bool(val):
    """Convierte 'Sponsored', 'Yes', 'No', vacío a bool."""
    if pd.isna(val):
        return False
    v = str(val).strip().upper()
    return v not in ("NO", "FALSE", "0", "", "N/A", "-")


def parsear_fecha(val):
    """Convierte 'May 13, 2015' a date o None."""
    if pd.isna(val) or str(val).strip() in ("N/A", "-", ""):
        return None
    try:
        return datetime.strptime(str(val).strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def detectar_tipo(df):
    cols = set(df.columns)
    for tipo, columnas_clave in DETECTORES.items():
        if all(c in cols for c in columnas_clave):
            return tipo
    return "desconocido"


# ─────────────────────────────────────────────
# PARSERS POR TIPO DE ARCHIVO
# ─────────────────────────────────────────────

def _get(row, *keys):
    """Devuelve el primer valor no-nulo entre las claves dadas (manejo multi-versión H10)."""
    for k in keys:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return v
    return None


def parsear_xray(df, mercado):
    """Parsea Helium 10 Xray export → tabla productos.
    Soporta columnas en MX$ ('Price  MX$', 'Fees  MX$') y USD ('Price $', 'Fees $')."""
    registros = []
    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or asin in ("nan", "N/A"):
            continue

        # Sub-filas de variantes (Display Order = "1.1.", "1.2."…) se incluyen normalmente
        registros.append({
            "asin":                     asin,
            "titulo":                   str(_get(row, "Product Details") or "")[:500],
            "marca":                    str(_get(row, "Brand") or "")[:255],
            "categoria":                str(_get(row, "Category") or "")[:255] or None,
            "size_tier":                str(_get(row, "Size Tier") or "")[:50] or None,
            # Precio: versión MX primero, luego USD legacy
            "precio":                   limpiar_numero(_get(row, "Price MX$", "Price $")),
            "bsr":                      int(limpiar_numero(_get(row, "BSR")) or 0) or None,
            "reviews_count":            int(limpiar_numero(_get(row, "Review Count")) or 0) or None,
            "rating":                   limpiar_numero(_get(row, "Ratings")),
            "ventas_mensuales_asin":    int(limpiar_numero(_get(row, "ASIN Sales")) or 0) or None,
            "ventas_mensuales_parent":  int(limpiar_numero(_get(row, "Parent Level Sales")) or 0) or None,
            "revenue_mensual_asin":     limpiar_numero(_get(row, "ASIN Revenue")),
            "revenue_mensual_parent":   limpiar_numero(_get(row, "Parent Level Revenue")),
            # Fees: versión MX primero, luego USD legacy
            "fees":                     limpiar_numero(_get(row, "Fees MX$", "Fees $")),
            "active_sellers":           int(limpiar_numero(_get(row, "Active Sellers")) or 0) or None,
            "review_velocity":          int(limpiar_numero(_get(row, "Review velocity")) or 0) or None,
            "fba":                      str(_get(row, "Fulfillment") or "").upper() in ("FBA", "AMZ"),
            "dimensiones":              str(_get(row, "Dimensions") or "")[:100] or None,
            "peso_kg":                  limpiar_numero(_get(row, "Weight")),
            "seller_nombre":            str(_get(row, "Seller") or "")[:255] or None,
            "seller_age_months":        int(limpiar_numero(_get(row, "Seller Age (mo)")) or 0) or None,
            "buy_box":                  str(_get(row, "Buy Box") or "")[:255] or None,
            "best_seller":              limpiar_bool(_get(row, "Best Seller")),
            "pais_vendedor":            str(_get(row, "Seller Country/Region") or "")[:10] or None,
            "imagen_url":               str(_get(row, "Image URL") or "") or None,
            "fecha_creacion_listing":   parsear_fecha(_get(row, "Creation Date")),
            "fuente":                   "xray",
            "mercado":                  mercado,
            "fecha_captura":            date.today(),
        })
    return registros


def parsear_asin_grabber(df, mercado):
    """Parsea ASIN Grabber export → tabla productos."""
    registros = []
    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or asin in ("nan", "N/A"):
            continue
        registros.append({
            "asin":          asin,
            "titulo":        str(row.get("Product Details", ""))[:500],
            "marca":         str(row.get("Brand", ""))[:255],
            "precio":        limpiar_numero(row.get("Price MX$")),
            "bsr":           int(limpiar_numero(row.get("BSR")) or 0) or None,
            "reviews_count": int(limpiar_numero(row.get("Review Count")) or 0) or None,
            "rating":        limpiar_numero(row.get("Ratings")),
            "imagen_url":    str(row.get("Image URL", "")) if not pd.isna(row.get("Image URL", "")) else None,
            "fuente":        "asin_grabber",
            "mercado":       mercado,
            "fecha_captura": date.today(),
        })
    return registros


def extraer_asin_de_nombre(nombre_archivo: str) -> str:
    """Extrae el ASIN del nombre del archivo si está presente.
    Ej: MX_AMAZON_cerebro_B09WBSG47Q_2026-05-02.csv → B09WBSG47Q"""
    m = re.search(r'[_-]([A-Z0-9]{10})[_-]', nombre_archivo)
    return m.group(1) if m else ""


def parsear_xray_keyword(df, mercado, nombre_archivo=""):
    """Parsea Helium 10 Cerebro / Magnet / Xray Keyword export → tabla keywords.

    Cerebro: columnas 'Organic Rank', 'Sponsored Rank' (rank del ASIN objetivo)
    Magnet:  columnas 'Keyword Sales', 'Competitor Rank (avg)', 'Suggested PPC Bid'
    Ambos soportados simultáneamente.
    """
    asin_origen = extraer_asin_de_nombre(nombre_archivo)
    registros = []

    for _, row in df.iterrows():
        keyword = str(row.get("Keyword Phrase", "")).strip()
        if not keyword or keyword == "nan":
            continue

        # Rank orgánico: Cerebro usa "Organic Rank", Magnet usa "Competitor Rank (avg)"
        organic_rank = limpiar_numero(_get(row, "Organic Rank", "Competitor Rank (avg)"))

        # Tendencia: Cerebro da % cambio absoluto (306 = +306%), Magnet da factor (2 = 2x)
        # Se guarda tal cual; el agente de keywords lo usa solo como signo (positivo/negativo)
        tendencia = limpiar_numero(_get(row, "Search Volume Trend"))

        registros.append({
            "keyword":                keyword,
            "volumen_busqueda":       int(limpiar_numero(_get(row, "Search Volume")) or 0) or None,
            "tendencia_30d":          tendencia,
            "productos_competidores": int(limpiar_numero(_get(row, "Competing Products")) or 0) or None,
            "cerebro_iq_score":       int(limpiar_numero(_get(row, "Cerebro IQ Score")) or 0) or None,
            # Keyword Sales solo existe en Magnet; Cerebro no lo tiene
            "keyword_sales":          int(limpiar_numero(_get(row, "Keyword Sales")) or 0) or None,
            "title_density":          int(limpiar_numero(_get(row, "Title Density")) or 0) or None,
            "competitor_rank_avg":    organic_rank,
            "sugerido_ppc_bid":       limpiar_numero(_get(row, "Suggested PPC Bid")),
            "fuente":                 "cerebro" if asin_origen else "xray_keyword",
            "asin_origen":            asin_origen,
            "mercado":                mercado,
            "fecha_captura":          date.today(),
        })
    return registros


def parsear_inventory(df):
    """Parsea Helium 10 Inventory Levels — solo retorna un dict de resumen, no inserta en BD."""
    resumen = []
    for _, row in df.iterrows():
        brand = str(row.get("Brand", "")).strip()
        stock = str(row.get("Stock", "")).strip()
        precio = limpiar_numero(row.get("Price"))
        fulfillment = str(row.get("Fulfillment", "")).strip()
        resumen.append(f"{brand} | {fulfillment} | stock: {stock} | precio: MX${precio or '?'}")
    return resumen


# ─────────────────────────────────────────────
# CARGA A POSTGRESQL
# ─────────────────────────────────────────────

def insertar_productos(registros, engine):
    if not registros:
        return 0

    cols = list(registros[0].keys())
    col_names   = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}"
        for c in cols
        if c not in ("asin", "fuente", "fecha_captura")
    )

    sql = text(f"""
        INSERT INTO productos ({col_names})
        VALUES ({placeholders})
        ON CONFLICT (asin, fuente, fecha_captura) DO UPDATE SET {updates}
    """)

    insertados = 0
    with engine.begin() as conn:
        for r in registros:
            conn.execute(sql, r)
            insertados += 1

    return insertados


def insertar_keywords(registros, engine):
    if not registros:
        return 0

    cols = list(registros[0].keys())
    col_names    = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}"
        for c in cols
        if c not in ("keyword", "fuente", "asin_origen", "fecha_captura")
    )

    sql = text(f"""
        INSERT INTO keywords ({col_names})
        VALUES ({placeholders})
        ON CONFLICT (keyword, fuente, asin_origen, fecha_captura) DO UPDATE SET {updates}
    """)

    insertados = 0
    with engine.begin() as conn:
        for r in registros:
            conn.execute(sql, r)
            insertados += 1

    return insertados


# ─────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 1: INGESTA DE DATOS")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)

    # Priorizar archivos auto-generados para este mercado específico.
    # Esto evita que CSVs de Helium 10 de otros mercados contaminen el análisis.
    slug = re.sub(r"[^\w]", "_", mercado.lower().strip())
    auto_archivos = sorted(AUTO_DIR.glob(f"{slug}_*.csv")) if AUTO_DIR.exists() else []
    if auto_archivos:
        archivos_csv = auto_archivos
        print(f"\n  Usando {len(archivos_csv)} archivo(s) auto-generados para '{mercado}'")
    else:
        # Análisis aislado: seleccionar solo los CSVs de data/raw/ cuyo nombre
        # corresponde al mercado pedido. Evita mezclar las ~500 categorías del
        # directorio en un mismo análisis.
        todos = sorted(RAW_DIR.glob("*.csv"))
        archivos_csv = seleccionar_archivos_por_mercado(mercado, todos)

        if archivos_csv:
            print(f"\n  Mercado '{mercado}': {len(archivos_csv)} de {len(todos)} CSVs coinciden por nombre")
        elif todos:
            print(f"\n  ADVERTENCIA: ningún CSV en data/raw/ coincide con '{mercado}'.")
            print(f"  Hay {len(todos)} CSVs de otras categorías que NO se ingestarán")
            print(f"  para no contaminar el análisis. Verifica el nombre del mercado")
            print(f"  o agrega un CSV de Helium 10 para esta categoría.")
            return None

    if not archivos_csv:
        print("\n  Sin archivos CSV en data/raw/ ni data/raw/auto/")
        return None

    print(f"\n  Archivos CSV a procesar: {len(archivos_csv)}")
    for p in archivos_csv:
        print(f"    - {p.name}")

    engine = get_engine()
    resumen = {"productos": 0, "keywords": 0, "omitidos": [], "errores": []}
    procesados = []

    asins_vistos = set()  # para deduplicar asinGrabber duplicados entre archivos

    for path in archivos_csv:
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
            df = normalizar_columnas(df)
        except Exception as e:
            print(f"\n  Error leyendo {path.name}: {e}")
            resumen["errores"].append(path.name)
            continue

        tipo = detectar_tipo(df)
        print(f"\n  {path.name}")
        print(f"    Tipo: {tipo} | Filas: {len(df)}")

        if tipo == "xray":
            registros = parsear_xray(df, mercado)
            n = insertar_productos(registros, engine)
            resumen["productos"] += n
            print(f"    Insertados en productos: {n}")
            procesados.append({"archivo": path.name, "tipo": tipo, "registros": n})

        elif tipo == "asin_grabber":
            registros = parsear_asin_grabber(df, mercado)
            nuevos = [r for r in registros if r["asin"] not in asins_vistos]
            asins_vistos.update(r["asin"] for r in nuevos)
            n = insertar_productos(nuevos, engine)
            omitidos = len(registros) - len(nuevos)
            resumen["productos"] += n
            print(f"    Insertados en productos: {n} ({omitidos} duplicados omitidos)")
            procesados.append({"archivo": path.name, "tipo": tipo, "registros": n})

        elif tipo == "xray_keyword":
            registros = parsear_xray_keyword(df, mercado, nombre_archivo=path.name)
            n = insertar_keywords(registros, engine)
            resumen["keywords"] += n
            asin_ref = extraer_asin_de_nombre(path.name)
            print(f"    Insertados en keywords: {n}{f' (ASIN origen: {asin_ref})' if asin_ref else ''}")
            procesados.append({"archivo": path.name, "tipo": tipo, "registros": n})

        elif tipo == "inventory":
            lineas_inv = parsear_inventory(df)
            for l in lineas_inv:
                print(f"    Inventario: {l}")
            procesados.append({"archivo": path.name, "tipo": tipo, "registros": len(lineas_inv)})

        else:
            print(f"    Tipo no reconocido — omitido")
            resumen["omitidos"].append(path.name)

    # Guardar reporte
    lineas = [
        "# Reporte de Ingesta\n",
        f"- Mercado: **{mercado}**",
        f"- Fecha: {date.today()}",
        f"- Registros en `productos`: {resumen['productos']}",
        f"- Registros en `keywords`: {resumen['keywords']}",
    ]
    if resumen["omitidos"]:
        lineas.append(f"- Archivos omitidos (tipo desconocido): {', '.join(resumen['omitidos'])}")
    if resumen["errores"]:
        lineas.append(f"- Archivos con error: {', '.join(resumen['errores'])}")
    lineas.append("\n## Detalle por archivo")
    for p in procesados:
        lineas.append(f"- `{p['archivo']}` → tipo `{p['tipo']}`, {p['registros']} registros")

    reporte_path = REPORTS_DIR / "ingesta.md"
    reporte_path.write_text("\n".join(lineas), encoding="utf-8")

    print(f"\n  Reporte guardado en: {reporte_path}")
    print(f"\n  Productos: {resumen['productos']} | Keywords: {resumen['keywords']}")
    print("\n  Agente de ingesta completado.")
    return resumen


if __name__ == "__main__":
    ejecutar()

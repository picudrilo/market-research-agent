# agents/batch_arbitraje.py
import os
import re
import json
import pandas as pd
from pathlib import Path
from datetime import date
from sqlalchemy import create_engine, text
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.ingesta import normalizar_columnas, limpiar_numero, _get
from agents.estacionalidad import obtener_penalizacion_batch, PENALIZACION_POR_RIESGO
from agents.restricciones import obtener_restriccion_batch

load_dotenv()

HISTORIAL_DIR = Path("historial")
OUTPUTS_DIR   = Path("outputs")

FBA_FEE_DEFAULT = 75.0  # MX$ estimado si no hay dato de fees en el CSV


# ─────────────────────────────────────────────
# BLOQUE 1 — Parseo del CSV Xray
# ─────────────────────────────────────────────

def parsear_xray_batch(df, precios_extra=None):
    """
    Extrae productos del CSV Xray de Helium 10 para análisis batch.

    Busca columna 'precio_compra' en el CSV (la agrega el usuario en Excel).
    precios_extra: dict {asin: float} para precios ingresados desde la UI.
    Productos sin precio de compra quedan incluidos pero sin análisis financiero.
    """
    precios_extra = precios_extra or {}
    productos = []

    for _, row in df.iterrows():
        asin = str(row.get("ASIN", "")).strip()
        if not asin or asin in ("nan", "N/A"):
            continue

        # Precio de compra: columna CSV > override de UI
        precio_compra_csv = limpiar_numero(
            _get(row, "precio_compra", "Precio Compra", "Purchase Price", "Precio de Compra")
        )
        precio_compra = precio_compra_csv or precios_extra.get(asin)

        productos.append({
            "asin":           asin,
            "titulo":         str(_get(row, "Product Details") or "")[:200],
            "marca":          str(_get(row, "Brand") or "")[:100],
            "categoria":      str(_get(row, "Category") or "")[:100] or None,
            "precio_amazon":  limpiar_numero(_get(row, "Price MX$", "Price $")),
            "precio_compra":  precio_compra,
            "bsr":            _safe_int(limpiar_numero(_get(row, "BSR"))),
            "reviews_count":  _safe_int(limpiar_numero(_get(row, "Review Count"))),
            "rating":         limpiar_numero(_get(row, "Ratings")),
            "ventas_mes":     _safe_int(limpiar_numero(_get(row, "ASIN Sales"))),
            "active_sellers": _safe_int(limpiar_numero(_get(row, "Active Sellers"))),
            "fees":           limpiar_numero(_get(row, "Fees MX$", "Fees $")),
            "fba":            str(_get(row, "Fulfillment") or "").upper() in ("FBA", "AMZ"),
            "revenue_mes":    limpiar_numero(_get(row, "ASIN Revenue")),
        })

    return productos


def _safe_int(val, default=None):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────
# BLOQUE 2 — Cálculos financieros (sin Claude)
# ─────────────────────────────────────────────

def calcular_financiero(producto):
    """
    Calcula ROI y desglose de fees para un producto.
    Retorna None si faltan precio_amazon o precio_compra.
    Todos los valores en MX$.
    """
    precio_amazon = producto.get("precio_amazon") or 0
    precio_compra = producto.get("precio_compra") or 0

    if not precio_amazon or not precio_compra:
        return None

    referral_fee = round(precio_amazon * 0.15, 2)
    fba_fee      = round(float(producto.get("fees") or FBA_FEE_DEFAULT), 2)
    ganancia_neta = round(precio_amazon - referral_fee - fba_fee - precio_compra, 2)
    roi           = round((ganancia_neta / precio_compra) * 100, 1)

    return {
        "precio_amazon":  precio_amazon,
        "precio_compra":  precio_compra,
        "referral_fee":   referral_fee,
        "fba_fee":        fba_fee,
        "ganancia_neta":  ganancia_neta,
        "roi":            roi,
    }


def calcular_score_arbitraje(producto, financiero, penalizacion_estacional: int = 0):
    """
    Score 0-100 basado en 4 factores determinísticos + penalización estacional opcional.

    ROI calculado    (40 pts) — rendimiento financiero puro
    BSR              (20 pts) — velocidad de ventas del producto
    Reviews + Rating (20 pts) — confianza del comprador
    Sellers activos  (20 pts) — competencia en Buy Box
    Estacionalidad   (-15 pts máx) — penalización si el mes actual es temporada baja
    """
    if not financiero:
        return 0

    score = 0
    roi     = financiero.get("roi", 0)
    bsr     = producto.get("bsr") or 999_999
    reviews = producto.get("reviews_count") or 0
    rating  = producto.get("rating") or 0
    sellers = producto.get("active_sellers") or 99

    # ROI (40 pts)
    if   roi >= 50: score += 40
    elif roi >= 30: score += 30
    elif roi >= 20: score += 20
    elif roi >= 10: score += 10

    # BSR (20 pts)
    if   bsr <= 100:    score += 20
    elif bsr <= 500:    score += 17
    elif bsr <= 2_000:  score += 13
    elif bsr <= 5_000:  score +=  9
    elif bsr <= 20_000: score +=  5

    # Reviews + Rating (20 pts)
    if   rating >= 4.3 and reviews >= 500: score += 20
    elif rating >= 4.0 and reviews >= 100: score += 15
    elif rating >= 3.7 and reviews >= 50:  score += 10
    elif reviews >= 20:                    score +=  5

    # Sellers activos (20 pts)
    if   sellers <= 2:  score += 20
    elif sellers <= 5:  score += 15
    elif sellers <= 10: score += 10
    elif sellers <= 20: score +=  5

    # Penalización estacional (hasta -15 pts si mes actual es temporada baja)
    score -= penalizacion_estacional

    return max(0, min(score, 100))


def asignar_semaforo(roi, score):
    """
    INVERTIR    — ROI >= 30% Y score >= 60
    RIESGO MEDIO — ROI >= 15% Y score >= 40
    DESCARTAR   — cualquier otro caso
    """
    if roi >= 30 and score >= 60:
        return "INVERTIR"
    if roi >= 15 and score >= 40:
        return "RIESGO MEDIO"
    return "DESCARTAR"


# ─────────────────────────────────────────────
# BLOQUE 3 — Consulta histórica en PostgreSQL
# ─────────────────────────────────────────────

def consultar_historial_asins(asins, engine):
    """
    Busca datos previos de estos ASINs en la tabla productos de Neon.
    Retorna dict {asin: {precio_historico, ventas_historicas, bsr_historico, fecha_ultimo}}.
    """
    if not asins or not engine:
        return {}
    try:
        sql = text("""
            SELECT DISTINCT ON (asin)
                asin, precio, ventas_mensuales_asin, bsr, fecha_captura
            FROM productos
            WHERE asin = ANY(:asins)
            ORDER BY asin, fecha_captura DESC
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql, {"asins": list(asins)}).fetchall()

        return {
            row[0]: {
                "precio_historico":   row[1],
                "ventas_historicas":  row[2],
                "bsr_historico":      row[3],
                "fecha_ultimo":       str(row[4]),
                "en_bd":              True,
            }
            for row in rows
        }
    except Exception as e:
        print(f"  [historial_bd] {type(e).__name__}: {e}")
        return {}


def get_engine():
    url = os.getenv("DATABASE_URL")
    return create_engine(url) if url else None


# ─────────────────────────────────────────────
# BLOQUE 4 — Análisis Claude (1 sola llamada)
# ─────────────────────────────────────────────

def analizar_batch_con_claude(productos_enriquecidos):
    """
    Llama a Claude Sonnet UNA sola vez con todos los productos.
    Aporta análisis cualitativo que los números no detectan:
    riesgos específicos, insights de mercado, comparativa entre productos.
    """
    client = Anthropic()

    resumen = []
    for p in productos_enriquecidos:
        fin = p.get("financiero") or {}
        resumen.append({
            "asin":             p["asin"],
            "producto":         p["titulo"][:80],
            "categoria":        p.get("categoria") or "desconocida",
            "precio_compra":    fin.get("precio_compra", 0),
            "precio_amazon":    fin.get("precio_amazon", 0),
            "roi_pct":          fin.get("roi", 0),
            "ganancia_neta":    fin.get("ganancia_neta", 0),
            "bsr":              p.get("bsr"),
            "reviews":          p.get("reviews_count"),
            "rating":           p.get("rating"),
            "ventas_mes":       p.get("ventas_mes"),
            "sellers_activos":  p.get("active_sellers"),
            "score":            p.get("score_arbitraje", 0),
            "semaforo":         p.get("semaforo", "DESCARTAR"),
            "en_historial_bd":  p.get("en_historial_bd", False),
        })

    total    = len(resumen)
    invertir = sum(1 for p in resumen if p["semaforo"] == "INVERTIR")
    riesgo   = sum(1 for p in resumen if p["semaforo"] == "RIESGO MEDIO")

    prompt = f"""Eres experto en arbitraje de productos para Amazon México.
El vendedor evaluó {total} productos comprados en tienda para revenderlos en Amazon MX.

Los cálculos financieros ya están hechos (ROI, ganancia, fees).
Tu tarea: aportar análisis CUALITATIVO que los números no detectan.

=== RESUMEN BATCH ===
- Total: {total} | INVERTIR: {invertir} | RIESGO MEDIO: {riesgo} | DESCARTAR: {total - invertir - riesgo}

=== PRODUCTOS ===
{json.dumps(resumen, ensure_ascii=False, indent=2)}

=== QUÉ ANALIZAR POR PRODUCTO ===
1. riesgos: 1-3 riesgos ESPECÍFICOS Y CONCRETOS (no genéricos).
   Bueno: "BSR 450 con 18 sellers activos sugiere ~11 ventas/mes por seller, no {resumen[0].get('ventas_mes', 0) if resumen else 0} totales"
   Malo: "Hay bastante competencia en este mercado"
2. razon_veredicto: 1 oración que explique el semáforo asignado
3. insight: algo no obvio — restricciones de marca, estacionalidad, si Amazon vende directo,
   si es producto perecedero, si tiene historial de bajadas de precio, etc.

=== QUÉ ANALIZAR DEL BATCH ===
- top_3_asins: los 3 ASINs con mayor potencial REAL (no solo ROI alto — considera viabilidad)
- competencia_interna: ¿hay 2+ productos que compiten por el mismo nicho en Amazon?
- advertencia_general: el riesgo más importante que aplica a varios productos del batch

Responde ÚNICAMENTE con JSON válido, sin backticks ni explicaciones:

{{
  "productos": [
    {{
      "asin": "...",
      "riesgos": ["riesgo concreto 1", "riesgo concreto 2"],
      "razon_veredicto": "una oración directa",
      "insight": "dato no obvio sobre este producto específico"
    }}
  ],
  "top_3_asins": ["B0...", "B0...", "B0..."],
  "competencia_interna": "descripción o null",
  "advertencia_general": "el riesgo transversal más importante del batch"
}}"""

    respuesta = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4000,
        system="Eres experto en arbitraje Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = next((b.text for b in respuesta.content if b.type == "text"), "")
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    try:
        resultado = json.loads(texto)
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fin    = texto.rfind("}") + 1
        try:
            resultado = json.loads(texto[inicio:fin]) if inicio != -1 else {}
        except (json.JSONDecodeError, ValueError):
            resultado = {}

    resultado["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }
    return resultado


# ─────────────────────────────────────────────
# BLOQUE 5 — Sistema de historial Markdown
# ─────────────────────────────────────────────

def _mxn(val):
    try:
        return f"MX${float(val):,.0f}"
    except (TypeError, ValueError):
        return "—"


def actualizar_historial(nombre_sesion, productos, batch_meta):
    """
    Escribe/actualiza los 3 tipos de archivos de historial:
      historial/index.md
      historial/productos/{ASIN}.md
      historial/sesiones/{fecha}_{slug}.md
    """
    HISTORIAL_DIR.mkdir(exist_ok=True)
    (HISTORIAL_DIR / "productos").mkdir(exist_ok=True)
    (HISTORIAL_DIR / "sesiones").mkdir(exist_ok=True)

    hoy = date.today().isoformat()

    # ── 1. Archivos por ASIN ─────────────────────────
    rutas_asin = []
    for p in productos:
        asin   = p["asin"]
        fin    = p.get("financiero") or {}
        claude = p.get("claude_analisis") or {}
        path   = HISTORIAL_DIR / "productos" / f"{asin}.md"
        rutas_asin.append(str(path))

        est = p.get("riesgo_estacional", "—")
        fila = (
            f"| {hoy} | {_mxn(fin.get('precio_compra'))} | "
            f"{_mxn(fin.get('precio_amazon'))} | "
            f"{fin.get('roi', 0):.1f}% | "
            f"{p.get('score_arbitraje', 0)} | "
            f"{p.get('semaforo', '—')} | {est} |"
        )

        SEP_HIST = "|-------|--------------|---------------|-----|-------|----------|----|"

        if path.exists():
            contenido = path.read_text(encoding="utf-8")
            contenido = re.sub(r"Última actualización:.*", f"Última actualización: {hoy}", contenido)
            # Soportar separador viejo (sin columna estacionalidad) o nuevo
            sep_viejo = "|-------|--------------|---------------|-----|-------|----------|"
            if sep_viejo in contenido and SEP_HIST not in contenido:
                contenido = contenido.replace(sep_viejo, SEP_HIST)
            if SEP_HIST in contenido:
                contenido = contenido.replace(SEP_HIST, f"{SEP_HIST}\n{fila}")
            nuevo_analisis = _bloque_analisis_reciente(p, fin, claude)
            if "## Análisis más reciente" in contenido:
                partes = contenido.split("## Análisis más reciente")
                if "## Notas" in partes[1]:
                    notas_parte = partes[1].split("## Notas")[1]
                    contenido = partes[0] + "## Análisis más reciente\n" + nuevo_analisis + "## Notas" + notas_parte
                else:
                    contenido = partes[0] + "## Análisis más reciente\n" + nuevo_analisis
            path.write_text(contenido, encoding="utf-8")
        else:
            titulo = p.get("titulo", asin)[:80]
            lineas = [
                f"# {titulo}",
                f"ASIN: {asin}",
                f"Categoría: {p.get('categoria') or 'Sin categoría'}",
                f"Última actualización: {hoy}",
                "",
                "## Historial de análisis",
                "| Fecha | Precio compra | Precio Amazon | ROI | Score | Decisión | Estacionalidad |",
                SEP_HIST,
                fila,
                "",
                "## Análisis más reciente",
                _bloque_analisis_reciente(p, fin, claude),
                "## Notas",
                "*(campo libre para notas manuales)*",
            ]
            path.write_text("\n".join(lineas), encoding="utf-8")

    # ── 2. Archivo de sesión ──────────────────────────
    slug = re.sub(r"[^a-z0-9_]", "_", nombre_sesion.lower())[:30]
    path_sesion = HISTORIAL_DIR / "sesiones" / f"{hoy}_{slug}.md"

    invertir  = [p for p in productos if p.get("semaforo") == "INVERTIR"]
    riesgo    = [p for p in productos if p.get("semaforo") == "RIESGO MEDIO"]
    descartar = [p for p in productos if p.get("semaforo") == "DESCARTAR"]
    capital   = sum((p.get("financiero") or {}).get("precio_compra", 0) for p in invertir)

    sorted_prods = sorted(productos, key=lambda x: x.get("score_arbitraje", 0), reverse=True)

    lineas_sesion = [
        f"# Sesión {nombre_sesion} — {date.today().strftime('%d/%m/%Y')}",
        f"Productos analizados: {len(productos)}  ",
        f"INVERTIR: {len(invertir)} | RIESGO MEDIO: {len(riesgo)} | DESCARTAR: {len(descartar)}  ",
        f"Capital requerido (solo INVERTIR, 1 unidad): {_mxn(capital)}",
        "",
    ]

    if batch_meta.get("advertencia_general"):
        lineas_sesion += [
            f"> **Advertencia batch:** {batch_meta['advertencia_general']}",
            "",
        ]
    if batch_meta.get("competencia_interna"):
        lineas_sesion += [
            f"> **Competencia interna:** {batch_meta['competencia_interna']}",
            "",
        ]

    lineas_sesion += [
        "## Ranking completo",
        "| # | Producto | ASIN | Precio compra | Precio Amazon | ROI% | Score | Semáforo | Estacionalidad |",
        "|---|---------|------|--------------|---------------|------|-------|---------|----------------|",
    ]
    for i, p in enumerate(sorted_prods, 1):
        fin = p.get("financiero") or {}
        est = p.get("riesgo_estacional", "—")
        lineas_sesion.append(
            f"| {i} | {p.get('titulo','')[:40]} | `{p['asin']}` | "
            f"{_mxn(fin.get('precio_compra'))} | {_mxn(fin.get('precio_amazon'))} | "
            f"{fin.get('roi', 0):.1f}% | {p.get('score_arbitraje', 0)} | {p.get('semaforo', '—')} | {est} |"
        )

    if invertir:
        top = sorted(invertir, key=lambda x: x.get("score_arbitraje", 0), reverse=True)[:5]
        lineas_sesion += ["", "## Top recomendados (INVERTIR)"]
        for p in top:
            fin    = p.get("financiero") or {}
            claude = p.get("claude_analisis") or {}
            lineas_sesion += [
                f"### {p.get('titulo','')[:60]}",
                f"**ASIN:** `{p['asin']}`  ",
                f"**ROI:** {fin.get('roi', 0):.1f}% | **Ganancia:** {_mxn(fin.get('ganancia_neta'))} | **Score:** {p.get('score_arbitraje',0)}/100  ",
                f"**Razón:** {claude.get('razon_veredicto', '—')}  ",
                f"**Insight:** {claude.get('insight', '—')}",
                "",
            ]

    if descartar:
        lineas_sesion += ["", "## Productos descartados"]
        for p in descartar:
            fin    = p.get("financiero") or {}
            claude = p.get("claude_analisis") or {}
            razon  = claude.get("razon_veredicto") or f"ROI {fin.get('roi',0):.1f}% insuficiente"
            lineas_sesion.append(f"- `{p['asin']}` {p.get('titulo','')[:50]} — {razon}")

    path_sesion.write_text("\n".join(lineas_sesion), encoding="utf-8")

    # ── 3. index.md ──────────────────────────────────
    _actualizar_index(nombre_sesion, hoy, productos, capital)

    return {"sesion": str(path_sesion), "asins": rutas_asin}


def _bloque_analisis_reciente(p, fin, claude):
    lineas = [
        f"**Semáforo:** {p.get('semaforo', '—')}  ",
        f"**ROI:** {fin.get('roi', 0):.1f}% | **Ganancia neta:** {_mxn(fin.get('ganancia_neta'))} | **Score:** {p.get('score_arbitraje',0)}/100  ",
        f"**Desglose:** compra {_mxn(fin.get('precio_compra'))} → venta {_mxn(fin.get('precio_amazon'))} "
        f"− referral {_mxn(fin.get('referral_fee'))} − FBA {_mxn(fin.get('fba_fee'))}",
        "",
    ]
    if claude.get("razon_veredicto"):
        lineas += [f"**Razón:** {claude['razon_veredicto']}", ""]
    if claude.get("riesgos"):
        lineas += ["**Riesgos:**"]
        for r in claude["riesgos"]:
            lineas.append(f"- {r}")
        lineas.append("")
    if claude.get("insight"):
        lineas += [f"**Insight:** {claude['insight']}", ""]
    return "\n".join(lineas) + "\n"


def _actualizar_index(nombre_sesion, hoy, productos, capital):
    path = HISTORIAL_DIR / "index.md"
    invertir_n = sum(1 for p in productos if p.get("semaforo") == "INVERTIR")
    total      = len(productos)

    fila_sesion = (
        f"| {hoy} | {nombre_sesion} | {total} | {invertir_n} | {_mxn(capital)} |"
    )

    if path.exists():
        contenido = path.read_text(encoding="utf-8")
        contenido = re.sub(r"Última actualización:.*", f"Última actualización: {hoy}", contenido)

        sep_sesion = "|-------|--------|-----------|--------------|--------------|"
        if sep_sesion in contenido:
            contenido = contenido.replace(sep_sesion, f"{sep_sesion}\n{fila_sesion}")

        sep_prod = "|------|---------|----------------|-----------|----------|"
        if sep_prod in contenido:
            for p in sorted(productos, key=lambda x: (x.get("financiero") or {}).get("roi", 0), reverse=True):
                fin  = p.get("financiero") or {}
                asin = p["asin"]
                if asin not in contenido:
                    fila_prod = (
                        f"| `{asin}` | {p.get('titulo','')[:40]} | {hoy} | "
                        f"{fin.get('roi', 0):.1f}% | {p.get('semaforo','—')} |"
                    )
                    contenido = contenido.replace(sep_prod, f"{sep_prod}\n{fila_prod}")

        path.write_text(contenido, encoding="utf-8")
    else:
        top5 = sorted(
            [p for p in productos if p.get("financiero")],
            key=lambda x: x["financiero"]["roi"],
            reverse=True,
        )[:5]
        top5_str = "\n".join(
            f"- {p.get('titulo','')[:50]} (ROI {p['financiero']['roi']:.1f}%)" for p in top5
        )

        lineas = [
            "# Índice de Análisis de Arbitraje",
            f"Última actualización: {hoy}",
            "",
            "## Resumen",
            f"- Total productos analizados: {total}",
            "- Total sesiones batch: 1",
            "- Mejores oportunidades históricas:",
            top5_str,
            "",
            "## Sesiones batch",
            "| Fecha | Sesión | Productos | Recomendados | Capital req. |",
            "|-------|--------|-----------|--------------|--------------|",
            fila_sesion,
            "",
            "## Productos analizados",
            "| ASIN | Producto | Última análisis | Mejor ROI | Decisión |",
            "|------|---------|----------------|-----------|----------|",
        ]
        for p in sorted(productos, key=lambda x: (x.get("financiero") or {}).get("roi", 0), reverse=True):
            fin = p.get("financiero") or {}
            lineas.append(
                f"| `{p['asin']}` | {p.get('titulo','')[:40]} | {hoy} | "
                f"{fin.get('roi', 0):.1f}% | {p.get('semaforo','—')} |"
            )
        path.write_text("\n".join(lineas), encoding="utf-8")


# ─────────────────────────────────────────────
# BLOQUE 6 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(df, nombre_sesion="sesion_batch", precios_extra=None, engine=None):
    """
    Punto de entrada para análisis batch de arbitraje.

    df:            DataFrame del CSV Xray (ya leído con pd.read_csv)
    nombre_sesion: nombre para los archivos de historial
    precios_extra: dict {asin: float} si los precios vienen de la UI
    engine:        SQLAlchemy engine para consulta histórica (opcional)

    Retorna (lista_productos_enriquecidos, batch_meta).
    """
    print(f"\n{'='*50}")
    print("BATCH ARBITRAJE")
    print(f"{'='*50}")

    OUTPUTS_DIR.mkdir(exist_ok=True)

    # 1. Parseo
    df = normalizar_columnas(df)
    productos = parsear_xray_batch(df, precios_extra)
    print(f"\n  {len(productos)} productos en CSV")

    con_precio = [p for p in productos if p.get("precio_compra")]
    sin_precio = len(productos) - len(con_precio)
    if sin_precio:
        print(f"  {sin_precio} sin precio de compra — omitidos del análisis financiero")
    print(f"  {len(con_precio)} con precio → analizando")

    if not con_precio:
        print("  Agrega columna 'precio_compra' al CSV y vuelve a intentar.")
        return [], {}

    # 2. Historial en BD
    if engine:
        print(f"\n  Consultando historial en PostgreSQL...")
        asins = {p["asin"] for p in con_precio}
        historial_bd = consultar_historial_asins(asins, engine)
        print(f"  {len(historial_bd)} ASINs con historial previo")
    else:
        historial_bd = {}

    # 3. Estacionalidad — 1 llamada para el batch completo
    categorias = [p.get("categoria") for p in con_precio if p.get("categoria")]
    termino_estacional = max(set(categorias), key=categorias.count) if categorias else nombre_sesion
    print(f"\n  Verificando estacionalidad para: {termino_estacional!r}...")
    try:
        pen_pts, advertencia_estacional = obtener_penalizacion_batch(termino_estacional)
        riesgo_estacional = next(
            k for k, v in PENALIZACION_POR_RIESGO.items() if v == pen_pts
        )
    except Exception as e:
        print(f"  [estacionalidad] Error: {e} — sin penalización")
        pen_pts, advertencia_estacional, riesgo_estacional = 0, "", "BAJO"

    if pen_pts > 0:
        print(f"  Riesgo estacional: {riesgo_estacional} (-{pen_pts} pts a cada score)")
        if advertencia_estacional:
            print(f"  {advertencia_estacional}")
    else:
        print(f"  Riesgo estacional: BAJO (sin penalización)")

    # 3b. Restricciones — 1 llamada para el batch completo
    print(f"\n  Verificando restricciones regulatorias para: {termino_estacional!r}...")
    try:
        nivel_restriccion, advertencia_restriccion = obtener_restriccion_batch(termino_estacional)
    except Exception as e:
        print(f"  [restricciones] Error: {e} — sin alertas")
        nivel_restriccion, advertencia_restriccion = "BAJO", ""

    if nivel_restriccion in ("ALTO", "MEDIO"):
        print(f"  Restricción: {nivel_restriccion}")
        if advertencia_restriccion:
            print(f"  {advertencia_restriccion}")
    else:
        print(f"  Restricción: BAJO (categoría abierta)")

    # 4. Cálculos financieros y scores
    print(f"\n  Calculando financieros y scores...")
    for p in con_precio:
        fin      = calcular_financiero(p)
        score    = calcular_score_arbitraje(p, fin, pen_pts) if fin else 0
        semaforo = asignar_semaforo(fin["roi"], score) if fin else "DESCARTAR"

        p["financiero"]             = fin
        p["score_arbitraje"]        = score
        p["semaforo"]               = semaforo
        p["en_historial_bd"]        = p["asin"] in historial_bd
        p["datos_historicos"]       = historial_bd.get(p["asin"])
        p["riesgo_estacional"]      = riesgo_estacional
        p["penalizacion_estacional"] = pen_pts

    invertir  = sum(1 for p in con_precio if p["semaforo"] == "INVERTIR")
    riesgo    = sum(1 for p in con_precio if p["semaforo"] == "RIESGO MEDIO")
    descartar = sum(1 for p in con_precio if p["semaforo"] == "DESCARTAR")
    print(f"  INVERTIR: {invertir} | RIESGO MEDIO: {riesgo} | DESCARTAR: {descartar}")

    # 4. Análisis Claude (1 sola llamada para todo el batch)
    print(f"\n  Claude analizando {len(con_precio)} productos en 1 llamada...")
    analisis_claude = analizar_batch_con_claude(con_precio)

    analisis_por_asin = {
        item["asin"]: item
        for item in analisis_claude.get("productos", [])
        if "asin" in item
    }
    for p in con_precio:
        p["claude_analisis"] = analisis_por_asin.get(p["asin"], {})

    tokens = analisis_claude.get("_tokens", {})
    print(f"  Tokens: {tokens.get('entrada',0):,} entrada / {tokens.get('salida',0):,} salida")

    batch_meta = {
        "top_3_asins":              analisis_claude.get("top_3_asins", []),
        "competencia_interna":      analisis_claude.get("competencia_interna"),
        "advertencia_general":      analisis_claude.get("advertencia_general"),
        "tokens":                   tokens,
        "riesgo_estacional":        riesgo_estacional,
        "advertencia_estacional":   advertencia_estacional,
        "penalizacion_estacional":  pen_pts,
        "nivel_restriccion":        nivel_restriccion,
        "advertencia_restriccion":  advertencia_restriccion,
    }

    # 5. Historial markdown
    print(f"\n  Generando historial markdown...")
    rutas = actualizar_historial(nombre_sesion, con_precio, batch_meta)
    print(f"  Sesión: {rutas['sesion']}")

    resultado = sorted(con_precio, key=lambda x: x.get("score_arbitraje", 0), reverse=True)
    print(f"\n  Batch completado. {len(resultado)} productos analizados.")
    return resultado, batch_meta


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        df_test = pd.read_csv(sys.argv[1], encoding="utf-8-sig")
        resultado, meta = ejecutar(df_test, "test_local")
        print(f"\n{'='*50}")
        for p in resultado:
            fin = p.get("financiero") or {}
            print(
                f"  {p['semaforo']:<12} | Score {p['score_arbitraje']:>3} | "
                f"ROI {fin.get('roi',0):>6.1f}% | {p['asin']} | {p['titulo'][:50]}"
            )

# agents/competencia.py
import os
import json
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria, parsear_json_claude

load_dotenv()

REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga desde PostgreSQL
# ─────────────────────────────────────────────

def get_engine():
    return create_engine(os.getenv("DATABASE_URL"))


def cargar_productos(mercado):
    """Lee todos los productos del mercado desde PostgreSQL."""
    engine = get_engine()
    sql = text("""
        SELECT asin, titulo, marca, categoria, precio, bsr, reviews_count, rating,
               ventas_mensuales_asin, ventas_mensuales_parent,
               revenue_mensual_asin, revenue_mensual_parent,
               fees, active_sellers, review_velocity,
               fba, size_tier, peso_kg, seller_nombre, seller_age_months,
               pais_vendedor, best_seller, fuente, fecha_captura
        FROM productos
        WHERE mercado = :mercado
        ORDER BY bsr ASC NULLS LAST
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"mercado": mercado})
    return df


# ─────────────────────────────────────────────
# BLOQUE 2 — Análisis estadístico
# ─────────────────────────────────────────────

def analizar_precios(df):
    precios = df["precio"].dropna()
    if precios.empty:
        return {}
    return {
        "minimo":   round(float(precios.min()), 2),
        "maximo":   round(float(precios.max()), 2),
        "promedio": round(float(precios.mean()), 2),
        "mediana":  round(float(precios.median()), 2),
        "p25":      round(float(precios.quantile(0.25)), 2),
        "p75":      round(float(precios.quantile(0.75)), 2),
    }


def analizar_marcas(df):
    """Top marcas por número de productos y revenue total."""
    por_productos = (
        df.groupby("marca")
        .agg(
            num_productos=("asin", "count"),
            rating_promedio=("rating", "mean"),
            bsr_min=("bsr", "min"),
            reviews_total=("reviews_count", "sum"),
        )
        .round(2)
        .sort_values("num_productos", ascending=False)
        .head(15)
    )

    # Revenue solo está en datos Xray
    xray = df[df["fuente"] == "xray"].copy()
    if not xray.empty and "revenue_mensual_asin" in xray.columns:
        rev_por_marca = (
            xray.groupby("marca")["revenue_mensual_asin"]
            .sum()
            .sort_values(ascending=False)
            .head(10)
        )
        por_productos["revenue_mensual"] = por_productos.index.map(rev_por_marca).fillna(0).round(0)

    return por_productos.reset_index().to_dict(orient="records")


def analizar_bsr(df):
    """Distribución de BSR en el mercado."""
    bsr = df["bsr"].dropna()
    if bsr.empty:
        return {}
    rangos = {
        "top_100":   int((bsr <= 100).sum()),
        "top_500":   int((bsr <= 500).sum()),
        "top_1000":  int((bsr <= 1000).sum()),
        "top_5000":  int((bsr <= 5000).sum()),
        "sobre_5000": int((bsr > 5000).sum()),
    }
    return {
        "bsr_minimo":  int(bsr.min()),
        "bsr_maximo":  int(bsr.max()),
        "bsr_mediana": int(bsr.median()),
        "distribucion": rangos,
    }


def analizar_reviews(df):
    reviews = df["reviews_count"].dropna()
    if reviews.empty:
        return {}
    return {
        "promedio":    round(float(reviews.mean()), 0),
        "mediana":     round(float(reviews.median()), 0),
        "maximo":      int(reviews.max()),
        "sin_reviews": int((reviews == 0).sum()),
        "mas_100":     int((reviews >= 100).sum()),
        "mas_1000":    int((reviews >= 1000).sum()),
        "mas_10000":   int((reviews >= 10000).sum()),
    }


def analizar_revenue(df):
    """Solo disponible en datos Xray."""
    xray = df[df["fuente"] == "xray"].copy()
    rev = xray["revenue_mensual_asin"].dropna()
    if rev.empty:
        return {}
    total = float(rev.sum())
    top3  = float(rev.nlargest(3).sum())
    return {
        "revenue_total_mercado": round(total, 0),
        "revenue_promedio":      round(float(rev.mean()), 0),
        "revenue_mediana":       round(float(rev.median()), 0),
        "concentracion_top3_pct": round(top3 / total * 100, 1) if total > 0 else 0,
        "top_5_asins": (
            xray.nlargest(5, "revenue_mensual_asin")[["asin", "marca", "revenue_mensual_asin"]]
            .round(0)
            .to_dict(orient="records")
        ),
    }


def analizar_vendedores(df):
    xray = df[df["fuente"] == "xray"]
    if xray.empty:
        return {}
    total = len(xray)
    return {
        "fba_pct":        round(xray["fba"].sum() / total * 100, 1),
        "vendedor_mx_pct": round((xray["pais_vendedor"] == "MX").sum() / total * 100, 1),
        "age_promedio_meses": round(float(xray["seller_age_months"].dropna().mean()), 0),
        "best_sellers":   int(xray["best_seller"].sum()),
    }


# ─────────────────────────────────────────────
# BLOQUE 3 — Análisis con Claude + memoria
# ─────────────────────────────────────────────

def _retry_competencia(client, metricas):
    prompt = f"""Necesito estos campos de análisis de competencia en Amazon México.
Métricas del mercado: {json.dumps(metricas, ensure_ascii=False)[:1000]}

Responde ÚNICAMENTE con este JSON válido:
{{
  "intensidad_competencia": "baja | media | alta | muy alta",
  "marcas_dominantes": [{{"marca": "nombre", "posicion": "lider", "ventaja_principal": "descripción"}}],
  "barreras_entrada": ["barrera 1", "barrera 2"],
  "insight_clave": "hallazgo principal en 1 oración",
  "estrategia_recomendada": {{
    "posicionamiento": "cómo entrar al mercado",
    "precio_sugerido_mx": "rango MX$X–MX$Y",
    "diferenciador_clave": "qué debe tener el producto"
  }}
}}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=600,
            system="Responde SOLO con JSON válido.",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado = parsear_json_claude(resp.content[0].text, "competencia_retry")
        if resultado.get("intensidad_competencia"):
            print("  [competencia] Retry exitoso.")
        return resultado
    except Exception as e:
        print(f"  [competencia] Retry fallido: {e}")
        return {}


def analizar_con_claude(mercado, metricas, top_productos):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    prompt = f"""Eres un experto en investigación de mercado para Amazon México.
Analiza la competencia en el mercado de: **{mercado}**

{contexto_previo}
=== MÉTRICAS DEL MERCADO ===
{json.dumps(metricas, ensure_ascii=False, indent=2)}

=== TOP 10 PRODUCTOS POR BSR ===
{json.dumps(top_productos, ensure_ascii=False, indent=2)}

=== TU ANÁLISIS ===
Responde ÚNICAMENTE con un JSON válido, sin backticks ni texto extra:

{{
  "resumen_mercado": "descripción del mercado en 2-3 oraciones",
  "intensidad_competencia": "baja | media | alta | muy alta",
  "marcas_dominantes": [
    {{
      "marca": "nombre",
      "posicion": "lider | retador | seguidor | nicho",
      "ventaja_principal": "qué las hace ganar"
    }}
  ],
  "segmentos_precio": [
    {{
      "segmento": "economico | medio | premium",
      "rango_mx": "MX$X — MX$Y",
      "caracteristica": "qué define a este segmento"
    }}
  ],
  "barreras_entrada": [
    "barrera concreta con datos del mercado"
  ],
  "oportunidades_detectadas": [
    {{
      "oportunidad": "descripción concreta",
      "sustento": "qué dato del mercado la respalda"
    }}
  ],
  "estrategia_recomendada": {{
    "posicionamiento": "cómo entrar al mercado",
    "precio_sugerido_mx": "rango recomendado",
    "diferenciador_clave": "qué debe tener el producto para competir"
  }},
  "riesgo_principal": "el mayor riesgo al entrar en este mercado",
  "insight_clave": "el hallazgo más importante en 1-2 oraciones"
}}"""

    print("  Claude analizando competencia...")

    respuesta = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2500,
        system="Eres un analista de mercado experto en ecommerce Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = respuesta.content[0].text
    analisis = parsear_json_claude(texto, "competencia")
    analisis["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    if not analisis.get("intensidad_competencia"):
        print("  [competencia] intensidad_competencia vacía — reintentando con prompt simplificado...")
        analisis = _retry_competencia(client, metricas) or analisis

    hallazgos = {
        "intensidad_competencia":   analisis.get("intensidad_competencia", ""),
        "marcas_dominantes":        [m["marca"] for m in analisis.get("marcas_dominantes", [])],
        "segmentos_precio":         [s["rango_mx"] for s in analisis.get("segmentos_precio", [])],
        "barreras_entrada":         analisis.get("barreras_entrada", []),
        "posicionamiento_sugerido": analisis.get("estrategia_recomendada", {}).get("posicionamiento", ""),
        "precio_sugerido_mx":       analisis.get("estrategia_recomendada", {}).get("precio_sugerido_mx", ""),
        "diferenciador_clave":      analisis.get("estrategia_recomendada", {}).get("diferenciador_clave", ""),
        "insight_clave":            analisis.get("insight_clave", ""),
    }

    campos_vacios = [k for k, v in hallazgos.items() if not v]
    if campos_vacios:
        print(f"  [competencia] ADVERTENCIA: campos vacíos en memoria: {campos_vacios}")
    else:
        print(f"  [competencia] Memoria OK — intensidad={hallazgos['intensidad_competencia']}")

    escribir_memoria("competencia", hallazgos)
    return analisis


# ─────────────────────────────────────────────
# BLOQUE 4 — Reporte
# ─────────────────────────────────────────────

def generar_reporte(mercado, df, metricas, analisis_ia):
    r = []
    r.append(f"# Análisis de Competencia — {mercado}\n")

    r.append("## Resumen del mercado")
    r.append(f"- Total productos analizados: {len(df)}")
    r.append(f"- Marcas distintas: {df['marca'].nunique()}")
    r.append(f"- Fuentes: {', '.join(df['fuente'].unique())}\n")

    p = metricas.get("precios", {})
    if p:
        r.append("## Distribución de precios (MX$)")
        r.append(f"| Mín | P25 | Mediana | Promedio | P75 | Máx |")
        r.append(f"|-----|-----|---------|----------|-----|-----|")
        r.append(f"| {p['minimo']} | {p['p25']} | {p['mediana']} | {p['promedio']} | {p['p75']} | {p['maximo']} |\n")

    bsr = metricas.get("bsr", {})
    if bsr:
        r.append("## Distribución BSR")
        dist = bsr.get("distribucion", {})
        r.append(f"- BSR mínimo (mejor): {bsr['bsr_minimo']:,}")
        r.append(f"- BSR mediana: {bsr['bsr_mediana']:,}")
        for k, v in dist.items():
            r.append(f"- {k.replace('_', ' ').title()}: {v} productos")
        r.append("")

    marcas = metricas.get("marcas", [])
    if marcas:
        r.append("## Top marcas por número de productos")
        r.append("| Marca | Productos | Rating prom. | Reviews totales | BSR mín |")
        r.append("|-------|-----------|-------------|----------------|---------|")
        for m in marcas[:10]:
            rev = int(m.get("revenue_mensual", 0))
            r.append(
                f"| {m['marca']} | {m['num_productos']} | {m['rating_promedio']} "
                f"| {int(m.get('reviews_total') or 0):,} | {int(v) if (v := m.get('bsr_min')) and v == v else 0:,} |"
            )
        r.append("")

    rev = metricas.get("revenue", {})
    if rev:
        r.append("## Revenue mensual estimado (datos Xray)")
        r.append(f"- Revenue total del mercado: MX${rev.get('revenue_total_mercado', 0):,.0f}")
        r.append(f"- Revenue promedio por producto: MX${rev.get('revenue_promedio', 0):,.0f}")
        r.append(f"- Concentración top 3: {rev.get('concentracion_top3_pct', 0)}% del total")
        top5 = rev.get("top_5_asins", [])
        if top5:
            r.append("\n### Top 5 productos por revenue")
            r.append("| ASIN | Marca | Revenue mensual |")
            r.append("|------|-------|----------------|")
            for p in top5:
                r.append(f"| {p['asin']} | {p['marca']} | MX${float(p['revenue_mensual_asin']):,.0f} |")
        r.append("")

    if analisis_ia:
        r.append("---")
        r.append("## Análisis con Inteligencia Artificial (Claude)\n")

        r.append(f"**Resumen:** {analisis_ia.get('resumen_mercado', '')}")
        r.append(f"\n**Intensidad de competencia:** `{analisis_ia.get('intensidad_competencia', '')}`\n")

        r.append("### Marcas dominantes")
        for m in analisis_ia.get("marcas_dominantes", []):
            r.append(f"- **{m['marca']}** ({m['posicion']}): {m['ventaja_principal']}")

        r.append("\n### Segmentos de precio")
        for s in analisis_ia.get("segmentos_precio", []):
            r.append(f"- **{s['segmento'].title()}** {s['rango_mx']}: {s['caracteristica']}")

        r.append("\n### Barreras de entrada")
        for b in analisis_ia.get("barreras_entrada", []):
            r.append(f"- {b}")

        r.append("\n### Oportunidades detectadas")
        for o in analisis_ia.get("oportunidades_detectadas", []):
            r.append(f"\n**{o['oportunidad']}**")
            r.append(f"  → {o['sustento']}")

        est = analisis_ia.get("estrategia_recomendada", {})
        if est:
            r.append("\n### Estrategia recomendada de entrada")
            r.append(f"- **Posicionamiento:** {est.get('posicionamiento', '')}")
            r.append(f"- **Precio sugerido:** {est.get('precio_sugerido_mx', '')}")
            r.append(f"- **Diferenciador clave:** {est.get('diferenciador_clave', '')}")

        r.append(f"\n### Riesgo principal")
        r.append(analisis_ia.get("riesgo_principal", ""))

        r.append(f"\n### Insight clave")
        r.append(analisis_ia.get("insight_clave", ""))

        tokens = analisis_ia.get("_tokens", {})
        r.append(f"\n*Tokens: {tokens.get('entrada', 0)} entrada / {tokens.get('salida', 0)} salida*")

    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 5 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 2: ANÁLISIS DE COMPETENCIA")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    df = cargar_productos(mercado)
    if df.empty:
        print(f"\n  Sin productos en DB para mercado '{mercado}'")
        print("  Ejecuta primero el Agente 1 (ingesta)")
        return None

    print(f"\n  {len(df)} productos cargados para '{mercado}'")
    print(f"  Marcas: {df['marca'].nunique()} | Fuentes: {', '.join(df['fuente'].unique())}")

    metricas = {
        "precios":    analizar_precios(df),
        "bsr":        analizar_bsr(df),
        "reviews":    analizar_reviews(df),
        "revenue":    analizar_revenue(df),
        "vendedores": analizar_vendedores(df),
        "marcas":     analizar_marcas(df),
    }

    p = metricas["precios"]
    print(f"  Precio: MX${p.get('minimo', 0)} — MX${p.get('maximo', 0)} (mediana MX${p.get('mediana', 0)})")

    bsr = metricas["bsr"]
    print(f"  BSR: {bsr.get('bsr_minimo', 'N/A')} — {bsr.get('bsr_maximo', 'N/A')} (mediana {bsr.get('bsr_mediana', 'N/A')})")

    top_productos = (
        df[["asin", "marca", "titulo", "precio", "bsr", "reviews_count", "rating",
            "ventas_mensuales_asin", "revenue_mensual_asin", "fuente"]]
        .head(20)
        .fillna(0)
        .to_dict(orient="records")
    )

    analisis_ia = analizar_con_claude(mercado, metricas, top_productos)

    if analisis_ia:
        print(f"  Claude completó el análisis")
        print(f"  Intensidad: {analisis_ia.get('intensidad_competencia', '')}")
        print(f"  Insight: {str(analisis_ia.get('insight_clave', ''))[:80]}...")

    reporte = generar_reporte(mercado, df, metricas, analisis_ia)

    reporte_path = REPORTS_DIR / "fase1_competidores.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")

    csv_path = OUTPUTS_DIR / "competidores_ranking.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"  CSV guardado en: {csv_path}")

    print("\n  Agente de competencia completado.")
    return metricas


if __name__ == "__main__":
    ejecutar()

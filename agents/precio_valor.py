# agents/precio_valor.py
import os
import json
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria

load_dotenv()

REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga desde PostgreSQL
# ─────────────────────────────────────────────

def get_engine():
    return create_engine(os.getenv("DATABASE_URL"))


def cargar_productos(mercado):
    engine = get_engine()
    sql = text("""
        SELECT asin, marca, precio, bsr, reviews_count, rating,
               ventas_mensuales_asin, revenue_mensual_asin, fees,
               fba, size_tier, fuente
        FROM productos
        WHERE mercado = :mercado AND precio > 0
        ORDER BY bsr ASC NULLS LAST
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"mercado": mercado})
    return df


# ─────────────────────────────────────────────
# BLOQUE 2 — Análisis estadístico de precio
# ─────────────────────────────────────────────

def segmentar_precios(df):
    """Crea segmentos dinámicos basados en cuartiles reales del mercado."""
    p25 = df["precio"].quantile(0.25)
    p75 = df["precio"].quantile(0.75)

    def asignar(precio):
        if precio <= p25:
            return f"Economico (<= MX${p25:.0f})"
        elif precio <= p75:
            return f"Medio (MX${p25:.0f}-{p75:.0f})"
        else:
            return f"Premium (> MX${p75:.0f})"

    df = df.copy()
    df["segmento"] = df["precio"].apply(asignar)
    return df, p25, p75


def analizar_segmentos(df):
    resumen = []
    for seg in df["segmento"].unique():
        grupo = df[df["segmento"] == seg]
        rev_mean = grupo["revenue_mensual_asin"].dropna()
        resumen.append({
            "segmento":          seg,
            "num_productos":     len(grupo),
            "precio_min":        round(float(grupo["precio"].min()), 2),
            "precio_max":        round(float(grupo["precio"].max()), 2),
            "precio_promedio":   round(float(grupo["precio"].mean()), 2),
            "rating_promedio":   round(float(grupo["rating"].dropna().mean()), 2) if not grupo["rating"].dropna().empty else 0,
            "reviews_promedio":  round(float(grupo["reviews_count"].dropna().mean()), 0) if not grupo["reviews_count"].dropna().empty else 0,
            "revenue_promedio":  round(float(rev_mean.mean()), 0) if not rev_mean.empty else 0,
            "fba_pct":           round(grupo["fba"].sum() / len(grupo) * 100, 1) if len(grupo) > 0 else 0,
        })
    return sorted(resumen, key=lambda x: x["precio_min"])


def calcular_margen_estimado(precio, fees_promedio):
    """Estimación básica de margen con referral fee Amazon MX (~15%)."""
    referral = precio * 0.15
    margen_bruto = precio - referral - fees_promedio
    return round(margen_bruto, 2), round(margen_bruto / precio * 100, 1)


# ─────────────────────────────────────────────
# BLOQUE 3 — Análisis con Claude
# ─────────────────────────────────────────────

def analizar_con_claude(mercado, df, segmentos, p25, p75):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    fees_promedio = float(df["fees"].dropna().mean()) if not df["fees"].dropna().empty else 0
    top_revenue = (
        df.nlargest(5, "revenue_mensual_asin")[
            ["asin", "marca", "precio", "bsr", "reviews_count", "rating",
             "ventas_mensuales_asin", "revenue_mensual_asin", "fees"]
        ].fillna(0).to_dict(orient="records")
    )

    prompt = f"""Eres un experto en estrategia de precios para Amazon México.
Mercado: **{mercado}**

{contexto_previo}
Analiza la estructura de precios del mercado y recomienda el precio óptimo de entrada.

=== ESTADÍSTICAS GENERALES ===
- Total productos con precio: {len(df)}
- Precio mínimo: MX${df['precio'].min():.2f}
- Precio máximo: MX${df['precio'].max():.2f}
- Precio promedio: MX${df['precio'].mean():.2f}
- Precio mediana: MX${df['precio'].median():.2f}
- P25: MX${p25:.2f} | P75: MX${p75:.2f}
- FBA fees promedio: MX${fees_promedio:.2f}
- Referral fee Amazon MX estimado: ~15%

=== SEGMENTOS DE PRECIO ===
{json.dumps(segmentos, ensure_ascii=False, indent=2)}

=== TOP 5 POR REVENUE MENSUAL ===
{json.dumps(top_revenue, ensure_ascii=False, indent=2)}

=== REGLAS DE ANÁLISIS (obligatorias) ===
1. TRAZABILIDAD: cada recomendación de precio debe justificarse con los datos entregados
   (percentiles P25/P75, revenue por segmento, fees). Cita el número, no generalices.
2. El margen_estimado_pct debe calcularse con la fórmula real: precio - 15% referral - fees.
   No inventes un margen; derívalo de los números de arriba.
3. Si el segmento de oportunidad se elige, explica con qué dato (menos productos, mejor
   revenue por producto, o peor relación precio-valor de los competidores actuales).

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "diagnostico_precios": "2-3 oraciones sobre la estructura de precios del mercado",
  "segmento_recomendado": "Económico | Medio | Premium",
  "precio_entrada_mx": 0.0,
  "rango_viable_mx": {{"min": 0.0, "max": 0.0}},
  "justificacion_precio": "por qué este precio posiciona bien en el mercado",
  "margen_estimado_pct": 0.0,
  "segmento_saturado": "cuál segmento tiene más competidores con peor relación precio-valor",
  "segmento_oportunidad": "cuál segmento tiene menos competidores o mejor espacio para entrar",
  "estrategia_lanzamiento": "precio de lanzamiento vs precio objetivo y por qué",
  "precio_psicologico": "el precio exacto recomendado (ej: 349, 399, 449) y por qué ese número",
  "insight_precio": "el hallazgo más importante sobre precios en 1-2 oraciones"
}}"""

    print("  Claude analizando estrategia de precio...")
    respuesta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system="Eres experto en pricing Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = respuesta.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    try:
        analisis = json.loads(texto)
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fin    = texto.rfind("}") + 1
        try:
            analisis = json.loads(texto[inicio:fin]) if inicio != -1 else {}
        except (json.JSONDecodeError, ValueError):
            analisis = {}

    analisis["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    escribir_memoria("precio_valor", {
        "precio_entrada_mx":    analisis.get("precio_entrada_mx", 0),
        "rango_viable_mx":      analisis.get("rango_viable_mx", {}),
        "segmento_recomendado": analisis.get("segmento_recomendado", ""),
        "precio_psicologico":   analisis.get("precio_psicologico", ""),
        "margen_estimado_pct":  analisis.get("margen_estimado_pct", 0),
        "insight_precio":       analisis.get("insight_precio", ""),
    })
    return analisis


# ─────────────────────────────────────────────
# BLOQUE 4 — Reporte
# ─────────────────────────────────────────────

def generar_reporte(mercado, segmentos, analisis_ia):
    r = []
    r.append(f"# Análisis de Precio vs Valor — {mercado}\n")

    r.append("## Distribución de precios (MX$)")
    r.append("| Segmento | Productos | Precio prom. | Rating prom. | Revenue prom. | % FBA |")
    r.append("|----------|-----------|-------------|-------------|--------------|-------|")
    for s in segmentos:
        r.append(
            f"| {s['segmento']} | {s['num_productos']} | MX${s['precio_promedio']:,.0f} | "
            f"{s['rating_promedio']} | MX${s['revenue_promedio']:,.0f} | {s['fba_pct']}% |"
        )

    if analisis_ia:
        r.append("\n---")
        r.append("## Análisis con IA (Claude)\n")
        r.append(f"**Diagnóstico:** {analisis_ia.get('diagnostico_precios', '')}\n")

        r.append("### Recomendación de precio")
        r.append(f"- **Segmento recomendado:** {analisis_ia.get('segmento_recomendado', '')}")
        r.append(f"- **Precio de entrada:** MX${analisis_ia.get('precio_entrada_mx', 0):,.0f}")
        rango = analisis_ia.get("rango_viable_mx", {})
        if rango:
            r.append(f"- **Rango viable:** MX${rango.get('min', 0):,.0f} — MX${rango.get('max', 0):,.0f}")
        r.append(f"- **Margen estimado:** {analisis_ia.get('margen_estimado_pct', 0)}%")
        r.append(f"- **Justificación:** {analisis_ia.get('justificacion_precio', '')}")

        r.append(f"\n### Estrategia de lanzamiento")
        r.append(str(analisis_ia.get("estrategia_lanzamiento", "")))

        r.append(f"\n### Precio psicológico recomendado")
        r.append(str(analisis_ia.get("precio_psicologico", "")))

        r.append(f"\n### Oportunidades de precio")
        r.append(f"- **Segmento saturado:** {analisis_ia.get('segmento_saturado', '')}")
        r.append(f"- **Segmento con oportunidad:** {analisis_ia.get('segmento_oportunidad', '')}")

        r.append(f"\n### Insight clave")
        r.append(analisis_ia.get("insight_precio", ""))

        tokens = analisis_ia.get("_tokens", {})
        r.append(f"\n*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")

    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 5 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 5: PRECIO VS VALOR")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    df = cargar_productos(mercado)
    if df.empty:
        print(f"\n  Sin productos con precio en DB para '{mercado}'")
        return None

    print(f"\n  {len(df)} productos con precio para '{mercado}'")

    df, p25, p75 = segmentar_precios(df)
    segmentos    = analizar_segmentos(df)

    print(f"  Segmentos: {len(segmentos)}")
    for s in segmentos:
        print(f"    - {s['segmento']}: {s['num_productos']} productos, rating {s['rating_promedio']}")

    analisis_ia = analizar_con_claude(mercado, df, segmentos, p25, p75)

    if analisis_ia:
        print(f"  Claude completó análisis")
        print(f"  Precio recomendado: MX${analisis_ia.get('precio_entrada_mx', 0):,.0f}")
        print(f"  Margen estimado: {analisis_ia.get('margen_estimado_pct', 0)}%")

    reporte = generar_reporte(mercado, segmentos, analisis_ia)
    reporte_path = REPORTS_DIR / "fase3_precio_valor.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")

    print("\n  Agente de precio vs valor completado.")
    return analisis_ia


if __name__ == "__main__":
    ejecutar()

# agents/keywords.py
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


def cargar_keywords(mercado):
    engine = get_engine()
    sql = text("""
        SELECT keyword, volumen_busqueda, tendencia_30d, productos_competidores,
               cerebro_iq_score, keyword_sales, title_density, competitor_rank_avg,
               sugerido_ppc_bid, fuente
        FROM keywords
        WHERE mercado = :mercado
        ORDER BY volumen_busqueda DESC NULLS LAST
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"mercado": mercado})
    return df


# ─────────────────────────────────────────────
# BLOQUE 2 — Scoring y clasificación
# ─────────────────────────────────────────────

def clasificar_oportunidad(df):
    """Score basado en volumen, competencia y Cerebro IQ Score."""
    num_cols = ["volumen_busqueda", "productos_competidores", "cerebro_iq_score",
                "tendencia_30d", "title_density", "competitor_rank_avg"]
    df = df.copy()
    for col in num_cols:
        if col in df.columns:
            df[col] = df[col].fillna(0)

    _COLS = ["keyword", "volumen_busqueda", "competidores", "cerebro_iq_score",
             "tendencia_30d", "title_density", "competitor_rank_avg",
             "score_oportunidad", "nivel_oportunidad"]
    if df.empty:
        return pd.DataFrame(columns=_COLS)

    registros = []
    for _, row in df.iterrows():
        volumen      = row.get("volumen_busqueda") or 0
        competidores = row.get("productos_competidores") or 0
        cerebro_iq   = row.get("cerebro_iq_score") or 0
        tendencia    = row.get("tendencia_30d") or 0

        # Volumen: más es mejor pero hay punto de saturación
        if volumen >= 50000:
            s_vol = 1    # muy alta competencia probable
        elif volumen >= 10000:
            s_vol = 3
        elif volumen >= 2000:
            s_vol = 4
        else:
            s_vol = 2    # muy bajo, poca demanda

        # Competidores: menos es mejor
        if competidores == 0 or competidores > 5000:
            s_comp = 1
        elif competidores <= 200:
            s_comp = 4
        elif competidores <= 700:
            s_comp = 3
        else:
            s_comp = 2

        # Cerebro IQ Score: más es mejor (relevancia)
        if cerebro_iq >= 50000:
            s_iq = 4
        elif cerebro_iq >= 10000:
            s_iq = 3
        elif cerebro_iq >= 1000:
            s_iq = 2
        else:
            s_iq = 1

        # Tendencia: positiva suma
        s_tend = 2 if tendencia and tendencia > 0 else 1

        score = s_vol + s_comp + s_iq + s_tend

        if score >= 12:
            nivel = "Alta oportunidad"
        elif score >= 9:
            nivel = "Oportunidad media"
        else:
            nivel = "Competida"

        registros.append({
            "keyword":            row["keyword"],
            "volumen_busqueda":   int(volumen),
            "competidores":       int(competidores),
            "cerebro_iq_score":   int(cerebro_iq),
            "tendencia_30d":      round(float(tendencia), 1),
            "title_density":      row.get("title_density") or 0,
            "competitor_rank_avg": row.get("competitor_rank_avg") or 0,
            "score_oportunidad":  score,
            "nivel_oportunidad":  nivel,
        })

    return pd.DataFrame(registros).sort_values("score_oportunidad", ascending=False)


def agrupar_clusters(df, mercado):
    """Agrupa keywords en clusters semánticos detectados automáticamente."""
    palabras_clave = mercado.lower().split()

    # Clusters genéricos por intención de búsqueda
    clusters = {
        "Marca específica":   [],
        "Formato / presentación": [],
        "Ingrediente / componente": [],
        "Beneficio / función": [],
        "General / categoría": [],
    }

    marca_indicadores = ["habits", "now", "nutricost", "double wood", "life extension",
                         "nordic", "natsa", "matter", "raw", "swanson", "natrol"]
    formato_indicadores = ["gummy", "gomita", "polvo", "cápsula", "tableta", "líquido",
                           "spray", "sobres", "mg", "gr", "kg", "ml"]
    beneficio_indicadores = ["para", "apoyo", "soporte", "salud", "bienestar", "dormir",
                             "descanso", "energía", "músculo", "digestivo", "cerebro"]

    for _, row in df.iterrows():
        kw = row["keyword"].lower()
        asignado = False
        if any(m in kw for m in marca_indicadores):
            clusters["Marca específica"].append(row["keyword"])
            asignado = True
        elif any(f in kw for f in formato_indicadores):
            clusters["Formato / presentación"].append(row["keyword"])
            asignado = True
        elif any(b in kw for b in beneficio_indicadores):
            clusters["Beneficio / función"].append(row["keyword"])
            asignado = True
        elif any(p in kw for p in palabras_clave):
            clusters["Ingrediente / componente"].append(row["keyword"])
            asignado = True

        if not asignado:
            clusters["General / categoría"].append(row["keyword"])

    return {k: v for k, v in clusters.items() if v}


# ─────────────────────────────────────────────
# BLOQUE 3 — Análisis con Claude
# ─────────────────────────────────────────────

def analizar_con_claude(mercado, df_oportunidad, clusters):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    top_kw = df_oportunidad.head(15).to_dict(orient="records")
    alta_oportunidad = df_oportunidad[
        df_oportunidad["nivel_oportunidad"] == "Alta oportunidad"
    ].head(8).to_dict(orient="records")

    sin_datos = df_oportunidad.empty

    if sin_datos:
        seccion_datos = (
            "No hay keywords en la base de datos para este mercado.\n"
            "Infiere keywords desde tu conocimiento de Amazon México y el contexto acumulado.\n"
            "Genera keywords reales y específicas que un comprador mexicano usaría en Amazon MX."
        )
    else:
        seccion_datos = f"""=== TOP 15 KEYWORDS POR SCORE ===
{json.dumps(top_kw, ensure_ascii=False, indent=2)}

=== KEYWORDS DE ALTA OPORTUNIDAD ===
{json.dumps(alta_oportunidad, ensure_ascii=False, indent=2)}

=== CLUSTERS SEMÁNTICOS ===
{json.dumps({k: v for k, v in clusters.items()}, ensure_ascii=False, indent=2)}"""

    prompt = f"""Eres un experto en SEO para Amazon México especializado en {mercado}.

{contexto_previo}
Analiza la estrategia de keywords para entrar al mercado de **{mercado}** en Amazon MX.

{seccion_datos}

INSTRUCCIÓN CRÍTICA: El campo "keyword_principal" debe ser SIEMPRE una keyword real de búsqueda
(ej: "sal artesanal mexicana", "sal marina gourmet", "sal de colima sin aditivos").
NUNCA pongas advertencias, notas, comentarios ni texto entre corchetes en ese campo.

=== REGLAS DE ANÁLISIS (obligatorias) ===
1. TRAZABILIDAD: prioriza keywords usando los datos entregados (volumen, competidores,
   Cerebro IQ, score). Al recomendar una keyword como principal, justifica con su métrica
   (ej: "volumen 12,400 con solo 180 competidores = score 14").
2. ACCIONABILIDAD: keyword_principal y secundarias deben ser términos que un comprador
   mexicano realmente teclea en Amazon MX, no descripciones. Backend = términos que NO
   caben en el título pero capturan búsquedas adicionales.
3. La "oportunidad_oculta" debe apoyarse en un patrón visible en los datos (ej: keyword
   de alto volumen con baja title_density), no en intuición pura.

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "diagnostico_seo": "2-3 oraciones sobre el panorama de keywords para este mercado",
  "buyer_persona_principal": {{
    "descripcion": "quién busca estos productos según las keywords",
    "intencion_dominante": "qué quiere lograr el comprador",
    "momento_de_compra": "cuándo y por qué busca"
  }},
  "estrategia_titulo": {{
    "keyword_principal": "la keyword más importante para el título",
    "keywords_secundarias": ["keyword 2", "keyword 3"],
    "razon": "por qué esta combinación maximiza visibilidad y conversión"
  }},
  "estrategia_bullets": [
    {{
      "bullet_numero": 1,
      "keyword_a_incluir": "keyword específica",
      "intencion": "qué pain point o deseo ataca en este bullet"
    }}
  ],
  "keywords_backend_recomendadas": ["kw1", "kw2", "kw3", "kw4", "kw5"],
  "oportunidad_oculta": "keyword o patrón con alto potencial no obvio en el score",
  "advertencia_seo": "el error más común al usar estas keywords en Amazon MX"
}}

Genera exactamente 5 bullets."""

    print("  Claude analizando estrategia de keywords...")
    respuesta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2800,
        system="Eres experto en SEO Amazon México. Respondes siempre con JSON válido.",
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

    # Guardrail: si keyword_principal parece una advertencia o está vacía, usar fallback
    kw_tit = analisis.get("estrategia_titulo", {})
    kw_principal = kw_tit.get("keyword_principal", "")
    kw_invalida = (
        not kw_principal
        or kw_principal.upper().startswith("ADVERTENCIA")
        or kw_principal.startswith("[")
        or len(kw_principal) > 80
    )
    if kw_invalida:
        # Intentar rescatar de keywords_backend o usar el nombre del mercado
        backend_kws = analisis.get("keywords_backend_recomendadas", [])
        fallback = backend_kws[0] if backend_kws else mercado
        print(f"  [keywords] keyword_principal inválida — usando fallback: {fallback!r}")
        if "estrategia_titulo" not in analisis:
            analisis["estrategia_titulo"] = {}
        analisis["estrategia_titulo"]["keyword_principal"] = fallback

    escribir_memoria("keywords", {
        "keyword_principal":    analisis.get("estrategia_titulo", {}).get("keyword_principal", ""),
        "keywords_secundarias": analisis.get("estrategia_titulo", {}).get("keywords_secundarias", []),
        "buyer_persona":        analisis.get("buyer_persona_principal", {}).get("descripcion", ""),
        "intencion_dominante":  analisis.get("buyer_persona_principal", {}).get("intencion_dominante", ""),
        "oportunidad_oculta":   analisis.get("oportunidad_oculta", ""),
        "keywords_backend":     analisis.get("keywords_backend_recomendadas", []),
    })
    return analisis


# ─────────────────────────────────────────────
# BLOQUE 4 — Reporte
# ─────────────────────────────────────────────

def generar_reporte(mercado, df, df_oportunidad, clusters, analisis_ia):
    r = []
    r.append(f"# Análisis de Keywords y SEO — {mercado}\n")
    r.append(f"- Total keywords: {len(df)}")
    altas = df_oportunidad[df_oportunidad["nivel_oportunidad"] == "Alta oportunidad"]
    r.append(f"- Alta oportunidad: {len(altas)}")
    r.append(f"- Clusters semánticos: {len(clusters)}\n")

    r.append("## Keywords por score de oportunidad")
    r.append("| Keyword | Volumen | Competidores | Cerebro IQ | Tendencia | Score | Nivel |")
    r.append("|---------|---------|-------------|-----------|-----------|-------|-------|")
    for _, row in df_oportunidad.iterrows():
        r.append(
            f"| {row['keyword']} | {int(row['volumen_busqueda']):,} | {int(row['competidores']):,} | "
            f"{int(row['cerebro_iq_score']):,} | {row['tendencia_30d']:+.1f}% | "
            f"{row['score_oportunidad']} | {row['nivel_oportunidad']} |"
        )

    r.append("\n## Clusters semánticos")
    for cluster, keywords in clusters.items():
        r.append(f"\n### {cluster} ({len(keywords)})")
        for kw in keywords:
            r.append(f"- {kw}")

    if analisis_ia:
        r.append("\n---")
        r.append("## Estrategia SEO con IA (Claude)\n")
        r.append(f"**Diagnóstico:** {analisis_ia.get('diagnostico_seo', '')}\n")

        buyer = analisis_ia.get("buyer_persona_principal", {})
        if buyer:
            r.append("### Buyer persona")
            r.append(f"- **Perfil:** {buyer.get('descripcion', '')}")
            r.append(f"- **Intención:** {buyer.get('intencion_dominante', '')}")
            r.append(f"- **Momento:** {buyer.get('momento_de_compra', '')}")

        tit = analisis_ia.get("estrategia_titulo", {})
        if tit:
            r.append("\n### Estrategia de título")
            r.append(f"- **Keyword principal:** `{tit.get('keyword_principal', '')}`")
            sec = tit.get("keywords_secundarias", [])
            r.append(f"- **Secundarias:** {', '.join(f'`{k}`' for k in sec)}")
            r.append(f"- **Razón:** {tit.get('razon', '')}")

        bullets = analisis_ia.get("estrategia_bullets", [])
        if bullets:
            r.append("\n### Keywords por bullet")
            for b in bullets:
                r.append(f"\n**Bullet {b.get('bullet_numero','')}** → `{b.get('keyword_a_incluir','')}`")
                r.append(f"  *{b.get('intencion','')}*")

        backend = analisis_ia.get("keywords_backend_recomendadas", [])
        if backend:
            r.append("\n### Keywords backend")
            for kw in backend:
                r.append(f"- {kw}")

        if analisis_ia.get("oportunidad_oculta"):
            r.append(f"\n### Oportunidad oculta")
            r.append(analisis_ia["oportunidad_oculta"])

        if analisis_ia.get("advertencia_seo"):
            r.append(f"\n### Advertencia SEO")
            r.append(analisis_ia["advertencia_seo"])

        tokens = analisis_ia.get("_tokens", {})
        r.append(f"\n*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")

    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 5 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 6: KEYWORDS Y SEO")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    try:
        df = cargar_keywords(mercado)
    except Exception as e:
        print(f"  keywords DB no disponible ({type(e).__name__}) — análisis desde contexto")
        df = pd.DataFrame()

    if df.empty:
        print(f"  Sin keywords para '{mercado}' — Claude infiere desde contexto acumulado")
    else:
        print(f"\n  {len(df)} keywords cargadas para '{mercado}'")
        print(f"  Volumen top: {int(df['volumen_busqueda'].max()):,} ({df.iloc[0]['keyword']})")

    df_oportunidad = clasificar_oportunidad(df)
    clusters       = agrupar_clusters(df_oportunidad, mercado)

    altas = df_oportunidad[df_oportunidad["nivel_oportunidad"] == "Alta oportunidad"]
    print(f"  Alta oportunidad: {len(altas)} keywords")
    print(f"  Clusters: {len(clusters)}")

    analisis_ia = analizar_con_claude(mercado, df_oportunidad, clusters)

    if analisis_ia:
        print(f"  Claude completó análisis")
        tit = analisis_ia.get("estrategia_titulo", {})
        print(f"  Keyword principal: {tit.get('keyword_principal', '')}")

    reporte = generar_reporte(mercado, df, df_oportunidad, clusters, analisis_ia)
    reporte_path = REPORTS_DIR / "fase3_keywords.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")

    csv_path = OUTPUTS_DIR / "keywords_opportunity.csv"
    df_oportunidad.to_csv(csv_path, index=False, encoding="utf-8")
    print(f"  CSV guardado en: {csv_path}")

    print("\n  Agente de keywords completado.")
    return df_oportunidad


if __name__ == "__main__":
    ejecutar()

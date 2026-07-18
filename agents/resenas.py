# agents/resenas.py
import os
import json
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria

load_dotenv()

RAW_DIR     = Path("data/raw")
REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga de datos
# ─────────────────────────────────────────────

def get_engine():
    return create_engine(os.getenv("DATABASE_URL"))


def cargar_resenas(mercado):
    """Intenta cargar reseñas desde PostgreSQL. Fallback a CSV si la tabla está vacía."""
    engine = get_engine()
    sql = text("SELECT * FROM resenas WHERE mercado = :mercado")
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"mercado": mercado})

    if not df.empty:
        print(f"  Fuente: PostgreSQL ({len(df)} reseñas)")
        return df, "postgresql"

    # Fallback: SOLO CSVs cuyo nombre corresponde al mercado buscado.
    # Antes se tomaba el PRIMER CSV con columnas de reseñas sin filtrar por mercado,
    # lo que contaminaba el análisis (ej: usar reseñas de auriculares para un termo).
    from agents.ingesta import seleccionar_archivos_por_mercado
    todos = sorted(RAW_DIR.glob("*.csv"))
    candidatos = seleccionar_archivos_por_mercado(mercado, todos)

    for path in candidatos:
        try:
            tmp = pd.read_csv(path, encoding="utf-8-sig")
            tmp.columns = [c.strip().lower().replace(" ", "_") for c in tmp.columns]
            if "rating" in tmp.columns and ("cuerpo" in tmp.columns or "review" in tmp.columns or "body" in tmp.columns):
                # Normalizar nombre de columna del cuerpo
                for col in ("body", "review", "review_body"):
                    if col in tmp.columns:
                        tmp.rename(columns={col: "cuerpo"}, inplace=True)
                print(f"  Fuente: CSV ({path.name}, {len(tmp)} reseñas)")
                return tmp, "csv"
        except Exception:
            continue

    print("  Sin reseñas del mercado — análisis desde contexto (se evita usar reseñas de otra categoría)")
    return pd.DataFrame(), "vacio"


# ─────────────────────────────────────────────
# BLOQUE 2 — Análisis estadístico
# ─────────────────────────────────────────────

def separar_negativas(df):
    negativas = df[df["rating"] <= 3].copy()
    print(f"  Reseñas negativas: {len(negativas)} de {len(df)} ({round(len(negativas)/len(df)*100,1)}%)")
    return negativas


def detectar_pain_points(df_neg):
    temas = {
        "calidad_producto":  ["calidad", "malo", "falso", "engaño", "fraude", "adulterado"],
        "efectividad":       ["no funciona", "no sirve", "inefectivo", "efecto", "resultado", "funciona"],
        "sabor_olor":        ["sabor", "olor", "feo", "asqueroso", "mal gusto", "amargo"],
        "precio_valor":      ["caro", "precio", "valor", "económico", "costoso", "barato"],
        "presentacion":      ["empaque", "presentación", "frasco", "sello", "envase", "roto"],
        "envio_tiempo":      ["tardó", "llegó", "envío", "días", "retraso", "demoró"],
        "dosis_instrucciones": ["dosis", "instrucciones", "cómo tomar", "indicaciones", "confuso"],
        "efectos_secundarios": ["náuseas", "malestar", "dolor", "alergia", "efecto secundario", "reacción"],
        "autenticidad":      ["original", "copia", "falsificado", "pirata", "auténtico", "fake"],
        "servicio":          ["servicio", "atención", "responde", "soporte", "garantía", "devolución"],
    }
    from collections import Counter
    conteos = Counter()
    frases_por_tema = {t: [] for t in temas}
    for _, row in df_neg.iterrows():
        texto = str(row.get("cuerpo", "")).lower()
        for tema, palabras in temas.items():
            if any(p in texto for p in palabras):
                conteos[tema] += 1
                if len(frases_por_tema[tema]) < 3:
                    frases_por_tema[tema].append(str(row.get("cuerpo", ""))[:120])
    return conteos, frases_por_tema


def calcular_impacto(conteos, total_negativas):
    impacto = []
    for tema, count in conteos.most_common():
        pct = round(count / total_negativas * 100, 1) if total_negativas > 0 else 0
        impacto.append({
            "tema":       tema,
            "frecuencia": count,
            "porcentaje": pct,
            "prioridad":  "Alta" if pct >= 20 else "Media" if pct >= 10 else "Baja",
        })
    return impacto


# ─────────────────────────────────────────────
# BLOQUE 3 — Análisis con Claude
# ─────────────────────────────────────────────

def analizar_con_claude(mercado, fuente, df, df_neg, impacto, frases_por_tema):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    if fuente == "vacio":
        # Sin datos de reseñas: Claude analiza desde el contexto acumulado
        prompt = f"""Eres un experto en investigación de mercado para Amazon México.
Mercado analizado: **{mercado}**

{contexto_previo}
No hay datos de reseñas disponibles aún para este mercado.
Basándote en el análisis de competencia y keywords ya realizados,
infiere los pain points más probables que experimentan los clientes
en el mercado de {mercado} en Amazon México.

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "sentimiento_general": "positivo | negativo | mixto | desconocido",
  "nota": "análisis basado en contexto de competencia, sin datos directos de reseñas",
  "pain_points_criticos": [
    {{
      "tema": "nombre del tema",
      "por_que_importa": "explicación basada en el mercado",
      "nivel_frustracion": "alto | moderado | menor",
      "oportunidad": "cómo un nuevo producto puede resolver esto"
    }}
  ],
  "patrones_probables": ["patrón inferido 1", "patrón inferido 2"],
  "mensaje_emocional_del_cliente": "estado emocional probable del comprador insatisfecho",
  "top_3_mejoras_para_ganar_mercado": [
    {{"mejora": "descripción concreta", "impacto_esperado": "qué cambiaría"}}
  ],
  "insight_principal": "hallazgo más importante en 1-2 oraciones",
  "advertencia": "el error más común de los competidores según el análisis del mercado"
}}"""
    else:
        resumen_pain = [
            {"tema": item["tema"], "frecuencia": item["frecuencia"],
             "porcentaje": item["porcentaje"], "prioridad": item["prioridad"],
             "frases_reales": frases_por_tema.get(item["tema"], [])}
            for item in impacto[:8]
        ]
        stats = {
            "total_resenas":   len(df),
            "total_negativas": len(df_neg),
            "pct_negativas":   round(len(df_neg) / len(df) * 100, 1),
            "rating_promedio": round(df["rating"].mean(), 2),
        }
        prompt = f"""Eres un experto en investigación de mercado para Amazon México.
Mercado analizado: **{mercado}**

{contexto_previo}
Analiza estos pain points detectados en reseñas del mercado.

=== ESTADÍSTICAS ===
{json.dumps(stats, ensure_ascii=False, indent=2)}

=== PAIN POINTS DETECTADOS ===
{json.dumps(resumen_pain, ensure_ascii=False, indent=2)}

=== REGLAS DE ANÁLISIS (obligatorias) ===
1. TRAZABILIDAD: cada pain point crítico debe apoyarse en las frases_reales entregadas
   y en su frecuencia/porcentaje. Cita la evidencia (ej: "23% de las negativas mencionan sabor").
   No inventes pain points que no aparezcan en los datos.
2. ACCIONABILIDAD: la "oportunidad" debe ser un atributo de producto concreto que un
   fabricante pueda ejecutar, no un deseo genérico.
3. Distingue el pain point REAL (frecuente y grave) del anecdótico (una sola mención).
   Prioriza por frecuencia × gravedad, no por lo llamativo.

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "sentimiento_general": "positivo | negativo | mixto",
  "pain_points_criticos": [
    {{
      "tema": "nombre",
      "por_que_importa": "explicación en 1 oración",
      "evidencia": "frase real o cifra (% de negativas) que respalda este pain point",
      "nivel_frustracion": "bloqueante | alto | moderado | menor",
      "oportunidad": "atributo de producto concreto que resuelve esto"
    }}
  ],
  "patrones_ocultos": ["patrón que el conteo de palabras no captura"],
  "mensaje_emocional_del_cliente": "estado emocional del comprador insatisfecho",
  "top_3_mejoras_para_ganar_mercado": [
    {{"mejora": "descripción concreta", "impacto_esperado": "qué cambiaría en las reseñas"}}
  ],
  "insight_principal": "hallazgo más importante en 1-2 oraciones",
  "advertencia": "el error más común de los competidores según estas reseñas"
}}"""

    print("  Claude analizando reseñas...")
    respuesta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        system="Eres analista de mercado Amazon México. Respondes siempre con JSON válido.",
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

    escribir_memoria("resenas", {
        "sentimiento_general":  analisis.get("sentimiento_general", "desconocido"),
        "pain_points_criticos": [pp["tema"] for pp in analisis.get("pain_points_criticos", [])],
        "insight_principal":    analisis.get("insight_principal", ""),
        "mensaje_emocional":    analisis.get("mensaje_emocional_del_cliente", ""),
        "top_3_mejoras":        [m["mejora"] for m in analisis.get("top_3_mejoras_para_ganar_mercado", [])],
        "fuente_datos":         fuente,
    })
    return analisis


# ─────────────────────────────────────────────
# BLOQUE 4 — Reporte
# ─────────────────────────────────────────────

def generar_reporte(mercado, fuente, df, df_neg, impacto, analisis_ia):
    r = []
    r.append(f"# Análisis de Reseñas — {mercado}\n")
    r.append(f"*Fuente de datos: {fuente}*\n")

    if fuente != "vacio":
        r.append("## Resumen general")
        r.append(f"- Total reseñas: {len(df)}")
        r.append(f"- Reseñas negativas (1-3 ★): {len(df_neg)} ({round(len(df_neg)/len(df)*100,1)}%)")
        r.append(f"- Rating promedio: {round(df['rating'].mean(), 2)}\n")

        r.append("## Pain points por frecuencia")
        r.append("| Tema | Frecuencia | % | Prioridad |")
        r.append("|------|-----------|---|-----------|")
        for item in impacto:
            r.append(f"| {item['tema']} | {item['frecuencia']} | {item['porcentaje']}% | {item['prioridad']} |")
        r.append("")

    if analisis_ia:
        r.append("---")
        r.append("## Análisis con Inteligencia Artificial (Claude)\n")
        r.append(f"**Sentimiento general:** `{analisis_ia.get('sentimiento_general', '')}`\n")

        r.append("### Pain points críticos")
        for pp in analisis_ia.get("pain_points_criticos", []):
            r.append(f"\n**{pp['tema'].upper()}** — Frustración: `{pp.get('nivel_frustracion','')}`")
            r.append(f"- Por qué importa: {pp.get('por_que_importa','')}")
            if pp.get("evidencia"):
                r.append(f"- Evidencia: _{pp['evidencia']}_")
            r.append(f"- Oportunidad: {pp.get('oportunidad','')}")

        r.append("\n### Patrones")
        for p in analisis_ia.get("patrones_ocultos", analisis_ia.get("patrones_probables", [])):
            r.append(f"- {p}")

        r.append(f"\n### Estado emocional del cliente")
        r.append(analisis_ia.get("mensaje_emocional_del_cliente", ""))

        r.append("\n### Top 3 mejoras para ganar mercado")
        for i, m in enumerate(analisis_ia.get("top_3_mejoras_para_ganar_mercado", []), 1):
            r.append(f"\n{i}. **{m['mejora']}**")
            r.append(f"   → {m['impacto_esperado']}")

        r.append(f"\n### Insight principal")
        r.append(analisis_ia.get("insight_principal", ""))

        r.append(f"\n### Advertencia sobre competidores")
        r.append(analisis_ia.get("advertencia", ""))

        tokens = analisis_ia.get("_tokens", {})
        r.append(f"\n*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")

    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 5 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 3: ANÁLISIS DE RESEÑAS")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    df, fuente = cargar_resenas(mercado)

    impacto = []
    frases_por_tema = {}
    df_neg = pd.DataFrame()

    if fuente != "vacio":
        print(f"  {len(df)} reseñas cargadas")
        df_neg          = separar_negativas(df)
        conteos, frases = detectar_pain_points(df_neg)
        frases_por_tema = frases
        impacto         = calcular_impacto(conteos, len(df_neg))

        if impacto:
            print(f"  Top 3 pain points:")
            for item in impacto[:3]:
                print(f"    - {item['tema']}: {item['frecuencia']} menciones ({item['porcentaje']}%)")

        # Guardar CSV de pain points para agentes posteriores
        if impacto:
            pd.DataFrame(impacto).to_csv(
                OUTPUTS_DIR / "pain_points_ranked.csv", index=False, encoding="utf-8"
            )

    analisis_ia = analizar_con_claude(mercado, fuente, df, df_neg, impacto, frases_por_tema)

    if analisis_ia:
        print(f"  Claude completó el análisis ({fuente})")
        print(f"  Insight: {str(analisis_ia.get('insight_principal',''))[:80]}...")

    reporte = generar_reporte(mercado, fuente, df, df_neg, impacto, analisis_ia)
    reporte_path = REPORTS_DIR / "fase2_resenas.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")
    print("\n  Agente de reseñas completado.")
    return impacto or analisis_ia


if __name__ == "__main__":
    ejecutar()

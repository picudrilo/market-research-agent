# agents/gap_analysis.py
import json
import pandas as pd
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria, parsear_json_claude

load_dotenv()

REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga de contexto
# ─────────────────────────────────────────────

def cargar_pain_points():
    """Lee pain_points_ranked.csv si existe (generado por resenas.py)."""
    path = OUTPUTS_DIR / "pain_points_ranked.csv"
    if path.exists():
        df = pd.read_csv(path)
        print(f"  Pain points CSV: {len(df)} temas")
        return df.to_dict(orient="records")
    print("  Pain points CSV: no disponible (se usará contexto de memoria)")
    return []


def cargar_competidores():
    """Lee competidores_ranking.csv si existe (generado por competencia.py)."""
    path = OUTPUTS_DIR / "competidores_ranking.csv"
    if path.exists():
        df = pd.read_csv(path)
        cols = ["asin", "marca", "precio", "bsr", "reviews_count", "rating",
                "ventas_mensuales_asin", "revenue_mensual_asin", "fba", "fuente"]
        cols_disp = [c for c in cols if c in df.columns]
        print(f"  Competidores CSV: {len(df)} productos")
        return df[cols_disp].head(20).fillna(0).to_dict(orient="records")
    print("  Competidores CSV: no disponible (se usará contexto de memoria)")
    return []


# ─────────────────────────────────────────────
# BLOQUE 2 — GAP analysis con Claude
# ─────────────────────────────────────────────

def _retry_gap(client, mercado, pain_points, competidores):
    prompt = f"""Identifica gaps de mercado para: {mercado}
Pain points: {json.dumps((pain_points or [])[:5], ensure_ascii=False)[:500]}
Competidores: {json.dumps((competidores or [])[:5], ensure_ascii=False)[:500]}

Responde ÚNICAMENTE con este JSON válido:
{{
  "gap_mas_critico": "área y razón en 1 oración",
  "combinacion_ganadora": "2-3 atributos que ningún competidor combina hoy",
  "gaps": [
    {{"area": "nombre", "problema_cliente": "qué experimentan",
      "cobertura_mercado": "qué hacen o no hacen los competidores",
      "oportunidad": "qué debería ofrecer un producto nuevo",
      "impacto": "Alto", "facilidad": "Media", "evidencia": "dato concreto"}}
  ],
  "resumen_mercado": "estado actual en 2 oraciones"
}}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=2000,
            system="Responde SOLO con JSON válido.",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado = parsear_json_claude(resp.content[0].text, "gap_retry")
        if resultado.get("gap_mas_critico"):
            print("  [gap_analysis] Retry exitoso.")
        return resultado
    except Exception as e:
        print(f"  [gap_analysis] Retry fallido: {e}")
        return {}


def analizar_gaps_con_claude(mercado, pain_points, competidores):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    prompt = f"""Eres un experto en investigación de mercado para Amazon México.
Mercado: **{mercado}**

{contexto_previo}
Cruza los pain points de clientes con las características de los competidores.
Identifica exactamente dónde el mercado falla y dónde existe oportunidad real.

=== PAIN POINTS DE CLIENTES ===
{json.dumps(pain_points, ensure_ascii=False, indent=2) if pain_points else "Ver contexto acumulado de agentes anteriores."}

=== DATOS DE COMPETIDORES ===
{json.dumps(competidores, ensure_ascii=False, indent=2) if competidores else "Ver contexto acumulado de agentes anteriores."}

=== REGLAS DE ANÁLISIS (obligatorias) ===
1. TRAZABILIDAD: cada gap debe apoyarse en un dato concreto — cita el pain point
   exacto (con su frecuencia si la tienes) o la métrica del competidor que lo evidencia.
   No inventes gaps sin sustento en los datos entregados.
2. ACCIONABILIDAD: el campo "oportunidad" debe describir un producto/atributo concreto
   y ejecutable, no un deseo genérico. Debe poder convertirse en una decisión de compra.
3. Si un gap se basa solo en intuición y no en los datos, no lo incluyas.

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "resumen_mercado": "2-3 oraciones sobre el estado actual y la oportunidad, citando cifras",
  "gaps": [
    {{
      "area": "nombre del área de oportunidad",
      "problema_cliente": "qué experimentan los clientes exactamente",
      "cobertura_mercado": "qué hacen o NO hacen los competidores en esta área",
      "oportunidad": "qué debería ofrecer un nuevo producto, concreto y ejecutable",
      "impacto": "Alto | Medio | Bajo",
      "facilidad": "Alta | Media | Baja",
      "evidencia": "pain point o métrica específica que respalda este gap (con número si existe)"
    }}
  ],
  "gap_mas_critico": "área con mayor oportunidad y por qué en 1 oración, citando el dato",
  "combinacion_ganadora": "combinación de 2-3 atributos que ningún competidor tiene juntos"
}}

Genera entre 5 y 8 gaps ordenados de mayor a menor oportunidad."""

    print("  Claude analizando gaps de mercado...")
    respuesta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=5000,
        system="Eres analista de mercado Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = respuesta.content[0].text
    resultado = parsear_json_claude(texto, "gap_analysis")
    resultado["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    if not resultado.get("gap_mas_critico"):
        print("  [gap_analysis] gap_mas_critico vacío — reintentando con prompt simplificado...")
        resultado = _retry_gap(client, mercado, pain_points, competidores) or resultado

    hallazgos = {
        "gap_mas_critico":      resultado.get("gap_mas_critico", ""),
        "combinacion_ganadora": resultado.get("combinacion_ganadora", ""),
        "top_gaps":             [g["area"] for g in resultado.get("gaps", [])[:5]],
        "resumen_mercado":      resultado.get("resumen_mercado", ""),
    }

    campos_vacios = [k for k, v in hallazgos.items() if not v]
    if campos_vacios:
        print(f"  [gap_analysis] ADVERTENCIA: campos vacíos en memoria: {campos_vacios}")
    else:
        print(f"  [gap_analysis] Memoria OK — gap_critico={hallazgos['gap_mas_critico'][:60]}")

    escribir_memoria("gap_analysis", hallazgos)
    return resultado


# ─────────────────────────────────────────────
# BLOQUE 3 — Score y reporte
# ─────────────────────────────────────────────

def calcular_score(gaps):
    mapa_impacto   = {"Alto": 3, "Medio": 2, "Bajo": 1}
    mapa_facilidad = {"Alta": 3, "Media": 2, "Baja": 1}
    for gap in gaps:
        gap["score"] = (
            mapa_impacto.get(gap.get("impacto", "Medio"), 2) +
            mapa_facilidad.get(gap.get("facilidad", "Media"), 2)
        )
    return sorted(gaps, key=lambda x: x["score"], reverse=True)


def generar_reporte(mercado, gaps, resultado_ia):
    r = []
    r.append(f"# GAP Analysis — {mercado}\n")

    if resultado_ia.get("resumen_mercado"):
        r.append("## Resumen ejecutivo")
        r.append(resultado_ia["resumen_mercado"])
        r.append("")

    r.append(f"## Gaps identificados: {len(gaps)}")
    altos = [g for g in gaps if g.get("impacto") == "Alto"]
    r.append(f"- Impacto alto: {len(altos)} | Impacto medio/bajo: {len(gaps) - len(altos)}\n")

    r.append("## Priorización")
    r.append("| # | Área | Impacto | Facilidad | Score |")
    r.append("|---|------|---------|-----------|-------|")
    for i, gap in enumerate(gaps, 1):
        r.append(f"| {i} | {gap['area']} | {gap.get('impacto','—')} | {gap.get('facilidad','—')} | {gap['score']} |")

    r.append("\n## Detalle de gaps\n")
    for i, gap in enumerate(gaps, 1):
        r.append(f"### GAP {i}: {gap.get('area', f'Gap {i}')}")
        r.append(f"- **Problema:** {gap.get('problema_cliente', '')}")
        cobertura = gap.get('cobertura_mercado') or gap.get('oportunidad', '')
        r.append(f"- **Mercado actual:** {cobertura}")
        r.append(f"- **Oportunidad:** {gap.get('oportunidad', '')}")
        if gap.get("evidencia"):
            r.append(f"- **Evidencia:** {gap['evidencia']}")
        r.append(f"- Impacto: `{gap.get('impacto','—')}` | Facilidad: `{gap.get('facilidad','—')}` | Score: **{gap['score']}**\n")

    r.append("---")
    if resultado_ia.get("gap_mas_critico"):
        r.append(f"## Gap más crítico\n{resultado_ia['gap_mas_critico']}\n")
    if resultado_ia.get("combinacion_ganadora"):
        r.append(f"## Combinación ganadora\n{resultado_ia['combinacion_ganadora']}\n")

    tokens = resultado_ia.get("_tokens", {})
    r.append(f"*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")
    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 4 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 4: GAP ANALYSIS")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    pain_points  = cargar_pain_points()
    competidores = cargar_competidores()

    resultado_ia = analizar_gaps_con_claude(mercado, pain_points, competidores)
    if not resultado_ia:
        print("  Claude no pudo analizar")
        return None

    gaps = calcular_score(resultado_ia.get("gaps", []))

    print(f"\n  {len(gaps)} gaps identificados")
    for gap in gaps[:3]:
        print(f"    - {gap['area']} (score {gap['score']}): {gap['oportunidad'][:60]}...")

    reporte = generar_reporte(mercado, gaps, resultado_ia)
    reporte_path = REPORTS_DIR / "fase3_gap_analysis.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")

    if gaps:
        pd.DataFrame(gaps).to_csv(OUTPUTS_DIR / "gap_opportunities.csv", index=False, encoding="utf-8")
    else:
        (OUTPUTS_DIR / "gap_opportunities.csv").unlink(missing_ok=True)
    print("\n  Agente de GAP analysis completado.")
    return gaps


if __name__ == "__main__":
    ejecutar()

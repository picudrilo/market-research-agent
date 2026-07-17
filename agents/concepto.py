# agents/concepto.py
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
# BLOQUE 1 — Carga de hallazgos desde outputs
# ─────────────────────────────────────────────

def cargar_hallazgos():
    hallazgos = {}
    archivos = {
        "pain_points":  OUTPUTS_DIR / "pain_points_ranked.csv",
        "gaps":         OUTPUTS_DIR / "gap_opportunities.csv",
        "keywords":     OUTPUTS_DIR / "keywords_opportunity.csv",
        "competidores": OUTPUTS_DIR / "competidores_ranking.csv",
    }
    for nombre, path in archivos.items():
        if path.exists():
            try:
                hallazgos[nombre] = pd.read_csv(path)
                print(f"  {nombre}: {len(hallazgos[nombre])} registros")
            except pd.errors.EmptyDataError:
                hallazgos[nombre] = pd.DataFrame()
                print(f"  {nombre}: vacío")
    return hallazgos


# ─────────────────────────────────────────────
# BLOQUE 2 — Concepto con Claude
# ─────────────────────────────────────────────

def _retry_concepto(client, mercado, precio_info):
    prompt = f"""Crea el concepto de diferenciación para un producto nuevo en: {mercado}
{precio_info}

Responde ÚNICAMENTE con este JSON válido:
{{
  "nombre_concepto": "nombre del producto o marca sugerida",
  "tagline": "tagline corto que refleje el diferenciador",
  "precio_objetivo_mx": 0.0,
  "posicionamiento": "2-3 oraciones sobre por qué gana en este mercado",
  "mensaje_central": "frase de 1 línea para el cliente",
  "segmento_objetivo": "descripción del cliente ideal",
  "atributos_diferenciadores": [
    {{"atributo": "descripción", "justificacion": "por qué importa para el cliente"}}
  ]
}}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=800,
            system="Responde SOLO con JSON válido.",
            messages=[{"role": "user", "content": prompt}]
        )
        resultado = parsear_json_claude(resp.content[0].text, "concepto_retry")
        if resultado.get("nombre_concepto"):
            print("  [concepto] Retry exitoso.")
        return resultado
    except Exception as e:
        print(f"  [concepto] Retry fallido: {e}")
        return {}


def definir_propuesta_valor(mercado, hallazgos):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    pain = hallazgos.get("pain_points", pd.DataFrame())
    gaps = hallazgos.get("gaps", pd.DataFrame())
    kw   = hallazgos.get("keywords", pd.DataFrame())

    pain_data = pain.head(8).to_dict(orient="records") if not pain.empty else []
    gaps_data = gaps.head(6).to_dict(orient="records") if not gaps.empty else []
    kw_data   = kw["keyword"].head(10).tolist() if not kw.empty else []

    # Tomar precio sugerido de memoria si está disponible
    precio_info = ""
    try:
        from agents.memoria import leer_memoria
        mem = leer_memoria()
        pv = mem.get("precio_valor", {}).get("hallazgos", {})
        if pv.get("precio_entrada_mx"):
            precio_info = f"Precio de entrada recomendado: MX${pv['precio_entrada_mx']:,.0f}"
            rango = pv.get("rango_viable_mx", {})
            if rango:
                precio_info += f" (rango MX${rango.get('min',0):,.0f}–MX${rango.get('max',0):,.0f})"
    except Exception:
        pass

    prompt = f"""Eres un experto en desarrollo de productos y estrategia de e-commerce en Amazon México.
Mercado objetivo: **{mercado}**
{precio_info}

{contexto_previo}
Basándote EXCLUSIVAMENTE en los hallazgos de la investigación de mercado, crea el concepto
de diferenciación para un NUEVO producto que va a entrar a competir en este mercado.

=== PAIN POINTS DEL MERCADO ===
{json.dumps(pain_data, ensure_ascii=False, indent=2) if pain_data else "Ver contexto acumulado."}

=== GAPS DE MERCADO ===
{json.dumps(gaps_data, ensure_ascii=False, indent=2) if gaps_data else "Ver contexto acumulado."}

=== KEYWORDS CLAVE ===
{json.dumps(kw_data, ensure_ascii=False)}

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "nombre_concepto": "nombre del producto o marca sugerida",
  "tagline": "tagline corto que refleje el diferenciador principal",
  "precio_objetivo_mx": 0.0,
  "segmento_objetivo": "descripción del cliente ideal basada en los pain points reales",
  "posicionamiento": "2-3 oraciones que expliquen por qué este producto gana en este mercado",
  "problemas_que_resuelve": ["problema 1", "problema 2", "problema 3"],
  "gaps_que_aprovecha": ["gap 1", "gap 2", "gap 3"],
  "keywords_principales": ["keyword 1", "keyword 2", "keyword 3", "keyword 4", "keyword 5"],
  "atributos_diferenciadores": [
    {{
      "atributo": "descripción del atributo",
      "justificacion": "por qué responde a un gap o pain point real del mercado"
    }}
  ],
  "atributos_a_evitar": [
    {{
      "evitar": "qué no hacer",
      "razon": "error de competidores que justifica evitarlo"
    }}
  ],
  "ventaja_vs_competencia": {{
    "vs_economicos": "qué tiene este producto que los económicos no pueden dar",
    "vs_medio": "por qué gana en relación precio-valor",
    "vs_premium": "por qué es suficientemente bueno a menor costo"
  }},
  "mensaje_central": "la frase de 1 línea que resume el concepto para el cliente"
}}

Genera exactamente 6-8 atributos diferenciadores con justificación en datos."""

    print("  Claude generando concepto de diferenciación...")
    respuesta = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=4500,
        system="Eres experto en product strategy Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = next((b.text for b in respuesta.content if b.type == "text"), "")
    propuesta = parsear_json_claude(texto, "concepto")
    propuesta["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    if not propuesta.get("nombre_concepto"):
        print("  [concepto] nombre_concepto vacío — reintentando con prompt simplificado...")
        propuesta = _retry_concepto(client, mercado, precio_info) or propuesta

    hallazgos = {
        "nombre_concepto":    propuesta.get("nombre_concepto", ""),
        "tagline":            propuesta.get("tagline", ""),
        "posicionamiento":    propuesta.get("posicionamiento", ""),
        "mensaje_central":    propuesta.get("mensaje_central", ""),
        "segmento_objetivo":  propuesta.get("segmento_objetivo", ""),
        "precio_objetivo_mx": propuesta.get("precio_objetivo_mx", 0),
        "atributos_top3": [
            a.get("atributo", "") if isinstance(a, dict) else str(a)
            for a in propuesta.get("atributos_diferenciadores", [])[:3]
        ],
    }

    campos_vacios = [k for k, v in hallazgos.items() if not v]
    if campos_vacios:
        print(f"  [concepto] ADVERTENCIA: campos vacíos en memoria: {campos_vacios}")
    else:
        print(f"  [concepto] Memoria OK — concepto={hallazgos['nombre_concepto']}")

    escribir_memoria("concepto", hallazgos)
    return propuesta


# ─────────────────────────────────────────────
# BLOQUE 3 — Reporte
# ─────────────────────────────────────────────

def generar_reporte(mercado, propuesta):
    r = []
    r.append(f"# Concepto de Diferenciación — {mercado}\n")

    r.append("## Concepto del producto")
    r.append(f"- **Nombre:** {propuesta.get('nombre_concepto', '')}")
    r.append(f"- **Tagline:** {propuesta.get('tagline', '')}")
    r.append(f"- **Precio objetivo:** MX${propuesta.get('precio_objetivo_mx', 0):,.0f}")
    r.append(f"- **Segmento:** {propuesta.get('segmento_objetivo', '')}\n")

    r.append("## Posicionamiento")
    r.append(propuesta.get("posicionamiento", "") + "\n")

    r.append("## Mensaje central")
    r.append(f"> {propuesta.get('mensaje_central', '')}\n")

    r.append("## Problemas que resuelve")
    for p in propuesta.get("problemas_que_resuelve", []):
        r.append(f"- {p}")

    r.append("\n## Gaps que aprovecha")
    for g in propuesta.get("gaps_que_aprovecha", []):
        r.append(f"- {g}")

    r.append("\n## Atributos diferenciadores")
    for i, a in enumerate(propuesta.get("atributos_diferenciadores", []), 1):
        if isinstance(a, dict):
            r.append(f"\n{i}. **{a.get('atributo', '')}**")
            r.append(f"   → *{a.get('justificacion', '')}*")

    r.append("\n## Qué evitar")
    for a in propuesta.get("atributos_a_evitar", []):
        if isinstance(a, dict):
            r.append(f"- **{a.get('evitar', '')}** — {a.get('razon', '')}")

    r.append("\n## Ventaja vs cada segmento")
    for seg, v in propuesta.get("ventaja_vs_competencia", {}).items():
        r.append(f"- **{seg.replace('_', ' ').title()}:** {v}")

    r.append("\n## Keywords principales")
    for kw in propuesta.get("keywords_principales", []):
        r.append(f"- {kw}")

    tokens = propuesta.get("_tokens", {})
    r.append(f"\n*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")
    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 4 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 7: CONCEPTO DE DIFERENCIACIÓN")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    print("\n  Cargando hallazgos de agentes anteriores...")
    hallazgos = cargar_hallazgos()

    propuesta = definir_propuesta_valor(mercado, hallazgos)
    if not propuesta:
        return None

    print(f"\n  Concepto: {propuesta.get('nombre_concepto', '')}")
    print(f"  Tagline: {propuesta.get('tagline', '')}")
    print(f"  Mensaje: {str(propuesta.get('mensaje_central', ''))[:80]}...")

    reporte = generar_reporte(mercado, propuesta)
    reporte_path = REPORTS_DIR / "fase4_concepto_diferenciacion.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")
    print("\n  Agente de concepto completado.")
    return propuesta


if __name__ == "__main__":
    ejecutar()

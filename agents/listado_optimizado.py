# agents/listado_optimizado.py
import json
import pandas as pd
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria, leer_memoria

load_dotenv()

REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga de contexto
# ─────────────────────────────────────────────

def cargar_hallazgos():
    hallazgos = {}
    archivos = {
        "pain_points":  OUTPUTS_DIR / "pain_points_ranked.csv",
        "gaps":         OUTPUTS_DIR / "gap_opportunities.csv",
        "keywords":     OUTPUTS_DIR / "keywords_opportunity.csv",
    }
    for nombre, path in archivos.items():
        if path.exists():
            hallazgos[nombre] = pd.read_csv(path)
            print(f"  {nombre}: {len(hallazgos[nombre])} registros")
    return hallazgos


def cargar_concepto_md():
    path = REPORTS_DIR / "fase4_concepto_diferenciacion.md"
    if path.exists():
        contenido = path.read_text(encoding="utf-8")
        print(f"  concepto: cargado ({len(contenido)} caracteres)")
        return contenido
    print("  concepto: no encontrado, usando memoria")
    return None


# ─────────────────────────────────────────────
# BLOQUE 2 — Listing con Claude
# ─────────────────────────────────────────────

def generar_listing_con_claude(mercado, hallazgos, concepto_md):
    client = Anthropic()
    contexto_previo = obtener_contexto_para_claude()

    # Extraer datos de memoria para el precio
    mem = leer_memoria()
    concepto_mem = mem.get("concepto", {}).get("hallazgos", {})
    precio_valor_mem = mem.get("precio_valor", {}).get("hallazgos", {})

    nombre_producto = concepto_mem.get("nombre_concepto", mercado)
    precio_objetivo = concepto_mem.get("precio_objetivo_mx") or precio_valor_mem.get("precio_entrada_mx", 0)
    tagline         = concepto_mem.get("tagline", "")

    pain = hallazgos.get("pain_points", pd.DataFrame())
    gaps = hallazgos.get("gaps", pd.DataFrame())
    kw   = hallazgos.get("keywords", pd.DataFrame())

    pain_top = pain.head(5).to_dict(orient="records") if not pain.empty else []
    gaps_top = gaps.head(5).to_dict(orient="records") if not gaps.empty else []
    kw_alta  = kw["keyword"].head(8).tolist() if not kw.empty else []

    prompt = f"""Eres un experto en copywriting para Amazon con especialización en Amazon México.
Mercado: **{mercado}**
Producto: **{nombre_producto}**
Tagline: {tagline}
Precio objetivo: MX${precio_objetivo:,.0f}

{contexto_previo}
Escribe el listing completo de Amazon para este producto basándote en toda la investigación.

REGLAS:
- Título: máximo 200 caracteres, keyword principal al inicio
- 5 bullets: máximo 255 caracteres cada uno, MAYÚSCULAS al inicio, emoji al inicio
- Descripción: 1500-2000 caracteres, narrativa problema → solución
- Cada bullet resuelve un pain point específico de los datos
- NO hacer claims falsos — solo atributos respaldados por el mercado

=== CONCEPTO DEL PRODUCTO ===
{concepto_md[:2000] if concepto_md else "Ver contexto acumulado en memoria."}

=== TOP 5 PAIN POINTS ===
{json.dumps(pain_top, ensure_ascii=False, indent=2) if pain_top else "Ver contexto acumulado."}

=== TOP 5 GAPS ===
{json.dumps(gaps_top, ensure_ascii=False, indent=2) if gaps_top else "Ver contexto acumulado."}

=== KEYWORDS PRIORITARIAS ===
{json.dumps(kw_alta, ensure_ascii=False)}

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "titulo": "título completo del listing (máx 200 caracteres)",
  "bullets": [
    {{
      "numero": 1,
      "texto": "BULLET completo con emoji al inicio (máx 255 caracteres)",
      "pain_point_que_resuelve": "qué problema del mercado ataca"
    }}
  ],
  "descripcion": "descripción completa de 1500-2000 caracteres",
  "terminos_backend": ["keyword 1", "keyword 2", "keyword 3", "keyword 4", "keyword 5"],
  "estrategia_imagenes": [
    {{
      "posicion": 1,
      "tipo": "tipo de imagen",
      "descripcion": "qué mostrar y por qué convierte",
      "elemento_clave": "el detalle visual más importante"
    }}
  ],
  "recomendacion_precio": {{
    "precio_lanzamiento_mx": 0.0,
    "precio_objetivo_mx": 0.0,
    "justificacion": "estrategia de precio para este mercado"
  }},
  "riesgos": ["riesgo 1", "riesgo 2", "riesgo 3"],
  "proximos_pasos": ["paso 1", "paso 2", "paso 3", "paso 4", "paso 5"]
}}

Genera exactamente 5 bullets y 7 imágenes."""

    print("  Claude escribiendo el listing optimizado...")
    respuesta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system="Eres copywriter experto en Amazon México. Respondes siempre con JSON válido.",
        messages=[{"role": "user", "content": prompt}]
    )

    texto = respuesta.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    try:
        listing = json.loads(texto)
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fin    = texto.rfind("}") + 1
        listing = json.loads(texto[inicio:fin]) if inicio != -1 else {}

    listing["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    precio_rec = listing.get("recomendacion_precio", {})
    escribir_memoria("listado_optimizado", {
        "titulo":                listing.get("titulo", ""),
        "precio_lanzamiento_mx": precio_rec.get("precio_lanzamiento_mx", 0),
        "precio_objetivo_mx":    precio_rec.get("precio_objetivo_mx", 0),
        "top_3_bullets":         [b.get("texto", "")[:80] for b in listing.get("bullets", [])[:3]],
        "terminos_backend":      listing.get("terminos_backend", []),
    })
    return listing, nombre_producto


# ─────────────────────────────────────────────
# BLOQUE 3 — Reporte final
# ─────────────────────────────────────────────

def generar_reporte(mercado, nombre_producto, listing):
    r = []
    r.append(f"# Listado Optimizado — {mercado}\n")
    r.append(f"**Producto:** {nombre_producto}\n")

    titulo = listing.get("titulo", "")
    r.append("## Título")
    r.append(f"```\n{titulo}\n```")
    r.append(f"*{len(titulo)} caracteres*\n")

    r.append("## Bullet points")
    for b in listing.get("bullets", []):
        r.append(f"\n**Bullet {b.get('numero', '')}:**")
        r.append(b.get("texto", ""))
        if b.get("pain_point_que_resuelve"):
            r.append(f"*↳ Resuelve: {b['pain_point_que_resuelve']}*")

    desc = listing.get("descripcion", "")
    r.append(f"\n## Descripción\n{desc}")
    r.append(f"\n*{len(desc)} caracteres*")

    r.append("\n## Términos backend")
    for t in listing.get("terminos_backend", []):
        r.append(f"- {t}")

    r.append("\n## Estrategia de imágenes")
    r.append("| # | Tipo | Descripción | Elemento clave |")
    r.append("|---|------|-------------|----------------|")
    for img in listing.get("estrategia_imagenes", []):
        desc_corta = str(img.get("descripcion", ""))[:50]
        r.append(
            f"| {img.get('posicion','—')} | {img.get('tipo','—')} | "
            f"{desc_corta}... | {img.get('elemento_clave','—')} |"
        )

    precio = listing.get("recomendacion_precio", {})
    r.append("\n## Estrategia de precio")
    r.append(f"- **Precio de lanzamiento:** MX${precio.get('precio_lanzamiento_mx', 0):,.0f}")
    r.append(f"- **Precio objetivo:** MX${precio.get('precio_objetivo_mx', 0):,.0f}")
    r.append(f"- **Justificación:** {precio.get('justificacion', '')}")

    r.append("\n## Riesgos")
    for riesgo in listing.get("riesgos", []):
        r.append(f"- {riesgo}")

    r.append("\n## Próximos pasos")
    for i, paso in enumerate(listing.get("proximos_pasos", []), 1):
        r.append(f"{i}. {paso}")

    tokens = listing.get("_tokens", {})
    r.append(f"\n*Tokens: {tokens.get('entrada',0)} entrada / {tokens.get('salida',0)} salida*")
    return "\n".join(r)


# ─────────────────────────────────────────────
# BLOQUE 4 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="suplementos"):
    print("\n" + "="*50)
    print("AGENTE 8: LISTADO OPTIMIZADO")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    print("\n  Cargando hallazgos...")
    hallazgos   = cargar_hallazgos()
    concepto_md = cargar_concepto_md()

    listing, nombre_producto = generar_listing_con_claude(mercado, hallazgos, concepto_md)
    if not listing:
        print("  No se pudo generar el listing")
        return None

    titulo   = listing.get("titulo", "")
    bullets  = listing.get("bullets", [])
    imagenes = listing.get("estrategia_imagenes", [])

    print(f"\n  Título: {len(titulo)} caracteres")
    print(f"  Bullets: {len(bullets)} | Imágenes: {len(imagenes)}")
    print(f"\n  Título: {titulo[:80]}...")

    reporte = generar_reporte(mercado, nombre_producto, listing)
    reporte_path = REPORTS_DIR / "fase5_listado_optimizado.md"
    reporte_path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {reporte_path}")

    json_path = OUTPUTS_DIR / "final_recommendation.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(listing, f, ensure_ascii=False, indent=2)
    print(f"  JSON guardado en: {json_path}")

    print("\n" + "="*50)
    print(f"  PIPELINE COMPLETO — {mercado}")
    print("="*50)
    print("\n  Agente de listado completado.")
    return listing


if __name__ == "__main__":
    ejecutar()

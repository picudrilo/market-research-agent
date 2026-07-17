# agents/validador.py
import json
import re
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv
from agents.memoria import obtener_contexto_para_claude, escribir_memoria, leer_memoria

load_dotenv()
REPORTS_DIR = Path("reports")


def extraer_asin(url: str) -> str:
    m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', url or "")
    return m.group(1) if m else ""


def analizar_arbitraje(producto, precio_compra, unidades=1,
                       url_amazon="", precio_amazon=0, ventas_mes=0):
    client = Anthropic()
    contexto = obtener_contexto_para_claude()
    mem = leer_memoria()

    asin = extraer_asin(url_amazon)

    # Precio de referencia: dato real del usuario > análisis de mercado
    listing_mem  = mem.get("listado_optimizado", {}).get("hallazgos", {})
    precio_mercado = listing_mem.get("precio_objetivo_mx", 0)
    precio_venta_base = precio_amazon if precio_amazon > 0 else precio_mercado

    precio_valor_mem = mem.get("precio_valor", {}).get("hallazgos", {})
    margen_pct = precio_valor_mem.get("margen_estimado_pct", 30)

    inversion_total = precio_compra * unidades

    # Bloque de velocidad de ventas
    if ventas_mes > 0:
        dias_liquidar = round((unidades / ventas_mes) * 30)
        velocidad_txt = (
            f"VENTAS EN AMAZON: {ventas_mes} unidades/mes\n"
            f"Con {unidades} uds y Buy Box activo: liquidación en ~{dias_liquidar} días.\n"
            f"Sin Buy Box (escenario realista de arbitraje): multiplicar por 3-5x."
        )
    else:
        velocidad_txt = "Velocidad de ventas: no proporcionada."

    prompt = f"""Eres un experto en arbitraje de productos para Amazon México.
El vendedor evalúa si conviene comprar un producto específico para revenderlo en Amazon MX.

═══ PRODUCTO A ANALIZAR ═══
Nombre: {producto}
ASIN: {asin if asin else "No identificado"}
URL Amazon MX: {url_amazon if url_amazon else "No proporcionada"}
Precio de compra (por unidad): MX${precio_compra:,.2f}
Unidades a comprar: {unidades}
Inversión total: MX${inversion_total:,.2f}
Precio actual en Amazon MX: {f"MX${precio_amazon:,.2f} (dato real del vendedor)" if precio_amazon > 0 else f"No proporcionado — referencia de mercado: MX${precio_mercado:,.0f}"}
{velocidad_txt}

═══ INSTRUCCIÓN CRÍTICA ═══
Este es análisis de ARBITRAJE PURO del producto específico indicado.
El vendedor NO va a lanzar una marca nueva — va a revender este mismo producto.
El precio de venta debe ser IGUAL O MENOR al precio actual en Amazon (si se proporcionó).
Compite directamente contra otros sellers del mismo ASIN.

═══ CONTEXTO DE MERCADO ═══
{contexto}
Margen estimado del mercado: {margen_pct}%

═══ CÁLCULO ═══
Usa el precio de venta real (si se proporcionó) para los cálculos:
- Referral fee Amazon MX: 15% del precio de venta
- FBA fee estimado: MX$45-80 según tamaño/peso típico del producto
- Ganancia neta por unidad = precio_venta - precio_compra - referral_fee - fba_fee
- ROI = (ganancia_neta_total / inversión_total) * 100

Responde ÚNICAMENTE con JSON válido, sin backticks:

{{
  "asin": "{asin or ''}",
  "veredicto": "COMPRA",
  "score_oportunidad": 0,
  "precio_venta_recomendado_mx": 0.0,
  "precio_lanzamiento_mx": 0.0,
  "referral_fee_mx": 0.0,
  "fba_fee_estimado_mx": 0.0,
  "ganancia_por_unidad_mx": 0.0,
  "ganancia_total_estimada_mx": 0.0,
  "roi_estimado_pct": 0.0,
  "tiempo_recuperacion_estimado": "X semanas",
  "razon_principal": "razón del veredicto en 1-2 oraciones",
  "resumen_ejecutivo": "párrafo de 3-4 oraciones para el vendedor",
  "riesgos": ["riesgo 1", "riesgo 2", "riesgo 3"],
  "acciones_inmediatas": ["acción 1", "acción 2", "acción 3"]
}}

veredicto debe ser exactamente: "COMPRA", "NO COMPRA" o "RIESGO MEDIO"
score_oportunidad: entero de 0 a 100"""

    print("  Claude evaluando arbitraje...")
    respuesta = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1500,
        system="Eres experto en arbitraje Amazon Mexico. Respondes siempre con JSON valido.",
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

    # Ensure asin is always in resultado
    if "asin" not in resultado or not resultado["asin"]:
        resultado["asin"] = asin

    # Guardrail financiero: veredicto no puede ser COMPRA si la ganancia por unidad es negativa.
    # Claude puede dar COMPRA especulando con precios futuros, pero el vendedor necesita
    # saber la realidad actual antes de comprometer capital.
    ganancia_ud = resultado.get("ganancia_por_unidad_mx", 0) or 0
    roi_actual  = resultado.get("roi_estimado_pct", 0) or 0
    if ganancia_ud < 0 and resultado.get("veredicto") == "COMPRA":
        resultado["veredicto"] = "RIESGO MEDIO"
        # Score máximo 59 cuando hay pérdida real — refleja que es una apuesta, no una compra segura
        resultado["score_oportunidad"] = min(resultado.get("score_oportunidad", 50), 59)
        resultado["razon_principal"] = (
            "Al precio actual la ganancia por unidad es negativa — solo viable si el precio sube. "
            + resultado.get("razon_principal", "")
        )

    resultado["_tokens"] = {
        "entrada": respuesta.usage.input_tokens,
        "salida":  respuesta.usage.output_tokens,
    }

    escribir_memoria("validador", {
        "producto":                    producto,
        "asin":                        asin,
        "precio_compra_mx":            precio_compra,
        "precio_amazon_mx":            precio_amazon,
        "ventas_mes":                  ventas_mes,
        "veredicto":                   resultado.get("veredicto", ""),
        "roi_estimado_pct":            resultado.get("roi_estimado_pct", 0),
        "precio_venta_recomendado_mx": resultado.get("precio_venta_recomendado_mx", 0),
        "ganancia_por_unidad_mx":      resultado.get("ganancia_por_unidad_mx", 0),
        "score_oportunidad":           resultado.get("score_oportunidad", 0),
    })
    return resultado


def ejecutar(producto, precio_compra, unidades=1, mercado=None,
             url_amazon="", precio_amazon=0, ventas_mes=0):
    print("\n" + "="*50)
    print("AGENTE 9: VALIDADOR DE ARBITRAJE")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)

    resultado = analizar_arbitraje(
        producto, precio_compra, unidades,
        url_amazon=url_amazon, precio_amazon=precio_amazon, ventas_mes=ventas_mes
    )
    if not resultado:
        print("  No se pudo generar el analisis")
        return None

    veredicto    = resultado.get("veredicto", "?")
    roi          = resultado.get("roi_estimado_pct", 0)
    precio_venta = resultado.get("precio_venta_recomendado_mx", 0)
    score        = resultado.get("score_oportunidad", 0)

    print(f"\n  Producto: {producto}")
    print(f"  ASIN: {resultado.get('asin', 'N/A')}")
    print(f"  Precio compra: MX${precio_compra:,.2f} x {unidades} uds")
    print(f"\n  VEREDICTO: {veredicto}")
    print(f"  Score: {score}/100")
    print(f"  ROI estimado: {roi}%")
    print(f"  Precio venta: MX${precio_venta:,.0f}")
    print(f"\n  {resultado.get('razon_principal', '')[:120]}")

    reporte = generar_reporte(producto, precio_compra, unidades, resultado)
    path = REPORTS_DIR / "fase6_arbitraje.md"
    path.write_text(reporte, encoding="utf-8")
    print(f"\n  Reporte guardado en: {path}")

    return resultado


def generar_reporte(producto, precio_compra, unidades, resultado):
    r = []
    veredicto = resultado.get("veredicto", "?")
    asin = resultado.get("asin", "")

    r.append(f"# Analisis de Arbitraje — {producto}\n")
    if asin:
        r.append(f"**ASIN:** `{asin}`\n")
    r.append(f"## Veredicto: **{veredicto}**")
    r.append(f"**Score de oportunidad:** {resultado.get('score_oportunidad', 0)}/100\n")

    r.append("## Numeros clave")
    r.append("| | |")
    r.append("|---|---|")
    r.append(f"| Precio de compra | MX${precio_compra:,.2f} |")
    r.append(f"| Unidades | {unidades} |")
    r.append(f"| Inversion total | MX${precio_compra * unidades:,.2f} |")
    r.append(f"| Precio venta recomendado | MX${resultado.get('precio_venta_recomendado_mx', 0):,.0f} |")
    r.append(f"| Referral fee (15%) | MX${resultado.get('referral_fee_mx', 0):,.0f} |")
    r.append(f"| FBA fee estimado | MX${resultado.get('fba_fee_estimado_mx', 0):,.0f} |")
    r.append(f"| Ganancia por unidad | MX${resultado.get('ganancia_por_unidad_mx', 0):,.0f} |")
    r.append(f"| Ganancia total estimada | MX${resultado.get('ganancia_total_estimada_mx', 0):,.0f} |")
    r.append(f"| ROI estimado | {resultado.get('roi_estimado_pct', 0)}% |")
    r.append(f"| Tiempo recuperacion | {resultado.get('tiempo_recuperacion_estimado', '?')} |\n")

    r.append("## Resumen ejecutivo")
    r.append(resultado.get("resumen_ejecutivo", "") + "\n")

    r.append("## Razon del veredicto")
    r.append(resultado.get("razon_principal", "") + "\n")

    r.append("## Riesgos")
    for riesgo in resultado.get("riesgos", []):
        r.append(f"- {riesgo}")

    r.append("\n## Acciones inmediatas")
    for i, accion in enumerate(resultado.get("acciones_inmediatas", []), 1):
        r.append(f"{i}. {accion}")

    tokens = resultado.get("_tokens", {})
    r.append(f"\n*Tokens: {tokens.get('entrada', 0)} entrada / {tokens.get('salida', 0)} salida*")
    return "\n".join(r)


if __name__ == "__main__":
    ejecutar("NOW Foods Vitamina C-1000 100 Capsulas", 140.0, 100,
             url_amazon="https://www.amazon.com.mx/dp/B0C29KV9TH",
             precio_amazon=299.0, ventas_mes=1500)

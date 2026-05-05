# agents/restricciones.py
import os
from anthropic import Anthropic
from agents.memoria import escribir_memoria, parsear_json_claude

PROMPT_SISTEMA = (
    "Eres experto en regulaciones de Amazon México y normativas mexicanas de ecommerce. "
    "Respondes solo con JSON válido."
)

PROMPT_USUARIO = """Analiza las restricciones para vender en Amazon México en el nicho: {mercado}

Responde SOLO con JSON válido, sin backticks ni texto adicional:
{{
  "nivel_restriccion": "ALTO",
  "requiere_aprobacion_amazon": true,
  "cofepris_aplica": false,
  "certificaciones_requeridas": [],
  "restricciones_principales": [],
  "advertencia": "",
  "puede_vender_sin_marca_registrada": true
}}

Criterios de nivel:
- ALTO: categoría restringida en Amazon MX (requiere aprobación previa), o requiere COFEPRIS, o está prohibida/regulada fuertemente
- MEDIO: hay requisitos técnicos alcanzables (NOM, etiquetado específico, importación con permiso)
- BAJO: categoría abierta, sin restricciones especiales para vendedores individuales

Categorías con aprobación Amazon MX requerida: alimentos, suplementos dietéticos, cosméticos, medicamentos OTC, bebidas, alcohol, electrónica de seguridad, juguetes para bebés menores de 3 años, productos de seguridad personal.
COFEPRIS aplica a: alimentos, bebidas, suplementos, cosméticos, medicamentos, dispositivos médicos, artículos de higiene.
NOM: aplica a electrónica (NOM-019, NOM-003), textil (NOM-004, NOM-020), calzado (NOM-113), juguetes (NOM-015)."""


def analizar_restricciones_haiku(mercado: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {}

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=PROMPT_SISTEMA,
            messages=[{"role": "user", "content": PROMPT_USUARIO.format(mercado=mercado)}],
        )
        return parsear_json_claude(resp.content[0].text.strip(), "restricciones")
    except Exception as e:
        print(f"  [RESTRICCIONES] Error API: {e}")
        return {}


def ejecutar(mercado: str) -> dict:
    print(f"  [RESTRICCIONES] Analizando para: {mercado}")

    resultado = analizar_restricciones_haiku(mercado)

    if not resultado.get("nivel_restriccion"):
        resultado = {
            "nivel_restriccion":             "BAJO",
            "requiere_aprobacion_amazon":    False,
            "cofepris_aplica":               False,
            "certificaciones_requeridas":    [],
            "restricciones_principales":     [],
            "advertencia":                   "",
            "puede_vender_sin_marca_registrada": True,
        }

    nivel = resultado.get("nivel_restriccion", "BAJO")
    print(f"  [RESTRICCIONES] Nivel: {nivel}")
    if resultado.get("advertencia"):
        print(f"  [RESTRICCIONES] Advertencia: {resultado['advertencia']}")

    escribir_memoria("restricciones", resultado)
    return resultado


def obtener_restriccion_batch(categoria: str) -> tuple[str, str]:
    """
    Llamada ligera para el batch: retorna (nivel, advertencia).
    Sin pytrends, solo Claude Haiku con la categoría dominante.
    """
    if not categoria:
        return "BAJO", ""
    resultado = analizar_restricciones_haiku(categoria)
    nivel      = resultado.get("nivel_restriccion", "BAJO")
    advertencia = resultado.get("advertencia", "")
    return nivel, advertencia

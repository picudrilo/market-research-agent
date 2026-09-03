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
  "categoria_regulatoria": "a qué categoría regulada pertenece el nicho y por qué",
  "pasos_para_cumplir": [],
  "advertencia": "",
  "puede_vender_sin_marca_registrada": true
}}

Criterios de nivel:
- ALTO: categoría restringida en Amazon MX (requiere aprobación previa), o requiere COFEPRIS, o está prohibida/regulada fuertemente
- MEDIO: hay requisitos técnicos alcanzables (NOM, etiquetado específico, importación con permiso)
- BAJO: categoría abierta, sin restricciones especiales para vendedores individuales

REGLAS OBLIGATORIAS:
1. TRAZABILIDAD: cada restricción principal debe nombrar la norma o política específica que
   la origina (ej: "COFEPRIS para suplementos", "NOM-051 de etiquetado", "aprobación de
   categoría Salud y Cuidado Personal en Amazon MX"). No des restricciones vagas.
2. ACCIONABILIDAD: en "pasos_para_cumplir" lista acciones concretas y ordenadas que el
   vendedor debe hacer para poder vender legalmente (ej: "tramitar registro sanitario COFEPRIS",
   "solicitar aprobación de categoría en Seller Central", "etiquetar según NOM-051").
3. Si el nivel es BAJO, dilo claramente y deja pasos_para_cumplir vacío o mínimo.

Categorías con aprobación Amazon MX requerida: alimentos, suplementos dietéticos, cosméticos, medicamentos OTC, bebidas, alcohol, electrónica de seguridad, juguetes para bebés menores de 3 años, productos de seguridad personal.
COFEPRIS aplica a: alimentos, bebidas, suplementos, cosméticos, medicamentos, dispositivos médicos, artículos de higiene.
NOM: aplica a electrónica (NOM-019, NOM-003), textil (NOM-004, NOM-020), calzado (NOM-113), juguetes (NOM-015), etiquetado general (NOM-051)."""


def analizar_restricciones(mercado: str, modelo: str = "claude-haiku-4-5-20251001") -> dict:
    """Analiza restricciones regulatorias. modelo=Sonnet para marca propia (más rigor
    legal); Haiku por defecto para el batch de arbitraje (muchas llamadas, más barato)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {}

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=modelo,
            max_tokens=2000,
            system=PROMPT_SISTEMA,
            messages=[{"role": "user", "content": PROMPT_USUARIO.format(mercado=mercado)}],
        )
        return parsear_json_claude(resp.content[0].text.strip(), "restricciones")
    except Exception as e:
        print(f"  [RESTRICCIONES] Error API: {e}")
        return {}


# Alias de compatibilidad por si algún módulo externo aún importa el nombre viejo.
analizar_restricciones_haiku = analizar_restricciones


def ejecutar(mercado: str) -> dict:
    print(f"  [RESTRICCIONES] Analizando para: {mercado}")

    # Marca propia: usar Sonnet para mayor rigor en matices legales/regulatorios.
    resultado = analizar_restricciones(mercado, modelo="claude-sonnet-4-6")

    if not resultado.get("nivel_restriccion"):
        resultado = {
            "nivel_restriccion":             "BAJO",
            "requiere_aprobacion_amazon":    False,
            "cofepris_aplica":               False,
            "certificaciones_requeridas":    [],
            "restricciones_principales":     [],
            "categoria_regulatoria":         "",
            "pasos_para_cumplir":            [],
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
    # Batch de arbitraje: se queda en Haiku (muchas llamadas, prioriza costo).
    resultado = analizar_restricciones(categoria)
    nivel      = resultado.get("nivel_restriccion", "BAJO")
    advertencia = resultado.get("advertencia", "")
    return nivel, advertencia

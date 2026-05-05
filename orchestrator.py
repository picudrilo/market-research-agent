# orchestrator.py
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from agents import ingesta, competencia, resenas, gap_analysis, precio_valor, keywords, estacionalidad, restricciones, concepto, listado_optimizado, dashboard
from agents import scraper
from agents.memoria import limpiar_memoria, leer_memoria, escribir_memoria
from agents import conocimiento

def imprimir_header(mercado):
    print("\n" + "="*55)
    print("=  SISTEMA MULTIAGENTE DE INVESTIGACION DE MERCADO    =")
    print("="*55)
    print(f"  Mercado: {mercado}")
    print(f"  Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("="*55 + "\n")

def imprimir_resumen_memoria():
    memoria = leer_memoria()
    if not memoria:
        return

    print("\n" + "="*55)
    print("MEMORIA COMPARTIDA DEL PIPELINE")
    print("="*55)

    orden = ["resenas", "gap_analysis", "keywords", "concepto", "listado_optimizado"]
    agentes_en_memoria = [a for a in orden if a in memoria]

    for agente in agentes_en_memoria:
        datos     = memoria[agente]
        hallazgos = datos.get("hallazgos", {})
        print(f"\n  [{agente.upper()}]")
        for clave, valor in hallazgos.items():
            if isinstance(valor, list):
                preview = ", ".join(str(v)[:30] for v in valor[:3])
                print(f"    {clave}: [{preview}...]")
            else:
                print(f"    {clave}: {str(valor)[:80]}")

    print(f"\n  Archivo: outputs/memoria_pipeline.json")
    print("="*55)

def imprimir_resumen_final(resultados, tiempo_total):
    print("\n" + "="*55)
    print("RESUMEN FINAL DEL PIPELINE")
    print("="*55)
    for agente, estado in resultados.items():
        icono = "OK" if estado.startswith("OK") else "XX"
        print(f"  {icono} {agente}: {estado}")
    print(f"\n  Tiempo total: {round(tiempo_total, 1)} segundos")
    print("\n  Archivos generados:")
    reportes = list(Path("reports").glob("*.md"))
    outputs  = list(Path("outputs").glob("*.*"))
    for r in sorted(reportes):
        print(f"    - reports/{r.name}")
    for o in sorted(outputs):
        print(f"    - outputs/{o.name}")
    print("="*55)
    print("\n  Pipeline completado exitosamente.")
    print("  Dashboard: reports/dashboard.html")
    print("  Listing:   reports/fase5_listado_optimizado.md\n")

def ejecutar_pipeline(mercado, modo: str = "marca_propia"):
    print("\n  Preparando pipeline...")
    limpiar_memoria()

    # Cargar historial ANTES de que los agentes empiecen a leer memoria.
    # Si falla, el pipeline continúa sin contexto histórico.
    try:
        contexto_historico = conocimiento.obtener_contexto_historico(mercado)
        if contexto_historico:
            escribir_memoria("historial", {"contexto": contexto_historico})
            print("  OK Contexto histórico cargado desde BD")
        else:
            print("  -- Sin historial previo para este mercado")
    except Exception as _e_hist:
        print(f"  -- Historial no disponible ({_e_hist})")

    imprimir_header(mercado)
    inicio_total = time.time()
    resultados   = {}

    agentes = [
        ("Agente 0 - Verificación de Datos",     lambda: scraper.ejecutar(mercado)),
        ("Agente 1 - Ingesta de Datos",          lambda: ingesta.ejecutar(mercado)),
        ("Agente 2 - Analisis de Competencia",    lambda: competencia.ejecutar(mercado)),
        ("Agente 3 - Analisis de Resenas",        lambda: resenas.ejecutar(mercado)),
        ("Agente 4 - GAP Analysis",               lambda: gap_analysis.ejecutar(mercado)),
        ("Agente 5 - Precio vs Valor",            lambda: precio_valor.ejecutar(mercado)),
        ("Agente 6 - Keywords y SEO",             lambda: keywords.ejecutar(mercado)),
        ("Agente 7 - Estacionalidad",             lambda: estacionalidad.ejecutar(mercado)),
        ("Agente 8 - Restricciones",              lambda: restricciones.ejecutar(mercado)),
        ("Agente 9 - Concepto de Diferenciacion", lambda: concepto.ejecutar(mercado)),
        ("Agente 10 - Listado Optimizado",        lambda: listado_optimizado.ejecutar(mercado)),
        ("Agente 11 - Dashboard Visual",          lambda: dashboard.ejecutar(mercado)),
    ]

    for nombre, funcion in agentes:
        inicio = time.time()
        try:
            resultado = funcion()
            estado    = "OK" if resultado is not None else "SIN DATOS"
        except Exception as e:
            estado = f"ERROR: {str(e)[:40]}"
            print(f"\nERROR en {nombre}: {e}")
        duracion = round(time.time() - inicio, 1)
        resultados[nombre] = f"{estado} ({duracion}s)"

    tiempo_total = time.time() - inicio_total

    imprimir_resumen_memoria()
    imprimir_resumen_final(resultados, tiempo_total)

    # Persistir análisis en BD para alimentar futuros runs.
    # Tolerante a fallos: si guardar falla, el pipeline ya terminó correctamente.
    try:
        conocimiento.guardar_analisis(mercado, modo, leer_memoria())
    except Exception as _e_guard:
        print(f"\n  -- Análisis no guardado en historial ({_e_guard})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sistema Multiagente de Investigación de Mercado")
    parser.add_argument("--market", type=str, default="auriculares bluetooth", help="Nombre del mercado a analizar")
    parser.add_argument("--mode",   type=str, default="marca_propia",
                        choices=["marca_propia", "arbitraje"],
                        help="Modo de análisis: marca_propia (default) | arbitraje")
    args = parser.parse_args()
    ejecutar_pipeline(args.market, modo=args.mode)
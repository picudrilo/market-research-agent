# api/main.py
import sys
import os
import json
import uuid
import threading
import asyncio
from pathlib import Path

# Set working directory to project root so agents find data/ outputs/ etc.
PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic
from agents import (
    ingesta, competencia, resenas, gap_analysis,
    precio_valor, keywords, estacionalidad, restricciones, concepto, listado_optimizado,
    scraper,
)
from agents.memoria import limpiar_memoria, leer_memoria, escribir_memoria
from agents.validador import ejecutar as ejecutar_validador
from agents import conocimiento
from agents.batch_arbitraje import ejecutar as ejecutar_batch
from agents.memoria_decisiones import (
    registrar_decision, obtener_contexto_previo, formatear_contexto_para_claude
)
import pandas as pd
import io

app = FastAPI(title="Market Research Validator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job storage — each job stores a list of events (survives reconnects)
jobs: dict = {}

# One pipeline at a time per instance
_pipeline_lock = threading.Semaphore(1)


class ValidarRequest(BaseModel):
    producto: str
    precio_compra: float = 0
    unidades: int = 1
    url_amazon: str = ""
    precio_amazon: float = 0
    ventas_mes: int = 0
    modo: str = "arbitraje"  # "arbitraje" | "marca_propia"


def extraer_asin_de_url(url: str) -> str:
    import re as _re
    m = _re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', url or "")
    return m.group(1) if m else ""


def detectar_mercado(producto: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY no esta configurada en el servidor")

    client = Anthropic(api_key=api_key)
    try:
        respuesta = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            system=(
                "Devuelve SOLO el termino de busqueda (2-5 palabras en espanol) que un comprador "
                "escribiria en Amazon Mexico para encontrar ESTE producto especifico. Conserva el "
                "TIPO de producto y su caracteristica o uso distintivo. Elimina marca, modelo, "
                "tamano, cantidad y adjetivos de relleno. NO lo generalices a una categoria amplia: "
                "un termo para cerveza NO es 'accesorios para bebidas', es 'termo para cerveza'. "
                "Ejemplos: 'Termo Stanley 1.4L para cerveza artesanal' -> termo para cerveza; "
                "'Creatina monohidratada Optimum Nutrition 300g' -> creatina monohidrato; "
                "'Audifonos Sony WH-1000XM5 bluetooth' -> audifonos bluetooth; "
                "'Freidora de aire Ninja 5L sin aceite' -> freidora de aire. "
                "Sin puntuacion ni explicacion."
            ),
            messages=[{"role": "user", "content": f"Producto: {producto}"}]
        )
        return respuesta.content[0].text.strip()
    except Exception as e:
        raise RuntimeError(f"Anthropic API error ({type(e).__name__}): {e}")


def ejecutar_pipeline(job_id: str, producto: str, precio_compra: float, unidades: int,
                      url_amazon: str = "", precio_amazon: float = 0, ventas_mes: int = 0,
                      modo: str = "arbitraje"):
    """Runs the agent pipeline. In arbitraje mode skips concepto/listado (steps 7-8).
    In marca_propia mode skips the validador (step 9)."""

    def emit(event: dict):
        jobs[job_id]["events"].append(event)

    def prog(step: int, agente: str, mensaje: str, status: str = "running"):
        emit({
            "type": "progress",
            "step": step,
            "total": 11,
            "agent": agente,
            "message": mensaje,
            "status": status,
        })

    acquired = _pipeline_lock.acquire(timeout=5)
    if not acquired:
        emit({"type": "error", "message": "Servidor ocupado. Intenta en 30 segundos."})
        jobs[job_id]["status"] = "error"
        return

    try:
        prog(0, "Detector de nicho", "Identificando nicho de mercado...", "running")
        mercado = detectar_mercado(producto)
        prog(0, "Detector de nicho", f"Nicho: {mercado}", "done")

        # Consultar decisiones previas antes de arrancar el pipeline
        asin_param = extraer_asin_de_url(url_amazon)
        decisiones_previas = obtener_contexto_previo(mercado, asin=asin_param)
        jobs[job_id]["decisiones_previas"] = decisiones_previas

        limpiar_memoria()

        # Cargar historial institucional ANTES de que los agentes lean la memoria.
        # Tolerante a fallos: si falla, el pipeline continúa sin contexto histórico.
        try:
            contexto_historico = conocimiento.obtener_contexto_historico(mercado)
            if contexto_historico:
                escribir_memoria("historial", {"contexto": contexto_historico})
                emit({"type": "progress", "step": 0, "total": 11,
                      "agent": "Memoria histórica",
                      "message": "Contexto histórico cargado desde BD",
                      "status": "done"})
        except Exception:
            pass  # Sin historial — primer análisis o BD no disponible

        # Verificar frescura y scrapear si es necesario (antes de ingesta)
        emit({"type": "progress", "step": 0, "total": 11,
              "agent": "Verificación de datos",
              "message": "Verificando datos disponibles para este mercado...",
              "status": "running"})
        try:
            fuente = scraper.ejecutar(mercado)
            fuente_txt = {
                "csv":      "Datos frescos en BD",
                "scraping": "Scraping completado",
                "hibrido":  "Datos actualizados con scraping",
            }.get(fuente, fuente)
            emit({"type": "progress", "step": 0, "total": 11,
                  "agent": "Verificación de datos",
                  "message": fuente_txt,
                  "status": "done"})
        except Exception as e:
            emit({"type": "progress", "step": 0, "total": 11,
                  "agent": "Verificación de datos",
                  "message": f"Scraper omitido: {str(e)[:60]}",
                  "status": "error"})

        pasos = [
            (1, "Ingesta de datos",        lambda: ingesta.ejecutar(mercado)),
            (2, "Analisis de competencia", lambda: competencia.ejecutar(mercado)),
            (3, "Analisis de resenas",     lambda: resenas.ejecutar(mercado)),
            (4, "GAP Analysis",            lambda: gap_analysis.ejecutar(mercado)),
            (5, "Precio vs Valor",         lambda: precio_valor.ejecutar(mercado)),
            (6, "Keywords y SEO",          lambda: keywords.ejecutar(mercado)),
            (7, "Estacionalidad",           lambda: estacionalidad.ejecutar(mercado)),
            (8, "Restricciones",            lambda: restricciones.ejecutar(mercado)),
        ]

        if modo == "marca_propia":
            pasos += [
                (9,  "Concepto de diferenciacion", lambda: concepto.ejecutar(mercado)),
                (10, "Listado optimizado",         lambda: listado_optimizado.ejecutar(mercado)),
            ]
        else:  # arbitraje — skip concepto y listado, ir directo al validador
            pasos += [
                (11, "Validacion de arbitraje", lambda: ejecutar_validador(
                    producto, precio_compra, unidades, mercado,
                    url_amazon=url_amazon, precio_amazon=precio_amazon, ventas_mes=ventas_mes,
                )),
            ]

        resultados = {}
        for step, nombre, funcion in pasos:
            prog(step, nombre, f"Analizando {nombre.lower()}...", "running")
            try:
                resultados[nombre] = funcion()
                prog(step, nombre, nombre, "done")
            except Exception as e:
                prog(step, nombre, f"Error: {str(e)[:60]}", "error")
                resultados[nombre] = None

        mem                = leer_memoria()
        validador_mem      = mem.get("validador",          {}).get("hallazgos", {})
        listado_mem        = mem.get("listado_optimizado", {}).get("hallazgos", {})
        concepto_mem       = mem.get("concepto",           {}).get("hallazgos", {})
        keywords_mem       = mem.get("keywords",           {}).get("hallazgos", {})
        estacionalidad_mem  = mem.get("estacionalidad",  {}).get("hallazgos", {})
        restricciones_mem   = mem.get("restricciones",   {}).get("hallazgos", {})
        validador_full = resultados.get("Validacion de arbitraje") or {}

        final = {
            "modo":              modo,
            "mercado":           mercado,
            "producto":          producto,
            "precio_compra_mx":  precio_compra,
            "unidades":          unidades,
            "url_amazon":        url_amazon,
            "precio_amazon_mx":  precio_amazon,
            "ventas_mes":        ventas_mes,
            "asin":              validador_full.get("asin", ""),
            "veredicto":         validador_mem.get("veredicto", ""),
            "score_oportunidad": validador_mem.get("score_oportunidad", 0),
            "roi_estimado_pct":  validador_mem.get("roi_estimado_pct", 0),
            "precio_venta_recomendado_mx": validador_mem.get("precio_venta_recomendado_mx", 0),
            "ganancia_por_unidad_mx":      validador_full.get("ganancia_por_unidad_mx", 0),
            "ganancia_total_estimada_mx":  validador_full.get("ganancia_total_estimada_mx", 0),
            "referral_fee_mx":             validador_full.get("referral_fee_mx", 0),
            "fba_fee_estimado_mx":         validador_full.get("fba_fee_estimado_mx", 0),
            "tiempo_recuperacion":         validador_full.get("tiempo_recuperacion_estimado", ""),
            "razon_principal":             validador_full.get("razon_principal", ""),
            "resumen_ejecutivo":           validador_full.get("resumen_ejecutivo", ""),
            "riesgos":                     validador_full.get("riesgos", []),
            "acciones_inmediatas":         validador_full.get("acciones_inmediatas", []),
            "listing": {
                "titulo":             listado_mem.get("titulo", ""),
                "precio_lanzamiento": listado_mem.get("precio_lanzamiento_mx", 0),
                "precio_objetivo":    listado_mem.get("precio_objetivo_mx", 0),
                "terminos_backend":   listado_mem.get("terminos_backend", []),
                "top_bullets":        listado_mem.get("top_3_bullets", []),
            },
            "concepto": {
                "nombre":          concepto_mem.get("nombre_concepto", ""),
                "tagline":         concepto_mem.get("tagline", ""),
                "mensaje_central": concepto_mem.get("mensaje_central", ""),
            },
            "keyword_principal": keywords_mem.get("keyword_principal", ""),
            "estacionalidad": {
                "riesgo_actual":  estacionalidad_mem.get("riesgo_actual", "BAJO"),
                "advertencia":    estacionalidad_mem.get("advertencia", ""),
                "pico_meses":     estacionalidad_mem.get("pico_meses", []),
                "valle_meses":    estacionalidad_mem.get("valle_meses", []),
                "tiene_estacionalidad": estacionalidad_mem.get("tiene_estacionalidad", False),
            },
            "restricciones": {
                "nivel":                      restricciones_mem.get("nivel_restriccion", "BAJO"),
                "requiere_aprobacion_amazon": restricciones_mem.get("requiere_aprobacion_amazon", False),
                "cofepris_aplica":            restricciones_mem.get("cofepris_aplica", False),
                "certificaciones_requeridas": restricciones_mem.get("certificaciones_requeridas", []),
                "restricciones_principales":  restricciones_mem.get("restricciones_principales", []),
                "advertencia":                restricciones_mem.get("advertencia", ""),
                "puede_vender_sin_marca":     restricciones_mem.get("puede_vender_sin_marca_registrada", True),
            },
        }

        # ── Métricas básicas de mercado (todos los modos) ────────────────────
        def _csv_lista(ruta, max_rows=10, cols=None):
            p = Path(ruta)
            if not p.exists():
                return []
            try:
                df_tmp = pd.read_csv(p, encoding="utf-8")
                if cols:
                    df_tmp = df_tmp[[c for c in cols if c in df_tmp.columns]]
                return df_tmp.head(max_rows).fillna(0).to_dict(orient="records")
            except Exception:
                return []

        comp_cols = ["asin", "marca", "precio", "bsr", "reviews_count",
                     "rating", "ventas_mensuales_asin", "revenue_mensual_asin", "fba"]
        competidores_raw_base = _csv_lista("outputs/competidores_ranking.csv", 10, comp_cols)
        precios_base = [float(r["precio"]) for r in competidores_raw_base if r.get("precio")]
        rev_total_base = sum(float(r.get("revenue_mensual_asin") or 0) for r in competidores_raw_base)
        precio_stats_base: dict = {}
        if precios_base:
            ps = sorted(precios_base)
            n = len(ps)
            precio_stats_base = {
                "min": ps[0], "p25": ps[max(0, n // 4 - 1)],
                "mediana": ps[n // 2], "p75": ps[min(n - 1, (3 * n) // 4)], "max": ps[-1],
            }

        competencia_mem_base = mem.get("competencia", {}).get("hallazgos", {})
        intensidad_base = competencia_mem_base.get("intensidad_competencia", "alta") or "alta"
        score_mercado_base = 50 + {"baja": 20, "media": 5, "alta": -10}.get(intensidad_base.lower(), 0)
        kw_base  = _csv_lista("outputs/keywords_opportunity.csv", 20, ["keyword", "nivel_oportunidad"])
        gap_base = _csv_lista("outputs/gap_opportunities.csv",    10, ["impacto"])
        score_mercado_base += min(sum(1 for k in kw_base  if k.get("nivel_oportunidad") == "Alta oportunidad") * 2, 20)
        score_mercado_base += min(sum(1 for g in gap_base if g.get("impacto") == "Alto") * 3, 15)
        if rev_total_base > 1_000_000:
            score_mercado_base += 15
        elif rev_total_base > 500_000:
            score_mercado_base += 10
        elif rev_total_base > 100_000:
            score_mercado_base += 5
        score_mercado_base = max(0, min(100, round(score_mercado_base)))

        final["score_mercado"]   = score_mercado_base
        final["revenue_mercado"] = round(rev_total_base, 2)
        final["precio_stats"]    = precio_stats_base

        # ── Datos detallados para dashboard de marca propia ───────────────────
        if modo == "marca_propia":
            kw_cols  = ["keyword", "volumen_busqueda", "competidores",
                        "tendencia_30d", "score_oportunidad", "nivel_oportunidad"]
            gap_cols = ["area", "problema_cliente", "cobertura_mercado",
                        "oportunidad", "impacto", "facilidad", "evidencia", "score"]
            pp_cols  = ["tema", "frecuencia", "porcentaje", "prioridad"]

            competencia_mem = mem.get("competencia", {}).get("hallazgos", {})
            resenas_mem     = mem.get("resenas",     {}).get("hallazgos", {})
            gap_mem         = mem.get("gap_analysis",{}).get("hallazgos", {})

            kw_raw   = _csv_lista("outputs/keywords_opportunity.csv", 20, kw_cols)
            gaps_raw = _csv_lista("outputs/gap_opportunities.csv",    10, gap_cols)

            final["competidores_top"]     = competidores_raw_base
            final["keywords_top"]         = kw_raw
            final["gaps_detalle"]         = gaps_raw
            final["pain_points_top"]      = _csv_lista("outputs/pain_points_ranked.csv", 8, pp_cols)
            final["barreras_entrada"]     = competencia_mem.get("barreras_entrada", [])
            final["sentimiento"]          = resenas_mem.get("sentimiento_general", "")
            final["insight_resenas"]      = resenas_mem.get("insight_principal", "")
            final["gap_critico"]          = gap_mem.get("gap_mas_critico", "")
            final["combinacion_ganadora"] = gap_mem.get("combinacion_ganadora", "")

        # Persistir análisis en BD para alimentar futuros runs.
        # Se pasa metricas_extra con los campos calculados en este scope.
        try:
            metricas_extra_api = {
                "score_mercado":          final.get("score_mercado"),
                "revenue_mercado":        final.get("revenue_mercado"),
                "precio_stats":           final.get("precio_stats"),
                "pain_points_top":        final.get("pain_points_top"),
                "gaps_detalle":           final.get("gaps_detalle"),
                "keywords_top":           final.get("keywords_top"),
                "intensidad_competencia": mem.get("competencia", {}).get(
                                              "hallazgos", {}).get("intensidad_competencia"),
                "veredicto":              final.get("veredicto"),
            }
            conocimiento.guardar_analisis(mercado, modo, mem, metricas_extra_api)
        except Exception:
            pass  # No interrumpir el flujo si el guardado falla

        # Auto-registrar decisión en memoria histórica
        veredicto_str = final.get("veredicto", "")
        score_val     = int(final.get("score_oportunidad", 0) or 0)
        roi_val       = float(final.get("roi_estimado_pct", 0) or 0)
        decision_id   = registrar_decision(
            mercado          = mercado,
            veredicto_sistema= veredicto_str,
            score_oportunidad= score_val,
            roi_estimado_pct = roi_val,
            precio_compra_mx = precio_compra,
            asin             = final.get("asin", ""),
        )
        final["decision_id"] = decision_id
        final["decisiones_previas"] = jobs[job_id].get("decisiones_previas", [])

        jobs[job_id]["result"] = final
        jobs[job_id]["status"] = "done"
        emit({"type": "done", "result": final})

    except Exception as e:
        jobs[job_id]["status"] = "error"
        emit({"type": "error", "message": str(e)})
    finally:
        _pipeline_lock.release()


@app.get("/buscar-por-barcode/{codigo}")
async def buscar_por_barcode(codigo: str):
    """
    Busca un producto en Amazon MX por código de barras (EAN-13 / UPC-A).
    Retorna {asin, titulo, precio_amazon, url} o {asin: null, codigo}.
    Amazon puede bloquear scraping — el frontend maneja el fallback.
    """
    import httpx
    import re as _re

    url = f"https://www.amazon.com.mx/s?k={codigo}&i=aps"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "es-MX,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8) as client:
            resp = await client.get(url, headers=headers)

        html = resp.text

        # data-asin es el método más fiable en resultados de búsqueda
        m = _re.search(r'data-asin="([A-Z0-9]{10})"', html)
        if not m:
            m = _re.search(r'/dp/([A-Z0-9]{10})', html)

        if not m:
            return {"asin": None, "codigo": codigo}

        asin = m.group(1)

        # Título del primer resultado
        titulo = ""
        t = _re.search(
            r'<span[^>]*class="[^"]*a-size-(?:medium|base-plus)[^"]*a-color-base[^"]*'
            r'a-text-normal[^"]*"[^>]*>(.*?)</span>',
            html, _re.DOTALL
        )
        if t:
            titulo = _re.sub(r"<[^>]+>", "", t.group(1)).strip()[:150]

        # Precio del primer resultado
        precio = 0.0
        p = _re.search(r'<span[^>]*class="[^"]*a-price-whole[^"]*"[^>]*>([\d,]+)', html)
        if p:
            try:
                precio = float(p.group(1).replace(",", ""))
            except ValueError:
                pass

        return {
            "asin":          asin,
            "titulo":        titulo,
            "precio_amazon": precio,
            "url":           f"https://www.amazon.com.mx/dp/{asin}",
            "codigo":        codigo,
        }

    except Exception as e:
        return {"asin": None, "codigo": codigo, "error": str(e)[:80]}


@app.post("/validar")
async def iniciar_validacion(request: ValidarRequest):
    if not request.producto.strip():
        raise HTTPException(400, "El nombre del producto es requerido")
    if request.modo == "arbitraje" and request.precio_compra <= 0:
        raise HTTPException(400, "El precio de compra debe ser mayor a 0")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"events": [], "result": None, "status": "pending"}

    threading.Thread(
        target=ejecutar_pipeline,
        args=(job_id, request.producto.strip(), request.precio_compra, request.unidades),
        kwargs={
            "url_amazon":    request.url_amazon.strip(),
            "precio_amazon": request.precio_amazon,
            "ventas_mes":    request.ventas_mes,
            "modo":          request.modo,
        },
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/stream/{job_id}")
async def stream_progreso(job_id: str):
    """
    SSE endpoint. Polls the events list — never blocks the event loop.
    Reconnecting clients replay all past events automatically.
    """
    if job_id not in jobs:
        raise HTTPException(404, "Job no encontrado")

    async def generate():
        # Tell browser: retry after 5 s on any disconnect
        yield "retry: 5000\n\n"
        idx = 0
        max_seconds = 720   # 12 min hard limit
        elapsed = 0.0
        interval = 0.4      # poll every 400 ms
        last_ping = 0.0
        PING_EVERY = 8.0    # real data event every 8 s — resets Railway proxy idle timer

        while elapsed < max_seconds:
            events = jobs[job_id]["events"]

            if idx < len(events):
                for event in events[idx:]:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("type") in ("done", "error"):
                        return
                idx = len(events)
                last_ping = elapsed
            else:
                if elapsed - last_ping >= PING_EVERY:
                    # Real data event (not a comment) so Railway proxy resets its idle timer
                    yield f"data: {json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
                    last_ping = elapsed
                else:
                    yield ": keep-alive\n\n"

            await asyncio.sleep(interval)
            elapsed += interval

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/resultado/{job_id}")
async def obtener_resultado(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job no encontrado")
    job = jobs[job_id]
    return {"status": job["status"], "result": job.get("result")}


@app.get("/resultado-batch/{job_id}")
async def obtener_resultado_batch(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job no encontrado")
    job = jobs[job_id]
    return {"status": job["status"], "result": job.get("result")}


# ─────────────────────────────────────────────
# BATCH ARBITRAJE
# ─────────────────────────────────────────────

class ProductoBatch(BaseModel):
    asin: str
    precio_compra: float


class ValidarBatchRequest(BaseModel):
    productos: list[ProductoBatch]          # ASINs + precios de compra de la UI
    csv_data:  str                          # CSV Xray completo en base64 o texto plano
    nombre_sesion: str = "sesion_batch"


def ejecutar_pipeline_batch(job_id: str, csv_texto: str,
                            precios_ui: dict, nombre_sesion: str):
    """
    Corre el análisis batch de arbitraje:
    1. Parsea el CSV Xray
    2. Calcula financiero + score (sin Claude)
    3. Llama a Claude 1 vez para análisis cualitativo
    4. Escribe historial markdown
    5. Emite resultado final vía SSE
    """
    def emit(event: dict):
        jobs[job_id]["events"].append(event)

    def prog(step: int, mensaje: str, status: str = "running"):
        emit({
            "type":    "progress",
            "step":    step,
            "total":   4,
            "message": mensaje,
            "status":  status,
        })

    acquired = _pipeline_lock.acquire(timeout=5)
    if not acquired:
        emit({"type": "error", "message": "Servidor ocupado. Intenta en 30 segundos."})
        jobs[job_id]["status"] = "error"
        return

    try:
        prog(1, "Leyendo CSV y calculando financiero...")
        try:
            df = pd.read_csv(io.StringIO(csv_texto), encoding="utf-8")
        except Exception:
            df = pd.read_csv(io.StringIO(csv_texto), encoding="latin-1")

        prog(1, "Cálculo financiero completado", "done")

        prog(2, "Consultando historial en base de datos...")
        engine = None
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            from sqlalchemy import create_engine as _ce
            engine = _ce(db_url)
        prog(2, "Historial consultado", "done")

        prog(3, "Claude analizando productos en batch (1 llamada)...")
        resultado, batch_meta = ejecutar_batch(
            df,
            nombre_sesion=nombre_sesion,
            precios_extra=precios_ui,
            engine=engine,
        )
        prog(3, f"{len(resultado)} productos analizados por Claude", "done")

        prog(4, "Generando historial y resultados finales...")

        # Serializar resultado para JSON (quitar objetos no serializables)
        def serializar(p):
            return {
                "asin":           p["asin"],
                "titulo":         p.get("titulo", ""),
                "marca":          p.get("marca", ""),
                "categoria":      p.get("categoria"),
                "score_arbitraje": p.get("score_arbitraje", 0),
                "semaforo":       p.get("semaforo", "DESCARTAR"),
                "en_historial_bd": p.get("en_historial_bd", False),
                "financiero":     p.get("financiero"),
                "claude_analisis": p.get("claude_analisis", {}),
                # Datos de mercado del CSV
                "precio_amazon":    p.get("precio_amazon"),   # expuesto para recálculo en frontend
                "bsr":            p.get("bsr"),
                "reviews_count":  p.get("reviews_count"),
                "rating":         p.get("rating"),
                "ventas_mes":     p.get("ventas_mes"),
                "active_sellers":   p.get("active_sellers"),
                "fba":              p.get("fba", False),
                "riesgo_estacional": p.get("riesgo_estacional", ""),
            }

        final = {
            "modo":            "batch_arbitraje",
            "nombre_sesion":   nombre_sesion,
            "total":           len(resultado),
            "invertir":        sum(1 for p in resultado if p["semaforo"] == "INVERTIR"),
            "riesgo_medio":    sum(1 for p in resultado if p["semaforo"] == "RIESGO MEDIO"),
            "descartar":       sum(1 for p in resultado if p["semaforo"] == "DESCARTAR"),
            "capital_invertir": sum(
                (p.get("financiero") or {}).get("precio_compra", 0)
                for p in resultado if p["semaforo"] == "INVERTIR"
            ),
            "roi_promedio_invertir": (
                round(
                    sum((p.get("financiero") or {}).get("roi", 0)
                        for p in resultado if p["semaforo"] == "INVERTIR") /
                    max(sum(1 for p in resultado if p["semaforo"] == "INVERTIR"), 1),
                    1
                )
            ),
            "productos":       [serializar(p) for p in resultado],
            "batch_meta":      batch_meta,
        }

        # Persistir en memoria institucional para que futuros análisis aprendan de este batch
        try:
            conocimiento.guardar_batch_por_categorias(resultado)
        except Exception:
            pass

        jobs[job_id]["result"] = final
        jobs[job_id]["status"] = "done"
        prog(4, "Análisis batch completado", "done")
        emit({"type": "done", "result": final})

    except Exception as e:
        jobs[job_id]["status"] = "error"
        emit({"type": "error", "message": str(e)})
    finally:
        _pipeline_lock.release()


@app.post("/validar-batch")
async def iniciar_validacion_batch(request: ValidarBatchRequest):
    """
    Inicia un análisis batch de arbitraje.
    Acepta CSV Xray (texto plano) + lista de {asin, precio_compra} de la UI.
    Retorna job_id para seguir progreso en /stream/{job_id}.
    """
    if not request.csv_data.strip():
        raise HTTPException(400, "csv_data está vacío")

    precios_ui = {p.asin: p.precio_compra for p in request.productos if p.precio_compra > 0}

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"events": [], "result": None, "status": "pending"}

    threading.Thread(
        target=ejecutar_pipeline_batch,
        args=(job_id, request.csv_data, precios_ui, request.nombre_sesion),
        daemon=True,
    ).start()

    return {"job_id": job_id}


# ─────────────────────────────────────────────
# PORTAFOLIO DE INVERSIONES
# ─────────────────────────────────────────────

class InversionCreate(BaseModel):
    asin: str
    titulo: str = ""
    unidades: int = 1
    precio_compra_mx: float
    fecha_compra: str   # YYYY-MM-DD
    notas: str = ""


class InversionUpdate(BaseModel):
    precio_venta_real_mx: float | None = None
    fecha_liquidacion: str | None = None
    estado: str | None = None   # "activo" | "liquidado"
    notas: str | None = None
    roi_real_pct: float | None = None


def _get_engine():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise HTTPException(503, "DATABASE_URL no configurada")
    from sqlalchemy import create_engine as _ce
    return _ce(db_url)


def _row_dict(r) -> dict:
    return {
        "id":                  r.id,
        "asin":                r.asin,
        "titulo":              r.titulo or "",
        "unidades":            r.unidades,
        "precio_compra_mx":    float(r.precio_compra_mx or 0),
        "precio_venta_real_mx": float(r.precio_venta_real_mx) if r.precio_venta_real_mx is not None else None,
        "fecha_compra":        str(r.fecha_compra),
        "fecha_liquidacion":   str(r.fecha_liquidacion) if r.fecha_liquidacion else None,
        "estado":              r.estado,
        "roi_real_pct":        float(r.roi_real_pct) if r.roi_real_pct is not None else None,
        "notas":               r.notas or "",
        "created_at":          str(r.created_at),
    }


# ─────────────────────────────────────────────
# MEMORIA DE DECISIONES
# ─────────────────────────────────────────────

class DecisionUpdate(BaseModel):
    decision_usuario: str | None = None   # ACEPTO | RECHAZO | PENDIENTE
    resultado_real:   str | None = None   # EXITOSO | PERDIDA | PENDIENTE | CANCELADO
    roi_real_pct:     float | None = None
    fecha_resultado:  str  | None = None
    lecciones:        str  | None = None
    notas:            str  | None = None


@app.get("/decisiones")
async def listar_decisiones():
    from sqlalchemy import text as sql_text
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT id, asin, mercado, veredicto_sistema, score_oportunidad, "
            "roi_estimado_pct, decision_usuario, resultado_real, roi_real_pct, "
            "fecha_decision, fecha_resultado, lecciones, notas, created_at "
            "FROM decisiones ORDER BY created_at DESC LIMIT 100"
        )).fetchall()
    return [
        {
            "id":               r[0],
            "asin":             r[1] or "",
            "mercado":          r[2],
            "veredicto_sistema": r[3],
            "score_oportunidad": r[4],
            "roi_estimado_pct": float(r[5]) if r[5] is not None else None,
            "decision_usuario": r[6],
            "resultado_real":   r[7],
            "roi_real_pct":     float(r[8]) if r[8] is not None else None,
            "fecha_decision":   str(r[9]),
            "fecha_resultado":  str(r[10]) if r[10] else None,
            "lecciones":        r[11] or "",
            "notas":            r[12] or "",
            "created_at":       str(r[13]),
        }
        for r in rows
    ]


@app.put("/decisiones/{decision_id}")
async def actualizar_decision(decision_id: int, upd: DecisionUpdate):
    from sqlalchemy import text as sql_text
    engine = _get_engine()
    sets, params = [], {"id": decision_id}
    mapping = {
        "decision_usuario": upd.decision_usuario,
        "resultado_real":   upd.resultado_real,
        "roi_real_pct":     upd.roi_real_pct,
        "fecha_resultado":  upd.fecha_resultado,
        "lecciones":        upd.lecciones,
        "notas":            upd.notas,
    }
    for col, val in mapping.items():
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if not sets:
        raise HTTPException(400, "No hay campos para actualizar")
    with engine.connect() as conn:
        conn.execute(sql_text(f"UPDATE decisiones SET {', '.join(sets)} WHERE id = :id"), params)
        conn.commit()
    return {"ok": True}


@app.get("/inversiones/resumen")
async def resumen_inversiones():
    from sqlalchemy import text as sql_text
    from collections import defaultdict
    import datetime

    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT id, asin, titulo, unidades, precio_compra_mx, precio_venta_real_mx, "
            "fecha_compra, fecha_liquidacion, estado, roi_real_pct "
            "FROM inversiones ORDER BY fecha_compra"
        )).fetchall()

    hoy = datetime.date.today()
    capital_total = capital_activo = capital_liquidado = roi_sum = 0.0
    roi_count = activas = liquidadas = 0
    alerta_sin_vender: list = []

    for r in rows:
        cap = float(r.precio_compra_mx or 0) * (r.unidades or 1)
        capital_total += cap
        if r.estado == "liquidado":
            capital_liquidado += cap
            liquidadas += 1
            if r.roi_real_pct is not None:
                roi_sum += float(r.roi_real_pct)
                roi_count += 1
        else:
            capital_activo += cap
            activas += 1
            dias = (hoy - r.fecha_compra).days
            if dias > 45:
                alerta_sin_vender.append({
                    "id": r.id, "asin": r.asin,
                    "titulo": r.titulo or r.asin, "dias": dias, "capital": round(cap, 2),
                })

    mensual: dict = defaultdict(lambda: {"capital": 0.0, "ganancia": 0.0, "count": 0})
    for r in rows:
        if r.estado == "liquidado" and r.fecha_liquidacion and r.roi_real_pct is not None:
            mes = str(r.fecha_liquidacion)[:7]
            cap = float(r.precio_compra_mx or 0) * (r.unidades or 1)
            mensual[mes]["capital"]  += cap
            mensual[mes]["ganancia"] += cap * float(r.roi_real_pct) / 100
            mensual[mes]["count"]    += 1

    return {
        "capital_total_invertido": round(capital_total, 2),
        "capital_activo":          round(capital_activo, 2),
        "capital_liquidado":       round(capital_liquidado, 2),
        "roi_real_promedio":       round(roi_sum / roi_count, 1) if roi_count else 0,
        "total_inversiones":       len(rows),
        "activas":                 activas,
        "liquidadas":              liquidadas,
        "alerta_sin_vender":       alerta_sin_vender,
        "historial_mensual":       [{"mes": m, **d} for m, d in sorted(mensual.items())],
    }


@app.get("/inversiones")
async def listar_inversiones():
    from sqlalchemy import text as sql_text
    engine = _get_engine()
    with engine.connect() as conn:
        rows = conn.execute(sql_text(
            "SELECT id, asin, titulo, unidades, precio_compra_mx, precio_venta_real_mx, "
            "fecha_compra, fecha_liquidacion, estado, roi_real_pct, notas, created_at "
            "FROM inversiones ORDER BY created_at DESC"
        )).fetchall()
    return [_row_dict(r) for r in rows]


@app.post("/inversiones")
async def crear_inversion(inv: InversionCreate):
    from sqlalchemy import text as sql_text
    engine = _get_engine()
    with engine.connect() as conn:
        result = conn.execute(sql_text(
            "INSERT INTO inversiones (asin, titulo, unidades, precio_compra_mx, fecha_compra, notas) "
            "VALUES (:asin, :titulo, :unidades, :precio_compra_mx, :fecha_compra, :notas) "
            "RETURNING id, created_at"
        ), {
            "asin": inv.asin, "titulo": inv.titulo, "unidades": inv.unidades,
            "precio_compra_mx": inv.precio_compra_mx, "fecha_compra": inv.fecha_compra,
            "notas": inv.notas,
        })
        conn.commit()
        row = result.fetchone()
    return {"id": row.id, "created_at": str(row.created_at)}


@app.put("/inversiones/{inv_id}")
async def actualizar_inversion(inv_id: int, upd: InversionUpdate):
    from sqlalchemy import text as sql_text
    engine = _get_engine()
    sets, params = [], {"id": inv_id}
    if upd.precio_venta_real_mx is not None:
        sets.append("precio_venta_real_mx = :precio_venta_real_mx")
        params["precio_venta_real_mx"] = upd.precio_venta_real_mx
    if upd.fecha_liquidacion is not None:
        sets.append("fecha_liquidacion = :fecha_liquidacion")
        params["fecha_liquidacion"] = upd.fecha_liquidacion
    if upd.estado is not None:
        sets.append("estado = :estado")
        params["estado"] = upd.estado
    if upd.notas is not None:
        sets.append("notas = :notas")
        params["notas"] = upd.notas
    if upd.roi_real_pct is not None:
        sets.append("roi_real_pct = :roi_real_pct")
        params["roi_real_pct"] = upd.roi_real_pct
    if not sets:
        raise HTTPException(400, "No hay campos para actualizar")
    with engine.connect() as conn:
        conn.execute(sql_text(f"UPDATE inversiones SET {', '.join(sets)} WHERE id = :id"), params)
        conn.commit()
    return {"ok": True}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.1.0",
        "anthropic_key": "set" if os.getenv("ANTHROPIC_API_KEY") else "MISSING",
        "database_url":  "set" if os.getenv("DATABASE_URL") else "MISSING",
    }


@app.get("/test")
async def test_conectividad():
    import httpx
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    resultados: dict = {}

    try:
        r = httpx.get("https://api.anthropic.com", timeout=10)
        resultados["tcp_anthropic"] = {"status": "ok", "http_code": r.status_code}
    except Exception as e:
        resultados["tcp_anthropic"] = {"status": "error", "tipo": type(e).__name__, "msg": str(e)[:200]}

    try:
        custom = httpx.Client(timeout=20.0, trust_env=False)
        client = Anthropic(api_key=api_key, http_client=custom)
        r2 = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "ok"}],
        )
        resultados["anthropic_sdk"] = {"status": "ok", "respuesta": r2.content[0].text}
    except Exception as e:
        resultados["anthropic_sdk"] = {"status": "error", "tipo": type(e).__name__, "msg": str(e)[:200]}

    try:
        from sqlalchemy import create_engine, text as sql_text
        engine = create_engine(os.getenv("DATABASE_URL", ""))
        with engine.connect() as conn:
            n = conn.execute(sql_text("SELECT COUNT(*) FROM productos")).scalar()
        resultados["database"] = {"status": "ok", "productos": n}
    except Exception as e:
        resultados["database"] = {"status": "error", "tipo": type(e).__name__, "msg": str(e)[:200]}

    return resultados


@app.get("/monitor/test")
async def test_monitor():
    """
    Diagnóstico completo del monitor de precios.
    Verifica env vars, inversiones activas, precios en BD y conectividad Telegram.
    """
    import urllib.request, urllib.parse
    from sqlalchemy import create_engine as _ce, text as _t

    resultado: dict = {}

    # 1. Env vars de Telegram
    tok  = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    resultado["telegram_token"] = "set" if tok else "MISSING"
    resultado["telegram_chat"]  = "set" if chat else "MISSING"

    # 2. Inversiones activas en BD
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        try:
            engine = _ce(db_url)
            with engine.connect() as conn:
                inv_count = conn.execute(
                    _t("SELECT COUNT(*) FROM inversiones WHERE estado='activo'")
                ).scalar()
                prod_count = conn.execute(
                    _t("SELECT COUNT(DISTINCT asin) FROM productos")
                ).scalar()
            resultado["inversiones_activas"] = inv_count
            resultado["asins_con_precio_bd"] = prod_count
        except Exception as e:
            resultado["bd_error"] = str(e)[:200]
    else:
        resultado["bd_error"] = "DATABASE_URL no configurada"

    # 3. Test de Telegram (envía mensaje real si credenciales OK)
    if tok and chat:
        try:
            url  = f"https://api.telegram.org/bot{tok}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id":    chat,
                "text":       "✅ Test monitor de precios — conexión OK",
                "parse_mode": "HTML",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                resultado["telegram_test"] = "OK" if resp.status == 200 else f"HTTP {resp.status}"
        except Exception as e:
            resultado["telegram_test"] = f"ERROR: {str(e)[:150]}"
    else:
        resultado["telegram_test"] = "SKIP (faltan credenciales)"

    return resultado

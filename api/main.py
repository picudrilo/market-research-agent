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
    precio_valor, keywords, concepto, listado_optimizado
)
from agents.memoria import limpiar_memoria, leer_memoria
from agents.validador import ejecutar as ejecutar_validador
from agents.batch_arbitraje import ejecutar as ejecutar_batch
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


def detectar_mercado(producto: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY no esta configurada en el servidor")

    client = Anthropic(api_key=api_key)
    try:
        respuesta = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            system=(
                "Responde SOLO con el nombre del nicho de mercado en 1-3 palabras en espanol. "
                "Ejemplos: suplementos, electrodomesticos, ropa deportiva, miel y mermeladas, "
                "snacks saludables, productos de limpieza. Sin puntuacion ni explicacion."
            ),
            messages=[{"role": "user", "content": f"Nicho de Amazon para: {producto}"}]
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
            "total": 9,
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

        limpiar_memoria()

        pasos = [
            (1, "Ingesta de datos",        lambda: ingesta.ejecutar(mercado)),
            (2, "Analisis de competencia", lambda: competencia.ejecutar(mercado)),
            (3, "Analisis de resenas",     lambda: resenas.ejecutar(mercado)),
            (4, "GAP Analysis",            lambda: gap_analysis.ejecutar(mercado)),
            (5, "Precio vs Valor",         lambda: precio_valor.ejecutar(mercado)),
            (6, "Keywords y SEO",          lambda: keywords.ejecutar(mercado)),
        ]

        if modo == "marca_propia":
            pasos += [
                (7, "Concepto de diferenciacion", lambda: concepto.ejecutar(mercado)),
                (8, "Listado optimizado",         lambda: listado_optimizado.ejecutar(mercado)),
            ]
        else:  # arbitraje — skip concepto y listado, ir directo al validador
            pasos += [
                (9, "Validacion de arbitraje", lambda: ejecutar_validador(
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

        mem            = leer_memoria()
        validador_mem  = mem.get("validador",          {}).get("hallazgos", {})
        listado_mem    = mem.get("listado_optimizado", {}).get("hallazgos", {})
        concepto_mem   = mem.get("concepto",           {}).get("hallazgos", {})
        keywords_mem   = mem.get("keywords",           {}).get("hallazgos", {})
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
        }

        jobs[job_id]["result"] = final
        jobs[job_id]["status"] = "done"
        emit({"type": "done", "result": final})

    except Exception as e:
        jobs[job_id]["status"] = "error"
        emit({"type": "error", "message": str(e)})
    finally:
        _pipeline_lock.release()


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
                "bsr":            p.get("bsr"),
                "reviews_count":  p.get("reviews_count"),
                "rating":         p.get("rating"),
                "ventas_mes":     p.get("ventas_mes"),
                "active_sellers": p.get("active_sellers"),
                "fba":            p.get("fba", False),
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

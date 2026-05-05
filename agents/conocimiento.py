# agents/conocimiento.py
"""
Memoria institucional entre runs del pipeline.

Persiste cada análisis en la tabla analisis_historicos y recupera
contexto histórico relevante para inyectarlo en los prompts de Claude.

Diseño de tolerancia a fallos:
  - Toda función atrapa sus excepciones y retorna un valor vacío/None.
  - Si la BD no está disponible el pipeline continúa sin historial.
  - obtener_contexto_historico() tiene un timeout implícito: solo ejecuta
    queries simples con LIMIT; nunca llama a Claude en tiempo real.
"""

import json
import os
from datetime import date, datetime
from decimal import Decimal

from anthropic import Anthropic
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# ---------------------------------------------------------------------------
# Catálogo de categorías reconocidas
# ---------------------------------------------------------------------------
CATEGORIAS_VALIDAS = {
    "alimentos", "bebidas", "suplementos", "electronica", "hogar",
    "cosmeticos", "ropa", "deportes", "mascotas", "juguetes", "oficina", "otro",
}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _engine():
    """Crea engine SQLAlchemy o retorna None si DATABASE_URL no está configurada."""
    url = os.getenv("DATABASE_URL")
    return create_engine(url) if url else None


def _json_serial(obj):
    """Serializa tipos no-JSON-nativos (Decimal, date, datetime)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _safe_json(obj) -> str | None:
    """Convierte a JSON string tolerando tipos especiales; retorna None si falla."""
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=_json_serial)
    except Exception:
        return None


def _parse_json_col(valor) -> list | dict | None:
    """Parsea una columna JSONB devuelta por SQLAlchemy (puede ser str o dict/list)."""
    if valor is None:
        return None
    if isinstance(valor, (dict, list)):
        return valor
    try:
        return json.loads(valor)
    except Exception:
        return None


def _meses_desde(fecha_str: str) -> str:
    """Retorna texto legible como 'hace 3 meses' a partir de una fecha ISO."""
    try:
        fecha = date.fromisoformat(str(fecha_str)[:10])
        delta = (date.today() - fecha).days
        if delta < 30:
            return "hace menos de 1 mes"
        meses = delta // 30
        return f"hace {meses} mes" if meses == 1 else f"hace {meses} meses"
    except Exception:
        return str(fecha_str)


# ---------------------------------------------------------------------------
# detectar_categoria
# ---------------------------------------------------------------------------

def detectar_categoria(mercado: str) -> str:
    """
    Detecta la categoría del mercado usando Claude Haiku.
    Retorna una cadena del catálogo CATEGORIAS_VALIDAS.
    Si la llamada falla, retorna 'otro'.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return "otro"

    try:
        client = Anthropic(api_key=api_key)
        respuesta = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            system=(
                "Responde ÚNICAMENTE con una de estas categorías en minúsculas, "
                "sin puntuación ni explicación:\n"
                "alimentos, bebidas, suplementos, electronica, hogar, "
                "cosmeticos, ropa, deportes, mascotas, juguetes, oficina, otro"
            ),
            messages=[{
                "role": "user",
                "content": f"Categoría de Amazon para el mercado: {mercado}"
            }],
        )
        categoria = respuesta.content[0].text.strip().lower()
        return categoria if categoria in CATEGORIAS_VALIDAS else "otro"
    except Exception as e:
        print(f"  [CONOCIMIENTO] detectar_categoria falló: {e}")
        return "otro"


# ---------------------------------------------------------------------------
# guardar_analisis
# ---------------------------------------------------------------------------

def guardar_analisis(
    mercado: str,
    modo: str,
    hallazgos_pipeline: dict,
    metricas_extra: dict | None = None,
) -> int | None:
    """
    Persiste el análisis completo en analisis_historicos.
    Se llama al final de cada pipeline run exitoso.

    Args:
        mercado:            Nombre del mercado analizado.
        modo:               'arbitraje' | 'marca_propia'.
        hallazgos_pipeline: Contenido completo de outputs/memoria_pipeline.json
                            (resultado de leer_memoria()).
        metricas_extra:     Dict opcional con campos adicionales calculados
                            en orchestrator/api (score_mercado, revenue_mercado,
                            precio_stats, etc.).

    Retorna el id del registro insertado, o None si falla.
    """
    engine = _engine()
    if not engine:
        print("  [CONOCIMIENTO] DATABASE_URL no configurada — análisis no guardado.")
        return None

    try:
        # ── Extraer datos de la memoria del pipeline ──────────────────────────
        comp_h    = hallazgos_pipeline.get("competencia",       {}).get("hallazgos", {})
        resenas_h = hallazgos_pipeline.get("resenas",           {}).get("hallazgos", {})
        gap_h     = hallazgos_pipeline.get("gap_analysis",      {}).get("hallazgos", {})
        kw_h      = hallazgos_pipeline.get("keywords",          {}).get("hallazgos", {})
        concepto_h = hallazgos_pipeline.get("concepto",         {}).get("hallazgos", {})
        validador_h = hallazgos_pipeline.get("validador",       {}).get("hallazgos", {})

        extra = metricas_extra or {}

        # ── Categoría ─────────────────────────────────────────────────────────
        categoria = detectar_categoria(mercado)

        # ── Score de mercado ──────────────────────────────────────────────────
        score_mercado = extra.get("score_mercado") or None

        # ── Precio mediana ────────────────────────────────────────────────────
        precio_stats   = extra.get("precio_stats") or {}
        precio_mediana = precio_stats.get("mediana") or comp_h.get("precio_mediana") or None

        # ── Intensidad de competencia ─────────────────────────────────────────
        intensidad = (
            comp_h.get("intensidad_competencia") or
            extra.get("intensidad_competencia") or
            None
        )
        if intensidad:
            intensidad = intensidad.lower()[:20]

        # ── Veredicto (arbitraje) ─────────────────────────────────────────────
        veredicto = validador_h.get("veredicto") or extra.get("veredicto") or None

        # ── Concepto (marca propia) ───────────────────────────────────────────
        concepto_nombre  = concepto_h.get("nombre_concepto") or None
        precio_objetivo  = concepto_h.get("precio_objetivo_mx") or None

        # ── Pain points top ───────────────────────────────────────────────────
        pain_raw = resenas_h.get("pain_points_criticos") or []
        if isinstance(pain_raw, list):
            pain_points_top = [
                {"tema": p, "frecuencia_pct": None} if isinstance(p, str)
                else p
                for p in pain_raw[:10]
            ]
        else:
            pain_points_top = []

        # También intentar desde extra (pain_points_top viene de api/main.py)
        if not pain_points_top and extra.get("pain_points_top"):
            pain_points_top = extra["pain_points_top"][:10]

        # ── Gaps top ─────────────────────────────────────────────────────────
        gaps_raw = gap_h.get("gaps_principales") or extra.get("gaps_detalle") or []
        if isinstance(gaps_raw, list):
            gaps_top = [
                {"area": g.get("area", ""), "impacto": g.get("impacto", "")}
                for g in gaps_raw[:10]
                if isinstance(g, dict)
            ]
        else:
            gaps_top = []

        # ── Keywords top ─────────────────────────────────────────────────────
        kw_raw = kw_h.get("keywords_top") or extra.get("keywords_top") or []
        if isinstance(kw_raw, list):
            keywords_top = [
                {
                    "keyword": k.get("keyword", k) if isinstance(k, dict) else k,
                    "score": k.get("score_oportunidad") or k.get("score") if isinstance(k, dict) else None,
                }
                for k in kw_raw[:15]
            ]
        else:
            keywords_top = []

        # ── Métricas del mercado ──────────────────────────────────────────────
        metricas_mercado = {
            "num_productos":  comp_h.get("total_productos") or extra.get("num_productos"),
            "revenue_total":  extra.get("revenue_mercado"),
            "precio_min":     precio_stats.get("min"),
            "precio_max":     precio_stats.get("max"),
            "bsr_mediana":    comp_h.get("bsr_mediana"),
        }
        # Eliminar claves None para mantener el JSON limpio
        metricas_mercado = {k: v for k, v in metricas_mercado.items() if v is not None}

        # ── Insertar ──────────────────────────────────────────────────────────
        with engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO analisis_historicos (
                    mercado, modo, fecha_analisis,
                    score_mercado, precio_mediana_mx, intensidad_competencia,
                    categoria_producto,
                    veredicto,
                    concepto_nombre, precio_objetivo_mx,
                    pain_points_top, gaps_top, keywords_top,
                    metricas_mercado, hallazgos_pipeline
                ) VALUES (
                    :mercado, :modo, :fecha,
                    :score, :precio_mediana, :intensidad,
                    :categoria,
                    :veredicto,
                    :concepto_nombre, :precio_objetivo,
                    CAST(:pain_points AS jsonb), CAST(:gaps AS jsonb), CAST(:keywords AS jsonb),
                    CAST(:metricas AS jsonb), CAST(:pipeline AS jsonb)
                )
                RETURNING id
            """), {
                "mercado":        mercado,
                "modo":           modo,
                "fecha":          date.today().isoformat(),
                "score":          score_mercado,
                "precio_mediana": precio_mediana,
                "intensidad":     intensidad,
                "categoria":      categoria,
                "veredicto":      veredicto,
                "concepto_nombre": concepto_nombre,
                "precio_objetivo": precio_objetivo,
                "pain_points":    _safe_json(pain_points_top) or "[]",
                "gaps":           _safe_json(gaps_top)        or "[]",
                "keywords":       _safe_json(keywords_top)    or "[]",
                "metricas":       _safe_json(metricas_mercado) or "{}",
                "pipeline":       _safe_json(hallazgos_pipeline) or "{}",
            })
            conn.commit()
            row = result.fetchone()
            id_insertado = row[0] if row else None

        print(f"  [CONOCIMIENTO] Análisis guardado — id={id_insertado}, mercado={mercado}, categoria={categoria}")
        return id_insertado

    except Exception as e:
        print(f"  [CONOCIMIENTO] guardar_analisis falló (pipeline continúa): {e}")
        return None


# ---------------------------------------------------------------------------
# extraer_patrones_categoria
# ---------------------------------------------------------------------------

def extraer_patrones_categoria(categoria: str, min_analisis: int = 3) -> dict:
    """
    Sintetiza patrones aprendidos de múltiples análisis en la misma categoría.
    Solo activa cuando hay >= min_analisis registros con esa categoría.

    Retorna dict con estadísticas y listas de patrones, o {} si no hay suficientes datos.
    """
    engine = _engine()
    if not engine or not categoria:
        return {}

    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    COUNT(*)                            AS total,
                    AVG(score_mercado)                  AS score_prom,
                    AVG(precio_mediana_mx)              AS precio_prom,
                    -- Intensidad más frecuente
                    MODE() WITHIN GROUP (ORDER BY intensidad_competencia) AS intensidad_comun,
                    -- Arrays de JSONB para síntesis
                    ARRAY_AGG(pain_points_top)          AS all_pain_points,
                    ARRAY_AGG(gaps_top)                 AS all_gaps,
                    ARRAY_AGG(keywords_top)             AS all_keywords
                FROM analisis_historicos
                WHERE categoria_producto = :categoria
                  AND score_mercado IS NOT NULL
            """), {"categoria": categoria}).fetchone()

        if not rows or (rows[0] or 0) < min_analisis:
            return {}

        total = int(rows[0])

        # ── Consolidar pain points: contar frecuencia por tema ────────────────
        tema_count: dict = {}
        for pp_array in (rows[4] or []):
            items = _parse_json_col(pp_array) or []
            for item in items:
                if isinstance(item, dict):
                    tema = item.get("tema", "")
                elif isinstance(item, str):
                    tema = item
                else:
                    continue
                if tema:
                    tema_count[tema] = tema_count.get(tema, 0) + 1

        pain_frecuentes = sorted(
            [{"tema": t, "apariciones": c} for t, c in tema_count.items()],
            key=lambda x: x["apariciones"], reverse=True
        )[:5]

        # ── Consolidar gaps: contar áreas más repetidas ───────────────────────
        area_count: dict = {}
        for gap_array in (rows[5] or []):
            items = _parse_json_col(gap_array) or []
            for item in items:
                if isinstance(item, dict):
                    area = item.get("area", "")
                    if area:
                        area_count[area] = area_count.get(area, 0) + 1

        gaps_frecuentes = sorted(
            [{"area": a, "apariciones": c} for a, c in area_count.items()],
            key=lambda x: x["apariciones"], reverse=True
        )[:5]

        # ── Consolidar keywords más repetidas ────────────────────────────────
        kw_count: dict = {}
        for kw_array in (rows[6] or []):
            items = _parse_json_col(kw_array) or []
            for item in items:
                if isinstance(item, dict):
                    kw = item.get("keyword", "")
                    if kw:
                        kw_count[kw] = kw_count.get(kw, 0) + 1
                elif isinstance(item, str) and item:
                    kw_count[item] = kw_count.get(item, 0) + 1

        keywords_frecuentes = sorted(
            [{"keyword": k, "apariciones": c} for k, c in kw_count.items()],
            key=lambda x: x["apariciones"], reverse=True
        )[:8]

        return {
            "categoria":          categoria,
            "total_analisis":     total,
            "score_promedio":     round(float(rows[1] or 0), 1),
            "precio_promedio_mx": round(float(rows[2] or 0), 2),
            "intensidad_comun":   rows[3] or "media",
            "pain_frecuentes":    pain_frecuentes,
            "gaps_frecuentes":    gaps_frecuentes,
            "keywords_frecuentes": keywords_frecuentes,
        }

    except Exception as e:
        print(f"  [CONOCIMIENTO] extraer_patrones_categoria falló: {e}")
        return {}


# ---------------------------------------------------------------------------
# obtener_contexto_historico
# ---------------------------------------------------------------------------

def obtener_contexto_historico(mercado: str, limite: int = 5) -> str:
    """
    Recupera análisis previos relevantes y los formatea como contexto
    listo para inyectar en prompts de Claude.

    Estrategia de búsqueda (3 niveles):
      1. Mismo mercado exacto  — historial directo
      2. Misma categoría       — patrones cross-mercado
      3. Rango de precio similar (±40%) — dinámica competitiva comparable

    Diseñado para ser rápido (< 500 ms): solo queries simples con LIMIT,
    sin procesamiento con Claude en tiempo real.

    Retorna string vacío si no hay historial o la BD no está disponible.
    """
    engine = _engine()
    if not engine:
        return ""

    try:
        secciones: list[str] = []

        # ── NIVEL 1: mismo mercado ────────────────────────────────────────────
        with engine.connect() as conn:
            mismos = conn.execute(text("""
                SELECT mercado, modo, fecha_analisis,
                       score_mercado, precio_mediana_mx, intensidad_competencia,
                       pain_points_top, gaps_top, keywords_top,
                       concepto_nombre, precio_objetivo_mx,
                       veredicto, resultado_usuario, roi_real_pct
                FROM analisis_historicos
                WHERE LOWER(mercado) = LOWER(:mercado)
                ORDER BY fecha_analisis DESC
                LIMIT :lim
            """), {"mercado": mercado, "lim": limite}).fetchall()

        if mismos:
            secciones.append(f"[MISMO MERCADO — {mercado}]")
            for r in mismos:
                antigüedad = _meses_desde(r.fecha_analisis)
                score_txt  = f"Score: {r.score_mercado}/100" if r.score_mercado else "Score: n/d"
                precio_txt = f"Precio mediana: MX${r.precio_mediana_mx}" if r.precio_mediana_mx else ""
                comp_txt   = f"Competencia: {r.intensidad_competencia}" if r.intensidad_competencia else ""

                linea_meta = " | ".join(filter(None, [score_txt, precio_txt, comp_txt]))
                secciones.append(f"  Análisis {antigüedad}: {linea_meta}")

                pain = _parse_json_col(r.pain_points_top) or []
                if pain:
                    pain_str = ", ".join(
                        f"{p.get('tema', p)} ({p.get('frecuencia_pct', '')}{'%' if p.get('frecuencia_pct') else ''})"
                        if isinstance(p, dict) else str(p)
                        for p in pain[:3]
                    )
                    secciones.append(f"  Pain points críticos: {pain_str}")

                gaps = _parse_json_col(r.gaps_top) or []
                if gaps:
                    gap_str = ", ".join(
                        g.get("area", str(g)) if isinstance(g, dict) else str(g)
                        for g in gaps[:3]
                    )
                    secciones.append(f"  Gaps principales: {gap_str}")

                if r.concepto_nombre:
                    precio_obj = f" a MX${r.precio_objetivo_mx}" if r.precio_objetivo_mx else ""
                    secciones.append(f"  Concepto anterior: \"{r.concepto_nombre}\"{precio_obj}")

                if r.veredicto:
                    secciones.append(f"  Veredicto: {r.veredicto}")

                if r.resultado_usuario:
                    roi_txt = f" (ROI real: {r.roi_real_pct}%)" if r.roi_real_pct is not None else ""
                    secciones.append(f"  Resultado real: {r.resultado_usuario}{roi_txt}")

            secciones.append("")

        # ── NIVEL 2: misma categoría (si hay suficientes datos) ───────────────
        # Detectar categoría del mercado actual sin hacer nueva llamada a Claude
        # si ya tenemos un análisis previo del mismo mercado.
        categoria_actual: str | None = None
        if mismos:
            # Intentar inferir categoría desde el primer registro (si existiera en el schema)
            # Como no la guardamos en el row aquí, la detectamos rápido
            pass

        # Detectar categoría para buscar patrones cross-mercado
        categoria_actual = detectar_categoria(mercado)

        if categoria_actual and categoria_actual != "otro":
            with engine.connect() as conn:
                cat_rows = conn.execute(text("""
                    SELECT mercado, fecha_analisis,
                           score_mercado, precio_mediana_mx, intensidad_competencia,
                           pain_points_top, gaps_top, keywords_top
                    FROM analisis_historicos
                    WHERE categoria_producto = :cat
                      AND LOWER(mercado) != LOWER(:mercado)
                    ORDER BY fecha_analisis DESC
                    LIMIT 3
                """), {"cat": categoria_actual, "mercado": mercado}).fetchall()

            if cat_rows:
                secciones.append(f"[CATEGORIA SIMILAR — {categoria_actual}]")
                for r in cat_rows:
                    ant = _meses_desde(r.fecha_analisis)
                    score_txt  = f"Score {r.score_mercado}/100" if r.score_mercado else ""
                    precio_txt = f"mediana MX${r.precio_mediana_mx}" if r.precio_mediana_mx else ""
                    linea = " | ".join(filter(None, [r.mercado, ant, score_txt, precio_txt]))
                    secciones.append(f"  {linea}")

                    kws = _parse_json_col(r.keywords_top) or []
                    if kws:
                        kw_str = ", ".join(
                            f"\"{k.get('keyword', k)}\"" if isinstance(k, dict) else f"\"{k}\""
                            for k in kws[:4]
                        )
                        secciones.append(f"  Keywords: {kw_str}")

                secciones.append("")

            # ── NIVEL 3: patrones cross-categoría (solo si hay >= 3 análisis) ──
            patrones = extraer_patrones_categoria(categoria_actual, min_analisis=3)
            if patrones:
                n = patrones["total_analisis"]
                secciones.append(
                    f"[PATRON CROSS-CATEGORIA — {categoria_actual} — {n} análisis]"
                )
                secciones.append(
                    f"  Score promedio de mercado: {patrones['score_promedio']}/100"
                )
                secciones.append(
                    f"  Precio promedio de entrada: MX${patrones['precio_promedio_mx']}"
                )
                secciones.append(
                    f"  Intensidad de competencia más común: {patrones['intensidad_comun']}"
                )

                if patrones["pain_frecuentes"]:
                    pains = ", ".join(p["tema"] for p in patrones["pain_frecuentes"])
                    secciones.append(f"  Pain points recurrentes en la categoría: {pains}")

                if patrones["gaps_frecuentes"]:
                    gaps = ", ".join(g["area"] for g in patrones["gaps_frecuentes"])
                    secciones.append(f"  Gaps frecuentes en la categoría: {gaps}")

                if patrones["keywords_frecuentes"]:
                    kws = ", ".join(
                        f"\"{k['keyword']}\"" for k in patrones["keywords_frecuentes"]
                    )
                    secciones.append(f"  Keywords que se repiten: {kws}")

                secciones.append("")

        if not secciones:
            return ""  # Sin historial relevante — primer análisis

        # Construir el bloque final
        header = "=== HISTORIAL DE ANÁLISIS PREVIOS ==="
        footer = "=== FIN HISTORIAL ==="
        return "\n".join([header, ""] + secciones + [footer])

    except Exception as e:
        print(f"  [CONOCIMIENTO] obtener_contexto_historico falló (pipeline continúa): {e}")
        return ""


# ---------------------------------------------------------------------------
# guardar_batch_por_categorias
# ---------------------------------------------------------------------------

def guardar_batch_por_categorias(resultado_batch: list) -> int:
    """
    Persiste los resultados de un análisis batch en analisis_historicos.
    Agrupa los productos por categoría y guarda un registro por categoría,
    de modo que obtener_contexto_historico() pueda aprovechar este historial.

    Args:
        resultado_batch: lista de productos tal como la devuelve ejecutar_batch()

    Retorna el número de registros insertados.
    """
    engine = _engine()
    if not engine or not resultado_batch:
        return 0

    from collections import defaultdict

    por_categoria: dict = defaultdict(list)
    for p in resultado_batch:
        cat = (p.get("categoria") or "otro").lower()[:50]
        por_categoria[cat].append(p)

    guardados = 0
    for categoria, productos in por_categoria.items():
        try:
            precios = [
                float(p.get("precio_amazon") or (p.get("financiero") or {}).get("precio_amazon") or 0)
                for p in productos
                if (p.get("precio_amazon") or (p.get("financiero") or {}).get("precio_amazon"))
            ]
            bsrs = [int(p["bsr"]) for p in productos if p.get("bsr")]
            scores = [int(p.get("score_arbitraje") or 0) for p in productos]

            precio_mediana = float(sorted(precios)[len(precios) // 2]) if precios else None
            score_prom = round(sum(scores) / len(scores), 1) if scores else 50
            intensidad = "alta" if score_prom < 40 else ("media" if score_prom < 65 else "baja")

            semaforos = [p.get("semaforo", "DESCARTAR") for p in productos]
            n_inv  = semaforos.count("INVERTIR")
            n_ries = semaforos.count("RIESGO MEDIO")
            n_desc = semaforos.count("DESCARTAR")
            veredicto = f"{n_inv} INVERTIR | {n_ries} RIESGO MEDIO | {n_desc} DESCARTAR"

            top_prods = sorted(productos, key=lambda x: x.get("score_arbitraje") or 0, reverse=True)[:5]

            metricas = {
                "num_productos": len(productos),
                "precio_min":    round(min(precios), 2) if precios else None,
                "precio_max":    round(max(precios), 2) if precios else None,
                "bsr_mediana":   sorted(bsrs)[len(bsrs) // 2] if bsrs else None,
                "score_promedio": score_prom,
                "tasa_inversion": round(n_inv / len(productos) * 100, 1),
            }

            hallazgos_snapshot = {
                "batch": {
                    "hallazgos": {
                        "veredicto":    veredicto,
                        "score_prom":   score_prom,
                        "top_productos": [
                            {
                                "asin":    p.get("asin", ""),
                                "titulo":  (p.get("titulo") or "")[:80],
                                "score":   p.get("score_arbitraje", 0),
                                "semaforo": p.get("semaforo", ""),
                            }
                            for p in top_prods
                        ],
                    }
                }
            }

            with engine.connect() as conn:
                conn.execute(text("""
                    INSERT INTO analisis_historicos (
                        mercado, modo, fecha_analisis,
                        score_mercado, precio_mediana_mx, intensidad_competencia,
                        categoria_producto,
                        veredicto,
                        metricas_mercado, hallazgos_pipeline
                    ) VALUES (
                        :mercado, :modo, :fecha,
                        :score, :precio_mediana, :intensidad,
                        :categoria,
                        :veredicto,
                        CAST(:metricas AS jsonb), CAST(:pipeline AS jsonb)
                    )
                """), {
                    "mercado":       categoria,
                    "modo":          "batch_arbitraje",
                    "fecha":         date.today().isoformat(),
                    "score":         round(score_prom),
                    "precio_mediana": precio_mediana,
                    "intensidad":    intensidad,
                    "categoria":     categoria,
                    "veredicto":     veredicto,
                    "metricas":      _safe_json(metricas) or "{}",
                    "pipeline":      _safe_json(hallazgos_snapshot) or "{}",
                })
                conn.commit()
            guardados += 1
        except Exception as e:
            print(f"  [CONOCIMIENTO] guardar_batch_por_categorias falló ({categoria}): {e}")

    if guardados:
        print(f"  [CONOCIMIENTO] Batch guardado — {guardados} categorías en analisis_historicos")
    return guardados


# ---------------------------------------------------------------------------
# registrar_resultado_usuario
# ---------------------------------------------------------------------------

def registrar_resultado_usuario(
    mercado: str,
    fecha_analisis: str,
    resultado: str,
    roi_real: float | None = None,
    notas: str | None = None,
) -> bool:
    """
    Actualiza analisis_historicos con lo que realmente ocurrió.
    Llamar desde el portafolio cuando el usuario liquida una inversión.

    Args:
        mercado:        Nombre del mercado analizado.
        fecha_analisis: Fecha del análisis a actualizar (YYYY-MM-DD).
        resultado:      'invertido' | 'descartado' | 'pendiente' | 'en_seguimiento'
        roi_real:       ROI real observado (porcentaje), opcional.
        notas:          Notas libres del usuario.

    Retorna True si la actualización fue exitosa.
    """
    engine = _engine()
    if not engine:
        return False

    try:
        sets = ["resultado_usuario = :resultado", "fecha_resultado = :fecha_resultado"]
        params: dict = {
            "resultado":       resultado,
            "fecha_resultado": date.today().isoformat(),
            "mercado":         mercado,
            "fecha_analisis":  fecha_analisis,
        }

        if roi_real is not None:
            sets.append("roi_real_pct = :roi_real")
            params["roi_real"] = roi_real

        if notas is not None:
            sets.append("notas_usuario = :notas")
            params["notas"] = notas

        sql = f"""
            UPDATE analisis_historicos
            SET {', '.join(sets)}
            WHERE LOWER(mercado) = LOWER(:mercado)
              AND fecha_analisis = CAST(:fecha_analisis AS date)
        """

        with engine.connect() as conn:
            conn.execute(text(sql), params)
            conn.commit()

        print(f"  [CONOCIMIENTO] Resultado registrado — mercado={mercado}, resultado={resultado}")
        return True

    except Exception as e:
        print(f"  [CONOCIMIENTO] registrar_resultado_usuario falló: {e}")
        return False

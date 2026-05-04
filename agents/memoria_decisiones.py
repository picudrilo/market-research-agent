# agents/memoria_decisiones.py
"""
Memoria de decisiones: registra automáticamente cada análisis en PostgreSQL
y consulta decisiones previas de mercados similares para enriquecer el contexto.
"""
import os
from datetime import date
from sqlalchemy import create_engine, text


def _engine():
    url = os.getenv("DATABASE_URL")
    return create_engine(url) if url else None


# ─── Registro automático ──────────────────────────────────────────────────────

def registrar_decision(
    mercado: str,
    veredicto_sistema: str,
    score_oportunidad: int,
    roi_estimado_pct: float,
    precio_compra_mx: float = 0,
    asin: str = "",
) -> int | None:
    """
    Guarda una decisión del pipeline. Llamar después de completar el análisis.
    Retorna el id insertado, o None si falla.
    """
    engine = _engine()
    if not engine:
        return None
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO decisiones
                  (asin, mercado, veredicto_sistema, score_oportunidad,
                   roi_estimado_pct, precio_compra_mx, fecha_decision)
                VALUES
                  (:asin, :mercado, :veredicto, :score,
                   :roi, :precio, :fecha)
                RETURNING id
            """), {
                "asin":    asin or None,
                "mercado": mercado,
                "veredicto": veredicto_sistema,
                "score":   score_oportunidad,
                "roi":     roi_estimado_pct,
                "precio":  precio_compra_mx or None,
                "fecha":   date.today().isoformat(),
            })
            conn.commit()
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"  [DECISIONES] Error al registrar: {e}")
        return None


# ─── Consulta de contexto previo ──────────────────────────────────────────────

def obtener_contexto_previo(mercado: str, asin: str = "") -> list[dict]:
    """
    Busca decisiones anteriores para el mismo mercado o ASIN.
    Retorna lista de dicts con las decisiones más relevantes (máx 5).
    """
    engine = _engine()
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, asin, mercado, veredicto_sistema, score_oportunidad,
                       roi_estimado_pct, decision_usuario, resultado_real,
                       roi_real_pct, fecha_decision, lecciones
                FROM decisiones
                WHERE mercado ILIKE :mercado
                   OR (:asin != '' AND asin = :asin)
                ORDER BY created_at DESC
                LIMIT 5
            """), {"mercado": f"%{mercado}%", "asin": asin or ""}).fetchall()

        return [
            {
                "id":               r[0],
                "asin":             r[1] or "",
                "mercado":          r[2],
                "veredicto_sistema": r[3],
                "score_oportunidad": r[4],
                "roi_estimado_pct":  float(r[5]) if r[5] is not None else None,
                "decision_usuario":  r[6],
                "resultado_real":    r[7],
                "roi_real_pct":      float(r[8]) if r[8] is not None else None,
                "fecha_decision":    str(r[9]),
                "lecciones":         r[10] or "",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"  [DECISIONES] Error al consultar: {e}")
        return []


def formatear_contexto_para_claude(decisiones: list[dict]) -> str:
    """
    Convierte decisiones previas en texto para incluir en prompts de Claude.
    """
    if not decisiones:
        return ""

    lineas = ["DECISIONES PREVIAS EN ESTE MERCADO:"]
    for d in decisiones:
        resultado = d["resultado_real"]
        roi_real  = f" (ROI real: {d['roi_real_pct']:.1f}%)" if d["roi_real_pct"] is not None else ""
        lineas.append(
            f"- [{d['fecha_decision']}] {d['mercado']}"
            f" | Veredicto: {d['veredicto_sistema']} (score {d['score_oportunidad']})"
            f" | Resultado: {resultado}{roi_real}"
            + (f"\n  Lección: {d['lecciones']}" if d["lecciones"] else "")
        )
    return "\n".join(lineas)

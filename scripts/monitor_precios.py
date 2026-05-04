# scripts/monitor_precios.py
"""
Monitor de precios diario para arbitraje Amazon México.
Detecta cambios de precio > 10% en ASINs activos del portafolio
y envía alertas Telegram cuando el semáforo cambia de categoría.

Railway Cron (servicio separado):
  schedule: "0 9 * * *"
  command:  "python scripts/monitor_precios.py"

Variables .env requeridas:
  DATABASE_URL        (ya existe)
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import date

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from agents.batch_arbitraje import calcular_financiero, asignar_semaforo

UMBRAL_CAMBIO_PCT = 10.0
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT     = os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────
# BLOQUE 1 — Fuente de ASINs: PostgreSQL
# ─────────────────────────────────────────────

def leer_inversiones_activas(engine) -> list[dict]:
    """
    Lee inversiones activas desde PostgreSQL.
    Estas son el origen de verdad — no depende del filesystem efímero.
    """
    try:
        sql = text("""
            SELECT i.asin, i.titulo, i.precio_compra_mx,
                   p.precio              AS precio_amazon_prev,
                   p.bsr                 AS bsr_prev,
                   p.reviews_count       AS reviews_prev,
                   p.rating              AS rating_prev,
                   p.ventas_mensuales_asin AS ventas_prev
            FROM inversiones i
            LEFT JOIN (
                SELECT DISTINCT ON (asin)
                    asin, precio, bsr, reviews_count, rating, ventas_mensuales_asin
                FROM productos
                ORDER BY asin, fecha_captura DESC
            ) p ON p.asin = i.asin
            WHERE i.estado = 'activo'
              AND i.asin IS NOT NULL
              AND i.asin != ''
        """)
        with engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            {
                "asin":              r[0],
                "titulo":            r[1] or r[0],
                "precio_compra":     float(r[2] or 0),
                "precio_amazon_prev": float(r[3] or 0),
                "bsr":               int(r[4] or 0),
                "reviews_count":     int(r[5] or 0),
                "rating":            float(r[6] or 0),
                "ventas_mes":        int(r[7] or 0),
            }
            for r in rows
            if r[2] and r[2] > 0   # debe tener precio de compra
        ]
    except Exception as e:
        print(f"  [bd] Error leyendo inversiones: {type(e).__name__}: {e}")
        return []


def obtener_precio_actual(asin: str, engine) -> tuple[float | None, str | None]:
    """
    Precio más reciente en la tabla productos (capturado por el pipeline).
    """
    try:
        sql = text("""
            SELECT precio, fecha_captura
            FROM productos
            WHERE asin = :asin
            ORDER BY fecha_captura DESC
            LIMIT 1
        """)
        with engine.connect() as conn:
            row = conn.execute(sql, {"asin": asin}).fetchone()
        if row and row[0]:
            return float(row[0]), str(row[1])
        return None, None
    except Exception as e:
        print(f"  [bd] {asin}: {type(e).__name__}: {e}")
        return None, None


# ─────────────────────────────────────────────
# BLOQUE 2 — Telegram
# ─────────────────────────────────────────────

def enviar_telegram(mensaje: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("  [telegram] TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados")
        print(f"  [telegram] Mensaje (solo consola):\n  {mensaje[:300]}")
        return False

    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    TELEGRAM_CHAT,
        "text":       mensaje,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if ok:
                print("  [telegram] Enviado OK")
            else:
                print(f"  [telegram] HTTP {resp.status}")
            return ok
    except Exception as e:
        print(f"  [telegram] Error: {e}")
        return False


def enviar_resumen_diario(revisados: int, cambios: int, alertas: int, sin_precio: int):
    """Mensaje de diagnóstico diario aunque no haya alertas."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    msg = (
        f"📊 <b>Monitor diario — {date.today().strftime('%d/%m/%Y')}</b>\n"
        f"ASINs revisados: {revisados}\n"
        f"Con cambio &gt;{UMBRAL_CAMBIO_PCT:.0f}%: {cambios}\n"
        f"Alertas enviadas: {alertas}\n"
        f"Sin precio en BD: {sin_precio}"
    )
    enviar_telegram(msg)


def formatear_alerta(inv: dict, fin_nuevo: dict, semaforo_nuevo: str,
                     precio_actual: float, precio_prev: float) -> str:
    cambio_pct    = (precio_actual - precio_prev) / precio_prev * 100
    roi_prev      = calcular_financiero({
        "precio_amazon": precio_prev,
        "precio_compra": inv["precio_compra"],
        "fees": None,
    })
    roi_prev_val  = roi_prev["roi"] if roi_prev else 0

    semaforo_prev = asignar_semaforo(roi_prev_val, inv.get("bsr", 50000))

    if semaforo_nuevo == "INVERTIR" and semaforo_prev != "INVERTIR":
        cabecera = "🟢 <b>NUEVA OPORTUNIDAD</b>"
    elif semaforo_nuevo == "DESCARTAR" and semaforo_prev != "DESCARTAR":
        cabecera = "🔴 <b>ALERTA DE RIESGO</b>"
    else:
        cabecera = "🟡 <b>CAMBIO DE PRECIO</b>"

    signo = "↑" if cambio_pct > 0 else "↓"

    return (
        f"{cabecera}\n"
        f"Producto: {inv['titulo'][:60]}\n"
        f"ASIN: <code>{inv['asin']}</code>\n\n"
        f"Precio {signo}: MX${precio_prev:,.0f} → MX${precio_actual:,.0f} "
        f"({cambio_pct:+.1f}%)\n"
        f"ROI: {roi_prev_val:.1f}% → {fin_nuevo['roi']:.1f}%\n"
        f"Semáforo: {semaforo_prev} → {semaforo_nuevo}\n"
        f"Compra original: MX${inv['precio_compra']:,.0f}\n\n"
        f"📅 {date.today().strftime('%d/%m/%Y')}"
    )


# ─────────────────────────────────────────────
# BLOQUE 3 — Ejecución principal
# ─────────────────────────────────────────────

def ejecutar():
    print(f"\n{'='*50}")
    print("MONITOR DE PRECIOS DIARIO")
    print(f"{'='*50}")
    print(f"  Fecha: {date.today().isoformat()}")
    print(f"  Telegram: {'configurado' if TELEGRAM_TOKEN and TELEGRAM_CHAT else 'SIN CONFIGURAR'}")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("  ERROR: DATABASE_URL no configurada")
        return

    engine = create_engine(db_url)

    inversiones = leer_inversiones_activas(engine)
    if not inversiones:
        print("  Sin inversiones activas en la BD — nada que monitorear")
        print("  (Registra inversiones desde el dashboard para activar el monitor)")
        enviar_telegram(
            f"ℹ️ Monitor {date.today().strftime('%d/%m/%Y')}: "
            "sin inversiones activas registradas en portafolio."
        )
        return

    print(f"  {len(inversiones)} inversiones activas\n")

    revisados  = 0
    cambios    = 0
    alertas    = 0
    sin_precio = 0

    for inv in inversiones:
        asin = inv["asin"]
        precio_prev = inv["precio_amazon_prev"]

        precio_actual, fecha_bd = obtener_precio_actual(asin, engine)
        if not precio_actual:
            print(f"  {asin}: sin precio reciente en BD — omitiendo")
            sin_precio += 1
            continue

        if not precio_prev or precio_prev == 0:
            # Primera vez que vemos este ASIN — guardar como referencia
            print(f"  {asin}: sin precio previo de referencia — omitiendo esta vez")
            sin_precio += 1
            continue

        revisados += 1
        cambio_pct = abs((precio_actual - precio_prev) / precio_prev * 100)

        print(f"  {asin}: MX${precio_prev:.0f} → MX${precio_actual:.0f} "
              f"({cambio_pct:+.1f}%)", end="")

        if cambio_pct < UMBRAL_CAMBIO_PCT:
            print(" — sin alerta")
            continue

        cambios += 1

        fin_nuevo = calcular_financiero({
            "precio_amazon": precio_actual,
            "precio_compra": inv["precio_compra"],
            "fees": None,
        })
        if not fin_nuevo:
            print(" — no se pudo calcular financiero")
            continue

        semaforo_nuevo = asignar_semaforo(fin_nuevo["roi"], inv.get("bsr") or 50000)
        print(f" → ROI {fin_nuevo['roi']:.1f}% | {semaforo_nuevo}")

        mensaje = formatear_alerta(inv, fin_nuevo, semaforo_nuevo, precio_actual, precio_prev)
        ok = enviar_telegram(mensaje)
        if ok:
            alertas += 1

    print(f"\n  Revisados: {revisados} | Cambio >10%: {cambios} | "
          f"Alertas: {alertas} | Sin precio BD: {sin_precio}")

    # Resumen diario aunque no haya alertas
    enviar_resumen_diario(revisados, cambios, alertas, sin_precio)
    print("  Monitor completado.")


if __name__ == "__main__":
    ejecutar()

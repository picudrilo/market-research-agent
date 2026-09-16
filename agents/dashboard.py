# agents/dashboard.py
import json
import webbrowser
import pandas as pd
from pathlib import Path
from datetime import datetime

REPORTS_DIR = Path("reports")
OUTPUTS_DIR = Path("outputs")


def _si(val, default=0):
    """Safe int — handles NaN, None, and non-numeric gracefully."""
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────
# BLOQUE 1 — Carga de datos
# ─────────────────────────────────────────────

def cargar_datos():
    datos = {}

    # JSON principal del listing
    path_rec = OUTPUTS_DIR / "final_recommendation.json"
    datos["listing"] = json.loads(path_rec.read_text(encoding="utf-8")) if path_rec.exists() else {}

    # Memoria del pipeline
    path_mem = OUTPUTS_DIR / "memoria_pipeline.json"
    datos["memoria"] = json.loads(path_mem.read_text(encoding="utf-8")) if path_mem.exists() else {}

    # CSVs
    for nombre, archivo in [
        ("competidores", "competidores_ranking.csv"),
        ("keywords",     "keywords_opportunity.csv"),
        ("gaps",         "gap_opportunities.csv"),
        ("pain_points",  "pain_points_ranked.csv"),
    ]:
        path = OUTPUTS_DIR / archivo
        if path.exists():
            try:
                datos[nombre] = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                datos[nombre] = pd.DataFrame()
        else:
            datos[nombre] = pd.DataFrame()

    return datos


# ─────────────────────────────────────────────
# BLOQUE 2 — Cálculo de score de mercado
# ─────────────────────────────────────────────

def calcular_score(datos):
    score = 50
    mem   = datos["memoria"]

    # Intensidad de competencia
    intensidad = mem.get("competencia", {}).get("hallazgos", {}).get("intensidad_competencia", "alta")
    score += {"baja": 20, "media": 5, "alta": -10}.get(intensidad.lower(), 0)

    # Keywords de alta oportunidad
    df_kw = datos["keywords"]
    if not df_kw.empty and "nivel_oportunidad" in df_kw.columns:
        altas = len(df_kw[df_kw["nivel_oportunidad"] == "Alta oportunidad"])
        score += min(altas * 2, 20)

    # Gaps de alto impacto
    df_gaps = datos["gaps"]
    if not df_gaps.empty and "impacto" in df_gaps.columns:
        altos = len(df_gaps[df_gaps["impacto"] == "Alto"])
        score += min(altos * 3, 15)

    # Revenue total del mercado
    df_comp = datos["competidores"]
    if not df_comp.empty and "revenue_mensual_asin" in df_comp.columns:
        revenue = df_comp["revenue_mensual_asin"].dropna().sum()
        if revenue > 1_000_000:
            score += 15
        elif revenue > 500_000:
            score += 10
        elif revenue > 100_000:
            score += 5

    return max(0, min(100, round(score)))


# ─────────────────────────────────────────────
# BLOQUE 3 — Helpers de formato
# ─────────────────────────────────────────────

def fmt_mx(n):
    try:
        n = float(n)
        if n >= 1_000_000:
            return f"MX${n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"MX${n:,.0f}"
        return f"MX${n:.0f}"
    except Exception:
        return "—"


def score_color(score):
    if score >= 71:
        return "#43e97b"
    if score >= 41:
        return "#f9ca24"
    return "#ff6b6b"


def nivel_badge(nivel):
    colores = {
        "Alta oportunidad": ("bg-green", "#43e97b", "#0a2a1a"),
        "Oportunidad media": ("bg-yellow", "#f9ca24", "#2a2200"),
        "Competida": ("bg-red", "#ff6b6b", "#2a0a0a"),
    }
    color, fg, bg_c = colores.get(nivel, ("", "#aaa", "#222"))
    return f'<span style="background:{bg_c};color:{fg};padding:2px 8px;border-radius:99px;font-size:0.7rem;font-weight:600;white-space:nowrap">{nivel}</span>'


def impacto_badge(impacto):
    colores = {"Alto": ("#43e97b", "#0a2a1a"), "Medio": ("#f9ca24", "#2a2200"), "Bajo": ("#ff6b6b", "#2a0a0a")}
    fg, bg_c = colores.get(impacto, ("#aaa", "#222"))
    return f'<span style="background:{bg_c};color:{fg};padding:2px 8px;border-radius:99px;font-size:0.7rem;font-weight:600">{impacto}</span>'


def escape_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ─────────────────────────────────────────────
# BLOQUE 4 — Secciones HTML
# ─────────────────────────────────────────────

def seccion_hero(datos, score, mercado, fecha):
    mem       = datos["memoria"]
    listing   = datos["listing"]
    precio_rec = listing.get("recomendacion_precio", {})
    precio_l  = precio_rec.get("precio_lanzamiento_mx", 0)
    precio_o  = precio_rec.get("precio_objetivo_mx", 0)
    intensidad = mem.get("competencia", {}).get("hallazgos", {}).get("intensidad_competencia", "—").upper()
    color_sc  = score_color(score)

    color_int = {"ALTA": "#ff6b6b", "MEDIA": "#f9ca24", "BAJA": "#43e97b"}.get(intensidad, "#aaa")

    # SVG gauge
    pct   = score / 100
    # arco de 220 grados (de -110° a +110°)
    r = 70
    cx, cy = 90, 90
    import math
    start_deg = 220   # grados desde horizontal izquierda (sentido horario desde -110)
    sweep_deg = 220
    angle_start = math.radians(180 + 110)   # -110 desde "3 o'clock"
    angle_end   = angle_start + math.radians(sweep_deg * pct)

    def polar(angle, radius=r):
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    sx, sy   = polar(math.radians(180 + 110))
    ex_f, ey_f = polar(math.radians(180 + 110 + 220))
    ex, ey   = polar(angle_end)
    large    = 1 if sweep_deg * pct > 180 else 0

    gauge_svg = f"""
<svg viewBox="0 0 180 110" width="200" height="120" style="overflow:visible">
  <defs>
    <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#ff6b6b"/>
      <stop offset="50%" style="stop-color:#f9ca24"/>
      <stop offset="100%" style="stop-color:#43e97b"/>
    </linearGradient>
  </defs>
  <!-- track -->
  <path d="M {sx:.1f} {sy:.1f} A {r} {r} 0 1 1 {ex_f:.1f} {ey_f:.1f}" fill="none" stroke="#2a2a3a" stroke-width="10" stroke-linecap="round"/>
  <!-- fill -->
  <path d="M {sx:.1f} {sy:.1f} A {r} {r} 0 {large} 1 {ex:.1f} {ey:.1f}" fill="none" stroke="url(#gaugeGrad)" stroke-width="10" stroke-linecap="round"/>
  <text x="{cx}" y="{cy+8}" text-anchor="middle" font-size="28" font-weight="700" fill="{color_sc}" font-family="Syne,sans-serif">{score}</text>
  <text x="{cx}" y="{cy+26}" text-anchor="middle" font-size="9" fill="#666" font-family="DM Sans,sans-serif">SCORE DE MERCADO</text>
</svg>"""

    return f"""
<section class="hero reveal">
  <div class="hero-top">
    <div class="hero-info">
      <div class="badge-row">
        <span class="badge-market">MERCADO ANALIZADO</span>
        <span style="background:#1a1a2e;color:{color_int};padding:3px 10px;border-radius:99px;font-size:0.72rem;font-weight:600;border:1px solid {color_int}33">
          COMPETENCIA {intensidad}
        </span>
      </div>
      <h1 class="hero-title">{escape_html(mercado.upper())}</h1>
      <p class="hero-date">{fecha}</p>
      <div class="price-row">
        <div class="price-card">
          <div class="price-label">Lanzamiento</div>
          <div class="price-val">{fmt_mx(precio_l)}</div>
        </div>
        <div class="price-arrow">→</div>
        <div class="price-card accent">
          <div class="price-label">Precio objetivo</div>
          <div class="price-val">{fmt_mx(precio_o)}</div>
        </div>
      </div>
    </div>
    <div class="gauge-wrap">
      {gauge_svg}
      <div class="score-legend">
        <span style="color:#ff6b6b">0-40 Riesgo</span>
        <span style="color:#f9ca24">41-70 Medio</span>
        <span style="color:#43e97b">71-100 Bueno</span>
      </div>
    </div>
  </div>
</section>"""


def seccion_metricas(datos):
    df_comp = datos["competidores"]
    df_kw   = datos["keywords"]
    df_gaps = datos["gaps"]
    mem     = datos["memoria"]

    total_prod = len(df_comp.drop_duplicates(subset=["asin"]) if "asin" in df_comp.columns else df_comp) if not df_comp.empty else "—"

    revenue_total = "—"
    if not df_comp.empty and "revenue_mensual_asin" in df_comp.columns:
        rev = df_comp.drop_duplicates(subset=["asin"])["revenue_mensual_asin"].dropna().sum() if "asin" in df_comp.columns else df_comp["revenue_mensual_asin"].dropna().sum()
        revenue_total = fmt_mx(rev)

    total_kw   = len(df_kw) if not df_kw.empty else "—"
    total_gaps = len(df_gaps) if not df_gaps.empty else "—"

    items = [
        ("📦", "Productos en mercado",   str(total_prod), "#6c63ff"),
        ("💰", "Revenue mensual total",  revenue_total,   "#43e97b"),
        ("🔍", "Keywords analizadas",    str(total_kw),   "#f9ca24"),
        ("🎯", "Oportunidades detectadas", str(total_gaps), "#ff6b6b"),
    ]
    cards = "".join(f"""
      <div class="metric-card reveal">
        <div class="metric-icon" style="color:{c}">{icon}</div>
        <div class="metric-val" style="color:{c}">{val}</div>
        <div class="metric-label">{label}</div>
      </div>""" for icon, label, val, c in items)

    return f"""
<section class="section reveal">
  <h2 class="section-title">Métricas clave</h2>
  <div class="metrics-grid">{cards}
  </div>
</section>"""


def seccion_competencia(datos):
    df = datos["competidores"]
    mem = datos["memoria"]

    if df.empty:
        # No hay productos competidores para este mercado. En vez de ocultar la
        # sección en silencio (confunde al usuario), explicamos por qué falta.
        return """
<section class="section reveal">
  <h2 class="section-title">Análisis de competencia</h2>
  <div class="card">
    <p style="color:#e0e0e0;line-height:1.6">
      No se encontraron productos competidores para este mercado en la base de datos.
    </p>
    <p style="color:#888;font-size:0.85rem;line-height:1.6;margin-top:8px">
      Causa probable: el scraping de Amazon no obtuvo productos (Amazon bloquea las
      peticiones sin un proxy). Configura <code>SCRAPERAPI_KEY</code> en las variables
      de entorno para habilitar el scraping de productos, o sube un CSV de Helium&nbsp;10
      Xray de esta categoría a <code>data/raw</code>.
    </p>
  </div>
</section>"""

    # Deduplicar por ASIN, preferir filas xray
    if "asin" in df.columns and "fuente" in df.columns:
        df_xray = df[df["fuente"] == "xray"]
        df_other = df[df["fuente"] != "xray"]
        df = pd.concat([df_xray, df_other]).drop_duplicates(subset=["asin"], keep="first")

    df = df.sort_values("bsr", ascending=True, na_position="last").head(10)

    # Calcular max revenue para barras relativas
    rev_col = "revenue_mensual_asin"
    max_rev = df[rev_col].dropna().max() if rev_col in df.columns and not df[rev_col].dropna().empty else 1

    filas = ""
    for _, row in df.iterrows():
        rev  = _si(row.get(rev_col))
        pct  = min(100, round(rev / max_rev * 100)) if max_rev > 0 else 0
        r_color = "#43e97b" if pct > 60 else "#6c63ff" if pct > 30 else "#2a2a3a"
        marca = escape_html(str(row.get("marca", "—"))[:25])
        precio = f"MX${float(row.get('precio',0)):,.0f}" if row.get("precio") else "—"
        bsr   = f"{int(row.get('bsr',0)):,}" if pd.notna(row.get("bsr")) else "—"
        rev_count = f"{int(row.get('reviews_count',0)):,}" if pd.notna(row.get("reviews_count")) else "—"
        rating = f"{float(row.get('rating',0)):.1f} ★" if pd.notna(row.get("rating")) else "—"
        ventas = f"{int(row.get('ventas_mensuales_asin',0)):,}" if pd.notna(row.get("ventas_mensuales_asin")) else "—"

        filas += f"""<tr>
          <td class="td-marca">{marca}</td>
          <td>{precio}</td>
          <td>{bsr}</td>
          <td>{rev_count}</td>
          <td>{rating}</td>
          <td>{ventas}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1;background:#1a1a2e;border-radius:4px;height:6px;overflow:hidden">
                <div style="width:{pct}%;height:100%;background:{r_color};border-radius:4px;transition:width 1s ease"></div>
              </div>
              <span style="font-size:0.72rem;color:#888;min-width:40px">{fmt_mx(rev)}</span>
            </div>
          </td>
        </tr>"""

    # Distribución de precios
    precios = df["precio"].dropna() if "precio" in df.columns else pd.Series()
    dist_html = ""
    if not precios.empty:
        stats = [
            ("Mínimo", precios.min()),
            ("P25",    precios.quantile(0.25)),
            ("Mediana", precios.median()),
            ("P75",    precios.quantile(0.75)),
            ("Máximo", precios.max()),
        ]
        max_p = precios.max()
        dist_html = '<div class="price-dist">' + "".join(
            f"""<div class="dist-item">
              <div class="dist-bar-wrap">
                <div class="dist-bar" style="height:{max(8, round(v/max_p*80))}px;background:#6c63ff{('ff' if v==precios.median() else '66')}"></div>
              </div>
              <div class="dist-label">{lbl}</div>
              <div class="dist-val">MX${v:,.0f}</div>
            </div>""" for lbl, v in stats
        ) + "</div>"

    barreras = mem.get("competencia", {}).get("hallazgos", {}).get("barreras_entrada", [])
    barr_html = ""
    if barreras:
        barr_html = '<div class="barriers"><h3 class="sub-title">Barreras de entrada detectadas</h3><ul class="barr-list">' + \
            "".join(f'<li>{escape_html(b)}</li>' for b in barreras[:4]) + "</ul></div>"

    return f"""
<section class="section reveal">
  <h2 class="section-title">Análisis de competencia</h2>
  <div class="card">
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr>
          <th>Marca</th><th>Precio</th><th>BSR</th>
          <th>Reviews</th><th>Rating</th><th>Ventas/mes</th><th>Revenue mensual</th>
        </tr></thead>
        <tbody>{filas}</tbody>
      </table>
    </div>
    {dist_html}
  </div>
  {barr_html}
</section>"""


def seccion_keywords(datos):
    df = datos["keywords"]
    if df.empty:
        return ""

    mem      = datos["memoria"]
    kw_princ = mem.get("keywords", {}).get("hallazgos", {}).get("keyword_principal", "")

    max_vol = df["volumen_busqueda"].max() if "volumen_busqueda" in df.columns else 1

    filas = ""
    for _, row in df.head(20).iterrows():
        kw     = escape_html(str(row.get("keyword", "")))
        vol    = _si(row.get("volumen_busqueda"))
        comp   = _si(row.get("competidores"))
        iq     = _si(row.get("cerebro_iq_score"))
        tend   = row.get("tendencia_30d")
        score  = _si(row.get("score_oportunidad"))
        nivel  = str(row.get("nivel_oportunidad", ""))
        pct    = min(100, round(vol / max_vol * 100)) if max_vol > 0 else 0
        tend_s = f"+{tend:.0f}x" if pd.notna(tend) and tend > 1 else ("—" if pd.isna(tend) else f"{tend:.1f}x")
        tend_c = "#43e97b" if pd.notna(tend) and tend > 5 else "#888"
        es_principal = kw_princ and kw_princ.lower() in kw.lower()
        row_style = ' style="background:#1a1530"' if es_principal else ""

        filas += f"""<tr{row_style}>
          <td>{"⭐ " if es_principal else ""}<strong>{kw}</strong></td>
          <td>
            <div style="display:flex;align-items:center;gap:6px">
              <div style="width:60px;background:#1a1a2e;border-radius:3px;height:5px;overflow:hidden">
                <div style="width:{pct}%;height:100%;background:#6c63ff;border-radius:3px"></div>
              </div>
              <span>{vol:,}</span>
            </div>
          </td>
          <td>{comp:,}</td>
          <td style="color:{tend_c}">{tend_s}</td>
          <td><strong>{score}</strong>/14</td>
          <td>{nivel_badge(nivel)}</td>
        </tr>"""

    kw_dest = ""
    if kw_princ:
        oculta = mem.get("keywords", {}).get("hallazgos", {}).get("oportunidad_oculta", "")
        kw_dest = f"""<div class="kw-highlight reveal">
          <div class="kw-star">⭐</div>
          <div>
            <div class="kw-label">Keyword principal recomendada</div>
            <div class="kw-main">{escape_html(kw_princ)}</div>
            {f'<p class="kw-insight">{escape_html(oculta[:200])}...</p>' if oculta else ""}
          </div>
        </div>"""

    return f"""
<section class="section reveal">
  <h2 class="section-title">Keywords y SEO</h2>
  {kw_dest}
  <div class="card">
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr>
          <th>Keyword</th><th>Volumen</th><th>Competidores</th>
          <th>Tendencia 30d</th><th>Score</th><th>Nivel</th>
        </tr></thead>
        <tbody>{filas}</tbody>
      </table>
    </div>
  </div>
</section>"""


def seccion_gaps(datos):
    df = datos["gaps"]
    if df.empty:
        return ""

    cards = ""
    for i, (_, row) in enumerate(df.iterrows()):
        area     = escape_html(str(row.get("area", f"Gap {i+1}")))
        oport    = escape_html(str(row.get("oportunidad", "")))
        evidencia = escape_html(str(row.get("evidencia", "")))
        impacto  = str(row.get("impacto", ""))
        facilidad = str(row.get("facilidad", ""))
        score    = _si(row.get("score"))
        color_b  = "#43e97b" if score >= 6 else "#f9ca24" if score >= 4 else "#ff6b6b"

        cards += f"""<div class="gap-card reveal" style="border-left:3px solid {color_b}">
          <div class="gap-header">
            <div class="gap-score" style="background:{color_b}22;color:{color_b}">{score}/6</div>
            <h3 class="gap-title">{area}</h3>
            <div class="gap-badges">
              {impacto_badge(impacto)}
              <span style="background:#1a1a2e;color:#888;padding:2px 8px;border-radius:99px;font-size:0.7rem">Facilidad: {facilidad}</span>
            </div>
          </div>
          <p class="gap-oport">{oport}</p>
          {f'<p class="gap-evidencia">📊 {evidencia[:180]}...</p>' if evidencia else ""}
        </div>"""

    return f"""
<section class="section reveal">
  <h2 class="section-title">Oportunidades de mercado (Gap Analysis)</h2>
  <div class="gaps-grid">{cards}
  </div>
</section>"""


def seccion_pain_points(datos):
    df = datos["pain_points"]
    mem = datos["memoria"]

    # Intentar memoria si CSV vacío o escaso
    pain_ia = mem.get("resenas", {}).get("hallazgos", {}).get("pain_points_criticos", [])
    mejoras  = mem.get("resenas", {}).get("hallazgos", {}).get("top_3_mejoras", [])
    insight  = mem.get("resenas", {}).get("hallazgos", {}).get("insight_principal", "")
    sentimiento = mem.get("resenas", {}).get("hallazgos", {}).get("sentimiento_general", "")

    items_html = ""
    if not df.empty and "frecuencia" in df.columns:
        max_f = df["frecuencia"].max() or 1
        for _, row in df.sort_values("frecuencia", ascending=False).iterrows():
            tema   = escape_html(str(row.get("tema", "")).replace("_", " ").title())
            freq   = _si(row.get("frecuencia"))
            pct    = float(row.get("porcentaje", 0) or 0)
            prio   = str(row.get("prioridad", "Baja"))
            bar_pct = min(100, round(freq / max_f * 100))
            c_prio = {"Alta": "#ff6b6b", "Media": "#f9ca24", "Baja": "#43e97b"}.get(prio, "#888")
            items_html += f"""<div class="pp-item">
              <div class="pp-row">
                <span class="pp-tema">{tema}</span>
                <span style="color:{c_prio};font-size:0.75rem;font-weight:600">{prio}</span>
                <span class="pp-freq">{pct:.1f}%</span>
              </div>
              <div class="pp-bar-wrap">
                <div class="pp-bar" style="width:{bar_pct}%;background:{c_prio}88"></div>
              </div>
            </div>"""
    elif pain_ia:
        for tema in pain_ia:
            t = escape_html(str(tema).replace("_", " ").title())
            items_html += f'<div class="pp-item"><span class="pp-tema">⚠ {t}</span></div>'

    sent_badge = ""
    if sentimiento:
        c = {"positivo": "#43e97b", "negativo": "#ff6b6b", "mixto": "#f9ca24"}.get(sentimiento.lower(), "#888")
        sent_badge = f'<span style="background:{c}22;color:{c};padding:4px 12px;border-radius:99px;font-size:0.78rem;font-weight:600;border:1px solid {c}44">Sentimiento general: {sentimiento.upper()}</span>'

    mejoras_html = ""
    if mejoras:
        mejoras_html = '<div class="mejoras"><h3 class="sub-title">Top 3 mejoras para ganar mercado</h3>' + \
            "".join(f'<div class="mejora-item"><span class="mejora-num">{i+1}</span><p>{escape_html(m)}</p></div>' for i, m in enumerate(mejoras[:3])) + \
            "</div>"

    insight_html = f'<p class="pp-insight">{escape_html(insight)}</p>' if insight else ""

    return f"""
<section class="section reveal">
  <h2 class="section-title">Pain points del mercado</h2>
  {sent_badge}
  {insight_html}
  <div class="card" style="margin-top:16px">
    {items_html if items_html else '<p class="empty-msg">Sin datos de reseñas directas — análisis basado en contexto de mercado.</p>'}
  </div>
  {mejoras_html}
</section>"""


def seccion_listing(datos):
    listing = datos["listing"]
    if not listing:
        return ""

    titulo   = listing.get("titulo", "")
    bullets  = listing.get("bullets", [])
    desc     = listing.get("descripcion", "")
    backend  = listing.get("terminos_backend", [])
    imagenes = listing.get("estrategia_imagenes", [])

    # Semáforo de título
    t_len = len(titulo)
    t_color = "#ff6b6b" if t_len > 195 else "#f9ca24" if t_len > 180 else "#43e97b"
    t_label = "Largo" if t_len > 195 else "Límite" if t_len > 180 else "Óptimo"

    bullets_html = "".join(f"""<div class="bullet-item">
      <div class="bullet-num">{b.get('numero', i+1)}</div>
      <div class="bullet-body">
        <p class="bullet-texto">{escape_html(b.get('texto',''))}</p>
        <p class="bullet-pain">↳ {escape_html(b.get('pain_point_que_resuelve','')[:100])}</p>
      </div>
    </div>""" for i, b in enumerate(bullets))

    chips = "".join(f'<span class="chip" onclick="this.classList.toggle(\'chip-selected\')">{escape_html(t)}</span>' for t in backend)

    imgs_html = "".join(f"""<div class="img-card">
      <div class="img-num">#{img.get('posicion','?')}</div>
      <div class="img-tipo">{escape_html(str(img.get('tipo',''))[:40])}</div>
      <p class="img-desc">{escape_html(str(img.get('descripcion',''))[:150])}...</p>
      <p class="img-key">🔑 {escape_html(str(img.get('elemento_clave',''))[:100])}</p>
    </div>""" for img in imagenes)

    desc_escaped = escape_html(desc).replace("\n", "<br>")

    return f"""
<section class="section reveal">
  <h2 class="section-title">Listing generado</h2>

  <!-- Título -->
  <div class="card">
    <div class="titulo-header">
      <h3 class="sub-title">Título</h3>
      <div class="titulo-badge" style="background:{t_color}22;color:{t_color};border:1px solid {t_color}44">
        {t_len} / 200 — {t_label}
      </div>
    </div>
    <p class="listing-titulo">{escape_html(titulo)}</p>
    <div class="titulo-bar-wrap">
      <div style="width:{min(100, round(t_len/200*100))}%;height:4px;background:{t_color};border-radius:2px;transition:width 1s ease"></div>
    </div>
  </div>

  <!-- Bullets -->
  <div class="card">
    <h3 class="sub-title">Bullet points</h3>
    <div class="bullets-list">{bullets_html}</div>
  </div>

  <!-- Descripción expandible -->
  <div class="card">
    <div class="desc-header" onclick="toggleDesc()">
      <h3 class="sub-title" style="margin:0">Descripción del producto</h3>
      <span id="desc-toggle" style="color:#6c63ff;font-size:0.8rem;cursor:pointer">▼ Ver completa</span>
    </div>
    <div id="desc-content" class="desc-collapsed">
      <p class="desc-text">{desc_escaped}</p>
    </div>
    <div class="desc-fade" id="desc-fade"></div>
  </div>

  <!-- Backend terms -->
  <div class="card">
    <h3 class="sub-title">Términos backend <span style="color:#666;font-size:0.75rem;font-weight:400">(haz clic para marcar)</span></h3>
    <div class="chips-wrap">{chips}</div>
  </div>

  <!-- Imágenes -->
  <div class="card">
    <h3 class="sub-title">Estrategia de imágenes</h3>
    <div class="imgs-grid">{imgs_html}</div>
  </div>
</section>"""


def seccion_precio(datos):
    listing = datos["listing"]
    mem     = datos["memoria"]
    if not listing:
        return ""

    precio_rec = listing.get("recomendacion_precio", {})
    precio_l   = precio_rec.get("precio_lanzamiento_mx", 0)
    precio_o   = precio_rec.get("precio_objetivo_mx", 0)
    justif     = precio_rec.get("justificacion", "")

    pv_mem  = mem.get("precio_valor", {}).get("hallazgos", {})
    margen  = pv_mem.get("margen_estimado_pct", "—")
    psic    = pv_mem.get("precio_psicologico", "")
    insight = pv_mem.get("insight_precio", "")
    seg_rec = pv_mem.get("segmento_recomendado", "")

    return f"""
<section class="section reveal">
  <h2 class="section-title">Estrategia de precio</h2>
  <div class="precio-flow">
    <div class="precio-box">
      <div class="precio-etiq">Lanzamiento</div>
      <div class="precio-num">{fmt_mx(precio_l)}</div>
      <div class="precio-sub">Primeras 4-6 semanas</div>
    </div>
    <div class="precio-arrow-big">→</div>
    <div class="precio-box accent-box">
      <div class="precio-etiq">Objetivo</div>
      <div class="precio-num accent-num">{fmt_mx(precio_o)}</div>
      <div class="precio-sub">Segmento {escape_html(seg_rec)}</div>
    </div>
    <div class="precio-margen">
      <div class="margen-num">{margen}%</div>
      <div class="precio-etiq">Margen estimado</div>
    </div>
  </div>
  <div class="card">
    <p class="justif-text">{escape_html(justif)}</p>
    {f'<p class="psic-text">💡 <strong>Precio psicológico:</strong> {escape_html(str(psic)[:200])}</p>' if psic else ""}
    {f'<p class="insight-text">📊 {escape_html(insight)}</p>' if insight else ""}
  </div>
</section>"""


def seccion_riesgos_pasos(datos):
    listing = datos["listing"]
    if not listing:
        return ""

    riesgos = listing.get("riesgos", [])
    pasos   = listing.get("proximos_pasos", [])

    riesgos_html = "".join(f"""<div class="riesgo-item">
      <span class="riesgo-icon">⚠</span>
      <p>{escape_html(r)}</p>
    </div>""" for r in riesgos)

    pasos_html = "".join(f"""<label class="paso-item" onclick="this.classList.toggle('paso-done')">
      <span class="paso-check" id="check-{i}">☐</span>
      <span class="paso-num">{i+1}.</span>
      <p>{escape_html(p)}</p>
    </label>""" for i, p in enumerate(pasos))

    return f"""
<section class="section reveal">
  <div class="two-col">
    <div>
      <h2 class="section-title">Riesgos identificados</h2>
      <div class="riesgos-list">{riesgos_html}</div>
    </div>
    <div>
      <h2 class="section-title">Próximos pasos <span style="font-size:0.75rem;color:#666;font-weight:400">(haz clic para marcar)</span></h2>
      <div class="pasos-list">{pasos_html}</div>
    </div>
  </div>
</section>"""


# ─────────────────────────────────────────────
# BLOQUE 5 — HTML base (CSS + JS + layout)
# ─────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: #0a0a0f;
  color: #d4d4e8;
  font-family: 'DM Sans', sans-serif;
  font-size: 15px;
  line-height: 1.6;
  min-height: 100vh;
}
h1,h2,h3 { font-family: 'Syne', sans-serif; }

/* Layout */
.container { max-width: 1100px; margin: 0 auto; padding: 0 20px 80px; }
.section { margin-bottom: 56px; }
.card {
  background: #111118;
  border: 1px solid #2a2a3a;
  border-radius: 14px;
  padding: 20px;
  margin-top: 12px;
}
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 32px; }
@media(max-width:720px) { .two-col { grid-template-columns: 1fr; } }

/* Topbar */
.topbar {
  background: #111118;
  border-bottom: 1px solid #2a2a3a;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}
.topbar-logo { font-family:'Syne',sans-serif; font-weight:700; font-size:1rem; color:#6c63ff; }
.topbar-sep { color:#2a2a3a; }
.topbar-title { color:#888; font-size:0.85rem; }
.topbar-score { margin-left:auto; font-size:0.8rem; font-weight:600; padding:4px 12px; border-radius:99px; }

/* Reveal animation */
.reveal { opacity:0; transform:translateY(24px); transition:opacity .6s ease, transform .6s ease; }
.reveal.visible { opacity:1; transform:translateY(0); }

/* Hero */
.hero { padding: 48px 0 32px; }
.hero-top { display:flex; align-items:flex-start; justify-content:space-between; flex-wrap:wrap; gap:32px; }
.hero-info { flex:1; min-width:260px; }
.badge-row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.badge-market {
  background:#1a1530; color:#6c63ff; padding:3px 12px;
  border-radius:99px; font-size:0.72rem; font-weight:600;
  border:1px solid #6c63ff44;
}
.hero-title { font-size:clamp(1.8rem,4vw,3rem); font-weight:700; color:#f0f0ff; letter-spacing:-1px; line-height:1.1; margin-bottom:6px; }
.hero-date { color:#555; font-size:0.82rem; margin-bottom:20px; }
.price-row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.price-card { background:#1a1a2e; border:1px solid #2a2a3a; border-radius:10px; padding:10px 16px; }
.price-card.accent { background:#1a1530; border-color:#6c63ff44; }
.price-label { font-size:0.7rem; color:#666; text-transform:uppercase; letter-spacing:.05em; }
.price-val { font-size:1.3rem; font-weight:700; color:#e0e0f0; font-family:'Syne',sans-serif; }
.price-arrow { color:#444; font-size:1.2rem; }
.gauge-wrap { display:flex; flex-direction:column; align-items:center; gap:6px; }
.score-legend { display:flex; gap:10px; font-size:0.68rem; color:#666; }

/* Metrics */
.metrics-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px,1fr)); gap:14px; margin-top:12px; }
.metric-card { background:#111118; border:1px solid #2a2a3a; border-radius:14px; padding:20px; transition:border-color .2s; }
.metric-card:hover { border-color:#6c63ff55; }
.metric-icon { font-size:1.5rem; margin-bottom:8px; }
.metric-val { font-size:1.8rem; font-weight:700; font-family:'Syne',sans-serif; }
.metric-label { color:#666; font-size:0.8rem; margin-top:2px; }

/* Titles */
.section-title { font-size:1.25rem; font-weight:700; color:#e0e0f0; margin-bottom:4px; padding-bottom:8px; border-bottom:1px solid #1a1a2a; }
.sub-title { font-size:0.9rem; font-weight:600; color:#a0a0c0; text-transform:uppercase; letter-spacing:.05em; margin-bottom:12px; }

/* Tables */
.table-scroll { overflow-x:auto; }
.data-table { width:100%; border-collapse:collapse; font-size:0.82rem; }
.data-table th { color:#555; font-size:0.7rem; text-transform:uppercase; letter-spacing:.05em; padding:8px 10px; text-align:left; border-bottom:1px solid #1a1a2a; white-space:nowrap; }
.data-table td { padding:9px 10px; border-bottom:1px solid #1a1a2a; vertical-align:middle; }
.data-table tr:hover td { background:#151520; }
.td-marca { font-weight:600; color:#c0c0e0; }

/* Price distribution */
.price-dist { display:flex; justify-content:space-around; align-items:flex-end; height:100px; margin-top:20px; padding-top:16px; border-top:1px solid #1a1a2a; }
.dist-item { display:flex; flex-direction:column; align-items:center; gap:4px; }
.dist-bar-wrap { display:flex; align-items:flex-end; height:80px; }
.dist-bar { width:28px; border-radius:4px 4px 0 0; }
.dist-label { font-size:0.68rem; color:#666; }
.dist-val { font-size:0.7rem; color:#888; }

/* Barriers */
.barriers { margin-top:16px; }
.barr-list { list-style:none; display:flex; flex-direction:column; gap:8px; }
.barr-list li { background:#111118; border:1px solid #2a2a3a; border-left:3px solid #ff6b6b; border-radius:8px; padding:10px 14px; font-size:0.82rem; color:#aaa; }

/* Keywords */
.kw-highlight { display:flex; align-items:flex-start; gap:16px; background:#1a1530; border:1px solid #6c63ff44; border-radius:14px; padding:16px 20px; margin-bottom:12px; }
.kw-star { font-size:1.5rem; }
.kw-label { font-size:0.7rem; color:#888; text-transform:uppercase; letter-spacing:.05em; }
.kw-main { font-size:1.4rem; font-weight:700; color:#c0b0ff; font-family:'Syne',sans-serif; }
.kw-insight { font-size:0.8rem; color:#666; margin-top:6px; line-height:1.5; }

/* Gaps */
.gaps-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px,1fr)); gap:14px; margin-top:12px; }
.gap-card { background:#111118; border:1px solid #2a2a3a; border-radius:14px; padding:18px; }
.gap-header { display:flex; align-items:flex-start; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
.gap-score { font-size:0.9rem; font-weight:700; padding:4px 10px; border-radius:8px; flex-shrink:0; }
.gap-title { font-size:0.95rem; font-weight:600; color:#e0e0f0; flex:1; min-width:120px; }
.gap-badges { display:flex; gap:6px; flex-wrap:wrap; }
.gap-oport { font-size:0.82rem; color:#aaa; line-height:1.5; margin-bottom:8px; }
.gap-evidencia { font-size:0.75rem; color:#555; border-top:1px solid #1a1a2a; padding-top:8px; margin-top:4px; }

/* Pain points */
.pp-item { margin-bottom:12px; }
.pp-row { display:flex; align-items:center; gap:8px; margin-bottom:4px; }
.pp-tema { font-weight:600; color:#c0c0e0; flex:1; }
.pp-freq { color:#666; font-size:0.78rem; }
.pp-bar-wrap { background:#1a1a2e; border-radius:4px; height:6px; overflow:hidden; }
.pp-bar { height:100%; border-radius:4px; transition:width 1.2s ease; }
.pp-insight { font-size:0.82rem; color:#888; background:#111118; border:1px solid #2a2a3a; border-left:3px solid #6c63ff; border-radius:8px; padding:12px 14px; margin:12px 0; line-height:1.6; }
.mejoras { margin-top:20px; }
.mejora-item { display:flex; gap:12px; align-items:flex-start; margin-bottom:10px; background:#111118; border:1px solid #2a2a3a; border-radius:10px; padding:12px; }
.mejora-num { background:#6c63ff22; color:#6c63ff; font-weight:700; padding:2px 8px; border-radius:6px; font-size:0.9rem; flex-shrink:0; }
.empty-msg { color:#555; font-size:0.85rem; }

/* Listing */
.titulo-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.titulo-badge { padding:4px 12px; border-radius:99px; font-size:0.75rem; font-weight:600; }
.listing-titulo { font-size:0.95rem; color:#e0e0f0; font-weight:600; line-height:1.5; margin-bottom:10px; }
.titulo-bar-wrap { background:#1a1a2e; border-radius:2px; height:4px; overflow:hidden; }

.bullets-list { display:flex; flex-direction:column; gap:14px; }
.bullet-item { display:flex; gap:14px; }
.bullet-num { background:#6c63ff22; color:#6c63ff; font-weight:700; font-size:1rem; width:28px; height:28px; display:flex; align-items:center; justify-content:center; border-radius:8px; flex-shrink:0; font-family:'Syne',sans-serif; }
.bullet-body { flex:1; }
.bullet-texto { font-size:0.88rem; color:#d0d0e8; line-height:1.55; }
.bullet-pain { font-size:0.75rem; color:#555; margin-top:4px; font-style:italic; }

.desc-header { display:flex; justify-content:space-between; align-items:center; cursor:pointer; margin-bottom:10px; }
.desc-collapsed { max-height:80px; overflow:hidden; position:relative; transition:max-height .5s ease; }
.desc-expanded { max-height:2000px; }
.desc-fade { height:40px; background:linear-gradient(to bottom, transparent, #111118); position:relative; margin-top:-40px; pointer-events:none; transition:opacity .3s; }
.desc-fade.hidden { opacity:0; }
.desc-text { font-size:0.85rem; color:#888; line-height:1.7; }

.chips-wrap { display:flex; flex-wrap:wrap; gap:8px; margin-top:4px; }
.chip { background:#1a1a2e; color:#888; padding:6px 14px; border-radius:99px; font-size:0.78rem; cursor:pointer; border:1px solid #2a2a3a; transition:all .2s; user-select:none; }
.chip:hover { border-color:#6c63ff; color:#c0b0ff; }
.chip.chip-selected { background:#1a1530; color:#c0b0ff; border-color:#6c63ff; }

.imgs-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:12px; margin-top:4px; }
.img-card { background:#0f0f1a; border:1px solid #2a2a3a; border-radius:10px; padding:14px; }
.img-num { font-family:'Syne',sans-serif; font-size:1.4rem; font-weight:700; color:#6c63ff; margin-bottom:4px; }
.img-tipo { font-size:0.8rem; font-weight:600; color:#c0c0e0; margin-bottom:6px; }
.img-desc { font-size:0.75rem; color:#777; line-height:1.5; margin-bottom:8px; }
.img-key { font-size:0.72rem; color:#555; border-top:1px solid #1a1a2a; padding-top:6px; line-height:1.4; }

/* Precio */
.precio-flow { display:flex; align-items:center; gap:20px; flex-wrap:wrap; margin-bottom:16px; }
.precio-box { background:#111118; border:1px solid #2a2a3a; border-radius:14px; padding:16px 24px; }
.accent-box { background:#1a1530; border-color:#6c63ff44; }
.precio-etiq { font-size:0.7rem; color:#666; text-transform:uppercase; letter-spacing:.05em; margin-bottom:4px; }
.precio-num { font-size:1.8rem; font-weight:700; color:#e0e0f0; font-family:'Syne',sans-serif; }
.accent-num { color:#c0b0ff; }
.precio-sub { font-size:0.75rem; color:#555; margin-top:2px; }
.precio-arrow-big { font-size:1.5rem; color:#2a2a3a; }
.precio-margen { background:#0a2a1a; border:1px solid #43e97b33; border-radius:14px; padding:16px 24px; }
.margen-num { font-size:1.8rem; font-weight:700; color:#43e97b; font-family:'Syne',sans-serif; }
.justif-text { font-size:0.84rem; color:#888; line-height:1.7; margin-bottom:12px; }
.psic-text { font-size:0.82rem; color:#888; background:#1a1530; border-radius:8px; padding:10px 14px; margin-bottom:8px; line-height:1.6; }
.insight-text { font-size:0.82rem; color:#666; border-top:1px solid #1a1a2a; padding-top:10px; line-height:1.6; }

/* Riesgos y pasos */
.riesgos-list { display:flex; flex-direction:column; gap:10px; margin-top:12px; }
.riesgo-item { display:flex; gap:12px; background:#111118; border:1px solid #2a2a3a; border-left:3px solid #ff6b6b; border-radius:10px; padding:12px 14px; }
.riesgo-icon { color:#ff6b6b; font-size:1rem; flex-shrink:0; }
.riesgo-item p { font-size:0.82rem; color:#aaa; line-height:1.5; }
.pasos-list { display:flex; flex-direction:column; gap:8px; margin-top:12px; }
.paso-item { display:flex; gap:12px; align-items:flex-start; background:#111118; border:1px solid #2a2a3a; border-radius:10px; padding:12px 14px; cursor:pointer; transition:border-color .2s; }
.paso-item:hover { border-color:#43e97b44; }
.paso-item.paso-done { background:#0a2a1a; border-color:#43e97b44; opacity:.75; }
.paso-item.paso-done p { text-decoration:line-through; color:#555; }
.paso-item.paso-done .paso-check::before { content:'☑'; }
.paso-check { color:#43e97b; font-size:1rem; flex-shrink:0; }
.paso-num { color:#666; font-size:0.8rem; flex-shrink:0; }
.paso-item p { font-size:0.82rem; color:#aaa; line-height:1.5; flex:1; }

/* Footer */
.footer { text-align:center; padding:40px 20px; color:#333; font-size:0.78rem; border-top:1px solid #1a1a2a; }
"""

JS = """
// Scroll reveal
const observer = new IntersectionObserver((entries) => {
  entries.forEach(e => { if(e.isIntersecting) e.target.classList.add('visible'); });
}, { threshold: 0.08 });
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

// Descripción expandible
function toggleDesc() {
  const content = document.getElementById('desc-content');
  const fade    = document.getElementById('desc-fade');
  const toggle  = document.getElementById('desc-toggle');
  const exp     = content.classList.contains('desc-expanded');
  content.classList.toggle('desc-expanded', !exp);
  content.classList.toggle('desc-collapsed', exp);
  if(fade) fade.classList.toggle('hidden', !exp);
  toggle.textContent = exp ? '▼ Ver completa' : '▲ Colapsar';
}

// Checklist próximos pasos: actualizar icono
document.querySelectorAll('.paso-item').forEach(item => {
  item.addEventListener('click', () => {
    const check = item.querySelector('.paso-check');
    if(!check) return;
    check.textContent = item.classList.contains('paso-done') ? '☑' : '☐';
  });
});
"""


def generar_html(datos, score, mercado, fecha):
    color_sc = score_color(score)

    html_secciones = (
        seccion_hero(datos, score, mercado, fecha) +
        seccion_metricas(datos) +
        seccion_competencia(datos) +
        seccion_keywords(datos) +
        seccion_gaps(datos) +
        seccion_pain_points(datos) +
        seccion_listing(datos) +
        seccion_precio(datos) +
        seccion_riesgos_pasos(datos)
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — {escape_html(mercado)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>

<div class="topbar">
  <span class="topbar-logo">MarketAgent</span>
  <span class="topbar-sep">|</span>
  <span class="topbar-title">{escape_html(mercado)}</span>
  <span class="topbar-score" style="background:{color_sc}22;color:{color_sc};border:1px solid {color_sc}44">
    Score {score}/100
  </span>
</div>

<div class="container">
  {html_secciones}
</div>

<div class="footer">
  Generado por Sistema Multiagente de Investigación de Mercado · {fecha}
</div>

<script>{JS}</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# BLOQUE 6 — Punto de entrada
# ─────────────────────────────────────────────

def ejecutar(mercado="mercado"):
    print("\n" + "="*50)
    print("AGENTE 9: DASHBOARD VISUAL")
    print("="*50)

    REPORTS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)

    print("\n  Cargando datos del pipeline...")
    datos = cargar_datos()

    # Detectar nombre de mercado desde memoria si no se pasó
    mem_comp = datos["memoria"].get("competencia", {}).get("hallazgos", {})
    if mercado in ("mercado", "") and mem_comp:
        # Usar nombre pasado por el orchestrator
        pass

    score = calcular_score(datos)
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

    print(f"  Mercado: {mercado}")
    print(f"  Score calculado: {score}/100")
    print(f"  Competidores: {len(datos['competidores'])} | Keywords: {len(datos['keywords'])}")
    print(f"  Gaps: {len(datos['gaps'])} | Pain points: {len(datos['pain_points'])}")

    print("\n  Generando dashboard HTML...")
    html = generar_html(datos, score, mercado, fecha)

    path = REPORTS_DIR / "dashboard.html"
    path.write_text(html, encoding="utf-8")
    print(f"\n  Dashboard guardado en: {path}")
    print(f"  Tamaño: {round(len(html)/1024, 1)} KB")

    print("\n  Abriendo en el navegador...")
    webbrowser.open(path.resolve().as_uri())

    print("\n  Agente de dashboard completado.")
    return str(path)


if __name__ == "__main__":
    ejecutar()

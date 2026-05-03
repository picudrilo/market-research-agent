"use client"

import { useEffect, useRef, useState } from "react"
import { useParams, useSearchParams, useRouter } from "next/navigation"
import {
  ArrowLeft, Loader2, TrendingUp, DollarSign, Package,
  AlertTriangle, XCircle, CheckCircle2, ChevronDown, ChevronUp,
  BarChart2, Users, Star, Activity, RefreshCw, PlusCircle,
} from "lucide-react"
import { RegistrarInversionModal } from "../../components/RegistrarInversionModal"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

// ─── types ───────────────────────────────────────────────────────────────────

type Semaforo = "INVERTIR" | "RIESGO MEDIO" | "DESCARTAR"

interface Financiero {
  precio_compra:   number
  precio_amazon:   number
  referral_fee:    number
  fba_fee:         number
  ganancia_neta:   number
  roi:             number
}

interface ClaudeAnalisis {
  riesgos:         string[]
  razon_verdicto:  string
  insight:         string
}

interface Producto {
  asin:             string
  titulo:           string
  marca:            string
  categoria:        string | null
  score_arbitraje:  number
  semaforo:         Semaforo
  en_historial_bd:  boolean
  financiero:       Financiero | null
  claude_analisis:  ClaudeAnalisis
  bsr:              number | null
  reviews_count:    number | null
  rating:           number | null
  ventas_mes:       number | null
  active_sellers:   number | null
  fba:              boolean
}

interface BatchMeta {
  top_3_asins:          string[]
  competencia_interna:  string
  advertencia_general:  string
}

interface BatchResult {
  modo:                  string
  nombre_sesion:         string
  total:                 number
  invertir:              number
  riesgo_medio:          number
  descartar:             number
  capital_invertir:      number
  roi_promedio_invertir: number
  productos:             Producto[]
  batch_meta:            BatchMeta
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimales = 0) {
  if (n == null || isNaN(n)) return "—"
  return n.toLocaleString("es-MX", {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  })
}

function semaforoColor(s: Semaforo) {
  if (s === "INVERTIR")     return "text-emerald-400 bg-emerald-950/40 border-emerald-900/60"
  if (s === "RIESGO MEDIO") return "text-amber-400  bg-amber-950/40  border-amber-900/60"
  return "text-red-400 bg-red-950/40 border-red-900/60"
}

function semaforoIcon(s: Semaforo) {
  if (s === "INVERTIR")     return <CheckCircle2 className="w-3.5 h-3.5" />
  if (s === "RIESGO MEDIO") return <AlertTriangle className="w-3.5 h-3.5" />
  return <XCircle className="w-3.5 h-3.5" />
}

// ─── main ─────────────────────────────────────────────────────────────────────

export default function BatchResultPage() {
  const params       = useParams()
  const searchParams = useSearchParams()
  const router       = useRouter()
  const jobId        = params.jobId as string

  const [result,   setResult]   = useState<BatchResult | null>(null)
  const [status,   setStatus]   = useState<"loading" | "done" | "error">("loading")
  const [progress, setProgress] = useState<{ step: number; msg: string } | null>(null)
  const [errMsg,   setErrMsg]   = useState("")
  const [filter,   setFilter]   = useState<Semaforo | "TODOS">("TODOS")
  const [sortKey,  setSortKey]  = useState<"score_arbitraje" | "roi" | "bsr">("score_arbitraje")
  const [expanded, setExpanded] = useState<string | null>(null)

  const nombreSesion = searchParams.get("sesion") ?? ""
  const totalParam   = searchParams.get("total") ?? ""
  const eventSource  = useRef<EventSource | null>(null)

  // ── SSE stream ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!jobId) return
    const url = `${API_URL}/stream/${jobId}`
    const es  = new EventSource(url)
    eventSource.current = es

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        if (data.type === "ping") return

        if (data.type === "progress") {
          setProgress({ step: data.step ?? 0, msg: data.message ?? "" })
          return
        }
        if (data.type === "done" && data.result) {
          setResult(data.result as BatchResult)
          setStatus("done")
          es.close()
          return
        }
        if (data.type === "error") {
          setErrMsg(data.message ?? "Error desconocido")
          setStatus("error")
          es.close()
          return
        }
      } catch { /* ignore parse errors */ }
    }

    es.onerror = () => {
      // SSE closed — poll for result once
      es.close()
      setTimeout(async () => {
        try {
          const r = await fetch(`${API_URL}/resultado-batch/${jobId}`)
          if (r.ok) {
            const data = await r.json()
            if (data.status === "done" && data.result) {
              setResult(data.result as BatchResult)
              setStatus("done")
              return
            }
            if (data.status === "error") {
              setErrMsg(data.error ?? "Error en el análisis")
              setStatus("error")
              return
            }
          }
        } catch { /* ignore */ }
      }, 1500)
    }

    return () => { es.close() }
  }, [jobId])

  // ─── vista de carga ────────────────────────────────────────────────────────
  if (status === "loading") {
    return (
      <main className="flex flex-col flex-1 px-5 pt-16 pb-8">
        <BackBtn router={router} />
        <div className="flex flex-col items-center justify-center flex-1 gap-4">
          <div className="w-14 h-14 bg-zinc-800 rounded-2xl flex items-center justify-center">
            <Loader2 className="w-7 h-7 text-zinc-300 animate-spin" />
          </div>
          <div className="text-center">
            <p className="text-zinc-200 font-semibold text-lg">Analizando productos</p>
            {nombreSesion && (
              <p className="text-zinc-500 text-sm mt-1 font-mono">{nombreSesion}</p>
            )}
            {progress && (
              <p className="text-zinc-400 text-sm mt-3">
                <span className="text-zinc-600">Paso {progress.step}:</span>{" "}
                {progress.msg}
              </p>
            )}
            {totalParam && (
              <p className="text-zinc-600 text-xs mt-2">{totalParam} productos en cola</p>
            )}
          </div>
          <div className="mt-4 w-48 h-1 bg-zinc-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-zinc-400 transition-all duration-700"
              style={{ width: `${progress ? Math.min(progress.step * 25, 100) : 5}%` }}
            />
          </div>
        </div>
      </main>
    )
  }

  if (status === "error") {
    return (
      <main className="flex flex-col flex-1 px-5 pt-16 pb-8">
        <BackBtn router={router} />
        <div className="flex flex-col items-center justify-center flex-1 gap-4">
          <XCircle className="w-10 h-10 text-red-400" />
          <p className="text-zinc-200 font-semibold">Error en el análisis</p>
          <p className="text-zinc-500 text-sm text-center max-w-xs">{errMsg}</p>
          <button onClick={() => router.push("/")}
            className="mt-2 px-5 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm rounded-xl transition-colors">
            Volver al inicio
          </button>
        </div>
      </main>
    )
  }

  if (!result) return null

  // ─── datos derivados ───────────────────────────────────────────────────────
  const top3Asins = result.batch_meta?.top_3_asins ?? []
  const descartar = result.productos.filter(p => p.semaforo === "DESCARTAR")

  const filtrados = result.productos
    .filter(p => filter === "TODOS" || p.semaforo === filter)
    .slice()
    .sort((a, b) => {
      if (sortKey === "roi")  return (b.financiero?.roi ?? -999) - (a.financiero?.roi ?? -999)
      if (sortKey === "bsr")  return (a.bsr ?? 999999) - (b.bsr ?? 999999)
      return b.score_arbitraje - a.score_arbitraje
    })

  const top3Productos = top3Asins
    .map(asin => result.productos.find(p => p.asin === asin))
    .filter(Boolean) as Producto[]

  // ─── vista principal ───────────────────────────────────────────────────────
  return (
    <main className="flex flex-col flex-1 px-5 pt-16 pb-8 gap-6">

      {/* Header */}
      <div>
        <BackBtn router={router} />
        <div className="flex items-center gap-3 mt-3">
          <div className="w-10 h-10 bg-zinc-800 rounded-xl flex items-center justify-center">
            <BarChart2 className="w-5 h-5 text-zinc-300" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-zinc-50 leading-tight">Análisis batch</h1>
            {result.nombre_sesion && (
              <p className="text-xs text-zinc-600 font-mono mt-0.5">{result.nombre_sesion}</p>
            )}
          </div>
        </div>
      </div>

      {/* Resumen ejecutivo */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard
          icon={<Package className="w-4 h-4" />}
          label="Total analizados"
          value={fmt(result.total)}
          sub=""
        />
        <StatCard
          icon={<TrendingUp className="w-4 h-4" />}
          label="ROI prom. INVERTIR"
          value={`${fmt(result.roi_promedio_invertir, 1)}%`}
          sub="en productos viables"
          highlight
        />
        <StatCard
          icon={<DollarSign className="w-4 h-4" />}
          label="Capital necesario"
          value={`MX$${fmt(result.capital_invertir)}`}
          sub="para todos los INVERTIR"
        />
        <StatCard
          icon={<Activity className="w-4 h-4" />}
          label="Distribución"
          value={`${result.invertir} / ${result.riesgo_medio} / ${result.descartar}`}
          sub="Invertir / Riesgo / Descartar"
        />
      </div>

      {/* Advertencia general de Claude */}
      {result.batch_meta?.advertencia_general && (
        <div className="bg-amber-950/30 border border-amber-900/50 rounded-xl px-4 py-3 text-sm text-amber-300">
          <p className="font-medium text-amber-200 mb-0.5">Advertencia general</p>
          <p className="text-amber-400 text-xs leading-relaxed">
            {result.batch_meta.advertencia_general}
          </p>
        </div>
      )}

      {/* Competencia interna */}
      {result.batch_meta?.competencia_interna && (
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-sm text-zinc-400">
          <p className="text-zinc-500 text-xs font-medium uppercase tracking-wider mb-1">
            Análisis de competencia interna
          </p>
          <p className="text-xs leading-relaxed">{result.batch_meta.competencia_interna}</p>
        </div>
      )}

      {/* Top 3 cards */}
      {top3Productos.length > 0 && (
        <section>
          <SectionTitle>Top {top3Productos.length} recomendados</SectionTitle>
          <div className="flex flex-col gap-3 mt-3">
            {top3Productos.map((p, i) => (
              <Top3Card key={p.asin} producto={p} rank={i + 1} />
            ))}
          </div>
        </section>
      )}

      {/* Tabla comparativa */}
      <section>
        <SectionTitle>Comparativo completo</SectionTitle>

        {/* Filtros + orden */}
        <div className="flex flex-col gap-2 mt-3">
          {/* Filtro semáforo */}
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {(["TODOS", "INVERTIR", "RIESGO MEDIO", "DESCARTAR"] as const).map(f => (
              <button key={f} onClick={() => setFilter(f)}
                className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                  filter === f
                    ? f === "TODOS"         ? "bg-zinc-100 text-zinc-900 border-zinc-200"
                    : f === "INVERTIR"      ? "bg-emerald-900 text-emerald-200 border-emerald-700"
                    : f === "RIESGO MEDIO"  ? "bg-amber-900  text-amber-200  border-amber-700"
                    :                         "bg-red-900    text-red-200    border-red-700"
                    : "bg-zinc-900 text-zinc-500 border-zinc-800 hover:text-zinc-300"
                }`}>
                {f === "TODOS" ? `Todos (${result.total})`
                : f === "INVERTIR"     ? `Invertir (${result.invertir})`
                : f === "RIESGO MEDIO" ? `Riesgo (${result.riesgo_medio})`
                :                        `Descartar (${result.descartar})`}
              </button>
            ))}
          </div>

          {/* Ordenar */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-600">Ordenar:</span>
            {([
              ["score_arbitraje", "Score"],
              ["roi",             "ROI"],
              ["bsr",             "BSR"],
            ] as const).map(([k, label]) => (
              <button key={k} onClick={() => setSortKey(k)}
                className={`px-2.5 py-1 rounded-lg text-xs transition-colors border ${
                  sortKey === k
                    ? "bg-zinc-700 text-zinc-100 border-zinc-600"
                    : "bg-zinc-900 text-zinc-500 border-zinc-800 hover:text-zinc-300"
                }`}>
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Lista de productos */}
        <div className="flex flex-col gap-2 mt-3">
          {filtrados.map(p => (
            <ProductCard
              key={p.asin}
              producto={p}
              expanded={expanded === p.asin}
              onToggle={() => setExpanded(expanded === p.asin ? null : p.asin)}
            />
          ))}
          {filtrados.length === 0 && (
            <p className="text-zinc-600 text-sm text-center py-6">
              No hay productos con este filtro
            </p>
          )}
        </div>
      </section>

      {/* Descartados — resumen colapsado */}
      {descartar.length > 0 && filter === "TODOS" && (
        <section>
          <SectionTitle>Descartados ({descartar.length})</SectionTitle>
          <div className="flex flex-col gap-1.5 mt-2">
            {descartar.map(p => (
              <div key={p.asin}
                className="flex items-center gap-3 bg-zinc-900/50 border border-zinc-800/50 rounded-xl px-4 py-2.5">
                <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-zinc-400 truncate">{p.titulo || p.asin}</p>
                  <p className="text-xs text-zinc-700 font-mono mt-0.5">{p.asin}</p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-xs text-red-400">
                    ROI {fmt(p.financiero?.roi, 1)}%
                  </p>
                  <p className="text-xs text-zinc-700">Score {p.score_arbitraje}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <button onClick={() => router.push("/")}
        className="mt-4 w-full flex items-center justify-center gap-2 py-3.5 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 text-sm rounded-xl transition-colors">
        <RefreshCw className="w-4 h-4" />
        Nuevo análisis
      </button>

    </main>
  )
}

// ─── subcomponentes ────────────────────────────────────────────────────────────

function BackBtn({ router }: { router: ReturnType<typeof useRouter> }) {
  return (
    <button onClick={() => router.push("/")}
      className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm transition-colors mb-1">
      <ArrowLeft className="w-4 h-4" />
      Volver
    </button>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">{children}</h2>
  )
}

function StatCard({
  icon, label, value, sub, highlight = false,
}: {
  icon: React.ReactNode; label: string; value: string; sub: string; highlight?: boolean
}) {
  return (
    <div className={`bg-zinc-900 border rounded-xl px-4 py-3.5 flex flex-col gap-1 ${
      highlight ? "border-emerald-900/60" : "border-zinc-800"
    }`}>
      <div className={`flex items-center gap-1.5 text-xs ${
        highlight ? "text-emerald-400" : "text-zinc-500"
      }`}>
        {icon}
        <span>{label}</span>
      </div>
      <p className="text-xl font-bold text-zinc-100">{value}</p>
      {sub && <p className="text-xs text-zinc-600 leading-tight">{sub}</p>}
    </div>
  )
}

function Top3Card({ producto: p, rank }: { producto: Producto; rank: number }) {
  return (
    <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2.5">
          <span className="w-7 h-7 bg-zinc-800 rounded-lg flex items-center justify-center text-sm font-bold text-zinc-300">
            {rank}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-zinc-100 leading-tight line-clamp-2">
              {p.titulo || p.asin}
            </p>
            <p className="text-xs text-zinc-600 font-mono mt-0.5">{p.asin}</p>
          </div>
        </div>
        <SemaforoBadge semaforo={p.semaforo} />
      </div>

      <div className="grid grid-cols-3 gap-2 mb-3">
        <MetaChip
          icon={<TrendingUp className="w-3 h-3" />}
          label="ROI"
          value={`${fmt(p.financiero?.roi, 1)}%`}
          color={p.financiero?.roi != null && p.financiero.roi >= 30 ? "text-emerald-400" : "text-zinc-300"}
        />
        <MetaChip
          icon={<Activity className="w-3 h-3" />}
          label="Score"
          value={`${p.score_arbitraje}/100`}
          color="text-zinc-300"
        />
        <MetaChip
          icon={<BarChart2 className="w-3 h-3" />}
          label="BSR"
          value={p.bsr ? fmt(p.bsr) : "—"}
          color="text-zinc-300"
        />
      </div>

      {p.claude_analisis?.insight && (
        <p className="text-xs text-zinc-400 leading-relaxed border-t border-zinc-800 pt-3">
          {p.claude_analisis.insight}
        </p>
      )}
    </div>
  )
}

function ProductCard({
  producto: p, expanded, onToggle,
}: {
  producto: Producto; expanded: boolean; onToggle: () => void
}) {
  const [showModal, setShowModal] = useState(false)
  const roi = p.financiero?.roi ?? null
  const roiColor = roi == null ? "text-zinc-600"
    : roi >= 30  ? "text-emerald-400"
    : roi >= 15  ? "text-amber-400"
    : "text-red-400"

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
      <button type="button" onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-800/40 transition-colors text-left">
        <SemaforoIcon semaforo={p.semaforo} />
        <div className="flex-1 min-w-0">
          <p className="text-sm text-zinc-200 leading-tight truncate">
            {p.titulo || p.asin}
          </p>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-xs text-zinc-600 font-mono">{p.asin}</span>
            {p.en_historial_bd && (
              <span className="text-xs text-blue-500">historial</span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <p className={`text-sm font-semibold ${roiColor}`}>
            {roi != null ? `${fmt(roi, 1)}%` : "—"}
          </p>
          <p className="text-xs text-zinc-600">Score {p.score_arbitraje}</p>
        </div>
        {expanded
          ? <ChevronUp   className="w-4 h-4 text-zinc-600 shrink-0" />
          : <ChevronDown className="w-4 h-4 text-zinc-600 shrink-0" />}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-zinc-800 flex flex-col gap-4">

          {/* Financiero */}
          {p.financiero && (
            <div>
              <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">
                Financiero
              </p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <FinRow label="Precio compra"  value={`MX$${fmt(p.financiero.precio_compra,  2)}`} />
                <FinRow label="Precio Amazon"  value={`MX$${fmt(p.financiero.precio_amazon,  2)}`} />
                <FinRow label="Referral fee"   value={`MX$${fmt(p.financiero.referral_fee,   2)}`} />
                <FinRow label="FBA fee"        value={`MX$${fmt(p.financiero.fba_fee,        2)}`} />
                <FinRow label="Ganancia neta"  value={`MX$${fmt(p.financiero.ganancia_neta,  2)}`}
                  highlight={p.financiero.ganancia_neta > 0} />
                <FinRow label="ROI"            value={`${fmt(p.financiero.roi, 1)}%`}
                  highlight={p.financiero.roi >= 30} />
              </div>
            </div>
          )}

          {/* Métricas de mercado */}
          <div>
            <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">
              Métricas
            </p>
            <div className="flex flex-wrap gap-2">
              {p.bsr && (
                <Chip icon={<BarChart2 className="w-3 h-3" />} label={`BSR ${fmt(p.bsr)}`} />
              )}
              {p.reviews_count != null && (
                <Chip icon={<Users className="w-3 h-3" />} label={`${fmt(p.reviews_count)} reseñas`} />
              )}
              {p.rating != null && (
                <Chip icon={<Star className="w-3 h-3" />} label={`${p.rating} estrellas`} />
              )}
              {p.ventas_mes != null && (
                <Chip icon={<Activity className="w-3 h-3" />} label={`${fmt(p.ventas_mes)} ventas/mes`} />
              )}
              {p.active_sellers != null && (
                <Chip icon={<Users className="w-3 h-3" />} label={`${p.active_sellers} sellers`} />
              )}
              {p.fba && (
                <Chip icon={<Package className="w-3 h-3" />} label="FBA" />
              )}
            </div>
          </div>

          {/* Claude análisis */}
          {p.claude_analisis && (
            <div>
              <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">
                Análisis IA
              </p>
              {p.claude_analisis.razon_verdicto && (
                <p className="text-xs text-zinc-400 leading-relaxed mb-2">
                  {p.claude_analisis.razon_verdicto}
                </p>
              )}
              {p.claude_analisis.riesgos?.length > 0 && (
                <div className="flex flex-col gap-1">
                  {p.claude_analisis.riesgos.map((r: string, i: number) => (
                    <div key={i} className="flex items-start gap-2">
                      <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0 mt-0.5" />
                      <p className="text-xs text-zinc-500">{r}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Historial previo */}
          {p.en_historial_bd && (
            <div className="bg-blue-950/30 border border-blue-900/50 rounded-lg px-3 py-2">
              <p className="text-xs text-blue-400">
                Este ASIN ya fue analizado anteriormente y está en tu historial.
              </p>
            </div>
          )}

          {/* Registrar inversión */}
          {p.semaforo === "INVERTIR" && (
            <button
              onClick={() => setShowModal(true)}
              className="w-full flex items-center justify-center gap-2 py-2 bg-emerald-900/40 hover:bg-emerald-900/70 border border-emerald-800/50 text-emerald-300 text-xs font-medium rounded-lg transition-colors">
              <PlusCircle className="w-3.5 h-3.5" />
              Registrar inversión
            </button>
          )}
        </div>
      )}

      {showModal && (
        <RegistrarInversionModal
          asin={p.asin}
          titulo={p.titulo}
          precio_compra_sugerido={p.financiero?.precio_compra ?? 0}
          onClose={() => setShowModal(false)}
          onSuccess={() => setShowModal(false)}
        />
      )}
    </div>
  )
}

function SemaforoBadge({ semaforo }: { semaforo: Semaforo }) {
  return (
    <span className={`shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-semibold border ${semaforoColor(semaforo)}`}>
      {semaforoIcon(semaforo)}
      {semaforo}
    </span>
  )
}

function SemaforoIcon({ semaforo }: { semaforo: Semaforo }) {
  if (semaforo === "INVERTIR")     return <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
  if (semaforo === "RIESGO MEDIO") return <AlertTriangle className="w-4 h-4 text-amber-400  shrink-0" />
  return <XCircle className="w-4 h-4 text-red-400 shrink-0" />
}

function MetaChip({ icon, label, value, color }: {
  icon: React.ReactNode; label: string; value: string; color?: string
}) {
  return (
    <div className="bg-zinc-800/60 rounded-lg px-2 py-1.5 flex flex-col items-center gap-0.5">
      <div className="flex items-center gap-1 text-zinc-500">{icon}<span className="text-zinc-600 text-xs">{label}</span></div>
      <p className={`text-sm font-semibold ${color ?? "text-zinc-300"}`}>{value}</p>
    </div>
  )
}

function FinRow({ label, value, highlight = false }: {
  label: string; value: string; highlight?: boolean
}) {
  return (
    <>
      <span className="text-zinc-600">{label}</span>
      <span className={`text-right ${highlight ? "text-emerald-400 font-semibold" : "text-zinc-400"}`}>
        {value}
      </span>
    </>
  )
}

function Chip({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-1.5 bg-zinc-800 border border-zinc-700 rounded-lg px-2.5 py-1 text-xs text-zinc-400">
      {icon}{label}
    </div>
  )
}

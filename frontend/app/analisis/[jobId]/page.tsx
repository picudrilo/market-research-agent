"use client"

import { useEffect, useRef, useState } from "react"
import { useParams, useSearchParams, useRouter } from "next/navigation"
import {
  CheckCircle2, XCircle, AlertTriangle, Loader2,
  TrendingUp, TrendingDown, DollarSign, Package,
  ArrowLeft, Tag, Zap, Shield, ExternalLink, BarChart2,
  Sparkles, RefreshCw
} from "lucide-react"

const API_URL = process.env.NEXT_PUBLIC_API_URL

interface ProgressEvent {
  type: "progress" | "done" | "error" | "ping"
  step?: number
  total?: number
  agent?: string
  message?: string
  status?: "running" | "done" | "error"
  result?: AnalysisResult
}

interface AnalysisResult {
  modo: string
  mercado: string
  producto: string
  precio_compra_mx: number
  unidades: number
  url_amazon: string
  precio_amazon_mx: number
  ventas_mes: number
  asin: string
  // Arbitraje fields
  veredicto: "COMPRA" | "NO COMPRA" | "RIESGO MEDIO"
  score_oportunidad: number
  roi_estimado_pct: number
  precio_venta_recomendado_mx: number
  ganancia_por_unidad_mx: number
  ganancia_total_estimada_mx: number
  referral_fee_mx: number
  fba_fee_estimado_mx: number
  tiempo_recuperacion: string
  razon_principal: string
  resumen_ejecutivo: string
  riesgos: string[]
  acciones_inmediatas: string[]
  // Marca propia fields
  listing: {
    titulo: string
    precio_lanzamiento: number
    precio_objetivo: number
    terminos_backend: string[]
    top_bullets: string[]
  }
  concepto: {
    nombre: string
    tagline: string
    mensaje_central: string
  }
  keyword_principal: string
}

interface Step {
  step: number
  agent: string
  message: string
  status: "pending" | "running" | "done" | "error"
}

const STEP_LABELS: Record<number, string> = {
  0: "Detectando nicho",
  1: "Ingesta de datos",
  2: "Competencia",
  3: "Reseñas",
  4: "GAP Analysis",
  5: "Precio vs Valor",
  6: "Keywords SEO",
  7: "Concepto",
  8: "Listing",
  9: "Veredicto",
}

function verdictColor(v: string) {
  if (v === "COMPRA") return "text-emerald-400"
  if (v === "NO COMPRA") return "text-red-400"
  return "text-amber-400"
}
function verdictBg(v: string) {
  if (v === "COMPRA") return "bg-emerald-950/60 border-emerald-800/50"
  if (v === "NO COMPRA") return "bg-red-950/60 border-red-800/50"
  return "bg-amber-950/60 border-amber-800/50"
}
function verdictIcon(v: string) {
  if (v === "COMPRA") return <CheckCircle2 className="w-8 h-8 text-emerald-400" />
  if (v === "NO COMPRA") return <XCircle className="w-8 h-8 text-red-400" />
  return <AlertTriangle className="w-8 h-8 text-amber-400" />
}
function fmt(n: number) {
  return n.toLocaleString("es-MX", { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

export default function AnalisisPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const searchParams = useSearchParams()
  const router = useRouter()

  const producto     = searchParams.get("producto") || ""
  const precio       = parseFloat(searchParams.get("precio") || "0")
  const unidades     = parseInt(searchParams.get("unidades") || "1")
  const urlAmazon    = searchParams.get("url") || ""
  const precioAmazon = parseFloat(searchParams.get("precioAmazon") || "0")
  const ventasMes    = parseInt(searchParams.get("ventasMes") || "0")
  const modoParam    = searchParams.get("modo") || "arbitraje"

  const [steps,  setSteps]  = useState<Step[]>([])
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error,  setError]  = useState("")
  const [done,   setDone]   = useState(false)

  const esArbitraje = (result?.modo || modoParam) === "arbitraje"

  const esRef   = useRef<EventSource | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const applyEvent = (msg: ProgressEvent) => {
    if (msg.type === "ping") return

    if (msg.type === "progress") {
      setSteps((prev: Step[]) => {
        const idx = prev.findIndex((s: Step) => s.step === msg.step)
        const newStep: Step = {
          step:    msg.step!,
          agent:   STEP_LABELS[msg.step!] || msg.agent || "",
          message: msg.message || "",
          status:  (msg.status as Step["status"]) || "running",
        }
        if (idx === -1) return [...prev, newStep]
        const next = [...prev]
        next[idx] = newStep
        return next
      })
    }

    if (msg.type === "done") {
      setResult(msg.result!)
      setDone(true)
      esRef.current?.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }

    if (msg.type === "error") {
      setError(msg.message || "Error desconocido")
      setDone(true)
      esRef.current?.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }

  const startPolling = () => {
    if (pollRef.current) return
    pollRef.current = setInterval(async () => {
      try {
        const res  = await fetch(`${API_URL}/resultado/${jobId}`)
        const data = await res.json()
        if (data.status === "done" && data.result) {
          clearInterval(pollRef.current!)
          pollRef.current = null
          setResult(data.result)
          setDone(true)
        } else if (data.status === "error") {
          clearInterval(pollRef.current!)
          pollRef.current = null
          setError("Error en el análisis — revisa el producto e intenta de nuevo")
          setDone(true)
        }
      } catch { /* ignore transient errors */ }
    }, 5000)
  }

  useEffect(() => {
    const es = new EventSource(`${API_URL}/stream/${jobId}`)
    esRef.current = es

    es.onmessage = (e) => {
      if (!e.data || e.data.startsWith(":")) return
      applyEvent(JSON.parse(e.data))
    }

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) startPolling()
    }

    return () => {
      es.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [jobId])

  const doneSteps = steps.filter((s: Step) => s.status === "done").length
  const totalSteps = esArbitraje ? 8 : 10
  const progress = done ? 100 : Math.round((doneSteps / totalSteps) * 100)

  const displayAsin    = result?.asin || ""
  const displayUrl     = result?.url_amazon || urlAmazon
  const displayVentas  = result?.ventas_mes || ventasMes
  const displayPrecioA = result?.precio_amazon_mx || precioAmazon

  return (
    <main className="flex flex-col flex-1 px-5 pt-8 pb-10">
      {/* Back */}
      <button
        onClick={() => router.push("/")}
        className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm mb-6 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" />
        Nueva búsqueda
      </button>

      {/* Product header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-1">
          {esArbitraje
            ? <RefreshCw className="w-4 h-4 text-zinc-500" />
            : <Sparkles  className="w-4 h-4 text-zinc-500" />}
          <span className="text-xs text-zinc-500 uppercase tracking-wider">
            {esArbitraje ? "Análisis de arbitraje" : "Investigación de mercado"}
          </span>
        </div>
        <h1 className="text-lg font-semibold text-zinc-100 leading-snug">{producto}</h1>
        {esArbitraje && precio > 0 && (
          <p className="text-sm text-zinc-500 mt-0.5">
            MX${fmt(precio)} × {unidades} pz — Inversión MX${fmt(precio * unidades)}
          </p>
        )}

        {/* ASIN + link */}
        {(displayAsin || displayUrl) && (
          <div className="flex items-center gap-3 mt-2 flex-wrap">
            {displayAsin && (
              <span className="inline-flex items-center gap-1.5 bg-zinc-800 rounded-lg px-2.5 py-1 text-xs text-zinc-400">
                <Tag className="w-3 h-3" />
                ASIN: <span className="font-mono font-semibold text-zinc-200">{displayAsin}</span>
              </span>
            )}
            {displayUrl && (
              <a href={displayUrl} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 transition-colors">
                Ver en Amazon <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
        )}
      </div>

      {/* Progress bar */}
      {!done && (
        <div className="mb-6">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs text-zinc-500">Analizando...</span>
            <span className="text-xs text-zinc-500">{progress}%</span>
          </div>
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div className="h-full bg-zinc-300 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-950/50 border border-red-900/50 rounded-xl p-4 text-red-400 text-sm mb-6">
          {error}
        </div>
      )}

      {/* ── RESULTS ── */}
      {result && (
        <div className="flex flex-col gap-5">

          {/* ══ ARBITRAJE LAYOUT ══ */}
          {esArbitraje && (
            <>
              {/* Verdict */}
              <div className={`border rounded-2xl p-5 ${verdictBg(result.veredicto)}`}>
                <div className="flex items-center gap-3 mb-3">
                  {verdictIcon(result.veredicto)}
                  <div>
                    <div className={`text-2xl font-bold ${verdictColor(result.veredicto)}`}>
                      {result.veredicto}
                    </div>
                    <div className="text-xs text-zinc-500">
                      Score: {result.score_oportunidad}/100 · {result.mercado}
                    </div>
                  </div>
                </div>
                <p className="text-sm text-zinc-300 leading-relaxed">{result.razon_principal}</p>
              </div>

              {/* Real Amazon data */}
              {(displayPrecioA > 0 || displayVentas > 0) && (
                <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <BarChart2 className="w-3.5 h-3.5 text-zinc-400" />
                    <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Datos reales en Amazon
                    </h3>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {displayPrecioA > 0 && (
                      <div>
                        <div className="text-lg font-bold text-zinc-100">MX${fmt(displayPrecioA)}</div>
                        <div className="text-xs text-zinc-500">Precio actual</div>
                      </div>
                    )}
                    {displayVentas > 0 && (
                      <div>
                        <div className="text-lg font-bold text-zinc-100">{fmt(displayVentas)}</div>
                        <div className="text-xs text-zinc-500">Unidades/mes</div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Key numbers */}
              <div className="grid grid-cols-2 gap-3">
                <Stat icon={<TrendingUp className="w-4 h-4 text-emerald-400" />}
                  label="ROI estimado" value={`${result.roi_estimado_pct}%`}
                  highlight={result.roi_estimado_pct > 20} />
                <Stat icon={<DollarSign className="w-4 h-4 text-zinc-400" />}
                  label="Precio de venta" value={`MX$${fmt(result.precio_venta_recomendado_mx)}`} />
                <Stat icon={<TrendingUp className="w-4 h-4 text-zinc-400" />}
                  label="Ganancia x unidad" value={`MX$${fmt(result.ganancia_por_unidad_mx)}`}
                  highlight={result.ganancia_por_unidad_mx > 0} />
                <Stat icon={<TrendingDown className="w-4 h-4 text-zinc-400" />}
                  label="Ganancia total" value={`MX$${fmt(result.ganancia_total_estimada_mx)}`}
                  highlight={result.ganancia_total_estimada_mx > 0} />
              </div>

              {/* Fee breakdown */}
              <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">
                  Desglose por unidad
                </h3>
                <div className="flex flex-col gap-2 text-sm">
                  <FeeRow label="Precio de compra" value={`MX$${fmt(result.precio_compra_mx)}`} />
                  <FeeRow label="Referral fee (15%)" value={`-MX$${fmt(result.referral_fee_mx)}`} negative />
                  <FeeRow label="FBA fee estimado"  value={`-MX$${fmt(result.fba_fee_estimado_mx)}`} negative />
                  <div className="border-t border-zinc-800 pt-2 mt-1 flex justify-between font-semibold">
                    <span className="text-zinc-300">Ganancia neta</span>
                    <span className={result.ganancia_por_unidad_mx >= 0 ? "text-emerald-400" : "text-red-400"}>
                      MX${fmt(result.ganancia_por_unidad_mx)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Summary */}
              {result.resumen_ejecutivo && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                    Resumen ejecutivo
                  </h3>
                  <p className="text-sm text-zinc-300 leading-relaxed">{result.resumen_ejecutivo}</p>
                </div>
              )}

              {/* Actions */}
              {result.acciones_inmediatas?.length > 0 && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <Zap className="w-3.5 h-3.5 text-amber-400" />
                    <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Acciones inmediatas
                    </h3>
                  </div>
                  <ol className="flex flex-col gap-2">
                    {result.acciones_inmediatas.map((a, i) => (
                      <li key={i} className="flex gap-2.5 text-sm text-zinc-300">
                        <span className="text-zinc-600 font-mono text-xs mt-0.5 shrink-0">{i + 1}.</span>
                        {a}
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {/* Risks */}
              {result.riesgos?.length > 0 && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <div className="flex items-center gap-2 mb-3">
                    <Shield className="w-3.5 h-3.5 text-red-400" />
                    <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Riesgos
                    </h3>
                  </div>
                  <ul className="flex flex-col gap-2">
                    {result.riesgos.map((r, i) => (
                      <li key={i} className="flex gap-2.5 text-sm text-zinc-400">
                        <span className="text-red-800 shrink-0">·</span>{r}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {result.tiempo_recuperacion && (
                <p className="text-center text-xs text-zinc-600 pb-2">
                  Tiempo estimado de recuperación: {result.tiempo_recuperacion}
                </p>
              )}
            </>
          )}

          {/* ══ MARCA PROPIA LAYOUT ══ */}
          {!esArbitraje && (
            <>
              {/* Market header card */}
              <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-5">
                <div className="flex items-center gap-2 mb-1">
                  <Sparkles className="w-4 h-4 text-zinc-400" />
                  <span className="text-xs text-zinc-500 uppercase tracking-wider">Mercado analizado</span>
                </div>
                <p className="text-xl font-bold text-zinc-100 capitalize">{result.mercado}</p>
                {result.keyword_principal && (
                  <div className="mt-2 inline-block bg-zinc-800 rounded-lg px-2.5 py-1 text-xs text-zinc-400">
                    Keyword principal: <span className="text-zinc-200 font-medium">{result.keyword_principal}</span>
                  </div>
                )}
              </div>

              {/* Brand concept */}
              {result.concepto?.nombre && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-5">
                  <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">
                    Concepto de marca
                  </h3>
                  <p className="text-xl font-bold text-zinc-50 mb-1">{result.concepto.nombre}</p>
                  <p className="text-sm text-zinc-400 italic mb-3">{result.concepto.tagline}</p>
                  {result.concepto.mensaje_central && (
                    <p className="text-sm text-zinc-300 leading-relaxed border-t border-zinc-800 pt-3">
                      {result.concepto.mensaje_central}
                    </p>
                  )}
                </div>
              )}

              {/* Listing title */}
              {result.listing?.titulo && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Tag className="w-3.5 h-3.5 text-zinc-500" />
                    <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                      Título de listing
                    </h3>
                  </div>
                  <p className="text-sm text-zinc-200 leading-relaxed">{result.listing.titulo}</p>
                </div>
              )}

              {/* Bullets */}
              {result.listing?.top_bullets?.length > 0 && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">
                    Bullets principales
                  </h3>
                  <ul className="flex flex-col gap-2">
                    {result.listing.top_bullets.map((b, i) => (
                      <li key={i} className="flex gap-2.5 text-sm text-zinc-300">
                        <span className="text-zinc-600 shrink-0 mt-0.5">·</span>{b}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Price recommendations */}
              {(result.listing?.precio_lanzamiento > 0 || result.listing?.precio_objetivo > 0) && (
                <div className="grid grid-cols-2 gap-3">
                  {result.listing.precio_lanzamiento > 0 && (
                    <Stat icon={<DollarSign className="w-4 h-4 text-zinc-400" />}
                      label="Precio lanzamiento"
                      value={`MX$${fmt(result.listing.precio_lanzamiento)}`} />
                  )}
                  {result.listing.precio_objetivo > 0 && (
                    <Stat icon={<TrendingUp className="w-4 h-4 text-emerald-400" />}
                      label="Precio objetivo"
                      value={`MX$${fmt(result.listing.precio_objetivo)}`}
                      highlight />
                  )}
                </div>
              )}

              {/* Backend terms */}
              {result.listing?.terminos_backend?.length > 0 && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-4">
                  <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2">
                    Términos backend (SEO oculto)
                  </h3>
                  <p className="text-xs text-zinc-500 leading-relaxed font-mono">
                    {result.listing.terminos_backend.join(", ")}
                  </p>
                </div>
              )}
            </>
          )}

        </div>
      )}

      {/* Steps while running */}
      {!done && steps.length > 0 && (
        <div className="flex flex-col gap-2 mt-2">
          {steps.map((s) => <StepRow key={s.step} step={s} />)}
        </div>
      )}

      {/* Loading placeholder */}
      {!done && steps.length === 0 && (
        <div className="flex flex-col items-center justify-center flex-1 gap-3 text-zinc-600">
          <Loader2 className="w-8 h-8 animate-spin" />
          <p className="text-sm">Iniciando análisis...</p>
        </div>
      )}
    </main>
  )
}

function Stat({ icon, label, value, highlight = false }: {
  icon: React.ReactNode; label: string; value: string; highlight?: boolean
}) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-3.5">
      <div className="flex items-center gap-1.5 mb-1.5">{icon}</div>
      <div className={`text-lg font-bold ${highlight ? "text-zinc-50" : "text-zinc-300"}`}>{value}</div>
      <div className="text-xs text-zinc-600">{label}</div>
    </div>
  )
}

function FeeRow({ label, value, negative = false }: { label: string; value: string; negative?: boolean }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-zinc-500">{label}</span>
      <span className={negative ? "text-red-400" : "text-zinc-300"}>{value}</span>
    </div>
  )
}

function StepRow({ step }: { step: Step }) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <div className="shrink-0 w-5 h-5 flex items-center justify-center">
        {step.status === "done"    && <CheckCircle2 className="w-4 h-4 text-emerald-500" />}
        {step.status === "error"   && <XCircle      className="w-4 h-4 text-red-500" />}
        {step.status === "running" && <Loader2      className="w-4 h-4 text-zinc-400 animate-spin" />}
        {step.status === "pending" && <div          className="w-3 h-3 rounded-full bg-zinc-800 border border-zinc-700" />}
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-sm text-zinc-300">{step.agent}</span>
        {step.status === "running" && (
          <span className="text-xs text-zinc-600 ml-2">{step.message}</span>
        )}
      </div>
    </div>
  )
}

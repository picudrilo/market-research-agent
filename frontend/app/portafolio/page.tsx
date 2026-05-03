"use client"

import { useEffect, useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import {
  ArrowLeft, TrendingUp, DollarSign, Package, AlertTriangle,
  CheckCircle2, Loader2, RefreshCw, ChevronDown, ChevronUp, X,
} from "lucide-react"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

// ─── types ───────────────────────────────────────────────────────────────────

interface Inversion {
  id:                    number
  asin:                  string
  titulo:                string
  unidades:              number
  precio_compra_mx:      number
  precio_venta_real_mx:  number | null
  fecha_compra:          string
  fecha_liquidacion:     string | null
  estado:                string
  roi_real_pct:          number | null
  notas:                 string
  created_at:            string
}

interface MesHistorial {
  mes:      string
  capital:  number
  ganancia: number
  count:    number
}

interface Resumen {
  capital_total_invertido: number
  capital_activo:          number
  capital_liquidado:       number
  roi_real_promedio:       number
  total_inversiones:       number
  activas:                 number
  liquidadas:              number
  alerta_sin_vender:       { id: number; asin: string; titulo: string; dias: number; capital: number }[]
  historial_mensual:       MesHistorial[]
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, dec = 0): string {
  if (n == null) return "—"
  return n.toLocaleString("es-MX", { minimumFractionDigits: dec, maximumFractionDigits: dec })
}

function calcRoiReal(inv: Inversion): number | null {
  if (inv.precio_venta_real_mx == null) return null
  const ingreso = inv.precio_venta_real_mx * inv.unidades
  const costo   = inv.precio_compra_mx    * inv.unidades
  return costo > 0 ? ((ingreso - costo) / costo) * 100 : null
}

// ─── modal liquidar ──────────────────────────────────────────────────────────

function LiquidarModal({
  inv, onClose, onDone,
}: { inv: Inversion; onClose: () => void; onDone: () => void }) {
  const [precio,  setPrecio]  = useState("")
  const [fecha,   setFecha]   = useState(new Date().toISOString().split("T")[0])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState("")

  async function liquidar() {
    const p = parseFloat(precio)
    if (!p || p <= 0) { setError("Ingresa el precio de venta real"); return }
    setLoading(true)
    const roi = ((p - inv.precio_compra_mx) / inv.precio_compra_mx) * 100
    try {
      const res = await fetch(`${API_URL}/inversiones/${inv.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          precio_venta_real_mx: p,
          fecha_liquidacion: fecha,
          estado: "liquidado",
          roi_real_pct: parseFloat(roi.toFixed(2)),
        }),
      })
      if (!res.ok) throw new Error(`Error ${res.status}`)
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al liquidar")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-end sm:items-center justify-center p-4">
      <div className="w-full max-w-sm bg-zinc-900 rounded-2xl border border-zinc-800 shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <span className="text-sm font-medium text-zinc-200">Liquidar inversión</span>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 p-1">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="px-4 py-4 flex flex-col gap-3">
          <div className="bg-zinc-800/60 rounded-lg px-3 py-2">
            <p className="text-xs text-zinc-500 font-mono">{inv.asin}</p>
            <p className="text-sm text-zinc-300 line-clamp-2">{inv.titulo || inv.asin}</p>
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1 block">Precio venta real por unidad (MX$)</label>
            <input type="number" min="0" step="0.01" value={precio} onChange={e => setPrecio(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500" />
          </div>
          <div>
            <label className="text-xs text-zinc-500 mb-1 block">Fecha de liquidación</label>
            <input type="date" value={fecha} onChange={e => setFecha(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500" />
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>
        <div className="px-4 pb-4">
          <button onClick={liquidar} disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            Marcar como vendido
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── main page ────────────────────────────────────────────────────────────────

export default function PortafolioPage() {
  const router = useRouter()
  const [resumen,    setResumen]    = useState<Resumen | null>(null)
  const [inversiones, setInversiones] = useState<Inversion[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState("")
  const [liquidarTarget, setLiquidarTarget] = useState<Inversion | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [filtro,     setFiltro]     = useState<"todos" | "activo" | "liquidado">("todos")

  const cargar = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const [rRes, iRes] = await Promise.all([
        fetch(`${API_URL}/inversiones/resumen`),
        fetch(`${API_URL}/inversiones`),
      ])
      if (!rRes.ok || !iRes.ok) throw new Error("Error cargando datos")
      const [r, i] = await Promise.all([rRes.json(), iRes.json()])
      setResumen(r)
      setInversiones(i)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de red")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { cargar() }, [cargar])

  const filtradas = inversiones.filter(i =>
    filtro === "todos" ? true : i.estado === filtro
  )

  if (loading) {
    return (
      <main className="min-h-screen bg-[#09090b] flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-zinc-500 animate-spin" />
      </main>
    )
  }

  return (
    <main className="min-h-screen bg-[#09090b] text-zinc-100 px-4 py-6 max-w-lg mx-auto flex flex-col gap-6">

      {/* Header */}
      <div>
        <button onClick={() => router.push("/")}
          className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-300 text-sm transition-colors mb-3">
          <ArrowLeft className="w-4 h-4" />
          Volver
        </button>
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-zinc-100">Portafolio</h1>
          <button onClick={cargar}
            className="text-zinc-500 hover:text-zinc-300 p-1.5 rounded-lg hover:bg-zinc-800 transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-950/40 border border-red-900/50 rounded-xl px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Resumen cards */}
      {resumen && (
        <div className="grid grid-cols-2 gap-3">
          <StatCard
            icon={<DollarSign className="w-3.5 h-3.5" />}
            label="Capital total"
            value={`MX$${fmt(resumen.capital_total_invertido, 0)}`}
            sub={`${resumen.total_inversiones} inversiones`}
          />
          <StatCard
            icon={<TrendingUp className="w-3.5 h-3.5" />}
            label="ROI real promedio"
            value={`${fmt(resumen.roi_real_promedio, 1)}%`}
            sub={`${resumen.liquidadas} liquidadas`}
            highlight={resumen.roi_real_promedio >= 20}
          />
          <StatCard
            icon={<Package className="w-3.5 h-3.5" />}
            label="Capital activo"
            value={`MX$${fmt(resumen.capital_activo, 0)}`}
            sub={`${resumen.activas} en inventario`}
          />
          <StatCard
            icon={<CheckCircle2 className="w-3.5 h-3.5" />}
            label="Capital recuperado"
            value={`MX$${fmt(resumen.capital_liquidado, 0)}`}
            sub={`${resumen.liquidadas} vendidas`}
          />
        </div>
      )}

      {/* Alertas >45 días */}
      {resumen && resumen.alerta_sin_vender.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
            Alerta — sin vender +45 días
          </h2>
          <div className="flex flex-col gap-1.5">
            {resumen.alerta_sin_vender.map(a => (
              <div key={a.id}
                className="flex items-center gap-3 bg-amber-950/30 border border-amber-900/40 rounded-xl px-4 py-2.5">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-zinc-300 truncate">{a.titulo}</p>
                  <p className="text-xs text-zinc-600 font-mono">{a.asin}</p>
                </div>
                <div className="text-right shrink-0">
                  <p className="text-sm font-semibold text-amber-400">{a.dias}d</p>
                  <p className="text-xs text-zinc-600">MX${fmt(a.capital, 0)}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Historial mensual */}
      {resumen && resumen.historial_mensual.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
            Historial mensual
          </h2>
          <div className="flex flex-col gap-1.5">
            {[...resumen.historial_mensual].reverse().map(m => {
              const roiM = m.capital > 0 ? (m.ganancia / m.capital) * 100 : 0
              return (
                <div key={m.mes}
                  className="flex items-center gap-3 bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-2.5">
                  <div className="flex-1">
                    <p className="text-sm text-zinc-200">{m.mes}</p>
                    <p className="text-xs text-zinc-600">{m.count} liquidación{m.count !== 1 ? "es" : ""}</p>
                  </div>
                  <div className="text-right">
                    <p className={`text-sm font-semibold ${roiM >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {roiM >= 0 ? "+" : ""}{fmt(roiM, 1)}%
                    </p>
                    <p className="text-xs text-zinc-600">+MX${fmt(m.ganancia, 0)}</p>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Lista de inversiones */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">
            Inversiones
          </h2>
          <div className="flex gap-1.5">
            {(["todos", "activo", "liquidado"] as const).map(f => (
              <button key={f} onClick={() => setFiltro(f)}
                className={`px-2.5 py-1 rounded-lg text-xs transition-colors border ${
                  filtro === f
                    ? "bg-zinc-700 text-zinc-100 border-zinc-600"
                    : "bg-zinc-900 text-zinc-500 border-zinc-800 hover:text-zinc-300"
                }`}>
                {f === "todos" ? "Todos" : f === "activo" ? "Activos" : "Liquidados"}
              </button>
            ))}
          </div>
        </div>

        {filtradas.length === 0 ? (
          <p className="text-zinc-600 text-sm text-center py-8">
            {inversiones.length === 0
              ? "Aún no hay inversiones registradas"
              : "No hay inversiones con este filtro"}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {filtradas.map(inv => {
              const roiCalc  = calcRoiReal(inv)
              const roiColor = roiCalc == null ? "text-zinc-600"
                : roiCalc >= 30 ? "text-emerald-400"
                : roiCalc >= 10 ? "text-amber-400"
                : "text-red-400"
              const isOpen = expandedId === inv.id

              return (
                <div key={inv.id} className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
                  <button type="button" onClick={() => setExpandedId(isOpen ? null : inv.id)}
                    className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-800/40 transition-colors text-left">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${inv.estado === "liquidado" ? "bg-emerald-500" : "bg-blue-500"}`} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-zinc-200 truncate leading-tight">{inv.titulo || inv.asin}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-xs text-zinc-600 font-mono">{inv.asin}</span>
                        <span className="text-xs text-zinc-700">{inv.fecha_compra}</span>
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      {roiCalc != null ? (
                        <p className={`text-sm font-semibold ${roiColor}`}>{fmt(roiCalc, 1)}%</p>
                      ) : (
                        <p className="text-xs text-blue-400">activo</p>
                      )}
                      <p className="text-xs text-zinc-600">MX${fmt(inv.precio_compra_mx * inv.unidades, 0)}</p>
                    </div>
                    {isOpen
                      ? <ChevronUp   className="w-4 h-4 text-zinc-600 shrink-0" />
                      : <ChevronDown className="w-4 h-4 text-zinc-600 shrink-0" />}
                  </button>

                  {isOpen && (
                    <div className="px-4 pb-4 pt-1 border-t border-zinc-800 flex flex-col gap-3">
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                        <FinRow label="Unidades"       value={String(inv.unidades)} />
                        <FinRow label="Precio compra"  value={`MX$${fmt(inv.precio_compra_mx, 2)}`} />
                        <FinRow label="Capital total"  value={`MX$${fmt(inv.precio_compra_mx * inv.unidades, 2)}`} />
                        {inv.precio_venta_real_mx != null && (
                          <>
                            <FinRow label="Precio venta"   value={`MX$${fmt(inv.precio_venta_real_mx, 2)}`} />
                            <FinRow label="ROI real"        value={`${fmt(roiCalc, 1)}%`} highlight={(roiCalc ?? 0) > 0} />
                            <FinRow label="Ganancia neta"
                              value={`MX$${fmt((inv.precio_venta_real_mx - inv.precio_compra_mx) * inv.unidades, 2)}`}
                              highlight={(inv.precio_venta_real_mx - inv.precio_compra_mx) > 0} />
                          </>
                        )}
                        {inv.fecha_liquidacion && (
                          <FinRow label="Liquidación" value={inv.fecha_liquidacion} />
                        )}
                      </div>
                      {inv.notas && (
                        <p className="text-xs text-zinc-500 border-t border-zinc-800 pt-2">{inv.notas}</p>
                      )}
                      {inv.estado !== "liquidado" && (
                        <button
                          onClick={() => setLiquidarTarget(inv)}
                          className="w-full flex items-center justify-center gap-2 py-2 bg-blue-900/40 hover:bg-blue-900/70 border border-blue-800/50 text-blue-300 text-xs font-medium rounded-lg transition-colors">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          Marcar como vendido
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {liquidarTarget && (
        <LiquidarModal
          inv={liquidarTarget}
          onClose={() => setLiquidarTarget(null)}
          onDone={() => { setLiquidarTarget(null); cargar() }}
        />
      )}
    </main>
  )
}

// ─── subcomponentes ───────────────────────────────────────────────────────────

function StatCard({ icon, label, value, sub, highlight = false }: {
  icon: React.ReactNode; label: string; value: string; sub: string; highlight?: boolean
}) {
  return (
    <div className={`bg-zinc-900 border rounded-xl px-4 py-3.5 flex flex-col gap-1 ${
      highlight ? "border-emerald-900/60" : "border-zinc-800"
    }`}>
      <div className={`flex items-center gap-1.5 text-xs ${highlight ? "text-emerald-400" : "text-zinc-500"}`}>
        {icon}
        <span>{label}</span>
      </div>
      <p className="text-xl font-bold text-zinc-100">{value}</p>
      {sub && <p className="text-xs text-zinc-600 leading-tight">{sub}</p>}
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

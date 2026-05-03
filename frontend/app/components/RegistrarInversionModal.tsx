"use client"

import { useState } from "react"
import { X, PlusCircle, Loader2 } from "lucide-react"

interface Props {
  asin: string
  titulo: string
  precio_compra_sugerido: number
  onClose:   () => void
  onSuccess: () => void
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

export function RegistrarInversionModal({ asin, titulo, precio_compra_sugerido, onClose, onSuccess }: Props) {
  const [unidades, setUnidades] = useState("1")
  const [precio,   setPrecio]   = useState(precio_compra_sugerido > 0 ? String(precio_compra_sugerido) : "")
  const [fecha,    setFecha]    = useState(new Date().toISOString().split("T")[0])
  const [notas,    setNotas]    = useState("")
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState("")

  async function registrar() {
    setError("")
    const p = parseFloat(precio)
    const u = parseInt(unidades, 10)
    if (!p || p <= 0) { setError("Ingresa un precio válido"); return }
    if (!u || u <= 0) { setError("Ingresa unidades válidas"); return }

    setLoading(true)
    try {
      const res = await fetch(`${API_URL}/inversiones`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asin, titulo, unidades: u, precio_compra_mx: p, fecha_compra: fecha, notas }),
      })
      if (!res.ok) throw new Error(`Error ${res.status}`)
      onSuccess()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al registrar")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-end sm:items-center justify-center p-4">
      <div className="w-full max-w-sm bg-zinc-900 rounded-2xl overflow-hidden shadow-2xl border border-zinc-800">

        <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <PlusCircle className="w-4 h-4 text-emerald-400" />
            <span className="text-sm font-medium text-zinc-200">Registrar inversión</span>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 transition-colors p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-4 py-4 flex flex-col gap-3">
          <div className="bg-zinc-800/60 rounded-lg px-3 py-2">
            <p className="text-xs text-zinc-500 font-mono">{asin}</p>
            <p className="text-sm text-zinc-300 leading-tight mt-0.5 line-clamp-2">{titulo || asin}</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Unidades</label>
              <input type="number" min="1" value={unidades} onChange={e => setUnidades(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500" />
            </div>
            <div>
              <label className="text-xs text-zinc-500 mb-1 block">Precio compra (MX$)</label>
              <input type="number" min="0" step="0.01" value={precio} onChange={e => setPrecio(e.target.value)}
                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500" />
            </div>
          </div>

          <div>
            <label className="text-xs text-zinc-500 mb-1 block">Fecha de compra</label>
            <input type="date" value={fecha} onChange={e => setFecha(e.target.value)}
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500" />
          </div>

          <div>
            <label className="text-xs text-zinc-500 mb-1 block">Notas (opcional)</label>
            <textarea value={notas} onChange={e => setNotas(e.target.value)} rows={2}
              placeholder="Proveedor, condición, lote..."
              className="w-full bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-zinc-500 resize-none" />
          </div>

          {error && <p className="text-xs text-red-400">{error}</p>}
        </div>

        <div className="px-4 pb-4">
          <button onClick={registrar} disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition-colors">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlusCircle className="w-4 h-4" />}
            Registrar inversión
          </button>
        </div>
      </div>
    </div>
  )
}

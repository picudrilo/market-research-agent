"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { Search, Package, DollarSign, Hash, ChevronRight, Loader2 } from "lucide-react"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

export default function HomePage() {
  const router = useRouter()
  const [producto, setProducto] = useState("")
  const [precio, setPrecio] = useState("")
  const [unidades, setUnidades] = useState("1")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")

    const precioNum = parseFloat(precio)
    if (!producto.trim()) return setError("Escribe el nombre del producto")
    if (!precioNum || precioNum <= 0) return setError("Escribe un precio válido")

    setLoading(true)
    try {
      if (!API_URL) throw new Error("API no configurada — contacta al administrador")

      const res = await fetch(`${API_URL}/validar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          producto: producto.trim(),
          precio_compra: precioNum,
          unidades: parseInt(unidades) || 1,
        }),
      })

      if (!res.ok) {
        let msg = `Error ${res.status}`
        try {
          const data = await res.json()
          msg = data.detail || msg
        } catch {
          // respuesta no es JSON (página de error HTML del servidor)
        }
        throw new Error(msg)
      }

      const { job_id } = await res.json()
      router.push(`/analisis/${job_id}?producto=${encodeURIComponent(producto)}&precio=${precio}&unidades=${unidades}`)
    } catch (err: any) {
      setError(err.message || "No se pudo conectar al servidor")
      setLoading(false)
    }
  }

  return (
    <main className="flex flex-col flex-1 px-5 pt-16 pb-8">
      {/* Header */}
      <div className="mb-10">
        <div className="w-10 h-10 bg-zinc-800 rounded-xl flex items-center justify-center mb-4">
          <Search className="w-5 h-5 text-zinc-300" />
        </div>
        <h1 className="text-2xl font-bold text-zinc-50 leading-tight">
          Validador de productos
        </h1>
        <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
          Ingresa cualquier producto que encuentres y te digo si conviene venderlo en Amazon México.
        </p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        {/* Product name */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
            Producto
          </label>
          <div className="relative">
            <Package className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              value={producto}
              onChange={(e) => setProducto(e.target.value)}
              placeholder="Miel maple Members Mark 600ml"
              className="w-full bg-zinc-900 border border-zinc-800 rounded-xl pl-10 pr-4 py-3.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors"
              disabled={loading}
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>
        </div>

        {/* Price */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
            Precio de compra (MX$)
          </label>
          <div className="relative">
            <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="number"
              value={precio}
              onChange={(e) => setPrecio(e.target.value)}
              placeholder="189"
              inputMode="decimal"
              min="1"
              step="0.01"
              className="w-full bg-zinc-900 border border-zinc-800 rounded-xl pl-10 pr-4 py-3.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors"
              disabled={loading}
            />
          </div>
        </div>

        {/* Units */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
            Unidades a comprar
          </label>
          <div className="relative">
            <Hash className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="number"
              value={unidades}
              onChange={(e) => setUnidades(e.target.value)}
              placeholder="12"
              inputMode="numeric"
              min="1"
              className="w-full bg-zinc-900 border border-zinc-800 rounded-xl pl-10 pr-4 py-3.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors"
              disabled={loading}
            />
          </div>
          <p className="text-xs text-zinc-600">Cuántas piezas planeas comprar</p>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-950/50 border border-red-900/50 rounded-xl px-4 py-3 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={loading}
          className="mt-2 w-full bg-zinc-50 hover:bg-zinc-200 disabled:bg-zinc-800 disabled:text-zinc-600 text-zinc-950 font-semibold rounded-xl py-4 flex items-center justify-center gap-2 transition-colors text-sm"
        >
          {loading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Iniciando análisis...
            </>
          ) : (
            <>
              Analizar producto
              <ChevronRight className="w-4 h-4" />
            </>
          )}
        </button>
      </form>

      {/* Footer hint */}
      <p className="mt-auto pt-8 text-center text-xs text-zinc-700">
        El análisis tarda ~5 min y usa IA para evaluar el mercado
      </p>
    </main>
  )
}

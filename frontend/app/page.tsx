"use client"

import { useCallback, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import {
  Search, Package, DollarSign, Hash, ChevronRight, Loader2,
  Link, TrendingUp, ShoppingCart, RefreshCw, Sparkles,
  Upload, FileText, X, AlertCircle, Table2, ScanLine,
} from "lucide-react"
import { BarcodeScanner } from "./components/BarcodeScanner"

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ""

// ─── helpers ────────────────────────────────────────────────────────────────

function extraerAsin(url: string): string {
  const m = url.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/)
  return m ? m[1] : ""
}

function parsearCSV(texto: string): Record<string, string>[] {
  const lineas = texto.split(/\r?\n/).filter(Boolean)
  if (lineas.length < 2) return []
  const headers = lineas[0].split(",").map(h => h.replace(/^"|"$/g, "").trim())
  return lineas.slice(1).map(linea => {
    // parseo simple — respeta comillas dobles básicas
    const valores = linea.match(/(".*?"|[^,]+|(?<=,)(?=,)|^(?=,)|(?<=,)$)/g) ?? linea.split(",")
    const obj: Record<string, string> = {}
    headers.forEach((h, i) => {
      obj[h] = (valores[i] ?? "").replace(/^"|"$/g, "").trim()
    })
    return obj
  }).filter(r => r["ASIN"] && r["ASIN"] !== "ASIN")
}

function fmt(n: number) {
  return n.toLocaleString("es-MX", { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

// ─── tipos ──────────────────────────────────────────────────────────────────

type Modo = "arbitraje" | "marca_propia" | "batch"

interface ProductoBatch {
  asin:          string
  titulo:        string
  precio_amazon: number
  bsr:           number | null
  reviews:       number | null
  rating:        number | null
  ventas_mes:    number | null
  precio_compra: string  // editable en la tabla
}

// ─── componente principal ────────────────────────────────────────────────────

export default function HomePage() {
  const router  = useRouter()
  const [modo, setModo] = useState<Modo>("arbitraje")

  // -- Arbitraje individual --
  const [producto,     setProducto]     = useState("")
  const [precio,       setPrecio]       = useState("")
  const [unidades,     setUnidades]     = useState("1")
  const [urlAmazon,    setUrlAmazon]    = useState("")
  const [precioAmazon, setPrecioAmazon] = useState("")
  const [ventasMes,    setVentasMes]    = useState("")

  // -- Batch --
  const [csvTexto,        setCsvTexto]        = useState("")
  const [productosPreview, setProductosPreview] = useState<ProductoBatch[]>([])
  const [nombreSesion,    setNombreSesion]    = useState("")
  const [csvError,        setCsvError]        = useState("")
  const fileRef = useRef<HTMLInputElement>(null)

  // -- Scanner --
  const [showScanner,  setShowScanner]  = useState(false)
  const [scanFeedback, setScanFeedback] = useState("")

  // -- Común --
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState("")

  const asin = extraerAsin(urlAmazon)

  // ── scanner de código de barras ───────────────────────────────────────────
  async function buscarPorCodigo(codigo: string) {
    setShowScanner(false)
    setScanFeedback("Buscando en Amazon...")
    try {
      const res = await fetch(`${API_URL}/buscar-por-barcode/${encodeURIComponent(codigo)}`)
      const data = await res.json()
      if (data.asin) {
        setUrlAmazon(data.url ?? "")
        if (data.titulo) setProducto(data.titulo)
        if (data.precio_amazon) setPrecioAmazon(String(data.precio_amazon))
        setScanFeedback(`ASIN encontrado: ${data.asin}`)
      } else {
        setProducto(codigo)
        setScanFeedback("Producto no encontrado en Amazon MX — datos pre-llenados con el código")
      }
    } catch {
      setProducto(codigo)
      setScanFeedback("Sin conexión al servidor — código guardado en el campo de búsqueda")
    }
    setTimeout(() => setScanFeedback(""), 4000)
  }

  // ── cambio de modo ────────────────────────────────────────────────────────
  function cambiarModo(m: Modo) {
    setModo(m)
    setError("")
    setCsvError("")
  }

  // ── carga de CSV ──────────────────────────────────────────────────────────
  const handleCSV = useCallback((texto: string) => {
    setCsvError("")
    setCsvTexto(texto)

    const filas = parsearCSV(texto)
    if (!filas.length) {
      setCsvError("No se encontraron productos en el CSV. Verifica que sea un export Xray de Helium 10.")
      return
    }

    const preview: ProductoBatch[] = filas.map(f => ({
      asin:          f["ASIN"] ?? "",
      titulo:        (f["Product Details"] ?? f["Title"] ?? "").slice(0, 80),
      precio_amazon: parseFloat((f["Price MX$"] ?? f["Price $"] ?? "0").replace(/[^0-9.]/g, "")) || 0,
      bsr:           parseInt(f["BSR"]?.replace(/,/g, "") ?? "") || null,
      reviews:       parseInt(f["Review Count"]?.replace(/,/g, "") ?? "") || null,
      rating:        parseFloat(f["Ratings"] ?? "") || null,
      ventas_mes:    parseInt(f["ASIN Sales"]?.replace(/,/g, "") ?? "") || null,
      precio_compra: f["precio_compra"] ?? f["Precio Compra"] ?? "",
    }))

    setProductosPreview(preview)

    // Sugerir nombre de sesión con fecha
    if (!nombreSesion) {
      const hoy = new Date().toISOString().slice(0, 10)
      setNombreSesion(`sesion_${hoy}`)
    }
  }, [nombreSesion])

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => handleCSV((ev.target?.result as string) ?? "")
    reader.readAsText(file, "utf-8")
    // sugerir nombre de sesión desde el nombre del archivo
    const slug = file.name.replace(/\.csv$/i, "").replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 30)
    setNombreSesion(slug)
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = ev => handleCSV((ev.target?.result as string) ?? "")
    reader.readAsText(file, "utf-8")
    const slug = file.name.replace(/\.csv$/i, "").replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 30)
    setNombreSesion(slug)
  }

  function actualizarPrecio(asin: string, val: string) {
    setProductosPreview(prev =>
      prev.map(p => p.asin === asin ? { ...p, precio_compra: val } : p)
    )
  }

  function limpiarCSV() {
    setCsvTexto("")
    setProductosPreview([])
    setCsvError("")
    if (fileRef.current) fileRef.current.value = ""
  }

  // ── submit individual ─────────────────────────────────────────────────────
  async function handleSubmitIndividual(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (!producto.trim()) return setError("Escribe el nombre del producto")
    if (modo === "arbitraje") {
      if (!parseFloat(precio) || parseFloat(precio) <= 0)
        return setError("Escribe un precio de compra válido")
    }
    setLoading(true)
    try {
      if (!API_URL) throw new Error("API no configurada")
      const body: Record<string, unknown> = {
        producto: producto.trim(),
        precio_compra: modo === "arbitraje" ? parseFloat(precio) || 0 : 0,
        unidades:      modo === "arbitraje" ? parseInt(unidades) || 1 : 1,
        modo:          modo === "arbitraje" ? "arbitraje" : "marca_propia",
      }
      if (modo === "arbitraje") {
        body.url_amazon    = urlAmazon.trim()
        body.precio_amazon = parseFloat(precioAmazon) || 0
        body.ventas_mes    = parseInt(ventasMes) || 0
      }
      const res = await fetch(`${API_URL}/validar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Error ${res.status}`)
      }
      const { job_id } = await res.json()
      const params = new URLSearchParams({ producto, modo: body.modo as string })
      if (modo === "arbitraje") {
        params.set("precio",   precio)
        params.set("unidades", unidades)
        if (urlAmazon)    params.set("url",          urlAmazon)
        if (precioAmazon) params.set("precioAmazon", precioAmazon)
        if (ventasMes)    params.set("ventasMes",    ventasMes)
      }
      router.push(`/analisis/${job_id}?${params}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "No se pudo conectar al servidor"
      setError(msg)
      setLoading(false)
    }
  }

  // ── submit batch ──────────────────────────────────────────────────────────
  async function handleSubmitBatch(e: React.FormEvent) {
    e.preventDefault()
    setError("")

    const sinPrecio = productosPreview.filter(p => !parseFloat(p.precio_compra))
    if (sinPrecio.length === productosPreview.length)
      return setError("Ingresa al menos un precio de compra para analizar")
    if (!csvTexto) return setError("Sube un CSV de Helium 10 primero")

    setLoading(true)
    try {
      if (!API_URL) throw new Error("API no configurada")

      const productosConPrecio = productosPreview
        .filter(p => parseFloat(p.precio_compra) > 0)
        .map(p => ({ asin: p.asin, precio_compra: parseFloat(p.precio_compra) }))

      const res = await fetch(`${API_URL}/validar-batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          productos:      productosConPrecio,
          csv_data:       csvTexto,
          nombre_sesion:  nombreSesion || "sesion_batch",
        }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Error ${res.status}`)
      }
      const { job_id } = await res.json()
      router.push(`/batch/${job_id}?sesion=${encodeURIComponent(nombreSesion)}&total=${productosConPrecio.length}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "No se pudo conectar al servidor"
      setError(msg)
      setLoading(false)
    }
  }

  // ── contadores preview ────────────────────────────────────────────────────
  const totalConPrecio = productosPreview.filter(p => parseFloat(p.precio_compra) > 0).length
  const totalSinPrecio = productosPreview.length - totalConPrecio

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER
  // ─────────────────────────────────────────────────────────────────────────
  return (
    <main className="flex flex-col flex-1 px-5 pt-16 pb-8">

      {/* Header */}
      <div className="mb-8">
        <div className="flex items-start justify-between mb-4">
          <div className="w-10 h-10 bg-zinc-800 rounded-xl flex items-center justify-center">
            <Search className="w-5 h-5 text-zinc-300" />
          </div>
          <button onClick={() => router.push("/portafolio")}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 text-xs rounded-lg transition-colors">
            <TrendingUp className="w-3.5 h-3.5" />
            Portafolio
          </button>
        </div>
        <h1 className="text-2xl font-bold text-zinc-50 leading-tight">
          Validador de productos
        </h1>
        <p className="text-zinc-400 text-sm mt-2 leading-relaxed">
          Analiza oportunidades de arbitraje o investiga un mercado para marca propia en Amazon México.
        </p>
      </div>

      {/* Selector de modo — 3 opciones */}
      <div className="flex gap-1.5 mb-2 bg-zinc-900 p-1 rounded-xl border border-zinc-800">
        <ModeBtn active={modo === "arbitraje"}    onClick={() => cambiarModo("arbitraje")}
          icon={<RefreshCw className="w-3.5 h-3.5" />} label="Arbitraje" />
        <ModeBtn active={modo === "batch"}        onClick={() => cambiarModo("batch")}
          icon={<Table2    className="w-3.5 h-3.5" />} label="Batch" />
        <ModeBtn active={modo === "marca_propia"} onClick={() => cambiarModo("marca_propia")}
          icon={<Sparkles  className="w-3.5 h-3.5" />} label="Producto nuevo" />
      </div>

      {/* Subtítulo de modo */}
      <p className="text-xs text-zinc-600 mb-5 text-center">
        {modo === "arbitraje"    && "Evalúa 1 producto específico para revender en Amazon"}
        {modo === "batch"        && "Sube un CSV Xray con múltiples productos y compara todos a la vez"}
        {modo === "marca_propia" && "Investiga un mercado completo para lanzar tu propia marca"}
      </p>

      {/* Scanner modal */}
      {showScanner && (
        <BarcodeScanner onResult={buscarPorCodigo} onClose={() => setShowScanner(false)} />
      )}

      {/* ── MODO ARBITRAJE INDIVIDUAL ─────────────────────────────────────── */}
      {(modo === "arbitraje" || modo === "marca_propia") && (
        <form onSubmit={handleSubmitIndividual} className="flex flex-col gap-4">

          <Field label={modo === "arbitraje" ? "Producto" : "Mercado o producto de referencia"}>
            <div className="flex gap-2">
              <div className="flex-1">
                <IconInput icon={<Package className="w-4 h-4 text-zinc-500" />}
                  value={producto} onChange={e => setProducto(e.target.value)}
                  placeholder={modo === "arbitraje"
                    ? "NOW Foods Vitamina C-1000 100 Cápsulas"
                    : "Suplementos vitamínicos, miel artesanal..."}
                  disabled={loading} type="text" autoComplete="off" spellCheck={false} />
              </div>
              {modo === "arbitraje" && (
                <button type="button" onClick={() => setShowScanner(true)}
                  disabled={loading}
                  title="Escanear código de barras"
                  className="shrink-0 w-12 bg-zinc-900 border border-zinc-800 rounded-xl flex items-center justify-center text-zinc-500 hover:text-zinc-200 hover:border-zinc-600 transition-colors disabled:opacity-40">
                  <ScanLine className="w-4 h-4" />
                </button>
              )}
            </div>
            {scanFeedback && (
              <p className="text-xs text-emerald-500 mt-1 pl-1">{scanFeedback}</p>
            )}
          </Field>

          {modo === "arbitraje" && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Precio de compra">
                  <IconInput icon={<DollarSign className="w-4 h-4 text-zinc-500" />}
                    value={precio} onChange={e => setPrecio(e.target.value)}
                    placeholder="140" type="number" inputMode="decimal" min="1" step="0.01"
                    disabled={loading} />
                  <p className="text-xs text-zinc-600 mt-1">MX$ por unidad</p>
                </Field>
                <Field label="Unidades">
                  <IconInput icon={<Hash className="w-4 h-4 text-zinc-500" />}
                    value={unidades} onChange={e => setUnidades(e.target.value)}
                    placeholder="1" type="number" inputMode="numeric" min="1"
                    disabled={loading} />
                  <p className="text-xs text-zinc-600 mt-1">Piezas a comprar</p>
                </Field>
              </div>

              <Divider label="Datos de Amazon (opcional)" />

              <Field label="URL del producto">
                <IconInput icon={<Link className="w-4 h-4 text-zinc-500" />}
                  value={urlAmazon} onChange={e => setUrlAmazon(e.target.value)}
                  placeholder="https://www.amazon.com.mx/dp/B0C29KV9TH"
                  type="url" disabled={loading} autoComplete="off" />
                {asin && (
                  <p className="text-xs text-emerald-500 mt-1 pl-1">
                    ASIN: <span className="font-mono font-semibold">{asin}</span>
                  </p>
                )}
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Precio en Amazon">
                  <IconInput icon={<ShoppingCart className="w-4 h-4 text-zinc-500" />}
                    value={precioAmazon} onChange={e => setPrecioAmazon(e.target.value)}
                    placeholder="299" type="number" inputMode="decimal" min="0" step="0.01"
                    disabled={loading} />
                  <p className="text-xs text-zinc-600 mt-1">Precio actual MX$</p>
                </Field>
                <Field label="Ventas/mes">
                  <IconInput icon={<TrendingUp className="w-4 h-4 text-zinc-500" />}
                    value={ventasMes} onChange={e => setVentasMes(e.target.value)}
                    placeholder="1500" type="number" inputMode="numeric" min="0"
                    disabled={loading} />
                  <p className="text-xs text-zinc-600 mt-1">Estimado Helium 10</p>
                </Field>
              </div>
            </>
          )}

          <ErrorMsg msg={error} />

          <SubmitBtn loading={loading}>
            {modo === "arbitraje" ? "Analizar arbitraje" : "Analizar mercado"}
          </SubmitBtn>
        </form>
      )}

      {/* ── MODO BATCH ───────────────────────────────────────────────────── */}
      {modo === "batch" && (
        <form onSubmit={handleSubmitBatch} className="flex flex-col gap-4">

          {/* Drop zone */}
          {!productosPreview.length ? (
            <div
              onDrop={onDrop}
              onDragOver={e => e.preventDefault()}
              onClick={() => fileRef.current?.click()}
              className="border-2 border-dashed border-zinc-700 rounded-2xl p-8 flex flex-col items-center gap-3 cursor-pointer hover:border-zinc-500 transition-colors"
            >
              <div className="w-12 h-12 bg-zinc-800 rounded-xl flex items-center justify-center">
                <Upload className="w-6 h-6 text-zinc-400" />
              </div>
              <div className="text-center">
                <p className="text-sm font-medium text-zinc-300">Sube el CSV Xray de Helium 10</p>
                <p className="text-xs text-zinc-600 mt-1">
                  Arrastra el archivo o haz clic para seleccionar
                </p>
                <p className="text-xs text-zinc-700 mt-2">
                  Agrega columna <code className="text-zinc-500">precio_compra</code> en Excel,
                  o ingresa los precios aquí después de subir
                </p>
              </div>
              <input ref={fileRef} type="file" accept=".csv" className="hidden"
                onChange={onFileChange} />
            </div>
          ) : (
            /* Preview de productos */
            <div className="flex flex-col gap-3">
              {/* Header del preview */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-zinc-400" />
                  <span className="text-sm font-medium text-zinc-300">
                    {productosPreview.length} productos cargados
                  </span>
                  {totalSinPrecio > 0 && (
                    <span className="text-xs text-amber-500 bg-amber-950/40 px-2 py-0.5 rounded-full">
                      {totalSinPrecio} sin precio
                    </span>
                  )}
                </div>
                <button type="button" onClick={limpiarCSV}
                  className="text-zinc-600 hover:text-zinc-400 transition-colors">
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Tabla de preview */}
              <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-zinc-800">
                        <th className="text-left px-3 py-2.5 text-zinc-500 font-medium">Producto</th>
                        <th className="text-right px-3 py-2.5 text-zinc-500 font-medium">Amazon</th>
                        <th className="text-right px-3 py-2.5 text-zinc-500 font-medium">BSR</th>
                        <th className="text-right px-3 py-2.5 text-zinc-500 font-medium w-28">
                          Precio compra
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {productosPreview.map(p => (
                        <tr key={p.asin} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                          <td className="px-3 py-2">
                            <p className="text-zinc-300 leading-tight">{p.titulo || p.asin}</p>
                            <p className="text-zinc-600 font-mono mt-0.5">{p.asin}</p>
                          </td>
                          <td className="px-3 py-2 text-right text-zinc-400 whitespace-nowrap">
                            {p.precio_amazon > 0 ? `MX$${fmt(p.precio_amazon)}` : "—"}
                          </td>
                          <td className="px-3 py-2 text-right text-zinc-400">
                            {p.bsr ? fmt(p.bsr) : "—"}
                          </td>
                          <td className="px-3 py-2">
                            <div className="relative">
                              <span className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600 text-xs">$</span>
                              <input
                                type="number"
                                value={p.precio_compra}
                                onChange={e => actualizarPrecio(p.asin, e.target.value)}
                                placeholder="0"
                                inputMode="decimal"
                                min="0"
                                step="0.01"
                                className="w-full bg-zinc-800 border border-zinc-700 rounded-lg pl-5 pr-2 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-500 transition-colors"
                                disabled={loading}
                              />
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Nombre de sesión */}
              <Field label="Nombre de sesión">
                <IconInput icon={<FileText className="w-4 h-4 text-zinc-500" />}
                  value={nombreSesion} onChange={e => setNombreSesion(e.target.value)}
                  placeholder="sesion_2026-05-02"
                  type="text" autoComplete="off" disabled={loading} />
                <p className="text-xs text-zinc-600 mt-1">
                  Se usará como nombre del archivo en historial/sesiones/
                </p>
              </Field>

              {/* Resumen de qué se analizará */}
              {totalConPrecio > 0 && (
                <div className="bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-sm text-zinc-400">
                  Se analizarán <span className="text-zinc-100 font-semibold">{totalConPrecio}</span> productos
                  {totalSinPrecio > 0 && (
                    <span className="text-zinc-600"> ({totalSinPrecio} sin precio serán omitidos)</span>
                  )}
                </div>
              )}
            </div>
          )}

          {csvError && (
            <div className="flex items-start gap-2 bg-amber-950/40 border border-amber-900/50 rounded-xl px-4 py-3">
              <AlertCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-400">{csvError}</p>
            </div>
          )}

          <ErrorMsg msg={error} />

          {productosPreview.length > 0 && (
            <SubmitBtn loading={loading} disabled={totalConPrecio === 0}>
              Analizar {totalConPrecio} productos
            </SubmitBtn>
          )}
        </form>
      )}

      <p className="mt-auto pt-8 text-center text-xs text-zinc-700">
        {modo === "arbitraje"    && "Análisis individual ~5 min · 7 agentes de IA"}
        {modo === "batch"        && "Análisis batch ~90 seg · 1 llamada a Claude por lote"}
        {modo === "marca_propia" && "Investigación completa ~8 min · 9 agentes de IA"}
      </p>
    </main>
  )
}

// ─── componentes auxiliares ─────────────────────────────────────────────────

function ModeBtn({ active, onClick, icon, label }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string
}) {
  return (
    <button type="button" onClick={onClick}
      className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-colors ${
        active ? "bg-zinc-100 text-zinc-950" : "text-zinc-500 hover:text-zinc-300"
      }`}>
      {icon}{label}
    </button>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-zinc-400 uppercase tracking-wider">{label}</label>
      {children}
    </div>
  )
}

function IconInput({ icon, ...props }: { icon: React.ReactNode } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="relative">
      <span className="absolute left-3 top-1/2 -translate-y-1/2">{icon}</span>
      <input {...props}
        className="w-full bg-zinc-900 border border-zinc-800 rounded-xl pl-10 pr-4 py-3.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors" />
    </div>
  )
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-px bg-zinc-800" />
      <span className="text-xs text-zinc-600">{label}</span>
      <div className="flex-1 h-px bg-zinc-800" />
    </div>
  )
}

function ErrorMsg({ msg }: { msg: string }) {
  if (!msg) return null
  return (
    <div className="bg-red-950/50 border border-red-900/50 rounded-xl px-4 py-3 text-red-400 text-sm">
      {msg}
    </div>
  )
}

function SubmitBtn({ loading, children, disabled }: {
  loading: boolean; children: React.ReactNode; disabled?: boolean
}) {
  return (
    <button type="submit" disabled={loading || disabled}
      className="mt-2 w-full bg-zinc-50 hover:bg-zinc-200 disabled:bg-zinc-800 disabled:text-zinc-600 text-zinc-950 font-semibold rounded-xl py-4 flex items-center justify-center gap-2 transition-colors text-sm">
      {loading
        ? <><Loader2 className="w-4 h-4 animate-spin" />Iniciando análisis...</>
        : <>{children}<ChevronRight className="w-4 h-4" /></>}
    </button>
  )
}

import { useState, useEffect, useCallback, useRef } from 'react'
import { exportInvoicePDF, exportAllPDF, previewInvoicePDF } from '../utils/invoicePdf'

const API = 'http://127.0.0.1:8001'

// ─── Billing type badge colours ──────────────────────────────────────────────
const BT_COLORS = {
  'Charge Upon Activation': 'bg-purple-500/15 text-purple-300 border border-purple-500/30',
  'Hanover':                'bg-blue-500/15   text-blue-300   border border-blue-500/30',
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmt$(n) {
  if (n == null) return '—'
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPct(f) {
  if (f == null) return '—'
  return (f * 100).toFixed(1) + '%'
}

// Build current month default as YYYY-MM
function currentMonthStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

// Format YYYY-MM to "June 2026"
function fmtMonthLabel(ym) {
  if (!ym) return ''
  const [y, m] = ym.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleString('en-US', { month: 'long', year: 'numeric' })
}

// CSV helpers kept for future QB export — PDF is now the primary export

// ─── Line item row in the invoice detail ─────────────────────────────────────
function LineItemRow({ li, idx }) {
  const [expanded, setExpanded] = useState(false)
  const isProrated = li.type === 'prorated'

  return (
    <>
      <tr
        className={`border-b border-slate-700/50 cursor-pointer hover:bg-slate-700/20 transition-colors ${
          isProrated ? '' : 'bg-slate-800/30'
        }`}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Item Code */}
        <td className="px-3 py-2.5 text-xs text-blue-400 font-mono align-top">
          <div className="max-w-[160px] truncate" title={li.itemCode}>{li.itemCode}</div>
        </td>

        {/* Description — first line only, expand for serials */}
        <td className="px-3 py-2.5 text-xs text-slate-300 align-top">
          <div className="flex items-start gap-1.5">
            <span className={`mt-0.5 text-slate-500 transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <div>
              <div className="font-medium text-slate-200">{li.description.split('\n')[0]}</div>
              {li.description.split('\n')[1] && (
                <div className="text-slate-400 text-xs mt-0.5">{li.description.split('\n')[1]}</div>
              )}
              {!expanded && li.serials.length > 0 && (
                <div className="text-slate-500 text-xs mt-0.5">{li.serials.length} device{li.serials.length !== 1 ? 's' : ''}</div>
              )}
            </div>
          </div>
        </td>

        {/* Qty */}
        <td className="px-3 py-2.5 text-xs text-slate-300 text-center align-top">{li.quantity}</td>

        {/* Price Each */}
        <td className="px-3 py-2.5 text-xs text-slate-300 text-right align-top font-mono">
          {fmt$(li.priceEach)}
        </td>

        {/* Prorate info */}
        <td className="px-3 py-2.5 text-xs text-slate-400 text-center align-top">
          {isProrated ? (
            <span className="inline-flex items-center gap-1 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded px-1.5 py-0.5 text-xs">
              {li.daysActive}/{li.daysInMonth}d · {fmtPct(li.prorateFactor)}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 bg-green-500/10 text-green-400 border border-green-500/20 rounded px-1.5 py-0.5 text-xs">
              Full Month
            </span>
          )}
        </td>

        {/* Amount */}
        <td className="px-3 py-2.5 text-xs font-mono font-semibold text-right align-top text-slate-100">
          {fmt$(li.amount)}
        </td>

        {/* Tax */}
        <td className="px-3 py-2.5 text-xs text-slate-500 text-center align-top">Tax</td>
      </tr>

      {/* Expanded serial list */}
      {expanded && (
        <tr className="bg-slate-800/40 border-b border-slate-700/30">
          <td colSpan={7} className="px-8 py-2">
            <div className="flex flex-wrap gap-1.5">
              {li.serials.map(s => (
                <span key={s} className="px-2 py-0.5 bg-slate-700/60 border border-slate-600/40 rounded text-xs font-mono text-slate-300">
                  {s}
                </span>
              ))}
            </div>
            {li.type === 'prorated' && (
              <div className="mt-2 text-xs text-slate-500">
                Monthly rate: {fmt$(li.monthlyRate)} · Prorated to {fmt$(li.priceEach)} per device
                ({li.daysActive} of {li.daysInMonth} days = {fmtPct(li.prorateFactor)})
                · Price source: <span className="text-slate-400">{li.priceSource}</span>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

// ─── PDF Preview Modal ────────────────────────────────────────────────────────
function PdfPreviewModal({ invoice, onClose, onDownload }) {
  const [blobUrl, setBlobUrl] = useState(null)

  useEffect(() => {
    const url = previewInvoicePDF(invoice)
    setBlobUrl(url)
    return () => URL.revokeObjectURL(url)   // cleanup on unmount
  }, [invoice])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative bg-slate-900 border border-slate-700 rounded-xl shadow-2xl flex flex-col"
        style={{ width: '52rem', height: '90vh' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 flex-shrink-0">
          <div>
            <span className="text-sm font-semibold text-slate-100">{invoice.customerName}</span>
            <span className="text-xs text-slate-400 ml-2">· Invoice Preview</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onDownload}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Download PDF
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-100 hover:bg-slate-700 rounded-lg transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* PDF iframe */}
        <div className="flex-1 min-h-0 p-2">
          {blobUrl ? (
            <iframe
              src={blobUrl}
              className="w-full h-full rounded-lg border border-slate-700"
              title="Invoice Preview"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-slate-400 gap-3">
              <svg className="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
              Generating preview…
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Invoice detail panel ─────────────────────────────────────────────────────
function InvoiceDetail({ invoice, onExport }) {
  const [showPreview, setShowPreview] = useState(false)
  const proratedLines = invoice.lineItems.filter(li => li.type === 'prorated')
  const forwardLines  = invoice.lineItems.filter(li => li.type === 'forward')
  const btColor = BT_COLORS[invoice.billingType] || 'bg-slate-700/40 text-slate-400 border border-slate-600/30'

  return (
    <div className="flex flex-col h-full">
      {/* PDF Preview Modal */}
      {showPreview && (
        <PdfPreviewModal
          invoice={invoice}
          onClose={() => setShowPreview(false)}
          onDownload={() => { onExport(); setShowPreview(false) }}
        />
      )}

      {/* Invoice header */}
      <div className="flex items-start justify-between mb-4 pb-4 border-b border-slate-700/50">
        <div>
          <h2 className="text-lg font-bold text-slate-100">{invoice.customerName}</h2>
          <div className="flex items-center gap-2 mt-1">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${btColor}`}>
              {invoice.billingType}
            </span>
            <span className="text-xs text-slate-500">{invoice.billingMonthLabel} · New Activations</span>
          </div>
        </div>
        <div className="flex gap-2">
          {/* Preview button */}
          <button
            onClick={() => setShowPreview(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-xs transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            Preview
          </button>
          {/* Download button */}
          <button
            onClick={onExport}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Export PDF
          </button>
        </div>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-slate-800/60 rounded-lg p-3 border border-slate-700/40">
          <div className="text-xs text-slate-500 mb-1">New Devices</div>
          <div className="text-xl font-bold text-slate-100">{invoice.newDeviceCount}</div>
        </div>
        <div className="bg-amber-500/5 rounded-lg p-3 border border-amber-500/20">
          <div className="text-xs text-amber-500/70 mb-1">Prorated ({invoice.billingMonthLabel})</div>
          <div className="text-xl font-bold text-amber-400">{fmt$(invoice.proratedTotal)}</div>
        </div>
        <div className="bg-green-500/5 rounded-lg p-3 border border-green-500/20">
          <div className="text-xs text-green-500/70 mb-1">Forward ({invoice.nextMonthLabel})</div>
          <div className="text-xl font-bold text-green-400">{fmt$(invoice.forwardTotal)}</div>
        </div>
      </div>

      {/* Line items table */}
      <div className="flex-1 overflow-auto rounded-xl border border-slate-700/50">
        <table className="w-full min-w-[700px]">
          <thead className="sticky top-0 z-10 bg-slate-800/95 backdrop-blur-sm">
            <tr className="border-b border-slate-700">
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Item Code</th>
              <th className="px-3 py-2.5 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Description</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-slate-400 uppercase tracking-wider">Qty</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-400 uppercase tracking-wider">Price Each</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-slate-400 uppercase tracking-wider">Prorate</th>
              <th className="px-3 py-2.5 text-right text-xs font-semibold text-slate-400 uppercase tracking-wider">Amount</th>
              <th className="px-3 py-2.5 text-center text-xs font-semibold text-slate-400 uppercase tracking-wider">Tax</th>
            </tr>
          </thead>
          <tbody>
            {/* Prorated lines */}
            {proratedLines.length > 0 && (
              <tr className="bg-amber-500/5">
                <td colSpan={7} className="px-3 py-1.5 text-xs font-semibold text-amber-400/70 uppercase tracking-wider">
                  ── Prorated New Activations · {invoice.billingMonthLabel}
                </td>
              </tr>
            )}
            {proratedLines.map((li, i) => (
              <LineItemRow key={`p-${i}`} li={li} idx={i} />
            ))}

            {/* Forward lines */}
            {forwardLines.length > 0 && (
              <tr className="bg-green-500/5">
                <td colSpan={7} className="px-3 py-1.5 text-xs font-semibold text-green-400/70 uppercase tracking-wider">
                  ── Full Month Forward · {invoice.nextMonthLabel}
                </td>
              </tr>
            )}
            {forwardLines.map((li, i) => (
              <LineItemRow key={`f-${i}`} li={li} idx={i} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Totals footer */}
      <div className="mt-4 flex justify-end">
        <div className="w-64 space-y-1.5">
          <div className="flex justify-between text-xs text-slate-400">
            <span>Prorated subtotal</span>
            <span className="font-mono">{fmt$(invoice.proratedTotal)}</span>
          </div>
          <div className="flex justify-between text-xs text-slate-400">
            <span>Forward month subtotal</span>
            <span className="font-mono">{fmt$(invoice.forwardTotal)}</span>
          </div>
          <div className="flex justify-between text-sm font-bold text-slate-100 border-t border-slate-600 pt-2 mt-2">
            <span>Invoice Total</span>
            <span className="font-mono">{fmt$(invoice.grandTotal)}</span>
          </div>
          {invoice.hasPriceWarnings && (
            <div className="mt-2 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-1.5">
              ⚠ Some devices have unmapped SKUs or missing prices. Review before sending.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Customer list sidebar item ───────────────────────────────────────────────
function CustomerListItem({ invoice, active, onClick }) {
  const btColor = BT_COLORS[invoice.billingType] || 'bg-slate-700/40 text-slate-400'
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors border ${
        active
          ? 'bg-blue-600/20 border-blue-500/40 text-slate-100'
          : 'border-transparent hover:bg-slate-700/40 text-slate-300'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium truncate">{invoice.customerName}</span>
        <span className="text-xs font-mono font-semibold text-slate-200 flex-shrink-0">
          {fmt$(invoice.grandTotal)}
        </span>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <span className={`text-xs px-1.5 py-0 rounded-full ${btColor}`}>{invoice.billingType}</span>
        <span className="text-xs text-slate-500">{invoice.newDeviceCount} device{invoice.newDeviceCount !== 1 ? 's' : ''}</span>
        {invoice.hasPriceWarnings && (
          <span className="text-xs text-amber-400" title="Price warnings">⚠</span>
        )}
      </div>
    </button>
  )
}

// ─── Main Invoices page ───────────────────────────────────────────────────────
export default function Invoices() {
  const [month,        setMonth]        = useState(currentMonthStr())
  const [btFilter,     setBtFilter]     = useState('')           // '' = both
  const [data,         setData]         = useState(null)
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState(null)
  const [selectedId,   setSelectedId]   = useState(null)
  const [search,       setSearch]       = useState('')

  // Auto-generate on mount with current month
  useEffect(() => { generate() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const generate = useCallback(async (overrideMonth, overrideBt) => {
    const m  = overrideMonth ?? month
    const bt = overrideBt   ?? btFilter
    setLoading(true)
    setError(null)
    setSelectedId(null)
    try {
      const params = new URLSearchParams({ month: m })
      if (bt) params.set('billing_type', bt)
      const res = await fetch(`${API}/api/invoices/prorated?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (HTTP ${res.status})`)
      }
      const json = await res.json()
      setData(json)
      if (json.invoices?.length > 0) setSelectedId(json.invoices[0].customerId)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [month, btFilter])

  const handleMonthChange = (e) => {
    setMonth(e.target.value)
  }

  const handleGenerate = () => generate(month, btFilter)

  const filteredInvoices = (data?.invoices || []).filter(inv =>
    !search || inv.customerName.toLowerCase().includes(search.toLowerCase())
  )

  const selectedInvoice = data?.invoices?.find(inv => inv.customerId === selectedId)

  return (
    <div className="flex flex-col h-full gap-0">

      {/* ── Page header ──────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Prorated Invoices</h1>
          <p className="text-sm text-slate-400 mt-1">
            New device activations for CUA &amp; Hanover customers · prorated to remaining days in billing month
          </p>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3 flex-wrap justify-end">
          {/* Month picker */}
          <input
            type="month"
            value={month}
            onChange={handleMonthChange}
            className="px-3 py-2 bg-slate-800 border border-slate-600 text-slate-200 rounded-lg text-sm focus:outline-none focus:border-blue-500"
          />

          {/* Billing type tabs */}
          <div className="flex rounded-lg overflow-hidden border border-slate-600 text-sm">
            {[
              { value: '',                       label: 'Both' },
              { value: 'Charge Upon Activation', label: 'CUA' },
              { value: 'Hanover',                label: 'Hanover' },
            ].map(opt => (
              <button
                key={opt.value}
                onClick={() => setBtFilter(opt.value)}
                className={`px-3 py-1.5 transition-colors ${
                  btFilter === opt.value
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Generate button */}
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-sm transition-colors"
          >
            {loading ? (
              <>
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                Generating…
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 11h.01M12 11h.01M15 11h.01M4 19h16a2 2 0 002-2V7a2 2 0 00-2-2H4a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                Generate
              </>
            )}
          </button>

          {/* Export all */}
          {data && data.invoiceCount > 0 && (
            <button
              onClick={() => exportAllPDF(data)}
              className="flex items-center gap-1.5 px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-sm transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              Export All PDFs
            </button>
          )}
        </div>
      </div>

      {/* ── Summary bar ──────────────────────────────────────────────────── */}
      {data && (
        <div className="flex items-center gap-6 mb-4 px-4 py-3 bg-slate-800/60 border border-slate-700/50 rounded-xl text-sm">
          <div className="text-slate-400">
            <span className="text-slate-200 font-semibold">{data.invoiceCount}</span>
            <span className="ml-1">customer{data.invoiceCount !== 1 ? 's' : ''}</span>
          </div>
          <div className="text-slate-400">
            <span className="text-slate-200 font-semibold">{data.totalNewDevices}</span>
            <span className="ml-1">new device{data.totalNewDevices !== 1 ? 's' : ''}</span>
          </div>
          <div className="text-slate-400">
            Prorated: <span className="text-amber-400 font-semibold font-mono">{fmt$(data.totalProrated)}</span>
          </div>
          <div className="text-slate-400">
            Forward: <span className="text-green-400 font-semibold font-mono">{fmt$(data.totalForward)}</span>
          </div>
          <div className="text-slate-400 ml-auto">
            Total: <span className="text-slate-100 font-bold font-mono text-base">{fmt$(data.grandTotal)}</span>
          </div>
        </div>
      )}

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-900/20 border border-red-500/30 text-red-300 text-sm flex items-center gap-2">
          <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          {error}
        </div>
      )}

      {/* ── Loading placeholder ───────────────────────────────────────────── */}
      {loading && (
        <div className="flex-1 flex items-center justify-center text-slate-400 gap-3">
          <svg className="animate-spin w-6 h-6" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
          </svg>
          <span>Calculating prorated invoices…</span>
        </div>
      )}

      {/* ── No results ───────────────────────────────────────────────────── */}
      {!loading && data && data.invoiceCount === 0 && (
        <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
          <div className="text-center">
            <div className="text-4xl mb-3">📄</div>
            <div className="font-medium text-slate-400">No prorated invoices for {data.billingMonthLabel}</div>
            <div className="mt-1 text-slate-600">
              No CUA or Hanover devices had a first connect date in this month.
            </div>
          </div>
        </div>
      )}

      {/* ── Empty / pre-generate state ───────────────────────────────────── */}
      {!loading && !data && !error && (
        <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
          <div className="text-center">
            <div className="text-4xl mb-3">📋</div>
            <div className="font-medium text-slate-400">Select a month and click Generate</div>
          </div>
        </div>
      )}

      {/* ── Main split layout: list + detail ─────────────────────────────── */}
      {!loading && data && data.invoiceCount > 0 && (
        <div className="flex-1 flex gap-4 min-h-0">

          {/* Left: customer list */}
          <div className="w-72 flex-shrink-0 flex flex-col gap-2 min-h-0">
            {/* Search */}
            <div className="flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2">
              <svg className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Filter customers…"
                className="bg-transparent text-xs text-slate-200 placeholder-slate-500 outline-none flex-1"
              />
              {search && (
                <button onClick={() => setSearch('')} className="text-slate-500 hover:text-slate-300 text-xs">✕</button>
              )}
            </div>

            {/* Customer list */}
            <div className="flex-1 overflow-auto space-y-1 pr-1">
              {filteredInvoices.map(inv => (
                <CustomerListItem
                  key={inv.customerId}
                  invoice={inv}
                  active={inv.customerId === selectedId}
                  onClick={() => setSelectedId(inv.customerId)}
                />
              ))}
              {filteredInvoices.length === 0 && (
                <div className="text-xs text-slate-500 px-3 py-4 text-center">No customers match</div>
              )}
            </div>
          </div>

          {/* Right: invoice detail */}
          <div className="flex-1 bg-slate-800/40 border border-slate-700/50 rounded-xl p-4 min-h-0 overflow-auto">
            {selectedInvoice ? (
              <InvoiceDetail
                invoice={selectedInvoice}
                onExport={() => exportInvoicePDF(selectedInvoice)}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-slate-500 text-sm">
                Select a customer to view invoice detail
              </div>
            )}
          </div>

        </div>
      )}
    </div>
  )
}

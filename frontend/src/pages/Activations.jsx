import React, { useState, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmt$(n) {
  if (n == null) return '—'
  const v = Number(n)
  if (isNaN(v)) return '—'
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(f) {
  if (f == null) return '—'
  return (Number(f) * 100).toFixed(1) + '%'
}

function fmtDate(s) {
  if (!s) return '—'
  try {
    const [y, m, d] = s.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    })
  } catch { return s }
}

// Default: first of current month → today
function defaultDates() {
  const today = new Date()
  const from  = new Date(today.getFullYear(), today.getMonth(), 1)
  const fmt   = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  return { from: fmt(from), to: fmt(today) }
}

// Build the full ordered quick-range list
// Ascending order: smallest/most-recent windows first, then calendar years
function buildRanges(setFromDate, setToDate) {
  const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  const today = new Date()
  const t = fmt(today)

  // yesterday
  const yest = new Date(today); yest.setDate(yest.getDate() - 1)

  // first of this month
  const thisMonthStart = new Date(today.getFullYear(), today.getMonth(), 1)

  // last month: first → last day
  const lastMonthStart = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  const lastMonthEnd   = new Date(today.getFullYear(), today.getMonth(), 0)

  // rolling windows: N days ago → today
  const daysAgo = n => { const d = new Date(today); d.setDate(d.getDate() - n); return d }

  // 3 months ago (first of month 3 months back → today)
  const threeMonthsStart = new Date(today.getFullYear(), today.getMonth() - 3, 1)

  // 6 months ago
  const sixMonthsStart = new Date(today.getFullYear(), today.getMonth() - 6, 1)

  // 12 months ago
  const twelveMonthsStart = new Date(today.getFullYear(), today.getMonth() - 12, 1)

  // full calendar years going back 3 years
  const currentYear = today.getFullYear()
  const yearRanges = [currentYear, currentYear - 1, currentYear - 2].map(y => ({
    label: String(y),
    fn: () => {
      setFromDate(`${y}-01-01`)
      setToDate(y === currentYear ? t : `${y}-12-31`)
    },
  }))

  return [
    {
      label: 'Today',
      fn: () => { setFromDate(t); setToDate(t) },
    },
    {
      label: 'Yesterday',
      fn: () => { setFromDate(fmt(yest)); setToDate(fmt(yest)) },
    },
    {
      label: 'This Month',
      fn: () => { setFromDate(fmt(thisMonthStart)); setToDate(t) },
    },
    {
      label: 'Last Month',
      fn: () => { setFromDate(fmt(lastMonthStart)); setToDate(fmt(lastMonthEnd)) },
    },
    {
      label: 'Last 7 Days',
      fn: () => { setFromDate(fmt(daysAgo(7))); setToDate(t) },
    },
    {
      label: 'Last 14 Days',
      fn: () => { setFromDate(fmt(daysAgo(14))); setToDate(t) },
    },
    {
      label: 'Last 30 Days',
      fn: () => { setFromDate(fmt(daysAgo(30))); setToDate(t) },
    },
    {
      label: 'Last 3 Months',
      fn: () => { setFromDate(fmt(threeMonthsStart)); setToDate(t) },
    },
    {
      label: 'Last 6 Months',
      fn: () => { setFromDate(fmt(sixMonthsStart)); setToDate(t) },
    },
    {
      label: 'Last 12 Months',
      fn: () => { setFromDate(fmt(twelveMonthsStart)); setToDate(t) },
    },
    ...yearRanges,
  ]
}

// ─── Chips ───────────────────────────────────────────────────────────────────
function BillingTypeBadge({ billingType }) {
  const bt = billingType || ''
  let cls = 'bg-slate-700/50 text-slate-400 border-slate-600/30'
  if (bt === 'Charge Upon Activation') cls = 'bg-purple-500/15 text-purple-300 border-purple-500/30'
  else if (bt === 'Hanover')           cls = 'bg-blue-500/15   text-blue-300   border-blue-500/30'
  else if (bt === 'Han-CS')            cls = 'bg-cyan-500/15   text-cyan-300   border-cyan-500/30'
  else if (bt === 'Standard')          cls = 'bg-slate-600/50  text-slate-300  border-slate-500/30'
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${cls}`}>
      {bt || 'Unknown'}
    </span>
  )
}

function SkuChip({ skuKey }) {
  const sku = skuKey || ''
  let cls = 'bg-slate-700/50 text-slate-300 border-slate-600/30'
  if (sku === 'UNMAPPED')              cls = 'bg-amber-500/15  text-amber-300  border-amber-500/30'
  else if (sku.startsWith('EXCLUDED')) cls = 'bg-slate-600/40  text-slate-400  border-slate-500/20'
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-mono ${cls} max-w-[220px] truncate`}
          title={sku}>
      {sku}
    </span>
  )
}

// ─── Stat card ────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = 'blue' }) {
  const C = {
    blue:   { num: 'text-blue-400',    bg: 'bg-blue-500/10',    border: 'border-blue-500/20'    },
    green:  { num: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/20' },
    amber:  { num: 'text-amber-400',   bg: 'bg-amber-500/10',   border: 'border-amber-500/20'   },
    purple: { num: 'text-purple-400',  bg: 'bg-purple-500/10',  border: 'border-purple-500/20'  },
    red:    { num: 'text-red-400',     bg: 'bg-red-500/10',     border: 'border-red-500/20'     },
  }[color] || {}
  return (
    <div className={`rounded-lg border p-4 ${C.bg} ${C.border}`}>
      <p className="text-xs text-slate-400 font-medium">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${C.num}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

// ─── Row expand detail ────────────────────────────────────────────────────────
function ActivationRowDetail({ record }) {
  const p = record.proration
  return (
    <tr>
      <td colSpan={8} className="px-4 pb-3 pt-0 bg-slate-800/40">
        <div className="grid grid-cols-2 gap-4 text-xs border border-slate-700/40 rounded-md p-3 mt-1 bg-slate-900/40">

          {/* Left: contract details */}
          <div className="space-y-1.5">
            <p className="text-slate-400 font-semibold uppercase text-[10px] tracking-wider mb-2">Contract Details</p>
            <Row label="Serial"        value={record.serialNumber}     mono />
            <Row label="IMEI"          value={record.imei}             mono />
            <Row label="Rate Plan Code" value={record.ratePlanCode}    mono />
            <Row label="Active Plan"   value={record.activePlan} />
            <Row label="Database"      value={record.activeDatabase}   mono />
            <Row label="First Connect" value={fmtDate(record.firstConnectDate)} />
            <Row label="Billing Start" value={fmtDate(record.billingStartDate)} />
            <Row label="Contract Start" value={fmtDate(record.contractStartDate)} />
            {record.contractEndDate && (
              <Row label="Contract End" value={fmtDate(record.contractEndDate)} />
            )}
            {record.isPilot && (
              <div className="mt-1 inline-flex items-center gap-1 bg-amber-500/15 text-amber-300 border border-amber-500/30 rounded px-2 py-0.5 text-[11px]">
                ⚠ PILOT — excluded from prorated invoices
              </div>
            )}
          </div>

          {/* Right: proration */}
          <div className="space-y-1.5">
            <p className="text-slate-400 font-semibold uppercase text-[10px] tracking-wider mb-2">Proration Preview</p>
            {p ? (
              <>
                <Row label="Billing Month"   value={p.billingMonth} />
                <Row label="Activation Date" value={fmtDate(p.activationDate)} />
                <Row label="Days Active"     value={`${p.daysActive} / ${p.daysInMonth}`} />
                <Row label="Prorate Factor"  value={fmtPct(p.prorateFactor)} />
                <Row label="Monthly Rate"    value={fmt$(p.monthlyRate)} />
                <div className="flex gap-2 mt-1 pt-1 border-t border-slate-700/40">
                  <span className="text-slate-500 w-28 flex-shrink-0">Prorated Charge</span>
                  <span className="text-emerald-300 font-bold text-sm">{fmt$(p.proratedCharge)}</span>
                </div>
                <Row label="Price Source"    value={p.priceSource} dim />
                <Row label="QB Item Code"    value={record.itemCode} mono dim />
              </>
            ) : (
              <div className="text-slate-500 italic text-xs">
                {record.skuKey === 'UNMAPPED'
                  ? '⚠ SKU unmapped — add a rate plan mapping in Settings → SKU Mappings'
                  : record.excludedCategory
                    ? '⊘ Excluded category — billed through separate system (e.g. Digital Matter)'
                    : record.isPilot
                      ? '⊘ PILOT rate plan — not billed'
                      : '— Proration not available (no monthly rate found)'}
              </div>
            )}
          </div>

        </div>
      </td>
    </tr>
  )
}

function Row({ label, value, mono, dim }) {
  return (
    <div className="flex gap-2">
      <span className="text-slate-500 w-28 flex-shrink-0">{label}</span>
      <span className={`${mono ? 'font-mono' : ''} ${dim ? 'text-slate-400' : 'text-slate-200'} break-all`}>
        {value || '—'}
      </span>
    </div>
  )
}

// ─── Table row ────────────────────────────────────────────────────────────────
function ActivationRow({ record }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <tr
        className="border-b border-slate-700/40 hover:bg-slate-700/20 cursor-pointer transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        {/* Serial */}
        <td className="px-3 py-2.5 align-top">
          <div className="flex items-center gap-1.5">
            <span className={`text-slate-500 text-xs transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <span className="text-xs font-mono text-slate-200">{record.serialNumber || '—'}</span>
          </div>
        </td>

        {/* Customer */}
        <td className="px-3 py-2.5 align-top">
          <div className="text-xs text-slate-200 font-medium max-w-[190px] truncate" title={record.customerName}>
            {record.customerName || '—'}
          </div>
          {record.activeDatabase && (
            <div className="text-[11px] text-slate-500 font-mono mt-0.5 truncate max-w-[190px]">
              {record.activeDatabase}
            </div>
          )}
        </td>

        {/* Billing Type */}
        <td className="px-3 py-2.5 align-top">
          <BillingTypeBadge billingType={record.billingType} />
        </td>

        {/* Plan / Rate Plan Code */}
        <td className="px-3 py-2.5 align-top">
          <div className="text-xs text-slate-300 max-w-[160px] truncate" title={record.activePlan}>
            {record.activePlan || '—'}
          </div>
          {record.ratePlanCode && (
            <div className="text-[11px] font-mono text-slate-500 mt-0.5">{record.ratePlanCode}</div>
          )}
        </td>

        {/* Resolved SKU */}
        <td className="px-3 py-2.5 align-top">
          <SkuChip skuKey={record.skuKey} />
        </td>

        {/* First Connect Date (the activation date) */}
        <td className="px-3 py-2.5 text-xs text-slate-300 align-top whitespace-nowrap">
          {fmtDate(record.activationDate)}
        </td>

        {/* Billing Start */}
        <td className="px-3 py-2.5 text-xs text-slate-500 align-top whitespace-nowrap">
          {fmtDate(record.billingStartDate)}
        </td>

        {/* Prorated Amount */}
        <td className="px-3 py-2.5 text-right align-top">
          {record.proration ? (
            <div>
              <div className="text-xs font-semibold text-emerald-400">
                {fmt$(record.proration.proratedCharge)}
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {record.proration.daysActive}/{record.proration.daysInMonth}d
              </div>
            </div>
          ) : (
            <span className="text-xs text-slate-600">
              {record.skuKey === 'UNMAPPED' ? '⚠' : record.isPilot ? 'PILOT' : '—'}
            </span>
          )}
        </td>
      </tr>

      {expanded && <ActivationRowDetail record={record} />}
    </>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function Activations() {
  const defaults = defaultDates()

  const [fromDate,    setFromDate]    = useState(defaults.from)
  const [toDate,      setToDate]      = useState(defaults.to)
  const [btFilter,    setBtFilter]    = useState('')
  const [quickRange,  setQuickRange]  = useState('')
  const [search,      setSearch]      = useState('')

  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  async function fetchActivations() {
    setLoading(true)
    setError(null)
    setPage(1)
    try {
      const params = new URLSearchParams({ fromDate, toDate })
      if (btFilter) params.set('billingType', btFilter)
      const res = await fetch(`${API}/api/activations?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      setData(await res.json())
    } catch (e) {
      setError(e.message || 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const records = React.useMemo(() => {
    if (!data?.records) return []
    if (!search) return data.records
    const q = search.toLowerCase()
    return data.records.filter(r =>
      (r.serialNumber   || '').toLowerCase().includes(q) ||
      (r.customerName   || '').toLowerCase().includes(q) ||
      (r.ratePlanCode   || '').toLowerCase().includes(q) ||
      (r.skuKey         || '').toLowerCase().includes(q) ||
      (r.activePlan     || '').toLowerCase().includes(q) ||
      (r.activeDatabase || '').toLowerCase().includes(q)
    )
  }, [data, search])

  const totalPages  = Math.ceil(records.length / PAGE_SIZE) || 1
  const pageRecords = records.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const cacheLabel = data?.cacheAgeHours != null
    ? (data.cacheAgeHours < 1
        ? `Cache: ${Math.round(data.cacheAgeHours * 60)}m old`
        : `Cache: ${data.cacheAgeHours}h old`)
    : null

  return (
    <div className="p-6 space-y-5 text-slate-100">

      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold text-slate-100">Activations</h2>
        <p className="text-sm text-slate-400 mt-1">
          Devices that came online (first connect date) within the selected date range —
          source of truth for prorated invoices and QB Recurrence updates.
        </p>
      </div>

      {/* Filters */}
      <div className="bg-slate-800/60 border border-slate-700/50 rounded-lg p-4">
        <div className="flex flex-wrap items-end gap-3">

          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">From Date</label>
            <input type="date" value={fromDate}
              onChange={e => setFromDate(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30" />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">To Date</label>
            <input type="date" value={toDate}
              onChange={e => setToDate(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30" />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">Billing Type</label>
            <select value={btFilter} onChange={e => setBtFilter(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30">
              <option value="">All Types</option>
              <option value="Charge Upon Activation">Charge Upon Activation</option>
              <option value="Hanover">Hanover</option>
              <option value="Han-CS">Han-CS</option>
              <option value="Standard">Standard</option>
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">Quick Range</label>
            <select
              value={quickRange}
              onChange={e => {
                const chosen = e.target.value
                if (!chosen) return
                const match = buildRanges(setFromDate, setToDate).find(r => r.label === chosen)
                if (match) match.fn()
                setQuickRange('')
              }}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30">
              <option value="">Quick Range</option>
              {buildRanges(setFromDate, setToDate).map(({ label }) => (
                <option key={label} value={label}>{label}</option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium invisible">Load</label>
            <button onClick={fetchActivations} disabled={loading}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:text-blue-400
                         text-white text-sm font-medium rounded transition-colors">
              {loading ? 'Loading…' : 'Load Activations'}
            </button>
          </div>

        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-500/40 rounded-lg p-4 text-sm text-red-300">
          <span className="font-semibold">Error:</span> {error}
          {error.includes('sync') || error.includes('cached') ? (
            <p className="mt-1 text-amber-300/80 text-xs">
              💡 Go to <strong>Customers</strong> and run a MyAdmin sync first — the Activations tab reads from the sync cache.
            </p>
          ) : null}
        </div>
      )}

      {/* Stats */}
      {data && !loading && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard
            label="New Activations"
            value={data.totalRecords.toLocaleString()}
            sub={`${fmtDate(data.fromDate)} → ${fmtDate(data.toDate)}`}
            color="blue"
          />
          <StatCard
            label="Total Prorated"
            value={fmt$(data.totalProratedAmount)}
            sub="Combined proration preview"
            color="green"
          />
          <StatCard
            label="Unmapped SKUs"
            value={data.unmappedCount.toLocaleString()}
            sub={data.unmappedCount > 0 ? 'Check Settings → SKU Mappings' : 'All mapped ✓'}
            color={data.unmappedCount > 0 ? 'amber' : 'green'}
          />
          <StatCard
            label="Cache"
            value={cacheLabel || '—'}
            sub={`${(data.totalContractsInCache || 0).toLocaleString()} contracts loaded`}
            color="purple"
          />
        </div>
      )}

      {/* Table */}
      {data && !loading && (
        <div className="bg-slate-800/60 border border-slate-700/50 rounded-lg overflow-hidden">

          {/* Search + pagination header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/50">
            <div className="flex items-center gap-3">
              <input type="text" value={search}
                onChange={e => { setSearch(e.target.value); setPage(1) }}
                placeholder="Search serial, customer, SKU, plan…"
                className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                           placeholder-slate-500 focus:outline-none focus:border-blue-500
                           focus:ring-1 focus:ring-blue-500/30 w-64" />
              <span className="text-xs text-slate-500">
                {records.length.toLocaleString()} record{records.length !== 1 ? 's' : ''}
                {search ? ' (filtered)' : ''}
              </span>
            </div>
            {totalPages > 1 && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <button onClick={() => setPage(p => Math.max(1, p-1))} disabled={page===1}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">← Prev</button>
                <span>Page {page} / {totalPages}</span>
                <button onClick={() => setPage(p => Math.min(totalPages, p+1))} disabled={page===totalPages}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">Next →</button>
              </div>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-700/50 bg-slate-800/80">
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Serial</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Customer</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Billing Type</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Plan / Code</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Resolved SKU</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">First Connect</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Billing Start</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 text-right whitespace-nowrap">Prorated Amt</th>
                </tr>
              </thead>
              <tbody>
                {pageRecords.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-slate-500 text-sm">
                      {search
                        ? 'No records match your search.'
                        : `No devices with a first-connect date between ${fmtDate(data.fromDate)} and ${fmtDate(data.toDate)}.`}
                    </td>
                  </tr>
                ) : pageRecords.map((r, i) => (
                  <ActivationRow key={r.serialNumber + i} record={r} />
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-slate-700/50 bg-slate-800/40">
              <span className="text-xs text-slate-500">
                Showing {((page-1)*PAGE_SIZE)+1}–{Math.min(page*PAGE_SIZE, records.length)} of {records.length}
              </span>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <button onClick={() => setPage(p => Math.max(1, p-1))} disabled={page===1}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">← Prev</button>
                <span>Page {page} / {totalPages}</span>
                <button onClick={() => setPage(p => Math.min(totalPages, p+1))} disabled={page===totalPages}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40">Next →</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Empty initial state */}
      {!data && !loading && !error && (
        <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg p-10 text-center">
          <div className="text-4xl mb-3">📋</div>
          <p className="text-slate-300 font-medium mb-1">New Device Activations</p>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            Select a date range and click <strong className="text-slate-400">Load Activations</strong> to see
            devices whose first-connect date falls in that window — with resolved QB SKU and proration preview.
          </p>
          <p className="text-slate-600 text-xs mt-3">
            Data reads from the MyAdmin sync cache. Requires an active sync.
          </p>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg p-10 text-center">
          <div className="inline-block w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-3" />
          <p className="text-slate-400 text-sm">Filtering activations from sync cache…</p>
        </div>
      )}

    </div>
  )
}

import React, { useState, useEffect, useCallback, useRef } from 'react'

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
  // Convert YYYY-MM-DD to Mon DD, YYYY
  try {
    const [y, m, d] = s.split('-').map(Number)
    return new Date(y, m - 1, d).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    })
  } catch {
    return s
  }
}

// Default date range: last 30 days
function defaultDates() {
  const today = new Date()
  const from  = new Date(today)
  from.setDate(from.getDate() - 30)
  const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
  return { from: fmt(from), to: fmt(today) }
}

// ─── Request type chip ────────────────────────────────────────────────────────
const RT_COLORS = {
  activate:    'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  terminate:   'bg-red-500/15     text-red-300     border-red-500/30',
  'plan change':'bg-blue-500/15   text-blue-300    border-blue-500/30',
}

function RequestTypeChip({ requestType, isActivation }) {
  const rt = (requestType || '').toLowerCase()
  let cls = 'bg-slate-700/50 text-slate-400 border-slate-600/30'
  if (isActivation) cls = RT_COLORS.activate
  else if (rt.includes('terminat')) cls = RT_COLORS.terminate
  else if (rt.includes('plan change') || rt.includes('mo plan')) cls = RT_COLORS['plan change']
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {requestType || '—'}
    </span>
  )
}

function BillingTypeBadge({ billingType }) {
  const bt = billingType || ''
  let cls = 'bg-slate-700/50 text-slate-400 border-slate-600/30'
  if (bt === 'Charge Upon Activation') cls = 'bg-purple-500/15 text-purple-300 border-purple-500/30'
  else if (bt === 'Hanover')           cls = 'bg-blue-500/15   text-blue-300   border-blue-500/30'
  else if (bt === 'Han-CS')            cls = 'bg-cyan-500/15   text-cyan-300   border-cyan-500/30'
  else if (bt === 'Standard')          cls = 'bg-slate-600/50  text-slate-300  border-slate-500/30'
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {bt || 'Unknown'}
    </span>
  )
}

function SkuChip({ skuKey }) {
  const sku = skuKey || ''
  let cls = 'bg-slate-700/50 text-slate-300 border-slate-600/30'
  if (sku === 'UNMAPPED')                    cls = 'bg-amber-500/15  text-amber-300  border-amber-500/30'
  else if (sku.startsWith('EXCLUDED'))       cls = 'bg-slate-600/40  text-slate-400  border-slate-500/20'
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
    blue:   { num: 'text-blue-400',   bg: 'bg-blue-500/10',   border: 'border-blue-500/20'   },
    green:  { num: 'text-emerald-400',bg: 'bg-emerald-500/10',border: 'border-emerald-500/20' },
    amber:  { num: 'text-amber-400',  bg: 'bg-amber-500/10',  border: 'border-amber-500/20'  },
    purple: { num: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20' },
    red:    { num: 'text-red-400',    bg: 'bg-red-500/10',    border: 'border-red-500/20'    },
  }[color] || {}
  return (
    <div className={`rounded-lg border p-4 ${C.bg} ${C.border}`}>
      <p className="text-xs text-slate-400 font-medium">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${C.num}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

// ─── Row detail expand panel ──────────────────────────────────────────────────
function ActivationRowDetail({ record }) {
  const p = record.proration
  return (
    <tr>
      <td colSpan={10} className="px-4 pb-3 pt-0 bg-slate-800/40">
        <div className="grid grid-cols-2 gap-4 text-xs border border-slate-700/40 rounded-md p-3 mt-1 bg-slate-900/40">

          {/* Left: Device + plan details */}
          <div className="space-y-1.5">
            <p className="text-slate-400 font-semibold uppercase text-[10px] tracking-wider mb-2">Device Details</p>
            <div className="flex gap-2">
              <span className="text-slate-500 w-28 flex-shrink-0">Serial Number</span>
              <span className="text-slate-200 font-mono">{record.serialNumber || '—'}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-slate-500 w-28 flex-shrink-0">IMEI</span>
              <span className="text-slate-300 font-mono">{record.imei || '—'}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-slate-500 w-28 flex-shrink-0">SIM</span>
              <span className="text-slate-300 font-mono">{record.sim || '—'}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-slate-500 w-28 flex-shrink-0">Rate Plan Code</span>
              <span className="text-slate-200 font-mono">{record.ratePlanCode || '—'}</span>
            </div>
            <div className="flex gap-2">
              <span className="text-slate-500 w-28 flex-shrink-0">Active Plan</span>
              <span className="text-slate-300">{record.activePlan || record.requestedPlan || '—'}</span>
            </div>
            {record.activeFeatures && (
              <div className="flex gap-2">
                <span className="text-slate-500 w-28 flex-shrink-0">Features</span>
                <span className="text-slate-400">{record.activeFeatures}</span>
              </div>
            )}
            {record.comments && (
              <div className="flex gap-2">
                <span className="text-slate-500 w-28 flex-shrink-0">Comments</span>
                <span className="text-slate-400 italic">{record.comments}</span>
              </div>
            )}
          </div>

          {/* Right: Proration details */}
          <div className="space-y-1.5">
            <p className="text-slate-400 font-semibold uppercase text-[10px] tracking-wider mb-2">Proration Preview</p>
            {p ? (
              <>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Billing Month</span>
                  <span className="text-slate-200">{p.billingMonth}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Activation Date</span>
                  <span className="text-slate-200">{fmtDate(p.activationDate)}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Days Active</span>
                  <span className="text-slate-200">{p.daysActive} / {p.daysInMonth}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Prorate Factor</span>
                  <span className="text-slate-200">{fmtPct(p.prorateFactor)}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Monthly Rate</span>
                  <span className="text-slate-200">{fmt$(p.monthlyRate)}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Prorated Charge</span>
                  <span className="text-emerald-300 font-semibold">{fmt$(p.proratedCharge)}</span>
                </div>
                <div className="flex gap-2">
                  <span className="text-slate-500 w-28 flex-shrink-0">Price Source</span>
                  <span className="text-slate-400 text-[11px]">{p.priceSource || '—'}</span>
                </div>
                <div className="flex gap-2 mt-1">
                  <span className="text-slate-500 w-28 flex-shrink-0">QB Item Code</span>
                  <span className="text-blue-400 font-mono text-[11px] break-all">{record.itemCode || '—'}</span>
                </div>
              </>
            ) : (
              <div className="text-slate-500 italic text-xs">
                {record.skuKey === 'UNMAPPED'
                  ? '⚠ SKU unmapped — proration not available'
                  : record.excludedCategory
                    ? '⊘ Excluded category (Digital Matter / non-prorated)'
                    : '— No proration data available'}
              </div>
            )}
          </div>
        </div>
      </td>
    </tr>
  )
}

// ─── Main activation table row ────────────────────────────────────────────────
function ActivationRow({ record, idx }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <tr
        className={`border-b border-slate-700/40 transition-colors hover:bg-slate-700/20 cursor-pointer ${
          record.isActivation ? '' : 'opacity-80'
        }`}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Expand toggle + Serial */}
        <td className="px-3 py-2.5 align-top">
          <div className="flex items-center gap-1.5">
            <span className={`text-slate-500 text-xs transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
            <span className="text-xs font-mono text-slate-200">{record.serialNumber || '—'}</span>
          </div>
        </td>

        {/* Customer */}
        <td className="px-3 py-2.5 align-top">
          <div className="text-xs text-slate-200 font-medium max-w-[180px] truncate" title={record.customerName}>
            {record.customerName || '—'}
          </div>
          {record.activeDatabase && (
            <div className="text-[11px] text-slate-500 font-mono mt-0.5 truncate max-w-[180px]">
              {record.activeDatabase}
            </div>
          )}
        </td>

        {/* Billing Type */}
        <td className="px-3 py-2.5 align-top">
          <BillingTypeBadge billingType={record.billingType} />
        </td>

        {/* Request Type */}
        <td className="px-3 py-2.5 align-top">
          <RequestTypeChip requestType={record.requestType} isActivation={record.isActivation} />
        </td>

        {/* Requested Plan */}
        <td className="px-3 py-2.5 align-top">
          <div className="text-xs text-slate-300 max-w-[160px] truncate" title={record.requestedPlan}>
            {record.requestedPlan || record.activePlan || '—'}
          </div>
          {record.ratePlanCode && (
            <div className="text-[11px] font-mono text-slate-500 mt-0.5">
              {record.ratePlanCode}
            </div>
          )}
        </td>

        {/* SKU */}
        <td className="px-3 py-2.5 align-top">
          <SkuChip skuKey={record.skuKey} />
        </td>

        {/* Requested On */}
        <td className="px-3 py-2.5 text-xs text-slate-400 align-top whitespace-nowrap">
          {fmtDate(record.requestedOn)}
        </td>

        {/* Processed On */}
        <td className="px-3 py-2.5 text-xs text-slate-400 align-top whitespace-nowrap">
          {fmtDate(record.processedOn)}
        </td>

        {/* Proration */}
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
            <span className="text-xs text-slate-600">—</span>
          )}
        </td>

        {/* Status */}
        <td className="px-3 py-2.5 align-top">
          <span className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${
            (record.status || '').toLowerCase() === 'completed'
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
              : 'bg-slate-700/50 text-slate-400 border-slate-600/30'
          }`}>
            {record.status || '—'}
          </span>
        </td>
      </tr>

      {expanded && <ActivationRowDetail record={record} />}
    </>
  )
}

// ─── Main Activations page ────────────────────────────────────────────────────
export default function Activations() {
  const defaults = defaultDates()

  const [fromDate,        setFromDate]        = useState(defaults.from)
  const [toDate,          setToDate]          = useState(defaults.to)
  const [activationsOnly, setActivationsOnly] = useState(false)
  const [filterType,      setFilterType]      = useState('')
  const [search,          setSearch]          = useState('')

  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  // Pagination
  const [page,     setPage]     = useState(1)
  const PAGE_SIZE = 50

  async function fetchActivations() {
    setLoading(true)
    setError(null)
    setPage(1)
    try {
      const params = new URLSearchParams({
        fromDate: fromDate,
        toDate:   toDate,
        activationsOnly: activationsOnly ? 'true' : 'false',
      })
      if (filterType) params.set('requestType', filterType)
      const res = await fetch(`${API}/api/activations?${params}`)
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      const json = await res.json()
      setData(json)
    } catch (e) {
      setError(e.message || 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  // Filter records by search box
  const records = React.useMemo(() => {
    if (!data?.records) return []
    if (!search) return data.records
    const q = search.toLowerCase()
    return data.records.filter(r =>
      (r.serialNumber || '').toLowerCase().includes(q) ||
      (r.customerName || '').toLowerCase().includes(q) ||
      (r.ratePlanCode || '').toLowerCase().includes(q) ||
      (r.skuKey || '').toLowerCase().includes(q) ||
      (r.requestType || '').toLowerCase().includes(q) ||
      (r.requestedPlan || '').toLowerCase().includes(q) ||
      (r.activeDatabase || '').toLowerCase().includes(q)
    )
  }, [data, search])

  // Pagination slice
  const totalPages = Math.ceil(records.length / PAGE_SIZE) || 1
  const pageRecords = records.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // Request type options from loaded data
  const requestTypes = React.useMemo(() => {
    if (!data?.records) return []
    const seen = new Set()
    data.records.forEach(r => { if (r.requestType) seen.add(r.requestType) })
    return [...seen].sort()
  }, [data])

  return (
    <div className="p-6 space-y-5 text-slate-100">

      {/* ── Header ── */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">Activations</h2>
          <p className="text-sm text-slate-400 mt-1">
            Device Contract Request History from MyAdmin — source of truth for prorated invoices and QB Recurrence updates.
          </p>
        </div>
      </div>

      {/* ── Filters bar ── */}
      <div className="bg-slate-800/60 border border-slate-700/50 rounded-lg p-4">
        <div className="flex flex-wrap items-end gap-3">

          {/* From date */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">From Date</label>
            <input
              type="date"
              value={fromDate}
              onChange={e => setFromDate(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30"
            />
          </div>

          {/* To date */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">To Date</label>
            <input
              type="date"
              value={toDate}
              onChange={e => setToDate(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30"
            />
          </div>

          {/* Request type filter */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium">Request Type</label>
            <select
              value={filterType}
              onChange={e => setFilterType(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                         focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30"
            >
              <option value="">All Types</option>
              {requestTypes.map(rt => (
                <option key={rt} value={rt}>{rt}</option>
              ))}
            </select>
          </div>

          {/* Activations only toggle */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium invisible">Filter</label>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div
                onClick={() => setActivationsOnly(v => !v)}
                className={`relative w-9 h-5 rounded-full transition-colors ${
                  activationsOnly ? 'bg-blue-600' : 'bg-slate-600'
                }`}
              >
                <div className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                  activationsOnly ? 'translate-x-4' : 'translate-x-0'
                }`} />
              </div>
              <span className="text-sm text-slate-300">Activations only</span>
            </label>
          </div>

          {/* Load button */}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-slate-400 font-medium invisible">Load</label>
            <button
              onClick={fetchActivations}
              disabled={loading}
              className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800 disabled:text-blue-400
                         text-white text-sm font-medium rounded transition-colors"
            >
              {loading ? 'Loading…' : 'Load History'}
            </button>
          </div>

        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="bg-red-900/30 border border-red-500/40 rounded-lg p-4 text-sm text-red-300">
          <span className="font-semibold">Error:</span> {error}
          {error.includes('GetDeviceContractRequestsByPage') && (
            <p className="mt-1 text-red-400/80 text-xs">
              The MyAdmin API method may not be available with your account credentials.
              Please verify you have access to the Activation History page in MyAdmin.
            </p>
          )}
        </div>
      )}

      {/* ── Summary stats ── */}
      {data && !loading && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard
            label="Total Records"
            value={data.totalRecords.toLocaleString()}
            sub={`${data.fromDate} → ${data.toDate}`}
            color="blue"
          />
          <StatCard
            label="Activations"
            value={data.activationCount.toLocaleString()}
            sub={`${data.totalRecords > 0 ? Math.round(data.activationCount / data.totalRecords * 100) : 0}% of records`}
            color="green"
          />
          <StatCard
            label="Total Prorated"
            value={fmt$(data.totalProratedAmount)}
            sub="Combined proration preview"
            color="purple"
          />
          <StatCard
            label="Unmapped SKUs"
            value={data.unmappedCount.toLocaleString()}
            sub={data.unmappedCount > 0 ? 'Need mapping in Settings' : 'All mapped ✓'}
            color={data.unmappedCount > 0 ? 'amber' : 'green'}
          />
        </div>
      )}

      {/* ── Table ── */}
      {data && !loading && (
        <div className="bg-slate-800/60 border border-slate-700/50 rounded-lg overflow-hidden">

          {/* Search + count bar */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700/50">
            <div className="flex items-center gap-3">
              <input
                type="text"
                value={search}
                onChange={e => { setSearch(e.target.value); setPage(1) }}
                placeholder="Search serial, customer, SKU, plan…"
                className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-200
                           placeholder-slate-500 focus:outline-none focus:border-blue-500
                           focus:ring-1 focus:ring-blue-500/30 w-64"
              />
              <span className="text-xs text-slate-500">
                {records.length.toLocaleString()} record{records.length !== 1 ? 's' : ''}
                {search ? ' (filtered)' : ''}
              </span>
            </div>

            {/* Pagination controls */}
            {totalPages > 1 && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40"
                >← Prev</button>
                <span>Page {page} / {totalPages}</span>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40"
                >Next →</button>
              </div>
            )}
          </div>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-700/50 bg-slate-800/80">
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Serial / Device</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Customer</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Billing Type</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Request Type</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Plan / Code</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Resolved SKU</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Requested On</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Processed On</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400 text-right whitespace-nowrap">Prorated Amt</th>
                  <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Status</th>
                </tr>
              </thead>
              <tbody>
                {pageRecords.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-slate-500 text-sm">
                      {search ? 'No records match your search.' : 'No records found for the selected date range.'}
                    </td>
                  </tr>
                ) : (
                  pageRecords.map((r, i) => (
                    <ActivationRow key={r.id || i} record={r} idx={i} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Bottom pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-slate-700/50 bg-slate-800/40">
              <span className="text-xs text-slate-500">
                Showing {((page-1)*PAGE_SIZE)+1}–{Math.min(page*PAGE_SIZE, records.length)} of {records.length}
              </span>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <button
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page === 1}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40"
                >← Prev</button>
                <span>Page {page} / {totalPages}</span>
                <button
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                  className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-40"
                >Next →</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Empty initial state ── */}
      {!data && !loading && !error && (
        <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg p-10 text-center">
          <div className="text-4xl mb-3">📋</div>
          <p className="text-slate-300 font-medium mb-1">Device Contract Request History</p>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            Select a date range and click <strong className="text-slate-400">Load History</strong> to fetch
            activation records from MyAdmin. Use this data to generate prorated invoices and prepare
            QB Recurrence updates.
          </p>
        </div>
      )}

      {/* ── Loading state ── */}
      {loading && (
        <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg p-10 text-center">
          <div className="inline-block w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-3" />
          <p className="text-slate-400 text-sm">Fetching activation history from MyAdmin…</p>
        </div>
      )}

    </div>
  )
}

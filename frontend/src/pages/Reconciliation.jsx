import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://localhost:8001'

// ─── Tiny helpers ─────────────────────────────────────────────────────────────
function fmt$(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  return '$' + n.toFixed(2)
}

function fmtDelta(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return sign + '$' + n.toFixed(2)
}

// ─── Status chip ──────────────────────────────────────────────────────────────
const STATUS_META = {
  ok:         { label: 'OK',          cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/40' },
  over:       { label: 'Over-billed', cls: 'bg-blue-900/50   text-blue-300   border-blue-700/40'   },
  under:      { label: 'Under-billed',cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
  unmapped:   { label: 'Unmapped',    cls: 'bg-amber-900/50  text-amber-300  border-amber-700/40'  },
  no_price:   { label: 'No Price',    cls: 'bg-slate-700/80  text-slate-300  border-slate-600/40'  },
  not_in_qb:  { label: 'Not in QB',  cls: 'bg-purple-900/50 text-purple-300 border-purple-700/40' },
  discrepancy:{ label: 'Discrepancy', cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
}

function StatusChip({ status, size = 'sm' }) {
  const meta = STATUS_META[status] || { label: status, cls: 'bg-slate-700 text-slate-300 border-slate-600' }
  const pad = size === 'xs' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'
  return (
    <span className={`inline-flex items-center rounded border font-medium ${pad} ${meta.cls}`}>
      {meta.label}
    </span>
  )
}

// ─── Summary stat card ────────────────────────────────────────────────────────
function SummaryCard({ label, value, sub, color = 'blue', onClick, active }) {
  const colorMap = {
    green:  { icon: 'text-emerald-400', ring: 'border-emerald-500/40', bg: 'bg-emerald-900/20' },
    red:    { icon: 'text-red-400',     ring: 'border-red-500/40',     bg: 'bg-red-900/20'     },
    amber:  { icon: 'text-amber-400',   ring: 'border-amber-500/40',   bg: 'bg-amber-900/20'   },
    blue:   { icon: 'text-blue-400',    ring: 'border-blue-500/40',    bg: 'bg-blue-900/20'    },
    purple: { icon: 'text-purple-400',  ring: 'border-purple-500/40',  bg: 'bg-purple-900/20'  },
    slate:  { icon: 'text-slate-400',   ring: 'border-slate-500/40',   bg: 'bg-slate-800'      },
  }
  const c = colorMap[color] || colorMap.blue
  const activeRing = active ? `ring-2 ring-offset-1 ring-offset-slate-900 ${c.ring.replace('border-', 'ring-')}` : ''
  return (
    <button
      onClick={onClick}
      className={`text-left bg-slate-800 border ${active ? c.ring + ' ' + c.bg : 'border-slate-700'}
        rounded-xl p-4 transition-all hover:border-slate-500 ${activeRing} ${onClick ? 'cursor-pointer' : 'cursor-default'}`}
    >
      <div className={`text-2xl font-bold ${c.icon}`}>{value}</div>
      <div className="text-xs font-medium text-slate-200 mt-1">{label}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </button>
  )
}

// ─── Device row inside expanded customer ─────────────────────────────────────
function DeviceRow({ device }) {
  const { serialNumber, ratePlanCode, skuKey, skuName,
          expectedPrice, actualPrice, delta, priceSource, status } = device
  return (
    <tr className="border-t border-slate-700/40 hover:bg-slate-700/20">
      <td className="px-4 py-2 text-xs font-mono text-slate-300">{serialNumber || '—'}</td>
      <td className="px-4 py-2 text-xs font-mono">
        {ratePlanCode
          ? <span className="bg-slate-700 text-slate-200 px-1.5 py-0.5 rounded">{ratePlanCode}</span>
          : <span className="text-slate-600">—</span>}
      </td>
      <td className="px-4 py-2 text-xs text-slate-400">{skuKey || '—'}</td>
      <td className="px-4 py-2 text-xs text-slate-400 truncate max-w-[160px]">{skuName || '—'}</td>
      <td className="px-4 py-2 text-xs font-mono text-slate-300">{fmt$(expectedPrice)}</td>
      <td className="px-4 py-2 text-xs font-mono text-slate-300">{fmt$(actualPrice)}</td>
      <td className="px-4 py-2 text-xs font-mono">
        {delta !== null && delta !== undefined
          ? <span className={delta > 0.005 ? 'text-blue-400' : delta < -0.005 ? 'text-red-400' : 'text-emerald-400'}>
              {fmtDelta(delta)}
            </span>
          : <span className="text-slate-600">—</span>}
      </td>
      <td className="px-4 py-2">
        <StatusChip status={status} size="xs" />
      </td>
    </tr>
  )
}

// ─── Customer row (collapsible) ───────────────────────────────────────────────
function CustomerRow({ customer }) {
  const [expanded, setExpanded] = useState(false)
  const {
    customerName, deviceCount,
    ok, over, under, unmapped, noPrice,
    expectedMonthly, actualMonthly, delta,
    status, devices,
  } = customer

  const hasIssues = status !== 'ok'

  return (
    <>
      <tr
        className={`border-t border-slate-700/40 cursor-pointer hover:bg-slate-700/30 transition-colors
          ${expanded ? 'bg-slate-700/20' : ''}`}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Expand chevron */}
        <td className="px-3 py-3 w-8">
          <span className={`text-slate-500 text-xs transition-transform inline-block
            ${expanded ? 'rotate-90' : ''}`}>▶</span>
        </td>

        {/* Customer name */}
        <td className="px-4 py-3">
          <span className="text-sm font-medium text-slate-200">{customerName}</span>
        </td>

        {/* Device count */}
        <td className="px-4 py-3 text-sm text-slate-400 text-right">{deviceCount}</td>

        {/* Issue breakdown */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1.5 flex-wrap">
            {over > 0    && <span className="text-xs bg-blue-900/50 text-blue-300 border border-blue-700/40 rounded px-1.5 py-0.5">{over} over</span>}
            {under > 0   && <span className="text-xs bg-red-900/50 text-red-300 border border-red-700/40 rounded px-1.5 py-0.5">{under} under</span>}
            {unmapped > 0 && <span className="text-xs bg-amber-900/50 text-amber-300 border border-amber-700/40 rounded px-1.5 py-0.5">{unmapped} unmapped</span>}
            {noPrice > 0 && <span className="text-xs bg-slate-700 text-slate-300 border border-slate-600 rounded px-1.5 py-0.5">{noPrice} no price</span>}
            {!hasIssues  && <span className="text-xs text-emerald-500">All OK</span>}
          </div>
        </td>

        {/* Expected / Actual / Delta */}
        <td className="px-4 py-3 text-sm font-mono text-slate-300 text-right">{fmt$(expectedMonthly)}</td>
        <td className="px-4 py-3 text-sm font-mono text-slate-300 text-right">{fmt$(actualMonthly)}</td>
        <td className="px-4 py-3 text-sm font-mono text-right">
          {delta !== null && delta !== undefined
            ? <span className={delta > 0.005 ? 'text-blue-400' : delta < -0.005 ? 'text-red-400' : 'text-emerald-400'}>
                {fmtDelta(delta)}
              </span>
            : <span className="text-slate-600">—</span>}
        </td>

        {/* Status */}
        <td className="px-4 py-3">
          <StatusChip status={status} />
        </td>
      </tr>

      {/* Expanded device table */}
      {expanded && (
        <tr>
          <td colSpan={8} className="p-0">
            <div className="bg-slate-900/60 border-t border-b border-slate-700/40">
              <table className="w-full table-fixed text-xs">
                <colgroup>
                  <col style={{ width: '14%' }} />
                  <col style={{ width: '14%' }} />
                  <col style={{ width: '18%' }} />
                  <col style={{ width: '18%' }} />
                  <col style={{ width: '9%' }} />
                  <col style={{ width: '9%' }} />
                  <col style={{ width: '9%' }} />
                  <col style={{ width: '9%' }} />
                </colgroup>
                <thead>
                  <tr className="border-b border-slate-700/40">
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Serial</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Rate Plan</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">SKU Key</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">SKU Name</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Expected</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Actual</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Delta</th>
                    <th className="px-4 py-2 text-left text-xs text-slate-500 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d, i) => <DeviceRow key={`${d.serialNumber}-${i}`} device={d} />)}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ─── Empty / error states ─────────────────────────────────────────────────────
function EmptyState({ icon, title, body }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
      <div className="text-5xl">{icon}</div>
      <div className="text-base font-semibold text-slate-300">{title}</div>
      {body && <div className="text-sm text-slate-500 max-w-sm">{body}</div>}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  ROOT COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════
export default function Reconciliation() {
  const [data,         setData]        = useState(null)
  const [loading,      setLoading]     = useState(false)
  const [error,        setError]       = useState(null)
  const [statusFilter, setStatusFilter]= useState('')   // '' | 'ok' | 'discrepancy' | 'unmapped' | 'no_price'
  const [search,       setSearch]      = useState('')
  const [expandAll,    setExpandAll]   = useState(false)

  const fetchData = useCallback(async (filter = statusFilter) => {
    setLoading(true)
    setError(null)
    try {
      const params = filter ? `?status_filter=${encodeURIComponent(filter)}` : ''
      const r = await fetch(`${API}/api/reconciliation${params}`)
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${r.status}`)
      }
      setData(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  // Load on mount
  useEffect(() => { fetchData('') }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  function handleFilterClick(f) {
    const next = statusFilter === f ? '' : f
    setStatusFilter(next)
    fetchData(next)
  }

  const summary = data?.summary
  const customers = data?.customers || []

  // Client-side search filter
  const visible = search.trim()
    ? customers.filter(c =>
        (c.customerName || '').toLowerCase().includes(search.toLowerCase())
      )
    : customers

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">Reconciliation</h2>
          <p className="text-sm text-slate-400 mt-0.5">
            Compare MyAdmin device rate plans against QB invoiced prices. Surface under-billing,
            over-billing, unmapped codes, and customers not yet in QB.
          </p>
        </div>
        <button
          onClick={() => fetchData(statusFilter)}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50
            text-white text-sm font-medium rounded-xl transition-colors"
        >
          {loading
            ? <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
              </svg> Running…</>
            : <>↻ Run Reconciliation</>}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-900/40 border border-red-700/40 rounded-xl px-5 py-4 text-sm text-red-300">
          <span className="font-semibold">Error: </span>{error}
          {error.includes('No MyAdmin contract data') && (
            <span className="block mt-1 text-red-400/80">
              Go to the <strong>Customers</strong> page and wait for the sync to complete, then come back here.
            </span>
          )}
        </div>
      )}

      {/* Summary cards — clickable to filter */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <SummaryCard
            label="Customers"
            value={summary.totalCustomers.toLocaleString()}
            sub={`${summary.totalDevices.toLocaleString()} devices`}
            color="blue"
          />
          <SummaryCard
            label="All OK"
            value={summary.ok.toLocaleString()}
            sub="price matches catalog"
            color="green"
            active={statusFilter === 'ok'}
            onClick={() => handleFilterClick('ok')}
          />
          <SummaryCard
            label="Discrepancies"
            value={(summary.over + summary.under).toLocaleString()}
            sub={`${summary.over} over · ${summary.under} under`}
            color="red"
            active={statusFilter === 'discrepancy'}
            onClick={() => handleFilterClick('discrepancy')}
          />
          <SummaryCard
            label="Unmapped"
            value={summary.unmapped.toLocaleString()}
            sub="rate plan has no SKU"
            color="amber"
            active={statusFilter === 'unmapped'}
            onClick={() => handleFilterClick('unmapped')}
          />
          <SummaryCard
            label="No Price"
            value={summary.noPrice.toLocaleString()}
            sub="SKU exists, price missing"
            color="slate"
            active={statusFilter === 'no_price'}
            onClick={() => handleFilterClick('no_price')}
          />
          <SummaryCard
            label="Not in QB"
            value={summary.notInQb.toLocaleString()}
            sub="no QB invoice found"
            color="purple"
          />
        </div>
      )}

      {/* Monthly totals bar */}
      {summary && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl px-6 py-4 flex flex-wrap gap-8 items-center">
          <div>
            <div className="text-xs text-slate-500 mb-0.5">Expected Monthly</div>
            <div className="text-lg font-bold text-slate-200">{fmt$(summary.monthlyExpected)}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500 mb-0.5">Actual Invoiced</div>
            <div className="text-lg font-bold text-slate-200">{fmt$(summary.monthlyActual)}</div>
          </div>
          <div>
            <div className="text-xs text-slate-500 mb-0.5">Monthly Delta</div>
            <div className={`text-lg font-bold
              ${summary.monthlyDelta > 0.01 ? 'text-blue-400'
              : summary.monthlyDelta < -0.01 ? 'text-red-400'
              : 'text-emerald-400'}`}>
              {fmtDelta(summary.monthlyDelta)}
            </div>
          </div>
          <div className="ml-auto text-xs text-slate-500">
            Based on {summary.totalDevices.toLocaleString()} active devices
            {statusFilter && <span className="ml-1 text-amber-400">· filtered: {statusFilter}</span>}
          </div>
        </div>
      )}

      {/* Search + controls */}
      {data && (
        <div className="flex items-center gap-3">
          <div className="relative flex-1 max-w-sm">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500 text-sm">🔍</span>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search customers…"
              className="w-full pl-8 pr-4 py-2 bg-slate-800 border border-slate-700 rounded-xl
                text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-blue-500"
            />
          </div>
          {statusFilter && (
            <button
              onClick={() => { setStatusFilter(''); fetchData('') }}
              className="text-xs text-amber-400 hover:text-amber-300 border border-amber-700/40
                bg-amber-900/20 px-3 py-2 rounded-xl transition-colors"
            >
              ✕ Clear filter: {statusFilter}
            </button>
          )}
          <span className="text-xs text-slate-500 ml-auto">
            {visible.length} customer{visible.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Main table */}
      {loading && !data && (
        <div className="flex items-center gap-3 text-slate-400 py-16 justify-center">
          <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
          </svg>
          Running reconciliation…
        </div>
      )}

      {!loading && !error && !data && (
        <EmptyState
          icon="⚖️"
          title="No data yet"
          body='Click "Run Reconciliation" above to compare MyAdmin devices against QB pricing.'
        />
      )}

      {!loading && !error && data && visible.length === 0 && (
        <EmptyState
          icon="✅"
          title={search ? 'No customers match your search' : 'No customers in this filter'}
          body={search ? 'Try a different name.' : 'Try a different filter or clear the current one.'}
        />
      )}

      {!loading && visible.length > 0 && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
          <table className="w-full table-fixed text-sm">
            <colgroup>
              <col style={{ width: '32px' }} />
              <col style={{ width: '30%' }} />
              <col style={{ width: '7%' }} />
              <col style={{ width: '22%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '11%' }} />
            </colgroup>
            <thead className="bg-slate-900/60">
              <tr>
                <th className="px-3 py-3"></th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Customer</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">Devices</th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Issues</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">Expected</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">Actual</th>
                <th className="px-4 py-3 text-right text-xs text-slate-400 font-semibold uppercase tracking-wide">Delta</th>
                <th className="px-4 py-3 text-left text-xs text-slate-400 font-semibold uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(c => (
                <CustomerRow key={c.customerId} customer={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap gap-3 text-xs text-slate-500 pt-1">
        <span className="font-medium text-slate-400">Legend:</span>
        {Object.entries(STATUS_META)
          .filter(([k]) => !['discrepancy'].includes(k))
          .map(([k, v]) => (
            <span key={k} className={`inline-flex items-center gap-1 border rounded px-2 py-0.5 ${v.cls}`}>
              {v.label}
            </span>
          ))}
        <span className="text-slate-600 ml-2">
          Expected = catalog/override price · Actual = QB invoiced price · Delta = Actual − Expected
        </span>
      </div>

    </div>
  )
}

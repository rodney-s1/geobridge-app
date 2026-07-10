import { useState, useEffect, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Palette ───────────────────────────────────────────────────────────────
const BILLING_COLORS = {
  'Charge Upon Activation': '#3b82f6',
  'Standard':               '#10b981',
  'Hanover':                '#f59e0b',
  'Han-CS':                 '#8b5cf6',
  'Annual':                 '#ec4899',
  'Unknown':                '#6b7280',
}
const STATUS_COLORS = {
  ok:          '#10b981',
  discrepancy: '#f59e0b',
  unmapped:    '#ef4444',
  no_price:    '#6b7280',
  not_in_qb:   '#3b82f6',
}
const COLOR_SEQ = ['#3b82f6','#10b981','#f59e0b','#8b5cf6','#ec4899','#ef4444','#06b6d4','#84cc16']

const fmt$ = (n) => {
  if (n == null) return '—'
  return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
const fmtN = (n) => n == null ? '—' : Number(n).toLocaleString()
const delta$ = (n) => {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  return sign + fmt$(n)
}

// ─── Tiny bar chart (SVG) ──────────────────────────────────────────────────
function BarChart({ data, valueKey = 'count', labelKey = 'label', color = '#3b82f6', height = 120 }) {
  if (!data?.length) return <p className="text-gray-500 text-sm">No data</p>
  const max = Math.max(...data.map(d => d[valueKey] || 0), 1)
  const barW = Math.max(18, Math.floor(480 / data.length) - 4)
  return (
    <div className="overflow-x-auto">
      <svg width={data.length * (barW + 6)} height={height + 40} className="block">
        {data.map((d, i) => {
          const val  = d[valueKey] || 0
          const barH = Math.max(2, Math.round((val / max) * height))
          const x    = i * (barW + 6)
          const y    = height - barH
          const isPartial = d.isCurrentMonth
          return (
            <g key={i}>
              <rect
                x={x} y={y} width={barW} height={barH}
                fill={isPartial ? color + '80' : color}
                rx={3}
              />
              <text x={x + barW / 2} y={height + 14} textAnchor="middle"
                    fontSize={9} fill="#9ca3af">
                {d[labelKey]?.replace(' 20', " '")}
              </text>
              {val > 0 && (
                <text x={x + barW / 2} y={y - 3} textAnchor="middle"
                      fontSize={9} fill="#d1d5db">
                  {val}
                </text>
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// ─── Donut chart (SVG) ────────────────────────────────────────────────────
function DonutChart({ slices, size = 120 }) {
  // slices: [{label, value, color}]
  const total = slices.reduce((s, x) => s + (x.value || 0), 0)
  if (!total) return <p className="text-gray-500 text-sm">No data</p>
  const r = 44, cx = size / 2, cy = size / 2, strokeW = 22
  let cumAngle = -90
  const arcs = slices.map(s => {
    const pct   = s.value / total
    const angle = pct * 360
    const start = cumAngle
    cumAngle   += angle
    return { ...s, pct, startAngle: start, endAngle: cumAngle }
  })
  const toXY = (angle) => {
    const rad = (angle * Math.PI) / 180
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
  }
  return (
    <svg width={size} height={size}>
      {arcs.map((a, i) => {
        const [x1, y1] = toXY(a.startAngle)
        const [x2, y2] = toXY(a.endAngle)
        const large    = (a.endAngle - a.startAngle) > 180 ? 1 : 0
        const d        = `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`
        return (
          <path key={i} d={d} fill="none"
                stroke={a.color} strokeWidth={strokeW}
                strokeLinecap="butt" opacity={0.9}>
            <title>{a.label}: {a.value} ({(a.pct * 100).toFixed(1)}%)</title>
          </path>
        )
      })}
      <text x={cx} y={cy + 4} textAnchor="middle" fontSize={13}
            fontWeight="700" fill="#f3f4f6">
        {fmtN(total)}
      </text>
    </svg>
  )
}

// ─── Stat card ────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = '#3b82f6' }) {
  return (
    <div className="rounded-xl p-4 flex flex-col gap-1"
         style={{ background: color + '18', border: `1px solid ${color}40` }}>
      <p className="text-xs uppercase tracking-wide" style={{ color: color + 'cc' }}>{label}</p>
      <p className="text-2xl font-bold text-white">{value}</p>
      {sub && <p className="text-xs text-gray-400">{sub}</p>}
    </div>
  )
}

// ─── Section header ───────────────────────────────────────────────────────
function SectionHead({ title, sub }) {
  return (
    <div className="mb-4">
      <h3 className="text-sm font-semibold text-gray-200 uppercase tracking-wide">{title}</h3>
      {sub && <p className="text-xs text-gray-500 mt-0.5">{sub}</p>}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 1 — Monthly Revenue
// ═══════════════════════════════════════════════════════════════════════════
function TabRevenue({ data }) {
  const { mrr, totalActive } = data
  const byType    = mrr?.byBillingType   || {}
  const devByType = mrr?.devicesByType   || {}
  const rows = Object.entries(byType).sort((a, b) => b[1] - a[1])

  const barData = rows.map(([k, v], i) => ({
    label: k, count: v, color: BILLING_COLORS[k] || COLOR_SEQ[i % COLOR_SEQ.length]
  }))

  const donutSlices = rows.map(([k, v], i) => ({
    label: k, value: v, color: BILLING_COLORS[k] || COLOR_SEQ[i % COLOR_SEQ.length]
  }))

  return (
    <div className="space-y-8">
      {/* Top stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total MRR"      value={fmt$(mrr?.totalMRR)}  color="#3b82f6" />
        <StatCard label="Active Devices" value={fmtN(totalActive)}    color="#10b981" />
        <StatCard label="Est. ARR"       value={fmt$((mrr?.totalMRR || 0) * 12)} color="#8b5cf6" />
        <StatCard label="Billing Types"  value={rows.length}          color="#f59e0b" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Revenue by type table */}
        <div>
          <SectionHead title="MRR by Billing Type" />
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-700">
                <th className="pb-2 font-medium">Billing Type</th>
                <th className="pb-2 font-medium text-right">Devices</th>
                <th className="pb-2 font-medium text-right">Monthly Rev</th>
                <th className="pb-2 font-medium text-right">% of Total</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([k, v], i) => {
                const color = BILLING_COLORS[k] || COLOR_SEQ[i % COLOR_SEQ.length]
                const pct   = mrr?.totalMRR ? ((v / mrr.totalMRR) * 100).toFixed(1) : '0'
                return (
                  <tr key={k} className="border-b border-gray-800">
                    <td className="py-2.5 flex items-center gap-2">
                      <span className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ background: color }} />
                      {k}
                    </td>
                    <td className="py-2.5 text-right text-gray-300">{fmtN(devByType[k])}</td>
                    <td className="py-2.5 text-right font-mono text-green-400">{fmt$(v)}</td>
                    <td className="py-2.5 text-right text-gray-400">{pct}%</td>
                  </tr>
                )
              })}
              <tr className="font-semibold">
                <td className="pt-3">Total</td>
                <td className="pt-3 text-right">{fmtN(totalActive)}</td>
                <td className="pt-3 text-right text-green-400">{fmt$(mrr?.totalMRR)}</td>
                <td className="pt-3 text-right">100%</td>
              </tr>
            </tbody>
          </table>
        </div>

        {/* Donut */}
        <div className="flex flex-col items-center gap-4">
          <SectionHead title="Revenue Distribution" />
          <DonutChart slices={donutSlices} size={150} />
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-400">
            {rows.map(([k], i) => (
              <div key={k} className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                      style={{ background: BILLING_COLORS[k] || COLOR_SEQ[i % COLOR_SEQ.length] }} />
                {k}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 2 — Portfolio Health
// ═══════════════════════════════════════════════════════════════════════════
function TabPortfolio({ data }) {
  const { portfolioHealth, totalActive, totalTerminated } = data
  const sc  = portfolioHealth?.statusCounts || {}
  const sum = portfolioHealth?.summary || {}

  const statusSlices = [
    { label: 'OK',          value: sc.ok || 0,          color: STATUS_COLORS.ok },
    { label: 'Discrepancy', value: sc.discrepancy || 0,  color: STATUS_COLORS.discrepancy },
    { label: 'Unmapped',    value: sc.unmapped || 0,     color: STATUS_COLORS.unmapped },
    { label: 'No Price',    value: sc.no_price || 0,     color: STATUS_COLORS.no_price },
    { label: 'Not in QB',   value: sc.not_in_qb || 0,   color: STATUS_COLORS.not_in_qb },
  ].filter(s => s.value > 0)

  const totalCust = Object.values(sc).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Active Devices"    value={fmtN(totalActive)}    color="#10b981" />
        <StatCard label="Terminated Devices" value={fmtN(totalTerminated)} color="#ef4444" />
        <StatCard label="Expected MRR"      value={fmt$(sum.monthlyExpected)} color="#3b82f6" />
        <StatCard label="QB Billed MRR"     value={fmt$(sum.monthlyActual)}   color="#f59e0b"
          sub={`Delta ${delta$(sum.monthlyDelta)}`} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <div>
          <SectionHead title="Customer Health Breakdown"
            sub={`${totalCust} total customers`} />
          <div className="space-y-3 mt-2">
            {[
              { key: 'ok',        label: 'OK — prices match QB',      color: STATUS_COLORS.ok },
              { key: 'discrepancy', label: 'Discrepancy — price mismatch', color: STATUS_COLORS.discrepancy },
              { key: 'unmapped',  label: 'Unmapped rate plans',        color: STATUS_COLORS.unmapped },
              { key: 'no_price',  label: 'No price configured',        color: STATUS_COLORS.no_price },
              { key: 'not_in_qb', label: 'Customer not in QB',         color: STATUS_COLORS.not_in_qb },
            ].map(({ key, label, color }) => {
              const val = sc[key] || 0
              const pct = totalCust ? (val / totalCust) * 100 : 0
              return (
                <div key={key}>
                  <div className="flex justify-between text-xs text-gray-400 mb-1">
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block w-2 h-2 rounded-full" style={{ background: color }} />
                      {label}
                    </span>
                    <span className="font-mono">{val}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
                    <div className="h-full rounded-full transition-all"
                         style={{ width: `${pct}%`, background: color }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="flex flex-col items-center gap-4">
          <SectionHead title="Customer Status Distribution" />
          <DonutChart slices={statusSlices} size={150} />
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-gray-400">
            {statusSlices.map(s => (
              <div key={s.label} className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full" style={{ background: s.color }} />
                {s.label} ({s.value})
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Revenue delta summary */}
      <div className="rounded-xl p-5 bg-gray-800/50 border border-gray-700">
        <SectionHead title="Revenue Reconciliation Summary" />
        <div className="grid grid-cols-3 gap-6 text-center">
          <div>
            <p className="text-xs text-gray-500 mb-1">Expected Monthly</p>
            <p className="text-xl font-bold text-blue-400">{fmt$(sum.monthlyExpected)}</p>
            <p className="text-xs text-gray-500 mt-1">{fmtN(sum.totalDevices)} devices</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">QB Invoiced Monthly</p>
            <p className="text-xl font-bold text-yellow-400">{fmt$(sum.monthlyActual)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">Net Delta</p>
            <p className={`text-xl font-bold ${(sum.monthlyDelta || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {delta$(sum.monthlyDelta)}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 3 — Discrepancy Leaderboard
// ═══════════════════════════════════════════════════════════════════════════
function TabDiscrepancies({ data }) {
  const rows = data.discrepancies || []
  const [filter, setFilter] = useState('all')
  const filtered = filter === 'all' ? rows
    : rows.filter(r => r.status === filter)

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Customers with Gap" value={rows.length} color="#f59e0b" />
        <StatCard label="Overbilled (QB > Expected)"
          value={rows.filter(r => r.delta > 0).length} color="#ef4444" />
        <StatCard label="Underbilled (QB < Expected)"
          value={rows.filter(r => r.delta < 0).length} color="#10b981" />
      </div>

      <div className="flex gap-2 text-xs">
        {['all','discrepancy','unmapped','no_price'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-full border transition-colors ${
              filter === f
                ? 'bg-blue-600 border-blue-500 text-white'
                : 'border-gray-700 text-gray-400 hover:border-gray-500'
            }`}>
            {f === 'all' ? 'All' : f.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-700">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-800 text-left text-gray-400 text-xs uppercase tracking-wide">
              <th className="px-4 py-3">Customer</th>
              <th className="px-4 py-3 text-right">Devices</th>
              <th className="px-4 py-3 text-right">Expected / mo</th>
              <th className="px-4 py-3 text-right">QB Billed / mo</th>
              <th className="px-4 py-3 text-right">Delta</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-500">No discrepancies found</td></tr>
            )}
            {filtered.map((r, i) => (
              <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/40">
                <td className="px-4 py-2.5 font-medium text-gray-200 max-w-xs truncate">
                  {r.customerName}
                </td>
                <td className="px-4 py-2.5 text-right text-gray-400">{r.deviceCount}</td>
                <td className="px-4 py-2.5 text-right font-mono text-gray-300">{fmt$(r.expectedMonthly)}</td>
                <td className="px-4 py-2.5 text-right font-mono text-gray-300">{fmt$(r.actualMonthly)}</td>
                <td className={`px-4 py-2.5 text-right font-mono font-semibold ${
                  r.delta > 0 ? 'text-red-400' : r.delta < 0 ? 'text-green-400' : 'text-gray-500'
                }`}>
                  {delta$(r.delta)}
                </td>
                <td className="px-4 py-2.5">
                  <span className="px-2 py-0.5 rounded-full text-xs"
                        style={{
                          background: (STATUS_COLORS[r.status] || '#6b7280') + '25',
                          color:       STATUS_COLORS[r.status] || '#9ca3af',
                          border:      `1px solid ${STATUS_COLORS[r.status] || '#6b7280'}50`,
                        }}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 4 — Unmapped Devices
// ═══════════════════════════════════════════════════════════════════════════
function TabUnmapped({ data }) {
  const rows = data.unmapped || []
  const [search, setSearch] = useState('')
  const filtered = search
    ? rows.filter(r =>
        r.customerName.toLowerCase().includes(search.toLowerCase()) ||
        r.serialNumber.toLowerCase().includes(search.toLowerCase()) ||
        r.ratePlanCode.toLowerCase().includes(search.toLowerCase())
      )
    : rows

  // Group by rate plan for the summary
  const byPlan = {}
  rows.forEach(r => {
    const p = r.ratePlanCode || r.promoCode || '(blank)'
    byPlan[p] = (byPlan[p] || 0) + 1
  })
  const planRows = Object.entries(byPlan).sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Unmapped Devices" value={rows.length} color="#ef4444" />
        <StatCard label="Unique Rate Plans" value={Object.keys(byPlan).length} color="#f59e0b" />
        <StatCard label="Customers Affected"
          value={new Set(rows.map(r => r.customerName)).size} color="#8b5cf6" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Rate plan breakdown */}
        <div className="md:col-span-1">
          <SectionHead title="By Rate Plan Code" sub="Most common unmapped plans" />
          <div className="space-y-2 mt-2">
            {planRows.slice(0, 10).map(([p, cnt], i) => (
              <div key={p} className="flex justify-between items-center text-sm py-1
                                      border-b border-gray-800">
                <span className="font-mono text-blue-300 truncate max-w-[160px]"
                      title={p}>{p || '(blank)'}</span>
                <span className="text-gray-400 ml-2 flex-shrink-0">{cnt} device{cnt !== 1 ? 's' : ''}</span>
              </div>
            ))}
            {planRows.length === 0 && <p className="text-gray-500 text-sm">None — all devices are mapped ✓</p>}
          </div>
        </div>

        {/* Full device list */}
        <div className="md:col-span-2">
          <SectionHead title="All Unmapped Devices" />
          <input
            className="w-full mb-3 px-3 py-2 rounded-lg bg-gray-800 border border-gray-700
                       text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            placeholder="Search customer, serial, rate plan…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          <div className="overflow-x-auto rounded-xl border border-gray-700 max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-800">
                <tr className="text-left text-gray-400 uppercase tracking-wide">
                  <th className="px-3 py-2">Customer</th>
                  <th className="px-3 py-2">Serial</th>
                  <th className="px-3 py-2">Rate Plan</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && (
                  <tr><td colSpan={3} className="px-3 py-6 text-center text-gray-500">None found</td></tr>
                )}
                {filtered.map((r, i) => (
                  <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/40">
                    <td className="px-3 py-2 text-gray-300 max-w-[180px] truncate">{r.customerName}</td>
                    <td className="px-3 py-2 font-mono text-blue-300">{r.serialNumber}</td>
                    <td className="px-3 py-2 font-mono text-yellow-300">{r.ratePlanCode || r.promoCode || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 5 — Activations Trend
// ═══════════════════════════════════════════════════════════════════════════
function TabActivations({ data }) {
  const trend = data.activationsTrend || []
  const total6mo = trend.reduce((s, m) => s + m.count, 0)
  const maxMonth = trend.reduce((mx, m) => m.count > mx.count ? m : mx, trend[0] || {})
  const avg = trend.length ? (total6mo / trend.length).toFixed(1) : 0

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Activations (6 mo)" value={total6mo}      color="#3b82f6" />
        <StatCard label="Monthly Average"     value={avg}          color="#10b981" />
        <StatCard label="Best Month"
          value={maxMonth?.count || 0}
          sub={maxMonth?.label || '—'}
          color="#8b5cf6" />
      </div>

      <div>
        <SectionHead title="New Activations — Last 6 Months"
          sub="Devices with first activation date in each calendar month" />
        <div className="mt-4 p-4 rounded-xl bg-gray-800/40 border border-gray-700">
          <BarChart data={trend} valueKey="count" labelKey="label" color="#3b82f6" height={130} />
        </div>
      </div>

      <div>
        <SectionHead title="Monthly Breakdown" />
        <div className="overflow-x-auto rounded-xl border border-gray-700">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800 text-left text-gray-400 text-xs uppercase tracking-wide">
                <th className="px-4 py-3">Month</th>
                <th className="px-4 py-3 text-right">New Activations</th>
                <th className="px-4 py-3">Trend</th>
              </tr>
            </thead>
            <tbody>
              {[...trend].reverse().map((m, i) => {
                const maxC = Math.max(...trend.map(t => t.count), 1)
                return (
                  <tr key={i} className="border-t border-gray-800">
                    <td className="px-4 py-2.5 text-gray-300">{m.label}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-blue-300">{m.count}</td>
                    <td className="px-4 py-2.5 w-48">
                      <div className="h-1.5 rounded-full bg-gray-700 overflow-hidden">
                        <div className="h-full rounded-full bg-blue-500"
                             style={{ width: `${(m.count / maxC) * 100}%` }} />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 6 — Annual Billing
// ═══════════════════════════════════════════════════════════════════════════
function TabAnnual({ data }) {
  const rows = data.annualCustomers || []
  const totalArr = rows.reduce((s, r) => s + (r.estimatedAnnual || 0), 0)
  const totalDev = rows.reduce((s, r) => s + (r.deviceCount || 0), 0)

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Annual Customers" value={rows.length}       color="#ec4899" />
        <StatCard label="Devices on Annual" value={fmtN(totalDev)}  color="#8b5cf6" />
        <StatCard label="Est. Total ARR"    value={fmt$(totalArr)}   color="#3b82f6" />
      </div>

      {rows.length === 0 ? (
        <div className="rounded-xl border border-gray-700 p-8 text-center text-gray-500">
          No customers configured with Annual billing frequency.<br/>
          <span className="text-xs">Set billing frequency via the Customers → Billing Frequency panel.</span>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-700">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-800 text-left text-gray-400 text-xs uppercase tracking-wide">
                <th className="px-4 py-3">Customer</th>
                <th className="px-4 py-3 text-right">Devices</th>
                <th className="px-4 py-3 text-right">Est. Annual</th>
                <th className="px-4 py-3">Next Invoice Month</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/40">
                  <td className="px-4 py-2.5 font-medium text-gray-200">{r.customerName}</td>
                  <td className="px-4 py-2.5 text-right text-gray-400">{r.deviceCount}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-pink-400">
                    {fmt$(r.estimatedAnnual)}
                  </td>
                  <td className="px-4 py-2.5">
                    {r.billingStartLabel !== '—' ? (
                      <span className="px-2 py-0.5 rounded-full text-xs bg-pink-500/20
                                       text-pink-300 border border-pink-500/30">
                        {r.billingStartLabel}
                      </span>
                    ) : (
                      <span className="text-gray-600">Not set</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// TAB 7 — Terminated Devices
// ═══════════════════════════════════════════════════════════════════════════
function TabTerminated({ data }) {
  const td       = data.terminatedTrend || {}
  const byMonth  = td.byMonth    || []
  const recent   = td.recentDevices || []
  const byCust   = td.byCustomer || []
  const [search, setSearch] = useState('')

  const filteredRecent = search
    ? recent.filter(r =>
        r.customerName.toLowerCase().includes(search.toLowerCase()) ||
        r.serialNumber.toLowerCase().includes(search.toLowerCase())
      )
    : recent

  // Trend chart — show last 12 months
  const chartData = byMonth.map(m => ({
    label:          m.label,
    count:          m.count,
    isCurrentMonth: m.isCurrentMonth,
  }))

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Terminated" value={fmtN(td.totalTracked)} color="#ef4444" />
        <StatCard label="This Month"        value={td.thisMonth || 0}    color="#f59e0b" />
        <StatCard label="Last Month"        value={td.lastMonth || 0}    color="#6b7280" />
        <StatCard label="Customers Affected (12 mo)"
          value={byCust.length} color="#8b5cf6" />
      </div>

      {/* Trend chart */}
      <div>
        <SectionHead title="Terminations — Last 12 Months"
          sub="Bar shaded lighter = current partial month" />
        <div className="mt-4 p-4 rounded-xl bg-gray-800/40 border border-gray-700">
          <BarChart data={chartData} valueKey="count" labelKey="label" color="#ef4444" height={120} />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Top customers by terminations */}
        <div>
          <SectionHead title="Top Customers by Terminations (12 mo)" />
          <div className="overflow-x-auto rounded-xl border border-gray-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-800 text-left text-gray-400 text-xs uppercase tracking-wide">
                  <th className="px-4 py-2.5">Customer</th>
                  <th className="px-4 py-2.5 text-right">Terminated</th>
                  <th className="px-4 py-2.5 text-right">MRR Lost</th>
                </tr>
              </thead>
              <tbody>
                {byCust.length === 0 && (
                  <tr><td colSpan={3} className="px-4 py-6 text-center text-gray-500">None in last 12 months</td></tr>
                )}
                {byCust.slice(0, 10).map((r, i) => (
                  <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/40">
                    <td className="px-4 py-2 text-gray-300 max-w-[200px] truncate">{r.customerName}</td>
                    <td className="px-4 py-2 text-right text-red-400 font-mono">{r.count}</td>
                    <td className="px-4 py-2 text-right text-gray-400 font-mono">{fmt$(r.mrrLost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recent terminations log */}
        <div>
          <SectionHead title="Recent Terminated Devices" />
          <input
            className="w-full mb-3 px-3 py-2 rounded-lg bg-gray-800 border border-gray-700
                       text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            placeholder="Search customer or serial…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          <div className="overflow-x-auto rounded-xl border border-gray-700 max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-800">
                <tr className="text-left text-gray-400 uppercase tracking-wide">
                  <th className="px-3 py-2">Serial</th>
                  <th className="px-3 py-2">Customer</th>
                  <th className="px-3 py-2">End Date</th>
                  <th className="px-3 py-2 text-right">MRR</th>
                </tr>
              </thead>
              <tbody>
                {filteredRecent.length === 0 && (
                  <tr><td colSpan={4} className="px-3 py-6 text-center text-gray-500">None found</td></tr>
                )}
                {filteredRecent.map((r, i) => (
                  <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/40">
                    <td className="px-3 py-2 font-mono text-blue-300">{r.serialNumber}</td>
                    <td className="px-3 py-2 text-gray-300 max-w-[160px] truncate">{r.customerName}</td>
                    <td className="px-3 py-2 text-gray-400 whitespace-nowrap">{r.endDate}</td>
                    <td className="px-3 py-2 text-right text-gray-400 font-mono">
                      {r.monthlyRate ? fmt$(r.monthlyRate) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN REPORTS PAGE
// ═══════════════════════════════════════════════════════════════════════════
const TABS = [
  { id: 'revenue',     label: 'Revenue',       icon: '💰' },
  { id: 'health',      label: 'Portfolio Health', icon: '🏥' },
  { id: 'discrepancy', label: 'Discrepancies', icon: '⚠️' },
  { id: 'unmapped',    label: 'Unmapped',      icon: '🔍' },
  { id: 'activations', label: 'Activations',   icon: '📈' },
  { id: 'annual',      label: 'Annual Billing', icon: '📅' },
  { id: 'terminated',  label: 'Terminated',    icon: '🔴' },
]

export default function Reports() {
  const [activeTab, setActiveTab]  = useState('revenue')
  const [data, setData]            = useState(null)
  const [loading, setLoading]      = useState(true)
  const [error, setError]          = useState(null)
  const [lastFetched, setLastFetched] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    fetch(`${API}/api/reports/summary`)
      .then(r => {
        if (!r.ok) return r.json().then(e => Promise.reject(e.detail || r.statusText))
        return r.json()
      })
      .then(d => {
        setData(d)
        setLastFetched(new Date())
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <div className="inline-block w-8 h-8 border-2 border-blue-500 border-t-transparent
                        rounded-full animate-spin mb-3" />
        <p className="text-gray-400 text-sm">Building reports…</p>
      </div>
    </div>
  )

  if (error) return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center max-w-md">
        <div className="text-4xl mb-3">⚠️</div>
        <p className="text-red-400 font-semibold mb-2">Unable to load reports</p>
        <p className="text-gray-500 text-sm mb-4">{error}</p>
        <button onClick={load}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg">
          Retry
        </button>
      </div>
    </div>
  )

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-white">Reports</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {data?.totalActive != null && `${fmtN(data.totalActive)} active · ${fmtN(data.totalTerminated)} terminated · `}
            {lastFetched && `Updated ${lastFetched.toLocaleTimeString()}`}
          </p>
        </div>
        <button onClick={load}
          className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600
                     text-gray-300 text-sm rounded-lg border border-gray-600 transition-colors">
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M4 4v5h5M20 20v-5h-5M4 20l4.5-4.5A9 9 0 1019.5 7.5" />
          </svg>
          Refresh
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 overflow-x-auto pb-1">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium
                        whitespace-nowrap transition-colors flex-shrink-0 ${
              activeTab === t.id
                ? 'bg-blue-600 text-white'
                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
            }`}>
            <span>{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="rounded-xl bg-gray-900 border border-gray-700 p-6">
        {activeTab === 'revenue'     && <TabRevenue      data={data} />}
        {activeTab === 'health'      && <TabPortfolio    data={data} />}
        {activeTab === 'discrepancy' && <TabDiscrepancies data={data} />}
        {activeTab === 'unmapped'    && <TabUnmapped     data={data} />}
        {activeTab === 'activations' && <TabActivations  data={data} />}
        {activeTab === 'annual'      && <TabAnnual       data={data} />}
        {activeTab === 'terminated'  && <TabTerminated   data={data} />}
      </div>
    </div>
  )
}

import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://localhost:8001'

function fmt$(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return isNaN(n) ? '—' : '$' + n.toFixed(2)
}

function fmtDelta(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (isNaN(n)) return '—'
  return (n >= 0 ? '+' : '') + '$' + n.toFixed(2)
}

// ─── Status chip (reused from Reconciliation palette) ────────────────────────
const STATUS_META = {
  ok:        { label: 'OK',           cls: 'bg-emerald-900/50 text-emerald-300 border-emerald-700/40' },
  over:      { label: 'Over-billed',  cls: 'bg-blue-900/50   text-blue-300   border-blue-700/40'   },
  under:     { label: 'Under-billed', cls: 'bg-red-900/50    text-red-300    border-red-700/40'    },
  unmapped:  { label: 'Unmapped',     cls: 'bg-amber-900/50  text-amber-300  border-amber-700/40'  },
  no_price:  { label: 'No Price',     cls: 'bg-slate-700/80  text-slate-300  border-slate-600/40'  },
  not_in_qb: { label: 'Not in QB',   cls: 'bg-purple-900/50 text-purple-300 border-purple-700/40' },
}

function StatusChip({ status }) {
  const m = STATUS_META[status] || { label: status, cls: 'bg-slate-700 text-slate-300 border-slate-600' }
  return (
    <span className={`inline-flex items-center rounded border font-medium px-2 py-0.5 text-xs ${m.cls}`}>
      {m.label}
    </span>
  )
}

// ─── Tab button ───────────────────────────────────────────────────────────────
function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
        active
          ? 'bg-blue-600 text-white'
          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
      }`}
    >
      {children}
    </button>
  )
}

// ─── Info row (label + value) ─────────────────────────────────────────────────
function InfoRow({ label, value, mono = false }) {
  return (
    <div className="flex items-start gap-4 py-2 border-b border-slate-700/40 last:border-0">
      <span className="text-xs text-slate-500 w-32 flex-shrink-0 mt-0.5">{label}</span>
      <span className={`text-sm text-slate-200 ${mono ? 'font-mono' : ''}`}>{value || '—'}</span>
    </div>
  )
}

// ─── Devices tab ─────────────────────────────────────────────────────────────
function DevicesTab({ customerId }) {
  const [devices, setDevices] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/api/customers/${encodeURIComponent(customerId)}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setDevices(d.devices || []))
      .catch(e => setError(`Failed to load devices (${e})`))
      .finally(() => setLoading(false))
  }, [customerId])

  if (loading) return (
    <div className="flex items-center gap-3 text-slate-400 py-12 justify-center">
      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
      </svg>
      Loading devices…
    </div>
  )

  if (error) return (
    <div className="bg-red-900/40 border border-red-700/40 rounded-xl px-5 py-4 text-sm text-red-300">{error}</div>
  )

  if (!devices || devices.length === 0) return (
    <div className="py-12 text-center text-slate-500 text-sm">No active device contracts found.</div>
  )

  // Rate plan summary
  const rpcCounts = {}
  devices.forEach(d => { const r = d.ratePlanCode || '(none)'; rpcCounts[r] = (rpcCounts[r] || 0) + 1 })

  return (
    <div className="space-y-4">
      {/* RPC pill summary */}
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-slate-500">Rate plans:</span>
        {Object.entries(rpcCounts).sort((a,b) => b[1]-a[1]).map(([code, count]) => (
          <span key={code} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg
            bg-slate-700/80 border border-slate-600/40 text-xs">
            <span className="font-mono text-slate-200">{code}</span>
            <span className="bg-blue-500/30 text-blue-300 rounded-full px-1.5 py-0.5 text-xs font-bold">{count}</span>
          </span>
        ))}
        <span className="text-xs text-slate-600 ml-1">{devices.length} total devices</span>
      </div>

      {/* Device table */}
      <div className="bg-slate-900/60 border border-slate-700/40 rounded-xl overflow-hidden">
        <table className="w-full table-fixed text-xs">
          <colgroup>
            <col style={{ width: '16%' }} />
            <col style={{ width: '20%' }} />
            <col style={{ width: '15%' }} />
            <col style={{ width: '13%' }} />
            <col style={{ width: '16%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '10%' }} />
          </colgroup>
          <thead className="bg-slate-800/60">
            <tr>
              {['Serial', 'Device Type', 'Billing Plan', 'Rate Plan', 'Database', 'Start', 'End'].map(h => (
                <th key={h} className="px-3 py-2.5 text-left text-xs text-slate-400 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {devices.map((d, i) => (
              <tr key={`${d.serialNumber}-${i}`} className="border-t border-slate-700/30 hover:bg-slate-700/20">
                <td className="px-3 py-2 font-mono text-slate-300">{d.serialNumber || '—'}</td>
                <td className="px-3 py-2 text-slate-400">{d.deviceType || '—'}</td>
                <td className="px-3 py-2 text-slate-400">{d.activeBillingPlan || '—'}</td>
                <td className="px-3 py-2">
                  {d.ratePlanCode
                    ? <span className="bg-slate-700 text-slate-200 px-1.5 py-0.5 rounded font-mono">{d.ratePlanCode}</span>
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-3 py-2 text-slate-400 truncate">{d.database || '—'}</td>
                <td className="px-3 py-2 text-slate-500 font-mono">{d.contractStartDate || '—'}</td>
                <td className="px-3 py-2 text-slate-500 font-mono">{d.contractEndDate || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Pricing / Overrides tab ──────────────────────────────────────────────────
function PricingTab({ customerName }) {
  const [overrides,  setOverrides]  = useState(null)
  const [recon,      setRecon]      = useState(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState(null)

  useEffect(() => {
    async function load() {
      setLoading(true)
      try {
        const [ovrR, reconR] = await Promise.all([
          fetch(`${API}/api/settings/customer-overrides`),
          fetch(`${API}/api/reconciliation`),
        ])
        if (ovrR.ok) {
          const all = await ovrR.json()
          // Filter to just this customer (case-insensitive)
          const norm = customerName.trim().toLowerCase()
          setOverrides(all.filter(o => (o.customerName || '').trim().toLowerCase() === norm))
        }
        if (reconR.ok) {
          const data = await reconR.json()
          const match = (data.customers || []).find(
            c => (c.customerName || '').trim().toLowerCase() === customerName.trim().toLowerCase()
          )
          setRecon(match || null)
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [customerName])

  if (loading) return (
    <div className="flex items-center gap-3 text-slate-400 py-12 justify-center">
      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
      </svg>
      Loading pricing…
    </div>
  )

  if (error) return (
    <div className="bg-red-900/40 border border-red-700/40 rounded-xl px-5 py-4 text-sm text-red-300">{error}</div>
  )

  return (
    <div className="space-y-6">

      {/* Reconciliation summary for this customer */}
      {recon ? (
        <div className="bg-slate-900/60 border border-slate-700/40 rounded-xl p-5 space-y-3">
          <h4 className="text-sm font-semibold text-slate-300">Reconciliation Summary</h4>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: 'Expected/mo', value: fmt$(recon.expectedMonthly), cls: 'text-slate-200' },
              { label: 'Actual/mo',   value: fmt$(recon.actualMonthly),   cls: 'text-slate-200' },
              { label: 'Delta',       value: fmtDelta(recon.delta),
                cls: recon.delta > 0.01 ? 'text-blue-400' : recon.delta < -0.01 ? 'text-red-400' : 'text-emerald-400' },
              { label: 'Status',      value: <StatusChip status={recon.status} /> },
            ].map(item => (
              <div key={item.label} className="bg-slate-800 rounded-lg p-3">
                <div className="text-xs text-slate-500 mb-1">{item.label}</div>
                <div className={`text-base font-bold ${item.cls || ''}`}>{item.value}</div>
              </div>
            ))}
          </div>

          {/* Issue badges */}
          {(recon.over > 0 || recon.under > 0 || recon.unmapped > 0 || recon.noPrice > 0) && (
            <div className="flex flex-wrap gap-2 pt-1">
              {recon.over     > 0 && <span className="text-xs bg-blue-900/50 text-blue-300 border border-blue-700/40 rounded px-2 py-0.5">{recon.over} over-billed</span>}
              {recon.under    > 0 && <span className="text-xs bg-red-900/50 text-red-300 border border-red-700/40 rounded px-2 py-0.5">{recon.under} under-billed</span>}
              {recon.unmapped > 0 && <span className="text-xs bg-amber-900/50 text-amber-300 border border-amber-700/40 rounded px-2 py-0.5">{recon.unmapped} unmapped</span>}
              {recon.noPrice  > 0 && <span className="text-xs bg-slate-700 text-slate-300 border border-slate-600 rounded px-2 py-0.5">{recon.noPrice} no price</span>}
            </div>
          )}
        </div>
      ) : (
        <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl px-5 py-4 text-sm text-slate-500 italic">
          No reconciliation data — run Reconciliation first.
        </div>
      )}

      {/* Per-device reconciliation breakdown */}
      {recon?.devices && recon.devices.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-slate-300">Per-Device Pricing</h4>
          <div className="bg-slate-900/60 border border-slate-700/40 rounded-xl overflow-hidden">
            <table className="w-full table-fixed text-xs">
              <colgroup>
                <col style={{ width: '14%' }} />
                <col style={{ width: '13%' }} />
                <col style={{ width: '20%' }} />
                <col style={{ width: '11%' }} />
                <col style={{ width: '11%' }} />
                <col style={{ width: '11%' }} />
                <col style={{ width: '10%' }} />
                <col style={{ width: '10%' }} />
              </colgroup>
              <thead className="bg-slate-800/60">
                <tr>
                  {['Serial','Rate Plan','SKU','Expected','Actual','Delta','Source','Status'].map(h => (
                    <th key={h} className="px-3 py-2.5 text-left text-xs text-slate-400 font-semibold">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {recon.devices.map((d, i) => (
                  <tr key={`${d.serialNumber}-${i}`} className="border-t border-slate-700/30 hover:bg-slate-700/20">
                    <td className="px-3 py-2 font-mono text-slate-300">{d.serialNumber || '—'}</td>
                    <td className="px-3 py-2">
                      <span className="bg-slate-700 text-slate-200 px-1.5 py-0.5 rounded font-mono text-[10px]">{d.ratePlanCode || '—'}</span>
                    </td>
                    <td className="px-3 py-2 text-slate-400 truncate">{d.skuKey || '—'}</td>
                    <td className="px-3 py-2 font-mono text-slate-300">{fmt$(d.expectedPrice)}</td>
                    <td className="px-3 py-2 font-mono text-slate-300">{fmt$(d.actualPrice)}</td>
                    <td className="px-3 py-2 font-mono">
                      {d.delta !== null && d.delta !== undefined
                        ? <span className={d.delta > 0.005 ? 'text-blue-400' : d.delta < -0.005 ? 'text-red-400' : 'text-emerald-400'}>
                            {fmtDelta(d.delta)}
                          </span>
                        : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-500 capitalize">{d.priceSource || '—'}</td>
                    <td className="px-3 py-2"><StatusChip status={d.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* QB Price Overrides */}
      <div className="space-y-2">
        <h4 className="text-sm font-semibold text-slate-300">
          QB Price Overrides
          <span className="ml-2 text-xs font-normal text-slate-500">
            ({overrides?.length ?? 0} SKUs with custom pricing)
          </span>
        </h4>

        {!overrides || overrides.length === 0 ? (
          <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl px-5 py-4 text-sm text-slate-500 italic">
            No customer-specific price overrides found. This customer pays catalog defaults.
          </div>
        ) : (
          <div className="bg-slate-900/60 border border-slate-700/40 rounded-xl overflow-hidden">
            <table className="w-full table-fixed text-sm">
              <colgroup>
                <col style={{ width: '60%' }} />
                <col style={{ width: '40%' }} />
              </colgroup>
              <thead className="bg-slate-800/60">
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs text-slate-400 font-semibold">SKU Key</th>
                  <th className="px-4 py-2.5 text-right text-xs text-slate-400 font-semibold">Custom Price</th>
                </tr>
              </thead>
              <tbody>
                {overrides.sort((a,b) => (a.skuKey||'').localeCompare(b.skuKey||'')).map(o => (
                  <tr key={o.id || o.skuKey} className="border-t border-slate-700/30 hover:bg-slate-700/20">
                    <td className="px-4 py-2.5 font-mono text-xs text-slate-300">{o.skuKey}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-sm text-emerald-400">{fmt$(o.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
//  ROOT COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════
export default function CustomerDetail({ customerId, customerName, onBack }) {
  const [activeTab, setActiveTab] = useState('devices')
  const [customer,  setCustomer]  = useState(null)

  // Attempt to load some basic QB data for the header info section
  useEffect(() => {
    if (!customerId) return
    // We can pull it from customers list if cached — for now just use what we have
    setCustomer({ id: customerId, name: customerName })
  }, [customerId, customerName])

  return (
    <div className="space-y-6">

      {/* Back button + header */}
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-200
            bg-slate-800 border border-slate-700 hover:border-slate-500 px-3 py-2 rounded-lg transition-colors"
        >
          ← Back
        </button>
        <div>
          <h2 className="text-xl font-bold text-white">{customerName || 'Customer Detail'}</h2>
          <p className="text-xs text-slate-500 mt-0.5">ID: {customerId}</p>
        </div>
      </div>

      {/* Tab nav */}
      <div className="flex items-center gap-1 bg-slate-800 border border-slate-700 rounded-xl p-1 w-fit">
        <TabBtn active={activeTab === 'devices'} onClick={() => setActiveTab('devices')}>
          Devices & Contracts
        </TabBtn>
        <TabBtn active={activeTab === 'pricing'} onClick={() => setActiveTab('pricing')}>
          Pricing & Overrides
        </TabBtn>
      </div>

      {/* Tab content */}
      {activeTab === 'devices' && <DevicesTab customerId={customerId} />}
      {activeTab === 'pricing' && <PricingTab customerName={customerName} />}

    </div>
  )
}

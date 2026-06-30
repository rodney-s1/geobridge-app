import React, { useState, useEffect, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

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

// ─── Copy-to-clipboard helper ─────────────────────────────────────────────────
function useCopySerials() {
  const [copied, setCopied] = useState(false)
  const copy = useCallback((serials) => {
    const text = serials.filter(Boolean).join('\n')
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      const el = document.createElement('textarea')
      el.value = text
      document.body.appendChild(el)
      el.select()
      document.execCommand('copy')
      document.body.removeChild(el)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [])
  return [copied, copy]
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
  const [devices,       setDevices]       = useState(null)
  const [loading,       setLoading]       = useState(true)
  const [error,         setError]         = useState(null)
  const [filterCode,    setFilterCode]    = useState(null)
  const [copied,        copy]             = useCopySerials()

  // Inline picker state — tracks which picker is open:
  //   null         → none open
  //   { serial, mode: 'bsd' }  → billing-start-date picker
  //   { serial, mode: 'fcd' }  → first-connect-date picker
  const [activePicker,  setActivePicker]  = useState(null)
  const [dateInput,     setDateInput]     = useState('')
  const [saving,        setSaving]        = useState(false)
  const [saveError,     setSaveError]     = useState(null)

  const loadDevices = useCallback(() => {
    setLoading(true)
    fetch(`${API}/api/customers/${encodeURIComponent(customerId)}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setDevices(d.devices || []))
      .catch(e => setError(`Failed to load devices (${e})`))
      .finally(() => setLoading(false))
  }, [customerId])

  useEffect(() => { loadDevices() }, [loadDevices])

  // Open a date picker for a device row
  // mode: 'bsd' (billing start date) | 'fcd' (first connect date)
  function openPicker(serial, mode) {
    setActivePicker({ serial, mode })
    setDateInput('')
    setSaveError(null)
  }

  function closePicker() {
    setActivePicker(null)
    setSaveError(null)
  }

  // Save override via POST
  async function handleSaveDate() {
    if (!activePicker) return
    const { serial, mode } = activePicker
    if (!dateInput) { setSaveError('Please pick a date'); return }
    setSaving(true)
    setSaveError(null)
    try {
      const url    = mode === 'fcd'
        ? `${API}/api/customers/device/${encodeURIComponent(serial)}/first-connect-date`
        : `${API}/api/customers/device/${encodeURIComponent(serial)}/billing-date`
      const body   = mode === 'fcd'
        ? { firstConnectDate: dateInput }
        : { billingStartDate: dateInput }
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `Server error (${res.status})`)
      }
      closePicker()
      loadDevices()
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  // Clear override via DELETE
  async function handleClearDate(serial, mode) {
    setSaving(true)
    setSaveError(null)
    try {
      const url = mode === 'fcd'
        ? `${API}/api/customers/device/${encodeURIComponent(serial)}/first-connect-date`
        : `${API}/api/customers/device/${encodeURIComponent(serial)}/billing-date`
      const res = await fetch(url, { method: 'DELETE' })
      if (!res.ok) {
        const b = await res.json().catch(() => ({}))
        throw new Error(b.detail || `Server error (${res.status})`)
      }
      closePicker()
      loadDevices()
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

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

  // Rate plan counts
  const rpcCounts = {}
  devices.forEach(d => { const r = d.ratePlanCode || '(none)'; rpcCounts[r] = (rpcCounts[r] || 0) + 1 })

  const allSerials      = devices.map(d => d.serialNumber).filter(Boolean)
  const visibleDevices  = (filterCode
    ? devices.filter(d => (d.ratePlanCode || '(none)') === filterCode)
    : devices
  ).slice().sort((a, b) => {
    // Sort by Billing Start Date descending (newest first); blank dates go last
    const da = a.contractStartDate || ''
    const db = b.contractStartDate || ''
    if (!da && !db) return 0
    if (!da) return 1
    if (!db) return -1
    return db.localeCompare(da)
  })
  const filteredSerials = visibleDevices.map(d => d.serialNumber).filter(Boolean)
  const serialsToCopy   = filterCode ? filteredSerials : allSerials
  const copyLabel       = filterCode
    ? `Copy ${filteredSerials.length} serial${filteredSerials.length !== 1 ? 's' : ''}`
    : `Copy all ${allSerials.length} serial${allSerials.length !== 1 ? 's' : ''}`

  return (
    <div className="space-y-4">
      {/* RPC pill summary — clickable to filter */}
      <div className="flex flex-wrap gap-2 items-center bg-slate-900/60 border border-slate-700/40 rounded-xl px-4 py-3">
        <span className="text-xs text-slate-500 uppercase tracking-wider font-medium mr-1">Rate Plan Breakdown:</span>
        {Object.entries(rpcCounts).sort((a,b) => b[1]-a[1]).map(([code, count]) => {
          const isActive = filterCode === code
          return (
            <button
              key={code}
              onClick={() => setFilterCode(isActive ? null : code)}
              title={isActive ? 'Click to clear filter' : `Show only ${code} devices`}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs transition-all ${
                isActive
                  ? 'bg-blue-600/40 border-blue-500/70 ring-1 ring-blue-500/50 scale-105'
                  : 'bg-slate-700/80 border-slate-600/40 hover:bg-slate-600/60 hover:border-slate-500/60 cursor-pointer'
              }`}
            >
              <span className="font-mono text-slate-200">{code}</span>
              <span className={`inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full font-bold text-xs ${
                isActive ? 'bg-blue-500/50 text-blue-200' : 'bg-blue-500/30 text-blue-300'
              }`}>{count}</span>
            </button>
          )
        })}

        {/* Copy serials button */}
        <button
          onClick={() => copy(serialsToCopy)}
          title={copyLabel}
          className={`ml-1 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs transition-all ${
            copied
              ? 'bg-emerald-800/50 border-emerald-600/50 text-emerald-300'
              : 'bg-slate-800/60 border-slate-600/40 text-slate-400 hover:text-slate-200 hover:border-slate-500/60'
          }`}
        >
          {copied ? (
            <><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg> Copied!</>
          ) : (
            <><svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg> {copyLabel}</>
          )}
        </button>

        <span className="ml-auto text-xs text-slate-600 font-mono">
          {filterCode
            ? <>{filteredSerials.length} of {devices.length} devices <span className="text-blue-400">(filtered)</span></>
            : <>{devices.length} total devices</>}
        </span>
      </div>

      {/* Device table */}
      <div className="bg-slate-900/60 border border-slate-700/40 rounded-xl overflow-hidden">
        <table className="w-full table-fixed text-xs">
          <colgroup>
            <col style={{ width: '12%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '12%' }} />
            <col style={{ width: '9%' }} />
            <col style={{ width: '11%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '14%' }} />
            <col style={{ width: '7%' }} />
            <col style={{ width: '7%' }} />
          </colgroup>
          <thead className="bg-slate-800/60">
            <tr>
              {['Serial', 'Device Type', 'Billing Plan', 'Rate Plan', 'Database', 'First Connect Date', 'Billing Start Date', 'End', ''].map(h => (
                <th key={h} className="px-3 py-2.5 text-left text-xs text-slate-400 font-semibold">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleDevices.map((d, i) => {
              const isActive         = d.status === 'Active'
              const fcdPickerOpen    = activePicker?.serial === d.serialNumber && activePicker?.mode === 'fcd'
              const bsdPickerOpen    = activePicker?.serial === d.serialNumber && activePicker?.mode === 'bsd'
              const anyPickerOpen    = fcdPickerOpen || bsdPickerOpen
              // Show the BSD "Set date" button only when there is no date at all (needsDate)
              const needsDate        = isActive && !d.contractStartDate
              return (
                <React.Fragment key={`${d.serialNumber}-${i}`}>
                  <tr className="border-t border-slate-700/30 hover:bg-slate-700/20">
                    <td className="px-3 py-2 font-mono text-slate-300">{d.serialNumber || '—'}</td>
                    <td className="px-3 py-2 text-slate-400">{d.deviceType || '—'}</td>
                    <td className="px-3 py-2 text-slate-400">{d.activeBillingPlan || '—'}</td>
                    <td className="px-3 py-2">
                      {d.ratePlanCode
                        ? <span className="bg-slate-700 text-slate-200 px-1.5 py-0.5 rounded font-mono">{d.ratePlanCode}</span>
                        : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-3 py-2 text-slate-400 truncate">{d.database || '—'}</td>

                    {/* ── First Connect Date cell ── */}
                    <td className="px-3 py-2 font-mono">
                      {d.firstConnectDate ? (
                        <span className={`flex items-center gap-1 ${d.hasFirstConnectOverride ? 'text-sky-300' : 'text-slate-300'}`}>
                          {d.firstConnectDate}
                          {d.hasFirstConnectOverride && (
                            <span title="Manual override — click ✕ to clear"
                              className="text-sky-400 cursor-pointer hover:text-red-400 ml-0.5 leading-none"
                              onClick={() => handleClearDate(d.serialNumber, 'fcd')}>✕</span>
                          )}
                        </span>
                      ) : (
                        <button
                          onClick={() => fcdPickerOpen ? closePicker() : openPicker(d.serialNumber, 'fcd')}
                          title="Set manual first connect date"
                          className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                            fcdPickerOpen
                              ? 'bg-sky-500/30 text-sky-300 border border-sky-500/50'
                              : 'bg-sky-500/15 text-sky-400 border border-sky-500/30 hover:bg-sky-500/25'
                          }`}
                        >
                          Set FCD
                        </button>
                      )}
                    </td>

                    {/* ── Billing Start Date cell ── */}
                    <td className="px-3 py-2 font-mono">
                      {d.contractStartDate
                        ? <span className={d.hasDateOverride ? 'text-amber-300' : 'text-slate-300'}>
                            {d.contractStartDate}
                            {d.hasDateOverride && (
                              <span title="Manual override — click ✕ to clear"
                                className="text-amber-400 cursor-pointer hover:text-red-400 ml-1 leading-none"
                                onClick={() => handleClearDate(d.serialNumber, 'bsd')}>✕</span>
                            )}
                          </span>
                        : <span className="text-slate-600 italic">not set</span>}
                    </td>

                    <td className="px-3 py-2 text-slate-500 font-mono">{d.contractEndDate || '—'}</td>
                    <td className="px-3 py-2">
                      {needsDate && (
                        <button
                          onClick={() => bsdPickerOpen ? closePicker() : openPicker(d.serialNumber, 'bsd')}
                          title="Set manual billing start date"
                          className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
                            bsdPickerOpen
                              ? 'bg-amber-500/30 text-amber-300 border border-amber-500/50'
                              : 'bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25'
                          }`}
                        >
                          Set BSD
                        </button>
                      )}
                    </td>
                  </tr>

                  {/* Inline date picker row */}
                  {anyPickerOpen && (
                    <tr className={`border-t ${fcdPickerOpen ? 'bg-sky-900/10 border-sky-700/20' : 'bg-amber-900/10 border-amber-700/20'}`}>
                      <td colSpan={9} className="px-4 py-3">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className={`text-xs font-medium ${fcdPickerOpen ? 'text-sky-400' : 'text-amber-400'}`}>
                            {fcdPickerOpen
                              ? `Manual first connect date for ${activePicker.serial}:`
                              : `Manual billing start date for ${activePicker.serial}:`}
                          </span>
                          <input
                            type="date"
                            value={dateInput}
                            onChange={e => setDateInput(e.target.value)}
                            className={`px-2 py-1 bg-slate-800 border text-slate-200 rounded text-xs
                                       focus:outline-none ${fcdPickerOpen
                                         ? 'border-slate-600 focus:border-sky-500'
                                         : 'border-slate-600 focus:border-amber-500'}`}
                          />
                          <button
                            onClick={handleSaveDate}
                            disabled={saving || !dateInput}
                            className={`px-3 py-1 disabled:opacity-50 text-white rounded text-xs font-medium transition-colors ${
                              fcdPickerOpen
                                ? 'bg-sky-600 hover:bg-sky-500'
                                : 'bg-amber-600 hover:bg-amber-500'
                            }`}
                          >
                            {saving ? 'Saving…' : 'Save'}
                          </button>
                          <button
                            onClick={closePicker}
                            className="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-xs transition-colors"
                          >
                            Cancel
                          </button>
                          {saveError && (
                            <span className="text-xs text-red-400">{saveError}</span>
                          )}
                          <span className="ml-auto text-xs text-slate-500 italic">
                            {fcdPickerOpen
                              ? 'FCD override: invoice proration uses this as First Connect Date (priority over MyAdmin).'
                              : 'BSD override: takes priority over MyAdmin billing start date.'}
                          </span>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
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

const VALID_BILLING_FREQUENCIES_DETAIL = ['Annual', 'Semi-Annual', 'Quarterly']

function BillingFrequencySection({ customerId }) {
  const [freq,    setFreq]    = useState(null)   // null = loading
  const [editing, setEditing] = useState(false)
  const [selected, setSelected] = useState('')
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    fetch(`${API}/api/customers/${encodeURIComponent(customerId)}/billing-frequency`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const f = d?.billingFrequency || ''
        setFreq(f)
        setSelected(f)
      })
      .catch(() => setFreq(''))
  }, [customerId])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      if (selected) {
        const res = await fetch(`${API}/api/customers/${encodeURIComponent(customerId)}/billing-frequency`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ billingFrequency: selected }),
        })
        if (!res.ok) throw new Error(`Server error (${res.status})`)
        setFreq(selected)
      } else {
        const res = await fetch(`${API}/api/customers/${encodeURIComponent(customerId)}/billing-frequency`, { method: 'DELETE' })
        if (!res.ok) throw new Error(`Server error (${res.status})`)
        setFreq('')
      }
      setEditing(false)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (freq === null) return null   // still loading — don't flash

  if (editing) {
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-slate-500">Billing frequency:</span>
        <select
          value={selected}
          onChange={e => setSelected(e.target.value)}
          className="bg-slate-700 text-slate-200 text-xs rounded px-2 py-1 border border-teal-700/60 focus:outline-none focus:border-teal-500"
        >
          <option value="">— None —</option>
          {VALID_BILLING_FREQUENCIES_DETAIL.map(f => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
        <button
          onClick={save}
          disabled={saving}
          className="px-2 py-1 bg-teal-700 hover:bg-teal-600 text-white text-xs rounded disabled:opacity-50"
        >
          {saving ? '…' : '✓ Save'}
        </button>
        <button
          onClick={() => { setEditing(false); setSelected(freq); setError(null) }}
          className="px-2 py-1 bg-slate-600 hover:bg-slate-500 text-white text-xs rounded"
        >
          Cancel
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-2 group cursor-pointer"
      onClick={() => setEditing(true)}
      title={freq ? `Billing frequency: ${freq} — click to change` : 'Click to set billing frequency (Annual / Semi-Annual / Quarterly)'}
    >
      {freq ? (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border bg-teal-900/50 text-teal-300 border-teal-700/40">
          ↻ {freq}
        </span>
      ) : (
        <span className="text-xs text-slate-600 group-hover:text-teal-500 italic transition-colors">+ set billing frequency</span>
      )}
      <svg className="w-3 h-3 text-slate-600 group-hover:text-teal-500 transition-colors opacity-0 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
      </svg>
    </div>
  )
}

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
          <div className="mt-1.5">
            <BillingFrequencySection customerId={customerId} />
          </div>
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

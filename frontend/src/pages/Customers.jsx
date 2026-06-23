import React, { useState, useEffect, useCallback, useRef } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Billing type badge colours ───────────────────────────────────────────────
const BILLING_COLORS = {
  'Standard':               'bg-blue-500/20 text-blue-300 border border-blue-500/30',
  'CUA':                    'bg-purple-500/20 text-purple-300 border border-purple-500/30',
  'Sourcewell':             'bg-green-500/20 text-green-300 border border-green-500/30',
  'Hanover':                'bg-orange-500/20 text-orange-300 border border-orange-500/30',
  'Han-CS':                 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/30',
  'Charge Upon Activation': 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30',
  'Check Before Sending':   'bg-red-500/20 text-red-300 border border-red-500/30',
  'Reseller':               'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30',
  'In Collections':         'bg-red-700/30 text-red-300 border border-red-700/40',
  'Terminated':             'bg-gray-500/20 text-gray-400 border border-gray-500/30',
  'Unknown':                'bg-gray-700/30 text-gray-500 border border-gray-600/30',
  'Unassigned':             'bg-gray-700/30 text-gray-500 border border-gray-600/30',
}

const VALID_BILLING_TYPES = [
  'Standard','CUA','Sourcewell','Hanover','Han-CS',
  'Charge Upon Activation','Check Before Sending',
  'Reseller','In Collections','Terminated','Unknown',
]

function BillingBadge({ type }) {
  const cls = BILLING_COLORS[type] || BILLING_COLORS['Unknown']
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {type || 'Unknown'}
    </span>
  )
}

// ─── Sub-account grouping helpers ─────────────────────────────────────────────
// ── Han-CS-aware name helpers ────────────────────────────────────────────────
// MyAdmin naming convention:
//   "ACES Controls LLC {Han-CS}"            → parent name (Han-CS is identity)
//   "ACES Controls LLC {Han-CS} {Cameras}"  → sub of "ACES Controls LLC {Han-CS}"
//   "City of Raleigh {Cameras}"             → sub of "City of Raleigh"
//   "Acme Corp"                             → standalone parent
//
// Rule: if the FIRST {token} is "han-cs" (case-insensitive), it belongs to
// the parent name.  A SECOND {token} (if present) is the sub-account label.
// For all other first tokens, strip from the first { onward (original behaviour).

function _firstToken(name) {
  const open  = name.indexOf('{')
  if (open === -1) return null
  const close = name.indexOf('}', open)
  if (close === -1) return null
  return name.slice(open + 1, close).trim()
}

// Returns the canonical parent name.
// "ACES Controls LLC {Han-CS}"           → "ACES Controls LLC {Han-CS}"
// "ACES Controls LLC {Han-CS} {Cameras}" → "ACES Controls LLC {Han-CS}"
// "City of Raleigh {Cameras}"            → "City of Raleigh"
// "Acme Corp"                            → "Acme Corp"
function getParentName(name) {
  const open = name.indexOf('{')
  if (open === -1) return name
  const token = _firstToken(name)
  if (token && token.toLowerCase() === 'han-cs') {
    // {Han-CS} is part of the parent — include it, stop there
    const close = name.indexOf('}', open)
    return name.slice(0, close + 1).trimEnd()
  }
  // Ordinary sub-account suffix — strip from first {
  return name.slice(0, open).trimEnd()
}

// Returns true if this name is a sub-account entry (should be indented under parent).
function isSubAccount(name) {
  const token = _firstToken(name)
  if (!token) return false
  if (token.toLowerCase() === 'han-cs') {
    // Only a sub-account if there is a SECOND {token} after {Han-CS}
    const close = name.indexOf('}', name.indexOf('{'))
    return name.indexOf('{', close + 1) !== -1
  }
  return true
}

// Returns the sub-account label: the part inside the RELEVANT {…}.
// For Han-CS names the label is the SECOND brace token (first is identity).
// "ACES Controls LLC {Han-CS} {Cameras}" → "Cameras"
// "City of Raleigh {Cameras}"            → "Cameras"
function getSubLabel(name) {
  const token = _firstToken(name)
  if (token && token.toLowerCase() === 'han-cs') {
    // Skip the first {Han-CS} token and grab the second
    const firstClose = name.indexOf('}', name.indexOf('{'))
    const secondOpen  = name.indexOf('{', firstClose + 1)
    if (secondOpen === -1) return ''
    const secondClose = name.indexOf('}', secondOpen)
    return secondClose !== -1 ? name.slice(secondOpen + 1, secondClose).trim() : ''
  }
  const m = name.match(/\{([^}]+)\}/)
  return m ? m[1] : ''
}

// Groups a flat customer list into parent groups.
// Returns an array of:
//   { parentName, parent: customer|null, subs: customer[], combinedDeviceCount }
// - parent is the account whose name exactly matches the parentName (no {…})
// - subs are accounts whose stripped name matches parentName
// - If every account in a group is a sub (no plain parent exists), parent is null
// Standalone accounts (no subs and no {}) appear as { parent: customer, subs: [] }
function groupCustomers(customers) {
  const groups = {}     // key (lowercase) → { parentName (display), parent, subs }
  const keyToDisplay = {}  // tracks the first-seen display name for each key

  customers.forEach(c => {
    const rawParent = getParentName(c.name)
    const key = rawParent.toLowerCase()  // case-insensitive grouping key

    if (!groups[key]) {
      groups[key] = { parentName: rawParent, parent: null, subs: [] }
      keyToDisplay[key] = rawParent
    }
    if (isSubAccount(c.name)) {
      groups[key].subs.push(c)
    } else {
      // Prefer the non-sub account's own name as the display name
      groups[key].parentName = rawParent
      groups[key].parent = c
    }
  })

  // Sort groups by parentName (case-insensitive), subs within each group by their sub label
  return Object.values(groups)
    .sort((a, b) => a.parentName.toLowerCase().localeCompare(b.parentName.toLowerCase()))
    .map(g => {
      g.subs.sort((a, b) => a.name.localeCompare(b.name))
      // Combined device count across parent + all subs
      g.combinedDeviceCount =
        (g.parent?.deviceCount || 0) + g.subs.reduce((s, c) => s + (c.deviceCount || 0), 0)
      return g
    })
}

// ─── Copy-to-clipboard helper ────────────────────────────────────────────────
function useCopySerials() {
  const [copied, setCopied] = useState(false)
  const copy = useCallback((serials) => {
    const text = serials.filter(Boolean).join('\n')
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {
      // fallback for Electron/non-secure contexts
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

// ─── Rate Plan Breakdown pill row ─────────────────────────────────────────────
function RpcBreakdownRow({ rpcCounts, totalDevices, colSpan = 8, indent = 'pl-14',
                           activeFilter = null, onFilterChange = null,
                           filteredSerials = [], allSerials = [] }) {
  const entries = Object.entries(rpcCounts).sort((a, b) => b[1] - a[1])
  if (entries.length === 0) return null

  const [copied, copy] = useCopySerials()
  const serialsToCopy  = activeFilter ? filteredSerials : allSerials
  const copyLabel      = activeFilter
    ? `Copy ${filteredSerials.length} serial${filteredSerials.length !== 1 ? 's' : ''}`
    : `Copy all ${allSerials.length} serial${allSerials.length !== 1 ? 's' : ''}`

  return (
    <tr className="bg-slate-900/80 border-b border-white/10">
      <td colSpan={colSpan} className={`${indent} pr-4 py-2.5`}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-500 uppercase tracking-wider font-medium mr-1">
            Rate Plan Breakdown:
          </span>
          {entries.map(([code, count]) => {
            const isActive = activeFilter === code
            return (
              <button
                key={code}
                onClick={e => {
                  e.stopPropagation()
                  onFilterChange && onFilterChange(isActive ? null : code)
                }}
                title={onFilterChange ? (isActive ? 'Click to clear filter' : `Click to filter to ${code} devices`) : undefined}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs transition-all ${
                  isActive
                    ? 'bg-blue-600/40 border-blue-500/70 ring-1 ring-blue-500/50 scale-105'
                    : onFilterChange
                      ? 'bg-slate-700/60 border-slate-600/50 hover:bg-slate-600/60 hover:border-slate-500/60 cursor-pointer'
                      : 'bg-slate-700/60 border-slate-600/50'
                }`}
              >
                <span className="font-mono text-slate-200">{code}</span>
                <span className={`inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 rounded-full font-bold text-xs ${
                  isActive ? 'bg-blue-500/50 text-blue-200' : 'bg-blue-500/25 text-blue-300'
                }`}>
                  {count}
                </span>
              </button>
            )
          })}

          {/* Copy serials button */}
          {allSerials.length > 0 && (
            <button
              onClick={e => { e.stopPropagation(); copy(serialsToCopy) }}
              title={copyLabel}
              className={`ml-1 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs transition-all ${
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
          )}

          <span className="ml-auto text-xs text-slate-600 font-mono">
            {activeFilter
              ? <>{filteredSerials.length} of {totalDevices} device{totalDevices !== 1 ? 's' : ''} <span className="text-blue-400">(filtered)</span></>
              : <>{totalDevices} total device{totalDevices !== 1 ? 's' : ''}</>
            }
          </span>
        </div>
      </td>
    </tr>
  )
}

// ─── Device row inside expanded customer ─────────────────────────────────────
function DeviceRow({ device }) {
  return (
    <tr className="border-b border-white/5 hover:bg-white/5 transition-colors">
      <td className="pl-14 pr-4 py-2.5">
        <span className="font-mono text-xs text-slate-300">{device.serialNumber || '—'}</span>
      </td>
      <td className="px-4 py-2.5 text-xs text-slate-400">{device.deviceType || '—'}</td>
      <td className="px-4 py-2.5 text-xs text-slate-400">{device.activeBillingPlan || '—'}</td>
      <td className="px-4 py-2.5 text-xs text-slate-400 font-mono">{device.ratePlanCode || '—'}</td>
      <td className="px-4 py-2.5 text-xs text-slate-400">{device.database || '—'}</td>
      <td className="px-4 py-2.5 text-xs">
        <span className={`px-2 py-0.5 rounded text-xs ${
          device.status === 'Active'
            ? 'bg-green-500/15 text-green-400'
            : 'bg-gray-500/15 text-gray-400'
        }`}>
          {device.status || 'Active'}
        </span>
      </td>
      <td className="px-4 py-2.5 text-xs text-slate-500">{device.contractStartDate || '—'}</td>
      <td className="px-4 py-2.5 text-xs text-slate-500">{device.contractEndDate || '—'}</td>
    </tr>
  )
}

// ─── Shared billing-type editor (used by CustomerRow and SubAccountRow) ───────
function BillingTypeEditor({ customer, onBillingTypeChange, stopPropagation = true }) {
  const [editingBilling, setEditingBilling] = useState(false)
  const [selectedType, setSelectedType] = useState(customer.billingType)
  const [savingType, setSavingType] = useState(false)

  const saveBillingType = async () => {
    setSavingType(true)
    try {
      const res = await fetch(`${API}/api/customers/${customer.id}/billing-type`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ billing_type: selectedType }),
      })
      if (res.ok) {
        onBillingTypeChange(customer.id, selectedType)
        setEditingBilling(false)
      }
    } catch (e) {
      console.error('Failed to save billing type:', e)
    } finally {
      setSavingType(false)
    }
  }

  // sp = handler that also calls stopPropagation when needed
  const sp = (fn) => stopPropagation
    ? (e) => { e.stopPropagation(); fn() }
    : () => fn()

  if (editingBilling) {
    return (
      <div className="flex items-center gap-1">
        <select
          value={selectedType}
          onChange={e => setSelectedType(e.target.value)}
          className="bg-slate-700 text-slate-200 text-xs rounded px-2 py-1 border border-slate-600 focus:outline-none focus:border-blue-500"
          onClick={stopPropagation ? e => e.stopPropagation() : undefined}
        >
          {VALID_BILLING_TYPES.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
        <button
          onClick={sp(saveBillingType)}
          disabled={savingType}
          className="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded disabled:opacity-50"
        >
          {savingType ? '...' : '✓'}
        </button>
        <button
          onClick={sp(() => { setEditingBilling(false); setSelectedType(customer.billingType) })}
          className="px-2 py-1 bg-slate-600 hover:bg-slate-500 text-white text-xs rounded"
        >
          ✕
        </button>
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-2 group cursor-pointer"
      onClick={stopPropagation ? e => { e.stopPropagation(); setEditingBilling(true) } : () => setEditingBilling(true)}
      title="Click to change billing type"
    >
      <BillingBadge type={customer.billingType} />
      <svg className="w-3 h-3 text-slate-600 group-hover:text-slate-400 transition-colors opacity-0 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
      </svg>
    </div>
  )
}

// ─── Inline device sub-table (used inside both CustomerRow and SubAccountRow) ──
function DeviceSubTable({ devices }) {
  const [filterCode, setFilterCode] = useState(null)

  if (devices.length === 0) {
    return (
      <tr className="border-b border-white/5 bg-slate-900/50">
        <td colSpan={8} className="pl-14 pr-4 py-4 text-xs text-slate-500 italic">
          No device contracts found for this customer.
        </td>
      </tr>
    )
  }

  const rpcCounts = {}
  devices.forEach(d => {
    const code = d.ratePlanCode || '(none)'
    rpcCounts[code] = (rpcCounts[code] || 0) + 1
  })

  const allSerials      = devices.map(d => d.serialNumber).filter(Boolean)
  const visibleDevices  = filterCode
    ? devices.filter(d => (d.ratePlanCode || '(none)') === filterCode)
    : devices
  const filteredSerials = visibleDevices.map(d => d.serialNumber).filter(Boolean)

  return (
    <>
      <RpcBreakdownRow
        rpcCounts={rpcCounts}
        totalDevices={devices.length}
        activeFilter={filterCode}
        onFilterChange={setFilterCode}
        filteredSerials={filteredSerials}
        allSerials={allSerials}
      />
      <tr className="bg-slate-900/70">
        <td colSpan={8} className="px-0 py-0">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10">
                <th className="pl-14 pr-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Serial Number</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Device Type</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Active Billing Plan</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Rate Plan Code</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Database</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Status</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">Start Date</th>
                <th className="px-4 py-2 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">End Date</th>
              </tr>
            </thead>
            <tbody>
              {visibleDevices.map((d, i) => (
                <DeviceRow key={`${d.serialNumber}-${i}`} device={d} />
              ))}
            </tbody>
          </table>
        </td>
      </tr>
    </>
  )
}

// ─── Sub-account row (indented, inside an expanded parent group) ──────────────
// devices prop is pre-loaded by the parent; expandable to show the device table.
function SubAccountRow({ customer, devices, loadingDevices, onBillingTypeChange, onDetail }) {
  const [expanded, setExpanded] = useState(false)
  const subLabel = getSubLabel(customer.name)

  return (
    <>
      {/* Sub-account row */}
      <tr
        className={`border-b border-white/5 hover:bg-white/[0.04] transition-colors cursor-pointer ${
          expanded ? 'bg-white/[0.03]' : ''
        }`}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Indent + expand toggle */}
        <td className="w-10 pl-4 py-2.5">
          <div className="flex items-center">
            {/* Tree connector line */}
            <div className="w-4 flex-shrink-0 flex flex-col items-center mr-1">
              <div className="w-px h-2 bg-slate-600/50" />
              <div className="w-3 h-px bg-slate-600/50 self-end" />
            </div>
            <button
              onClick={e => { e.stopPropagation(); setExpanded(ex => !ex) }}
              className="text-slate-600 hover:text-slate-400 transition-colors"
            >
              {loadingDevices ? (
                <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
              ) : expanded ? (
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              )}
            </button>
          </div>
        </td>

        {/* Sub-account name — show label + full name dimmed */}
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2 pl-4">
            <span className="text-slate-500 text-xs select-none">└</span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-slate-300">
                  {subLabel || customer.name}
                </span>
                {customer.hasQbData && (
                  <span className="text-xs text-green-500" title="QB data loaded">QB</span>
                )}
                <span className="text-xs text-slate-600 font-mono">{customer.name}</span>
              </div>
              {customer.accountNo && (
                <div className="text-xs text-slate-600 mt-0.5">Acct #{customer.accountNo}</div>
              )}
            </div>
          </div>
        </td>

        {/* Billing type */}
        <td className="px-4 py-2.5" onClick={e => e.stopPropagation()}>
          <BillingTypeEditor customer={customer} onBillingTypeChange={onBillingTypeChange} />
        </td>

        {/* Primary database */}
        <td className="px-4 py-2.5 text-sm text-slate-500">{customer.primaryDatabase || '—'}</td>

        {/* Device count */}
        <td className="px-4 py-2.5">
          <span className="inline-flex items-center justify-center min-w-[2rem] h-6 px-1.5 bg-slate-700/60 rounded text-xs font-mono text-slate-400">
            {expanded && devices ? devices.length : customer.deviceCount || '—'}
          </span>
        </td>

        {/* Terms */}
        <td className="px-4 py-2.5 text-xs text-slate-600">{customer.terms || '—'}</td>

        {/* Balance */}
        <td className="px-4 py-2.5 text-xs font-mono">
          {customer.balance > 0 ? (
            <span className="text-amber-400/80">${Number(customer.balance).toFixed(2)}</span>
          ) : (
            <span className="text-slate-700">$0.00</span>
          )}
        </td>

        {/* Actions */}
        <td className="px-4 py-2.5">
          <button
            onClick={e => { e.stopPropagation(); onDetail && onDetail(customer.id, customer.name) }}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            Detail →
          </button>
        </td>
      </tr>

      {/* Expanded device table for this sub-account */}
      {expanded && devices && (
        <DeviceSubTable devices={devices} />
      )}
    </>
  )
}

// ─── Parent group row (with optional sub-accounts) ───────────────────────────
// When expanded, fetches all sub-accounts' devices in parallel,
// then shows a combined RPC breakdown + indented sub-account rows.
function ParentGroupRow({ group, onBillingTypeChange, onDetail }) {
  const { parentName, parent, subs, combinedDeviceCount } = group
  const hasSubs = subs.length > 0

  // Expanded = subs panel open (for groups with subs)
  const [subsExpanded, setSubsExpanded] = useState(false)
  // Per-customer device lists, keyed by customer.id
  const [devicesByCustomer, setDevicesByCustomer] = useState({})
  const [loadingDevices, setLoadingDevices] = useState(false)

  // Parent row's own device expansion (for the parent account's own devices, if it has any)
  const [parentExpanded, setParentExpanded] = useState(false)
  const [parentDevices, setParentDevices] = useState([])
  const [loadingParentDevices, setLoadingParentDevices] = useState(false)

  // Combined RPC filter for the cross-account breakdown bar
  const [combinedFilterCode, setCombinedFilterCode] = useState(null)

  // Fetch devices for all sub-accounts in parallel
  const fetchAllSubDevices = async () => {
    setLoadingDevices(true)
    const results = {}
    await Promise.all(
      subs.map(async (sub) => {
        if (devicesByCustomer[sub.id]) {
          results[sub.id] = devicesByCustomer[sub.id]
          return
        }
        try {
          const res = await fetch(`${API}/api/customers/${sub.id}`)
          if (res.ok) {
            const data = await res.json()
            results[sub.id] = data.devices || []
          } else {
            results[sub.id] = []
          }
        } catch {
          results[sub.id] = []
        }
      })
    )
    setDevicesByCustomer(prev => ({ ...prev, ...results }))
    setLoadingDevices(false)
  }

  // Fetch parent's own devices
  const fetchParentDevices = async () => {
    if (!parent) return
    setLoadingParentDevices(true)
    try {
      const res = await fetch(`${API}/api/customers/${parent.id}`)
      if (res.ok) {
        const data = await res.json()
        setParentDevices(data.devices || [])
      }
    } catch (e) {
      console.error('Failed to load parent devices:', e)
    } finally {
      setLoadingParentDevices(false)
    }
  }

  const toggleSubs = async () => {
    if (!subsExpanded && hasSubs) {
      await fetchAllSubDevices()
    }
    setSubsExpanded(e => !e)
  }

  const toggleParentDevices = async () => {
    if (!parentExpanded && parentDevices.length === 0 && parent) {
      await fetchParentDevices()
    }
    setParentExpanded(e => !e)
  }

  // Combined RPC breakdown across all loaded sub devices (+ parent devices if loaded)
  const combinedRpcCounts = {}
  if (subsExpanded) {
    subs.forEach(sub => {
      const devs = devicesByCustomer[sub.id] || []
      devs.forEach(d => {
        const code = d.ratePlanCode || '(none)'
        combinedRpcCounts[code] = (combinedRpcCounts[code] || 0) + 1
      })
    })
  }
  if (parentExpanded) {
    parentDevices.forEach(d => {
      const code = d.ratePlanCode || '(none)'
      combinedRpcCounts[code] = (combinedRpcCounts[code] || 0) + 1
    })
  }
  const hasCombinedRpc = Object.keys(combinedRpcCounts).length > 0

  // Total loaded devices (for combined RPC row denominator)
  const totalLoadedDevices = Object.values(devicesByCustomer).reduce((s, a) => s + a.length, 0)
    + (parentExpanded ? parentDevices.length : 0)

  // All serials across loaded devices (for copy button in combined RPC row)
  const allCombinedDevices = [
    ...Object.values(devicesByCustomer).flat(),
    ...(parentExpanded ? parentDevices : []),
  ]
  const allCombinedSerials      = allCombinedDevices.map(d => d.serialNumber).filter(Boolean)
  const filteredCombinedSerials = combinedFilterCode
    ? allCombinedDevices.filter(d => (d.ratePlanCode || '(none)') === combinedFilterCode)
        .map(d => d.serialNumber).filter(Boolean)
    : allCombinedSerials

  // ── If no subs, render as a plain CustomerRow ──────────────────────────────
  if (!hasSubs) {
    return (
      <CustomerRow
        customer={parent}
        onBillingTypeChange={onBillingTypeChange}
      />
    )
  }

  // ── Group row (parent has subs) ────────────────────────────────────────────
  return (
    <>
      {/* ── Parent account header row ── */}
      <tr
        className={`border-b border-white/5 hover:bg-white/5 transition-colors cursor-pointer ${
          subsExpanded ? 'bg-indigo-950/30' : ''
        }`}
        onClick={hasSubs ? toggleSubs : undefined}
      >
        {/* Sub-accounts expand toggle */}
        <td className="w-10 pl-4 py-3">
          {hasSubs && (
            <button
              onClick={e => { e.stopPropagation(); toggleSubs() }}
              className="text-indigo-400 hover:text-indigo-300 transition-colors"
              title={`${subsExpanded ? 'Collapse' : 'Expand'} sub-accounts`}
            >
              {loadingDevices ? (
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
              ) : subsExpanded ? (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              )}
            </button>
          )}
        </td>

        {/* Parent name + sub-account count badge */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Parent name */}
            {parent ? (
              <span className="text-sm font-semibold text-slate-100">{parent.name}</span>
            ) : (
              <span className="text-sm font-semibold text-slate-300 italic">{parentName}</span>
            )}
            {parent?.hasQbData && (
              <span className="text-xs text-green-500" title="QB data loaded">QB</span>
            )}
            {/* Sub-account count pill */}
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-500/15 border border-indigo-500/25 text-indigo-300 text-xs font-medium"
              title={`${subs.length} sub-account${subs.length !== 1 ? 's' : ''}: ${subs.map(s => getSubLabel(s.name)).join(', ')}`}
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {subs.length} sub
            </span>
            {parent?.accountNo && (
              <span className="text-xs text-slate-600 font-mono">#{parent.accountNo}</span>
            )}
          </div>
          {/* Combined RPC pill badges — visible once any devices are loaded */}
          {hasCombinedRpc && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-slate-600 mr-0.5">Combined RPC:</span>
              {Object.entries(combinedRpcCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([code, count]) => (
                  <span
                    key={code}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-700/80 border border-slate-600/40 text-xs"
                  >
                    <span className="font-mono text-slate-200">{code}</span>
                    <span className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 rounded-full bg-blue-500/25 text-blue-300 font-bold text-xs">
                      {count}
                    </span>
                  </span>
                ))
              }
              <span className="text-xs text-slate-600 font-mono ml-1">
                ({totalLoadedDevices} devices loaded)
              </span>
            </div>
          )}
          {/* Hint when collapsed */}
          {!subsExpanded && !hasCombinedRpc && hasSubs && (
            <div className="text-xs text-slate-600 mt-0.5">
              {combinedDeviceCount} total devices across {subs.length + (parent ? 1 : 0)} accounts · expand to see RPC breakdown
            </div>
          )}
        </td>

        {/* Billing type (parent's own, if it exists) */}
        <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
          {parent ? (
            <BillingTypeEditor customer={parent} onBillingTypeChange={onBillingTypeChange} />
          ) : (
            <span className="text-xs text-slate-600">—</span>
          )}
        </td>

        {/* Primary database */}
        <td className="px-4 py-3 text-sm text-slate-400">
          {parent?.primaryDatabase || '—'}
        </td>

        {/* Combined device count */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-1">
            <span className="inline-flex items-center justify-center min-w-[2rem] h-6 px-1.5 bg-indigo-600/20 border border-indigo-500/20 rounded text-xs font-mono text-indigo-300">
              {combinedDeviceCount}
            </span>
            {hasSubs && (
              <span className="text-xs text-slate-600">combined</span>
            )}
          </div>
        </td>

        {/* Terms */}
        <td className="px-4 py-3 text-xs text-slate-500">{parent?.terms || '—'}</td>

        {/* Balance (parent only) */}
        <td className="px-4 py-3 text-xs font-mono">
          {parent?.balance > 0 ? (
            <span className="text-amber-400">${Number(parent.balance).toFixed(2)}</span>
          ) : (
            <span className="text-slate-600">$0.00</span>
          )}
        </td>

        {/* Actions */}
        <td className="px-4 py-3">
          <button
            onClick={e => { e.stopPropagation(); onDetail && onDetail(parent?.id, parentName) }}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            Detail →
          </button>
        </td>
      </tr>

      {/* ── Expanded: combined RPC breakdown header (when both subs + parent loaded) ── */}
      {subsExpanded && hasCombinedRpc && (parent === null || parentExpanded) && (
        <RpcBreakdownRow
          rpcCounts={combinedRpcCounts}
          totalDevices={totalLoadedDevices}
          indent="pl-8"
          activeFilter={combinedFilterCode}
          onFilterChange={setCombinedFilterCode}
          filteredSerials={filteredCombinedSerials}
          allSerials={allCombinedSerials}
        />
      )}

      {/* ── Expanded: parent's own device rows (if parent has its own account) ── */}
      {subsExpanded && parent && (
        <>
          {/* Parent own-devices expand bar */}
          <tr
            className={`border-b border-white/5 hover:bg-white/[0.04] transition-colors cursor-pointer bg-slate-900/30 ${
              parentExpanded ? 'bg-white/[0.02]' : ''
            }`}
            onClick={toggleParentDevices}
          >
            <td className="w-10 pl-4 py-2">
              <button
                onClick={e => { e.stopPropagation(); toggleParentDevices() }}
                className="text-slate-500 hover:text-slate-300 transition-colors"
              >
                {loadingParentDevices ? (
                  <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                ) : parentExpanded ? (
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                ) : (
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                )}
              </button>
            </td>
            <td colSpan={7} className="px-4 py-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-400 font-medium">{parent.name}</span>
                <span className="text-xs text-slate-600">(primary account · {parent.deviceCount || 0} devices)</span>
              </div>
            </td>
          </tr>
          {parentExpanded && (
            <DeviceSubTable devices={parentDevices} />
          )}
        </>
      )}

      {/* ── Expanded: sub-account rows ── */}
      {subsExpanded && subs.map(sub => (
        <SubAccountRow
          key={sub.id}
          customer={sub}
          devices={devicesByCustomer[sub.id] || null}
          loadingDevices={loadingDevices && !devicesByCustomer[sub.id]}
          onBillingTypeChange={onBillingTypeChange}
          onDetail={onDetail}
        />
      ))}
    </>
  )
}

// ─── Single expandable customer row (standalone, no sub-account grouping) ─────
function CustomerRow({ customer, onBillingTypeChange }) {
  const [expanded, setExpanded] = useState(false)
  const [devices, setDevices] = useState([])
  const [loadingDevices, setLoadingDevices] = useState(false)

  const toggleExpand = async () => {
    if (!expanded && devices.length === 0) {
      setLoadingDevices(true)
      try {
        const res = await fetch(`${API}/api/customers/${customer.id}`)
        if (res.ok) {
          const data = await res.json()
          setDevices(data.devices || [])
        }
      } catch (e) {
        console.error('Failed to load devices:', e)
      } finally {
        setLoadingDevices(false)
      }
    }
    setExpanded(!expanded)
  }

  return (
    <>
      {/* Main customer row */}
      <tr
        className={`border-b border-white/5 hover:bg-white/5 transition-colors cursor-pointer ${
          expanded ? 'bg-white/5' : ''
        }`}
        onClick={toggleExpand}
      >
        {/* Expand toggle */}
        <td className="w-10 pl-4 py-3">
          <button
            onClick={e => { e.stopPropagation(); toggleExpand() }}
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            {loadingDevices ? (
              <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            ) : expanded ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            )}
          </button>
        </td>

        {/* Customer name */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-200">{customer.name}</span>
            {customer.hasQbData && (
              <span className="text-xs text-green-500" title="QB data loaded">QB</span>
            )}
          </div>
          {customer.accountNo && (
            <div className="text-xs text-slate-500 mt-0.5">Acct #{customer.accountNo}</div>
          )}
        </td>

        {/* Billing type (editable) */}
        <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
          <BillingTypeEditor customer={customer} onBillingTypeChange={onBillingTypeChange} />
        </td>

        {/* Primary database */}
        <td className="px-4 py-3 text-sm text-slate-400">{customer.primaryDatabase || '—'}</td>

        {/* Device count */}
        <td className="px-4 py-3">
          <span className="inline-flex items-center justify-center min-w-[2rem] h-6 px-1.5 bg-slate-700 rounded text-xs font-mono text-slate-300">
            {expanded ? devices.length : customer.deviceCount || '—'}
          </span>
        </td>

        {/* Terms */}
        <td className="px-4 py-3 text-xs text-slate-500">{customer.terms || '—'}</td>

        {/* Balance */}
        <td className="px-4 py-3 text-xs font-mono">
          {customer.balance > 0 ? (
            <span className="text-amber-400">${Number(customer.balance).toFixed(2)}</span>
          ) : (
            <span className="text-slate-600">$0.00</span>
          )}
        </td>

        {/* Actions */}
        <td className="px-4 py-3">
          <button
            onClick={e => { e.stopPropagation(); onDetail && onDetail(customer.id, customer.name) }}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            Detail →
          </button>
        </td>
      </tr>

      {/* Expanded device sub-table */}
      {expanded && (
        <DeviceSubTable devices={devices} />
      )}
    </>
  )
}

// ─── Main Customers page ──────────────────────────────────────────────────────
export default function Customers({ onDetail }) {
  const [customers, setCustomers] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingStart, setLoadingStart] = useState(null)
  const [fromCache, setFromCache] = useState(false)
  const [cacheAgeHours, setCacheAgeHours] = useState(null)
  const [isForcingRefresh, setIsForcingRefresh] = useState(false)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [search, setSearch] = useState('')
  const [billingFilter, setBillingFilter] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const debounceRef = useRef(null)
  const [importingQb, setImportingQb] = useState(false)
  const [importMsg, setImportMsg] = useState('')
  const [qbSummary, setQbSummary] = useState(null)
  const [syncProgress, setSyncProgress] = useState(null)   // SSE progress data
  const sseRef = useRef(null)   // holds the EventSource so we can close it
  const forceSyncingRef = useRef(false)   // true while a force-refresh sync is in progress
  const sseCompletedRef = useRef(false)   // true once SSE received done/error (normal close)
  const PAGE_SIZE = 50

  // ── SSE progress connection ────────────────────────────────────────────────
  const startProgressSSE = useCallback(() => {
    // Close any existing connection first
    if (sseRef.current) {
      sseRef.current.close()
      sseRef.current = null
    }
    setSyncProgress({ active: true, step: 'step1', step_label: 'Connecting…', pct: 0, message: 'Starting sync…' })
    sseCompletedRef.current = false

    const es = new EventSource(`${API}/api/customers/sync-progress`)
    sseRef.current = es

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        console.log('[SSE] message:', data.step, 'active:', data.active, 'pct:', data.pct)
        setSyncProgress(data)
        // Mark done so onerror knows this was a clean server-side close
        if (!data.active && (data.step === 'done' || data.step === 'error')) {
          console.log('[SSE] received done/error — marking complete, stream will close')
          sseCompletedRef.current = true
          // DON'T clear loading/isForcingRefresh here.
          // fetchCustomers() is still awaiting the HTTP response and will
          // clean up in its own finally block once the response arrives.
        }
      } catch (_) {}
    }
    es.onerror = () => {
      console.log('[SSE] onerror fired — sseCompletedRef:', sseCompletedRef.current)
      // onerror fires on both unexpected errors AND normal server-side close.
      // Only treat it as a real error if we never received a done/error message.
      if (!sseCompletedRef.current) {
        // Unexpected connection failure — abort the whole sync
        forceSyncingRef.current = false
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
        setSyncProgress(null)
      }
      // On clean close (sseCompletedRef=true), do nothing — fetchCustomers handles cleanup
      es.close()
      sseRef.current = null
    }
  }, [])

  // Close SSE on unmount
  useEffect(() => {
    return () => { if (sseRef.current) sseRef.current.close() }
  }, [])

  const fetchCustomers = useCallback(async (pg = 1, reset = false, forceRefresh = false) => {
    setLoading(true)
    setLoadingStart(Date.now())
    if (forceRefresh) {
      setIsForcingRefresh(true)
      forceSyncingRef.current = true
      console.log('[sync] forceRefresh=true, opening SSE...')
      startProgressSSE()
      await new Promise(r => setTimeout(r, 200))
      console.log('[sync] 200ms wait done, firing fetch...')
    }
    setError(null)
    try {
      const params = new URLSearchParams({
        page: pg,
        page_size: PAGE_SIZE,
        search,
        billing_type: billingFilter,
        force_refresh: forceRefresh ? 'true' : 'false',
      })
      const res = await fetch(`${API}/api/customers?${params}`)
      if (res.status === 401) {
        const body = await res.json().catch(() => ({}))
        throw new Error('not_logged_in:' + (body.detail || 'Not logged in'))
      }
      if (!res.ok) throw new Error(`Server error (HTTP ${res.status})`)
      const data = await res.json()
      console.log('[sync] response — fromCache:', data.fromCache, 'forceSyncingRef:', forceSyncingRef.current)
      setCustomers(prev => reset ? data.customers : [...prev, ...data.customers])
      setHasMore(data.hasMore)
      setPage(pg)
      setFromCache(data.fromCache || false)
      setCacheAgeHours(data.cacheAgeHours ?? null)

      // If the backend served from cache (no real sync ran), the SSE stream
      // will never go active — clean up loading state immediately.
      if (forceRefresh && data.fromCache) {
        // Cache hit — SSE never went active, clean up immediately
        console.log('[sync] cache hit — cleaning up immediately')
        forceSyncingRef.current = false
        if (sseRef.current) { sseRef.current.close(); sseRef.current = null }
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
        setSyncProgress(null)
      } else if (forceRefresh) {
        // Real sync completed — SSE has been streaming progress.
        // Now that we have the HTTP response, clean up loading state
        // and keep the final progress bar visible briefly.
        console.log('[sync] real sync HTTP response received — cleaning up loading state')
        forceSyncingRef.current = false
        if (sseRef.current) { sseRef.current.close(); sseRef.current = null }
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
        // Keep 100% bar visible for 3s then clear
        setTimeout(() => setSyncProgress(null), 3000)
      }
    } catch (e) {
      setError(e.message)
      // On error during force-refresh, clean up immediately
      if (forceRefresh) {
        forceSyncingRef.current = false
        setIsForcingRefresh(false)
      }
    } finally {
      console.log('[sync] finally — forceSyncingRef.current:', forceSyncingRef.current)
      // For force-refresh: keep loading=true so the progress bar stays visible.
      // The SSE onmessage handler will call setLoading(false) when sync is done.
      if (!forceSyncingRef.current) {
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
      }
    }
  }, [search, billingFilter, startProgressSSE])

  useEffect(() => {
    fetchCustomers(1, true)
  }, [search, billingFilter])

  // Debounced live search — update `search` 300ms after the user stops typing
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(searchInput)
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [searchInput])

  // Load QB summary on mount
  useEffect(() => {
    fetch(`${API}/api/customers/qb-data/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setQbSummary(d))
      .catch(() => {})
  }, [])

  const handleBillingTypeChange = (customerId, newType) => {
    setCustomers(prev =>
      prev.map(c => c.id === customerId ? { ...c, billingType: newType } : c)
    )
  }

  const handleQbImport = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setImportingQb(true)
    setImportMsg('')
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch(`${API}/api/customers/import-qb`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (HTTP ${res.status})`)
      }
      const data = await res.json()
      setImportMsg(`✓ ${data.message}`)
      // Refresh customer list and summary
      fetchCustomers(1, true)
      const sum = await fetch(`${API}/api/customers/qb-data/summary`)
      if (sum.ok) setQbSummary(await sum.json())
    } catch (e) {
      setImportMsg(`✗ Import failed: ${e.message}`)
    } finally {
      setImportingQb(false)
      e.target.value = ''
    }
  }

  // Group customers into parent/sub-account hierarchy
  const customerGroups = groupCustomers(customers)

  return (
    <div className="flex flex-col h-full">
      {/* ── Sync progress banner — sticky top, shown during force-refresh ── */}
      {isForcingRefresh && (() => {
        const p = syncProgress || { step: 'step1', step_label: 'Connecting to MyAdmin…', pct: 0, message: 'Starting sync…' }
        return (
          <div className="sticky top-0 z-20 mb-4 rounded-xl border border-slate-600/60 bg-slate-800/95 backdrop-blur-sm px-6 py-4 shadow-lg">
            {/* Step label + percentage */}
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-slate-200">
                {p.step_label || 'Syncing…'}
              </span>
              <span className={`text-sm font-mono font-bold ${
                p.step === 'error' ? 'text-red-400' :
                p.pct >= 100 ? 'text-green-400' : 'text-blue-400'
              }`}>
                {p.pct || 0}%
              </span>
            </div>

            {/* Progress bar */}
            <div className="w-full h-2.5 bg-slate-700 rounded-full overflow-hidden mb-2">
              <div
                className="h-full rounded-full transition-all duration-500 ease-out"
                style={{
                  width: `${p.pct || 0}%`,
                  background: p.step === 'error'
                    ? '#ef4444'
                    : p.pct >= 100
                      ? '#22c55e'
                      : 'linear-gradient(90deg, #3b82f6, #6366f1)',
                }}
              />
            </div>

            {/* Message + hint */}
            <div className="flex items-center justify-between">
              <span className="text-xs text-slate-400">{p.message || ''}</span>
              <span className="text-xs text-slate-600">
                First sync ~5 min · repeat syncs use 12-hour cache
              </span>
            </div>
          </div>
        )
      })()}

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Customers</h1>
          <p className="text-sm text-slate-400 mt-1">
            {customers.length} loaded · pulled from Geotab MyAdmin
          </p>
        </div>

        <div className="flex items-start gap-3">
          {/* QB Import button */}
          <label className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors ${
            importingQb
              ? 'bg-slate-700 text-slate-400 cursor-not-allowed'
              : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
          }`}>
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            {importingQb ? 'Importing...' : 'Import QB Customers'}
            <input type="file" accept=".csv" className="hidden" onChange={handleQbImport} disabled={importingQb} />
          </label>

          <div className="flex flex-col items-end gap-1">
            <button
              onClick={() => fetchCustomers(1, true, true)}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-lg text-sm transition-colors"
            >
              <svg className={`w-4 h-4 ${isForcingRefresh ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {isForcingRefresh ? 'Syncing… (may take ~5 min)' : 'Sync from MyAdmin'}
            </button>
            {fromCache && cacheAgeHours !== null && !loading && (
              <span className="text-xs text-slate-500">
                Cached · {cacheAgeHours < 1
                  ? `${Math.round(cacheAgeHours * 60)}m ago`
                  : `${cacheAgeHours.toFixed(1)}h ago`
                }
              </span>
            )}
          </div>
        </div>
      </div>

      {/* QB Summary bar */}
      {qbSummary && qbSummary.customersLoaded > 0 && typeof qbSummary.customersLoaded === 'number' && (
        <div className="mb-4 p-3 bg-green-900/20 border border-green-500/20 rounded-lg flex items-center gap-4 flex-wrap text-sm">
          <span className="text-green-400 font-medium">
            QB Data Loaded
          </span>
          <span className="text-slate-400">{qbSummary.customersLoaded} customers · {qbSummary.itemsLoaded} items</span>
          {Object.entries(qbSummary.billingTypeBreakdown || {}).map(([type, count]) => (
            <span key={type} className="text-slate-500 text-xs">
              {type}: <span className="text-slate-300">{count}</span>
            </span>
          ))}
        </div>
      )}

      {/* Import message */}
      {importMsg && (
        <div className={`mb-4 p-3 rounded-lg text-sm border ${
          importMsg.startsWith('✓')
            ? 'bg-green-900/20 border-green-500/20 text-green-300'
            : 'bg-red-900/20 border-red-500/20 text-red-300'
        }`}>
          {importMsg}
        </div>
      )}

      {/* Search + filters */}
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2">
          <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                // Instant search on Enter — cancel the debounce timer
                if (debounceRef.current) clearTimeout(debounceRef.current)
                setSearch(searchInput)
              }
              if (e.key === 'Escape') {
                setSearchInput('')
              }
            }}
            placeholder="Search customers..."
            className="bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none flex-1"
          />
          {searchInput && (
            <button
              onClick={() => { setSearchInput(''); setSearch('') }}
              className="text-slate-500 hover:text-slate-300"
              title="Clear search"
            >✕</button>
          )}
        </div>

        {/* Billing type filter */}
        <select
          value={billingFilter}
          onChange={e => setBillingFilter(e.target.value)}
          className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-blue-500"
        >
          <option value="">All Billing Types</option>
          {VALID_BILLING_TYPES.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </div>

      {/* Error state */}
      {error && (
        <div className={`mb-4 p-4 rounded-lg text-sm flex items-start gap-3 ${
          error.startsWith('not_logged_in:')
            ? 'bg-yellow-900/30 border border-yellow-500/30 text-yellow-300'
            : 'bg-red-900/30 border border-red-500/30 text-red-300'
        }`}>
          <svg className="w-5 h-5 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d={error.startsWith('not_logged_in:')
                ? "M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                : "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"} />
          </svg>
          <div>
            {error.startsWith('not_logged_in:') ? (
              <>
                <strong>Not connected to MyAdmin.</strong>
                <span className="ml-1">Please log in with your Geotab MyAdmin credentials to sync customers.</span>
              </>
            ) : (
              <>
                <strong>Error loading customers:</strong>
                <span className="ml-1">{error}</span>
                <button
                  onClick={() => fetchCustomers(1, true)}
                  className="ml-3 underline hover:no-underline"
                >
                  Retry
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto rounded-xl border border-slate-700/50">
        <table className="w-full min-w-[900px]">
          <thead className="sticky top-0 z-10 bg-slate-800/90 backdrop-blur-sm">
            <tr className="border-b border-slate-700">
              <th className="w-10 pl-4 py-3" />
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Customer Name
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Billing Type
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Primary Database
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Devices
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Terms
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Balance
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                Actions
              </th>
            </tr>
          </thead>
          <tbody>
            {customers.length === 0 && !loading ? (
              <tr>
                <td colSpan={8} className="text-center py-16 text-slate-500">
                  {error?.startsWith('not_logged_in:')
                    ? 'Log in to your Geotab MyAdmin account to load customers.'
                    : search || billingFilter
                      ? 'No customers match your filters.'
                      : 'Click "Sync from MyAdmin" to load customers.'}
                </td>
              </tr>
            ) : (
              customerGroups.map(group => (
                <ParentGroupRow
                  key={group.parentName}
                  group={group}
                  onBillingTypeChange={handleBillingTypeChange}
                  onDetail={onDetail}
                />
              ))
            )}

            {loading && !isForcingRefresh && (
              <tr>
                <td colSpan={8} className="text-center py-8">
                  {/* Simple spinner for cache/filter loads (progress bar is in the top banner) */}
                  <div className="flex items-center justify-center gap-3 text-slate-400">
                    <svg className="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                    </svg>
                    <span>Loading customers…</span>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Load more */}
      {hasMore && !loading && (
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => fetchCustomers(page + 1, false)}
            className="px-6 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-sm transition-colors"
          >
            Load more customers (page {page + 1})
          </button>
        </div>
      )}

      {/* Footer count */}
      {customers.length > 0 && (
        <div className="mt-3 text-xs text-slate-600 text-center">
          Showing {customers.length} customers · {customerGroups.filter(g => g.subs.length > 0).length > 0
            ? `${customerGroups.filter(g => g.subs.length > 0).length} grouped parent account${customerGroups.filter(g => g.subs.length > 0).length !== 1 ? 's' : ''} · `
            : ''
          }Click any row to expand · Click billing badge to edit
        </div>
      )}
    </div>
  )
}

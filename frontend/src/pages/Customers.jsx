import { useState, useEffect, useCallback, useRef } from 'react'

const API = 'http://localhost:8001'

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

// ─── Single expandable customer row ──────────────────────────────────────────
function CustomerRow({ customer, onBillingTypeChange }) {
  const [expanded, setExpanded] = useState(false)
  const [devices, setDevices] = useState([])
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [editingBilling, setEditingBilling] = useState(false)
  const [selectedType, setSelectedType] = useState(customer.billingType)
  const [savingType, setSavingType] = useState(false)

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
          {editingBilling ? (
            <div className="flex items-center gap-1">
              <select
                value={selectedType}
                onChange={e => setSelectedType(e.target.value)}
                className="bg-slate-700 text-slate-200 text-xs rounded px-2 py-1 border border-slate-600 focus:outline-none focus:border-blue-500"
                onClick={e => e.stopPropagation()}
              >
                {VALID_BILLING_TYPES.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <button
                onClick={saveBillingType}
                disabled={savingType}
                className="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded disabled:opacity-50"
              >
                {savingType ? '...' : '✓'}
              </button>
              <button
                onClick={() => { setEditingBilling(false); setSelectedType(customer.billingType) }}
                className="px-2 py-1 bg-slate-600 hover:bg-slate-500 text-white text-xs rounded"
              >
                ✕
              </button>
            </div>
          ) : (
            <div
              className="flex items-center gap-2 group cursor-pointer"
              onClick={() => setEditingBilling(true)}
              title="Click to change billing type"
            >
              <BillingBadge type={customer.billingType} />
              <svg className="w-3 h-3 text-slate-600 group-hover:text-slate-400 transition-colors opacity-0 group-hover:opacity-100" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
            </div>
          )}
        </td>

        {/* Primary database */}
        <td className="px-4 py-3 text-sm text-slate-400">{customer.primaryDatabase || '—'}</td>

        {/* Device count */}
        <td className="px-4 py-3">
          <span className="inline-flex items-center justify-center w-8 h-6 bg-slate-700 rounded text-xs font-mono text-slate-300">
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
            onClick={e => { e.stopPropagation(); /* TODO: open detail page */ }}
            className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
          >
            Detail →
          </button>
        </td>
      </tr>

      {/* Expanded device sub-table */}
      {expanded && (
        <>
          {devices.length === 0 ? (
            <tr className="border-b border-white/5 bg-slate-900/50">
              <td colSpan={8} className="pl-14 pr-4 py-4 text-xs text-slate-500 italic">
                No device contracts found for this customer.
              </td>
            </tr>
          ) : (
            <>
              {/* Device sub-header */}
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
                      {devices.map((d, i) => (
                        <DeviceRow key={`${d.serialNumber}-${i}`} device={d} />
                      ))}
                    </tbody>
                  </table>
                </td>
              </tr>
            </>
          )}
        </>
      )}
    </>
  )
}

// ─── Main Customers page ──────────────────────────────────────────────────────
export default function Customers() {
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
        setSyncProgress(data)
        // Auto-close when the backend signals done or error
        if (!data.active && (data.step === 'done' || data.step === 'error')) {
          sseCompletedRef.current = true   // mark as normal completion before close
          es.close()
          sseRef.current = null
          forceSyncingRef.current = false
          // Clear the loading state NOW (HTTP request finished long ago)
          setLoading(false)
          setLoadingStart(null)
          setIsForcingRefresh(false)
          // Keep the final 100% bar visible for 3s then clear
          setTimeout(() => setSyncProgress(null), 3000)
        }
      } catch (_) {}
    }
    es.onerror = () => {
      // onerror fires on both unexpected errors AND normal server-side close.
      // Only treat it as a real error if we never received a done/error message.
      if (!sseCompletedRef.current) {
        forceSyncingRef.current = false
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
        setSyncProgress(null)
      }
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
      // Open SSE FIRST so it's listening before the sync sets active=True.
      // Then wait 200 ms to give the EventSource time to connect before we
      // fire the data request that will kick off _fetch_myadmin_customers().
      startProgressSSE()
      await new Promise(r => setTimeout(r, 200))
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
      setCustomers(prev => reset ? data.customers : [...prev, ...data.customers])
      setHasMore(data.hasMore)
      setPage(pg)
      setFromCache(data.fromCache || false)
      setCacheAgeHours(data.cacheAgeHours ?? null)

      // If the backend served from cache (no real sync ran), the SSE stream
      // will never go active — clean up loading state immediately.
      if (forceRefresh && data.fromCache) {
        forceSyncingRef.current = false
        if (sseRef.current) { sseRef.current.close(); sseRef.current = null }
        setLoading(false)
        setLoadingStart(null)
        setIsForcingRefresh(false)
        setSyncProgress(null)
      }
    } catch (e) {
      setError(e.message)
      // On error during force-refresh, clean up immediately
      if (forceRefresh) {
        forceSyncingRef.current = false
        setIsForcingRefresh(false)
      }
    } finally {
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

  // Load QB summary on mount
  useEffect(() => {
    fetch(`${API}/api/customers/qb-data/summary`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setQbSummary(d))
      .catch(() => {})
  }, [])

  const handleSearch = () => {
    setSearch(searchInput)
  }

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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Customers</h1>
          <p className="text-sm text-slate-400 mt-1">
            {customers.length} loaded · pulled from Geotab MyAdmin
          </p>
        </div>

        <div className="flex items-center gap-3">
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
              {isForcingRefresh ? 'Syncing… (may take ~2 min)' : 'Sync from MyAdmin'}
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
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Search customers..."
            className="bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none flex-1"
          />
          {searchInput && (
            <button
              onClick={() => { setSearchInput(''); setSearch('') }}
              className="text-slate-500 hover:text-slate-300"
            >✕</button>
          )}
        </div>
        <button
          onClick={handleSearch}
          className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-sm transition-colors"
        >
          Search
        </button>

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
              customers.map(c => (
                <CustomerRow
                  key={c.id}
                  customer={c}
                  onBillingTypeChange={handleBillingTypeChange}
                />
              ))
            )}

            {loading && (
              <tr>
                <td colSpan={8} className="text-center py-8">
                  {isForcingRefresh && syncProgress ? (
                    /* ── Real-time progress bar (force-refresh sync) ── */
                    <div className="flex flex-col items-center gap-3 px-8 max-w-lg mx-auto">
                      {/* Step label */}
                      <div className="text-sm text-slate-300 font-medium">
                        {syncProgress.step_label || 'Syncing…'}
                      </div>

                      {/* Progress bar track */}
                      <div className="w-full h-3 bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full rounded-full transition-all duration-500 ease-out"
                          style={{
                            width: `${syncProgress.pct || 0}%`,
                            background: syncProgress.step === 'error'
                              ? '#ef4444'
                              : syncProgress.pct >= 100
                                ? '#22c55e'
                                : 'linear-gradient(90deg, #3b82f6, #6366f1)',
                          }}
                        />
                      </div>

                      {/* Percentage + message row */}
                      <div className="flex items-center justify-between w-full text-xs">
                        <span className="text-slate-400">{syncProgress.message || ''}</span>
                        <span className={`font-mono font-bold ${
                          syncProgress.step === 'error' ? 'text-red-400' :
                          syncProgress.pct >= 100 ? 'text-green-400' : 'text-blue-400'
                        }`}>
                          {syncProgress.pct || 0}%
                        </span>
                      </div>

                      {/* Hint text */}
                      <span className="text-xs text-slate-600">
                        First sync fetches all devices &amp; contracts (~2 min). Repeat syncs use a 12-hour cache and are instant.
                      </span>
                    </div>
                  ) : (
                    /* ── Simple spinner for cache/filter loads ── */
                    <div className="flex items-center justify-center gap-3 text-slate-400">
                      <svg className="animate-spin w-5 h-5" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                      </svg>
                      <span>Loading customers…</span>
                    </div>
                  )}
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
          Showing {customers.length} customers · Click any row to expand devices · Click billing badge to edit
        </div>
      )}
    </div>
  )
}

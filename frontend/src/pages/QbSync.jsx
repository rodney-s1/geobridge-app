import React, { useState, useEffect, useRef, useCallback } from 'react'

const API = 'http://127.0.0.1:8001'

// ─── Helpers ──────────────────────────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true,
    })
  } catch { return iso }
}

function fmtDuration(ms) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60000)
  const s = Math.round((ms % 60000) / 1000)
  return `${m}m ${s}s`
}

// ─── Status badge ─────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const MAP = {
    success:  { cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', icon: '✓', label: 'Success' },
    warnings: { cls: 'bg-amber-500/15  text-amber-300  border-amber-500/30',     icon: '⚠', label: 'Warnings' },
    failed:   { cls: 'bg-red-500/15    text-red-300    border-red-500/30',        icon: '✕', label: 'Failed' },
    running:  { cls: 'bg-blue-500/15   text-blue-300   border-blue-500/30',       icon: '↻', label: 'Running' },
    idle:     { cls: 'bg-slate-700/50  text-slate-400  border-slate-600/30',      icon: '–', label: 'Idle' },
  }
  const s = MAP[status] || MAP.idle
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-medium ${s.cls}`}>
      <span className={status === 'running' ? 'animate-spin inline-block' : ''}>{s.icon}</span>
      {s.label}
    </span>
  )
}

// ─── Mode card ────────────────────────────────────────────────────────────────
function ModeCard({ id, icon, title, description, groups, selected, onClick, disabled }) {
  return (
    <button
      onClick={() => !disabled && onClick(id)}
      disabled={disabled}
      className={`text-left w-full rounded-xl border-2 p-5 transition-all duration-150
        ${selected
          ? 'border-blue-500 bg-blue-500/10 shadow-[0_0_0_1px_rgba(59,130,246,0.3)]'
          : 'border-slate-700/60 bg-slate-800/50 hover:border-slate-600 hover:bg-slate-800/80'}
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <div className="flex items-start gap-3">
        <span className="text-2xl mt-0.5 select-none">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`font-semibold text-sm ${selected ? 'text-blue-300' : 'text-slate-200'}`}>
              {title}
            </span>
            {selected && (
              <span className="text-[10px] bg-blue-500 text-white px-1.5 py-0.5 rounded font-medium">
                SELECTED
              </span>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-1 leading-relaxed">{description}</p>
          <div className="flex flex-wrap gap-1.5 mt-2.5">
            {groups.map(g => (
              <span key={g} className="text-[11px] bg-slate-700/60 text-slate-300 border border-slate-600/40
                                       rounded px-2 py-0.5 font-mono">
                {g}
              </span>
            ))}
          </div>
        </div>
      </div>
    </button>
  )
}

// ─── Log line ─────────────────────────────────────────────────────────────────
function LogLine({ entry }) {
  const { type, message, ts } = entry
  const cls = {
    info:    'text-slate-300',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    error:   'text-red-400',
    step:    'text-blue-300 font-semibold',
    dim:     'text-slate-500',
  }[type] || 'text-slate-300'

  const prefix = {
    info:    '  ',
    success: '✓ ',
    warning: '⚠ ',
    error:   '✕ ',
    step:    '▶ ',
    dim:     '  ',
  }[type] || '  '

  return (
    <div className={`flex gap-3 text-xs font-mono leading-5 ${cls}`}>
      <span className="text-slate-600 flex-shrink-0 select-none w-20 text-right">
        {ts ? new Date(ts).toLocaleTimeString('en-US', { hour12: false }) : ''}
      </span>
      <span className="flex-shrink-0 select-none">{prefix}</span>
      <span className="break-all">{message}</span>
    </div>
  )
}

// ─── Error table ──────────────────────────────────────────────────────────────
function ErrorTable({ errors, title, emptyMessage }) {
  const [copied, setCopied] = useState(null)

  function copyRow(idx, msg) {
    navigator.clipboard.writeText(msg).catch(() => {})
    setCopied(idx)
    setTimeout(() => setCopied(null), 1800)
  }

  if (!errors || errors.length === 0) {
    return (
      <div className="text-center py-10 text-slate-500 text-sm">
        <div className="text-3xl mb-2">✓</div>
        {emptyMessage || 'No errors'}
      </div>
    )
  }

  return (
    <div>
      {title && <p className="text-xs text-slate-400 font-semibold mb-3 uppercase tracking-wider">{title}</p>}
      <div className="rounded-lg border border-slate-700/50 overflow-hidden">
        <table className="w-full text-left">
          <thead>
            <tr className="bg-slate-800/80 border-b border-slate-700/40">
              <th className="px-3 py-2 text-[11px] font-medium text-slate-500">Customer</th>
              <th className="px-3 py-2 text-[11px] font-medium text-slate-500">Type</th>
              <th className="px-3 py-2 text-[11px] font-medium text-slate-500">Error</th>
              <th className="px-3 py-2 text-[11px] font-medium text-slate-500 w-16"></th>
            </tr>
          </thead>
          <tbody>
            {errors.map((e, i) => (
              <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-700/20">
                <td className="px-3 py-2 text-xs text-slate-300 max-w-[180px] truncate" title={e.customer}>
                  {e.customer || '—'}
                </td>
                <td className="px-3 py-2">
                  <span className={`text-[11px] px-1.5 py-0.5 rounded border
                    ${e.syncType === 'recurrence'
                      ? 'bg-blue-500/15 text-blue-300 border-blue-500/30'
                      : 'bg-purple-500/15 text-purple-300 border-purple-500/30'}`}>
                    {e.syncType === 'recurrence' ? 'Recurrence' : 'Prorated'}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs text-red-300 max-w-[320px]">
                  <span className="line-clamp-2" title={e.message}>{e.message}</span>
                </td>
                <td className="px-3 py-2">
                  <button
                    onClick={() => copyRow(i, `${e.customer} | ${e.syncType} | ${e.message}`)}
                    className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors
                      ${copied === i
                        ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                        : 'bg-slate-700/50 text-slate-400 border-slate-600/40 hover:text-slate-200'}`}>
                    {copied === i ? '✓' : '⎘'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── History row ──────────────────────────────────────────────────────────────
function HistoryRow({ run }) {
  const [expanded, setExpanded] = useState(false)

  const modeLabel = {
    recurrences: 'Recurrences Only',
    prorated:    'Prorated Only',
    full:        'Full Sync',
  }[run.mode] || run.mode

  const modeCls = {
    recurrences: 'bg-blue-500/15   text-blue-300   border-blue-500/30',
    prorated:    'bg-purple-500/15 text-purple-300 border-purple-500/30',
    full:        'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  }[run.mode] || 'bg-slate-700/50 text-slate-400 border-slate-600/30'

  return (
    <>
      <tr
        className="border-b border-slate-700/40 hover:bg-slate-700/20 cursor-pointer transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        <td className="px-3 py-2.5 align-middle">
          <span className={`text-slate-500 text-xs transition-transform inline-block ${expanded ? 'rotate-90' : ''}`}>▶</span>
        </td>
        <td className="px-3 py-2.5 text-xs text-slate-300 align-middle whitespace-nowrap">
          {fmtDate(run.startedAt)}
        </td>
        <td className="px-3 py-2.5 align-middle">
          <span className={`text-[11px] px-1.5 py-0.5 rounded border ${modeCls}`}>{modeLabel}</span>
        </td>
        <td className="px-3 py-2.5 align-middle"><StatusBadge status={run.status} /></td>
        <td className="px-3 py-2.5 text-xs text-slate-300 align-middle text-center">
          {run.dryRun
            ? (run.totalPreviewed != null ? `${run.totalPreviewed} preview` : (run.recurrencesUpdated ?? '—'))
            : (run.recurrencesUpdated ?? '—')}
        </td>
        <td className="px-3 py-2.5 text-xs text-slate-300 align-middle text-center">
          {run.dryRun ? '—' : (run.invoicesCreated ?? '—')}
        </td>
        <td className="px-3 py-2.5 align-middle text-center">
          {run.errorCount > 0 ? (
            <span className="text-xs font-semibold text-red-400">{run.errorCount}</span>
          ) : (
            <span className="text-xs text-slate-600">0</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-xs text-slate-500 align-middle whitespace-nowrap">
          {fmtDuration(run.durationMs)}
        </td>
        <td className="px-3 py-2.5 text-xs text-slate-500 align-middle">
          {run.dryRun && (
            <span className="text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30 rounded px-1.5 py-0.5">
              DRY RUN
            </span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="px-5 py-3 bg-slate-900/50 border-b border-slate-700/40">
            {run.log && run.log.length > 0 ? (
              <div className="bg-slate-950/60 rounded-lg border border-slate-700/40 p-3 max-h-52 overflow-y-auto space-y-0.5">
                {run.log.map((l, i) => <LogLine key={i} entry={l} />)}
              </div>
            ) : (
              <p className="text-xs text-slate-500 italic">No log available for this run.</p>
            )}
            {run.errors && run.errors.length > 0 && (
              <div className="mt-3">
                <ErrorTable errors={run.errors} title="Errors from this run" />
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────
const MODES = [
  {
    id:          'recurrences',
    icon:        '📋',
    title:       'Recurrences Only',
    description: 'Update the monthly billing recurrences in QB for all active customers. Use this for regular end-of-month billing runs.',
    groups:      ['Monthly Recurrences'],
  },
  {
    id:          'prorated',
    icon:        '🧾',
    title:       'Prorated Invoices Only',
    description: 'Add prorated invoices for newly activated devices into the Prorated Service Invoices group in QB Memorized Transactions.',
    groups:      ['Prorated Service Invoices'],
  },
  {
    id:          'full',
    icon:        '⚡',
    title:       'Full Sync',
    description: 'Run both recurrences and prorated invoice updates together in one pass. Recommended for a complete monthly close.',
    groups:      ['Monthly Recurrences', 'Prorated Service Invoices'],
  },
]

export default function QbSync() {
  const [activeTab,  setActiveTab]  = useState('sync')
  const [mode,       setMode]       = useState(null)
  const [dryRun,     setDryRun]     = useState(false)
  const [syncState,  setSyncState]  = useState('idle')   // idle | running | success | warnings | failed
  const [progress,   setProgress]   = useState(0)
  const [progressLabel, setProgressLabel] = useState('')
  const [log,        setLog]        = useState([])
  const [errors,     setErrors]     = useState([])
  const [result,     setResult]     = useState(null)    // { recurrencesUpdated, invoicesCreated, errorCount, durationMs }
  const [history,    setHistory]    = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [preview,    setPreview]    = useState([])   // dry-run preview rows from done event
  const [previewFilter, setPreviewFilter] = useState('all') // 'all'|'recurrence'|'prorated'

  const logEndRef    = useRef(null)
  const sseRef       = useRef(null)
  const startTimeRef = useRef(null)

  // Error tab badge count
  const errorBadge = errors.length > 0 ? errors.length : null

  // Auto-scroll log
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [log])

  // Load history when tab switches to it
  useEffect(() => {
    if (activeTab === 'history') fetchHistory()
  }, [activeTab])

  async function fetchHistory() {
    setHistoryLoading(true)
    try {
      const res = await fetch(`${API}/api/qb-sync/history`)
      if (res.ok) setHistory(await res.json())
    } catch {}
    setHistoryLoading(false)
  }

  function appendLog(type, message) {
    setLog(prev => [...prev, { type, message, ts: new Date().toISOString() }])
  }

  async function startSync() {
    if (!mode || syncState === 'running') return
    setSyncState('running')
    setProgress(0)
    setProgressLabel('Initialising…')
    setLog([])
    setErrors([])
    setResult(null)
    setPreview([])
    setPreviewFilter('all')
    startTimeRef.current = Date.now()

    appendLog('step', `Starting ${MODES.find(m => m.id === mode)?.title}${dryRun ? ' [DRY RUN]' : ''}…`)

    // Close any existing SSE
    if (sseRef.current) { sseRef.current.close(); sseRef.current = null }

    try {
      const params = new URLSearchParams({ mode, dryRun: dryRun ? '1' : '0' })
      const evtSource = new EventSource(`${API}/api/qb-sync/run?${params}`)
      sseRef.current = evtSource

      evtSource.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)

          if (msg.type === 'progress') {
            setProgress(msg.pct ?? 0)
            setProgressLabel(msg.label ?? '')
            if (msg.log) appendLog(msg.logType || 'info', msg.log)
          }

          if (msg.type === 'log') {
            appendLog(msg.logType || 'info', msg.message)
          }

          if (msg.type === 'error_item') {
            setErrors(prev => [...prev, {
              customer:  msg.customer,
              syncType:  msg.syncType,
              message:   msg.message,
            }])
            appendLog('error', `${msg.customer}: ${msg.message}`)
          }

          if (msg.type === 'done') {
            const durationMs = Date.now() - startTimeRef.current
            const finalStatus = msg.errorCount > 0 ? (msg.failed ? 'failed' : 'warnings') : 'success'
            setSyncState(finalStatus)
            setProgress(100)
            setProgressLabel(finalStatus === 'success' ? 'Sync complete' : 'Completed with errors')
            // In dry-run mode the real counts are 0 — use preview length as the
            // display count so the summary cards show meaningful numbers.
            const previewRows  = msg.preview || []
            const recPreviewed = previewRows.filter(p => p.syncType === 'recurrence').length
            const proPreviewed = previewRows.filter(p => p.syncType === 'prorated').length
            const isDry = dryRun  // captured from outer scope
            setPreview(previewRows)
            setResult({
              recurrencesUpdated: isDry ? recPreviewed : (msg.recurrencesUpdated ?? 0),
              invoicesCreated:    isDry ? proPreviewed : (msg.invoicesCreated    ?? 0),
              errorCount:         msg.errorCount ?? 0,
              durationMs,
              isDryRun: isDry,
            })
            appendLog(
              finalStatus === 'success' ? 'success' : 'warning',
              isDry
                ? `Dry run finished in ${fmtDuration(durationMs)} — ` +
                  `${recPreviewed} recurrences would be updated, ` +
                  `${proPreviewed} prorated invoices would be created, ` +
                  `${msg.errorCount ?? 0} errors`
                : `Sync finished in ${fmtDuration(durationMs)} — ` +
                  `${msg.recurrencesUpdated ?? 0} recurrences, ` +
                  `${msg.invoicesCreated ?? 0} prorated invoices, ` +
                  `${msg.errorCount ?? 0} errors`
            )
            evtSource.close()
            sseRef.current = null
            fetchHistory()
            if (msg.errorCount > 0) setActiveTab('errors')
          }
        } catch {}
      }

      evtSource.onerror = () => {
        setSyncState('failed')
        setProgressLabel('Connection lost')
        appendLog('error', 'SSE connection to backend was lost. The sync may still be running — check History.')
        evtSource.close()
        sseRef.current = null
      }

    } catch (err) {
      setSyncState('failed')
      appendLog('error', `Failed to start sync: ${err.message}`)
    }
  }

  function cancelSync() {
    if (sseRef.current) { sseRef.current.close(); sseRef.current = null }
    setSyncState('idle')
    setProgress(0)
    setProgressLabel('')
    appendLog('warning', 'Sync cancelled by user.')
  }

  const isRunning = syncState === 'running'
  const hasDone   = ['success', 'warnings', 'failed'].includes(syncState)

  // ── Progress bar colour
  const barColor = syncState === 'failed'   ? 'bg-red-500'
                 : syncState === 'warnings' ? 'bg-amber-500'
                 : syncState === 'success'  ? 'bg-emerald-500'
                 : 'bg-blue-500'

  return (
    <div className="p-6 space-y-5 text-slate-100">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">QuickBooks Sync</h2>
          <p className="text-sm text-slate-400 mt-1">
            Push billing data from GeoBridge into QuickBooks Memorized Transactions.
          </p>
        </div>
        {/* Sync-again shortcut after completion */}
        {hasDone && !isRunning && (
          <button
            onClick={() => { setSyncState('idle'); setProgress(0); setLog([]); setErrors([]); setResult(null) }}
            className="text-xs text-slate-400 hover:text-slate-200 border border-slate-600/50
                       hover:border-slate-500 bg-slate-800/60 px-3 py-1.5 rounded transition-colors">
            ↺ New Sync
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-700/50">
        {[
          { id: 'sync',    label: 'Sync',    icon: '⚡' },
          { id: 'history', label: 'History', icon: '🕐' },
          { id: 'errors',  label: 'Errors',  icon: '⚠', badge: errorBadge },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors
              ${activeTab === tab.id
                ? 'border-blue-500 text-blue-300'
                : 'border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-600'}`}
          >
            <span>{tab.icon}</span>
            {tab.label}
            {tab.badge != null && (
              <span className="ml-0.5 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                {tab.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ══ TAB: SYNC ══════════════════════════════════════════════════════════ */}
      {activeTab === 'sync' && (
        <div className="space-y-5">

          {/* ── Mode selection (hidden while running or done) */}
          {syncState === 'idle' && (
            <div>
              <p className="text-xs text-slate-400 font-medium mb-3 uppercase tracking-wider">
                1 — Select Sync Mode
              </p>
              <div className="grid grid-cols-3 gap-4">
                {MODES.map(m => (
                  <ModeCard key={m.id} {...m} selected={mode === m.id} onClick={setMode} disabled={false} />
                ))}
              </div>
            </div>
          )}

          {/* ── Options (shown after mode selected, before run) */}
          {syncState === 'idle' && mode && (
            <div className="bg-slate-800/50 border border-slate-700/50 rounded-lg p-4">
              <p className="text-xs text-slate-400 font-medium mb-3 uppercase tracking-wider">
                2 — Options
              </p>
              <div className="flex items-center gap-6">
                {/* Dry run toggle */}
                <label className="flex items-center gap-2.5 cursor-pointer group">
                  <div
                    onClick={() => setDryRun(d => !d)}
                    className={`relative w-9 h-5 rounded-full transition-colors
                      ${dryRun ? 'bg-amber-500' : 'bg-slate-600'}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                                      transition-transform ${dryRun ? 'translate-x-4' : 'translate-x-0'}`} />
                  </div>
                  <div>
                    <span className="text-sm text-slate-200">Dry Run</span>
                    <p className="text-xs text-slate-500">Preview changes — nothing is pushed to QB</p>
                  </div>
                  {dryRun && (
                    <span className="text-[10px] bg-amber-500/15 text-amber-300 border border-amber-500/30
                                     rounded px-1.5 py-0.5 font-medium">PREVIEW ONLY</span>
                  )}
                </label>
              </div>
            </div>
          )}

          {/* ── Run button (idle state) */}
          {syncState === 'idle' && (
            <div className="flex items-center gap-4">
              <button
                onClick={startSync}
                disabled={!mode}
                className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition-all duration-150
                  ${mode
                    ? dryRun
                      ? 'bg-amber-600 hover:bg-amber-500 text-white shadow-lg shadow-amber-900/30'
                      : 'bg-blue-600  hover:bg-blue-500  text-white shadow-lg shadow-blue-900/30'
                    : 'bg-slate-700 text-slate-500 cursor-not-allowed'}`}
              >
                {!mode
                  ? 'Select a mode above'
                  : dryRun
                    ? `▶ Preview ${MODES.find(m => m.id === mode)?.title}`
                    : `▶ Start ${MODES.find(m => m.id === mode)?.title}`}
              </button>
              {mode && (
                <p className="text-xs text-slate-500">
                  Will update: {MODES.find(m => m.id === mode)?.groups.join(' + ')}
                </p>
              )}
            </div>
          )}

          {/* ── Progress + live log (running or done) */}
          {(isRunning || hasDone) && (
            <div className="space-y-4">

              {/* Mode reminder pill */}
              <div className="flex items-center gap-3">
                <span className="text-lg">{MODES.find(m => m.id === mode)?.icon}</span>
                <span className="text-sm font-medium text-slate-200">
                  {MODES.find(m => m.id === mode)?.title}
                </span>
                {dryRun && (
                  <span className="text-[11px] bg-amber-500/15 text-amber-300 border border-amber-500/30
                                   rounded px-2 py-0.5">DRY RUN</span>
                )}
                <StatusBadge status={syncState} />
              </div>

              {/* Progress bar */}
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-xs text-slate-400">{progressLabel}</span>
                  <span className="text-xs text-slate-500 font-mono">{progress}%</span>
                </div>
                <div className="w-full bg-slate-700/50 rounded-full h-2 overflow-hidden">
                  <div
                    className={`h-2 rounded-full transition-all duration-500 ${barColor}
                                ${isRunning ? 'animate-pulse' : ''}`}
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>

              {/* Result summary cards (shown on completion) */}
              {hasDone && result && (
                <div className="grid grid-cols-4 gap-3">
                  {[
                    {
                      label: 'Status',
                      value: syncState === 'success' ? 'Complete ✓'
                           : syncState === 'warnings' ? 'Warnings ⚠'
                           : 'Failed ✕',
                      cls: syncState === 'success' ? 'text-emerald-400'
                         : syncState === 'warnings' ? 'text-amber-400' : 'text-red-400',
                      bg: syncState === 'success' ? 'bg-emerald-500/10 border-emerald-500/20'
                        : syncState === 'warnings' ? 'bg-amber-500/10  border-amber-500/20'
                        : 'bg-red-500/10 border-red-500/20',
                    },
                    {
                      label: result.isDryRun ? 'Recurrences (preview)' : 'Recurrences',
                      value: result.recurrencesUpdated,
                      cls: 'text-blue-400',
                      bg: 'bg-blue-500/10 border-blue-500/20',
                    },
                    {
                      label: result.isDryRun ? 'Prorated (preview)' : 'Prorated Invoices',
                      value: result.invoicesCreated,
                      cls: 'text-purple-400',
                      bg: 'bg-purple-500/10 border-purple-500/20',
                    },
                    {
                      label: 'Errors',
                      value: result.errorCount,
                      cls: result.errorCount > 0 ? 'text-red-400' : 'text-emerald-400',
                      bg: result.errorCount > 0 ? 'bg-red-500/10 border-red-500/20' : 'bg-slate-800/50 border-slate-700/30',
                    },
                  ].map(card => (
                    <div key={card.label} className={`rounded-lg border p-3 ${card.bg}`}>
                      <p className="text-[11px] text-slate-400 mb-1">{card.label}</p>
                      <p className={`text-xl font-bold ${card.cls}`}>{card.value}</p>
                      {card.label === 'Status' && (
                        <p className="text-[11px] text-slate-500 mt-0.5">{fmtDuration(result.durationMs)}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {/* Dry-run preview table */}
              {hasDone && result?.isDryRun && preview.length > 0 && (
                <div>
                  <div className="flex items-center gap-3 mb-2">
                    <p className="text-xs text-amber-300 font-semibold uppercase tracking-wider">
                      📋 Dry Run Preview — {preview.length} line{preview.length !== 1 ? 's' : ''} that would be written to QB
                    </p>
                    {/* Filter chips */}
                    <div className="flex gap-1 ml-auto">
                      {['all', 'recurrence', 'prorated'].map(f => (
                        <button key={f}
                          onClick={() => setPreviewFilter(f)}
                          className={`text-[11px] px-2 py-0.5 rounded border transition-colors
                            ${previewFilter === f
                              ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              : 'bg-slate-700/50 text-slate-400 border-slate-600/40 hover:text-slate-200'}`}>
                          {f === 'all' ? `All (${preview.length})`
                            : f === 'recurrence' ? `Recurrences (${preview.filter(p => p.syncType === 'recurrence').length})`
                            : `Prorated (${preview.filter(p => p.syncType === 'prorated').length})`}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="bg-slate-950/70 border border-amber-500/20 rounded-lg overflow-hidden">
                    <div className="max-h-72 overflow-y-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="sticky top-0 bg-slate-800/90">
                        <tr className="border-b border-slate-700/50">
                          <th className="px-3 py-2 text-slate-400 font-medium">Customer</th>
                          <th className="px-3 py-2 text-slate-400 font-medium">Type</th>
                          <th className="px-3 py-2 text-slate-400 font-medium">QB SKU</th>
                          <th className="px-3 py-2 text-slate-400 font-medium text-center">Qty</th>
                          <th className="px-3 py-2 text-slate-400 font-medium text-center">Days</th>
                          <th className="px-3 py-2 text-slate-400 font-medium text-right">Amount</th>
                          <th className="px-3 py-2 text-slate-400 font-medium">Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview
                          .filter(p => previewFilter === 'all' || p.syncType === previewFilter)
                          .map((p, i) => (
                          <tr key={i} className="border-b border-slate-700/20 hover:bg-slate-800/40">
                            <td className="px-3 py-1.5 text-slate-200 max-w-[180px] truncate" title={p.customer}>
                              {p.customer}
                            </td>
                            <td className="px-3 py-1.5">
                              <span className={`px-1.5 py-0.5 rounded border text-[10px] font-medium
                                ${ p.syncType === 'recurrence'
                                  ? 'bg-blue-500/15 text-blue-300 border-blue-500/30'
                                  : 'bg-purple-500/15 text-purple-300 border-purple-500/30'}`}>
                                {p.syncType === 'recurrence' ? 'Recurrence' : 'Prorated'}
                              </span>
                            </td>
                            <td className="px-3 py-1.5 font-mono text-slate-300 max-w-[200px] truncate" title={p.skuKey}>
                              {p.skuKey || '—'}
                            </td>
                            <td className="px-3 py-1.5 text-center font-mono text-slate-300">{p.qty ?? '—'}</td>
                            <td className="px-3 py-1.5 text-center font-mono text-slate-400">
                              {p.daysActive != null ? p.daysActive : '—'}
                            </td>
                            <td className="px-3 py-1.5 text-right font-mono text-slate-200">
                              {p.amount != null ? `$${Number(p.amount).toFixed(2)}` : '—'}
                            </td>
                            <td className="px-3 py-1.5">
                              <span className="text-[10px] text-amber-400/80 italic">{p.action}</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    </div>
                  </div>
                  {/* Totals row */}
                  {(() => {
                    const rows = preview.filter(p => previewFilter === 'all' || p.syncType === previewFilter)
                    const total = rows.reduce((s, p) => s + (Number(p.amount) || 0), 0)
                    return (
                      <div className="flex justify-end mt-1.5 gap-4 text-xs text-slate-400 pr-1">
                        <span>{rows.length} line{rows.length !== 1 ? 's' : ''}</span>
                        <span className="font-mono text-slate-200">
                          Total: <span className="text-amber-300 font-semibold">${total.toFixed(2)}</span>
                        </span>
                      </div>
                    )
                  })()}
                </div>
              )}

              {/* Error CTA */}
              {hasDone && result?.errorCount > 0 && (
                <div className="flex items-center gap-3 bg-red-900/20 border border-red-500/30 rounded-lg px-4 py-3">
                  <span className="text-red-400 text-sm font-medium">
                    ⚠ {result.errorCount} error{result.errorCount !== 1 ? 's' : ''} occurred during sync
                  </span>
                  <button
                    onClick={() => setActiveTab('errors')}
                    className="ml-auto text-xs px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30
                               text-red-300 border border-red-500/30 rounded transition-colors">
                    View Errors →
                  </button>
                </div>
              )}

              {/* Live log */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs text-slate-400 font-medium uppercase tracking-wider">Sync Log</p>
                  {isRunning && (
                    <button onClick={cancelSync}
                      className="text-xs px-2.5 py-1 bg-red-900/30 hover:bg-red-900/50 text-red-400
                                 border border-red-500/30 rounded transition-colors">
                      ✕ Cancel
                    </button>
                  )}
                </div>
                <div className="bg-slate-950/70 border border-slate-700/40 rounded-lg p-3
                                h-64 overflow-y-auto space-y-0.5">
                  {log.length === 0 ? (
                    <p className="text-xs text-slate-600 font-mono">Waiting for sync to start…</p>
                  ) : (
                    log.map((l, i) => <LogLine key={i} entry={l} />)
                  )}
                  <div ref={logEndRef} />
                </div>
              </div>
            </div>
          )}

        </div>
      )}

      {/* ══ TAB: HISTORY ═══════════════════════════════════════════════════════ */}
      {activeTab === 'history' && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <p className="text-xs text-slate-400">
              Past sync runs — click any row to expand the log.
            </p>
            <button onClick={fetchHistory} disabled={historyLoading}
              className="text-xs px-3 py-1.5 bg-slate-700/60 hover:bg-slate-700 border border-slate-600/50
                         text-slate-300 rounded transition-colors disabled:opacity-50">
              {historyLoading ? 'Loading…' : '↻ Refresh'}
            </button>
          </div>

          {historyLoading ? (
            <div className="text-center py-12">
              <div className="inline-block w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mb-2" />
              <p className="text-slate-500 text-sm">Loading history…</p>
            </div>
          ) : history.length === 0 ? (
            <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg py-14 text-center">
              <div className="text-4xl mb-3">🕐</div>
              <p className="text-slate-300 font-medium">No sync history yet</p>
              <p className="text-slate-500 text-sm mt-1">Completed syncs will appear here.</p>
            </div>
          ) : (
            <div className="bg-slate-800/60 border border-slate-700/50 rounded-lg overflow-hidden">
              <table className="w-full text-left">
                <thead>
                  <tr className="bg-slate-800/80 border-b border-slate-700/50">
                    <th className="px-3 py-2.5 w-8"></th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Date / Time</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Mode</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400">Status</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400 text-center whitespace-nowrap">Recurrences</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400 text-center whitespace-nowrap">Prorated</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400 text-center">Errors</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400 whitespace-nowrap">Duration</th>
                    <th className="px-3 py-2.5 text-xs font-medium text-slate-400"></th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((run, i) => <HistoryRow key={run.id || i} run={run} />)}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ══ TAB: ERRORS ════════════════════════════════════════════════════════ */}
      {activeTab === 'errors' && (
        <div className="space-y-6">

          {/* Last sync errors */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs text-slate-400 font-semibold uppercase tracking-wider">
                Last Sync Errors
                {errors.length > 0 && (
                  <span className="ml-2 bg-red-500/20 text-red-300 border border-red-500/30
                                   rounded-full px-2 py-0.5 text-[10px] normal-case">
                    {errors.length}
                  </span>
                )}
              </p>
              {errors.length > 0 && (
                <button
                  onClick={() => {
                    const text = errors.map(e => `${e.customer} | ${e.syncType} | ${e.message}`).join('\n')
                    navigator.clipboard.writeText(text).catch(() => {})
                  }}
                  className="text-xs px-2.5 py-1 bg-slate-700/60 hover:bg-slate-700 border border-slate-600/50
                             text-slate-300 rounded transition-colors">
                  ⎘ Copy All
                </button>
              )}
            </div>
            <ErrorTable
              errors={errors}
              emptyMessage={
                syncState === 'idle'
                  ? 'Run a sync first — errors will appear here.'
                  : 'No errors in the last sync. ✓'
              }
            />
          </div>

          {/* Persistent warnings panel */}
          <div className="bg-slate-800/40 border border-slate-700/40 rounded-lg p-4">
            <p className="text-xs text-slate-400 font-semibold uppercase tracking-wider mb-3">
              Persistent Issues to Resolve
            </p>
            <div className="space-y-2 text-xs text-slate-400">
              {[
                { icon: '🔑', text: 'Customers with no QB ID mapped — they will be skipped on every sync until mapped in Settings.' },
                { icon: '📦', text: 'SKUs with no QB Item Code — line items cannot be created in QB without a matching item code.' },
                { icon: '🏢', text: 'Sub-accounts whose QB parent:sub name doesn\'t match any MyAdmin company name.' },
              ].map((item, i) => (
                <div key={i} className="flex items-start gap-2 py-2 border-b border-slate-700/30 last:border-0">
                  <span className="text-base">{item.icon}</span>
                  <span className="leading-relaxed">{item.text}</span>
                  <span className="ml-auto flex-shrink-0 text-[11px] bg-slate-700/60 text-slate-500
                                   border border-slate-600/30 rounded px-1.5 py-0.5">
                    Check Settings
                  </span>
                </div>
              ))}
            </div>
          </div>

        </div>
      )}

    </div>
  )
}

/**
 * SyncStatus.jsx — Sidebar S3 sync badge
 *
 * Polls /api/s3/status every 60 seconds and renders a small status pill:
 *   • not configured  → nothing (hidden)
 *   • syncing / pushing → ⟳ Syncing…
 *   • last error       → ✕ Sync error  (with tooltip)
 *   • last_sync set    → ✓ Synced · <time ago>
 *   • never synced     → ○ Not synced yet
 *
 * Designed to fit inside the narrow sidebar footer.
 */

import { useState, useEffect, useRef } from 'react'

const API = 'http://127.0.0.1:8001'
const POLL_MS = 60_000  // 60 seconds

function timeAgo(isoString) {
  if (!isoString) return null
  try {
    const diffMs  = Date.now() - new Date(isoString).getTime()
    const diffMin = Math.round(diffMs / 60_000)
    if (diffMin < 1)  return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffH = Math.round(diffMin / 60)
    if (diffH < 24)   return `${diffH}h ago`
    return `${Math.round(diffH / 24)}d ago`
  } catch {
    return null
  }
}

export default function SyncStatus() {
  const [status, setStatus]   = useState(null)  // null = not yet loaded
  const timerRef              = useRef(null)

  async function fetchStatus() {
    try {
      const r = await fetch(`${API}/api/s3/status`)
      if (r.ok) setStatus(await r.json())
    } catch {
      // Backend not reachable — keep last known status
    }
  }

  useEffect(() => {
    fetchStatus()
    timerRef.current = setInterval(fetchStatus, POLL_MS)
    return () => clearInterval(timerRef.current)
  }, [])

  // Not loaded yet, or S3 not configured — render nothing
  if (!status || !status.configured) return null

  // Determine display state
  const isBusy  = status.syncing || status.pushing
  const hasErr  = !!status.last_error
  const ago     = timeAgo(status.last_sync)

  // ── Pill styles ──────────────────────────────────────────────────────────
  let dotColor   = '#475569'   // default: muted
  let labelText  = 'Not synced'
  let titleText  = ''

  if (isBusy) {
    dotColor  = '#60a5fa'
    labelText = status.pushing ? 'Pushing…' : 'Syncing…'
  } else if (hasErr) {
    dotColor  = '#f87171'
    labelText = 'Sync error'
    titleText = status.last_error
  } else if (ago) {
    dotColor  = '#34d399'
    labelText = `Synced · ${ago}`
  }

  return (
    <div
      title={titleText || undefined}
      style={{
        display:      'flex',
        alignItems:   'center',
        gap:          '6px',
        padding:      '5px 0',
        cursor:       titleText ? 'help' : 'default',
      }}
    >
      {/* Animated dot / spinner */}
      {isBusy ? (
        <svg
          style={{ width: 10, height: 10, color: dotColor, flexShrink: 0 }}
          className="animate-spin"
          viewBox="0 0 24 24" fill="none"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10"
            stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
        </svg>
      ) : (
        <div style={{
          width:        8,
          height:       8,
          borderRadius: '50%',
          background:   dotColor,
          flexShrink:   0,
        }} />
      )}

      <span style={{
        fontSize:     '11px',
        color:        hasErr ? '#f87171' : '#64748b',
        whiteSpace:   'nowrap',
        overflow:     'hidden',
        textOverflow: 'ellipsis',
      }}>
        {labelText}
      </span>
    </div>
  )
}

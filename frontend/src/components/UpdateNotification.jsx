/**
 * UpdateNotification
 *
 * Handles the full update lifecycle inside the Electron app:
 *
 *   idle          — nothing happening; "Check for Updates" button available
 *   checking      — spinner while querying GitHub Releases
 *   available     — new version found; user can click "Download"
 *   downloading   — progress bar while the installer downloads
 *   ready         — installer downloaded; prominent "Restart & Install" banner
 *                   + MyAdmin sync is BLOCKED until the user installs or snoozes
 *   up-to-date    — brief confirmation then back to idle
 *   error         — something failed; shows message + retry button
 *
 * Props:
 *   onSyncBlocked(bool)  — called whenever the update-blocks-sync state changes;
 *                          the parent (App.jsx / Dashboard) passes this down to
 *                          Customers.jsx so the force-refresh button can be
 *                          disabled when an update is waiting.
 */

import { useState, useEffect, useCallback, useRef } from 'react'

// ─── tiny helpers ────────────────────────────────────────────────────────────
const isElectron = () =>
  typeof window !== 'undefined' && !!window.updaterAPI

function fmtBytes(bytes) {
  if (!bytes) return ''
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ─── component ───────────────────────────────────────────────────────────────
/**
 * compact  — when true (sidebar usage), the "ready to install" full-width
 *            blocking banner is suppressed; a small clickable reminder is
 *            shown instead.  The App.jsx top-level instance renders the real
 *            banner; the sidebar instance just needs idle/check/progress states.
 */
export default function UpdateNotification({ onSyncBlocked, compact = false }) {

  // ── state machine ──────────────────────────────────────────────────────────
  const [phase, setPhase] = useState('idle')
  // idle | checking | available | downloading | ready | up-to-date | error

  const [availableVersion,  setAvailableVersion]  = useState(null)
  const [currentVersion,    setCurrentVersion]    = useState(null)
  const [downloadProgress,  setDownloadProgress]  = useState(0)    // 0-100
  const [downloadedBytes,   setDownloadedBytes]   = useState(0)
  const [totalBytes,        setTotalBytes]         = useState(0)
  const [readyVersion,      setReadyVersion]       = useState(null)
  const [errorMessage,      setErrorMessage]       = useState(null)
  const [snoozed,           setSnoozed]            = useState(false)
  // snoozed: user clicked "Later" on the ready-to-install banner;
  // the block on MyAdmin sync is LIFTED but the banner can be re-shown
  // from the "Check for Updates" button in settings/header.

  const checkInProgress = useRef(false)

  // ── notify parent whenever sync-block status changes ──────────────────────
  const syncBlocked = phase === 'ready' && !snoozed
  useEffect(() => {
    onSyncBlocked?.(syncBlocked)
  }, [syncBlocked, onSyncBlocked])

  // ── get current app version on mount ──────────────────────────────────────
  useEffect(() => {
    if (!isElectron()) return
    window.electronAPI.getAppVersion().then(v => setCurrentVersion(v)).catch(() => {})

    // Also check if main process already has a downloaded update waiting
    // (e.g. user dismissed the window and reopened it)
    window.updaterAPI.getStatus().then(({ updateReadyToInstall, pendingUpdateVersion }) => {
      if (updateReadyToInstall && pendingUpdateVersion) {
        setReadyVersion(pendingUpdateVersion)
        setPhase('ready')
      }
    }).catch(() => {})
  }, [])

  // ── subscribe to push events from Electron main ───────────────────────────
  useEffect(() => {
    if (!isElectron()) return

    window.updaterAPI.onUpdateAvailable(({ version }) => {
      setAvailableVersion(version)
      setPhase('available')
      checkInProgress.current = false
    })

    window.updaterAPI.onUpdateNotAvailable(() => {
      setPhase('up-to-date')
      checkInProgress.current = false
      // Auto-dismiss after 4 s
      setTimeout(() => setPhase('idle'), 4000)
    })

    window.updaterAPI.onDownloadProgress(({ percent, transferred, total }) => {
      setDownloadProgress(percent)
      setDownloadedBytes(transferred)
      setTotalBytes(total)
    })

    window.updaterAPI.onUpdateDownloaded(({ version }) => {
      setReadyVersion(version)
      setDownloadProgress(100)
      setSnoozed(false)
      // Small delay so the 100% bar is visible for a moment
      setTimeout(() => setPhase('ready'), 800)
    })

    window.updaterAPI.onUpdateError(({ message }) => {
      setErrorMessage(message)
      setPhase('error')
      checkInProgress.current = false
    })

    return () => window.updaterAPI.removeAllListeners()
  }, [])

  // ── actions ────────────────────────────────────────────────────────────────
  const handleCheck = useCallback(async () => {
    if (!isElectron() || checkInProgress.current) return
    checkInProgress.current = true
    setErrorMessage(null)
    setPhase('checking')
    const result = await window.updaterAPI.checkForUpdates()
    if (!result.ok) {
      setErrorMessage(result.error || 'Check failed')
      setPhase('error')
      checkInProgress.current = false
    }
    // On success, the push events (update-available / update-not-available) drive the state
  }, [])

  const handleDownload = useCallback(async () => {
    if (!isElectron()) return
    setDownloadProgress(0)
    setPhase('downloading')
    const result = await window.updaterAPI.downloadUpdate()
    if (!result.ok) {
      setErrorMessage(result.error || 'Download failed')
      setPhase('error')
    }
  }, [])

  const handleInstall = useCallback(() => {
    if (!isElectron()) return
    // The main process kills Python backend then calls quitAndInstall
    window.updaterAPI.installUpdate()
  }, [])

  const handleSnooze = useCallback(() => {
    // User defers install — lift the sync block, keep update staged
    setSnoozed(true)
  }, [])

  // ── render: non-Electron (web preview) ────────────────────────────────────
  if (!isElectron()) {
    return (
      <div className="text-xs text-slate-500 px-2 py-1 rounded bg-slate-800/40">
        Updates only available in the installed app
      </div>
    )
  }

  // ── render: idle ──────────────────────────────────────────────────────────
  if (phase === 'idle') {
    return (
      <button
        onClick={handleCheck}
        className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200
                   transition-colors px-2.5 py-1.5 rounded-lg hover:bg-slate-700/60"
        title="Check for updates"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2}
             viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11
                   11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        Check for Updates
        {currentVersion && (
          <span className="ml-1 text-slate-600">v{currentVersion}</span>
        )}
      </button>
    )
  }

  // ── render: checking ──────────────────────────────────────────────────────
  if (phase === 'checking') {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-400 px-2.5 py-1.5">
        <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10"
                  stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor"
                d="M4 12a8 8 0 018-8v8H4z"/>
        </svg>
        Checking for updates…
      </div>
    )
  }

  // ── render: up-to-date ────────────────────────────────────────────────────
  if (phase === 'up-to-date') {
    return (
      <div className="flex items-center gap-2 text-xs text-emerald-400 px-2.5 py-1.5
                      rounded-lg bg-emerald-950/40 border border-emerald-800/40">
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5}
             viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7"/>
        </svg>
        Your app is up to date
        {currentVersion && <span className="text-emerald-600">v{currentVersion}</span>}
      </div>
    )
  }

  // ── render: error ─────────────────────────────────────────────────────────
  if (phase === 'error') {
    return (
      <div className="flex items-center gap-2 text-xs px-2.5 py-1.5 rounded-lg
                      bg-red-950/40 border border-red-800/40">
        <svg className="w-3.5 h-3.5 text-red-400 flex-shrink-0" fill="none"
             stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round"
                d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
        </svg>
        <span className="text-red-300 truncate max-w-[200px]" title={errorMessage}>
          {errorMessage || 'Update check failed'}
        </span>
        <button onClick={handleCheck}
                className="ml-1 text-red-400 hover:text-red-200 underline whitespace-nowrap">
          Retry
        </button>
        <button onClick={() => setPhase('idle')}
                className="text-slate-500 hover:text-slate-300">✕</button>
      </div>
    )
  }

  // ── render: update available ──────────────────────────────────────────────
  if (phase === 'available') {
    return (
      <div className="flex items-center gap-3 text-xs px-3 py-2 rounded-lg
                      bg-blue-950/50 border border-blue-700/40">
        <svg className="w-4 h-4 text-blue-400 flex-shrink-0" fill="none"
             stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round"
                d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0
                   011 9.9M9 19l3 3m0 0l3-3m-3 3V10"/>
        </svg>
        <div className="flex flex-col">
          <span className="text-blue-200 font-medium">
            Update available — v{availableVersion}
          </span>
          {currentVersion && (
            <span className="text-blue-500">Current: v{currentVersion}</span>
          )}
        </div>
        <button
          onClick={handleDownload}
          className="ml-auto px-3 py-1 rounded-md bg-blue-600 hover:bg-blue-500
                     text-white font-medium transition-colors whitespace-nowrap"
        >
          Download
        </button>
        <button onClick={() => setPhase('idle')}
                className="text-slate-500 hover:text-slate-300 flex-shrink-0">✕</button>
      </div>
    )
  }

  // ── render: downloading ───────────────────────────────────────────────────
  if (phase === 'downloading') {
    return (
      <div className="flex flex-col gap-1.5 px-3 py-2 rounded-lg
                      bg-blue-950/50 border border-blue-700/40 min-w-[260px]">
        <div className="flex items-center justify-between text-xs">
          <span className="text-blue-300 font-medium">
            Downloading v{availableVersion}…
          </span>
          <span className="text-blue-400 tabular-nums">{downloadProgress}%</span>
        </div>
        {/* Progress bar */}
        <div className="w-full h-1.5 bg-blue-900/60 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-400 rounded-full transition-all duration-300"
            style={{ width: `${downloadProgress}%` }}
          />
        </div>
        {totalBytes > 0 && (
          <div className="text-[10px] text-blue-500 tabular-nums">
            {fmtBytes(downloadedBytes)} / {fmtBytes(totalBytes)}
          </div>
        )}
      </div>
    )
  }

  // ── render: ready to install ──────────────────────────────────────────────
  // The full-width blocking banner is rendered by the App.jsx top-level instance.
  // When compact=true (sidebar), show only a small reminder so the banner text
  // never wraps into a single-word-per-line column inside the narrow sidebar.
  if (phase === 'ready') {
    if (compact || snoozed) {
      // Compact reminder — sync is unblocked (if snoozed), or full banner is
      // already visible at top of app (if compact).
      return (
        <button
          onClick={compact ? undefined : () => setSnoozed(false)}
          className="flex items-center gap-1.5 text-xs text-amber-400/80
                     hover:text-amber-300 transition-colors px-2.5 py-1.5
                     rounded-lg hover:bg-amber-950/40 border border-amber-800/30
                     w-full"
          title={`GeoBridge v${readyVersion} is ready to install`}
          style={{ cursor: compact ? 'default' : 'pointer' }}
        >
          <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor"
               strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11
                     11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          <span className="truncate">
            {compact ? `v${readyVersion} ready to install` : `v${readyVersion} ready — click to install`}
          </span>
        </button>
      )
    }

    // Full blocking banner
    return (
      <div className="w-full px-4 py-3 bg-amber-950/70 border-b border-amber-700/50
                      flex items-center gap-4 text-sm">
        {/* Icon */}
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-amber-500/20
                        flex items-center justify-center">
          <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor"
               strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11
                     11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
        </div>

        {/* Text */}
        <div className="flex-1 min-w-0">
          <p className="text-amber-200 font-semibold">
            GeoBridge v{readyVersion} is ready to install
          </p>
          <p className="text-amber-400/80 text-xs mt-0.5">
            MyAdmin sync is paused until you install or choose "Later" — this
            prevents data loss if the app restarts mid-sync.
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleInstall}
            className="px-4 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400
                       text-slate-900 font-semibold text-xs transition-colors"
          >
            Restart &amp; Install
          </button>
          <button
            onClick={handleSnooze}
            className="px-3 py-1.5 rounded-lg border border-amber-700/60
                       text-amber-400 hover:text-amber-200 hover:border-amber-500/60
                       text-xs transition-colors"
            title="Install later — sync will be re-enabled"
          >
            Later
          </button>
        </div>
      </div>
    )
  }

  return null
}

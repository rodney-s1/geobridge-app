import { useState, useCallback, useEffect } from 'react'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Setup from './pages/Setup'
import UpdateNotification from './components/UpdateNotification'
import './index.css'

const API = 'http://127.0.0.1:8001'

function App() {
  // 'checking' → brief startup check; 'setup' → wizard; 'login' → normal; 'dashboard'
  const [currentPage, setCurrentPage] = useState('checking')
  const [sessionData, setSessionData] = useState(null)

  // Lifted from UpdateNotification — true when a downloaded update is staged
  // and waiting to be installed.  Passed to Dashboard → Customers to block
  // the MyAdmin force-sync button until the user installs or snoozes.
  const [syncBlocked, setSyncBlocked] = useState(false)

  // ── On mount: ask backend whether aws_config.json exists, then try to get
  // straight into the app without showing the Login screen if at all possible.
  //
  // Order of attempts once S3 is configured:
  //   1. POST /api/geotab/session/restore — resume a still-valid MyAdmin
  //      session token that was persisted to disk on a previous run.
  //   2. If no valid saved session, and the user opted into "Remember me",
  //      silently re-authenticate using the encrypted credentials stored via
  //      Electron's safeStorage (window.credentialsAPI).
  //   3. Otherwise, fall back to showing the normal Login screen.
  //
  // Retries the S3 check up to 10 times (every 1.5 s) to handle the backend
  // taking several seconds to start. Only falls through to login if every
  // attempt fails.
  useEffect(() => {
    let cancelled = false

    // Attempt silent session restore, then silent "remember me" re-login.
    // Resolves by calling setCurrentPage('dashboard') or setCurrentPage('login').
    async function attemptAutoLogin() {
      // ── 1. Resume a persisted MyAdmin session, if still valid ──────────
      try {
        const r = await fetch(`${API}/api/geotab/session/restore`, {
          method: 'POST',
          signal: AbortSignal.timeout(8000),
        })
        if (r.ok) {
          const data = await r.json()
          if (!cancelled && data.restored) {
            setSessionData({
              success: true,
              name: data.name,
              accounts: data.accounts || [],
              account_id: data.account_id,
            })
            setCurrentPage('dashboard')
            return
          }
        }
      } catch {
        // Backend unreachable or restore failed — fall through to next step.
      }

      if (cancelled) return

      // ── 2. No valid saved session — try silent "Remember me" re-login ──
      // window.credentialsAPI is only present when running inside Electron
      // with the preload script loaded (never in a plain browser tab).
      if (window.credentialsAPI) {
        try {
          const { ok, credentials } = await window.credentialsAPI.load()
          if (ok && credentials && credentials.username && credentials.password) {
            const loginRes = await fetch(`${API}/api/geotab/login`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                username: credentials.username,
                password: credentials.password,
              }),
              signal: AbortSignal.timeout(15000),
            })
            const loginData = await loginRes.json()

            if (!cancelled && loginRes.ok && loginData.success) {
              const accts = loginData.accounts || []
              let accountIdToUse = null

              if (accts.length === 0) {
                // No account selection needed at all.
                setSessionData(loginData)
                setCurrentPage('dashboard')
                return
              } else if (accts.length === 1) {
                accountIdToUse = accts[0].accountId
              } else if (
                credentials.accountId &&
                accts.some(a => a.accountId === credentials.accountId)
              ) {
                // Multiple accounts, but we remember which one was picked last time.
                accountIdToUse = credentials.accountId
              }

              if (accountIdToUse) {
                const selRes = await fetch(`${API}/api/geotab/select-account`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ account_id: accountIdToUse }),
                })
                const selData = await selRes.json()
                if (!cancelled && selRes.ok && selData.success) {
                  setSessionData({ ...loginData, account_id: accountIdToUse })
                  setCurrentPage('dashboard')
                  return
                }
              }
              // Ambiguous multi-account case with no matching saved accountId,
              // or account selection failed — fall through to manual login.
            }
          }
        } catch {
          // Silent auto-login failed for any reason — fall through to manual login.
        }
      }

      if (!cancelled) setCurrentPage('login')
    }

    async function checkS3() {
      const MAX_ATTEMPTS = 10
      const RETRY_MS     = 1500

      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        if (cancelled) return
        try {
          const r = await fetch(`${API}/api/s3/check-configured`,
            { signal: AbortSignal.timeout(3000) })
          if (cancelled) return
          const data = await r.json()
          if (data.configured) {
            await attemptAutoLogin()
          } else {
            setCurrentPage('setup')
          }
          return  // success — stop retrying
        } catch {
          if (attempt === MAX_ATTEMPTS) {
            // Backend never came up — fall through to login
            if (!cancelled) setCurrentPage('login')
          } else {
            // Wait before retrying
            await new Promise(res => setTimeout(res, RETRY_MS))
          }
        }
      }
    }

    checkS3()
    return () => { cancelled = true }
  }, [])

  const handleLoginSuccess = (data) => {
    setSessionData(data)
    setCurrentPage('dashboard')
  }

  const handleLogout = async () => {
    // Best-effort: clear the persisted MyAdmin session on the backend so a
    // relaunch doesn't silently resume the account the user just signed out of.
    try {
      await fetch(`${API}/api/geotab/logout`, { method: 'POST' })
    } catch {
      // Backend unreachable — nothing more we can do; still clear local state.
    }
    // Also forget any "Remember me" encrypted credentials — an explicit
    // Sign Out means the user wants to be logged out, full stop.
    if (window.credentialsAPI) {
      try { await window.credentialsAPI.clear() } catch { /* ignore */ }
    }
    setSessionData(null)
    setCurrentPage('login')
  }

  const handleSyncBlocked = useCallback((blocked) => {
    setSyncBlocked(blocked)
  }, [])

  // Brief "checking" phase — blank dark screen so there's no flash of the
  // login page while we await the /api/s3/check-configured response.
  if (currentPage === 'checking') {
    return (
      <div style={{
        height: '100vh', background: '#0f172a',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <svg className="animate-spin" style={{ width: 28, height: 28, color: '#334155' }}
          viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10"
            stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
        </svg>
      </div>
    )
  }

  // Setup wizard — shown before login on first run (no aws_config.json yet)
  if (currentPage === 'setup') {
    return <Setup onSetupComplete={() => setCurrentPage('login')} />
  }

  return (
    <div className="app" style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>

      {/* ── Update banner (full-width, only visible when update is ready) ── */}
      {/* Rendered outside Login/Dashboard so it always appears at the top.  */}
      {/* UpdateNotification is safe to mount on the login page too — it only */}
      {/* shows the compact "Check for Updates" button once in the app shell.  */}
      <UpdateNotification onSyncBlocked={handleSyncBlocked} />

      {/* ── Page content ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {currentPage === 'login' && (
          <Login onLoginSuccess={handleLoginSuccess} />
        )}
        {currentPage === 'dashboard' && (
          <Dashboard
            sessionData={sessionData}
            onLogout={handleLogout}
            syncBlocked={syncBlocked}
          />
        )}
      </div>

    </div>
  )
}

export default App

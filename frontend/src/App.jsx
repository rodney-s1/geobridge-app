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

  // ── On mount: ask backend whether aws_config.json exists ──────────────────
  // If S3 is not configured → show Setup wizard before Login.
  // Retries up to 10 times (every 1.5 s) to handle the backend taking several
  // seconds to start. Only falls through to login if every attempt fails.
  useEffect(() => {
    let cancelled = false

    async function checkS3() {
      const MAX_ATTEMPTS = 10
      const RETRY_MS     = 1500

      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        if (cancelled) return
        try {
          const r = await fetch(`${API}/api/s3/check-configured`,
            { signal: AbortSignal.timeout(3000) })
          if (!cancelled) {
            const data = await r.json()
            setCurrentPage(data.configured ? 'login' : 'setup')
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

  const handleLogout = () => {
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

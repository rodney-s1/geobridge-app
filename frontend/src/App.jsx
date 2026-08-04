import { useState, useCallback } from 'react'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import UpdateNotification from './components/UpdateNotification'
import './index.css'

function App() {
  const [currentPage, setCurrentPage] = useState('login')
  const [sessionData, setSessionData] = useState(null)

  // Lifted from UpdateNotification — true when a downloaded update is staged
  // and waiting to be installed.  Passed to Dashboard → Customers to block
  // the MyAdmin force-sync button until the user installs or snoozes.
  const [syncBlocked, setSyncBlocked] = useState(false)

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

import { useState } from 'react'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import './index.css'

function App() {
  const [currentPage, setCurrentPage] = useState('login')
  const [sessionData, setSessionData] = useState(null)

  const handleLoginSuccess = (data) => {
    setSessionData(data)
    setCurrentPage('dashboard')
  }

  const handleLogout = () => {
    setSessionData(null)
    setCurrentPage('login')
  }

  return (
    <div className="app">
      {currentPage === 'login' && (
        <Login onLoginSuccess={handleLoginSuccess} />
      )}
      {currentPage === 'dashboard' && (
        <Dashboard
          sessionData={sessionData}
          onLogout={handleLogout}
        />
      )}
    </div>
  )
}

export default App

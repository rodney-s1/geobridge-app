import { useState } from 'react'

function Login({ onLoginSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleLogin = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const response = await fetch('http://127.0.0.1:8001/api/geotab/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      })

      const data = await response.json()

      if (response.ok && data.success) {
        onLoginSuccess(data)
      } else {
        setError(data.detail || 'Invalid username or password')
      }
    } catch (err) {
      setError('Cannot connect to backend. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.container}>
      <div style={styles.card}>

        {/* Logo / Title */}
        <div style={styles.header}>
          <div style={styles.logo}>GB</div>
          <h1 style={styles.title}>GeoBridge</h1>
          <p style={styles.subtitle}>Invoicing Suite</p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleLogin} style={styles.form}>

          <div style={styles.field}>
            <label style={styles.label}>MyAdmin Username</label>
            <input
              type="email"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="you@company.com"
              style={styles.input}
              required
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>MyAdmin Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              style={styles.input}
              required
            />
          </div>

          {/* Error Message */}
          {error && (
            <div style={styles.error}>
              {error}
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            style={loading ? styles.buttonLoading : styles.button}
            disabled={loading}
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>

        </form>

        <p style={styles.footer}>
          Connected to Geotab MyAdmin API
        </p>

      </div>
    </div>
  )
}

const styles = {
  container: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100vh',
    background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)',
  },
  card: {
    background: '#1e293b',
    borderRadius: '16px',
    padding: '48px',
    width: '420px',
    boxShadow: '0 25px 50px rgba(0,0,0,0.5)',
    border: '1px solid #334155',
  },
  header: {
    textAlign: 'center',
    marginBottom: '36px',
  },
  logo: {
    width: '64px',
    height: '64px',
    borderRadius: '16px',
    background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: '24px',
    fontWeight: 'bold',
    color: 'white',
    margin: '0 auto 16px',
  },
  title: {
    fontSize: '28px',
    fontWeight: '700',
    color: '#f1f5f9',
    margin: '0 0 4px',
  },
  subtitle: {
    fontSize: '14px',
    color: '#64748b',
    margin: 0,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: '20px',
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  label: {
    fontSize: '14px',
    fontWeight: '500',
    color: '#94a3b8',
  },
  input: {
    padding: '12px 16px',
    borderRadius: '8px',
    border: '1px solid #334155',
    background: '#0f172a',
    color: '#f1f5f9',
    fontSize: '15px',
    outline: 'none',
  },
  error: {
    background: '#450a0a',
    border: '1px solid #7f1d1d',
    color: '#fca5a5',
    padding: '12px 16px',
    borderRadius: '8px',
    fontSize: '14px',
  },
  button: {
    padding: '14px',
    borderRadius: '8px',
    border: 'none',
    background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
    color: 'white',
    fontSize: '16px',
    fontWeight: '600',
    cursor: 'pointer',
    marginTop: '8px',
  },
  buttonLoading: {
    padding: '14px',
    borderRadius: '8px',
    border: 'none',
    background: '#334155',
    color: '#64748b',
    fontSize: '16px',
    fontWeight: '600',
    cursor: 'not-allowed',
    marginTop: '8px',
  },
  footer: {
    textAlign: 'center',
    fontSize: '12px',
    color: '#475569',
    marginTop: '24px',
    marginBottom: 0,
  }
}

export default Login

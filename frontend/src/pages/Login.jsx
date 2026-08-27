import { useState } from 'react'

const API = 'http://127.0.0.1:8001'

// Electron's safeStorage bridge (window.credentialsAPI) is only present when
// running inside the packaged/dev Electron shell — never in a plain browser.
const hasCredentialsAPI = typeof window !== 'undefined' && !!window.credentialsAPI

function Login({ onLoginSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // "Remember me" — optional, encrypted (Electron safeStorage) credential
  // storage so the app can silently re-auth on next launch without the
  // saved-session token (auth.py's session.json) needing to still be valid.
  const [rememberMe, setRememberMe] = useState(false)

  // Account selection step
  const [accounts, setAccounts] = useState([])
  const [selectedAccount, setSelectedAccount] = useState('')
  const [sessionData, setSessionData] = useState(null)
  const [selectingAccount, setSelectingAccount] = useState(false)

  const handleLogin = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const response = await fetch(`${API}/api/geotab/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      })

      const data = await response.json()

      if (response.ok && data.success) {
        const accts = data.accounts || []
        if (accts.length === 1) {
          // Only one account — auto-select it
          await selectAccount(accts[0].accountId, data)
        } else if (accts.length > 1) {
          // Multiple accounts — show picker
          setAccounts(accts)
          setSelectedAccount(accts[0].accountId)
          setSessionData(data)
        } else {
          // No accounts returned — proceed anyway
          await maybeSaveRememberedCredentials(null)
          onLoginSuccess(data)
        }
      } else {
        setError(data.detail || 'Invalid username or password')
      }
    } catch (err) {
      setError('Cannot connect to backend. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  // Persist (or clear) the encrypted "Remember me" credentials via the
  // Electron main process (safeStorage). Best-effort — never blocks login.
  const maybeSaveRememberedCredentials = async (accountId) => {
    if (!hasCredentialsAPI) return
    try {
      if (rememberMe) {
        await window.credentialsAPI.save({ username, password, accountId })
      } else {
        await window.credentialsAPI.clear()
      }
    } catch {
      // Non-fatal — "Remember me" is a convenience feature only.
    }
  }

  const selectAccount = async (accountId, sessData) => {
    setSelectingAccount(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/geotab/select-account`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: accountId })
      })
      const data = await res.json()
      if (res.ok && data.success) {
        await maybeSaveRememberedCredentials(accountId)
        onLoginSuccess({ ...(sessData || sessionData), account_id: accountId })
      } else {
        setError(data.detail || 'Failed to select account')
      }
    } catch (err) {
      setError('Cannot connect to backend. Please try again.')
    } finally {
      setSelectingAccount(false)
    }
  }

  const handleSelectAccount = (e) => {
    e.preventDefault()
    selectAccount(selectedAccount, sessionData)
  }

  // ── Account picker screen ─────────────────────────────────
  if (accounts.length > 1 && sessionData) {
    return (
      <div style={styles.container}>
        <div style={styles.card}>
          <div style={styles.header}>
            <div style={styles.logo}>GB</div>
            <h1 style={styles.title}>GeoBridge</h1>
            <p style={styles.subtitle}>Select Account</p>
          </div>

          <form onSubmit={handleSelectAccount} style={styles.form}>
            <div style={styles.field}>
              <label style={styles.label}>MyAdmin Account</label>
              <select
                value={selectedAccount}
                onChange={e => setSelectedAccount(e.target.value)}
                style={styles.input}
              >
                {accounts.map(a => (
                  <option key={a.accountId} value={a.accountId}>
                    {a.accountId}
                  </option>
                ))}
              </select>
            </div>

            {error && <div style={styles.error}>{error}</div>}

            <button
              type="submit"
              style={selectingAccount ? styles.buttonLoading : styles.button}
              disabled={selectingAccount}
            >
              {selectingAccount ? 'Connecting...' : 'Continue'}
            </button>

            <button
              type="button"
              style={styles.backButton}
              onClick={() => { setAccounts([]); setSessionData(null) }}
            >
              Back to Login
            </button>
          </form>
        </div>
      </div>
    )
  }

  // ── Login screen ──────────────────────────────────────────
  return (
    <div style={styles.container}>
      <div style={styles.card}>

        <div style={styles.header}>
          <div style={styles.logo}>GB</div>
          <h1 style={styles.title}>GeoBridge</h1>
          <p style={styles.subtitle}>Invoicing Suite</p>
        </div>

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

          {hasCredentialsAPI && (
            <label style={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                style={styles.checkbox}
              />
              <span>Remember me on this device</span>
            </label>
          )}

          {error && <div style={styles.error}>{error}</div>}

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
  checkboxRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '13px',
    color: '#94a3b8',
    cursor: 'pointer',
    marginTop: '-8px',
  },
  checkbox: {
    width: '15px',
    height: '15px',
    cursor: 'pointer',
    accentColor: '#3b82f6',
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
  backButton: {
    padding: '10px',
    borderRadius: '8px',
    border: '1px solid #334155',
    background: 'transparent',
    color: '#64748b',
    fontSize: '14px',
    cursor: 'pointer',
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

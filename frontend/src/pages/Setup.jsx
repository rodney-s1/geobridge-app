/**
 * Setup.jsx — First-launch S3 credentials wizard
 *
 * Shown by App.jsx when /api/s3/check-configured returns { configured: false }.
 * Walks the user through:
 *   1. Enter Access Key ID + Secret Access Key (both masked)
 *   2. Optional: expand "Advanced" to change region / bucket / prefix
 *   3. Test connection → success indicator
 *   4. Save → triggers initial pull → calls onSetupComplete()
 *
 * Keys are NEVER shown back to the user after save (one-time entry).
 * They are NEVER stored in component state beyond the wizard lifetime.
 */

import { useState } from 'react'

const API = 'http://127.0.0.1:8001'

// Minimal eye-toggle icon rendered inline — no icon lib dependency
function EyeIcon({ open }) {
  return open ? (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none"
      viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M2.458 12C3.732 7.943 7.523 5 12 5c4.477 0 8.268 2.943 9.542 7
           -1.274 4.057-5.065 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
    </svg>
  ) : (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none"
      viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M13.875 18.825A10.05 10.05 0 0112 19c-4.477 0-8.268-2.943-9.542-7
           a9.97 9.97 0 012.163-3.592M6.38 6.38A9.953 9.953 0 0112 5c4.477 0
           8.268 2.943 9.542 7a10.05 10.05 0 01-4.417 5.425M3 3l18 18" />
    </svg>
  )
}

// Spinner SVG
function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10"
        stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
    </svg>
  )
}

export default function Setup({ onSetupComplete }) {
  // Credential fields
  const [accessKeyId,     setAccessKeyId]     = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')
  const [showKeyId,       setShowKeyId]       = useState(false)
  const [showSecret,      setShowSecret]      = useState(false)

  // Advanced fields (collapsed by default)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [region,       setRegion]       = useState('us-east-1')
  const [bucket,       setBucket]       = useState('geobridge-data-backup')
  const [prefix,       setPrefix]       = useState('data/')

  // Wizard phase: 'entry' | 'testing' | 'tested_ok' | 'tested_fail' | 'saving' | 'done'
  const [phase,      setPhase]      = useState('entry')
  const [testError,  setTestError]  = useState('')
  const [saveResult, setSaveResult] = useState(null)  // { pulled, details }

  const credentialsEntered = accessKeyId.trim().length > 0 && secretAccessKey.trim().length > 0

  async function handleTest() {
    if (!credentialsEntered) return
    setPhase('testing')
    setTestError('')
    try {
      const r = await fetch(`${API}/api/s3/test-connection`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          accessKeyId:     accessKeyId.trim(),
          secretAccessKey: secretAccessKey.trim(),
          region,
          bucket,
          prefix,
        }),
      })
      const data = await r.json()
      if (data.ok) {
        setPhase('tested_ok')
      } else {
        setTestError(data.error || 'Connection failed')
        setPhase('tested_fail')
      }
    } catch (e) {
      setTestError(`Could not reach backend: ${e.message}`)
      setPhase('tested_fail')
    }
  }

  async function handleSave() {
    setPhase('saving')
    try {
      const r = await fetch(`${API}/api/s3/save-config`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          accessKeyId:     accessKeyId.trim(),
          secretAccessKey: secretAccessKey.trim(),
          region,
          bucket,
          prefix,
        }),
      })
      const data = await r.json()
      if (data.ok) {
        setSaveResult(data)
        setPhase('done')
        // Brief pause so the user sees the success screen, then proceed
        setTimeout(() => onSetupComplete(), 1800)
      } else {
        setTestError(data.error || 'Save failed')
        setPhase('tested_fail')
      }
    } catch (e) {
      setTestError(`Could not reach backend: ${e.message}`)
      setPhase('tested_fail')
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{
      minHeight:       '100vh',
      background:      '#0f172a',
      display:         'flex',
      alignItems:      'center',
      justifyContent:  'center',
      padding:         '24px',
      fontFamily:      "'Inter', system-ui, sans-serif",
    }}>

      <div style={{
        width:        '100%',
        maxWidth:     '480px',
        background:   '#1e293b',
        border:       '1px solid #334155',
        borderRadius: '16px',
        padding:      '36px 40px',
        boxShadow:    '0 25px 60px rgba(0,0,0,0.5)',
      }}>

        {/* ── Header ── */}
        <div style={{ marginBottom: '28px', textAlign: 'center' }}>
          <div style={{
            width:        '52px',
            height:       '52px',
            borderRadius: '12px',
            background:   'linear-gradient(135deg, #3b82f6, #1d4ed8)',
            display:      'flex',
            alignItems:   'center',
            justifyContent: 'center',
            fontSize:     '22px',
            fontWeight:   'bold',
            color:        'white',
            margin:       '0 auto 16px',
          }}>
            GB
          </div>
          <h1 style={{ fontSize: '20px', fontWeight: '700', color: '#f1f5f9', margin: '0 0 6px' }}>
            GeoBridge Setup
          </h1>
          {phase !== 'done' && (
            <p style={{ fontSize: '13px', color: '#64748b', margin: 0 }}>
              Enter your S3 credentials to sync shared data across all GeoBridge users.
            </p>
          )}
        </div>

        {/* ── DONE screen ── */}
        {phase === 'done' && (
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '48px', marginBottom: '16px' }}>✓</div>
            <h2 style={{ fontSize: '18px', fontWeight: '700', color: '#34d399', margin: '0 0 8px' }}>
              Connected!
            </h2>
            <p style={{ fontSize: '13px', color: '#64748b', margin: '0 0 16px' }}>
              {saveResult?.pulled > 0
                ? `Pulled ${saveResult.pulled} file${saveResult.pulled !== 1 ? 's' : ''} from S3.`
                : 'Configuration saved. Proceeding to login…'}
            </p>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', color: '#64748b', fontSize: '13px' }}>
              <Spinner /> Loading…
            </div>
          </div>
        )}

        {/* ── Entry / test / save form ── */}
        {phase !== 'done' && (
          <>
            {/* Admin note */}
            <div style={{
              background:   '#1e3a5f',
              border:       '1px solid #2563eb40',
              borderRadius: '10px',
              padding:      '12px 16px',
              marginBottom: '24px',
              fontSize:     '12px',
              color:        '#93c5fd',
              lineHeight:   '1.6',
            }}>
              <strong style={{ color: '#60a5fa' }}>Need credentials?</strong> Contact your administrator
              for the AWS Access Key ID and Secret Access Key. These are provided by whoever
              manages the shared GeoBridge data bucket.
            </div>

            {/* Access Key ID */}
            <div style={{ marginBottom: '16px' }}>
              <label style={labelStyle}>Access Key ID</label>
              <div style={inputWrapStyle}>
                <input
                  type={showKeyId ? 'text' : 'password'}
                  value={accessKeyId}
                  onChange={e => { setAccessKeyId(e.target.value); setPhase('entry') }}
                  placeholder="AKIA…"
                  autoComplete="new-password"
                  spellCheck={false}
                  style={inputStyle}
                />
                <button
                  type="button"
                  onClick={() => setShowKeyId(v => !v)}
                  style={eyeBtnStyle}
                  title={showKeyId ? 'Hide' : 'Show'}
                >
                  <EyeIcon open={showKeyId} />
                </button>
              </div>
            </div>

            {/* Secret Access Key */}
            <div style={{ marginBottom: '8px' }}>
              <label style={labelStyle}>Secret Access Key</label>
              <div style={inputWrapStyle}>
                <input
                  type={showSecret ? 'text' : 'password'}
                  value={secretAccessKey}
                  onChange={e => { setSecretAccessKey(e.target.value); setPhase('entry') }}
                  placeholder="••••••••••••••••••••••••••••••••••••••••"
                  autoComplete="new-password"
                  spellCheck={false}
                  style={inputStyle}
                />
                <button
                  type="button"
                  onClick={() => setShowSecret(v => !v)}
                  style={eyeBtnStyle}
                  title={showSecret ? 'Hide' : 'Show'}
                >
                  <EyeIcon open={showSecret} />
                </button>
              </div>
            </div>

            {/* Security note */}
            <p style={{ fontSize: '11px', color: '#475569', marginBottom: '20px', lineHeight: '1.5' }}>
              🔒 Credentials are saved to your local AppData folder and never
              visible in the UI again after setup.
            </p>

            {/* Advanced toggle */}
            <button
              type="button"
              onClick={() => setShowAdvanced(v => !v)}
              style={{
                background:   'transparent',
                border:       'none',
                color:        '#475569',
                fontSize:     '12px',
                cursor:       'pointer',
                padding:      '0 0 16px',
                display:      'flex',
                alignItems:   'center',
                gap:          '4px',
              }}
            >
              {showAdvanced ? '▲' : '▼'} Advanced options
            </button>

            {showAdvanced && (
              <div style={{
                background:   '#0f172a',
                border:       '1px solid #334155',
                borderRadius: '10px',
                padding:      '16px',
                marginBottom: '20px',
              }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                  <div>
                    <label style={labelStyle}>Region</label>
                    <input
                      value={region}
                      onChange={e => setRegion(e.target.value)}
                      style={plainInputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Bucket Name</label>
                    <input
                      value={bucket}
                      onChange={e => setBucket(e.target.value)}
                      style={plainInputStyle}
                    />
                  </div>
                  <div style={{ gridColumn: '1 / -1' }}>
                    <label style={labelStyle}>Key Prefix</label>
                    <input
                      value={prefix}
                      onChange={e => setPrefix(e.target.value)}
                      style={plainInputStyle}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Error banner */}
            {phase === 'tested_fail' && testError && (
              <div style={{
                background:   '#450a0a',
                border:       '1px solid #dc2626',
                borderRadius: '8px',
                padding:      '10px 14px',
                marginBottom: '16px',
                fontSize:     '13px',
                color:        '#fca5a5',
              }}>
                ✕ {testError}
              </div>
            )}

            {/* Success confirmation */}
            {phase === 'tested_ok' && (
              <div style={{
                background:   '#052e16',
                border:       '1px solid #16a34a',
                borderRadius: '8px',
                padding:      '10px 14px',
                marginBottom: '16px',
                fontSize:     '13px',
                color:        '#86efac',
              }}>
                ✓ Connection successful — credentials are valid
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>

              {/* Test connection — always visible until saved */}
              {phase !== 'saving' && (
                <button
                  type="button"
                  disabled={!credentialsEntered || phase === 'testing'}
                  onClick={handleTest}
                  style={{
                    ...btnStyle,
                    background: '#1e293b',
                    border:     '1px solid #475569',
                    color:      credentialsEntered ? '#cbd5e1' : '#475569',
                    cursor:     credentialsEntered ? 'pointer' : 'not-allowed',
                  }}
                >
                  {phase === 'testing'
                    ? <><Spinner /> Testing…</>
                    : 'Test Connection'
                  }
                </button>
              )}

              {/* Save — only enabled after a successful test */}
              <button
                type="button"
                disabled={phase !== 'tested_ok' && phase !== 'saving'}
                onClick={handleSave}
                style={{
                  ...btnStyle,
                  background: (phase === 'tested_ok' || phase === 'saving')
                    ? 'linear-gradient(135deg, #3b82f6, #1d4ed8)'
                    : '#0f172a',
                  color:      (phase === 'tested_ok' || phase === 'saving') ? '#fff' : '#334155',
                  border:     '1px solid transparent',
                  cursor:     (phase === 'tested_ok') ? 'pointer' : 'not-allowed',
                  fontWeight: '600',
                }}
              >
                {phase === 'saving'
                  ? <><Spinner /> Saving &amp; syncing…</>
                  : 'Save & Continue'
                }
              </button>

            </div>
          </>
        )}

      </div>
    </div>
  )
}

// ── Shared micro-styles ────────────────────────────────────────────────────────
const labelStyle = {
  display:      'block',
  fontSize:     '12px',
  fontWeight:   '500',
  color:        '#94a3b8',
  marginBottom: '6px',
}

const inputWrapStyle = {
  position: 'relative',
  display:  'flex',
  alignItems: 'center',
}

const inputStyle = {
  width:        '100%',
  background:   '#0f172a',
  border:       '1px solid #334155',
  borderRadius: '8px',
  padding:      '10px 40px 10px 14px',
  fontSize:     '13px',
  color:        '#f1f5f9',
  outline:      'none',
  fontFamily:   'monospace',
  letterSpacing: '0.05em',
  boxSizing:    'border-box',
}

const plainInputStyle = {
  width:        '100%',
  background:   '#1e293b',
  border:       '1px solid #334155',
  borderRadius: '8px',
  padding:      '8px 12px',
  fontSize:     '13px',
  color:        '#f1f5f9',
  outline:      'none',
  boxSizing:    'border-box',
}

const eyeBtnStyle = {
  position:   'absolute',
  right:      '10px',
  background: 'transparent',
  border:     'none',
  color:      '#475569',
  cursor:     'pointer',
  padding:    '4px',
  display:    'flex',
  alignItems: 'center',
}

const btnStyle = {
  width:        '100%',
  padding:      '11px 16px',
  borderRadius: '8px',
  fontSize:     '14px',
  display:      'flex',
  alignItems:   'center',
  justifyContent: 'center',
  gap:          '8px',
  transition:   'opacity 0.15s',
}

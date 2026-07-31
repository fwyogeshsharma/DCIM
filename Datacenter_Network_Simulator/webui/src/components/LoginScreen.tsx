import { useState } from 'react'
import { api, setToken, type ApiError } from '../api/client'

const Logo = () => (
  <svg width="34" height="34" viewBox="0 0 24 24" fill="none">
    {/* fill/stroke go through `style`, not the presentation attribute — SVG
        attributes are not parsed as CSS values, so var() never resolves there. */}
    <circle cx="5"  cy="6"  r="2.2" style={{ fill: 'var(--ok)' }} />
    <circle cx="19" cy="6"  r="2.2" style={{ fill: 'var(--brand)' }} />
    <circle cx="12" cy="18" r="2.2" style={{ fill: 'var(--brand-alt)' }} />
    <line x1="5"  y1="6"  x2="12" y2="18" strokeWidth="1.4" style={{ stroke: 'var(--line-dim)' }} />
    <line x1="19" y1="6"  x2="12" y2="18" strokeWidth="1.4" style={{ stroke: 'var(--line-dim)' }} />
    <line x1="5"  y1="6"  x2="19" y2="6"  strokeWidth="1.4" style={{ stroke: 'var(--line-dim)' }} />
  </svg>
)

export default function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!username || !password || busy) return
    setBusy(true)
    setError('')
    try {
      const { token } = await api.login(username, password)
      setToken(token)
      onSuccess()
    } catch (err) {
      const status = (err as ApiError)?.status
      setError(status === 401 ? 'Invalid username or password' : 'Login failed — is the server running?')
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'radial-gradient(circle at 50% 30%, var(--backdrop-glow) 0%, var(--backdrop) 70%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'system-ui, sans-serif',
    }}>
      <form onSubmit={submit} style={{
        width: 340,
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        boxShadow: '0 16px 48px rgba(0,0,0,0.7)',
        padding: '28px 28px 24px',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <Logo />
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', textAlign: 'center' }}>
            Datacenter Network Simulator
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            Sign in to continue
          </div>
        </div>

        <input
          type="text"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={e => setUsername(e.target.value)}
          placeholder="Username"
          style={{
            padding: '9px 11px', fontSize: 12,
            background: 'rgba(255,255,255,0.04)',
            border: `1px solid ${error ? 'var(--crit)' : 'var(--border)'}`,
            borderRadius: 5, color: 'var(--text)', outline: 'none',
          }}
        />

        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder="Password"
          style={{
            padding: '9px 11px', fontSize: 12,
            background: 'rgba(255,255,255,0.04)',
            border: `1px solid ${error ? 'var(--crit)' : 'var(--border)'}`,
            borderRadius: 5, color: 'var(--text)', outline: 'none',
            marginTop: -4,
          }}
        />

        {error && (
          <div style={{ fontSize: 11, color: 'var(--crit)', textAlign: 'center', marginTop: -6 }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          className="primary"
          disabled={busy || !username || !password}
          style={{ padding: '9px 0', fontSize: 12, fontWeight: 600, opacity: busy || !username || !password ? 0.6 : 1 }}
        >
          {busy ? 'Signing in…' : 'Sign In'}
        </button>
      </form>
    </div>
  )
}

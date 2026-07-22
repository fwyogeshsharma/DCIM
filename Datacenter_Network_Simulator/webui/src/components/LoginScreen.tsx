import { useState } from 'react'
import { api, setToken, type ApiError } from '../api/client'

const Logo = () => (
  <svg width="34" height="34" viewBox="0 0 24 24" fill="none">
    <circle cx="5"  cy="6"  r="2.2" fill="#5fb37e" />
    <circle cx="19" cy="6"  r="2.2" fill="#c78a2d" />
    <circle cx="12" cy="18" r="2.2" fill="#c07a3a" />
    <line x1="5"  y1="6"  x2="12" y2="18" stroke="#3d434c" strokeWidth="1.4" />
    <line x1="19" y1="6"  x2="12" y2="18" stroke="#3d434c" strokeWidth="1.4" />
    <line x1="5"  y1="6"  x2="19" y2="6"  stroke="#3d434c" strokeWidth="1.4" />
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
      background: 'radial-gradient(circle at 50% 30%, #1f2126 0%, #0e0f11 70%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'system-ui, sans-serif',
    }}>
      <form onSubmit={submit} style={{
        width: 340,
        background: 'var(--bg-card, #1f2126)',
        border: '1px solid var(--border, #272a30)',
        borderRadius: 8,
        boxShadow: '0 16px 48px rgba(0,0,0,0.7)',
        padding: '28px 28px 24px',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <Logo />
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text, #dcdbd7)', textAlign: 'center' }}>
            Datacenter Network Simulator
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #8b8d8c)' }}>
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
            border: `1px solid ${error ? 'var(--red, #d95d52)' : 'var(--border, #272a30)'}`,
            borderRadius: 5, color: 'var(--text, #dcdbd7)', outline: 'none',
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
            border: `1px solid ${error ? 'var(--red, #d95d52)' : 'var(--border, #272a30)'}`,
            borderRadius: 5, color: 'var(--text, #dcdbd7)', outline: 'none',
            marginTop: -4,
          }}
        />

        {error && (
          <div style={{ fontSize: 11, color: 'var(--red, #d95d52)', textAlign: 'center', marginTop: -6 }}>
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

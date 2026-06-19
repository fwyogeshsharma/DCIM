import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import { getToken } from '../api/client'

/**
 * Floor-plan / heatmap viewer, embedded as the self-contained
 * `floorplan_viewer.html` (served from webui/public). The viewer is plain
 * canvas + Three.js and polls the simulator for live thermal; we hand it the
 * API base + auth token via the URL hash (hash is never sent to the server),
 * and `live=1` so it auto-connects on open.
 */
export default function FloorPlanPage() {
  const { setActiveView } = useStore()

  const src = useMemo(() => {
    // Same base the API client uses: absolute in dev (Vite :5173 -> :8000),
    // same-origin '/api' in prod (FastAPI serves /web and /api together).
    const apiBase = (import.meta.env.DEV ? 'http://localhost:8000' : '') + '/api'
    const token = getToken() || ''
    const hash = `#api=${encodeURIComponent(apiBase)}&token=${encodeURIComponent(token)}&live=1`
    return `${import.meta.env.BASE_URL}floorplan_viewer.html${hash}`
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', background: '#0e1116' }}>
      <div style={{
        height: 36, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 12px', background: 'linear-gradient(180deg,#0b1628,#080f1e)',
        borderBottom: '1px solid var(--border)',
      }}>
        <button onClick={() => setActiveView('main')} style={{
          background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text)',
          borderRadius: 4, padding: '4px 10px', fontSize: 11, cursor: 'pointer',
        }}>← Back</button>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text)' }}>Floor Plan &amp; Heatmap</span>
      </div>
      <iframe
        title="Floor Plan"
        src={src}
        style={{ flex: 1, width: '100%', border: 'none', background: '#0e1116' }}
      />
    </div>
  )
}

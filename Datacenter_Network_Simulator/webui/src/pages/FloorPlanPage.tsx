import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import { getToken } from '../api/client'
import ProvisionDialog from '../components/ProvisionDialog'

/**
 * Floor-plan / heatmap viewer, embedded as the self-contained
 * `floorplan_viewer.html` (served from webui/public). The viewer is plain
 * canvas + Three.js and polls the simulator for live thermal; we hand it the
 * API base + auth token via the URL hash (hash is never sent to the server),
 * and `live=1` so it auto-connects on open.
 */
export default function FloorPlanPage() {
  const { setActiveView, provisionOpen, setProvisionOpen } = useStore()

  const src = useMemo(() => {
    // Same base the API client uses: absolute in dev (Vite :5173 -> :8000),
    // same-origin '/api' in prod (FastAPI serves /web and /api together).
    const apiBase = (import.meta.env.DEV ? 'http://localhost:8000' : '') + '/api'
    const token = getToken() || ''
    const hash = `#api=${encodeURIComponent(apiBase)}&token=${encodeURIComponent(token)}&live=1`
    // Cache-bust the static viewer: its floor-plan data is inlined at build time,
    // so a stale cached copy hides topology changes. Fresh query per mount.
    const bust = `?v=${Date.now()}`
    return `${import.meta.env.BASE_URL}floorplan_viewer.html${bust}${hash}`
  }, [])

  return (
    <div style={{ position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', background: '#131417' }}>
      <div style={{
        height: 36, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 12px', background: 'linear-gradient(180deg,#191b1f,#0e0f11)',
        borderBottom: '1px solid var(--border)',
      }}>
        <button onClick={() => setActiveView('main')} style={{
          background: 'var(--bg-hover)', border: '1px solid var(--border)', color: 'var(--text)',
          borderRadius: 4, padding: '4px 10px', fontSize: 11, cursor: 'pointer',
        }}>← Back</button>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text)' }}>Floor Plan &amp; Heatmap</span>
        <div style={{ flex: 1 }} />
        <button onClick={() => setProvisionOpen(true)} style={{
          background: 'var(--accent)', border: '1px solid var(--accent)', color: '#0e0f11',
          borderRadius: 4, padding: '4px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer',
        }}>＋ Provision Capacity</button>
      </div>
      <iframe
        title="Floor Plan"
        src={src}
        style={{ flex: 1, width: '100%', border: 'none', background: '#131417' }}
      />
      {provisionOpen && <ProvisionDialog onClose={() => setProvisionOpen(false)} />}
    </div>
  )
}

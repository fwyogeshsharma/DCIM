import { useState } from 'react'
import { api } from '../api/client'
import { useStore } from '../store/useStore'

interface Props { onClose: () => void }

const EXAMPLE = `DC1-SW1,switch,cisco,10.0.1.1,24
DC1-SW2,switch,cisco,10.0.1.2,24
DC1-RTR1,router,juniper,10.0.1.3,4
DC1-SRV1,server,generic,10.0.1.10,2`

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
}
const dialog: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 6, width: 520, boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
}

interface Row { name: string; device_type: string; vendor: string; ip: string; ifaces: number; error?: string }

function parseCSV(text: string): Row[] {
  return text.split('\n')
    .map(l => l.trim()).filter(Boolean)
    .map(line => {
      const parts = line.split(',').map(s => s.trim())
      return {
        name:        parts[0] || '',
        device_type: parts[1] || 'switch',
        vendor:      parts[2] || 'cisco',
        ip:          parts[3] || '',
        ifaces:      parseInt(parts[4]) || 4,
      }
    })
}

export default function BulkAddDeviceDialog({ onClose }: Props) {
  const { fetchGraph, fetchDevices } = useStore()
  const [csv,     setCsv]     = useState('')
  const [busy,    setBusy]    = useState(false)
  const [results, setResults] = useState<{ ok: string[]; fail: string[] } | null>(null)

  const rows = parseCSV(csv)

  async function submit() {
    if (rows.length === 0) return
    setBusy(true)
    const ok: string[] = [], fail: string[] = []
    for (const r of rows) {
      try {
        await api.addDevice({
          name:            r.name,
          device_type:     r.device_type,
          vendor:          r.vendor,
          ip_address:      r.ip,
          interface_count: r.ifaces,
          snmp_port:       161,
          gnmi_port:       50051,
        })
        ok.push(r.name)
      } catch (e: unknown) {
        fail.push(`${r.name}: ${String(e).slice(0, 80)}`)
      }
    }
    setBusy(false)
    setResults({ ok, fail })
    fetchGraph(); fetchDevices()
  }

  return (
    <div style={overlay} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div style={dialog}>
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0' }}>
          <span className="title">Bulk Add Devices</span>
          <button onClick={onClose} style={{ border: 'none', background: 'none', color: 'var(--text-muted)', fontSize: 16, cursor: 'pointer', padding: '0 4px' }}>✕</button>
        </div>

        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            One device per line: <code style={{ color: 'var(--text)' }}>name, type, vendor, ip, interface_count</code>
          </div>

          <textarea
            style={{
              width: '100%', height: 160,
              background: 'var(--bg-base)', border: '1px solid var(--border)',
              color: 'var(--text)', borderRadius: 4, padding: 8,
              fontFamily: 'monospace', fontSize: 11, resize: 'vertical',
              outline: 'none',
            }}
            value={csv}
            onChange={e => { setCsv(e.target.value); setResults(null) }}
            placeholder={EXAMPLE}
            spellCheck={false}
          />

          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
            {rows.length > 0 ? `${rows.length} device${rows.length > 1 ? 's' : ''} parsed` : 'Paste CSV above'}
          </div>

          {results && (
            <div style={{ fontSize: 10 }}>
              {results.ok.length > 0 && (
                <div style={{ color: 'var(--green)', marginBottom: 4 }}>
                  ✓ Added {results.ok.length}: {results.ok.join(', ')}
                </div>
              )}
              {results.fail.length > 0 && (
                <div style={{ color: 'var(--red)' }}>
                  {results.fail.map((f, i) => <div key={i}>✗ {f}</div>)}
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="primary" style={{ flex: 1 }}
              onClick={submit}
              disabled={busy || rows.length === 0}
            >
              {busy ? `Adding… (${rows.length})` : `Add ${rows.length} Device${rows.length !== 1 ? 's' : ''}`}
            </button>
            <button style={{ flex: 1 }} onClick={onClose}>
              {results ? 'Close' : 'Cancel'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

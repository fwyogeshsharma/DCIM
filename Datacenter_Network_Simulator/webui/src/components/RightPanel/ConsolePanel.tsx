import { useRef, useEffect, useState } from 'react'
import { useStore } from '../../store/useStore'
import type { LogEntry } from '../../api/types'

const LEVEL_COLOR: Record<string, string> = {
  info:    '#c4c3bf',
  success: '#5fb37e',
  warning: '#cbb04a',
  error:   '#d95d52',
}

const TABS: { id: LogEntry['tab']; label: string }[] = [
  { id: 'snmp',   label: 'SNMP Simulator' },
  { id: 'gnmi',   label: 'gNMI Simulation' },
  { id: 'sflow',  label: 'sFlow' },
  { id: 'bacnet', label: 'BACnet/IP' },
  { id: 'redfish', label: 'Redfish' },
]

function LogLine({ entry }: { entry: LogEntry }) {
  const col = LEVEL_COLOR[entry.level] || '#c4c3bf'
  const ts  = new Date(entry.ts).toLocaleTimeString()
  return (
    <div style={{
      fontFamily: 'monospace', fontSize: 10, color: col,
      borderBottom: '1px solid rgba(61, 67, 76,0.3)',
      padding: '2px 8px',
      lineHeight: 1.5,
    }}>
      <span style={{ color: 'var(--text-dim)', marginRight: 6 }}>{ts}</span>
      {entry.msg}
    </div>
  )
}

export default function ConsolePanel() {
  const { logs } = useStore()
  const [tab, setTab] = useState<LogEntry['tab']>('snmp')
  const bottomRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  const filtered = logs.filter(l => l.tab === tab)

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [filtered.length, autoScroll])

  function clearCurrent() {
    // logs cleared locally only (no API for selective clear)
    useStore.setState(s => ({
      logs: s.logs.filter(l => l.tab !== tab)
    }))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="panel-header">
        <span className="title">Console</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <label style={{ fontSize: 10, color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={autoScroll} onChange={e => setAutoScroll(e.target.checked)} />
            Auto-scroll
          </label>
          <button onClick={clearCurrent} style={{ fontSize: 10, padding: '2px 6px' }}>Clear</button>
        </div>
      </div>

      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)' }}>
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              flex: 1,
              borderRadius: 0,
              border: 'none',
              borderRight: '1px solid var(--border)',
              borderBottom: tab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
              background: tab === t.id ? 'var(--bg-selected)' : 'transparent',
              color: tab === t.id ? 'var(--text)' : 'var(--text-muted)',
              padding: '5px 4px',
              fontSize: 10,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', background: 'var(--bg-base)' }}>
        {filtered.map(e => <LogLine key={e.id} entry={e} />)}
        {filtered.length === 0 && (
          <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-dim)', fontSize: 10 }}>
            No log output
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

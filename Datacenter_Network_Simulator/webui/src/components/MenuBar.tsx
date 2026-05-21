import { useRef, useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { useStore } from '../store/useStore'
import AddDeviceDialog from './AddDeviceDialog'
import BulkAddDeviceDialog from './BulkAddDeviceDialog'

interface MenuItem {
  label: string
  shortcut?: string
  divider?: boolean
  submenu?: MenuItem[]
  action: () => void
  disabled?: boolean
  checked?: boolean
}

const MENU_STYLE: React.CSSProperties = {
  background: '#111827',
  border: '1px solid var(--border)',
  borderRadius: 4,
  minWidth: 210,
  boxShadow: '0 10px 28px rgba(0,0,0,0.7), 0 2px 6px rgba(0,0,0,0.4)',
  overflow: 'visible',
  padding: '4px 0',
}

// Small network-logo (three connected nodes)
const Logo = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
    <circle cx="5"  cy="6"  r="2.2" fill="#3fb950" />
    <circle cx="19" cy="6"  r="2.2" fill="#1e6ec8" />
    <circle cx="12" cy="18" r="2.2" fill="#db6d28" />
    <line x1="5"  y1="6"  x2="12" y2="18" stroke="#2d3f55" strokeWidth="1.4" />
    <line x1="19" y1="6"  x2="12" y2="18" stroke="#2d3f55" strokeWidth="1.4" />
    <line x1="5"  y1="6"  x2="19" y2="6"  stroke="#2d3f55" strokeWidth="1.4" />
  </svg>
)

function MenuItemRow({ item, onClose }: { item: MenuItem; onClose: () => void }) {
  const [subOpen, setSubOpen] = useState(false)

  if (item.divider) {
    return <div style={{ height: 1, background: 'var(--border)', margin: '4px 6px' }} />
  }

  const hasSubmenu = !!item.submenu?.length

  return (
    <div
      style={{ position: 'relative' }}
      onMouseEnter={() => hasSubmenu && setSubOpen(true)}
      onMouseLeave={() => hasSubmenu && setSubOpen(false)}
    >
      <div
        onClick={() => {
          if (item.disabled) return
          if (!hasSubmenu) { item.action(); onClose() }
        }}
        style={{
          padding: '6px 12px 6px 22px',
          fontSize: 11,
          color: item.disabled ? 'var(--text-dim)' : 'var(--text)',
          cursor: item.disabled ? 'not-allowed' : 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 24,
          background: subOpen ? 'var(--bg-selected)' : 'transparent',
          position: 'relative',
        }}
        onMouseEnter={e => { if (!subOpen && !item.disabled) e.currentTarget.style.background = 'var(--bg-selected)' }}
        onMouseLeave={e => { if (!subOpen) e.currentTarget.style.background = 'transparent' }}
      >
        {item.checked && (
          <span style={{
            position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
            color: 'var(--accent)', fontSize: 10,
          }}>✓</span>
        )}
        <span>{item.label}</span>
        <span style={{ color: 'var(--text-dim)', fontSize: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
          {item.shortcut && <span style={{ fontFamily: 'Consolas, monospace' }}>{item.shortcut}</span>}
          {hasSubmenu && <span>▶</span>}
        </span>
      </div>

      {hasSubmenu && subOpen && (
        <div style={{
          ...MENU_STYLE,
          position: 'absolute',
          top: -4,
          left: '100%',
          zIndex: 1100,
          marginLeft: 1,
        }}>
          {item.submenu!.map((sub, j) => <MenuItemRow key={j} item={sub} onClose={onClose} />)}
        </div>
      )}
    </div>
  )
}

function MenuButton({
  label, isOpen, badge, onOpen, onHover, items,
}: {
  label: string
  isOpen: boolean
  badge?: { text: string; color: string }
  onOpen: () => void
  onHover: () => void
  items: MenuItem[]
}) {
  return (
    <div style={{ position: 'relative', height: '100%' }}>
      <div
        onClick={onOpen}
        onMouseEnter={onHover}
        style={{
          padding: '0 12px',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          color: isOpen ? 'var(--text)' : 'var(--text-muted)',
          background: isOpen ? 'var(--bg-selected)' : 'transparent',
          cursor: 'pointer',
          borderBottom: isOpen ? '2px solid var(--accent)' : '2px solid transparent',
          transition: 'background 0.1s, color 0.1s',
        }}
        onMouseOver={e => { if (!isOpen) e.currentTarget.style.background = 'var(--bg-hover)' }}
        onMouseOut={e => { if (!isOpen) e.currentTarget.style.background = 'transparent' }}
      >
        <span>{label}</span>
        {badge && (
          <span style={{
            background: badge.color, color: '#fff',
            padding: '1px 5px', borderRadius: 3,
            fontSize: 8, fontWeight: 700, letterSpacing: '0.4px',
          }}>{badge.text}</span>
        )}
      </div>
      {isOpen && (
        <div style={{ ...MENU_STYLE, position: 'absolute', top: '100%', left: 0, zIndex: 1000 }}>
          {items.map((item, i) => <MenuItemRow key={i} item={item} onClose={() => onOpen()} />)}
        </div>
      )}
    </div>
  )
}

function StatusChip({ label, dot, value, color }: {
  label: string; dot?: string; value: string; color?: string
}) {
  return (
    <div
      title={label}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '2px 8px', height: 20,
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        fontSize: 10, color: color ?? 'var(--text)',
        fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap',
      }}
    >
      {dot && <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: dot, flexShrink: 0,
        boxShadow: dot !== 'var(--text-dim)' ? `0 0 4px ${dot}` : undefined,
      }} />}
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  )
}

export default function MenuBar() {
  const fileRef    = useRef<HTMLInputElement>(null)
  const menuBarRef = useRef<HTMLDivElement>(null)
  const [openMenu,  setOpenMenu]  = useState<string | null>(null)
  const [showAdd,   setShowAdd]   = useState(false)
  const [showBulk,  setShowBulk]  = useState(false)
  const [showAbout, setShowAbout] = useState(false)

  const {
    snmp, gnmi, sflow, devices, health,
    fetchGraph, fetchDevices, fetchSnmp, selectedDeviceId,
    linkMode, setLinkMode, triggerFitView, setLayoutAlgo, setRightTab,
  } = useStore()

  const closeAll = useCallback(() => setOpenMenu(null), [])

  // Outside-click closes menu
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuBarRef.current && !menuBarRef.current.contains(e.target as Node)) closeAll()
    }
    document.addEventListener('mousedown', handler, true)
    return () => document.removeEventListener('mousedown', handler, true)
  }, [closeAll])

  // Hover-to-switch when any menu is open
  function handleHover(name: string) {
    if (openMenu && openMenu !== name) setOpenMenu(name)
  }
  function toggleMenu(name: string) {
    setOpenMenu(prev => prev === name ? null : name)
  }

  const openFileDialog = useCallback(() => fileRef.current?.click(), [])

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    await api.uploadTopology(f)
    fetchGraph(); fetchDevices(); fetchSnmp()
    e.target.value = ''
  }

  function newTopology() {
    if (!confirm('Clear the current topology?')) return
    api.clearTopology().then(() => { fetchGraph(); fetchDevices() }).catch(console.error)
  }
  function closeTopology() {
    if (!confirm('Close the current topology?')) return
    api.clearTopology().then(() => { fetchGraph(); fetchDevices() }).catch(console.error)
  }
  async function saveTopology() {
    try {
      const data = await api.exportTopology()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = 'topology.json'
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { console.error(e) }
  }
  async function exportJson() {
    try {
      const data = await api.exportTopology()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `topology-export-${new Date().toISOString().slice(0,10)}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) { console.error(e) }
  }
  async function removeSelected() {
    if (!selectedDeviceId) return
    if (!confirm(`Remove device ${selectedDeviceId}?`)) return
    try {
      await api.delDevice(selectedDeviceId)
      fetchGraph(); fetchDevices()
    } catch (e) { console.error(e) }
  }

  // Keyboard shortcuts
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) return
      const ctrl = e.ctrlKey || e.metaKey
      if (!ctrl) return
      const k = e.key.toLowerCase()
      if (ctrl && e.shiftKey && k === 'f') { e.preventDefault(); triggerFitView() }
      else if (ctrl && e.shiftKey && k === 'd') { e.preventDefault(); setLayoutAlgo('default') }
      else if (ctrl && k === 'n') { e.preventDefault(); newTopology() }
      else if (ctrl && k === 'o') { e.preventDefault(); openFileDialog() }
      else if (ctrl && k === 's') { e.preventDefault(); saveTopology() }
      else if (ctrl && k === 'l') { e.preventDefault(); setLinkMode(!linkMode) }
      else if (ctrl && k === 'f') { e.preventDefault(); setLayoutAlgo('spring') }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [linkMode, setLinkMode, triggerFitView, setLayoutAlgo, openFileDialog])

  const fileMenuItems: MenuItem[] = [
    { label: 'New Topology',    shortcut: 'Ctrl+N', action: newTopology    },
    { label: 'Open Topology…',  shortcut: 'Ctrl+O', action: openFileDialog },
    { label: 'Save Topology',   shortcut: 'Ctrl+S', action: saveTopology   },
    { label: '',                divider: true,      action: () => {}       },
    { label: 'Close Topology',                       action: closeTopology },
    { label: '',                divider: true,      action: () => {}       },
    { label: 'Export as JSON',                       action: exportJson    },
  ]
  const devMenuItems: MenuItem[] = [
    { label: 'Add Device',        action: () => setShowAdd(true)  },
    { label: 'Bulk Add Devices',  action: () => setShowBulk(true) },
    { label: '', divider: true, action: () => {} },
    { label: 'Remove Selected', action: removeSelected, disabled: !selectedDeviceId },
  ]
  const topoMenuItems: MenuItem[] = [
    { label: 'Link Mode', shortcut: 'Ctrl+L', checked: linkMode,
      action: () => setLinkMode(!linkMode) },
    { label: '', divider: true, action: () => {} },
    { label: 'Fit View', shortcut: 'Ctrl+Shift+F', action: () => triggerFitView() },
    { label: '', divider: true, action: () => {} },
    {
      label: 'Layouts', action: () => {},
      submenu: [
        { label: 'Default Layout',      shortcut: 'Ctrl+Shift+D', action: () => setLayoutAlgo('default') },
        { label: 'Spring Layout',       shortcut: 'Ctrl+F',       action: () => setLayoutAlgo('spring') },
        { label: 'Shell Layout',                                  action: () => setLayoutAlgo('shell') },
        { label: 'Kamada-Kawai Layout',                           action: () => setLayoutAlgo('kamada_kawai') },
      ],
    },
  ]
  const simMenuItems: MenuItem[] = [
    { label: 'SNMP Simulator',  action: () => setRightTab('snmp')  },
    { label: 'gNMI Simulator',  action: () => setRightTab('gnmi')  },
    { label: 'sFlow Simulator', action: () => setRightTab('sflow') },
    { label: '', divider: true, action: () => {} },
    { label: 'SNMP Traps',      action: () => setRightTab('traps') },
    { label: 'Bindings',        action: () => setRightTab('binding') },
  ]
  const helpMenuItems: MenuItem[] = [
    { label: 'About', action: () => setShowAbout(true) },
  ]

  const menus: { name: string; label: string; items: MenuItem[]; badge?: { text: string; color: string } }[] = [
    { name: 'file', label: 'File',     items: fileMenuItems },
    { name: 'dev',  label: 'Devices',  items: devMenuItems  },
    { name: 'topo', label: 'Topology', items: topoMenuItems,
      badge: linkMode ? { text: 'LINK', color: '#db6d28' } : undefined },
    { name: 'sim',  label: 'Simulation', items: simMenuItems },
    { name: 'help', label: 'Help',     items: helpMenuItems },
  ]

  return (
    <div ref={menuBarRef} style={{
      height: 32,
      background: 'linear-gradient(180deg, #0c1220 0%, #0a0f1a 100%)',
      borderBottom: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      flexShrink: 0,
      userSelect: 'none',
    }}>
      {/* ── Title block ──────────────────────────────────────── */}
      <div style={{
        padding: '0 12px', height: '100%',
        display: 'flex', alignItems: 'center', gap: 8,
        borderRight: '1px solid var(--border)', flexShrink: 0,
      }}>
        <Logo />
        <span style={{
          fontWeight: 700, fontSize: 11, color: 'var(--text)',
          letterSpacing: '0.3px',
        }}>
          Datacenter Network Simulator
        </span>
        <span style={{
          padding: '1px 6px', borderRadius: 3,
          background: 'rgba(30,110,200,0.18)',
          border: '1px solid rgba(30,110,200,0.4)',
          color: '#93c5fd',
          fontSize: 9, fontWeight: 700,
          fontFamily: 'Consolas, monospace',
          letterSpacing: '0.3px',
        }}>v2.2</span>
      </div>

      {/* ── Menu strip ───────────────────────────────────────── */}
      {menus.map(m => (
        <MenuButton
          key={m.name}
          label={m.label}
          isOpen={openMenu === m.name}
          badge={m.badge}
          onOpen={() => toggleMenu(m.name)}
          onHover={() => handleHover(m.name)}
          items={m.items}
        />
      ))}

      {/* ── Right-side status chips ──────────────────────────── */}
      <div style={{ flex: 1 }} />
      <div style={{
        display: 'flex', gap: 5, alignItems: 'center',
        padding: '0 10px', height: '100%',
      }}>
        <StatusChip
          label="Devices"
          dot={devices.length > 0 ? 'var(--accent)' : 'var(--text-dim)'}
          value={String(devices.length)}
        />
        <StatusChip
          label="SNMP"
          dot={snmp?.running ? 'var(--green)' : 'var(--text-dim)'}
          value={snmp?.running ? 'running' : snmp?.datasets_generated ? 'ready' : 'idle'}
          color={snmp?.running ? 'var(--green)' : undefined}
        />
        <StatusChip
          label="gNMI"
          dot={gnmi?.running ? 'var(--green)' : 'var(--text-dim)'}
          value={gnmi?.running ? 'running' : gnmi?.datasets_generated ? 'ready' : 'idle'}
          color={gnmi?.running ? 'var(--green)' : undefined}
        />
        <StatusChip
          label="sFlow"
          dot={sflow?.running ? 'var(--green)' : 'var(--text-dim)'}
          value={sflow?.running ? 'running' : 'idle'}
          color={sflow?.running ? 'var(--green)' : undefined}
        />
        <div
          title={`API ${health?.status === 'ok' ? 'connected' : 'disconnected'}`}
          style={{
            width: 8, height: 8, borderRadius: '50%',
            background: health?.status === 'ok' ? 'var(--green)' : 'var(--red)',
            boxShadow: health?.status === 'ok'
              ? '0 0 6px var(--green)'
              : '0 0 6px var(--red)',
            marginLeft: 4,
          }}
        />
      </div>

      {showAbout && <AboutDialog onClose={() => setShowAbout(false)} />}

      <input
        ref={fileRef}
        type="file"
        accept=".json"
        style={{ display: 'none' }}
        onChange={onFileChange}
      />

      {showAdd  && <AddDeviceDialog     onClose={() => setShowAdd(false)}  />}
      {showBulk && <BulkAddDeviceDialog onClose={() => setShowBulk(false)} />}
    </div>
  )
}

function AboutDialog({ onClose }: { onClose: () => void }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000,
      backdropFilter: 'blur(2px)',
    }} onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)',
        borderRadius: 6, width: 460, boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
      }}>
        <div className="panel-header" style={{ borderRadius: '6px 6px 0 0' }}>
          <span className="title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Logo />
            About
          </span>
          <button onClick={onClose} style={{
            border: 'none', background: 'none', color: 'var(--text-muted)',
            fontSize: 16, cursor: 'pointer', padding: '0 4px',
          }}>✕</button>
        </div>

        <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)' }}>
              Datacenter Network Simulator
            </span>
            <span style={{
              padding: '1px 6px', borderRadius: 3,
              background: 'rgba(30,110,200,0.18)',
              border: '1px solid rgba(30,110,200,0.4)',
              color: '#93c5fd', fontSize: 9, fontWeight: 700,
              fontFamily: 'Consolas, monospace',
            }}>v2.2</span>
          </div>

          <div style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.7 }}>
            Visually build network topologies and simulate both SNMP and gNMI protocols
            for routers, switches, and servers.
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 11 }}>
            {[
              ['Tech Stack',    'Python 3.11+, PySide6, NetworkX, SNMPSim, gRPC'],
              ['Supports',      'Routers, Switches, Servers, Firewalls, PDUs, UPS, Sensors'],
              ['SNMP Versions', 'v1, v2c'],
              ['gNMI',          'OpenConfig — Interfaces, LLDP, BGP, OSPF, AFT, System'],
              ['Telemetry',     'gNMI Subscribe STREAM / ONCE / POLL'],
              ['sFlow',         'v5 UDP datagrams — counter + flow samples'],
              ['Web UI',        'React 18, TypeScript, Vite, Zustand, React Flow'],
            ].map(([label, value]) => (
              <div key={label} style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: 'var(--text-muted)', width: 110, flexShrink: 0, textAlign: 'right' }}>{label}:</span>
                <span style={{ color: 'var(--text)' }}>{value}</span>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
            <button className="primary" onClick={onClose} style={{ minWidth: 80 }}>OK</button>
          </div>
        </div>
      </div>
    </div>
  )
}

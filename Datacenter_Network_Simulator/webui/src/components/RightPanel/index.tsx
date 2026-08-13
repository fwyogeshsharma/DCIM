import { useState, useRef, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import BindingPanel from './BindingPanel'
import SNMPPanel    from './SNMPPanel'
import GNMIPanel    from './GNMIPanel'
import SFlowPanel   from './SFlowPanel'
import BACnetPanel  from './BACnetPanel'
import RedfishPanel from './RedfishPanel'
import ModbusPanel  from './ModbusPanel'
import TrapsPanel   from './TrapsPanel'
import RulesPanel   from './RulesPanel'
import ConsolePanel from './ConsolePanel'
import TickPanel    from './TickPanel'
import FleetPanel   from './FleetPanel'

const MIN_WIDTH     = 200
const MAX_WIDTH     = 900
const DEFAULT_WIDTH = 320
const STORAGE_KEY   = 'dcim:right-panel-width'

interface Tab {
  id:           string
  icon:         string
  iconSrc?:     string    // if set, renders <img> instead of emoji
  iconWhiteBg?: boolean   // true = logo has white bg; wrap in white pill
  iconInvert?:  boolean   // true = dark icon on transparent bg; apply invert(1) for dark theme
  iconNoFilter?: boolean  // true = full-colour brand logo; render it untouched.
                          // The default contrast(2)/brightness() punch is tuned for
                          // flat monochrome glyphs — on a colour logo it collapses
                          // distinct hues into one (the Modbus orange disc merges
                          // into its yellow dots and the blue M washes out).
  iconSize?:    number    // override rendered px size (default 22)
  iconColor?:   string    // override inactive text color
  label:        string
  component:    React.ReactNode
}

const TABS: Tab[] = [
  { id: 'binding', icon: '🔗',  label: 'Binding',      component: <BindingPanel /> },
  { id: 'snmp',    icon: '🖥️',  iconSrc: '/assets/icons/snmp.png', iconInvert: true, label: 'SNMP', component: <SNMPPanel /> },
  { id: 'gnmi',    icon: '📡',  iconSrc: '/assets/icons/gnmi.svg', label: 'gNMI', component: <GNMIPanel /> },
  { id: 'sflow', icon: '📶', iconSrc: '/assets/icons/sflow.png', iconInvert: true, label: 'sFlow', component: <SFlowPanel /> },
  { id: 'bacnet',  icon: '🔋',  iconSrc: '/assets/icons/bacnet.png', label: 'BACnet/IP', component: <BACnetPanel /> },
  { id: 'redfish', icon: '🖧',  iconSrc: '/assets/icons/redfish.png', label: 'Redfish', component: <RedfishPanel /> },
  // The full Modbus lockup, not a crop: the mark and the wordmark overlap in the
  // source art (the M is drawn over the orange disc), so every attempt to cut the
  // mark out either sliced the disc or truncated the M.
  // iconSize 38 because the art is 3.14:1 — objectFit:contain fits it to WIDTH, so
  // a 38px box yields a 38x12 logo inside the 44x44 button. The default 26 would
  // render it 8px tall and illegible; going past ~40 would touch the button edge.
  // iconNoFilter: the rail's default contrast(2)/brightness() punch is for flat
  // monochrome glyphs and collapses this logo's orange disc into its yellow dots.
  { id: 'modbus',  icon: '🔌',  iconSrc: '/assets/icons/modbus.png', iconNoFilter: true, iconSize: 38, label: 'Modbus/TCP', component: <ModbusPanel /> },
  { id: 'traps',   icon: '⚡',  label: 'Traps',        component: <TrapsPanel />   },
  { id: 'rules',   icon: '⚙️',  iconSrc: '/assets/icons/rules.png', iconInvert: true, iconSize: 20, label: 'Rules', component: <RulesPanel /> },
  { id: 'tick',    icon: '🔃',  iconSrc: '/assets/icons/tick.png', iconInvert: true, iconSize: 20, label: 'Metrics Tick', component: <TickPanel />    },
  { id: 'fleet',   icon: '📈',  label: 'Fleet Lifecycle', component: <FleetPanel /> },
  { id: 'console', icon: '>_',  iconColor: 'var(--text)', label: 'Console', component: <ConsolePanel /> },
]

function TabButton({
  tab, active, onClick, badge,
}: { tab: Tab; active: boolean; onClick: () => void; badge?: string }) {
  return (
    <button
      title={tab.label}
      onClick={onClick}
      style={{
        position: 'relative',
        width: 44, height: 44,
        border: 'none', borderRadius: 7,
        background: active ? 'var(--bg-selected)' : 'transparent',
        color: active ? 'var(--text)' : (tab.iconColor ?? 'var(--text-dim)'),
        fontSize: tab.icon === '>_' ? 13 : 21,
        fontFamily: tab.icon === '>_' ? 'Consolas, monospace' : 'inherit',
        fontWeight: tab.icon === '>_' ? 700 : 400,
        cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background 0.15s, color 0.15s',
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = 'var(--bg-hover)' }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      {active && (
        <span style={{
          position: 'absolute', left: 0, top: 6, bottom: 6,
          width: 2, borderRadius: 1,
          background: 'var(--accent)',
        }} />
      )}
      {tab.iconSrc
        ? tab.iconWhiteBg
          ? <div style={{
              width: tab.iconSize ?? 30, height: tab.iconSize ?? 30,
              borderRadius: 5, overflow: 'hidden',
              background: 'var(--on-solid)', padding: 2,
              opacity: active ? 1 : 0.82,
              boxShadow: active ? '0 0 0 1.5px var(--accent)' : '0 0 0 1px rgba(255,255,255,0.18)',
              flexShrink: 0,
            }}>
              <img src={tab.iconSrc} alt={tab.label}
                style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }} />
            </div>
          : <img src={tab.iconSrc} alt={tab.label}
              style={{
                width:  tab.iconSize ?? 26,
                height: tab.iconSize ?? 26,
                objectFit: 'contain',
                opacity: active ? 1 : 0.93,
                filter: tab.iconNoFilter
                  ? active
                    ? 'drop-shadow(0 0 2px rgba(255,255,255,0.25))'
                    : 'none'
                  : tab.iconInvert
                    ? active
                      ? 'invert(1) hue-rotate(180deg) brightness(1.1)'
                      : 'invert(1) hue-rotate(180deg) brightness(1.4)'
                    : active
                      ? 'contrast(2) brightness(1.5) drop-shadow(0 0 2px rgba(255,255,255,0.2))'
                      : 'contrast(2) brightness(1.8)',
              }} />
        : tab.icon
      }
      {badge && (
        <span
          title={badge}
          style={{
            position: 'absolute', top: 6, right: 6,
            width: 7, height: 7, borderRadius: '50%',
            background: 'var(--green)',
            boxShadow: '0 0 4px var(--green)',
          }}
        />
      )}
    </button>
  )
}

export default function RightPanel() {
  const { rightTab, setRightTab, snmp, gnmi, sflow, bacnet, redfish, traps, binding } = useStore()
  const [panelOpen, setPanelOpen] = useState(true)
  const [width, setWidth] = useState<number>(() => {
    const saved = parseInt(localStorage.getItem(STORAGE_KEY) || '', 10)
    return Number.isFinite(saved) && saved >= MIN_WIDTH && saved <= MAX_WIDTH
      ? saved
      : DEFAULT_WIDTH
  })
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  useEffect(() => {
    function onMove(e: MouseEvent) {
      if (!dragRef.current) return
      const dx = dragRef.current.startX - e.clientX
      const next = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, dragRef.current.startW + dx))
      setWidth(next)
    }
    function onUp() {
      if (dragRef.current) {
        dragRef.current = null
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        localStorage.setItem(STORAGE_KEY, String(width))
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [width])

  function startDrag(e: React.MouseEvent) {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: width }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  const active = TABS.find(t => t.id === rightTab) ?? TABS[0]

  function handleTabClick(id: string) {
    if (id === rightTab && panelOpen) {
      setPanelOpen(false)
    } else {
      setRightTab(id)
      setPanelOpen(true)
    }
  }

  const badges: Record<string, string | undefined> = {
    binding: (binding?.bound_count ?? 0) > 0 ? `${binding!.bound_count} bound` : undefined,
    snmp:    snmp?.running   ? 'SNMP running'   : undefined,
    gnmi:    gnmi?.running   ? 'gNMI running'   : undefined,
    sflow:   sflow?.running  ? 'sFlow running'  : undefined,
    bacnet:  bacnet?.running ? 'BACnet running' : undefined,
    redfish: redfish?.running ? 'Redfish running' : undefined,
    traps:   snmp?.autonomous_faults ? 'Autonomous faults on' : undefined,
  }

  return (
    <div style={{
      display: 'flex',
      flexShrink: 0,
      height: '100%',
      overflow: 'hidden',
    }}>
      {panelOpen && (
        <div style={{
          width,
          background: 'var(--bg-panel)',
          borderLeft: '1px solid var(--border)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          position: 'relative',
        }}>
          <div style={{ flex: 1, overflow: 'hidden', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            {active.component}
          </div>

          {/* Resize handle on left edge */}
          <div
            onMouseDown={startDrag}
            onDoubleClick={() => { setWidth(DEFAULT_WIDTH); localStorage.setItem(STORAGE_KEY, String(DEFAULT_WIDTH)) }}
            title="Drag to resize · double-click to reset"
            style={{
              position: 'absolute',
              top: 0, left: -3, bottom: 0,
              width: 6, cursor: 'col-resize',
              zIndex: 10,
            }}
            className="resize-handle"
          />
        </div>
      )}

      {/* Icon tab strip */}
      <div style={{
        width: 54,
        background: 'linear-gradient(180deg, var(--chrome-a) 0%, var(--chrome-b) 100%)',
        borderLeft: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '10px 0',
        gap: 4,
      }}>
        {TABS.slice(0, 6).map(t => (
          <TabButton
            key={t.id}
            tab={t}
            active={rightTab === t.id && panelOpen}
            onClick={() => handleTabClick(t.id)}
            badge={badges[t.id]}
          />
        ))}

        <div style={{ flex: 1 }} />
        <div style={{
          width: 28, height: 1,
          background: 'var(--border)',
          margin: '6px 0',
        }} />

        {TABS.slice(6).map(t => (
          <TabButton
            key={t.id}
            tab={t}
            active={rightTab === t.id && panelOpen}
            onClick={() => handleTabClick(t.id)}
            badge={badges[t.id]}
          />
        ))}
      </div>
    </div>
  )
}

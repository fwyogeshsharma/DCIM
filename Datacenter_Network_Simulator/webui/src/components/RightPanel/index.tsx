import { useState, useRef, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import BindingPanel from './BindingPanel'
import SNMPPanel    from './SNMPPanel'
import GNMIPanel    from './GNMIPanel'
import SFlowPanel   from './SFlowPanel'
import BACnetPanel  from './BACnetPanel'
import TrapsPanel   from './TrapsPanel'
import RulesPanel   from './RulesPanel'
import ConsolePanel from './ConsolePanel'
import TickPanel    from './TickPanel'

const MIN_WIDTH     = 200
const MAX_WIDTH     = 900
const DEFAULT_WIDTH = 320
const STORAGE_KEY   = 'dcim:right-panel-width'

interface Tab {
  id: string
  icon: string
  label: string
  component: React.ReactNode
}

const TABS: Tab[] = [
  { id: 'binding', icon: '🔗',  label: 'Binding',      component: <BindingPanel /> },
  { id: 'snmp',    icon: '🖥️',  label: 'SNMP',         component: <SNMPPanel />    },
  { id: 'gnmi',    icon: '📡',  label: 'gNMI',         component: <GNMIPanel />    },
  { id: 'sflow',   icon: '📶',  label: 'sFlow',        component: <SFlowPanel />   },
  { id: 'bacnet',  icon: '🔋',  label: 'BACnet/IP',    component: <BACnetPanel />  },
  { id: 'traps',   icon: '⚡',  label: 'Traps',        component: <TrapsPanel />   },
  { id: 'rules',   icon: '⚙️',  label: 'Rules',        component: <RulesPanel />   },
  { id: 'tick',    icon: '🔃',   label: 'Metrics Tick', component: <TickPanel />    },
  { id: 'console', icon: '>_',  label: 'Console',      component: <ConsolePanel /> },
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
        width: 40, height: 40,
        border: 'none', borderRadius: 4,
        background: active ? 'var(--bg-selected)' : 'transparent',
        color: active ? 'var(--text)' : 'var(--text-dim)',
        fontSize: tab.icon === '>_' ? 11 : 18,
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
      {tab.icon}
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
  const { rightTab, setRightTab, snmp, gnmi, sflow, bacnet, traps, binding } = useStore()
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
      // Dragging left edge: moving left increases width
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
    traps:   traps.length > 0 ? `${traps.length} traps` : undefined,
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
        width: 44,
        background: 'linear-gradient(180deg, #0c1220 0%, #0a0f1a 100%)',
        borderLeft: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '6px 0',
        gap: 2,
      }}>
        {TABS.slice(0, 5).map(t => (
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
          width: 22, height: 1,
          background: 'var(--border)',
          margin: '4px 0',
        }} />

        {TABS.slice(5).map(t => (
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

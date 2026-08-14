import { useEffect } from 'react'
import ConsolePanel from './RightPanel/ConsolePanel'

/**
 * The simulator console, as a modal rather than a sidebar tab.
 *
 * It moved out of the right-hand rail because it is not a control surface like
 * the protocol panels — it is a log you open when something needs reading, and
 * it wants far more width than a 320px sidebar gives a monospace line. Opened
 * from Simulation ▸ Console.
 *
 * ConsolePanel is reused unchanged: it is already `height: 100%` flex, so it
 * fills whatever box it is given.
 */
export default function ConsoleModal({ onClose }: { onClose: () => void }) {
  // Escape closes, matching every other dialog in the app.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div style={overlay} onClick={onClose}>
      {/* Stop clicks inside the dialog from reaching the overlay's close. */}
      <div style={dialog} onClick={e => e.stopPropagation()}>
        <div style={header}>
          <span style={{ fontSize: 11, color: 'var(--text)' }}>Console</span>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{
              border: 'none', background: 'transparent', cursor: 'pointer',
              color: 'var(--text-muted)', fontSize: 16, lineHeight: 1,
              padding: '0 2px',
            }}
          >×</button>
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <ConsolePanel />
        </div>
      </div>
    </div>
  )
}

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2000,
}

// Wide and tall on purpose: log lines are the content, and wrapping them is
// what made the sidebar version hard to read.
const dialog: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 6, width: 'min(1100px, 92vw)', height: 'min(680px, 85vh)',
  display: 'flex', flexDirection: 'column',
  boxShadow: '0 16px 48px rgba(0,0,0,0.8)',
  overflow: 'hidden',
}

const header: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '8px 10px', borderBottom: '1px solid var(--border)',
  flexShrink: 0,
}

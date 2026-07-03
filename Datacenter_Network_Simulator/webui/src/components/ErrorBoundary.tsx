import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

// Catches any uncaught render error in the subtree. Without this, React 18
// unmounts the ENTIRE tree on a single throw, leaving a blank page (only the
// body background shows). A late-session bad render — a churned-in device with
// a null field, a metric that formats undefined — would otherwise white out the
// whole app. Here it degrades to a recoverable fallback instead.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the technical detail in the console for developers.
    console.error('[ui] uncaught render error:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={{
        position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 14,
        background: '#060b16', color: '#c9d5e6', fontSize: 13,
        fontFamily: 'system-ui, sans-serif', padding: 24, textAlign: 'center',
      }}>
        <div style={{ fontSize: 15, fontWeight: 600 }}>Something went wrong.</div>
        <div style={{ color: '#8aa0bd', maxWidth: 420, lineHeight: 1.5 }}>
          The interface hit an unexpected error. Your simulator is still running —
          reloading the page will reconnect.
        </div>
        <button
          onClick={() => window.location.reload()}
          style={{
            marginTop: 6, padding: '8px 18px', fontSize: 13, fontWeight: 600,
            cursor: 'pointer', borderRadius: 6, border: '1px solid #1f6feb',
            background: '#1f6feb', color: '#fff',
          }}
        >
          Reload
        </button>
      </div>
    )
  }
}

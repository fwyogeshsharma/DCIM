import { useState, useEffect, useRef } from 'react'
import { api, errorMessage } from '../../api/client'
import { useStore } from '../../store/useStore'
import TargetsBox from './TargetsBox'
import NumberInput from '../NumberInput'

function formatUptime(connectedAt: number): string {
  if (!connectedAt) return '—'
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - connectedAt))
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return m > 0 ? `${m}m${String(s).padStart(2, '0')}s` : `${s}s`
}

async function pollJob(
  id: string,
  onMsg: (m: string) => void,
  onProg?: (d: number, t: number) => void,
) {
  for (let i = 0; i < 600; i++) {
    const j = await api.job(id) as {
      status: string; message: string; error: string
      progress_done: number; progress_total: number
    }
    onMsg(j.message || j.status)
    if (onProg && j.progress_total > 0) onProg(j.progress_done, j.progress_total)
    if (j.status === 'completed') return
    if (j.status === 'failed') throw new Error(j.error || 'Job failed')
    await new Promise(r => setTimeout(r, 400))
  }
}

const IconGenerate = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
    <path d="M16 16h5v5" />
  </svg>
)
const IconPlay = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
    <polygon points="6 4 20 12 6 20 6 4" />
  </svg>
)
const IconStop = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
    <rect x="6" y="6" width="12" height="12" rx="1" />
  </svg>
)
const IconTrash = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
  </svg>
)
const IconProxy = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="20" height="14" rx="2" />
    <line x1="8" y1="21" x2="16" y2="21" />
    <line x1="12" y1="17" x2="12" y2="21" />
  </svg>
)

function StatRow({ label, value, labelColor, valueColor }: {
  label: string; value: number | string
  labelColor?: string; valueColor?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, lineHeight: 1.85 }}>
      <span style={{ color: labelColor ?? 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: valueColor ?? 'var(--text)' }}>{value}</span>
    </div>
  )
}

export default function GNMIPanel() {
  const { gnmi, fetchGnmi, devices, binding, gnmiPort, setGnmiPort,
          gnmiProxyPort, setGnmiProxyPort } = useStore()
  const [busy,      setBusy]      = useState(false)
  const [operation, setOperation] = useState<'generate' | 'start' | 'stop' | 'clear' | 'proxy' | null>(null)
  const [prog,      setProg]      = useState<[number, number] | null>(null)
  // Why the last start was refused. The API names the unbound IPs; swallowing
  // that left Start looking like a no-op with the status stuck on "Idle".
  const [err,       setErr]       = useState<string | null>(null)
  const resumedJob = useRef<string | null>(null)

  const running       = gnmi?.running            ?? false
  const proxyOn       = gnmi?.proxy_running      ?? false
  const hasData       = gnmi?.datasets_generated ?? false
  const port          = gnmi?.port               ?? 50051
  const datasetCount  = gnmi?.dataset_count      ?? 0
  const clients       = gnmi?.clients            ?? []

  // Switch/router count for generate (gNMI only serves these)
  const gnmiCapable = devices.filter(d => d.device_type === 'switch' || d.device_type === 'router').length

  // Resume in-progress job if panel was closed mid-operation
  useEffect(() => {
    const activeJob = gnmi?.active_job_id
    if (!activeJob || resumedJob.current === activeJob || busy) return
    resumedJob.current = activeJob
    api.job(activeJob).then((j: unknown) => {
      const job = j as { operation: string; status: string }
      if (job.status !== 'running') { fetchGnmi(); return }
      const op: 'generate' | 'start' = job.operation === 'generate_gnmi_datasets' ? 'generate' : 'start'
      setBusy(true); setOperation(op)
      if (op === 'generate') setProg([0, gnmiCapable || 1])
      pollJob(activeJob, () => {}, (d, t) => setProg([d, t]))
        .catch(() => {})
        .finally(() => { setBusy(false); setOperation(null); setProg(null); fetchGnmi() })
    }).catch(() => {})
  }, [gnmi?.active_job_id])

  // Per-type counts (full picture in Active Devices section)
  const tc: Record<string, number> = {}
  for (const d of devices) tc[d.device_type] = (tc[d.device_type] || 0) + 1

  // Header badge
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (operation === 'generate')   badge = { cls: 'ready',   dot: 'yellow', text: 'Generating' }
  else if (operation === 'start') badge = { cls: 'ready',   dot: 'yellow', text: 'Starting' }
  else if (operation === 'stop')  badge = { cls: 'ready',   dot: 'yellow', text: 'Stopping' }
  else if (operation === 'clear') badge = { cls: 'ready',   dot: 'yellow', text: 'Clearing' }
  else if (operation === 'proxy') badge = { cls: 'ready',   dot: 'yellow', text: 'Proxy…' }
  else if (running && proxyOn)    badge = { cls: 'running', dot: 'green',  text: 'Running + Proxy' }
  else if (running)               badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else if (hasData)               badge = { cls: 'ready',   dot: 'green',  text: 'Ready' }
  else                            badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  async function generate() {
    setBusy(true); setOperation('generate'); setProg([0, gnmiCapable || 1])
    try {
      const j = await api.genGnmi() as { job_id: string }
      await pollJob(j.job_id, () => {}, (d, t) => setProg([d, t]))
      fetchGnmi()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null); setProg(null) }
  }

  async function start() {
    setBusy(true); setOperation('start'); setErr(null)
    try {
      const j = await api.startGnmi(gnmiPort) as { job_id: string }
      await pollJob(j.job_id, () => {}, (d, t) => setProg([d, t]))
      fetchGnmi()
    } catch (e: unknown) { setErr(errorMessage(e)) }
    finally { setBusy(false); setOperation(null); setProg(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop')
    try { await api.stopGnmi(); fetchGnmi() }
    catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function clear() {
    setBusy(true); setOperation('clear')
    try {
      const j = await api.clearGnmi() as { job_id: string }
      await pollJob(j.job_id, () => {})
      fetchGnmi()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function toggleProxy() {
    setBusy(true); setOperation('proxy')
    try {
      if (proxyOn) await api.stopProxy()
      else         await api.startProxy(gnmiProxyPort)
      fetchGnmi()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  const showStats = running || operation === 'start'
  const pct = prog ? (prog[1] ? Math.round(prog[0] / prog[1] * 100) : 0) : 0
  const determinate = !!(prog && prog[1] > 0)

  const generateTip = running
    ? 'Stop simulator before regenerating'
    : gnmiCapable === 0
      ? 'No switches or routers in topology'
      : hasData
        ? 'Rebuild OpenConfig datasets for switches and routers'
        : 'Build OpenConfig datasets for switches and routers'
  const ipsBound = (binding?.bound_count ?? 0) > 0
  const startTip = !hasData
    ? 'Generate datasets first'
    : !ipsBound
      ? 'Bind IPs first (Binding panel → Bind IPs)'
      : 'Start gNMI server on all devices'
  const clearTip = running
    ? 'Stop simulator before clearing'
    : !hasData
      ? 'No datasets to clear'
      : 'Delete all gNMI datasets'
  const proxyTip = !running
    ? 'Start the gNMI simulator first'
    : proxyOn
      ? 'Stop the aggregating proxy server'
      : 'Aggregate all device gRPC endpoints behind a single port'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">gNMI Simulator</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* ── Progress ───────────────────────────────────────── */}
        {prog && (
          <div className="snmp-progress">
            <div
              className="snmp-progress-fill"
              style={{
                width: determinate ? `${pct}%` : '40%',
                animation: determinate ? undefined : 'indeterminate 1.4s ease infinite',
                transition: determinate ? 'width 0.3s ease' : undefined,
              }}
            />
            <span className="snmp-progress-label">
              {determinate ? `${prog![0]} / ${prog![1]} · ${pct}%` : 'Working…'}
            </span>
          </div>
        )}

        {/* ── Active Devices (gNMI serves switches + routers only) ──── */}
        {showStats && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Active Devices</span>
            <StatRow label="Switches:" value={tc['switch'] ?? 0} />
            <StatRow label="Routers:"  value={tc['router'] ?? 0} />
            <div style={{ height: 4 }} />
            <StatRow
              label="Total:"
              value={gnmiCapable}
              labelColor="var(--cyan)"
              valueColor="var(--cyan)"
            />
          </div>
        )}

        {/* ── Dataset summary (idle/ready) — only with a topology loaded ── */}
        {!showStats && devices.length > 0 && (
          <TargetsBox
            rows={([['switch', 'Switches:'], ['router', 'Routers:']] as [string, string][])
              .filter(([k]) => (tc[k] ?? 0) > 0)
              .map(([k, label]) => ({ label, value: tc[k] ?? 0 }))}
            total={gnmiCapable}
            footer={
              <>
                <div style={{ height: 4 }} />
                <StatRow
                  label="Datasets:"
                  value={datasetCount > 0 ? `${datasetCount} ready` : '—'}
                  valueColor={datasetCount > 0 ? 'var(--green)' : 'var(--text-muted)'}
                />
              </>
            }
          />
        )}

        {/* ── gNMI port ──────────────────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 10, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
            gNMI Port
          </label>
          <NumberInput
            int
            min={1} max={65535}
            value={gnmiPort}
            onChange={n => setGnmiPort(n)}
            fallback={50051}
            disabled={busy || running}
            style={{
              width: 90,
              background: 'var(--bg-inset)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              color: 'var(--text)',
              fontSize: 13,
              fontFamily: 'monospace',
              fontWeight: 700,
              padding: '4px 8px',
              outline: 'none',
              opacity: (busy || running) ? 0.4 : 1,
              transition: 'border-color 0.15s, color 0.15s',
            }}
            title="gRPC port for gNMI server and proxy (default 50051)"
          />
          <span style={{ fontSize: 9, fontFamily: 'monospace', color: 'var(--text-muted)', opacity: 0.5 }}>
            1–65535
          </span>
        </div>

        {/* ── Why the last start was refused ─────────────────────
            Sits directly above the button that produced it. The API's refusals
            are actionable (which IPs are unbound, which port is taken), and the
            operator cannot act on what they never see. */}
        {err && (
          <div style={{
            fontSize: 10, color: 'var(--red)', padding: '6px 8px',
            background: 'color-mix(in srgb, var(--red) 10%, var(--bg-base))',
            border: '1px solid color-mix(in srgb, var(--red) 30%, transparent)',
            borderRadius: 4, lineHeight: 1.5,
          }}>
            {err}
          </div>
        )}

        {/* ── Simulator actions ──────────────────────────────── */}
        <div className="snmp-actions">
          <button
            className="btn-action btn-generate"
            onClick={generate}
            disabled={busy || running || gnmiCapable === 0}
            title={generateTip}
          >
            <IconGenerate />
            <span>{hasData ? 'Regenerate Datasets' : 'Generate Datasets'}</span>
          </button>

          {running ? (
            <button
              className="btn-action btn-stop"
              onClick={stop}
              disabled={busy}
              title="Stop gNMI server"
            >
              <IconStop />
              <span>Stop Simulator</span>
            </button>
          ) : (
            <button
              className="btn-action btn-start"
              onClick={start}
              disabled={busy || !hasData || !ipsBound}
              title={startTip}
            >
              <IconPlay />
              <span>Start Simulator</span>
            </button>
          )}

          <button
            className="btn-action btn-clear"
            onClick={clear}
            disabled={busy || running || !hasData}
            title={clearTip}
          >
            <IconTrash />
            <span>Clear Datasets</span>
          </button>
        </div>

        {/* ── Proxy Server Configuration ─────────────────────── */}
        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Proxy Server Configuration</span>

          <p style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 0 8px 0', lineHeight: 1.5 }}>
            Aggregates all device gRPC endpoints behind a single port.
            Start the simulator first, then enable the proxy.
          </p>

          <div className="field-row-split" style={{ marginBottom: 8 }}>
            <span className="label">Proxy Port</span>
            {proxyOn ? (
              <span style={{
                fontSize: 11, fontFamily: 'Consolas, monospace',
                color: 'var(--green)', fontVariantNumeric: 'tabular-nums',
              }}>{port}</span>
            ) : (
              <NumberInput
                int
                min={1} max={65535}
                value={gnmiProxyPort}
                onChange={n => setGnmiProxyPort(n)}
                fallback={50051}
                disabled={busy || !running}
                style={{
                  width: 90,
                  background: 'var(--bg-inset)',
                  border: '1px solid var(--border)',
                  borderRadius: 4,
                  color: 'var(--text)',
                  fontSize: 13,
                  fontFamily: 'monospace',
                  fontWeight: 700,
                  padding: '4px 8px',
                  outline: 'none',
                  opacity: (busy || !running) ? 0.4 : 1,
                  transition: 'border-color 0.15s, color 0.15s',
                }}
                title="Proxy gRPC port — clients connect here and use target= to address a device. Keep it distinct from the per-device gNMI port (default 50051)."
              />
            )}
          </div>

          <button
            className={`btn-action ${proxyOn ? 'btn-warn' : ''}`}
            onClick={toggleProxy}
            disabled={busy || !running}
            title={proxyTip}
          >
            <IconProxy />
            <span>{proxyOn ? 'Disable Proxy' : 'Enable Proxy'}</span>
          </button>
        </div>

        {/* ── Connected Clients ──────────────────────────────── */}
        {running && (
          <div className="group-box">
            <span className="group-box-label">Connected Clients · {clients.length}</span>
            {clients.length > 0 ? (
              <div style={{ maxHeight: 200, overflowY: 'auto', marginTop: 4 }}>
                <table className="client-table">
                  <thead>
                    <tr>
                      <th>Peer</th>
                      <th>Mode</th>
                      <th>Target</th>
                      <th title="Subscribed paths">Paths</th>
                      <th title="Update messages pushed">Pushes</th>
                      <th>Uptime</th>
                    </tr>
                  </thead>
                  <tbody>
                    {clients.map((c, i) => (
                      <tr key={`${c.peer}-${i}`}>
                        <td title={c.peer}>{c.peer}</td>
                        <td>{c.mode}</td>
                        <td title={c.target}>{c.target || '—'}</td>
                        <td title={(c.paths || []).join(', ') || '/'}>
                          {(c.paths || []).length > 0
                            ? (c.paths.length === 1 ? c.paths[0] : `${c.paths.length} paths`)
                            : '/'}
                        </td>
                        <td style={{ fontVariantNumeric: 'tabular-nums' }}>{c.push_count ?? 0}</td>
                        <td style={{ fontVariantNumeric: 'tabular-nums' }}>{formatUptime(c.connected_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="mono-list-empty">No connected clients</div>
            )}
          </div>
        )}

      </div>
    </div>
  )
}

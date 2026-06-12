import { useState, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

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

export default function SNMPPanel() {
  const { snmp, fetchSnmp, devices, binding, snmpPort, mgmtPort, setSnmpPort, setMgmtPort } = useStore()
  const [busy,      setBusy]      = useState(false)
  const [operation, setOperation] = useState<'generate' | 'start' | 'stop' | 'clear' | null>(null)
  const [prog,      setProg]      = useState<[number, number] | null>(null)
  const [linkCounts, setLinkCounts] = useState({ production: 0, management: 0, power: 0 })
  const [portFocused,    setPortFocused]    = useState(false)
  const [mgmtPortFocused,setMgmtPortFocused] = useState(false)
  const resumedJob = useRef<string | null>(null)

  const running = snmp?.running ?? false
  const ready   = snmp?.ready   ?? false
  const hasData = snmp?.datasets_generated ?? false

  // Resume in-progress job if panel was closed mid-operation
  useEffect(() => {
    const activeJob = snmp?.active_job_id
    if (!activeJob || resumedJob.current === activeJob || busy) return
    resumedJob.current = activeJob
    api.job(activeJob).then((j: unknown) => {
      const job = j as { operation: string; status: string }
      if (job.status !== 'running') { fetchSnmp(); return }
      const op: 'generate' | 'start' = job.operation === 'generate_snmp_datasets' ? 'generate' : 'start'
      setBusy(true); setOperation(op)
      if (op === 'generate') setProg([0, devices.length])
      pollJob(activeJob, () => {}, (d, t) => setProg([d, t]))
        .catch(() => {})
        .finally(() => { setBusy(false); setOperation(null); setProg(null); fetchSnmp() })
    }).catch(() => {})
  }, [snmp?.active_job_id])

  useEffect(() => {
    api.graph().then((data: unknown) => {
      const d = data as { links: { layer: string }[] }
      const counts = { production: 0, management: 0, power: 0 }
      for (const l of d.links ?? []) {
        if (l.layer in counts) counts[l.layer as keyof typeof counts]++
      }
      setLinkCounts(counts)
    }).catch(() => {})
  }, [running])

  // CRAH/CDU are the only cooling devices with SNMP agents (native comm
  // cards); chiller/pump/cooling_tower/valve are BACnet-only.
  const SNMP_TYPES = new Set(['switch','router','server','firewall','load_balancer',
    'oob_switch','sensor','ups','pdu','floor_pdu','generator','crah','cdu'])
  const snmpDevices = devices.filter(d => SNMP_TYPES.has(d.device_type))
  const tc: Record<string, number> = {}
  for (const d of snmpDevices) tc[d.device_type] = (tc[d.device_type] || 0) + 1
  const total = snmpDevices.length

  // Badge state for header
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (operation === 'generate')      badge = { cls: 'ready',   dot: 'yellow', text: 'Generating' }
  else if (operation === 'start')    badge = { cls: 'ready',   dot: 'yellow', text: 'Starting' }
  else if (operation === 'stop')     badge = { cls: 'ready',   dot: 'yellow', text: 'Stopping' }
  else if (operation === 'clear')    badge = { cls: 'ready',   dot: 'yellow', text: 'Clearing' }
  else if (running && !ready)        badge = { cls: 'ready',   dot: 'yellow', text: 'Starting…' }
  else if (running)                  badge = { cls: 'running', dot: 'green',  text: 'Running' }
  else if (hasData)                  badge = { cls: 'ready',   dot: 'green',  text: 'Ready' }
  else                               badge = { cls: 'stopped', dot: 'grey',   text: 'Idle' }

  async function generate() {
    setBusy(true); setOperation('generate'); setProg([0, devices.length])
    try {
      const j = await api.genSnmp() as { job_id: string }
      await pollJob(j.job_id, () => {}, (d, t) => setProg([d, t]))
      fetchSnmp()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null); setProg(null) }
  }

  async function start() {
    setBusy(true); setOperation('start')
    try {
      const j = await api.startSnmp(snmpPort, mgmtPort) as { job_id: string }
      await pollJob(j.job_id, () => {})
      fetchSnmp()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function stop() {
    setBusy(true); setOperation('stop')
    try { await api.stopSnmp(); fetchSnmp() }
    catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  async function clear() {
    setBusy(true); setOperation('clear')
    try {
      const j = await api.clearSnmp() as { job_id: string }
      await pollJob(j.job_id, () => {})
      fetchSnmp()
    } catch { /* ignore */ }
    finally { setBusy(false); setOperation(null) }
  }

  const showStats = running || operation === 'start'
  const pct = prog ? (prog[1] ? Math.round(prog[0] / prog[1] * 100) : 0) : 0
  const determinate = !!(prog && prog[1] > 0)

  // Action button reasons (tooltips)
  const generateTip = running
    ? 'Stop simulator before regenerating'
    : hasData
      ? 'Rebuild SNMP datasets from current topology'
      : 'Build per-device SNMP datasets from current topology'
  const ipsBound = (binding?.bound_count ?? 0) > 0
  const startTip = !hasData
    ? 'Generate datasets first'
    : !ipsBound
      ? 'Bind IPs first (Binding panel → Bind IPs)'
      : 'Start SNMP agents on all devices'
  const clearTip = running
    ? 'Stop simulator before clearing'
    : !hasData
      ? 'No datasets to clear'
      : 'Delete all generated datasets'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">SNMP Simulator</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {/* Progress bar — generation only */}
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
              {determinate ? `${prog[0]} / ${prog[1]} · ${pct}%` : 'Working…'}
            </span>
          </div>
        )}

        {/* Targets — idle/ready state */}
        {!showStats && total > 0 && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Targets</span>
            {(tc['switch']        ?? 0) > 0 && <StatRow label="Switches:"       value={tc['switch']        ?? 0} />}
            {(tc['router']        ?? 0) > 0 && <StatRow label="Routers:"        value={tc['router']        ?? 0} />}
            {(tc['server']        ?? 0) > 0 && <StatRow label="Servers:"        value={tc['server']        ?? 0} />}
            {(tc['firewall']      ?? 0) > 0 && <StatRow label="Firewalls:"      value={tc['firewall']      ?? 0} />}
            {(tc['load_balancer'] ?? 0) > 0 && <StatRow label="Load Balancers:" value={tc['load_balancer'] ?? 0} />}
            {(tc['oob_switch']    ?? 0) > 0 && <StatRow label="OOB Switches:"   value={tc['oob_switch']    ?? 0} />}
            {(tc['sensor']        ?? 0) > 0 && <StatRow label="Sensors:"        value={tc['sensor']        ?? 0} />}
            {(tc['ups']           ?? 0) > 0 && <StatRow label="UPS:"            value={tc['ups']           ?? 0} />}
            {(tc['pdu']           ?? 0) > 0 && <StatRow label="Rack PDUs:"      value={tc['pdu']           ?? 0} />}
            {(tc['floor_pdu']     ?? 0) > 0 && <StatRow label="Floor PDUs:"     value={tc['floor_pdu']     ?? 0} />}
            {(tc['generator']     ?? 0) > 0 && <StatRow label="Generators:"     value={tc['generator']     ?? 0} />}
            {(tc['crah']          ?? 0) > 0 && <StatRow label="CRAHs:"          value={tc['crah']          ?? 0} />}
            {(tc['cdu']           ?? 0) > 0 && <StatRow label="CDUs:"           value={tc['cdu']           ?? 0} />}
            <StatRow label="Total:" value={total} labelColor="#06b6d4" valueColor="#06b6d4" />
            <div style={{ height: 4 }} />
            <StatRow
              label="Datasets:"
              value={hasData ? `${snmp?.dataset_count ?? '?'} ready` : '—'}
              valueColor={hasData ? 'var(--green)' : 'var(--text-muted)'}
            />
          </div>
        )}

        {/* Active Devices */}
        {showStats && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Active Devices</span>
            {(tc['switch']        ?? 0) > 0 && <StatRow label="Switches:"       value={tc['switch']        ?? 0} />}
            {(tc['router']        ?? 0) > 0 && <StatRow label="Routers:"        value={tc['router']        ?? 0} />}
            {(tc['server']        ?? 0) > 0 && <StatRow label="Servers:"        value={tc['server']        ?? 0} />}
            {(tc['firewall']      ?? 0) > 0 && <StatRow label="Firewalls:"      value={tc['firewall']      ?? 0} />}
            {(tc['load_balancer'] ?? 0) > 0 && <StatRow label="Load Balancers:" value={tc['load_balancer'] ?? 0} />}
            {(['oob_switch','sensor','ups','pdu','floor_pdu','generator','crah','cdu'].some(t => (tc[t] ?? 0) > 0)) && <div style={{ height: 4 }} />}
            {(tc['oob_switch']    ?? 0) > 0 && <StatRow label="OOB Switches:"   value={tc['oob_switch']    ?? 0} />}
            {(tc['sensor']        ?? 0) > 0 && <StatRow label="Sensors:"        value={tc['sensor']        ?? 0} />}
            {(tc['ups']           ?? 0) > 0 && <StatRow label="UPS:"            value={tc['ups']           ?? 0} />}
            {(tc['pdu']           ?? 0) > 0 && <StatRow label="Rack PDUs:"      value={tc['pdu']           ?? 0} />}
            {(tc['floor_pdu']     ?? 0) > 0 && <StatRow label="Floor PDUs:"     value={tc['floor_pdu']     ?? 0} />}
            {(tc['generator']     ?? 0) > 0 && <StatRow label="Generators:"     value={tc['generator']     ?? 0} />}
            {(tc['crah']          ?? 0) > 0 && <StatRow label="CRAHs:"          value={tc['crah']          ?? 0} />}
            {(tc['cdu']           ?? 0) > 0 && <StatRow label="CDUs:"           value={tc['cdu']           ?? 0} />}
            <StatRow label="Total:" value={total} labelColor="#06b6d4" valueColor="#06b6d4" />
          </div>
        )}

        {/* Network Links */}
        {showStats && (
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Network Links</span>
            <StatRow label="Prod Links:"  value={linkCounts.production} />
            <StatRow label="Mgmt Links:"  value={linkCounts.management} />
          </div>
        )}

        {/* Port Configuration group */}
        <div className="group-box">
          <span className="group-box-label">Port Configuration</span>

          {/* SNMP Port row */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <label style={{ fontSize: 10, color: 'var(--text)', whiteSpace: 'nowrap' }}>SNMP Port</label>
            <input
              type="number"
              min={1} max={65535}
              value={snmpPort}
              onChange={e => setSnmpPort(Math.max(1, Math.min(65535, parseInt(e.target.value) || 161)))}
              onFocus={() => setPortFocused(true)}
              onBlur={() => setPortFocused(false)}
              disabled={busy || running}
              style={{
                width: 72,
                background: '#0d1117',
                border: `1px solid ${portFocused ? '#58a6ff' : 'var(--border)'}`,
                borderRadius: 4,
                color: portFocused ? '#58a6ff' : 'var(--text)',
                fontSize: 12,
                fontFamily: 'monospace',
                fontWeight: 700,
                padding: '3px 7px',
                outline: 'none',
                opacity: (busy || running) ? 0.4 : 1,
                transition: 'border-color 0.15s, color 0.15s',
              }}
              title="UDP port snmpsim will listen on. Use 1611+ if Windows SNMP service occupies 161."
            />
          </div>
          <div style={{ fontSize: 9, color: '#484f58', marginBottom: 6, paddingLeft: 1 }}>
            Read · GET / WALK · NMS &amp; monitoring tools
          </div>

          <div style={{ borderTop: '1px solid #21262d', margin: '4px 0' }} />

          {/* Mgmt Port row */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 4 }}>
            <label style={{ fontSize: 10, color: 'var(--text)', whiteSpace: 'nowrap' }}>Mgmt Port</label>
            <input
              type="number"
              min={1} max={65535}
              value={mgmtPort}
              onChange={e => setMgmtPort(Math.max(1, Math.min(65535, parseInt(e.target.value) || 1161)))}
              onFocus={() => setMgmtPortFocused(true)}
              onBlur={() => setMgmtPortFocused(false)}
              disabled={busy || running}
              style={{
                width: 72,
                background: '#0d1117',
                border: `1px solid ${mgmtPortFocused ? '#58a6ff' : 'var(--border)'}`,
                borderRadius: 4,
                color: mgmtPortFocused ? '#58a6ff' : 'var(--text)',
                fontSize: 12,
                fontFamily: 'monospace',
                fontWeight: 700,
                padding: '3px 7px',
                outline: 'none',
                opacity: (busy || running) ? 0.4 : 1,
                transition: 'border-color 0.15s, color 0.15s',
              }}
              title="UDP port for SNMP SET requests. DCIM systems update sysName, sysLocation, rack fields here. Community = device IP."
            />
          </div>
          <div style={{ fontSize: 9, color: '#484f58', paddingLeft: 1 }}>
            Write · SET
          </div>
        </div>

        {/* Action buttons — primary flow: Generate → Start/Stop → Clear */}
        <div className="snmp-actions">
          <button
            className="btn-action btn-generate"
            onClick={generate}
            disabled={busy || running}
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
              title="Stop SNMP agents"
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

      </div>
    </div>
  )
}
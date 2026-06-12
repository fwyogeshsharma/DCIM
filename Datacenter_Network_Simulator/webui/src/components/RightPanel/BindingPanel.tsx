import { useState, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

const IconRefresh = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
    <path d="M16 16h5v5" />
  </svg>
)
const IconLink = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </svg>
)
const IconUnlink = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M18.84 12.25l1.72-1.71a5 5 0 0 0-7.07-7.07L12 4.96" />
    <path d="M5.17 11.75l-1.72 1.71a5 5 0 0 0 7.07 7.07L12 19.04" />
    <line x1="8" y1="2" x2="8" y2="5" />
    <line x1="2" y1="8" x2="5" y2="8" />
    <line x1="16" y1="22" x2="16" y2="19" />
    <line x1="22" y1="16" x2="19" y2="16" />
  </svg>
)

export default function BindingPanel() {
  const {
    binding, adapters, fetchBinding, fetchAdapters,
    bindingBusy, bindingProgress, setBindingBusy, setBindingProgress,
    bacnet, snmp, gnmi, devices,
  } = useStore()
  const [selectedAdapter, setSelectedAdapter] = useState('')
  const [mask, setMask] = useState('255.255.255.0')
  const resumedJob = useRef<string | null>(null)

  useEffect(() => {
    if (binding?.selected_adapter) setSelectedAdapter(binding.selected_adapter)
    if (binding?.subnet_mask) setMask(binding.subnet_mask)
  }, [binding])

  useEffect(() => {
    const list = adapters?.adapters || []
    if (!selectedAdapter && list.length > 0) {
      setSelectedAdapter(list[0])
    }
  }, [adapters])

  useEffect(() => { fetchBinding() }, [])

  useEffect(() => {
    const activeJob = binding?.active_job_id
    if (activeJob && resumedJob.current !== activeJob && !bindingBusy) {
      resumedJob.current = activeJob
      setBindingBusy(true)
      pollUntilDone(activeJob, setBindingProgress)
        .catch(() => {})
        .finally(() => { setBindingBusy(false); setBindingProgress(null); fetchBinding() })
    }
  }, [binding?.active_job_id])

  async function doBind() {
    if (!selectedAdapter) return
    setBindingBusy(true); setBindingProgress(null)
    try {
      await api.setAdapter(selectedAdapter)
      await api.setMask(mask)
      const job = await api.bindIPs() as { job_id: string }
      await pollUntilDone(job.job_id, setBindingProgress)
      fetchBinding()
    } catch (_e: unknown) {
      // ignore
    } finally { setBindingBusy(false); setBindingProgress(null) }
  }

  async function doUnbind() {
    setBindingBusy(true); setBindingProgress(null)
    try {
      const job = await api.unbindIPs() as { job_id: string }
      if (job.job_id !== 'none') await pollUntilDone(job.job_id, setBindingProgress)
      fetchBinding()
    } catch (_e: unknown) {
      // ignore
    } finally { setBindingBusy(false); setBindingProgress(null) }
  }

  const allAdapters = adapters?.adapters || []
  const labels      = adapters?.adapter_labels || {}
  const boundCount  = binding?.bound_count ?? 0

  // Breakdown of bound IPs: production vs OOB management addresses.
  // An IP shared by both networks (OOB switches, sensors) counts as prod.
  const prodSet = new Set(devices.map(d => d.ip_address).filter(Boolean))
  const boundProd = (binding?.bound_ips ?? []).filter(ip => prodSet.has(ip)).length
  const boundMgmt = Math.max(0, boundCount - boundProd)

  // Header badge
  type BadgeCfg = { cls: string; dot: string; text: string }
  let badge: BadgeCfg
  if (bindingBusy)         badge = { cls: 'ready',   dot: 'yellow', text: 'Working' }
  else if (boundCount > 0) badge = { cls: 'running', dot: 'green',  text: `${boundCount} bound` }
  else                     badge = { cls: 'stopped', dot: 'grey',   text: 'Unbound' }

  const pct = bindingProgress && bindingProgress.total > 0
    ? Math.round(bindingProgress.done / bindingProgress.total * 100)
    : 0
  const determinate = !!(bindingProgress && bindingProgress.total > 0)

  const bacnetRunning = bacnet?.running ?? false
  const snmpRunning   = snmp?.running   ?? false
  const gnmiRunning   = gnmi?.running   ?? false
  const simRunning    = bacnetRunning || snmpRunning || gnmiRunning
  const simName       = snmpRunning ? 'SNMP' : gnmiRunning ? 'gNMI' : 'BACnet'
  const canBind   = !bindingBusy && !simRunning && !!selectedAdapter
  const canUnbind = !bindingBusy && !simRunning && boundCount > 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">Network Interface Bindings</span>
        <span className={`badge ${badge.cls}`}>
          <span className={`status-dot ${badge.dot}`} />
          {badge.text}
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        <div className="group-box" style={{ marginTop: 6 }}>
          <span className="group-box-label">Adapter</span>

          <p style={{ fontSize: 10, color: 'var(--text-muted)', margin: '0 0 8px 0', lineHeight: 1.5 }}>
            Device IPs bind to selected adapter.
          </p>

          <div style={{ display: 'flex', gap: 5, marginBottom: 8, minWidth: 0 }}>
            <select
              style={{ flex: 1, minWidth: 0, fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis' }}
              value={selectedAdapter}
              onChange={e => setSelectedAdapter(e.target.value)}
              disabled={bindingBusy}
            >
              {allAdapters.length === 0
                ? <option value="">Loading interfaces…</option>
                : allAdapters.map(a => (
                    <option key={a} value={a}>{labels[a] || a}</option>
                  ))
              }
            </select>
            <button
              className="btn-icon"
              onClick={() => fetchAdapters()}
              disabled={bindingBusy}
              title="Refresh adapter list"
            >
              <IconRefresh />
            </button>
          </div>

          <div className="field-row-split" style={{ marginBottom: 8 }}>
            <span className="label">Subnet Mask</span>
            <input
              value={mask}
              onChange={e => setMask(e.target.value)}
              disabled={bindingBusy}
              style={{ width: 124, fontSize: 10, fontFamily: 'Consolas, monospace', textAlign: 'right' }}
            />
          </div>

          <div className="field-row-split">
            <span className="label">IPs Bound</span>
            <span style={{
              fontSize: 11, fontWeight: 600,
              color: boundCount > 0 ? 'var(--green)' : 'var(--text-muted)',
              fontVariantNumeric: 'tabular-nums',
            }}>{boundCount}</span>
          </div>

          {boundCount > 0 && (
            <div style={{ marginTop: 2, paddingLeft: 10 }}>
              <div className="field-row-split">
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Production IPs</span>
                <span style={{ fontSize: 10, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{boundProd}</span>
              </div>
              <div className="field-row-split">
                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Management IPs</span>
                <span style={{ fontSize: 10, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{boundMgmt}</span>
              </div>
            </div>
          )}
        </div>

        {bindingBusy && (
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
              {determinate
                ? `${bindingProgress!.done} / ${bindingProgress!.total} · ${pct}%`
                : 'Working…'}
            </span>
          </div>
        )}

        <div className="snmp-actions">
          <button
            className="btn-action btn-start"
            onClick={doBind}
            disabled={!canBind}
            title={
              simRunning         ? `Stop ${simName} simulator before changing bindings`
              : !selectedAdapter ? 'Select an adapter first'
              : bindingBusy      ? 'Operation in progress'
              : 'Bind device IPs to selected adapter'
            }
          >
            <IconLink />
            <span>{boundCount > 0 ? 'Rebind IPs' : 'Bind IPs'}</span>
          </button>

          <button
            className="btn-action btn-clear"
            onClick={doUnbind}
            disabled={!canUnbind}
            title={
              simRunning         ? `Stop ${simName} simulator before changing bindings`
              : bindingBusy      ? 'Operation in progress'
              : boundCount === 0 ? 'Nothing bound to remove'
              : 'Remove all device IP bindings'
            }
          >
            <IconUnlink />
            <span>Remove Binding</span>
          </button>
        </div>

      </div>
    </div>
  )
}

async function pollUntilDone(
  jobId: string,
  onProgress: (p: { done: number; total: number }) => void
) {
  for (let i = 0; i < 600; i++) {
    try {
      const j = await api.job(jobId) as { status: string; error: string; progress_done: number; progress_total: number }
      onProgress({ done: j.progress_done ?? 0, total: j.progress_total ?? 0 })
      if (j.status === 'completed') return
      if (j.status === 'failed') throw new Error(j.error || 'Job failed')
    } catch (e: unknown) {
      // Swallow transient proxy/network errors (503, ECONNRESET) during IP binding.
      // Re-throw only real job failures.
      if (e instanceof Error && e.message.startsWith('Job failed')) throw e
    }
    await new Promise(r => setTimeout(r, 500))
  }
}
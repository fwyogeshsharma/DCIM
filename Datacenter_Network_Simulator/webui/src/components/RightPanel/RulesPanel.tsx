import { useState, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'
import type { Rule } from '../../api/types'

import { SEVERITY_COLOR } from '../../theme'

const METRIC_LABEL: Record<string, [string, string]> = {
  cpu_usage:           ['cpu',         '%'],
  memory_usage:        ['mem',         '%'],
  temperature:         ['temp',        '°C'],
  humidity:            ['humidity',    '%'],
  dewpoint:            ['dewpoint',    '°C×10'],
  airflow:             ['airflow',     'm/s×10'],
  ambient_temp:        ['amb.temp',    '°C'],
  ups_output_load:     ['out.load',    '%'],
  ups_input_voltage:   ['in.volt',     'V'],
  ups_input_frequency: ['in.freq',     'Hz'],
  pdu_load:            ['load',        '%'],
  pdu_voltage:         ['voltage',     'V'],
  pdu_power_factor:    ['pwr.factor',  ''],
  pdu_phase_imbalance: ['phase.imbal', '%'],
  pdu_outlet_current:  ['outlet.cur',  'A'],
  pdu_frequency:       ['pdu.freq',    'Hz'],
  pdu_temperature:     ['pdu.temp',    '°C'],
  pdu_humidity:        ['pdu.humid',   '%'],
  pdu_energy_kwh:      ['pdu.energy',  'kWh'],
  ups_battery_health:  ['batt.health', '%'],
  ups_bypass_status:   ['bypass',      ''],
  ups_operating_mode:  ['mode',        ''],
  mid_temp:            ['mid.temp',    '°C'],
  outlet_temp:         ['exhaust.temp','°C'],
}

const CATEGORIES: [string, string[]][] = [
  ['Interface / Link',          ['LinkDown','LinkUp','LinkFlap','OOBSwitchLinkDown','OOBSwitchLinkUp']],
  ['CPU',                       ['HighCPU','HighCPUSustained','CPUNormal']],
  ['Memory',                    ['HighMemory','MemoryNormal']],
  ['Temperature',               ['HighTemperature','TemperatureNormal','CriticalCPUAndTemp']],
  ['Power / UPS',               ['UPSOnBattery','UPSLowBattery','UPSBatteryNormal','UPSUtilityRestored','UPSOutputOverload','UPSOutputNormal','UPSFanFailure','UPSBatteryFailure','UPSBatteryDisconnected','UPSChargerFailure','UPSInputVoltageHigh','UPSInputVoltageNormal','UPSInputVoltageLow','UPSInputVoltageLowCleared','UPSFrequencyOutOfRange','UPSRectifierFailure','UPSPhaseFailure']],
  ['Power / PDU',               ['PDUOutletOn','PDUOutletOff','PDUBreakerTripped','PDULoadHigh','PDULoadCritical','PDUVoltageHigh','PDUVoltageLow','PDUPhaseImbalance','PDUPowerFactorLow','PDUOutletFailure','PDUSmokeDetected','PDUOutletCurrentHigh','PDUGroundFault']],
  ['Routing',                   ['BGPSessionDown']],
  ['Infrastructure',            ['RackFailure']],
  ['Env. Sensors — Temperature',['SensorAmbientTempHigh','SensorAmbientTempCritical','SensorAmbientTempNormal']],
  ['Env. Sensors — Humidity',   ['SensorHighHumidity','SensorCriticalHumidity','SensorLowHumidity','SensorHumidityNormal']],
  ['Env. Sensors — Dew Point',  ['SensorHighDewPoint','SensorDewPointNormal']],
  ['Env. Sensors — Airflow',    ['SensorHighAirflow','SensorLowAirflow','SensorAirflowNormal']],
  ['Env. Sensors — Mid/Exhaust Temp', ['SensorMidTempHigh','SensorMidTempNormal','SensorOutletTempHigh','SensorOutletTempNormal']],
  ['Power / UPS Extended',      ['UPSBatteryLowHealth','UPSBatteryHealthRestored','UPSBypassActive','UPSBypassCleared']],
  ['Power / PDU Environment',   ['PDUFrequencyFault','PDUFrequencyNormal','PDUTempHigh','PDUTempNormal','PDUHumidityHigh','PDUHumidityNormal']],
]

function formatThreshold(conditions: Record<string, unknown>[]): string {
  if (!conditions?.length) return '—'
  const c = conditions[0]
  const ct = c.condition_type as string

  if (ct === 'threshold') {
    const metric = c.metric as string
    const [label, unit] = METRIC_LABEL[metric] ?? [metric, '']
    const val = c.threshold as number
    let s = `${label} ${c.operator} ${val}${unit}`
    if (c.duration_sec) s += ` for ${c.duration_sec}s`
    return s
  }
  if (ct === 'state_change') {
    return `${(c.from_state as string) || '*'} → ${(c.to_state as string) || '*'}`
  }
  if (ct === 'temporal') {
    return `${c.event_count}× / ${c.window_sec}s`
  }
  if (ct === 'composite') {
    const subs = (c.conditions as Record<string, unknown>[]) || []
    const parts = subs.map(sub => {
      const [lbl, unit] = METRIC_LABEL[sub.metric as string] ?? [sub.metric as string, '']
      return `${lbl}${sub.operator}${sub.threshold}${unit}`
    })
    return parts.join(` ${c.logic} `)
  }
  if (ct === 'rack_failure') {
    return `≥${c.threshold} devices`
  }
  return '—'
}

function extractOid(actions: string[]): string {
  if (!actions?.length) return '—'
  const oid = actions.find(a => /^\d+(\.\d+){3,}/.test(a))
  return oid ?? actions[0] ?? '—'
}

function groupRules(rules: Rule[]): [string, Rule[]][] {
  const ruleMap = new Map(rules.map(r => [r.name, r]))
  const placed = new Set<string>()
  const groups: [string, Rule[]][] = []

  for (const [label, names] of CATEGORIES) {
    const matched = names.flatMap(n => ruleMap.has(n) ? [ruleMap.get(n)!] : [])
    if (matched.length > 0) {
      groups.push([label, matched])
      matched.forEach(r => placed.add(r.name))
    }
  }

  const leftover = rules.filter(r => !placed.has(r.name))
  if (leftover.length > 0) groups.push(['Other', leftover])
  return groups
}

function RuleRow({ r, onToggle }: { r: Rule; onToggle: () => void }) {
  const sev = r.severity
  const sevColor = sev ? (SEVERITY_COLOR[sev] || 'var(--text-muted)') : undefined
  const oid = extractOid(r.actions)
  const threshold = formatThreshold(r.conditions)

  return (
    <tr style={{ opacity: r.enabled ? 1 : 0.5 }}>
      <td style={{ textAlign: 'center' }}>
        <input
          type="checkbox"
          checked={r.enabled}
          onChange={onToggle}
          style={{ cursor: 'pointer' }}
        />
      </td>
      <td title={r.description || r.name}>{r.name}</td>
      <td className="mono oid" title={oid}>{oid}</td>
      <td>
        {sev && sevColor && (
          <span style={{
            display: 'inline-block',
            background: sevColor, color: 'var(--on-solid)',
            padding: '1px 4px', borderRadius: 3,
            fontSize: 9, fontWeight: 700,
            textTransform: 'uppercase',
          }}>
            {sev.slice(0, 4)}
          </span>
        )}
      </td>
      <td className="threshold" title={threshold}>{threshold}</td>
      <td style={{
        textAlign: 'right',
        fontWeight: r.total_fired > 0 ? 700 : 400,
        color: r.total_fired > 0 ? 'var(--yellow)' : 'var(--text-muted)',
      }}>
        {r.total_fired}
      </td>
      <td className="last">{r.last_fired ? r.last_fired.slice(11, 19) : '—'}</td>
    </tr>
  )
}

export default function RulesPanel() {
  const { rules, fetchRules } = useStore()
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  useEffect(() => { fetchRules() }, [])

  function isExpanded(label: string) {
    return expanded[label] !== false  // default = expanded
  }

  function toggleSection(label: string) {
    setExpanded(prev => ({ ...prev, [label]: !isExpanded(label) }))
  }

  async function toggleRule(name: string, enabled: boolean) {
    try {
      if (enabled) await api.disableRule(name)
      else await api.enableRule(name)
      fetchRules()
    } catch (e) { console.error(e) }
  }

  async function resetCounts() {
    try { await api.resetCounts(); fetchRules() }
    catch (e) { console.error(e) }
  }

  const ruleList = rules?.rules ?? []
  const groups = groupRules(ruleList)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="panel-header">
        <span className="title">Traps Rule Engine</span>
      </div>

      <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          Grand total: <strong>{rules?.total_fired_grand ?? 0}</strong> fired
        </span>
        <button onClick={resetCounts} style={{ fontSize: 10, padding: '2px 8px' }}>
          Reset Counts
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        <table className="rules-table">
          <thead>
            <tr>
              <th style={{ width: 30 }}>On</th>
              <th style={{ width: 150 }}>Rule Name</th>
              <th style={{ width: 175 }}>Trap OID</th>
              <th style={{ width: 46 }}>Sev</th>
              <th style={{ width: 110 }}>Threshold</th>
              <th style={{ width: 46, textAlign: 'right' }}>Fired</th>
              <th style={{ width: 90 }}>Last Fired</th>
            </tr>
          </thead>
          <tbody>
            {groups.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ padding: 16, textAlign: 'center', color: 'var(--text-dim)', fontSize: 10 }}>
                  No rules loaded
                </td>
              </tr>
            ) : groups.flatMap(([label, catRules]) => {
              const open = isExpanded(label)
              const headerRow = (
                <tr
                  key={`cat-${label}`}
                  className="rules-category-header"
                  onClick={() => toggleSection(label)}
                  title="Click to expand / collapse"
                >
                  <td colSpan={7}>
                    <span style={{ marginRight: 6, fontSize: 9 }}>{open ? '▾' : '▸'}</span>
                    {label}
                    <span style={{ marginLeft: 8, fontSize: 9, opacity: 0.6 }}>
                      {catRules.length} rule{catRules.length !== 1 ? 's' : ''}
                    </span>
                  </td>
                </tr>
              )
              const ruleRows = open
                ? catRules.map(r => (
                    <RuleRow key={r.name} r={r} onToggle={() => toggleRule(r.name, r.enabled)} />
                  ))
                : []
              return [headerRow, ...ruleRows]
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

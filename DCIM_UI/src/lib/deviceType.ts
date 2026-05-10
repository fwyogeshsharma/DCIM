export type DeviceCategory =
  | 'PDU'
  | 'UPS'
  | 'SENSOR'
  | 'SERVER'
  | 'TOR_SWITCH'
  | 'AGG_SWITCH'
  | 'ROUTER'
  | 'OOB_SWITCH'
  | 'UNKNOWN'

export interface DeviceTypeMeta {
  category: DeviceCategory
  label: string
  icon: string
  color: string
  bgClass: string
  badgeClass: string
  isPowerDevice: boolean
}

const META: Record<DeviceCategory, DeviceTypeMeta> = {
  PDU: {
    category: 'PDU',
    label: 'PDU',
    icon: 'Zap',
    color: '#f59e0b',
    bgClass: 'bg-amber-500/5 hover:bg-amber-500/10',
    badgeClass: 'bg-amber-500/15 text-amber-400 border border-amber-500/30',
    isPowerDevice: true,
  },
  UPS: {
    category: 'UPS',
    label: 'UPS',
    icon: 'BatteryCharging',
    color: '#84cc16',
    bgClass: 'bg-lime-500/5 hover:bg-lime-500/10',
    badgeClass: 'bg-lime-500/15 text-lime-400 border border-lime-500/30',
    isPowerDevice: true,
  },
  SENSOR: {
    category: 'SENSOR',
    label: 'Sensor',
    icon: 'Thermometer',
    color: '#06b6d4',
    bgClass: 'bg-cyan-500/5 hover:bg-cyan-500/10',
    badgeClass: 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/30',
    isPowerDevice: true,
  },
  SERVER: {
    category: 'SERVER',
    label: 'Server',
    icon: 'Server',
    color: '#3b82f6',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-blue-500/15 text-blue-400 border border-blue-500/30',
    isPowerDevice: false,
  },
  TOR_SWITCH: {
    category: 'TOR_SWITCH',
    label: 'ToR Switch',
    icon: 'Network',
    color: '#8b5cf6',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-violet-500/15 text-violet-400 border border-violet-500/30',
    isPowerDevice: false,
  },
  AGG_SWITCH: {
    category: 'AGG_SWITCH',
    label: 'Agg Switch',
    icon: 'Network',
    color: '#a855f7',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-purple-500/15 text-purple-400 border border-purple-500/30',
    isPowerDevice: false,
  },
  ROUTER: {
    category: 'ROUTER',
    label: 'Router',
    icon: 'Router',
    color: '#6366f1',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-indigo-500/15 text-indigo-400 border border-indigo-500/30',
    isPowerDevice: false,
  },
  OOB_SWITCH: {
    category: 'OOB_SWITCH',
    label: 'OOB Switch',
    icon: 'Shield',
    color: '#64748b',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-slate-500/15 text-slate-400 border border-slate-500/30',
    isPowerDevice: false,
  },
  UNKNOWN: {
    category: 'UNKNOWN',
    label: 'Unknown',
    icon: 'HelpCircle',
    color: '#64748b',
    bgClass: 'hover:bg-white/5',
    badgeClass: 'bg-slate-500/15 text-slate-400 border border-slate-500/30',
    isPowerDevice: false,
  },
}

export function getDeviceTypeMeta(agentId: string): DeviceTypeMeta {
  const parts = agentId.split('-')
  const device = parts.length >= 3 ? parts.slice(2).join('-').toUpperCase() : agentId.toUpperCase()

  if (device.includes('PDU') || device.includes('FPDU')) return META.PDU
  if (device.includes('UPS')) return META.UPS
  if (device.includes('SENSOR')) return META.SENSOR
  if (device.includes('SRV') || device.includes('SERVER')) return META.SERVER
  if (device.includes('TOR')) return META.TOR_SWITCH
  if (device.includes('AGG')) return META.AGG_SWITCH
  if (device.includes('CORE') || device.includes('ROUTER') || device.startsWith('CORE')) return META.ROUTER
  if (device.includes('OOB')) return META.OOB_SWITCH
  return META.UNKNOWN
}

export const POWER_DEVICE_DESCRIPTIONS: Record<string, string> = {
  PDU: 'Monitors power distribution to rack equipment — tracks outlet load, voltage, and current draw.',
  UPS: 'Provides battery backup and power conditioning — monitors charge level, runtime, and load.',
  SENSOR: 'Environmental monitoring sensor — tracks temperature, humidity, and airflow.',
}

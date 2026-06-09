/**
 * fireSafetyFacility — turn raw device metrics into a floor/room facility model
 * for the Fire & Safety 3D view.
 *
 * Environment metrics (e.g. `environment.temperature_c`) carry the reporting
 * device's physical placement in their `metadata`: `metadata.room` and
 * `metadata.floor`. We collapse the stream to the latest reading per device,
 * derive a fire-safety status from the value, then group the devices into
 * floors → rooms so the scene can build the building from real data.
 */

import type { Metric } from './types'

// ── Public types ────────────────────────────────────────────────────────────

export type DeviceStatus = 'normal' | 'alarm' | 'fault' | 'offline' | 'testing'

export interface SceneDevice {
  id: string
  name: string
  room: string
  floor: string
  value?: number
  unit?: string
  status: DeviceStatus
  lastSeen?: string
}

export interface FacilityFloor {
  label: string      // floor name exactly as reported (e.g. 'Ground', 'L2')
  rooms: string[]    // ordered room names present on this floor
}

export interface Facility {
  floors: FacilityFloor[]   // ordered ground → top
  devices: SceneDevice[]
}

// Temperature thresholds (°C) that drive sensor colour in the scene.
export const TEMP_FAULT = 30
export const TEMP_ALARM = 35

// How long after a device's last reading we treat it as offline (ms).
const OFFLINE_AFTER_MS = 15 * 60 * 1000

// ── Helpers ─────────────────────────────────────────────────────────────────

function str(v: unknown): string {
  return v == null ? '' : String(v).trim()
}

/** Read a key from metadata tolerating common casing variants. */
function md(meta: Record<string, any>, ...keys: string[]): string {
  for (const k of keys) {
    const hit = str(meta[k]) || str(meta[k.toLowerCase()]) || str(meta[k.toUpperCase()])
    if (hit) return hit
  }
  return ''
}

/** Rank a floor label so floors sort ground → top regardless of naming. */
export function floorRank(label: string): number {
  const s = label.trim().toLowerCase()
  if (/^(g|gf|ground|lobby|0)$/.test(s)) return 0
  const bm = s.match(/^(?:b|basement|lg|lower\s*ground)\s*(\d+)?$/)
  if (bm) return -(parseInt(bm[1] || '1', 10))
  const m = s.match(/-?\d+/)
  if (m) return parseInt(m[0], 10)
  return 1000 // unknown labels sort last but stay stable among themselves
}

export function statusFromTemp(value: number | undefined, lastSeen?: string): DeviceStatus {
  if (lastSeen) {
    const age = Date.now() - new Date(lastSeen).getTime()
    if (age > OFFLINE_AFTER_MS) return 'offline'
  }
  if (value == null || Number.isNaN(value)) return 'offline'
  if (value >= TEMP_ALARM) return 'alarm'
  if (value >= TEMP_FAULT) return 'fault'
  return 'normal'
}

function deviceKey(m: Metric, meta: Record<string, any>): string {
  return (
    md(meta, 'device_name', 'hostname', 'device_host', 'name', 'device_ip', 'ip') ||
    str((m as any).server_name) ||
    str(m.agent_id)
  )
}

// ── Builder ─────────────────────────────────────────────────────────────────

/**
 * Collapse a metric stream into a floor/room facility model. Only metrics that
 * expose a `room` and/or `floor` in their metadata contribute to the building.
 */
export function buildFacility(metrics: Metric[] | undefined | null): Facility {
  const latest = new Map<string, { m: Metric; room: string; floor: string }>()

  for (const m of metrics ?? []) {
    const meta = (m.metadata ?? {}) as Record<string, any>
    const room = md(meta, 'room', 'room_name', 'hall')
    const floor = md(meta, 'floor', 'floor_name', 'level')
    if (!room && !floor) continue // no placement info → not part of the facility

    const key = deviceKey(m, meta)
    if (!key) continue

    const prev = latest.get(key)
    if (!prev || new Date(m.timestamp).getTime() > new Date(prev.m.timestamp).getTime()) {
      latest.set(key, { m, room: room || 'Unassigned', floor: floor || 'Ground' })
    }
  }

  const devices: SceneDevice[] = []
  for (const [key, { m, room, floor }] of latest) {
    const meta = (m.metadata ?? {}) as Record<string, any>
    devices.push({
      id: key,
      name: md(meta, 'device_name', 'hostname', 'name') || key,
      room,
      floor,
      value: typeof m.value === 'number' ? m.value : undefined,
      unit: m.unit || '°C',
      status: statusFromTemp(typeof m.value === 'number' ? m.value : undefined, m.timestamp),
      lastSeen: m.timestamp,
    })
  }

  const floorMap = new Map<string, Set<string>>()
  for (const d of devices) {
    if (!floorMap.has(d.floor)) floorMap.set(d.floor, new Set())
    floorMap.get(d.floor)!.add(d.room)
  }

  const floors: FacilityFloor[] = [...floorMap.entries()]
    .map(([label, rooms]) => ({ label, rooms: [...rooms].sort((a, b) => a.localeCompare(b)) }))
    .sort((a, b) => floorRank(a.label) - floorRank(b.label) || a.label.localeCompare(b.label))

  return { floors, devices }
}

/** True when no device reported a usable room/floor — caller can show a demo. */
export function isFacilityEmpty(f: Facility): boolean {
  return f.floors.length === 0 || f.devices.length === 0
}

// ── Demo fallback ───────────────────────────────────────────────────────────
// Shown only when no live device reports a room/floor, so the 3D tab still
// renders a representative building instead of an empty shell.

const DEMO_LAYOUT: Record<string, string[]> = {
  Ground: ['Server Room A', 'Server Room B', 'NOC', 'Loading Bay'],
  L1: ['Server Room C', 'Server Room D', 'Battery Room', 'Comms Room'],
  L2: ['Server Room E', 'Server Room F', 'UPS Room'],
}

export function demoFacility(): Facility {
  const floors: FacilityFloor[] = Object.entries(DEMO_LAYOUT).map(([label, rooms]) => ({ label, rooms }))
  const devices: SceneDevice[] = []
  let n = 0
  for (const fl of floors) {
    for (const room of fl.rooms) {
      const perRoom = 3 + (n % 3)
      for (let i = 0; i < perRoom; i++) {
        // deterministic pseudo-temperature so a few rooms show fault/alarm
        const value = 21 + ((n * 7 + i * 13) % 18)
        devices.push({
          id: `demo-${fl.label}-${room}-${i}`.replace(/\s+/g, '-'),
          name: `${room.split(' ').map((w) => w[0]).join('')}-TEMP-${i + 1}`,
          room,
          floor: fl.label,
          value,
          unit: '°C',
          status: statusFromTemp(value),
        })
        n++
      }
    }
  }
  return { floors, devices }
}

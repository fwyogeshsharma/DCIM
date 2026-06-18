/**
 * facilityLayout — turn the flat /facility/layout device rows into a physical
 * building model for the Fire & Safety 3D Facility view.
 *
 * Hierarchy:  building (network_id) → room → rack (rack_num) → devices
 *
 * Each network_id is rendered as its own building, named by its datacenter
 * city / name / address (falling back to the raw network_id). Racks carry the
 * device's real rack_num so the 3D scene can label them exactly as the device
 * inventory shows. Devices with no rack_num drop into an "Unassigned" rack, and
 * devices with no room into an "Unassigned" room — nothing is silently dropped.
 */

import type { FacilityDeviceRow } from './types'

export interface RackDevice {
  id: string
  name: string
  deviceType: string | null
  status: 'online' | 'offline'
  rackUnit: number | null
  mgmtIp: string | null
}

export interface FacilityRack {
  /** Real rack number from the devices table, or null when unassigned. */
  num: number | null
  /** Display label, e.g. "Rack 12" or "Unassigned". */
  label: string
  row: number | null
  devices: RackDevice[]
}

export interface FacilityRoom {
  name: string
  racks: FacilityRack[]
}

export interface FacilityBuilding {
  /** network_id — stable id for the building. */
  id: string
  /** Human label: datacenter city / name / address, else the network_id. */
  name: string
  /** Secondary line under the name (e.g. country, or the network_id itself). */
  subtitle: string
  rooms: FacilityRoom[]
  deviceCount: number
  rackCount: number
}

const UNASSIGNED = 'Unassigned'

function str(v: unknown): string {
  return v == null ? '' : String(v).trim()
}

/** Pick the best human name for a building from a representative device row. */
function buildingName(row: FacilityDeviceRow): string {
  return str(row.datacenter_city) || str(row.datacenter_name) || str(row.network_id)
}

function buildingSubtitle(row: FacilityDeviceRow): string {
  // Prefer the network_id as the subtitle when we used a friendlier name above;
  // otherwise show the country so the line still adds context.
  const name = buildingName(row)
  const parts = [str(row.country), name === str(row.network_id) ? '' : str(row.network_id)]
  return parts.filter(Boolean).join(' · ')
}

/** Group the flat rows into the building → room → rack → device hierarchy. */
export function buildRackFacility(rows: FacilityDeviceRow[] | undefined | null): FacilityBuilding[] {
  const byNet = new Map<string, FacilityDeviceRow[]>()
  for (const r of rows ?? []) {
    const net = str(r.network_id)
    if (!net) continue
    if (!byNet.has(net)) byNet.set(net, [])
    byNet.get(net)!.push(r)
  }

  const buildings: FacilityBuilding[] = []
  for (const [net, devs] of byNet) {
    // room name → rack_num (as string key) → rack
    const roomMap = new Map<string, Map<string, FacilityRack>>()
    for (const d of devs) {
      const roomName = str(d.room) || UNASSIGNED
      if (!roomMap.has(roomName)) roomMap.set(roomName, new Map())
      const rackMap = roomMap.get(roomName)!

      const rackKey = d.rack_num == null ? UNASSIGNED : String(d.rack_num)
      if (!rackMap.has(rackKey)) {
        rackMap.set(rackKey, {
          num: d.rack_num,
          label: d.rack_num == null ? UNASSIGNED : `Rack ${d.rack_num}`,
          row: d.rack_row,
          devices: [],
        })
      }
      rackMap.get(rackKey)!.devices.push({
        id: d.device_id,
        name: d.hostname || d.mgmt_ip || d.device_id,
        deviceType: d.device_type,
        status: d.status,
        rackUnit: d.rack_unit,
        mgmtIp: d.mgmt_ip,
      })
    }

    // Sort: rooms alphabetically (Unassigned last); racks by number (Unassigned
    // last); devices by rack unit then name.
    const rooms: FacilityRoom[] = [...roomMap.entries()]
      .sort(([a], [b]) => rankName(a) - rankName(b) || a.localeCompare(b))
      .map(([name, rackMap]) => ({
        name,
        racks: [...rackMap.values()]
          .sort((a, b) => rankRack(a) - rankRack(b))
          .map((rk) => ({
            ...rk,
            devices: rk.devices.sort(
              (x, y) => (x.rackUnit ?? 1e9) - (y.rackUnit ?? 1e9) || x.name.localeCompare(y.name),
            ),
          })),
      }))

    const deviceCount = devs.length
    const rackCount = rooms.reduce((s, r) => s + r.racks.length, 0)
    const sample = devs[0]
    buildings.push({
      id: net,
      name: buildingName(sample),
      subtitle: buildingSubtitle(sample),
      rooms,
      deviceCount,
      rackCount,
    })
  }

  // Buildings ordered by name for a stable left-to-right placement.
  return buildings.sort((a, b) => a.name.localeCompare(b.name))
}

/** Sort key that pushes the "Unassigned" bucket to the end. */
function rankName(name: string): number {
  return name === UNASSIGNED ? 1 : 0
}

function rankRack(r: FacilityRack): number {
  if (r.num == null) return Number.MAX_SAFE_INTEGER
  return r.num
}

export function isRackFacilityEmpty(buildings: FacilityBuilding[]): boolean {
  return buildings.length === 0 || buildings.every((b) => b.deviceCount === 0)
}

import { Router, Request, Response } from 'express'
import { Pool, PoolClient } from 'pg'

// ── Types ──────────────────────────────────────────────────────────────────────

interface IngestInterface {
  interface_name: string
  interface_index?: number
  interface_description?: string
  interface_type?: string
  interface_mac_address?: string
  speed_mbps?: number
  admin_status?: number
  operational_status?: number
  access_vlan_id?: number
  mtu_bytes?: number
  addresses?: Array<{
    address: string
    address_family?: string
    is_primary?: boolean
    vrf?: string
  }>
}

interface IngestMetric {
  metric_name: string
  tag?: string
  value: number
  ts?: string
  attributes?: Record<string, unknown>
  collector_protocol?: string
  collector_agent?: string
  interface_name?: string
}

interface IngestDevice {
  hostname: string
  device_type: string
  vendor?: string
  model_name?: string
  os_name?: string
  os_version?: string
  sys_oid?: string
  sys_description?: string
  sys_location?: string
  mgmt_ip?: string
  prod_ip?: string
  loopback_ip?: string
  oob_ip?: string
  snmp_enabled?: boolean
  gnmi_enabled?: boolean
  snmp_port?: number
  snmp_version?: number
  gnmi_port?: number
  collector_agent?: string
  country?: string
  datacenter_city?: string
  datacenter?: string
  room?: string
  rack_row?: number
  rack_num?: number
  rack_unit?: number
  power_draw_w?: number
  is_reachable?: boolean
  interfaces?: IngestInterface[]
  metrics?: IngestMetric[]
}

interface IngestTopologyLink {
  layer: string
  src_hostname: string
  src_port_name?: string
  dst_hostname: string
  dst_port_name?: string
  link_speed_mbps?: number
  link_type?: string
  protocol?: string
  is_active?: boolean
}

interface IngestEvent {
  hostname?: string
  source_ip?: string
  kind?: string
  event_name: string
  severity?: string
  trap_oid?: string
  ts?: string
  payload?: Record<string, unknown>
  collector_agent?: string
}

interface IngestPayload {
  org_id: string
  datacenter_id: string
  floor_id: string
  network_id: string
  group_id: string
  devices?: IngestDevice[]
  topology_links?: IngestTopologyLink[]
  events?: IngestEvent[]
}

// ── Router ─────────────────────────────────────────────────────────────────────

export function createIngestRouter(dbPool: Pool): Router {
  const router = Router()

  router.post('/', async (req: Request, res: Response) => {
    const apiKey = req.headers['x-ingest-key']
    if (!apiKey || apiKey !== process.env.INGEST_API_KEY) {
      return res.status(401).json({ success: false, error: 'Invalid or missing X-Ingest-Key' })
    }

    const body: IngestPayload = req.body
    if (!body.org_id || !body.datacenter_id || !body.floor_id || !body.network_id || !body.group_id) {
      return res.status(400).json({ success: false, error: 'org_id, datacenter_id, floor_id, network_id, group_id are required' })
    }

    const client: PoolClient = await dbPool.connect()
    try {
      await client.query('BEGIN')

      // hostname → device UUID lookup (populated as devices are upserted)
      const deviceIdMap = new Map<string, string>()
      // (device_id + interface_name) → interface UUID lookup
      const ifaceIdMap = new Map<string, string>()

      // ── 1. Upsert devices ──────────────────────────────────────────────────
      for (const dev of body.devices ?? []) {
        const { rows } = await client.query<{ id: string }>(`
          INSERT INTO devices (
            org_id, datacenter_id, floor_id, network_id, group_id,
            hostname, device_type, vendor, model_name,
            os_name, os_version, sys_oid, sys_description, sys_location,
            mgmt_ip, prod_ip, loopback_ip, oob_ip,
            snmp_enabled, gnmi_enabled, snmp_port, snmp_version, gnmi_port,
            collector_agent, country, datacenter_city, datacenter, room,
            rack_row, rack_num, rack_unit, power_draw_w,
            is_reachable, last_seen_at, updated_at
          ) VALUES (
            $1,$2,$3,$4,$5,
            $6,$7,$8,$9,
            $10,$11,$12,$13,$14,
            $15,$16,$17,$18,
            $19,$20,$21,$22,$23,
            $24,$25,$26,$27,$28,
            $29,$30,$31,$32,
            $33, now(), now()
          )
          ON CONFLICT (org_id, datacenter_id, floor_id, network_id, group_id, hostname)
          DO UPDATE SET
            device_type      = EXCLUDED.device_type,
            vendor           = COALESCE(EXCLUDED.vendor,           devices.vendor),
            model_name       = COALESCE(EXCLUDED.model_name,       devices.model_name),
            os_name          = COALESCE(EXCLUDED.os_name,          devices.os_name),
            os_version       = COALESCE(EXCLUDED.os_version,       devices.os_version),
            sys_oid          = COALESCE(EXCLUDED.sys_oid,          devices.sys_oid),
            sys_description  = COALESCE(EXCLUDED.sys_description,  devices.sys_description),
            sys_location     = COALESCE(EXCLUDED.sys_location,     devices.sys_location),
            mgmt_ip          = COALESCE(EXCLUDED.mgmt_ip,          devices.mgmt_ip),
            prod_ip          = COALESCE(EXCLUDED.prod_ip,          devices.prod_ip),
            loopback_ip      = COALESCE(EXCLUDED.loopback_ip,      devices.loopback_ip),
            oob_ip           = COALESCE(EXCLUDED.oob_ip,           devices.oob_ip),
            snmp_enabled     = EXCLUDED.snmp_enabled,
            gnmi_enabled     = EXCLUDED.gnmi_enabled,
            snmp_port        = EXCLUDED.snmp_port,
            snmp_version     = EXCLUDED.snmp_version,
            gnmi_port        = EXCLUDED.gnmi_port,
            collector_agent  = EXCLUDED.collector_agent,
            country          = COALESCE(EXCLUDED.country,          devices.country),
            datacenter_city  = COALESCE(EXCLUDED.datacenter_city,  devices.datacenter_city),
            datacenter       = COALESCE(EXCLUDED.datacenter,       devices.datacenter),
            room             = COALESCE(EXCLUDED.room,             devices.room),
            rack_row         = COALESCE(EXCLUDED.rack_row,         devices.rack_row),
            rack_num         = COALESCE(EXCLUDED.rack_num,         devices.rack_num),
            rack_unit        = COALESCE(EXCLUDED.rack_unit,        devices.rack_unit),
            power_draw_w     = COALESCE(EXCLUDED.power_draw_w,     devices.power_draw_w),
            is_reachable     = EXCLUDED.is_reachable,
            last_seen_at     = now(),
            updated_at       = now()
          RETURNING id
        `, [
          body.org_id, body.datacenter_id, body.floor_id, body.network_id, body.group_id,
          dev.hostname, dev.device_type, dev.vendor ?? null, dev.model_name ?? null,
          dev.os_name ?? null, dev.os_version ?? null, dev.sys_oid ?? null,
          dev.sys_description ?? null, dev.sys_location ?? null,
          dev.mgmt_ip ?? null, dev.prod_ip ?? null, dev.loopback_ip ?? null, dev.oob_ip ?? null,
          dev.snmp_enabled ?? false, dev.gnmi_enabled ?? false,
          dev.snmp_port ?? 161, dev.snmp_version ?? 2, dev.gnmi_port ?? 57400,
          dev.collector_agent ?? 'EDR',
          dev.country ?? null, dev.datacenter_city ?? null, dev.datacenter ?? null, dev.room ?? null,
          dev.rack_row ?? null, dev.rack_num ?? null, dev.rack_unit ?? null, dev.power_draw_w ?? null,
          dev.is_reachable ?? true,
        ])

        const deviceId = rows[0].id
        deviceIdMap.set(dev.hostname, deviceId)

        // ── 2. Upsert interfaces ─────────────────────────────────────────────
        for (const iface of dev.interfaces ?? []) {
          const { rows: ifRows } = await client.query<{ id: string }>(`
            INSERT INTO interfaces (
              device_id, interface_name, interface_index, interface_description,
              interface_type, interface_mac_address, speed_mbps,
              admin_status, operational_status, access_vlan_id, mtu_bytes, updated_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now())
            ON CONFLICT (device_id, interface_name) DO UPDATE SET
              interface_index       = COALESCE(EXCLUDED.interface_index,       interfaces.interface_index),
              interface_description = COALESCE(EXCLUDED.interface_description, interfaces.interface_description),
              interface_type        = COALESCE(EXCLUDED.interface_type,        interfaces.interface_type),
              interface_mac_address = COALESCE(EXCLUDED.interface_mac_address, interfaces.interface_mac_address),
              speed_mbps            = COALESCE(EXCLUDED.speed_mbps,            interfaces.speed_mbps),
              admin_status          = EXCLUDED.admin_status,
              operational_status    = EXCLUDED.operational_status,
              access_vlan_id        = COALESCE(EXCLUDED.access_vlan_id,        interfaces.access_vlan_id),
              mtu_bytes             = COALESCE(EXCLUDED.mtu_bytes,             interfaces.mtu_bytes),
              updated_at            = now()
            RETURNING id
          `, [
            deviceId, iface.interface_name, iface.interface_index ?? null,
            iface.interface_description ?? null, iface.interface_type ?? null,
            iface.interface_mac_address ?? null, iface.speed_mbps ?? null,
            iface.admin_status ?? 1, iface.operational_status ?? 1,
            iface.access_vlan_id ?? null, iface.mtu_bytes ?? null,
          ])

          const ifaceId = ifRows[0].id
          ifaceIdMap.set(`${deviceId}:${iface.interface_name}`, ifaceId)

          // ── 3. Upsert interface addresses ──────────────────────────────────
          for (const addr of iface.addresses ?? []) {
            await client.query(`
              INSERT INTO interface_addresses (interface_id, address, address_family, is_primary, vrf, updated_at)
              VALUES ($1,$2,$3,$4,$5,now())
              ON CONFLICT (interface_id, address) DO UPDATE SET
                address_family = EXCLUDED.address_family,
                is_primary     = EXCLUDED.is_primary,
                vrf            = COALESCE(EXCLUDED.vrf, interface_addresses.vrf),
                updated_at     = now()
            `, [
              ifaceId, addr.address, addr.address_family ?? 'ipv4',
              addr.is_primary ?? true, addr.vrf ?? null,
            ])
          }
        }

        // ── 4. Insert metrics (ignore duplicates) ────────────────────────────
        for (const m of dev.metrics ?? []) {
          const ifaceKey = m.interface_name ? `${deviceId}:${m.interface_name}` : undefined
          const ifaceId = ifaceKey ? (ifaceIdMap.get(ifaceKey) ?? null) : null
          await client.query(`
            INSERT INTO metrics (device_id, ts, metric_name, tag, value, attributes,
                                 collector_agent, collector_protocol, interface_id)
            VALUES ($1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (device_id, metric_name, tag, ts) DO NOTHING
          `, [
            deviceId, m.ts ?? 'now()', m.metric_name, m.tag ?? '', m.value,
            m.attributes ? JSON.stringify(m.attributes) : null,
            m.collector_agent ?? 'EDR', m.collector_protocol ?? 'SNMP',
            ifaceId,
          ])
        }
      }

      // ── 5. Upsert topology links ───────────────────────────────────────────
      for (const link of body.topology_links ?? []) {
        const srcId = deviceIdMap.get(link.src_hostname)
        const dstId = deviceIdMap.get(link.dst_hostname)
        if (!srcId || !dstId) continue

        const srcIfaceKey = link.src_port_name ? `${srcId}:${link.src_port_name}` : undefined
        const dstIfaceKey = link.dst_port_name ? `${dstId}:${link.dst_port_name}` : undefined

        await client.query(`
          INSERT INTO topology_links (
            layer, src_device_id, src_port_name, src_interface_id,
            dst_device_id, dst_port_name, dst_interface_id,
            link_speed_mbps, link_type, protocol, is_active, updated_at
          ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now())
          ON CONFLICT (layer, src_device_id, src_port_name, dst_device_id) DO UPDATE SET
            dst_port_name    = EXCLUDED.dst_port_name,
            src_interface_id = EXCLUDED.src_interface_id,
            dst_interface_id = EXCLUDED.dst_interface_id,
            link_speed_mbps  = COALESCE(EXCLUDED.link_speed_mbps, topology_links.link_speed_mbps),
            link_type        = COALESCE(EXCLUDED.link_type,        topology_links.link_type),
            protocol         = EXCLUDED.protocol,
            is_active        = EXCLUDED.is_active,
            updated_at       = now()
        `, [
          link.layer,
          srcId, link.src_port_name ?? '',
          srcIfaceKey ? (ifaceIdMap.get(srcIfaceKey) ?? null) : null,
          dstId, link.dst_port_name ?? '',
          dstIfaceKey ? (ifaceIdMap.get(dstIfaceKey) ?? null) : null,
          link.link_speed_mbps ?? null, link.link_type ?? null,
          link.protocol ?? 'lldp', link.is_active ?? true,
        ])
      }

      // ── 6. Insert events ───────────────────────────────────────────────────
      for (const ev of body.events ?? []) {
        const deviceId = ev.hostname ? (deviceIdMap.get(ev.hostname) ?? null) : null
        await client.query(`
          INSERT INTO events (device_id, source_hostname, ts, kind, event_name, severity,
                              trap_oid, source_ip, event_payload, collector_agent)
          VALUES ($1,$2,$3::timestamptz,$4,$5,$6,$7,$8::inet,$9,$10)
        `, [
          deviceId, ev.hostname ?? null, ev.ts ?? 'now()',
          ev.kind ?? 'event', ev.event_name, ev.severity ?? 'informational',
          ev.trap_oid ?? null, ev.source_ip ?? null,
          ev.payload ? JSON.stringify(ev.payload) : null,
          ev.collector_agent ?? 'EDR',
        ])
      }

      await client.query('COMMIT')

      res.status(201).json({
        success: true,
        ingested: {
          devices:        (body.devices ?? []).length,
          topology_links: (body.topology_links ?? []).length,
          events:         (body.events ?? []).length,
        },
      })
    } catch (err: any) {
      await client.query('ROLLBACK')
      res.status(500).json({ success: false, error: err.message })
    } finally {
      client.release()
    }
  })

  return router
}
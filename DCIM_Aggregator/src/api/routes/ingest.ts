import { Router, Request, Response } from 'express'
import { Pool, PoolClient } from 'pg'
import { emitSSEEvent } from '../../events/sseEmitter'

// ── Types ──────────────────────────────────────────────────────────────────────

interface IngestInterface {
  id?: string
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
    id?: string
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
  id?: string
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
  device_role?: string
  role_confidence?: number
  role_source?: string
  is_reachable?: boolean
  interfaces?: IngestInterface[]
  metrics?: IngestMetric[]
}

interface IngestTopologyLink {
  id?: string
  layer: string
  src_hostname: string
  src_port_name?: string
  dst_hostname: string
  dst_port_name?: string
  link_speed_mbps?: number
  link_type?: string
  protocol?: string
  is_active?: boolean
  relation?: string
}

interface IngestEvent {
  id?: string
  device_id?: string
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

// Thresholds for auto-generating alert events from ingested metrics.
const METRIC_THRESHOLDS: Record<string, { warn: number; crit: number; unit: string }> = {
  cpu_util:      { warn: 80, crit: 90, unit: '%' },
  mem_util:      { warn: 80, crit: 90, unit: '%' },
  disk_util:     { warn: 85, crit: 95, unit: '%' },
  temperature:   { warn: 45, crit: 55, unit: '°C' },
  if_error_rate: { warn: 1,  crit: 5,  unit: '%' },
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
      // Trap events seen in this payload — emitted over SSE after COMMIT so the
      // topology page reacts live (highlights the node, draws link-down).
      const trapsToEmit: Array<{
        deviceId: string | null
        source_ip: string | null
        device_name: string | null
        trap_type: string
        trap_oid: string | null
        severity: string
        description: string
        timestamp: string | null
      }> = []

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
            is_reachable, last_seen_at, updated_at, id,
            device_role, role_confidence, role_source
          ) VALUES (
                     $1,$2,$3,$4,$5,
                     $6,$7,$8,$9,
                     $10,$11,$12,$13,$14,
                     $15,$16,$17,$18,
                     $19,$20,$21,$22,$23,
                     $24,$25,$26,$27,$28,
                     $29,$30,$31,$32,
                     $33, now(), now(), COALESCE($34::uuid, gen_random_uuid()),
                     $35, $36, $37
                   )
            ON CONFLICT (id)
          DO UPDATE SET
            hostname         = EXCLUDED.hostname,
                           device_role      = EXCLUDED.device_role,
                           role_confidence  = EXCLUDED.role_confidence,
                           role_source      = EXCLUDED.role_source,
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
          dev.id ?? null,
          dev.device_role ?? null, dev.role_confidence ?? null, dev.role_source ?? null,
        ])

        const deviceId = rows[0].id
        deviceIdMap.set(dev.hostname, deviceId)

        // ── 2. Upsert interfaces ─────────────────────────────────────────────
        for (const iface of dev.interfaces ?? []) {
          // The interfaces table has TWO unique keys: (device_id, interface_name)
          // and partial (device_id, interface_index) [uix_iface_device_idx]. A
          // single ON CONFLICT arbiter can't cover both, so a renamed port (same
          // ifIndex, new ifName) or a reindexed port (same name, new ifIndex)
          // throws a duplicate-key on whichever key isn't the arbiter. Pre-delete
          // any DIFFERENT row that collides on either key, then upsert by primary
          // key (id). Steady state deletes nothing (the live row matches by id),
          // so this neither churns ids nor nulls metric→interface links.
          await client.query(`
            DELETE FROM interfaces
            WHERE device_id = $1
              AND (interface_name = $2 OR ($3::int IS NOT NULL AND interface_index = $3))
              AND ($4::uuid IS NULL OR id <> $4)
          `, [deviceId, iface.interface_name, iface.interface_index ?? null, iface.id ?? null])

          const { rows: ifRows } = await client.query<{ id: string }>(`
            INSERT INTO interfaces (
              device_id, interface_name, interface_index, interface_description,
              interface_type, interface_mac_address, speed_mbps,
              admin_status, operational_status, access_vlan_id, mtu_bytes, updated_at, id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now(), COALESCE($12::uuid, gen_random_uuid()))
              ON CONFLICT (id) DO UPDATE SET
              interface_name        = EXCLUDED.interface_name,
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
            iface.id ?? null,
          ])

          const ifaceId = ifRows[0].id
          ifaceIdMap.set(`${deviceId}:${iface.interface_name}`, ifaceId)

          // ── 3. Upsert interface addresses ──────────────────────────────────
          for (const addr of iface.addresses ?? []) {
            await client.query(`
              INSERT INTO interface_addresses (interface_id, address, address_family, is_primary, vrf, updated_at, id)
              VALUES ($1,$2,$3,$4,$5,now(), COALESCE($6::uuid, gen_random_uuid()))
                ON CONFLICT (interface_id, address) DO UPDATE SET
                address_family = EXCLUDED.address_family,
                                                         is_primary     = EXCLUDED.is_primary,
                                                         vrf            = COALESCE(EXCLUDED.vrf, interface_addresses.vrf),
                                                         updated_at     = now()
            `, [
              ifaceId, addr.address, addr.address_family ?? 'ipv4',
              addr.is_primary ?? true, addr.vrf ?? null, addr.id ?? null,
            ])
          }
        }

        // ── 4. Insert metrics + auto-generate threshold alerts ───────────────
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

          const thr = METRIC_THRESHOLDS[m.metric_name]
          if (thr && m.value >= thr.warn) {
            const severity = m.value >= thr.crit ? 'critical' : 'warning'
            const threshold = m.value >= thr.crit ? thr.crit : thr.warn
            // Skip if an active alert for this device+metric already exists within 10 min
            const { rows: existing } = await client.query(`
              SELECT id FROM events
              WHERE device_id = $1
                AND event_name = $2
                AND COALESCE((event_payload->>'resolved')::boolean, false) = false
                AND ts >= NOW() - INTERVAL '10 minutes'
                LIMIT 1
            `, [deviceId, m.metric_name])
            if (existing.length === 0) {
              await client.query(`
                INSERT INTO events (device_id, source_hostname, ts, kind, event_name, severity,
                                    source_ip, event_payload, collector_agent)
                VALUES ($1,$2,now(),'alert',$3,$4,$5::inet,$6,$7)
              `, [
                deviceId, dev.hostname, m.metric_name, severity,
                dev.mgmt_ip ?? null,
                JSON.stringify({
                  message: `${m.metric_name} is ${m.value}${thr.unit} (threshold: ${threshold}${thr.unit})`,
                  actual_value: m.value,
                  threshold_value: threshold,
                  resolved: false,
                }),
                m.collector_agent ?? 'EDR',
              ])
            }
          }
        }
      }

      // ── 5. Upsert topology links ───────────────────────────────────────────
      // Resolve a link endpoint to a device UUID. Endpoints upserted in THIS
      // payload are already in deviceIdMap; but the far end of an inter-datacenter
      // / cross-floor cable lives in a DIFFERENT (dc,floor) payload, so it won't
      // be in the map — fall back to a DB lookup by hostname within the tenant.
      // Without this, every cross-scope link hit `!srcId || !dstId` and was
      // silently dropped ("topology_links always misses some entries").
      const resolveEndpoint = async (hostname: string): Promise<string | null> => {
        const cached = deviceIdMap.get(hostname)
        if (cached) return cached
        const r = await client.query<{ id: string }>(
            `SELECT id FROM devices
             WHERE org_id = $1 AND network_id = $2 AND group_id = $3 AND hostname = $4
               LIMIT 1`,
            [body.org_id, body.network_id, body.group_id, hostname],
        )
        const id = r.rows[0]?.id ?? null
        if (id) deviceIdMap.set(hostname, id)
        return id
      }

      for (const link of body.topology_links ?? []) {
        const srcId = await resolveEndpoint(link.src_hostname)
        const dstId = await resolveEndpoint(link.dst_hostname)
        if (!srcId || !dstId) continue

        const srcIfaceKey = link.src_port_name ? `${srcId}:${link.src_port_name}` : undefined
        const dstIfaceKey = link.dst_port_name ? `${dstId}:${link.dst_port_name}` : undefined

        await client.query(`
          INSERT INTO topology_links (
            layer, src_device_id, src_port_name, src_interface_id,
            dst_device_id, dst_port_name, dst_interface_id,
            link_speed_mbps, link_type, protocol, is_active, relation, updated_at, id
          ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now(), COALESCE($13::uuid, gen_random_uuid()))
            ON CONFLICT (layer, src_device_id, src_port_name, dst_device_id) DO UPDATE SET
            dst_port_name    = EXCLUDED.dst_port_name,
                                                                                  src_interface_id = EXCLUDED.src_interface_id,
                                                                                  dst_interface_id = EXCLUDED.dst_interface_id,
                                                                                  link_speed_mbps  = COALESCE(EXCLUDED.link_speed_mbps, topology_links.link_speed_mbps),
                                                                                  link_type        = COALESCE(EXCLUDED.link_type,        topology_links.link_type),
                                                                                  protocol         = EXCLUDED.protocol,
                                                                                  is_active        = EXCLUDED.is_active,
                                                                                  relation         = EXCLUDED.relation,
                                                                                  updated_at       = now()
        `, [
          link.layer,
          srcId, link.src_port_name ?? '',
          srcIfaceKey ? (ifaceIdMap.get(srcIfaceKey) ?? null) : null,
          dstId, link.dst_port_name ?? '',
          dstIfaceKey ? (ifaceIdMap.get(dstIfaceKey) ?? null) : null,
          link.link_speed_mbps ?? null, link.link_type ?? null,
          link.protocol ?? 'lldp', link.is_active ?? true,
          link.relation ?? 'peer', link.id ?? null,
        ])
      }

      // ── 6. Insert events ───────────────────────────────────────────────────
      for (const ev of body.events ?? []) {
        const deviceId = (ev.hostname ? deviceIdMap.get(ev.hostname) : undefined) ?? ev.device_id ?? null
        await client.query(`
          INSERT INTO events (device_id, source_hostname, ts, kind, event_name, severity,
                              trap_oid, source_ip, event_payload, collector_agent, id)
          VALUES ($1,$2,$3::timestamptz,$4,$5,$6,$7,$8::inet,$9,$10, COALESCE($11::uuid, gen_random_uuid()))
        `, [
          deviceId, ev.hostname ?? null, ev.ts ?? 'now()',
          ev.kind ?? 'event', ev.event_name, ev.severity ?? 'informational',
          ev.trap_oid ?? null, ev.source_ip ?? null,
          ev.payload ? JSON.stringify(ev.payload) : null,
          ev.collector_agent ?? 'EDR',
          ev.id ?? null,
        ])

        if ((ev.kind ?? 'event') === 'trap') {
          const msg = ev.payload?.message
          trapsToEmit.push({
            deviceId,
            source_ip:   ev.source_ip ?? null,
            device_name: ev.hostname ?? null,
            trap_type:   ev.event_name,
            trap_oid:    ev.trap_oid ?? null,
            severity:    ev.severity ?? 'critical',
            description: typeof msg === 'string' ? msg : ev.event_name,
            timestamp:   ev.ts ?? null,
          })
        }
      }

      await client.query('COMMIT')

      // Push live trap events to connected topology clients (SSE). Resolve each
      // trap's hostname + mgmt_ip from the device row so the topology can match
      // the node by either key. Runs after COMMIT — best-effort, never fails the
      // ingest if a client write or lookup errors.
      if (trapsToEmit.length > 0) {
        const ids = [...new Set(trapsToEmit.map(t => t.deviceId).filter(Boolean))] as string[]
        const meta = new Map<string, { hostname: string | null; mgmt_ip: string | null }>()
        if (ids.length > 0) {
          try {
            const { rows } = await dbPool.query(
                `SELECT id::text AS id, hostname, mgmt_ip::text AS mgmt_ip FROM devices WHERE id = ANY($1::uuid[])`,
                [ids]
            )
            for (const r of rows) meta.set(r.id, { hostname: r.hostname, mgmt_ip: r.mgmt_ip })
          } catch { /* non-fatal — fall back to event-supplied fields */ }
        }
        for (const t of trapsToEmit) {
          const m = t.deviceId ? meta.get(t.deviceId) : undefined
          emitSSEEvent('trap', {
            source_ip:   t.source_ip   ?? m?.mgmt_ip  ?? null,
            device_name: t.device_name ?? m?.hostname ?? null,
            trap_type:   t.trap_type,
            trap_oid:    t.trap_oid ?? '',
            severity:    t.severity,
            description: t.description,
            timestamp:   t.timestamp ?? new Date().toISOString(),
          })
        }
      }

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
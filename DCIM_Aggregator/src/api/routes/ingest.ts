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

// BACnet/IP energy reading (Verdigris EV2 power meters). Stored in the dedicated
// energy_metrics hypertable, not the generic metrics table. circuit/phase are
// first-class columns (derived from tag when not explicitly forwarded).
interface IngestEnergyMetric {
  metric_name: string
  tag?: string
  circuit?: string
  phase?: string
  value: number
  scope?: string            // it|cooling|facility — meter classification for PUE/DCiE
  ts?: string
  attributes?: Record<string, unknown>
  collector_protocol?: string
  collector_agent?: string
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
  role_overridden?: boolean   // admin-set role; classifier never touches these rows
  is_reachable?: boolean
  power_state?: number   // 1=On, 2=Off (chassis power, from Redfish)
  interfaces?: IngestInterface[]
  metrics?: IngestMetric[]
  energy_metrics?: IngestEnergyMetric[]
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
  // Link-event correlation (migration 004). A linkDown/linkUp that DCS correlated
  // to a topology_links row carries the peer endpoint + the link id. All NULL for
  // non-link events.
  src_port_name?: string
  dst_hostname?: string
  dst_port_name?: string
  dst_device_id?: string
  link_id?: string
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
  // Change-feed cursor: DCS sends the cursor it last received; the response
  // carries every UI edit (SNMP SET / Redfish field) made since then so DCS can
  // apply them to the live device objects. Omit on first contact to just receive
  // a fresh cursor without a backlog.
  ui_changes_since?: string
  // Up-feeds from DCS: results of applied commands + current threshold values.
  command_status?: Array<{ device_ip: string; field: string; status: string; error?: string }>
  threshold_changes?: Array<{ device_ip: string; hostname?: string; rule: string; value: number }>
}

// Thresholds for auto-generating alert events from ingested metrics.
const METRIC_THRESHOLDS: Record<string, { warn: number; crit: number; unit: string }> = {
  cpu_util:      { warn: 80, crit: 90, unit: '%' },
  mem_util:      { warn: 80, crit: 90, unit: '%' },
  disk_util:     { warn: 85, crit: 95, unit: '%' },
  temperature:   { warn: 45, crit: 55, unit: '°C' },
  if_error_rate: { warn: 1,  crit: 5,  unit: '%' },
}

// ── Failure-risk flag (devices.at_risk) ─────────────────────────────────────────
// Core rules evaluated to decide whether a device is "likely to fail". For each
// rule we take the worst (max) recent value across the metric names that feed it
// and compare against the device's configured threshold (device_thresholds,
// falling back to the default). Higher value = worse for all of these.
const RISK_RULE_DEFAULTS: Record<string, number> = {
  HighCPU:         90,
  HighMemory:      85,
  HighTemperature: 60,
}
const RISK_RULE_LABEL: Record<string, { label: string; unit: string }> = {
  HighCPU:         { label: 'CPU',  unit: '%'  },
  HighMemory:      { label: 'Mem',  unit: '%'  },
  HighTemperature: { label: 'Temp', unit: '°C' },
}
// metric_name → the rule it contributes to. Multiple collectors report the same
// signal under different names; whichever is highest drives the rule.
const RISK_METRIC_RULES: Record<string, string> = {
  cpu_util:                            'HighCPU',
  'system.cpu_utilization_percent':    'HighCPU',
  'server.cpu_percent':                'HighCPU',
  mem_util:                            'HighMemory',
  'system.memory_utilization_percent': 'HighMemory',
  'server.memory_used_percent':        'HighMemory',
  temperature:                         'HighTemperature',
}
// Clear the flag only once a metric falls this fraction below its threshold, so a
// value hovering right at the limit doesn't flicker the flag on and off.
const RISK_HYSTERESIS = 0.05

// Recompute devices.at_risk from the device's recent metrics. Called at ingest
// time whenever a payload carries a metric that feeds one of the core rules.
async function evaluateDeviceRisk(client: PoolClient, deviceId: string): Promise<void> {
  const metricNames = Object.keys(RISK_METRIC_RULES)

  // Latest value per relevant metric in a short window — robust even when the
  // current payload only carried a subset of the metrics.
  const { rows: latest } = await client.query<{ metric_name: string; value: number }>(`
    SELECT DISTINCT ON (metric_name) metric_name, value
    FROM metrics
    WHERE device_id = $1
      AND metric_name = ANY($2)
      AND ts >= NOW() - INTERVAL '15 minutes'
    ORDER BY metric_name, ts DESC
  `, [deviceId, metricNames])
  if (latest.length === 0) return

  // Per-device threshold overrides for the core rules (fall back to defaults).
  const { rows: thrRows } = await client.query<{ rule: string; value: number }>(
    `SELECT rule, value FROM device_thresholds WHERE device_id = $1 AND rule = ANY($2)`,
    [deviceId, Object.keys(RISK_RULE_DEFAULTS)],
  )
  const thresholds: Record<string, number> = { ...RISK_RULE_DEFAULTS }
  for (const r of thrRows) thresholds[r.rule] = Number(r.value)

  // Worst observed value per rule.
  const ruleValue: Record<string, number> = {}
  for (const row of latest) {
    const rule = RISK_METRIC_RULES[row.metric_name]
    const v = Number(row.value)
    if (!(rule in ruleValue) || v > ruleValue[rule]) ruleValue[rule] = v
  }

  const breaches: string[] = []
  let anyElevated = false   // above the clear-threshold but below breach → hysteresis band
  for (const [rule, value] of Object.entries(ruleValue)) {
    const threshold = thresholds[rule]
    if (value >= threshold) {
      const { label, unit } = RISK_RULE_LABEL[rule]
      breaches.push(`${label} ${Math.round(value)}${unit} ≥ ${threshold}${unit}`)
    } else if (value > threshold * (1 - RISK_HYSTERESIS)) {
      anyElevated = true
    }
  }

  const { rows: cur } = await client.query<{ at_risk: boolean }>(
    `SELECT at_risk FROM devices WHERE id = $1`, [deviceId])
  const wasAtRisk = cur[0]?.at_risk ?? false

  let nextRisk: boolean
  let reason: string | null
  if (breaches.length > 0) {
    nextRisk = true
    reason = breaches.join('; ')
  } else if (wasAtRisk && anyElevated) {
    return                    // hold inside the hysteresis band — leave flag + reason as-is
  } else {
    nextRisk = false
    reason = null
  }

  if (!nextRisk && !wasAtRisk) return   // already clear, nothing to write
  await client.query(
    `UPDATE devices SET at_risk = $2, risk_reason = $3, risk_updated_at = now() WHERE id = $1`,
    [deviceId, nextRisk, reason],
  )
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
        dstDeviceId: string | null
        dst_hostname: string | null
        trap_type: string
        trap_oid: string | null
        severity: string
        description: string
        alarm_state: string | null
        timestamp: string | null
      }> = []

      // ── 1. Upsert devices ──────────────────────────────────────────────────
      for (const dev of body.devices ?? []) {
        // Stable-identity upsert. Match an existing row first by mgmt_ip (stable
        // across BOTH a hostname change and a DCS DB reset, which regenerates
        // device ids), then by hostname. The matched row is UPDATEd in place — so
        // a rename never inserts a duplicate, and a forwarded id that no longer
        // matches any row (post-reset) never trips the uix_devices_identity
        // hostname index (the cause of the duplicate-key 500s). Only a genuinely
        // new device is INSERTed, adopting the forwarded id.
        const found = await client.query<{ id: string }>(
            `SELECT id FROM devices
             WHERE org_id = $1 AND network_id = $2 AND group_id = $3
               AND ( ($4::inet IS NOT NULL AND mgmt_ip = $4::inet)
               OR (datacenter_id = $5 AND floor_id = $6 AND hostname = $7) )
             ORDER BY ($4::inet IS NOT NULL AND mgmt_ip = $4::inet) DESC
               LIMIT 1`,
            [body.org_id, body.network_id, body.group_id, dev.mgmt_ip ?? null,
              body.datacenter_id, body.floor_id, dev.hostname],
        )

        // Shared column values $1..$31 — identical for the UPDATE and the INSERT.
        const dp = [
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
          dev.device_role ?? null, dev.role_confidence ?? null, dev.role_source ?? null,
          dev.power_state ?? null,
          dev.role_overridden ?? null,
        ]

        let deviceId: string
        if (found.rows[0]) {
          deviceId = found.rows[0].id
          await client.query(`
            UPDATE devices SET
                             hostname=$1, device_type=$2,
                             vendor=COALESCE($3,vendor), model_name=COALESCE($4,model_name),
                             os_name=COALESCE($5,os_name), os_version=COALESCE($6,os_version),
                             sys_oid=COALESCE($7,sys_oid), sys_description=COALESCE($8,sys_description),
                             sys_location=COALESCE($9,sys_location),
                             mgmt_ip=COALESCE($10::inet,mgmt_ip), prod_ip=COALESCE($11::inet,prod_ip),
                             loopback_ip=COALESCE($12::inet,loopback_ip), oob_ip=COALESCE($13::inet,oob_ip),
                             snmp_enabled=$14, gnmi_enabled=$15, snmp_port=$16, snmp_version=$17, gnmi_port=$18,
                             collector_agent=$19,
                             country=COALESCE($20,country), datacenter_city=COALESCE($21,datacenter_city),
                             datacenter=COALESCE($22,datacenter), room=COALESCE($23,room),
                             rack_row=COALESCE($24,rack_row), rack_num=COALESCE($25,rack_num),
                             rack_unit=COALESCE($26,rack_unit), power_draw_w=COALESCE($27,power_draw_w),
                             is_reachable=$28, device_role=$29, role_confidence=$30, role_source=$31,
                             power_state=COALESCE($32,power_state),
                             role_overridden=COALESCE($33,role_overridden),
                             last_seen_at=now(), updated_at=now()
            WHERE id=$34
          `, [...dp, deviceId])
        } else {
          const { rows } = await client.query<{ id: string }>(`
            INSERT INTO devices (
              hostname, device_type, vendor, model_name,
              os_name, os_version, sys_oid, sys_description, sys_location,
              mgmt_ip, prod_ip, loopback_ip, oob_ip,
              snmp_enabled, gnmi_enabled, snmp_port, snmp_version, gnmi_port,
              collector_agent, country, datacenter_city, datacenter, room,
              rack_row, rack_num, rack_unit, power_draw_w,
              is_reachable, device_role, role_confidence, role_source, power_state, role_overridden,
              org_id, datacenter_id, floor_id, network_id, group_id,
              last_seen_at, updated_at, id
            ) VALUES (
                       $1,$2,$3,$4,$5,$6,$7,$8,$9,
                       $10::inet,$11::inet,$12::inet,$13::inet,
                       $14,$15,$16,$17,$18,$19,
                       $20,$21,$22,$23,$24,$25,$26,$27,
                       $28,$29,$30,$31,$32,$33,
                       $35,$36,$37,$38,$39,
                       now(), now(), COALESCE($34::uuid, gen_random_uuid())
                     ) RETURNING id
          `, [...dp, dev.id ?? null,
            body.org_id, body.datacenter_id, body.floor_id, body.network_id, body.group_id])
          deviceId = rows[0].id
        }
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

        // ── 4a. Failure-risk flag: breach of this device's thresholds ─────────
        // If a core metric (CPU/Memory/Temperature) crossed the device's
        // configured threshold, flag it as "at risk" so topology 2D/3D + the
        // inventory table surface it before it actually fails. Only runs when the
        // payload carried a relevant metric; cleared with hysteresis.
        if ((dev.metrics ?? []).some(m => RISK_METRIC_RULES[m.metric_name])) {
          await evaluateDeviceRisk(client, deviceId)
        }

        // ── 4b. Insert energy metrics → dedicated energy_metrics table ────────
        for (const e of dev.energy_metrics ?? []) {
          const tag = e.tag ?? ''
          const circuit = e.circuit ?? (tag.startsWith('Ckt') ? tag : '')
          const phase = e.phase ?? (tag === 'PhA' || tag === 'PhB' || tag === 'PhC' ? tag : '')
          await client.query(`
            INSERT INTO energy_metrics (device_id, ts, metric_name, tag, circuit, phase, value,
                                        scope, attributes, collector_agent, collector_protocol)
            VALUES ($1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9, $10, $11)
              ON CONFLICT (device_id, metric_name, tag, ts) DO NOTHING
          `, [
            deviceId, e.ts ?? 'now()', e.metric_name, tag, circuit, phase, e.value,
            e.scope ?? '',
            e.attributes ? JSON.stringify(e.attributes) : null,
            e.collector_agent ?? 'EDR', e.collector_protocol ?? 'BACNET',
          ])
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
        // Resolve the peer endpoint for link events (migration 004): prefer a
        // forwarded dst_device_id, else look up dst_hostname within the tenant —
        // same resolver the topology links use, so a cross-(dc,floor) peer that
        // isn't in this payload still resolves.
        const dstDeviceId = ev.dst_hostname
            ? (await resolveEndpoint(ev.dst_hostname)) ?? ev.dst_device_id ?? null
            : ev.dst_device_id ?? null
        await client.query(`
          INSERT INTO events (device_id, source_hostname, ts, kind, event_name, severity,
                              trap_oid, source_ip, event_payload, collector_agent,
                              src_port_name, dst_device_id, dst_hostname, dst_port_name, link_id, id)
          VALUES ($1,$2,$3::timestamptz,$4,$5,$6,$7,$8::inet,$9,$10,
                  $11,$12::uuid,$13,$14,$15::uuid, COALESCE($16::uuid, gen_random_uuid()))
        `, [
          deviceId, ev.hostname ?? null, ev.ts ?? 'now()',
          ev.kind ?? 'event', ev.event_name, ev.severity ?? 'informational',
          ev.trap_oid ?? null, ev.source_ip ?? null,
          ev.payload ? JSON.stringify(ev.payload) : null,
          ev.collector_agent ?? 'EDR',
          ev.src_port_name ?? null, dstDeviceId, ev.dst_hostname ?? null,
          ev.dst_port_name ?? null, ev.link_id ?? null,
          ev.id ?? null,
        ])

        // ── 6a. Auto-resolve the matching linkDown on a linkUp/restore ─────────
        // A linkUp means the link came back, so the still-open linkDown for the
        // same link should stop showing as an active alert. Correlate on link_id
        // when DCS forwarded one, else on the endpoint pair (source_hostname +
        // dst_hostname) in EITHER orientation — the down may have been recorded
        // from the peer's side. Derive the counterpart name from this event's name
        // (linkUp→linkDown, OOBSwitchLinkUp→OOBSwitchLinkDown, …) so vendor
        // variants pair up too.
        const evNameLower = (ev.event_name ?? '').toLowerCase()
        if (evNameLower.includes('linkup')) {
          const downName = evNameLower.replace('linkup', 'linkdown')
          await client.query(`
            UPDATE events SET
              event_payload = COALESCE(event_payload, '{}'::jsonb)
                || jsonb_build_object('resolved', true, 'resolved_at', now()::text)
            WHERE LOWER(event_name) = $1
              AND COALESCE((event_payload->>'resolved')::boolean, false) = false
              AND (
              ($2::uuid IS NOT NULL AND link_id = $2::uuid)
                OR ($2::uuid IS NULL AND $3::text IS NOT NULL AND $4::text IS NOT NULL
                    AND ( (source_hostname = $3::text AND dst_hostname = $4::text)
                       OR (source_hostname = $4::text AND dst_hostname = $3::text) ))
              )
          `, [downName, ev.link_id ?? null, ev.hostname ?? null, ev.dst_hostname ?? null])
        }

        // Emit traps, device-change notifications and alarms live to the topology
        // (all surface in the SNMP-trap feed / node highlight). trap_oid may be
        // null for the non-SNMP kinds — that's fine, the UI doesn't require it.
        if (['trap', 'device_change', 'alarm'].includes(ev.kind ?? 'event')) {
          const msg = ev.payload?.message
          trapsToEmit.push({
            deviceId,
            source_ip:   ev.source_ip ?? null,
            device_name: ev.hostname ?? null,
            dstDeviceId: dstDeviceId,
            dst_hostname: ev.dst_hostname ?? null,
            trap_type:   ev.event_name,
            trap_oid:    ev.trap_oid ?? null,
            severity:    ev.severity ?? 'critical',
            description: typeof msg === 'string' ? msg : ev.event_name,
            alarm_state: typeof (ev.payload as any)?.alarm_state === 'string' ? (ev.payload as any).alarm_state : null,
            timestamp:   ev.ts ?? null,
          })
        }
      }

      await client.query('COMMIT')

      // ── 5+6. Control-plane up-feeds — AFTER COMMIT on the pool, so a consume
      // error can't poison the device-upsert transaction or block the down-feed.
      // (command results → rule_changes lifecycle; threshold values → store + confirm)
      try {
        for (const cs of body.command_status ?? []) {
          if (!cs.device_ip || !cs.field) continue
          await dbPool.query(`
            UPDATE rule_changes
            SET status = $3,
                error  = COALESCE($4, ''),
                applied_at = CASE WHEN $3 = 'applied' THEN now() ELSE applied_at END,
                updated_at = now()
            WHERE device_ip = $1 AND field = $2
              AND status IN ('pending','applied')
          `, [cs.device_ip, cs.field, cs.status, cs.error ?? ''])
        }
        for (const t of body.threshold_changes ?? []) {
          if (!t.device_ip || !t.rule) continue
          // device_thresholds is keyed by device_id (UUID) — resolve via mgmt IP.
          // device_ip may be CIDR ("10.20.0.5/32"); ::inet matches the host.
          const dres = await dbPool.query<{ id: string }>(
              `SELECT id FROM devices WHERE mgmt_ip = $1::inet LIMIT 1`, [t.device_ip])
          const did = dres.rows[0]?.id
          if (!did) continue
          await dbPool.query(`
            INSERT INTO device_thresholds (device_id, rule, value, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (device_id, rule)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
          `, [did, t.rule, t.value])
          await dbPool.query(`
            UPDATE rule_changes
            SET status = 'confirmed', confirmed_at = now(), updated_at = now()
            WHERE device_ip = $1 AND field = $2
              AND new_value = $3::text
              AND status IN ('pending','applied')
          `, [t.device_ip, t.rule, String(t.value)])
        }
      } catch (e: any) {
        console.error('ingest: control-plane consume failed (non-fatal):', e?.message)
      }

      // Push live trap events to connected topology clients (SSE). Resolve each
      // trap's hostname + mgmt_ip from the device row so the topology can match
      // the node by either key. Runs after COMMIT — best-effort, never fails the
      // ingest if a client write or lookup errors.
      if (trapsToEmit.length > 0) {
        // Resolve hostname + mgmt_ip for BOTH endpoints (source and peer) so the
        // topology can match the exact link by either key on either end.
        const ids = [...new Set(
            trapsToEmit.flatMap(t => [t.deviceId, t.dstDeviceId]).filter(Boolean)
        )] as string[]
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
          const m  = t.deviceId    ? meta.get(t.deviceId)    : undefined
          const dm = t.dstDeviceId ? meta.get(t.dstDeviceId) : undefined
          emitSSEEvent('trap', {
            device_id:    t.deviceId,
            source_ip:    t.source_ip   ?? m?.mgmt_ip  ?? null,
            device_name:  t.device_name ?? m?.hostname ?? null,
            // Peer endpoint — present on link traps so the UI breaks only this edge.
            dst_device_id: t.dstDeviceId,
            dst_ip:        dm?.mgmt_ip  ?? null,
            dst_hostname:  t.dst_hostname ?? dm?.hostname ?? null,
            trap_type:   t.trap_type,
            trap_oid:    t.trap_oid ?? '',
            severity:    t.severity,
            description: t.description,
            alarm_state: t.alarm_state ?? null,
            timestamp:   t.timestamp ?? new Date().toISOString(),
          })
        }
      }

      // ── 7. UI change-feed → tell DCS what the operator edited ──────────────
      // Best-effort, after COMMIT: forward the still-pending rule_changes rows
      // accumulated since DCS's cursor as a {mgmt_ip, field, new_value} list DCS
      // applies via SNMP SET / Redfish. The new cursor is the newest updated_at
      // returned (else the cursor we were given, or now() on first contact) so DCS
      // advances monotonically.
      let uiChanges: Array<{ mgmt_ip: string; field: string; new_value: unknown }> = []
      let uiCursor: string = body.ui_changes_since ?? new Date().toISOString()
      if (body.ui_changes_since) {
        try {
          const { rows } = await dbPool.query<{ mgmt_ip: string; field: string; new_value: string; updated_at: string }>(
              `SELECT device_ip AS mgmt_ip, field, new_value, updated_at
               FROM rule_changes
               WHERE status = 'pending'
                 AND updated_at > $1::timestamptz
               AND device_ip <> ''
               ORDER BY updated_at ASC`,
              [body.ui_changes_since]
          )
          for (const r of rows) {
            uiChanges.push({ mgmt_ip: r.mgmt_ip, field: r.field, new_value: r.new_value })
          }
          if (rows.length > 0) uiCursor = rows[rows.length - 1].updated_at
        } catch { /* non-fatal — DCS keeps its cursor and retries next ingest */ }
      }

      res.status(201).json({
        success: true,
        ingested: {
          devices:        (body.devices ?? []).length,
          topology_links: (body.topology_links ?? []).length,
          events:         (body.events ?? []).length,
          energy_metrics: (body.devices ?? []).reduce((n, d) => n + (d.energy_metrics?.length ?? 0), 0),
        },
        ui_changes: uiChanges,
        ui_changes_cursor: uiCursor,
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
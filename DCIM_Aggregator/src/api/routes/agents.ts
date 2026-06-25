import { Router } from 'express'
import { Pool } from 'pg'

const HEARTBEAT_TIMEOUT_SECONDS = 300

// ── Editable device configuration (SNMP SET + Redfish) ──────────────────────────
//
// What the UI can edit on the device page, grouped exactly like the writable
// SNMP/Redfish surface DCS exposes:
//
//   identity  → SNMP SET 1.3.6.1.2.1.1.x  (devices columns)
//   asset     → SNMP SET 1.3.6.1.4.1.99999.4.x  (devices columns)
//   thresholds→ SNMP SET 1.3.6.1.4.1.99999.3.x  (device_thresholds, one row per rule)
//   redfish   → Redfish HTTPS, servers only      (device_state category 'redfish')
//
// Every edit also lands as a pending row in rule_changes ({device_ip, field,
// old_value, new_value}). That table is the durable change-feed DCS pulls (via
// the ingest response) and applies to the live device over SNMP SET / Redfish.

// devices-column ↔ identity/asset field map. `parse` coerces the inbound string
// to the column's type (SMALLINT columns reject '' / non-numbers otherwise).
const IDENTITY_FIELDS: Record<string, { col: string }> = {
  sysContact:  { col: 'sys_contact' },
  sysName:     { col: 'hostname' },
  sysLocation: { col: 'sys_location' },
}

const ASSET_FIELDS: Record<string, { col: string; numeric?: boolean }> = {
  country:         { col: 'country' },
  datacenter_city: { col: 'datacenter_city' },
  datacenter:      { col: 'datacenter' },
  floor:           { col: 'floor' },
  room:            { col: 'room' },
  rack_row:        { col: 'rack_row',     numeric: true },
  rack_num:        { col: 'rack_num',     numeric: true },
  rack_unit:       { col: 'rack_unit',    numeric: true },
  model:           { col: 'model_name' },
  power_draw_w:    { col: 'power_draw_w', numeric: true },
}

// Alert-threshold defaults mirror Datacenter_Network_Simulator/core/trap_rules.py
// so the form is pre-filled with the values DCS already enforces.
const THRESHOLD_DEFAULTS: Record<string, number> = {
  HighCPU:               90,
  HighCPUSustained:      90,
  CPUNormal:             70,
  HighMemory:            85,
  HighTemperature:       60,
  RackFailureMin:         3,
  SensorTempHigh:        32,
  SensorTempCritical:    38,
  SensorHumidityHigh:    70,
  SensorHumidityCritical:80,
  SensorHumidityLow:     30,
  SensorDewpointHigh:    21,
  SensorAirflowHigh:      3.5,
  SensorAirflowLow:       0.3,
}

const REDFISH_DEFAULTS: Record<string, string> = {
  // Redfish ComputerSystem.Reset action — last requested power transition.
  power_action: '',
  // Redfish IndicatorLED / LocationIndicatorActive.
  indicator_led: 'Off',
}

// Server device_types eligible for Redfish (power + identify LED).
const REDFISH_TYPES = new Set(['server'])

// Which threshold rules actually apply to each device_type. Mirrors the
// simulator's APPLICABLE_TRAPS (Datacenter_Network_Simulator/core/trap_definitions.py):
// only device types that run an SNMP agent AND expose the underlying metric get
// the knob. Types absent here — chiller, pump, cooling_tower, valve, rpp, crah,
// cdu, energy_monitor, generator, … — are BACnet-only with no SNMP agent, so
// they get NO threshold surface (an empty object, not the static defaults).
// Keep in sync with APPLICABLE_TRAPS.
const CPU_RULES = ['HighCPU', 'HighCPUSustained', 'CPUNormal']
const SENSOR_RULES = [
  'SensorTempHigh', 'SensorTempCritical',
  'SensorHumidityHigh', 'SensorHumidityCritical', 'SensorHumidityLow',
  'SensorDewpointHigh', 'SensorAirflowHigh', 'SensorAirflowLow',
]
const THRESHOLDS_BY_TYPE: Record<string, string[]> = {
  router:        [...CPU_RULES, 'HighTemperature'],
  switch:        [...CPU_RULES, 'HighTemperature'],
  oob_switch:    [...CPU_RULES, 'HighTemperature'],
  firewall:      [...CPU_RULES, 'HighTemperature'],
  load_balancer: [...CPU_RULES, 'HighMemory', 'HighTemperature'],
  server:        [...CPU_RULES, 'HighMemory', 'HighTemperature'],
  sensor:        [...SENSOR_RULES],
  ups:           ['HighTemperature'],
  // pdu: link/power only — no metric thresholds
}

// Threshold rules editable for a device, gated by type. Falls back to device_role
// 'server' to match the Redfish gate (a server may carry a generic device_type).
// Empty array → device has no threshold surface at all.
function applicableThresholdRules(deviceType: unknown, deviceRole: unknown): string[] {
  const t = String(deviceType ?? '').toLowerCase()
  if (THRESHOLDS_BY_TYPE[t]) return THRESHOLDS_BY_TYPE[t]
  if (String(deviceRole ?? '').toLowerCase() === 'server') return THRESHOLDS_BY_TYPE.server
  return []
}

// Upsert a device_state config category, replacing its JSONB blob wholesale.
async function upsertState(
  client: { query: (q: string, p: any[]) => Promise<unknown> },
  deviceId: string,
  category: string,
  data: Record<string, unknown>
): Promise<void> {
  await client.query(
    `INSERT INTO device_state (device_id, category, data, updated_at)
     VALUES ($1::uuid, $2, $3::jsonb, now())
     ON CONFLICT (device_id, category)
     DO UPDATE SET data = device_state.data || EXCLUDED.data, updated_at = now()`,
    [deviceId, category, JSON.stringify(data)]
  )
}

// Maps devices row to the Agent shape the UI expects
const DEVICE_SELECT = `
  SELECT
    d.id::text                  AS agent_id,
    d.hostname,
    d.mgmt_ip::text             AS ip_address,
    d.group_id                  AS agent_group,
    d.group_id                  AS "group",
    d.network_id                AS server_name,
    d.network_id                AS server_id,
    d.network_id                AS network_id,
    d.datacenter_id             AS datacenter_id,
    d.last_seen_at              AS last_seen,
    d.device_type,
    d.device_role,
    d.vendor,
    d.collector_agent,
    d.is_reachable,
    CASE
      WHEN d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 'online'
      ELSE 'offline'
    END AS status,
    COALESCE(mc.total_metrics, 0)::int AS total_metrics,
    COALESCE(ac.total_alerts,  0)::int AS total_alerts
  FROM devices d
  LEFT JOIN (
    SELECT device_id, COUNT(*)::int AS total_metrics
    FROM metrics
    WHERE ts >= NOW() - INTERVAL '24 hours'
    GROUP BY device_id
  ) mc ON mc.device_id = d.id
  LEFT JOIN (
    SELECT device_id, COUNT(*)::int AS total_alerts
    FROM events
    WHERE COALESCE((event_payload->>'resolved')::boolean, false) = false
    GROUP BY device_id
  ) ac ON ac.device_id = d.id
`

export function createAgentsRouter(dbPool: Pool): Router {
  const router = Router()

  // Create a manual device
  router.post('/', async (req, res) => {
    try {
      const { hostname, ip_address, agent_group, protocol, metadata } = req.body
      if (!hostname) {
        return res.status(400).json({ success: false, error: 'hostname is required' })
      }
      const { rows } = await dbPool.query(
        `INSERT INTO devices
           (org_id, datacenter_id, floor_id, network_id, group_id, hostname, device_type,
            mgmt_ip, collector_agent, snmp_enabled)
         VALUES ('default','default','default','manual',$1,$2,'server',$3,'IDR',false)
         RETURNING *`,
        [agent_group || 'manual', hostname, ip_address || null]
      )
      res.status(201).json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Update device
  router.put('/:agentId', async (req, res) => {
    try {
      const { agentId } = req.params
      const { hostname, ip_address, agent_group, metadata } = req.body
      const sets: string[] = []
      const params: any[] = []
      let i = 1
      if (hostname    !== undefined) { sets.push(`hostname  = $${i++}`); params.push(hostname) }
      if (ip_address  !== undefined) { sets.push(`mgmt_ip   = $${i++}`); params.push(ip_address) }
      if (agent_group !== undefined) { sets.push(`group_id  = $${i++}`); params.push(agent_group) }
      if (sets.length === 0) return res.status(400).json({ success: false, error: 'No fields to update' })
      sets.push('updated_at = NOW()')
      params.push(agentId)
      const { rows } = await dbPool.query(
        `UPDATE devices SET ${sets.join(', ')} WHERE id = $${i}::uuid RETURNING *`,
        params
      )
      if (rows.length === 0) return res.status(404).json({ success: false, error: 'Device not found' })
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Delete device
  router.delete('/:agentId', async (req, res) => {
    try {
      await dbPool.query('DELETE FROM devices WHERE id = $1::uuid', [req.params.agentId])
      res.json({ success: true })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // List all devices
  router.get('/', async (req, res) => {
    try {
      const { server_id, status, agent_group, group, hostname, search } = req.query
      let query = `${DEVICE_SELECT} WHERE 1=1`
      const params: any[] = []
      let i = 1

      if (server_id) {
        params.push(server_id); query += ` AND d.network_id = $${i++}`
      }
      const groupFilter = (agent_group || group) as string | undefined
      if (groupFilter) {
        params.push(groupFilter); query += ` AND d.group_id = $${i++}`
      }
      const searchFilter = (search || hostname) as string | undefined
      if (searchFilter) {
        params.push(`%${searchFilter}%`)
        query += ` AND (d.hostname ILIKE $${i} OR d.mgmt_ip::text ILIKE $${i})`
        i++
      }
      if (status === 'online') {
        query += ` AND d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'`
      } else if (status === 'offline') {
        query += ` AND (d.last_seen_at < NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' OR d.last_seen_at IS NULL)`
      }

      query += ' ORDER BY d.network_id, d.hostname'
      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Summary stats — MUST be before /:agentId
  router.get('/stats/summary', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          COUNT(*)::int                                                                                   AS total_agents,
          COUNT(CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int  AS online_agents,
          COUNT(CASE WHEN last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                      OR last_seen_at IS NULL THEN 1 END)::int                                           AS offline_agents,
          COUNT(DISTINCT network_id)::int                                                                AS total_servers,
          COUNT(DISTINCT group_id)::int                                                                  AS total_groups
        FROM devices
      `)
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Per-network breakdown — MUST be before /by-server/:serverId
  router.get('/stats/by-server', async (req, res) => {
    try {
      const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 5, 1), 50)

      const { rows: totalRows } = await dbPool.query(`
        SELECT
          COUNT(*)::int AS total,
          COUNT(CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int AS online,
          COUNT(CASE WHEN last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                      OR last_seen_at IS NULL THEN 1 END)::int AS offline,
          COUNT(DISTINCT network_id)::int AS servers
        FROM devices
      `)

      const { rows: serverRows } = await dbPool.query(`
        SELECT
          network_id          AS server_id,
          network_id          AS server_name,
          NULL::text          AS color,
          COUNT(*)::int       AS total,
          COUNT(CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int AS online,
          COUNT(CASE WHEN last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                      OR last_seen_at IS NULL THEN 1 END)::int AS offline
        FROM devices
        GROUP BY network_id
        ORDER BY offline DESC, total DESC
        LIMIT $1
      `, [limit])

      res.json({ success: true, data: { totals: totalRows[0], servers: serverRows } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Recently seen devices
  router.get('/recent', async (req, res) => {
    try {
      const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 6, 1), 50)
      const { rows } = await dbPool.query(`
        SELECT
          id::text  AS agent_id,
          hostname,
          mgmt_ip::text AS ip_address,
          CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 'online' ELSE 'offline' END AS status,
          last_seen_at AS last_seen,
          group_id     AS "group",
          network_id   AS server_name
        FROM devices
        ORDER BY last_seen_at DESC NULLS LAST
        LIMIT $1
      `, [limit])
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Devices by network — MUST be before /:agentId
  router.get('/by-server/:serverId', async (req, res) => {
    try {
      const { rows } = await dbPool.query(
        `${DEVICE_SELECT} WHERE d.network_id = $1 ORDER BY d.hostname`,
        [req.params.serverId]
      )
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Latest metrics for a device
  router.get('/:agentId/metrics/latest', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT DISTINCT ON (metric_name)
          device_id::text AS agent_id,
          metric_name     AS metric_type,
          value,
          ts              AS timestamp,
          tag,
          collector_protocol AS protocol
        FROM metrics
        WHERE device_id = $1::uuid
        ORDER BY metric_name, ts DESC
      `, [req.params.agentId])
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Device configuration (editable SNMP SET + Redfish surface) ───────────────
  // MUST be before /:agentId so the literal "config" segment wins the match.
  router.get('/:agentId/config', async (req, res) => {
    try {
      const { agentId } = req.params
      const { rows } = await dbPool.query(
        `SELECT id, device_type, device_role, mgmt_ip::text AS mgmt_ip,
                sys_contact, hostname, sys_location,
                country, datacenter_city, datacenter, floor, room,
                rack_row, rack_num, rack_unit, model_name, power_draw_w
         FROM devices WHERE id = $1::uuid`,
        [agentId]
      )
      if (rows.length === 0) return res.status(404).json({ success: false, error: 'Device not found' })
      const d = rows[0]

      // Current thresholds live in device_thresholds (one row per rule). EDR keeps
      // them in sync with the device; merge the saved rows onto the defaults so the
      // form is always fully populated even before a device has any overrides.
      const { rows: thrRows } = await dbPool.query(
        `SELECT rule, value FROM device_thresholds WHERE device_id = $1::uuid`,
        [agentId]
      )
      const savedThresholds: Record<string, number> = {}
      for (const r of thrRows) savedThresholds[r.rule] = Number(r.value)

      // Only surface the threshold rules that actually apply to this device_type.
      // A chiller/pump/etc. has no SNMP metrics → no knobs (empty object), instead
      // of fabricating the full static default set for it.
      const applicableRules = applicableThresholdRules(d.device_type, d.device_role)
      const thresholds: Record<string, number> = {}
      for (const k of applicableRules) {
        thresholds[k] = savedThresholds[k] ?? THRESHOLD_DEFAULTS[k]
      }

      // Redfish overrides still live in device_state.
      const { rows: redfishRows } = await dbPool.query(
        `SELECT data FROM device_state WHERE device_id = $1::uuid AND category = 'redfish'`,
        [agentId]
      )
      const redfishState = redfishRows[0]?.data ?? {}

      // "Last edit queued" hint — newest still-pending rule_changes row for this device.
      let pendingSince: string | null = null
      if (d.mgmt_ip) {
        const { rows: pendRows } = await dbPool.query(
          `SELECT max(updated_at) AS pending_since
           FROM rule_changes WHERE device_ip = $1 AND status = 'pending'`,
          [d.mgmt_ip]
        )
        pendingSince = pendRows[0]?.pending_since ?? null
      }

      const redfishCapable =
        REDFISH_TYPES.has(String(d.device_type)) || String(d.device_role) === 'server'

      res.json({
        success: true,
        data: {
          agent_id:      d.id,
          device_type:   d.device_type,
          redfish_capable: redfishCapable,
          identity: {
            sysContact:  d.sys_contact ?? '',
            sysName:     d.hostname ?? '',
            sysLocation: d.sys_location ?? '',
          },
          asset: {
            country:         d.country ?? '',
            datacenter_city: d.datacenter_city ?? '',
            datacenter:      d.datacenter ?? '',
            floor:           d.floor ?? '',
            room:            d.room ?? '',
            rack_row:        d.rack_row ?? null,
            rack_num:        d.rack_num ?? null,
            rack_unit:       d.rack_unit ?? null,
            model:           d.model_name ?? '',
            power_draw_w:    d.power_draw_w ?? null,
          },
          thresholds,
          redfish:    redfishCapable ? { ...REDFISH_DEFAULTS, ...redfishState } : null,
          // When DCS last had a change queued (drives the "pending push" hint).
          pending_since: pendingSince,
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Persist an edit. Identity/asset write through to devices columns; thresholds
  // upsert into device_thresholds; redfish goes to device_state. Every field that
  // actually changes also lands as a pending rule_changes row — the durable feed
  // DCS pulls (via the ingest response) and applies over SNMP SET / Redfish.
  router.put('/:agentId/config', async (req, res) => {
    const { agentId } = req.params
    const { identity = {}, asset = {}, thresholds, redfish } = req.body ?? {}

    const client = await dbPool.connect()
    try {
      await client.query('BEGIN')

      // Load the current row (FOR UPDATE). It gives us the OLD values recorded in
      // rule_changes plus the mgmt_ip/hostname/tenant the DCS feed keys on.
      const { rows: devRows } = await client.query(
        `SELECT device_type, device_role, org_id, network_id,
                mgmt_ip::text AS mgmt_ip, hostname,
                sys_contact, sys_location,
                country, datacenter_city, datacenter, floor, room,
                rack_row, rack_num, rack_unit, model_name, power_draw_w
         FROM devices WHERE id = $1::uuid FOR UPDATE`,
        [agentId]
      )
      if (devRows.length === 0) {
        await client.query('ROLLBACK')
        return res.status(404).json({ success: false, error: 'Device not found' })
      }
      const dev = devRows[0]

      // Every {field, old, new} this edit actually changes → one rule_changes row.
      const asStr = (v: unknown) => (v === null || v === undefined ? '' : String(v))
      const changes: Array<{ field: string; old: unknown; new: unknown }> = []
      const record = (field: string, oldVal: unknown, newVal: unknown) => {
        if (asStr(oldVal) !== asStr(newVal)) changes.push({ field, old: oldVal, new: newVal })
      }

      // ── identity + asset → devices columns ───────────────────────────────────
      const sets: string[] = []
      const params: any[] = []
      let i = 1
      const applyGroup = (
        group: Record<string, any>,
        defs: Record<string, { col: string; numeric?: boolean }>
      ) => {
        for (const [field, def] of Object.entries(defs)) {
          if (!(field in group)) continue
          let v = group[field]
          if (def.numeric) {
            v = v === '' || v === null || v === undefined ? null : Number(v)
            if (v !== null && Number.isNaN(v)) continue
          } else if (v === undefined) {
            continue
          }
          sets.push(`${def.col} = $${i++}`)
          params.push(v)
          record(field, dev[def.col], v)
        }
      }
      applyGroup(identity, IDENTITY_FIELDS)
      applyGroup(asset, ASSET_FIELDS)
      if (sets.length > 0) {
        sets.push('updated_at = now()')
        params.push(agentId)
        await client.query(
          `UPDATE devices SET ${sets.join(', ')} WHERE id = $${i}::uuid`,
          params
        )
      }

      // ── thresholds → device_thresholds (one row per rule) ─────────────────────
      if (thresholds && typeof thresholds === 'object') {
        const { rows: curThr } = await client.query(
          'SELECT rule, value FROM device_thresholds WHERE device_id = $1::uuid',
          [agentId]
        )
        const curMap = new Map<string, number>(curThr.map((r: any) => [r.rule, Number(r.value)]))
        // Only persist rules that apply to this device_type — never queue a dead
        // command for a metric the device doesn't have (e.g. HighCPU on a chiller).
        const applicableRules = applicableThresholdRules(dev.device_type, dev.device_role)
        for (const k of applicableRules) {
          if (!(k in thresholds) || thresholds[k] === '' || thresholds[k] === null) continue
          const n = Number(thresholds[k])
          if (Number.isNaN(n)) continue
          // Old value: the device's current override, else the enforced default.
          const old = curMap.has(k) ? curMap.get(k) : THRESHOLD_DEFAULTS[k]
          await client.query(
            `INSERT INTO device_thresholds (device_id, rule, value, updated_at)
             VALUES ($1::uuid, $2, $3, now())
             ON CONFLICT (device_id, rule)
             DO UPDATE SET value = EXCLUDED.value, updated_at = now()`,
            [agentId, k, n]
          )
          record(k, old, n)
        }
      }

      // ── redfish → device_state('redfish'), servers only ──────────────────────
      if (redfish && typeof redfish === 'object') {
        const redfishCapable =
          REDFISH_TYPES.has(String(dev.device_type)) || String(dev.device_role) === 'server'
        if (!redfishCapable) {
          await client.query('ROLLBACK')
          return res.status(400).json({ success: false, error: 'Redfish actions are servers-only' })
        }
        const { rows: curR } = await client.query(
          `SELECT data FROM device_state WHERE device_id = $1::uuid AND category = 'redfish'`,
          [agentId]
        )
        const curRedfish: Record<string, unknown> = curR[0]?.data ?? {}
        const clean: Record<string, string> = {}
        for (const k of Object.keys(REDFISH_DEFAULTS)) {
          if (k in redfish && redfish[k] !== undefined && redfish[k] !== null) {
            clean[k] = String(redfish[k])
            const old = k in curRedfish ? curRedfish[k] : REDFISH_DEFAULTS[k]
            record(k, old, clean[k])
          }
        }
        await upsertState(client, agentId, 'redfish', clean)
      }

      // ── change-feed → rule_changes (DCS pulls the pending rows) ───────────────
      // Keyed on the device mgmt IP (the SNMP SET / Redfish target). The partial
      // unique index collapses a repeat edit of the same still-pending field into
      // the existing row, so dragging a slider doesn't queue 50 changes. A device
      // with no mgmt_ip can't be applied to, so we only persist its column edits.
      if (changes.length > 0 && dev.mgmt_ip) {
        for (const c of changes) {
          await client.query(
            `INSERT INTO rule_changes
               (org_id, network_id, device_ip, hostname, field, old_value, new_value, status, updated_at)
             VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', now())
             ON CONFLICT (device_ip, field) WHERE status = 'pending'
             DO UPDATE SET new_value = EXCLUDED.new_value, updated_at = now(), attempts = 0`,
            [dev.org_id ?? '', dev.network_id ?? '', dev.mgmt_ip, dev.hostname ?? '',
             c.field, asStr(c.old), asStr(c.new)]
          )
        }
      }

      await client.query('COMMIT')
      res.json({ success: true, data: { agent_id: agentId, changed: changes.map(c => c.field) } })
    } catch (error: any) {
      await client.query('ROLLBACK')
      res.status(500).json({ success: false, error: error.message })
    } finally {
      client.release()
    }
  })

  // Single device by id
  router.get('/:agentId', async (req, res) => {
    try {
      const { rows } = await dbPool.query(
        `${DEVICE_SELECT} WHERE d.id = $1::uuid`,
        [req.params.agentId]
      )
      if (rows.length === 0) return res.status(404).json({ success: false, error: 'Device not found' })
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}
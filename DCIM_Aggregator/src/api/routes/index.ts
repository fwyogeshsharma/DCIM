import { Express } from 'express'
import { Pool } from 'pg'
import { RedisClientType } from 'redis'
import { CacheService } from '../../services/CacheService'
import { createServersRouter } from './servers'
import { createAgentsRouter } from './agents'
import { createMetricsRouter } from './metrics'
import { createEnergyRouter } from './energy'
import { createAlertsRouter } from './alerts'
import { createTicketsRouter } from './tickets'
import { createIngestRouter } from './ingest'
import { createInventoryRouter } from './inventory'
import { createAuthRouter } from './auth'
import { addSSEClient, removeSSEClient } from '../../events/sseEmitter'

const HEARTBEAT_TIMEOUT_SECONDS = 300

export function setupRoutes(app: Express, dbPool: Pool, redisClient: RedisClientType) {
  const cacheService = new CacheService(redisClient)

  // SSE event stream
  app.get('/api/v1/events', (req, res) => {
    const clientId = addSSEClient(res)
    req.on('close', () => removeSSEClient(clientId))
  })

  app.use('/api/v1/servers',  createServersRouter(dbPool, cacheService))
  app.use('/api/v1/agents',   createAgentsRouter(dbPool))
  app.use('/api/v1/metrics',  createMetricsRouter(dbPool, cacheService))
  app.use('/api/v1/energy',   createEnergyRouter(dbPool, cacheService))
  app.use('/api/v1/alerts',   createAlertsRouter(dbPool))
  app.use('/api/v1/tickets',  createTicketsRouter(dbPool))
  app.use('/api/v1/ingest',     createIngestRouter(dbPool))
  app.use('/api/v1/inventory',  createInventoryRouter(dbPool))
  app.use('/api/v1/auth',       createAuthRouter(dbPool))

  // ── Dashboard stats ────────────────────────────────────────────────────────
  app.get('/api/v1/dashboard/stats', async (req, res) => {
    try {
      const [devicesResult, alertsResult] = await Promise.all([
        dbPool.query(`
          SELECT
            COUNT(*)::int AS total,
            COUNT(CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int  AS online,
            COUNT(CASE WHEN last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                        OR last_seen_at IS NULL THEN 1 END)::int AS offline,
            COUNT(DISTINCT network_id)::int AS networks
          FROM devices
        `),
        dbPool.query(`
          SELECT COUNT(*)::int AS count
          FROM events
          WHERE COALESCE((event_payload->>'resolved')::boolean, false) = false
        `),
      ])

      res.json({
        success: true,
        data: {
          servers: devicesResult.rows[0].networks,
          agents: {
            total:   devicesResult.rows[0].total,
            online:  devicesResult.rows[0].online,
            offline: devicesResult.rows[0].offline,
          },
          activeAlerts: alertsResult.rows[0].count,
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Full dashboard ─────────────────────────────────────────────────────────
  app.get('/api/v1/dashboard', async (req, res) => {
    try {
      const [devicesResult, alertsResult, recentMetricsResult, recentAlertsResult] = await Promise.all([
        dbPool.query(`
          SELECT
            COUNT(*)::int AS total_agents,
            COUNT(CASE WHEN last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int AS online_agents,
            COUNT(CASE WHEN last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                        OR last_seen_at IS NULL THEN 1 END)::int AS offline_agents
          FROM devices
        `),
        dbPool.query(`
          SELECT
            COUNT(*)::int AS total_alerts,
            COUNT(CASE WHEN LOWER(severity) = 'critical'
                        AND COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END)::int AS critical_alerts
          FROM events
          WHERE ts >= NOW() - INTERVAL '24 hours'
        `),
        dbPool.query(`
          SELECT
            device_id::text AS agent_id,
            metric_name     AS metric_type,
            value,
            ts              AS timestamp,
            tag
          FROM metrics
          ORDER BY ts DESC
          LIMIT 50
        `),
        dbPool.query(`
          SELECT
            e.id::text                                          AS id,
            e.device_id::text                                   AS agent_id,
            e.ts                                                AS timestamp,
            e.event_name                                        AS metric_type,
            COALESCE(e.event_payload->>'message', e.event_name) AS message,
            UPPER(e.severity)                                   AS severity,
            COALESCE((e.event_payload->>'resolved')::boolean, false) AS resolved,
            d.hostname                                          AS server_name
          FROM events e
          LEFT JOIN devices d ON d.id = e.device_id
          WHERE COALESCE((e.event_payload->>'resolved')::boolean, false) = false
          ORDER BY e.ts DESC
          LIMIT 20
        `),
      ])

      res.json({
        success: true,
        data: {
          total_agents:    devicesResult.rows[0].total_agents,
          online_agents:   devicesResult.rows[0].online_agents,
          total_alerts:    alertsResult.rows[0].total_alerts,
          critical_alerts: alertsResult.rows[0].critical_alerts,
          recent_metrics:  recentMetricsResult.rows,
          recent_alerts:   recentAlertsResult.rows,
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── SNMP metrics ───────────────────────────────────────────────────────────
  app.get('/api/v1/snmp/metrics', async (req, res) => {
    try {
      const { agent_id, device_name, limit = 1000 } = req.query
      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))
      let query = `
        SELECT
          m.device_id::text  AS agent_id,
          m.ts               AS timestamp,
          m.metric_name,
          m.tag,
          m.value,
          d.hostname         AS device_name,
          d.mgmt_ip::text    AS device_ip
        FROM metrics m
        LEFT JOIN devices d ON d.id = m.device_id
        WHERE m.collector_protocol = 'SNMP'
          AND m.ts >= NOW() - INTERVAL '24 hours'
      `
      const params: any[] = []
      let i = 1
      if (agent_id)    { params.push(agent_id);    query += ` AND m.device_id = $${i++}::uuid` }
      if (device_name) { params.push(device_name); query += ` AND d.hostname = $${i++}` }
      params.push(safeLimit)
      query += ` ORDER BY m.ts DESC LIMIT $${i++}`
      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── SNMP devices ───────────────────────────────────────────────────────────
  app.get('/api/v1/snmp/devices', async (req, res) => {
    try {
      const { agent_id, server_id } = req.query
      let query = `
        SELECT DISTINCT ON (d.mgmt_ip)
          d.id::text       AS id,
          d.hostname       AS device_name,
          d.mgmt_ip::text  AS device_ip,
          d.id::text       AS agent_id,
          d.network_id     AS server_id,
          d.network_id     AS server_name,
          d.network_id     AS network_id,
          d.datacenter_id  AS datacenter_id,
          d.device_type,
          d.device_role,
          d.last_seen_at   AS last_seen
        FROM devices d
        WHERE d.snmp_enabled = true
      `
      const params: any[] = []
      let i = 1
      if (agent_id) { params.push(agent_id); query += ` AND d.id::text = $${i++}` }
      if (server_id) { params.push(server_id); query += ` AND d.network_id = $${i++}` }
      query += ` ORDER BY d.mgmt_ip, d.last_seen_at DESC NULLS LAST`
      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── SNMP traps ─────────────────────────────────────────────────────────────
  app.get('/api/v1/traps', async (req, res) => {
    try {
      const { source_ip, device_name, severity, resolved, limit = 200 } = req.query
      // Surface every operational event the topology cares about, not just SNMP
      // traps: device-change notifications and alarms too. trap_oid is optional
      // (null for non-SNMP kinds) — the UI keys off event_name/severity, not OID.
      const conditions: string[] = [`e.kind IN ('trap', 'device_change', 'alarm')`]
      const params: any[] = []
      let i = 1

      if (source_ip)  { params.push(source_ip);                  conditions.push(`COALESCE(e.source_ip, d.mgmt_ip) = $${i++}::inet`) }
      if (device_name){ params.push(`%${device_name}%`);         conditions.push(`COALESCE(e.source_hostname, d.hostname) ILIKE $${i++}`) }
      if (severity)   { params.push(String(severity).toLowerCase()); conditions.push(`LOWER(e.severity) = $${i++}`) }
      if (resolved !== undefined) {
        params.push(resolved === 'true'); conditions.push(`COALESCE((e.event_payload->>'resolved')::boolean, false) = $${i++}`)
      }

      params.push(Math.min(parseInt(String(limit)) || 200, 2000))
      // Fall back to the device's hostname/mgmt_ip when the trap row didn't
      // carry them, so the topology can always match the trap to a node by
      // either key. trap_type/description are the field names the UI reads.
      const { rows } = await dbPool.query(`
        SELECT
          e.id::text                                            AS id,
          e.device_id::text                                     AS device_id,
          e.ts                                                  AS timestamp,
          e.event_name                                          AS trap_type,
          e.event_name,
          e.severity,
          e.trap_oid,
          COALESCE(e.source_ip::text, d.mgmt_ip::text)          AS source_ip,
          COALESCE(e.source_hostname, d.hostname)               AS device_name,
          -- Peer endpoint (link traps) so the UI can break only the affected edge.
          e.dst_device_id::text                                 AS dst_device_id,
          COALESCE(e.dst_hostname, dd.hostname)                 AS dst_hostname,
          dd.mgmt_ip::text                                      AS dst_ip,
          e.link_id::text                                       AS link_id,
          COALESCE(e.event_payload->>'message', e.event_name)   AS description,
          e.event_payload,
          COALESCE((e.event_payload->>'resolved')::boolean, false) AS resolved
        FROM events e
        LEFT JOIN devices d  ON d.id  = e.device_id
        LEFT JOIN devices dd ON dd.id = e.dst_device_id
        WHERE ${conditions.join(' AND ')}
        ORDER BY e.ts DESC LIMIT $${i}
      `, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Trap timeline ──────────────────────────────────────────────────────────
  app.get('/api/v1/traps/timeline', async (req, res) => {
    try {
      const { source_ip, hours = 24 } = req.query
      const safeHours = Math.min(parseInt(String(hours)) || 24, 720)
      const conditions: string[] = [
        `kind = 'trap'`,
        `ts >= NOW() - INTERVAL '${safeHours} hours'`,
      ]
      const params: any[] = []
      let i = 1
      if (source_ip) { params.push(source_ip); conditions.push(`source_ip = $${i++}::inet`) }

      const { rows } = await dbPool.query(`
        SELECT
          date_trunc('hour', ts)                                                            AS bucket,
          COUNT(*)::int                                                                     AS total,
          COUNT(CASE WHEN LOWER(severity) = 'critical'                       THEN 1 END)::int AS critical,
          COUNT(CASE WHEN LOWER(severity) IN ('major','warning')             THEN 1 END)::int AS warning,
          COUNT(CASE WHEN LOWER(severity) IN ('minor','info','informational') THEN 1 END)::int AS info
        FROM events
        WHERE ${conditions.join(' AND ')}
        GROUP BY bucket
        ORDER BY bucket ASC
      `, params)
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Networks summary ───────────────────────────────────────────────────────
  app.get('/api/v1/networks/summary', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          d.network_id,
          COUNT(*)::int                                                                                              AS total_devices,
          COUNT(CASE WHEN d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 1 END)::int AS online,
          COUNT(CASE WHEN d.last_seen_at <  NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
                      OR d.last_seen_at IS NULL THEN 1 END)::int                                                    AS offline,
          COUNT(DISTINCT d.device_type)::int                                                                        AS device_types,
          MAX(d.last_seen_at)                                                                                       AS last_seen,
          -- Datacenter location for this network (representative value; a network
          -- normally maps to a single datacenter, so MAX picks its name/city/country).
          MAX(d.datacenter)                                                                                        AS datacenter,
          MAX(d.datacenter_city)                                                                                   AS datacenter_city,
          MAX(d.country)                                                                                           AS country,
          COALESCE(SUM(ac.active_alerts), 0)::int                                                                   AS active_alerts,
          COALESCE(SUM(ac.critical_alerts), 0)::int                                                                 AS critical_alerts
        FROM devices d
        LEFT JOIN (
          SELECT
            e.device_id,
            COUNT(CASE WHEN COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END)::int          AS active_alerts,
            COUNT(CASE WHEN LOWER(e.severity) = 'critical'
                        AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END)::int          AS critical_alerts
          FROM events e
          GROUP BY e.device_id
        ) ac ON ac.device_id = d.id
        GROUP BY d.network_id
        ORDER BY d.network_id
      `)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Topology tree ──────────────────────────────────────────────────────────
  // Returns { nodes, links } where:
  //   nodes — every device with parent + depth derived from the link `relation`
  //           (uplink/downlink build the tree; peer links are not hierarchical)
  //   links — every topology_links row so the UI can draw ALL physical edges
  //           (uplinks, downlinks, AND peer/spine-mesh links)
  app.get('/api/v1/topology/tree', async (req, res) => {
    try {
      const { network_id } = req.query
      const params: any[] = []
      const netFilter = network_id ? `AND d.network_id = $1` : ''
      if (network_id) params.push(network_id)

      const [nodesResult, linksResult] = await Promise.all([
        // ── Nodes: relation-based BFS depth ──────────────────────────────────
        // The hierarchy is built purely from the topology_links `relation` flag
        // (see migration 002), NOT from device_type guesses:
        //   uplink   → dst_device is the PARENT of src_device
        //   downlink → src_device is the PARENT of dst_device
        //   peer     → ignored (no hierarchy edge → device falls out as a root)
        dbPool.query(`
          WITH RECURSIVE
          -- Devices in scope (optionally filtered to one network).
          scope AS (
            SELECT id, network_id
            FROM devices d
            WHERE 1=1 ${netFilter}
          ),
          -- One parent per child, derived from the directed relation edge.
          -- Only edges where BOTH endpoints are in scope are considered, so a
          -- per-network query never pulls in cross-network parents.
          parent_of AS (
            SELECT DISTINCT ON (child_id) child_id, parent_id
            FROM (
              SELECT tl.src_device_id AS child_id, tl.dst_device_id AS parent_id
              FROM topology_links tl
              JOIN scope c ON c.id = tl.src_device_id
              JOIN scope p ON p.id = tl.dst_device_id
              WHERE tl.relation = 'uplink'
              UNION ALL
              SELECT tl.dst_device_id AS child_id, tl.src_device_id AS parent_id
              FROM topology_links tl
              JOIN scope c ON c.id = tl.dst_device_id
              JOIN scope p ON p.id = tl.src_device_id
              WHERE tl.relation = 'downlink'
            ) edges
            ORDER BY child_id, parent_id
          ),
          -- BFS from roots (devices that are nobody's child via uplink/downlink).
          tree AS (
            SELECT id AS device_id, NULL::uuid AS parent_id, 0 AS depth
            FROM scope
            WHERE NOT EXISTS (SELECT 1 FROM parent_of po WHERE po.child_id = scope.id)
            UNION ALL
            SELECT po.child_id, po.parent_id, t.depth + 1
            FROM parent_of po
            JOIN tree t ON po.parent_id = t.device_id
            WHERE t.depth < 100          -- cycle guard against dirty relation data
          )
          SELECT
            t.device_id::text,
            d.hostname,
            d.device_type,
            d.device_role,
            d.role_confidence,
            d.mgmt_ip::text                                                                        AS mgmt_ip,
            d.network_id,
            d.group_id,
            d.datacenter_id,
            t.parent_id::text                                                                      AS parent_device_id,
            p.hostname                                                                             AS parent_hostname,
            t.depth,
            (t.parent_id IS NULL)                                                                  AS is_root,
            CASE
              WHEN d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
              THEN 'online' ELSE 'offline'
            END                                                                                    AS status,
            COALESCE(ac.active_alerts,   0)::int                                                   AS active_alerts,
            COALESCE(ac.critical_alerts, 0)::int                                                   AS critical_alerts
          FROM tree t
          JOIN   devices d  ON d.id = t.device_id
          LEFT JOIN devices p  ON p.id = t.parent_id
          LEFT JOIN (
            SELECT device_id,
              COUNT(CASE WHEN COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END)::int         AS active_alerts,
              COUNT(CASE WHEN LOWER(severity) = 'critical'
                          AND COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END)::int         AS critical_alerts
            FROM events GROUP BY device_id
          ) ac ON ac.device_id = t.device_id
          ORDER BY t.depth, d.network_id, d.hostname
        `, params),

        // ── Links: every physical edge, both directions ───────────────────────
        // We return ALL topology_links (including peer/spine-mesh) so the UI can
        // draw every cable, not just the hierarchical parent→child edges.
        dbPool.query(`
          SELECT
            tl.id::text,
            tl.src_device_id::text,
            tl.dst_device_id::text,
            tl.is_active,
            tl.relation,
            tl.link_speed_mbps,
            tl.link_type,
            d1.hostname    AS src_hostname,
            d1.network_id  AS src_network_id,
            d2.hostname    AS dst_hostname,
            d2.network_id  AS dst_network_id,
            CASE WHEN d1.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 'online' ELSE 'offline' END AS src_status,
            CASE WHEN d2.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds' THEN 'online' ELSE 'offline' END AS dst_status
          FROM topology_links tl
          JOIN devices d1 ON d1.id = tl.src_device_id
          JOIN devices d2 ON d2.id = tl.dst_device_id
          ${network_id ? `WHERE d1.network_id = $1 AND d2.network_id = $1` : ''}
          ORDER BY d1.hostname, d2.hostname
        `, params),
      ])

      res.json({
        success: true,
        data: {
          nodes: nodesResult.rows,
          links: linksResult.rows,
        },
        count: nodesResult.rows.length,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Topology nodes ─────────────────────────────────────────────────────────
  app.get('/api/v1/topology/nodes', async (req, res) => {
    try {
      const { server_id } = req.query
      let query = `
        SELECT
          id::text           AS id,
          hostname           AS device_name,
          mgmt_ip::text      AS device_ip,
          last_seen_at       AS last_seen,
          network_id         AS server_id,
          network_id         AS server_name,
          device_type,
          vendor,
          snmp_enabled,
          CASE
            WHEN is_reachable AND last_seen_at >= NOW() - INTERVAL '1800 seconds' THEN 'online'
            ELSE 'offline'
          END AS status
        FROM device_inventory
        WHERE 1=1
      `
      const params: any[] = []
      if (server_id) { params.push(server_id); query += ` AND network_id = $1` }
      query += ` ORDER BY device_type, hostname`
      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Topology links ─────────────────────────────────────────────────────────
  app.get('/api/v1/topology/links', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          id,
          layer,
          protocol,
          is_active,
          link_speed_mbps,
          link_type,
          updated_at       AS last_seen,
          src_mgmt_ip::text AS source_ip,
          src_hostname      AS source_name,
          dst_mgmt_ip::text AS target_ip,
          dst_hostname      AS target_name,
          src_port_name     AS source_port,
          dst_port_name     AS target_port,
          src_device_type   AS src_type,
          dst_device_type   AS dst_type
        FROM topology_view
        ORDER BY src_hostname, dst_hostname
      `)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })
}
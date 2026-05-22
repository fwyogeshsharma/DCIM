import { Router } from 'express'
import { Pool } from 'pg'

const HEARTBEAT_TIMEOUT_SECONDS = 300

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
    d.last_seen_at              AS last_seen,
    d.device_type,
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
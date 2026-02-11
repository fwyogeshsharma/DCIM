import { Router } from 'express'
import { Pool } from 'pg'

// Common SELECT with computed total_metrics, total_alerts, and group alias
const AGENT_SELECT = `
  SELECT a.*,
    a.agent_group AS "group",
    s.name AS server_name,
    s.url AS server_url,
    COALESCE(mc.total_metrics, 0)::int AS total_metrics,
    COALESCE(ac.total_alerts, 0)::int AS total_alerts
  FROM agents a
  JOIN servers s ON a.server_id = s.id
  LEFT JOIN (
    SELECT agent_id, server_id, COUNT(*)::int AS total_metrics
    FROM metrics
    GROUP BY agent_id, server_id
  ) mc ON mc.agent_id = a.agent_id AND mc.server_id = a.server_id
  LEFT JOIN (
    SELECT agent_id, server_id, COUNT(*)::int AS total_alerts
    FROM alerts
    WHERE resolved = false
    GROUP BY agent_id, server_id
  ) ac ON ac.agent_id = a.agent_id AND ac.server_id = a.server_id
`

export function createAgentsRouter(dbPool: Pool): Router {
  const router = Router()

  // Get all agents (aggregated from all servers)
  router.get('/', async (req, res) => {
    try {
      const { server_id, status, agent_group, group, hostname, search } = req.query

      let query = `${AGENT_SELECT} WHERE 1=1`
      const params: any[] = []
      let paramIndex = 1

      if (server_id) {
        params.push(server_id)
        query += ` AND a.server_id = $${paramIndex++}`
      }

      if (status) {
        params.push(status)
        query += ` AND a.status = $${paramIndex++}`
      }

      // Support both "agent_group" and "group" filter params
      const groupFilter = agent_group || group
      if (groupFilter) {
        params.push(groupFilter)
        query += ` AND a.agent_group = $${paramIndex++}`
      }

      // Support both "hostname" and "search" filter params
      const searchFilter = search || hostname
      if (searchFilter) {
        params.push(`%${searchFilter}%`)
        query += ` AND (a.hostname ILIKE $${paramIndex} OR a.agent_id ILIKE $${paramIndex} OR a.ip_address ILIKE $${paramIndex})`
        paramIndex++
      }

      query += ' ORDER BY s.name, a.agent_id'

      const { rows } = await dbPool.query(query, params)

      res.json({
        success: true,
        data: rows,
        count: rows.length,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get agent stats — MUST be before /:agentId
  router.get('/stats/summary', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          COUNT(*) as total_agents,
          COUNT(CASE WHEN status = 'online' THEN 1 END) as online_agents,
          COUNT(CASE WHEN status = 'offline' THEN 1 END) as offline_agents,
          COUNT(DISTINCT server_id) as total_servers,
          COUNT(DISTINCT agent_group) as total_groups
        FROM agents
      `)

      res.json({
        success: true,
        data: rows[0],
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get agents by server — MUST be before /:agentId
  router.get('/by-server/:serverId', async (req, res) => {
    try {
      const { serverId } = req.params

      const { rows } = await dbPool.query(
        `${AGENT_SELECT} WHERE a.server_id = $1 ORDER BY a.agent_id`,
        [serverId]
      )

      res.json({
        success: true,
        data: rows,
        count: rows.length,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get single agent by agent_id (searches across all servers)
  router.get('/:agentId', async (req, res) => {
    try {
      const { agentId } = req.params

      const { rows } = await dbPool.query(
        `${AGENT_SELECT} WHERE a.agent_id = $1`,
        [agentId]
      )

      if (rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Agent not found' })
      }

      res.json({
        success: true,
        data: rows[0],
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get latest metrics for an agent
  router.get('/:agentId/metrics/latest', async (req, res) => {
    try {
      const { agentId } = req.params

      const { rows } = await dbPool.query(
        `
        SELECT DISTINCT ON (metric_type)
          m.*,
          s.name as server_name
        FROM metrics m
        JOIN servers s ON m.server_id = s.id
        WHERE m.agent_id = $1
        ORDER BY metric_type, timestamp DESC
      `,
        [agentId]
      )

      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get agent by server ID and agent ID
  router.get('/:serverId/:agentId', async (req, res) => {
    try {
      const { serverId, agentId } = req.params

      const { rows } = await dbPool.query(
        `${AGENT_SELECT} WHERE a.server_id = $1 AND a.agent_id = $2`,
        [serverId, agentId]
      )

      if (rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Agent not found' })
      }

      res.json({
        success: true,
        data: rows[0],
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

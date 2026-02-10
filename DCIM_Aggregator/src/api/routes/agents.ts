import { Router } from 'express'
import { Pool } from 'pg'

export function createAgentsRouter(dbPool: Pool): Router {
  const router = Router()

  // Get all agents (aggregated from all servers)
  router.get('/', async (req, res) => {
    try {
      const { server_id, status, agent_group, hostname } = req.query

      let query = `
        SELECT a.*, s.name as server_name, s.url as server_url
        FROM agents a
        JOIN servers s ON a.server_id = s.id
        WHERE 1=1
      `
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

      if (agent_group) {
        params.push(agent_group)
        query += ` AND a.agent_group = $${paramIndex++}`
      }

      if (hostname) {
        params.push(`%${hostname}%`)
        query += ` AND a.hostname ILIKE $${paramIndex++}`
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

  // Get agent by ID
  router.get('/:serverId/:agentId', async (req, res) => {
    try {
      const { serverId, agentId } = req.params

      const { rows } = await dbPool.query(
        `
        SELECT a.*, s.name as server_name, s.url as server_url
        FROM agents a
        JOIN servers s ON a.server_id = s.id
        WHERE a.server_id = $1 AND a.agent_id = $2
      `,
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

  // Get agent stats
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

  // Get agents by server
  router.get('/by-server/:serverId', async (req, res) => {
    try {
      const { serverId } = req.params

      const { rows } = await dbPool.query(
        `
        SELECT a.*, s.name as server_name
        FROM agents a
        JOIN servers s ON a.server_id = s.id
        WHERE a.server_id = $1
        ORDER BY a.agent_id
      `,
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

  return router
}

import { Router } from 'express'
import { Pool } from 'pg'

export function createAlertsRouter(dbPool: Pool): Router {
  const router = Router()

  // Get all alerts
  router.get('/', async (req, res) => {
    try {
      const { server_id, agent_id, severity, resolved, limit = 1000 } = req.query

      let query = `
        SELECT a.*, s.name as server_name
        FROM alerts a
        JOIN servers s ON a.server_id = s.id
        WHERE 1=1
      `
      const params: any[] = []
      let paramIndex = 1

      if (server_id) {
        params.push(server_id)
        query += ` AND a.server_id = $${paramIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        query += ` AND a.agent_id = $${paramIndex++}`
      }

      if (severity) {
        params.push(severity)
        query += ` AND a.severity = $${paramIndex++}`
      }

      if (resolved !== undefined) {
        params.push(resolved === 'true')
        query += ` AND a.resolved = $${paramIndex++}`
      }

      query += ` ORDER BY a.timestamp DESC LIMIT ${limit}`

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

  // Get alert stats
  router.get('/stats', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          COUNT(*) as total_alerts,
          COUNT(CASE WHEN resolved = false THEN 1 END) as active_alerts,
          COUNT(CASE WHEN severity = 'critical' AND resolved = false THEN 1 END) as critical_alerts,
          COUNT(CASE WHEN severity = 'warning' AND resolved = false THEN 1 END) as warning_alerts,
          COUNT(CASE WHEN severity = 'info' AND resolved = false THEN 1 END) as info_alerts
        FROM alerts
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
      `)

      res.json({
        success: true,
        data: rows[0],
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get alerts by server
  router.get('/by-server/:serverId', async (req, res) => {
    try {
      const { serverId } = req.params

      const { rows } = await dbPool.query(
        `
        SELECT a.*, s.name as server_name
        FROM alerts a
        JOIN servers s ON a.server_id = s.id
        WHERE a.server_id = $1
        ORDER BY a.timestamp DESC
        LIMIT 1000
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

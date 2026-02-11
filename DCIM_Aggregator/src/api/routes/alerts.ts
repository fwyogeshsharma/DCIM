import { Router } from 'express'
import { Pool } from 'pg'

// Common SELECT with UI-friendly aliases
const ALERT_SELECT = `
  SELECT a.*,
    COALESCE(a.threshold_value, 0) AS threshold,
    COALESCE(a.actual_value, 0) AS value,
    UPPER(a.severity) AS severity,
    s.name AS server_name
  FROM alerts a
  JOIN servers s ON a.server_id = s.id
`

export function createAlertsRouter(dbPool: Pool): Router {
  const router = Router()

  // Get all alerts
  router.get('/', async (req, res) => {
    try {
      const { server_id, agent_id, severity, resolved, limit = 1000 } = req.query

      let query = `${ALERT_SELECT} WHERE 1=1`
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
        params.push(String(severity).toLowerCase())
        query += ` AND LOWER(a.severity) = $${paramIndex++}`
      }

      if (resolved !== undefined) {
        params.push(resolved === 'true')
        query += ` AND a.resolved = $${paramIndex++}`
      }

      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))
      params.push(safeLimit)
      query += ` ORDER BY a.timestamp DESC LIMIT $${paramIndex++}`

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
          COUNT(CASE WHEN LOWER(severity) = 'critical' AND resolved = false THEN 1 END) as critical_alerts,
          COUNT(CASE WHEN LOWER(severity) = 'warning' AND resolved = false THEN 1 END) as warning_alerts,
          COUNT(CASE WHEN LOWER(severity) = 'info' AND resolved = false THEN 1 END) as info_alerts
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
        `${ALERT_SELECT} WHERE a.server_id = $1 ORDER BY a.timestamp DESC LIMIT 1000`,
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

  // Bulk resolve alerts — MUST be before /:alertId/resolve
  router.post('/bulk/resolve', async (req, res) => {
    try {
      const { alert_ids } = req.body

      if (!Array.isArray(alert_ids) || alert_ids.length === 0) {
        return res.status(400).json({ success: false, error: 'alert_ids array is required' })
      }

      const placeholders = alert_ids.map((_, i) => `$${i + 1}`).join(', ')
      const { rowCount } = await dbPool.query(
        `UPDATE alerts SET resolved = true, resolved_at = NOW() WHERE id IN (${placeholders}) AND resolved = false`,
        alert_ids
      )

      res.json({
        success: true,
        data: { resolved_count: rowCount },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Resolve single alert
  router.post('/:alertId/resolve', async (req, res) => {
    try {
      const { alertId } = req.params

      const { rowCount } = await dbPool.query(
        'UPDATE alerts SET resolved = true, resolved_at = NOW() WHERE id = $1 AND resolved = false',
        [alertId]
      )

      if (rowCount === 0) {
        return res.status(404).json({ success: false, error: 'Alert not found or already resolved' })
      }

      res.json({
        success: true,
        message: 'Alert resolved',
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

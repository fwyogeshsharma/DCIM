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

  // Get alerts (with pagination via offset + limit)
  router.get('/', async (req, res) => {
    try {
      const { server_id, agent_id, severity, resolved, limit = 20, offset = 0 } = req.query

      let query = `${ALERT_SELECT} WHERE 1=1`
      let countQuery = `SELECT COUNT(*) as total FROM alerts a JOIN servers s ON a.server_id = s.id WHERE 1=1`
      const params: any[] = []
      const countParams: any[] = []
      let paramIndex = 1
      let countParamIndex = 1

      if (server_id) {
        params.push(server_id)
        countParams.push(server_id)
        query += ` AND a.server_id = $${paramIndex++}`
        countQuery += ` AND a.server_id = $${countParamIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        countParams.push(agent_id)
        query += ` AND a.agent_id = $${paramIndex++}`
        countQuery += ` AND a.agent_id = $${countParamIndex++}`
      }

      if (severity) {
        params.push(String(severity).toLowerCase())
        countParams.push(String(severity).toLowerCase())
        query += ` AND LOWER(a.severity) = $${paramIndex++}`
        countQuery += ` AND LOWER(a.severity) = $${countParamIndex++}`
      }

      if (resolved !== undefined) {
        params.push(resolved === 'true')
        countParams.push(resolved === 'true')
        query += ` AND a.resolved = $${paramIndex++}`
        countQuery += ` AND a.resolved = $${countParamIndex++}`
      }

      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 20, 10000))
      const safeOffset = Math.max(0, parseInt(String(offset), 10) || 0)

      params.push(safeLimit)
      query += ` ORDER BY a.timestamp DESC LIMIT $${paramIndex++}`
      params.push(safeOffset)
      query += ` OFFSET $${paramIndex++}`

      const [{ rows }, countResult] = await Promise.all([
        dbPool.query(query, params),
        dbPool.query(countQuery, countParams),
      ])

      const total = parseInt(countResult.rows[0].total, 10)

      res.json({
        success: true,
        data: rows,
        count: rows.length,
        total,
        hasMore: safeOffset + rows.length < total,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get alert counts per agent
  router.get('/counts', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          a.agent_id,
          ag.hostname,
          s.name AS server_name,
          COUNT(*) AS total,
          COUNT(CASE WHEN a.resolved = false THEN 1 END) AS active,
          COUNT(CASE WHEN LOWER(a.severity) = 'critical' AND a.resolved = false THEN 1 END) AS critical,
          COUNT(CASE WHEN LOWER(a.severity) = 'warning' AND a.resolved = false THEN 1 END) AS warning,
          COUNT(CASE WHEN LOWER(a.severity) = 'info' AND a.resolved = false THEN 1 END) AS info
        FROM alerts a
        JOIN servers s ON a.server_id = s.id
        LEFT JOIN agents ag ON a.agent_id = ag.agent_id AND a.server_id = ag.server_id
        GROUP BY a.agent_id, ag.hostname, s.name
        ORDER BY active DESC, a.agent_id
      `)

      res.json({
        success: true,
        data: rows,
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

  // Get deduplicated latest alerts (one per agent+metric_type+severity)
  router.get('/latest', async (req, res) => {
    try {
      const { agent_id, severity, limit = 100, offset = 0 } = req.query

      let whereClause = 'WHERE a.resolved = false'
      const params: any[] = []
      let paramIndex = 1

      if (agent_id) {
        params.push(agent_id)
        whereClause += ` AND a.agent_id = $${paramIndex++}`
      }

      if (severity) {
        params.push(String(severity).toLowerCase())
        whereClause += ` AND LOWER(a.severity) = $${paramIndex++}`
      }

      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 100, 10000))
      const safeOffset = Math.max(0, parseInt(String(offset), 10) || 0)

      const query = `
        WITH latest AS (
          SELECT a.*,
            ROW_NUMBER() OVER (
              PARTITION BY a.agent_id, a.server_id, a.metric_type, LOWER(a.severity)
              ORDER BY a.timestamp DESC
            ) as rn,
            COUNT(*) OVER (
              PARTITION BY a.agent_id, a.server_id, a.metric_type, LOWER(a.severity)
            )::int as occurrence_count,
            MIN(a.timestamp) OVER (
              PARTITION BY a.agent_id, a.server_id, a.metric_type, LOWER(a.severity)
            ) as first_seen
          FROM alerts a
          ${whereClause}
        )
        SELECT l.id, l.server_id, l.agent_id, l.message, l.metric_type,
          COALESCE(l.threshold_value, 0) AS threshold,
          COALESCE(l.actual_value, 0) AS value,
          UPPER(l.severity) AS severity,
          l.timestamp, l.created_at,
          s.name AS server_name,
          l.occurrence_count,
          l.first_seen
        FROM latest l
        JOIN servers s ON l.server_id = s.id
        WHERE l.rn = 1
        ORDER BY l.timestamp DESC
        LIMIT $${paramIndex++} OFFSET $${paramIndex++}
      `

      params.push(safeLimit, safeOffset)

      // Count query for pagination
      const countQuery = `
        WITH latest AS (
          SELECT a.*,
            ROW_NUMBER() OVER (
              PARTITION BY a.agent_id, a.server_id, a.metric_type, LOWER(a.severity)
              ORDER BY a.timestamp DESC
            ) as rn
          FROM alerts a
          ${whereClause}
        )
        SELECT COUNT(*)::int as total FROM latest WHERE rn = 1
      `

      const [{ rows }, countResult] = await Promise.all([
        dbPool.query(query, params),
        dbPool.query(countQuery, params.slice(0, paramIndex - 3)), // exclude limit/offset
      ])

      const total = countResult.rows[0].total

      res.json({
        success: true,
        data: rows,
        count: rows.length,
        total,
        hasMore: safeOffset + rows.length < total,
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

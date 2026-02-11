import { Express } from 'express'
import { Pool } from 'pg'
import { RedisClientType } from 'redis'
import { CacheService } from '../../services/CacheService'
import { createServersRouter } from './servers'
import { createAgentsRouter } from './agents'
import { createMetricsRouter } from './metrics'
import { createAlertsRouter } from './alerts'

export function setupRoutes(app: Express, dbPool: Pool, redisClient: RedisClientType) {
  const cacheService = new CacheService(redisClient)

  // Mount API routes
  app.use('/api/v1/servers', createServersRouter(dbPool, cacheService))
  app.use('/api/v1/agents', createAgentsRouter(dbPool))
  app.use('/api/v1/metrics', createMetricsRouter(dbPool, cacheService))
  app.use('/api/v1/alerts', createAlertsRouter(dbPool))

  // Dashboard stats endpoint
  app.get('/api/v1/dashboard/stats', async (req, res) => {
    try {
      const [serversResult, agentsResult, alertsResult] = await Promise.all([
        dbPool.query('SELECT COUNT(*) as count FROM servers WHERE enabled = true'),
        dbPool.query(`
          SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN status = 'online' THEN 1 END) as online,
            COUNT(CASE WHEN status = 'offline' THEN 1 END) as offline
          FROM agents
        `),
        dbPool.query(`
          SELECT COUNT(*) as count
          FROM alerts
          WHERE resolved = false AND timestamp >= NOW() - INTERVAL '24 hours'
        `),
      ])

      res.json({
        success: true,
        data: {
          servers: parseInt(serversResult.rows[0].count),
          agents: {
            total: parseInt(agentsResult.rows[0].total),
            online: parseInt(agentsResult.rows[0].online),
            offline: parseInt(agentsResult.rows[0].offline),
          },
          activeAlerts: parseInt(alertsResult.rows[0].count),
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Full dashboard data endpoint
  app.get('/api/v1/dashboard', async (req, res) => {
    try {
      const [agentsResult, alertsResult, metricsResult, recentAlertsResult] = await Promise.all([
        dbPool.query(`
          SELECT
            COUNT(*) as total_agents,
            COUNT(CASE WHEN status = 'online' THEN 1 END) as online_agents,
            COUNT(CASE WHEN status = 'offline' THEN 1 END) as offline_agents
          FROM agents
        `),
        dbPool.query(`
          SELECT
            COUNT(*) as total_alerts,
            COUNT(CASE WHEN LOWER(severity) = 'critical' AND resolved = false THEN 1 END) as critical_alerts
          FROM alerts
          WHERE timestamp >= NOW() - INTERVAL '24 hours'
        `),
        dbPool.query(`
          SELECT m.*, s.name as server_name
          FROM metrics m
          JOIN servers s ON m.server_id = s.id
          ORDER BY m.timestamp DESC
          LIMIT 50
        `),
        dbPool.query(`
          SELECT a.*,
            COALESCE(a.threshold_value, 0) AS threshold,
            COALESCE(a.actual_value, 0) AS value,
            UPPER(a.severity) AS severity,
            s.name as server_name
          FROM alerts a
          JOIN servers s ON a.server_id = s.id
          WHERE a.resolved = false
          ORDER BY a.timestamp DESC
          LIMIT 20
        `),
      ])

      res.json({
        success: true,
        data: {
          total_agents: parseInt(agentsResult.rows[0].total_agents),
          online_agents: parseInt(agentsResult.rows[0].online_agents),
          total_alerts: parseInt(alertsResult.rows[0].total_alerts),
          critical_alerts: parseInt(alertsResult.rows[0].critical_alerts),
          recent_metrics: metricsResult.rows,
          recent_alerts: recentAlertsResult.rows,
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // SNMP metrics query endpoint
  app.get('/api/v1/snmp/metrics', async (req, res) => {
    try {
      const { server_id, agent_id, device_name, limit = 1000 } = req.query

      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))

      let query = `
        SELECT sm.*, s.name as server_name
        FROM snmp_metrics sm
        JOIN servers s ON sm.server_id = s.id
        WHERE sm.timestamp >= NOW() - INTERVAL '24 hours'
      `
      const params: any[] = []
      let paramIndex = 1

      if (server_id) {
        params.push(server_id)
        query += ` AND sm.server_id = $${paramIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        query += ` AND sm.agent_id = $${paramIndex++}`
      }

      if (device_name) {
        params.push(device_name)
        query += ` AND sm.device_name = $${paramIndex++}`
      }

      params.push(safeLimit)
      query += ` ORDER BY sm.timestamp DESC LIMIT $${paramIndex++}`

      const { rows } = await dbPool.query(query, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // SNMP devices listing endpoint
  app.get('/api/v1/snmp/devices', async (req, res) => {
    try {
      const { server_id, agent_id } = req.query

      let query = `
        SELECT DISTINCT ON (sm.device_name, sm.device_ip)
          sm.device_name,
          sm.device_ip,
          sm.agent_id,
          sm.server_id,
          s.name as server_name,
          MAX(sm.timestamp) as last_seen
        FROM snmp_metrics sm
        JOIN servers s ON sm.server_id = s.id
      `
      const conditions: string[] = []
      const params: any[] = []
      let paramIndex = 1

      if (server_id) {
        params.push(server_id)
        conditions.push(`sm.server_id = $${paramIndex++}`)
      }

      if (agent_id) {
        params.push(agent_id)
        conditions.push(`sm.agent_id = $${paramIndex++}`)
      }

      if (conditions.length > 0) {
        query += ` WHERE ${conditions.join(' AND ')}`
      }

      query += ` GROUP BY sm.device_name, sm.device_ip, sm.agent_id, sm.server_id, s.name`
      query += ` ORDER BY sm.device_name, sm.device_ip`

      const { rows } = await dbPool.query(query, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })
}

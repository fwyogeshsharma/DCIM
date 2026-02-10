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
}

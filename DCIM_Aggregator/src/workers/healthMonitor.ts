import cron from 'node-cron'
import axios from 'axios'
import { Pool } from 'pg'
import { RedisClientType } from 'redis'
import { CacheService } from '../services/CacheService'
import { getAgentForServer } from '../utils/certManager'
import { logger } from '../utils/logger'

let dbPool: Pool
let cacheService: CacheService

export function initHealthMonitorWorker(pool: Pool, redisClient: RedisClientType) {
  dbPool = pool
  cacheService = new CacheService(redisClient)
}

// Run every 30 seconds
export function startHealthMonitorWorker() {
  cron.schedule('*/30 * * * * *', async () => {
    try {
      const { rows: servers } = await dbPool.query(
        'SELECT id, name, url FROM servers WHERE enabled = true'
      )

      for (const server of servers) {
        try {
          const startTime = Date.now()
          const baseUrl = server.url.replace(/\/api\/v\d+\/?$/, '')
          const httpsAgent = getAgentForServer(server.id)
          await axios.get(`${baseUrl}/health`, { timeout: 5000, httpsAgent })
          const responseTime = Date.now() - startTime

          await cacheService.setServerHealth(server.id, {
            status: 'healthy',
            responseTime,
            timestamp: new Date().toISOString(),
          })

          logger.debug(`Server ${server.name} is healthy (${responseTime}ms)`)
        } catch (error: any) {
          await cacheService.setServerHealth(server.id, {
            status: 'offline',
            error: error.message,
            timestamp: new Date().toISOString(),
          })

          logger.warn(`Server ${server.name} is offline: ${error.message}`)
        }
      }
    } catch (error: any) {
      logger.error('Health monitor worker error:', error.message)
    }
  })

  logger.info('Health monitor worker started (every 30s)')
}

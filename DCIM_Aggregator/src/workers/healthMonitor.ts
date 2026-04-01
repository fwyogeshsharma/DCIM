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
          const baseUrl = server.url.replace(/\/api\/v\d+\/?$/, '').replace('localhost', '127.0.0.1')
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
          const tlsCodes = [
            'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
            'CERT_SIGNATURE_FAILURE',
            'DEPTH_ZERO_SELF_SIGNED_CERT',
            'SELF_SIGNED_CERT_IN_CHAIN',
            'ERR_TLS_CERT_ALTNAME_INVALID',
            'CERT_HAS_EXPIRED',
            'ECONNRESET',
          ]
          const code: string | undefined = error.code || error.cause?.code
          const isTls = code && tlsCodes.includes(code)

          const status = isTls ? 'tls_error' : 'offline'
          const errorMsg = isTls
            ? `TLS/mTLS error (${code}): check certificates for this server`
            : (error.message || error.errors?.map((e: any) => e.message).join('; ') || code || 'connection failed')

          await cacheService.setServerHealth(server.id, {
            status,
            error: errorMsg,
            timestamp: new Date().toISOString(),
          })

          logger.warn(`Server ${server.name} ${status}: ${errorMsg}`)
        }
      }
    } catch (error: any) {
      logger.error('Health monitor worker error:', error.message)
    }
  })

  logger.info('Health monitor worker started (every 30s)')
}

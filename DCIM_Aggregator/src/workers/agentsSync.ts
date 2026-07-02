import cron from 'node-cron'
import { Pool } from 'pg'
import { DataSyncService } from '../services/DataSyncService'
import { logger } from '../utils/logger'

let syncService: DataSyncService
let dbPool: Pool

export function initAgentsSyncWorker(pool: Pool) {
  dbPool = pool
  syncService = new DataSyncService(dbPool)
}

// Run every 30 seconds
export function startAgentsSyncWorker() {
  cron.schedule('*/30 * * * * *', async () => {
    try {
      let servers: any[] = []
      try {
        const result = await dbPool.query('SELECT id, url FROM servers WHERE enabled = true')
        servers = result.rows
      } catch { return } // servers table removed in new schema

      if (servers.length === 0) return

      await Promise.all(
        servers.map((server) => syncService.syncAgentsFromServer(server.id, server.url))
      )
    } catch (error: any) {
      logger.error(`Agents sync worker error: ${error.message}`)
    }
  })

  logger.info('Agents sync worker started (every 30s)')
}

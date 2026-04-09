import cron from 'node-cron'
import { Pool } from 'pg'
import { DataSyncService } from '../services/DataSyncService'
import { logger } from '../utils/logger'

let syncService: DataSyncService
let dbPool: Pool

export function initTrapsSyncWorker(pool: Pool) {
  dbPool = pool
  syncService = new DataSyncService(dbPool)
}

// Run every 15 seconds
export function startTrapsSyncWorker() {
  cron.schedule('*/15 * * * * *', async () => {
    try {
      const { rows: servers } = await dbPool.query(
        'SELECT id, url FROM servers WHERE enabled = true'
      )

      if (servers.length === 0) return

      await Promise.all(
        servers.map((server: any) =>
          syncService.syncTrapsFromServer(server.id, server.url)
        )
      )
    } catch (error: any) {
      logger.error('Traps sync worker error:', error.message)
    }
  })

  logger.info('Traps sync worker started (every 15s)')
}

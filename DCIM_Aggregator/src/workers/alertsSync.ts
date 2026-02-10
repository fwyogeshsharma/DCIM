import cron from 'node-cron'
import { Pool } from 'pg'
import { DataSyncService } from '../services/DataSyncService'
import { logger } from '../utils/logger'

let syncService: DataSyncService
let dbPool: Pool

export function initAlertsSyncWorker(pool: Pool) {
  dbPool = pool
  syncService = new DataSyncService(dbPool)
}

// Run every 15 seconds
export function startAlertsSyncWorker() {
  cron.schedule('*/15 * * * * *', async () => {
    try {
      // Get enabled servers
      const { rows: servers } = await dbPool.query(
        'SELECT id, url FROM servers WHERE enabled = true'
      )

      if (servers.length === 0) {
        return
      }

      // Sync alerts from all servers in parallel
      await Promise.all(
        servers.map((server) =>
          syncService.syncAlertsFromServer(server.id, server.url)
        )
      )
    } catch (error: any) {
      logger.error('Alerts sync worker error:', error.message)
    }
  })

  logger.info('Alerts sync worker started (every 15s)')
}

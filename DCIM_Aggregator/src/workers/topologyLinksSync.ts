import cron from 'node-cron'
import { Pool } from 'pg'
import { DataSyncService } from '../services/DataSyncService'
import { logger } from '../utils/logger'

let syncService: DataSyncService
let dbPool: Pool

export function initTopologyLinksSyncWorker(pool: Pool) {
  dbPool = pool
  syncService = new DataSyncService(dbPool)
}

// Run every 30 seconds — links change only when walker runs (every 15m)
export function startTopologyLinksSyncWorker() {
  cron.schedule('*/30 * * * * *', async () => {
    try {
      const { rows: servers } = await dbPool.query(
        'SELECT id, url FROM servers WHERE enabled = true'
      )

      if (servers.length === 0) return

      await Promise.all(
        servers.map((server: any) =>
          syncService.syncTopologyLinksFromServer(server.id, server.url)
        )
      )
    } catch (error: any) {
      logger.error('Topology links sync worker error:', error.message)
    }
  })

  logger.info('Topology links sync worker started (every 30s)')
}

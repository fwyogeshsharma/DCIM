import cron from 'node-cron'
import { Pool } from 'pg'
import { DataSyncService } from '../services/DataSyncService'
import { logger } from '../utils/logger'

let syncService: DataSyncService
let dbPool: Pool

export function initMetricsSyncWorker(pool: Pool) {
  dbPool = pool
  syncService = new DataSyncService(dbPool)
}

// Run every 10 seconds
export function startMetricsSyncWorker() {
  cron.schedule('*/10 * * * * *', async () => {
    try {
      // Get enabled servers
      const { rows: servers } = await dbPool.query(
        'SELECT id, url FROM servers WHERE enabled = true'
      )

      if (servers.length === 0) {
        return
      }

      // Sync metrics from all servers in parallel
      await Promise.all(
        servers.map((server) =>
          syncService.syncMetricsFromServer(server.id, server.url)
        )
      )

      // Also sync SNMP metrics
      await Promise.all(
        servers.map((server) =>
          syncService.syncSNMPMetricsFromServer(server.id, server.url)
        )
      )
    } catch (error: any) {
      logger.error('Metrics sync worker error:', error.message)
    }
  })

  logger.info('Metrics sync worker started (every 10s)')
}

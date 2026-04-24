import { Pool } from 'pg'
import { RedisClientType } from 'redis'
import { initMetricsSyncWorker, startMetricsSyncWorker } from './metricsSync'
import { initAgentsSyncWorker, startAgentsSyncWorker } from './agentsSync'
import { initAlertsSyncWorker, startAlertsSyncWorker } from './alertsSync'
import { initHealthMonitorWorker, startHealthMonitorWorker } from './healthMonitor'
import { initTrapsSyncWorker, startTrapsSyncWorker } from './trapsSync'
import { initTopologyLinksSyncWorker, startTopologyLinksSyncWorker } from './topologyLinksSync'
import { initTrapStreamWorker, startTrapStreamWorker } from './trapStream'
import { logger } from '../utils/logger'

export function startWorkers(dbPool: Pool, redisClient: RedisClientType) {
  // Initialize workers with dependencies
  initMetricsSyncWorker(dbPool)
  initAgentsSyncWorker(dbPool)
  initAlertsSyncWorker(dbPool)
  initHealthMonitorWorker(dbPool, redisClient)
  initTrapsSyncWorker(dbPool)
  initTopologyLinksSyncWorker(dbPool)
  initTrapStreamWorker(dbPool)

  // Delay first server API calls by 10 seconds to allow servers to come online
  logger.info('Workers initialized — first sync in 10 seconds...')
  setTimeout(() => {
    startMetricsSyncWorker()
    startAgentsSyncWorker()
    startAlertsSyncWorker()
    startHealthMonitorWorker()
    startTrapsSyncWorker()
    startTopologyLinksSyncWorker()
    startTrapStreamWorker()
    logger.info('All workers started')
  }, 10000)
}

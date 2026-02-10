import { Pool } from 'pg'
import { RedisClientType } from 'redis'
import { initMetricsSyncWorker, startMetricsSyncWorker } from './metricsSync'
import { initAgentsSyncWorker, startAgentsSyncWorker } from './agentsSync'
import { initAlertsSyncWorker, startAlertsSyncWorker } from './alertsSync'
import { initHealthMonitorWorker, startHealthMonitorWorker } from './healthMonitor'

export function startWorkers(dbPool: Pool, redisClient: RedisClientType) {
  // Initialize workers with dependencies
  initMetricsSyncWorker(dbPool)
  initAgentsSyncWorker(dbPool)
  initAlertsSyncWorker(dbPool)
  initHealthMonitorWorker(dbPool, redisClient)

  // Start all workers
  startMetricsSyncWorker()
  startAgentsSyncWorker()
  startAlertsSyncWorker()
  startHealthMonitorWorker()
}

import cron from 'node-cron'
import { Pool } from 'pg'
import { config } from '../config/database'
import { getMongoDb } from '../database/mongo'
import { logger } from '../utils/logger'

// Replaces the retired TimescaleDB continuous aggregates (metrics_5m /
// energy_metrics_5m, formerly created by 001/007). Every interval this worker
// summarizes the previous complete window across ALL metric names — from both
// the `metrics` and `energy_metrics` hypertables — and writes ONE document per
// window to the MongoDB `metric_summaries` collection:
//
//   {
//     window_start, window_end, generated_at, interval_minutes,
//     metric_count, total_samples,
//     metrics: [
//       { name, source: 'metrics'|'energy_metrics',
//         avg, min, max, samples, devices },
//       …one entry per metric name (40+)…
//     ]
//   }
//
// Metric names are kept in an array (not as document keys) because they
// contain dots (e.g. system.cpu_utilization_percent), which MongoDB path
// queries cannot address as field names.

const COLLECTION = 'metric_summaries'

let dbPool: Pool
let indexesReady = false

export function initMetricsSummaryWorker(pool: Pool) {
  dbPool = pool
}

const SUMMARY_SQL = (table: 'metrics' | 'energy_metrics') => `
  SELECT
    metric_name,
    AVG(value)                AS avg,
    MIN(value)                AS min,
    MAX(value)                AS max,
    COUNT(*)::bigint          AS samples,
    COUNT(DISTINCT device_id) AS devices
  FROM ${table}
  WHERE ts >= $1 AND ts < $2
  GROUP BY metric_name
  ORDER BY metric_name
`

async function ensureIndexes() {
  if (indexesReady) return
  const db = await getMongoDb()
  const col = db.collection(COLLECTION)

  // One document per (window, interval); upserts make restarts idempotent.
  await col.createIndex(
    { window_start: 1, interval_minutes: 1 },
    { unique: true, name: 'uix_window' }
  )

  const expireAfterSeconds = config.metricsSummary.retentionDays * 86400
  try {
    await col.createIndex(
      { window_end: 1 },
      { expireAfterSeconds, name: 'ttl_window_end' }
    )
  } catch (err: any) {
    if (err.codeName === 'IndexOptionsConflict') {
      // Retention changed since the index was created — update in place.
      await db.command({
        collMod: COLLECTION,
        index: { name: 'ttl_window_end', expireAfterSeconds },
      })
    } else {
      throw err
    }
  }
  indexesReady = true
}

export async function summarizeWindow(windowStart: Date, windowEnd: Date) {
  const [metricRows, energyRows] = await Promise.all([
    dbPool.query(SUMMARY_SQL('metrics'), [windowStart, windowEnd]),
    dbPool.query(SUMMARY_SQL('energy_metrics'), [windowStart, windowEnd]),
  ])

  const entries = [
    ...metricRows.rows.map((r) => ({ source: 'metrics', ...toEntry(r) })),
    ...energyRows.rows.map((r) => ({ source: 'energy_metrics', ...toEntry(r) })),
  ]

  if (entries.length === 0) {
    logger.debug(`Metrics summary: no samples in window ${windowStart.toISOString()} — skipped`)
    return
  }

  await ensureIndexes()
  const db = await getMongoDb()
  await db.collection(COLLECTION).updateOne(
    { window_start: windowStart, interval_minutes: config.metricsSummary.intervalMinutes },
    {
      $set: {
        window_end: windowEnd,
        generated_at: new Date(),
        metric_count: entries.length,
        total_samples: entries.reduce((n, e) => n + e.samples, 0),
        metrics: entries,
      },
    },
    { upsert: true }
  )

  logger.info(
    `Metrics summary written: ${entries.length} metrics, window ${windowStart.toISOString()} → ${windowEnd.toISOString()}`
  )
}

function toEntry(r: any) {
  return {
    name: r.metric_name as string,
    avg: Number(r.avg),
    min: Number(r.min),
    max: Number(r.max),
    samples: Number(r.samples),
    devices: Number(r.devices),
  }
}

// Previous complete window aligned to the interval grid, so document windows
// line up regardless of when the process (re)started.
function previousWindow(intervalMinutes: number): [Date, Date] {
  const ms = intervalMinutes * 60_000
  const end = new Date(Math.floor(Date.now() / ms) * ms)
  return [new Date(end.getTime() - ms), end]
}

export function startMetricsSummaryWorker() {
  const interval = config.metricsSummary.intervalMinutes
  cron.schedule(`*/${interval} * * * *`, async () => {
    try {
      const [start, end] = previousWindow(interval)
      await summarizeWindow(start, end)
    } catch (error: any) {
      // Mongo down or query failure — log and try again next tick. The rest of
      // the aggregator is unaffected.
      logger.error(`Metrics summary worker error: ${error.message}`)
    }
  })

  logger.info(
    `Metrics summary worker started (every ${interval}m → MongoDB ${config.mongo.database}.${COLLECTION}, TTL ${config.metricsSummary.retentionDays}d)`
  )
}
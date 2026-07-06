import cron from 'node-cron'
import { Pool } from 'pg'
import { config } from '../config/database'
import { getMongoDb } from '../database/mongo'
import { logger } from '../utils/logger'

// Replaces the retired TimescaleDB continuous aggregates (metrics_5m /
// energy_metrics_5m, formerly created by 001/007). Every interval this worker
// summarizes the previous complete window — from both the `metrics` and
// `energy_metrics` hypertables — and writes ONE document per (window, device)
// to the MongoDB `metric_summaries` collection:
//
//   {
//     window_start, window_end, generated_at, interval_minutes,
//     device_id,
//     metric_count, total_samples,
//     metrics: [
//       { name, source: 'metrics'|'energy_metrics', avg, min, max, samples },
//       …one entry per metric name reported by this device…
//     ]
//   }
//
// Metric names are kept in an array (not as document keys) because they
// contain dots (e.g. system.cpu_utilization_percent), which MongoDB path
// queries cannot address as field names.
//
// All devices for a window are written in a single bulkWrite — one round
// trip regardless of fleet size — instead of one updateOne per device.

const COLLECTION = 'metric_summaries'

let dbPool: Pool
let indexesReady = false

export function initMetricsSummaryWorker(pool: Pool) {
  dbPool = pool
}

const SUMMARY_SQL = (table: 'metrics' | 'energy_metrics') => `
  SELECT
    device_id,
    metric_name,
    AVG(value)       AS avg,
    MIN(value)       AS min,
    MAX(value)       AS max,
    COUNT(*)::bigint AS samples
  FROM ${table}
  WHERE ts >= $1 AND ts < $2
  GROUP BY device_id, metric_name
`
// device_id + metric_name are the leading columns of idx_metrics_device /
// idx_energy_device, and ts is the hypertable partition key, so the ts range
// filter above prunes to a single chunk before the GROUP BY runs — grouping
// by device adds no extra scan cost over the old metric-only grouping.

async function ensureIndexes() {
  if (indexesReady) return
  const db = await getMongoDb()
  const col = db.collection(COLLECTION)

  // One document per (window, interval, device); upserts make restarts idempotent.
  await col.createIndex(
    { window_start: 1, interval_minutes: 1, device_id: 1 },
    { unique: true, name: 'uix_window_device' }
  )

  // Fast lookups of a single device's recent summary history.
  await col.createIndex(
    { device_id: 1, window_start: -1 },
    { name: 'ix_device_window' }
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

  // Drop the old per-window-only unique index from the previous schema (one
  // doc per window across all devices combined) if it's still present.
  try {
    await col.dropIndex('uix_window')
  } catch (err: any) {
    if (err.codeName !== 'IndexNotFound' && err.codeName !== 'NamespaceNotFound') throw err
  }

  indexesReady = true
}

type MetricEntry = {
  name: string
  source: 'metrics' | 'energy_metrics'
  avg: number
  min: number
  max: number
  samples: number
}

export async function summarizeWindow(windowStart: Date, windowEnd: Date) {
  const [metricRows, energyRows] = await Promise.all([
    dbPool.query(SUMMARY_SQL('metrics'), [windowStart, windowEnd]),
    dbPool.query(SUMMARY_SQL('energy_metrics'), [windowStart, windowEnd]),
  ])

  // Group rows by device so each device gets exactly one summary document.
  const byDevice = new Map<string, MetricEntry[]>()
  collectEntries(byDevice, metricRows.rows, 'metrics')
  collectEntries(byDevice, energyRows.rows, 'energy_metrics')

  if (byDevice.size === 0) {
    logger.debug(`Metrics summary: no samples in window ${windowStart.toISOString()} — skipped`)
    return
  }

  await ensureIndexes()
  const db = await getMongoDb()
  const generatedAt = new Date()
  const interval = config.metricsSummary.intervalMinutes

  const ops = Array.from(byDevice.entries()).map(([device_id, metrics]) => ({
    updateOne: {
      filter: { window_start: windowStart, interval_minutes: interval, device_id },
      update: {
        $set: {
          window_end: windowEnd,
          generated_at: generatedAt,
          metric_count: metrics.length,
          total_samples: metrics.reduce((n, e) => n + e.samples, 0),
          metrics,
        },
      },
      upsert: true,
    },
  }))

  // Single round trip for every device in this window — keeps write overhead
  // flat as the fleet grows, unlike N sequential updateOne calls. Unordered
  // so one bad doc can't block the rest of the batch.
  await db.collection(COLLECTION).bulkWrite(ops, { ordered: false })

  logger.info(
    `Metrics summary written: ${byDevice.size} devices, window ${windowStart.toISOString()} → ${windowEnd.toISOString()}`
  )
}

function collectEntries(byDevice: Map<string, MetricEntry[]>, rows: any[], source: 'metrics' | 'energy_metrics') {
  for (const r of rows) {
    const deviceId = String(r.device_id)
    let list = byDevice.get(deviceId)
    if (!list) {
      list = []
      byDevice.set(deviceId, list)
    }
    list.push({
      name: r.metric_name as string,
      source,
      avg: Number(r.avg),
      min: Number(r.min),
      max: Number(r.max),
      samples: Number(r.samples),
    })
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

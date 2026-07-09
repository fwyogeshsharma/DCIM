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
//
// Tiered retention — each tier is aggregated into the next before it expires:
//
//   metric_summaries         5m docs     TTL retention5mHours   (default 24h)
//     └─ hourly rollup (minute 2 of every hour)
//   metric_summaries_hourly  1h docs     TTL hourlyRetentionDays (default 7d)
//     └─ daily rollup (every hour at minute 5, previous complete UTC day)
//   metric_summaries_daily   1d docs     TTL dailyRetentionDays (default 180d)
//
// All tiers share the same document shape (interval_minutes: 5 / 60 / 1440).
// avg is recomputed sample-weighted at every step, so a daily avg equals the
// avg the day's raw samples would have produced; min/max/samples merge
// exactly. Each tier lives in its own collection because a TTL index expires
// a whole collection uniformly — the tiers need different TTLs. Expired docs
// are deleted by Mongo's TTL monitor; nothing else has to clean them up.

const COLLECTION = 'metric_summaries'
const HOURLY_COLLECTION = 'metric_summaries_hourly'
const DAILY_COLLECTION = 'metric_summaries_daily'
const HOURLY_INTERVAL_MINUTES = 60
const DAILY_INTERVAL_MINUTES = 1440

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

async function ensureBaseIndexes(collection: string) {
  const db = await getMongoDb()
  const col = db.collection(collection)

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
}

async function ensureTtlIndex(collection: string, expireAfterSeconds: number) {
  const db = await getMongoDb()
  try {
    await db.collection(collection).createIndex(
      { window_end: 1 },
      { expireAfterSeconds, name: 'ttl_window_end' }
    )
  } catch (err: any) {
    if (err.codeName === 'IndexOptionsConflict') {
      // Retention changed since the index was created — update in place.
      // This is also how existing deployments move from the old 180d TTL on
      // the 5m collection down to retention5mHours.
      await db.command({
        collMod: collection,
        index: { name: 'ttl_window_end', expireAfterSeconds },
      })
    } else {
      throw err
    }
  }
}

async function ensureIndexes() {
  if (indexesReady) return

  await ensureBaseIndexes(COLLECTION)
  await ensureBaseIndexes(HOURLY_COLLECTION)
  await ensureBaseIndexes(DAILY_COLLECTION)
  await ensureTtlIndex(DAILY_COLLECTION, config.metricsSummary.dailyRetentionDays * 86400)
  // Deliberately NOT the 5m/hourly TTLs: catchUpRollups applies each tier's
  // TTL only after everything that TTL would expire has been rolled up into
  // the next tier — otherwise the first deploy of a shorter TTL could delete
  // history before it was ever aggregated. The daily tier is terminal
  // (nothing rolls up out of it), so its TTL is safe to apply immediately.

  // Drop the old per-window-only unique index from the previous schema (one
  // doc per window across all devices combined) if it's still present.
  try {
    const db = await getMongoDb()
    await db.collection(COLLECTION).dropIndex('uix_window')
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

// The two aggregation steps of the tier pyramid. Each merges one target
// window of source-tier documents into one target-tier document per device.
type TierSpec = {
  label: string
  sourceCollection: string
  sourceIntervalMinutes: () => number
  targetCollection: string
  targetIntervalMinutes: number
}

const HOURLY_TIER: TierSpec = {
  label: 'Hourly',
  sourceCollection: COLLECTION,
  sourceIntervalMinutes: () => config.metricsSummary.intervalMinutes,
  targetCollection: HOURLY_COLLECTION,
  targetIntervalMinutes: HOURLY_INTERVAL_MINUTES,
}

const DAILY_TIER: TierSpec = {
  label: 'Daily',
  sourceCollection: HOURLY_COLLECTION,
  sourceIntervalMinutes: () => HOURLY_INTERVAL_MINUTES,
  targetCollection: DAILY_COLLECTION,
  targetIntervalMinutes: DAILY_INTERVAL_MINUTES,
}

// Merges one window of source-tier documents into one document per device in
// the target tier. Reads only Mongo (never the hypertables): each source
// entry carries its sample count, so the merged avg can be recomputed exactly
// as the sample-weighted mean of the source avgs. Upserts on the same unique
// key shape as every tier, so re-running a window (restart, catch-up,
// repeated daily tick) overwrites instead of duplicating.
async function rollupTier(tier: TierSpec, windowStart: Date, windowEnd: Date) {
  await ensureIndexes()
  const db = await getMongoDb()

  const sourceDocs = await db
    .collection(tier.sourceCollection)
    .find({
      interval_minutes: tier.sourceIntervalMinutes(),
      window_start: { $gte: windowStart, $lt: windowEnd },
    })
    .toArray()

  if (sourceDocs.length === 0) {
    logger.debug(
      `${tier.label} rollup: no source documents in window ${windowStart.toISOString()} — skipped`
    )
    return
  }

  type MetricAcc = MetricEntry & { weightedSum: number }
  const byDevice = new Map<string, { windows: number; metrics: Map<string, MetricAcc> }>()

  for (const doc of sourceDocs) {
    const deviceId = String(doc.device_id)
    let dev = byDevice.get(deviceId)
    if (!dev) {
      dev = { windows: 0, metrics: new Map() }
      byDevice.set(deviceId, dev)
    }
    dev.windows++
    for (const m of doc.metrics as MetricEntry[]) {
      const key = `${m.source}|${m.name}`
      const acc = dev.metrics.get(key)
      if (!acc) {
        dev.metrics.set(key, { ...m, weightedSum: m.avg * m.samples })
      } else {
        acc.weightedSum += m.avg * m.samples
        acc.samples += m.samples
        if (m.min < acc.min) acc.min = m.min
        if (m.max > acc.max) acc.max = m.max
      }
    }
  }

  const generatedAt = new Date()
  const ops = Array.from(byDevice.entries()).map(([device_id, dev]) => {
    const metrics: MetricEntry[] = Array.from(dev.metrics.values()).map(
      ({ weightedSum, ...m }) => ({
        ...m,
        avg: m.samples > 0 ? weightedSum / m.samples : 0,
      })
    )
    return {
      updateOne: {
        filter: {
          window_start: windowStart,
          interval_minutes: tier.targetIntervalMinutes,
          device_id,
        },
        update: {
          $set: {
            window_end: windowEnd,
            generated_at: generatedAt,
            source_windows: dev.windows,
            metric_count: metrics.length,
            total_samples: metrics.reduce((n, e) => n + e.samples, 0),
            metrics,
          },
        },
        upsert: true,
      },
    }
  })

  await db.collection(tier.targetCollection).bulkWrite(ops, { ordered: false })

  logger.info(
    `${tier.label} rollup written: ${byDevice.size} devices, window ${windowStart.toISOString()} → ${windowEnd.toISOString()}`
  )
}

export const rollupHour = (start: Date, end: Date) => rollupTier(HOURLY_TIER, start, end)
export const rollupDay = (start: Date, end: Date) => rollupTier(DAILY_TIER, start, end)

// Re-roll every complete target window still derivable from the tier's source
// collection, oldest first.
async function backfillTier(tier: TierSpec) {
  const windowMs = tier.targetIntervalMinutes * 60_000
  const db = await getMongoDb()

  const oldest = await db
    .collection(tier.sourceCollection)
    .find({ interval_minutes: tier.sourceIntervalMinutes() })
    .sort({ window_start: 1 })
    .limit(1)
    .project({ window_start: 1 })
    .next()
  if (!oldest) return

  const current = Math.floor(Date.now() / windowMs) * windowMs
  const first = Math.floor(new Date(oldest.window_start).getTime() / windowMs) * windowMs
  for (let start = first; start < current; start += windowMs) {
    await rollupTier(tier, new Date(start), new Date(start + windowMs))
  }
}

// Backfill every tier, oldest first, applying each tier's (shorter) TTL only
// AFTER everything that TTL would expire has been rolled up into the next
// tier. Runs once at worker start. Two jobs in one:
//   - downtime repair: any window whose rollup tick was missed while the
//     aggregator was down is recomputed before its source documents expire;
//   - retention-shrink migration: a collection carrying history under an old,
//     longer TTL (e.g. hourly 180d → 7d) is fully rolled up into the next
//     tier BEFORE the TTL is shortened, so no history is expired unaggregated.
// Idempotent — windows already rolled up are overwritten with the same
// values. If Mongo is unreachable the TTLs are left as-is (the long, safe
// direction) and the next restart retries.
export async function catchUpRollups() {
  try {
    await ensureIndexes()

    await backfillTier(HOURLY_TIER)
    await ensureTtlIndex(COLLECTION, config.metricsSummary.retention5mHours * 3600)

    await backfillTier(DAILY_TIER)
    await ensureTtlIndex(HOURLY_COLLECTION, config.metricsSummary.hourlyRetentionDays * 86400)
  } catch (error: any) {
    logger.error(`Rollup catch-up error: ${error.message}`)
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

  // Minute 2, not minute 0: the top-of-hour 5m summary (e.g. 09:55–10:00) is
  // written at 10:00 by the tick above; rolling up at 10:02 guarantees the
  // hour's final 5m document is already in Mongo.
  cron.schedule('2 * * * *', async () => {
    try {
      const [start, end] = previousWindow(HOURLY_INTERVAL_MINUTES)
      await rollupHour(start, end)
    } catch (error: any) {
      logger.error(`Hourly rollup worker error: ${error.message}`)
    }
  })

  // Every hour at minute 5 (after the hourly rollup above), re-roll the
  // previous complete UTC day. Running hourly instead of once at midnight
  // makes the daily tier self-healing — a failed tick is retried within the
  // hour — and sidesteps server-timezone vs UTC-day-boundary drift; the
  // upsert makes the 23 extra runs per day harmless overwrites.
  cron.schedule('5 * * * *', async () => {
    try {
      const [start, end] = previousWindow(DAILY_INTERVAL_MINUTES)
      await rollupDay(start, end)
    } catch (error: any) {
      logger.error(`Daily rollup worker error: ${error.message}`)
    }
  })

  void catchUpRollups()

  logger.info(
    `Metrics summary worker started (every ${interval}m → ${config.mongo.database}.${COLLECTION}, ` +
      `TTL ${config.metricsSummary.retention5mHours}h; hourly rollup → ${HOURLY_COLLECTION}, ` +
      `TTL ${config.metricsSummary.hourlyRetentionDays}d; daily rollup → ${DAILY_COLLECTION}, ` +
      `TTL ${config.metricsSummary.dailyRetentionDays}d)`
  )
}

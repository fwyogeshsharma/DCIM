import { Router } from 'express'
import { Pool } from 'pg'
import { CacheService } from '../../services/CacheService'

// Read API for the energy_metrics hypertable (BACnet/IP power-meter telemetry,
// e.g. Verdigris EV2). Keyed by device_id — the same UUID the UI knows as the
// device's agent_id. circuit/phase are first-class columns (see migration 007),
// so per-breaker and per-phase breakdowns need no tag string-parsing here.

const HEARTBEAT_TIMEOUT_SECONDS = 300

const VALID_TIME_RANGES = new Set([
  '1h', '6h', '12h', '24h', '48h', '7 days', '14 days', '30 days', '90 days',
  '1 hour', '6 hours', '12 hours', '24 hours', '48 hours',
])

function validateTimeRange(input: string): string {
  if (VALID_TIME_RANGES.has(input)) return input
  if (/^(\d+)\s*(h|hours?|d|days?|m|minutes?)$/.test(input)) return input
  return '24h'
}

// Guard the time_bucket interval against injection (it's interpolated, not a
// bound param, because $-params for interval literals are awkward in pg).
function validateInterval(input: string): string {
  return /^\d+\s*(m|min|mins|minute|minutes|h|hour|hours|d|day|days)$/.test(input) ? input : '5 minutes'
}

export function createEnergyRouter(dbPool: Pool, cacheService: CacheService): Router {
  const router = Router()

  // ── Energy meter devices ───────────────────────────────────────────────────
  // The set of power meters: devices flagged device_role='energy_monitor', with
  // their latest-reading time and circuit count so the UI can list them even
  // before drilling into readings.
  router.get('/devices', async (_req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          d.id::text       AS device_id,
          d.hostname,
          d.mgmt_ip::text  AS mgmt_ip,
          d.network_id,
          CASE WHEN d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
               THEN 'online' ELSE 'offline' END                          AS status,
          MAX(em.ts)                                                     AS last_reading,
          MAX(em.attributes->>'scope')                                   AS scope,
          COUNT(DISTINCT em.circuit) FILTER (WHERE em.circuit <> '')::int AS circuit_count
        FROM devices d
        LEFT JOIN energy_metrics em
          ON em.device_id = d.id AND em.ts >= NOW() - INTERVAL '24 hours'
        WHERE d.device_role = 'energy_monitor'
        GROUP BY d.id, d.hostname, d.mgmt_ip, d.network_id, d.last_seen_at
        ORDER BY d.hostname
      `)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Latest snapshot ─────────────────────────────────────────────────────────
  // One row per (device, metric_name, tag): the most recent reading. The UI
  // groups these into panel scalars (tag=''), per-phase (phase<>''), per-circuit
  // (circuit<>''), and harmonics (tag like 'H%'). device_id optional → all meters.
  router.get('/snapshot', async (req, res) => {
    try {
      const { device_id } = req.query
      const cacheKey = `energy:snapshot:${device_id || 'all'}`
      const cached = await cacheService.get(cacheKey)
      if (cached) return res.json(cached)

      let query = `
        SELECT DISTINCT ON (em.device_id, em.metric_name, em.tag)
          em.device_id::text  AS device_id,
          d.hostname,
          d.mgmt_ip::text     AS mgmt_ip,
          CASE WHEN d.last_seen_at >= NOW() - INTERVAL '${HEARTBEAT_TIMEOUT_SECONDS} seconds'
               THEN 'online' ELSE 'offline' END AS status,
          em.metric_name,
          em.tag,
          em.circuit,
          em.phase,
          em.value,
          em.ts,
          em.attributes,
          em.attributes->>'scope' AS scope
        FROM energy_metrics em
        JOIN devices d ON d.id = em.device_id
        WHERE em.ts >= NOW() - INTERVAL '24 hours'
      `
      const params: any[] = []
      if (device_id) { params.push(device_id); query += ` AND em.device_id = $1::uuid` }
      query += ` ORDER BY em.device_id, em.metric_name, em.tag, em.ts DESC`

      const { rows } = await dbPool.query(query, params)
      const response = { success: true, data: rows, count: rows.length }
      await cacheService.set(cacheKey, response, 10)
      res.json(response)
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Timeseries ───────────────────────────────────────────────────────────────
  // Time-bucketed trend for one quantity on one meter. tag pins a specific
  // phase/circuit/harmonic instance; omit it for panel scalars (tag='').
  router.get('/timeseries', async (req, res) => {
    try {
      const { device_id, metric_name, tag = '', time_range = '24h', interval = '5 minutes' } = req.query
      if (!device_id || !metric_name) {
        return res.status(400).json({ success: false, error: 'device_id and metric_name are required' })
      }
      const safeTimeRange = validateTimeRange(String(time_range))
      const safeInterval = validateInterval(String(interval))

      const { rows } = await dbPool.query(`
        SELECT
          time_bucket('${safeInterval}', ts) AS bucket,
          AVG(value) AS avg_value,
          MAX(value) AS max_value,
          MIN(value) AS min_value
        FROM energy_metrics
        WHERE device_id = $1::uuid
          AND metric_name = $2
          AND tag = $3
          AND ts >= NOW() - $4::interval
        GROUP BY bucket
        ORDER BY bucket ASC
      `, [device_id, metric_name, tag, safeTimeRange])
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Scope trend ──────────────────────────────────────────────────────────────
  // Time-bucketed active power summed per meter scope (facility / it / cooling),
  // for the PUE tab's shared Power Trend. Two-level aggregation: average each
  // meter within a bucket, then sum meters of the same scope.
  router.get('/trend', async (req, res) => {
    try {
      const { time_range = '24h', interval = '1 hour', metric_name = 'energy.active_power_kw' } = req.query
      const safeTimeRange = validateTimeRange(String(time_range))
      const safeInterval = validateInterval(String(interval))

      const { rows } = await dbPool.query(`
        SELECT bucket, scope, SUM(meter_avg) AS total_kw
        FROM (
          SELECT
            time_bucket('${safeInterval}', ts)        AS bucket,
            device_id,
            COALESCE(attributes->>'scope', '')         AS scope,
            AVG(value)                                 AS meter_avg
          FROM energy_metrics
          WHERE metric_name = $1
            AND tag = ''
            AND ts >= NOW() - $2::interval
          GROUP BY bucket, device_id, COALESCE(attributes->>'scope', '')
        ) per_meter
        GROUP BY bucket, scope
        ORDER BY bucket ASC
      `, [metric_name, safeTimeRange])
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

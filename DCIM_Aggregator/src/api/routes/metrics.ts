import { Router } from 'express'
import { Pool } from 'pg'
import { CacheService } from '../../services/CacheService'

const VALID_TIME_RANGES = new Set([
  '1h', '6h', '12h', '24h', '48h', '7 days', '14 days', '30 days', '90 days',
  '1 hour', '6 hours', '12 hours', '24 hours', '48 hours',
])

function validateTimeRange(input: string): string {
  if (VALID_TIME_RANGES.has(input)) return input
  if (/^(\d+)\s*(h|hours?|d|days?|m|minutes?)$/.test(input)) return input
  return '24h'
}

export function createMetricsRouter(dbPool: Pool, cacheService: CacheService): Router {
  const router = Router()

  // List metrics
  router.get('/', async (req, res) => {
    try {
      const { server_id, agent_id, metric_type, time_range = '24h', limit = 1000 } = req.query
      const safeTimeRange = validateTimeRange(String(time_range))
      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))

      const cacheKey = `metrics:${server_id || 'all'}:${agent_id || 'all'}:${metric_type || 'all'}:${safeTimeRange}`
      const cached = await cacheService.get(cacheKey)
      if (cached) return res.json(cached)

      let query = `
        SELECT
          m.device_id::text  AS agent_id,
          m.ts               AS timestamp,
          m.metric_name      AS metric_type,
          m.tag,
          m.value,
          m.collector_agent,
          m.collector_protocol,
          d.hostname         AS server_name
        FROM metrics m
        LEFT JOIN devices d ON d.id = m.device_id
        WHERE m.ts >= NOW() - $1::interval
      `
      const params: any[] = [safeTimeRange]
      let idx = 2

      if (agent_id) { params.push(agent_id);    query += ` AND m.device_id = $${idx++}::uuid` }
      if (metric_type) { params.push(metric_type); query += ` AND m.metric_name = $${idx++}` }

      params.push(safeLimit)
      query += ` ORDER BY m.ts DESC LIMIT $${idx++}`

      const { rows } = await dbPool.query(query, params)
      const response = { success: true, data: rows, count: rows.length }
      await cacheService.set(cacheKey, response, 10)
      res.json(response)
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Aggregated metrics (uses metrics_5m continuous aggregate)
  router.get('/aggregated', async (req, res) => {
    try {
      const { agent_id, metric_type, interval = '1 hour', time_range = '7 days' } = req.query
      const safeTimeRange = validateTimeRange(String(time_range))

      let query = `
        SELECT
          time_bucket($1::interval, ts) AS bucket,
          device_id::text               AS agent_id,
          metric_name                   AS metric_type,
          tag,
          AVG(value)  AS avg_value,
          MAX(value)  AS max_value,
          MIN(value)  AS min_value,
          COUNT(*)    AS sample_count
        FROM metrics
        WHERE ts >= NOW() - $2::interval
      `
      const params: any[] = [interval, safeTimeRange]
      let idx = 3

      if (agent_id)    { params.push(agent_id);    query += ` AND device_id = $${idx++}::uuid` }
      if (metric_type) { params.push(metric_type); query += ` AND metric_name = $${idx++}` }

      query += ` GROUP BY bucket, device_id, metric_name, tag ORDER BY bucket DESC`

      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Latest metric per type for a device
  router.get('/latest/:agentId', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT DISTINCT ON (metric_name)
          device_id::text  AS agent_id,
          metric_name      AS metric_type,
          value,
          ts               AS timestamp,
          tag,
          collector_protocol
        FROM metrics
        WHERE device_id = $1::uuid
        ORDER BY metric_name, ts DESC
      `, [req.params.agentId])
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // SNMP metrics (collector_protocol = 'SNMP')
  router.get('/snmp', async (req, res) => {
    try {
      const { agent_id, device_name, limit = 1000 } = req.query
      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))

      let query = `
        SELECT
          m.device_id::text   AS agent_id,
          m.ts                AS timestamp,
          m.metric_name,
          m.tag,
          m.value,
          d.hostname          AS device_name,
          d.mgmt_ip::text     AS device_ip
        FROM metrics m
        LEFT JOIN devices d ON d.id = m.device_id
        WHERE m.collector_protocol = 'SNMP'
          AND m.ts >= NOW() - INTERVAL '24 hours'
      `
      const params: any[] = []
      let idx = 1

      if (agent_id)    { params.push(agent_id);    query += ` AND m.device_id = $${idx++}::uuid` }
      if (device_name) { params.push(device_name); query += ` AND d.hostname = $${idx++}` }

      params.push(safeLimit)
      query += ` ORDER BY m.ts DESC LIMIT $${idx++}`

      const { rows } = await dbPool.query(query, params)
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}
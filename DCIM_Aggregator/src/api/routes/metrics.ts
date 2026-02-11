import { Router } from 'express'
import { Pool } from 'pg'
import { CacheService } from '../../services/CacheService'

const VALID_TIME_RANGES = new Set([
  '1h', '6h', '12h', '24h', '48h', '7 days', '14 days', '30 days', '90 days',
  '1 hour', '6 hours', '12 hours', '24 hours', '48 hours',
])

function validateTimeRange(input: string): string {
  if (VALID_TIME_RANGES.has(input)) return input
  // Try to parse as a simple number+unit pattern
  const match = input.match(/^(\d+)\s*(h|hours?|d|days?|m|minutes?)$/)
  if (match) return input
  return '24h'
}

export function createMetricsRouter(dbPool: Pool, cacheService: CacheService): Router {
  const router = Router()

  // Get metrics (with caching)
  router.get('/', async (req, res) => {
    try {
      const {
        server_id,
        agent_id,
        metric_type,
        time_range = '24h',
        limit = 1000,
      } = req.query

      const safeTimeRange = validateTimeRange(String(time_range))
      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))

      // Build cache key
      const cacheKey = `metrics:${server_id || 'all'}:${agent_id || 'all'}:${metric_type || 'all'}:${safeTimeRange}`

      // Check cache first
      const cached = await cacheService.get(cacheKey)
      if (cached) {
        return res.json(cached)
      }

      // Build query
      let query = `
        SELECT m.*, s.name as server_name
        FROM metrics m
        JOIN servers s ON m.server_id = s.id
        WHERE m.timestamp >= NOW() - $1::interval
      `
      const params: any[] = [safeTimeRange]
      let paramIndex = 2

      if (server_id) {
        params.push(server_id)
        query += ` AND m.server_id = $${paramIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        query += ` AND m.agent_id = $${paramIndex++}`
      }

      if (metric_type) {
        params.push(metric_type)
        query += ` AND m.metric_type = $${paramIndex++}`
      }

      params.push(safeLimit)
      query += ` ORDER BY m.timestamp DESC LIMIT $${paramIndex++}`

      const { rows } = await dbPool.query(query, params)

      const response = { success: true, data: rows, count: rows.length }

      // Cache for 10 seconds
      await cacheService.set(cacheKey, response, 10)

      res.json(response)
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get aggregated metrics (hourly averages)
  router.get('/aggregated', async (req, res) => {
    try {
      const {
        server_id,
        agent_id,
        metric_type,
        interval = '1 hour',
        time_range = '7 days',
      } = req.query

      const safeTimeRange = validateTimeRange(String(time_range))

      let query = `
        SELECT
          time_bucket($1::interval, timestamp) AS bucket,
          server_id,
          agent_id,
          metric_type,
          AVG(value) as avg_value,
          MAX(value) as max_value,
          MIN(value) as min_value,
          COUNT(*) as sample_count
        FROM metrics
        WHERE timestamp >= NOW() - $2::interval
      `
      const params: any[] = [interval, safeTimeRange]
      let paramIndex = 3

      if (server_id) {
        params.push(server_id)
        query += ` AND server_id = $${paramIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        query += ` AND agent_id = $${paramIndex++}`
      }

      if (metric_type) {
        params.push(metric_type)
        query += ` AND metric_type = $${paramIndex++}`
      }

      query += `
        GROUP BY bucket, server_id, agent_id, metric_type
        ORDER BY bucket DESC
      `

      const { rows } = await dbPool.query(query, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get latest metrics for an agent
  router.get('/latest/:agentId', async (req, res) => {
    try {
      const { agentId } = req.params

      const { rows } = await dbPool.query(
        `
        SELECT DISTINCT ON (metric_type)
          m.*,
          s.name as server_name
        FROM metrics m
        JOIN servers s ON m.server_id = s.id
        WHERE m.agent_id = $1
        ORDER BY metric_type, timestamp DESC
      `,
        [agentId]
      )

      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get SNMP metrics
  router.get('/snmp', async (req, res) => {
    try {
      const { server_id, agent_id, device_name, limit = 1000 } = req.query

      const safeLimit = Math.max(1, Math.min(parseInt(String(limit), 10) || 1000, 10000))

      let query = `
        SELECT sm.*, s.name as server_name
        FROM snmp_metrics sm
        JOIN servers s ON sm.server_id = s.id
        WHERE sm.timestamp >= NOW() - INTERVAL '24 hours'
      `
      const params: any[] = []
      let paramIndex = 1

      if (server_id) {
        params.push(server_id)
        query += ` AND sm.server_id = $${paramIndex++}`
      }

      if (agent_id) {
        params.push(agent_id)
        query += ` AND sm.agent_id = $${paramIndex++}`
      }

      if (device_name) {
        params.push(device_name)
        query += ` AND sm.device_name = $${paramIndex++}`
      }

      params.push(safeLimit)
      query += ` ORDER BY sm.timestamp DESC LIMIT $${paramIndex++}`

      const { rows } = await dbPool.query(query, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

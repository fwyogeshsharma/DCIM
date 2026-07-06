import { Router } from 'express'
import { Pool } from 'pg'
import { isAssignedOnlyScoped } from '../../config/rbac'

// Technicians (assigned_only scope) only see alerts on devices they have an
// open ticket assigned to — matches rbac.yml's "resolve alert tied to their
// ticket". Root/admin/other roles are never scoped (see isAssignedOnlyScoped).
const ASSIGNED_DEVICE_SUBQUERY = `SELECT device_id FROM critical_tickets WHERE assigned_to = `

// Maps events row to the Alert shape the UI expects.
// Resolution state is stored in event_payload->>'resolved'.
const ALERT_SELECT = `
  SELECT
    e.id::text                                                            AS id,
    e.device_id::text                                                     AS agent_id,
    e.ts                                                                  AS timestamp,
    e.event_name                                                          AS metric_type,
    COALESCE(e.event_payload->>'message', e.event_name)                   AS message,
    UPPER(e.severity)                                                     AS severity,
    e.kind,
    e.source_ip::text                                                     AS source_ip,
    e.source_hostname,
    e.trap_oid,
    COALESCE((e.event_payload->>'resolved')::boolean, false)              AS resolved,
    e.event_payload->>'resolved_at'                                       AS resolved_at,
    COALESCE((e.event_payload->>'threshold_value')::float, 0)             AS threshold_value,
    COALESCE((e.event_payload->>'actual_value')::float, 0)                AS actual_value,
    COALESCE((e.event_payload->>'threshold_value')::float, 0)             AS threshold,
    COALESCE((e.event_payload->>'actual_value')::float, 0)                AS value,
    d.hostname                                                            AS server_name,
    e.ts                                                                  AS created_at
  FROM events e
  LEFT JOIN devices d ON d.id = e.device_id
`

export function createAlertsRouter(dbPool: Pool): Router {
  const router = Router()

  // List alerts / events
  router.get('/', async (req, res) => {
    try {
      const { agent_id, severity, resolved, limit = 20, offset = 0 } = req.query
      let query = `${ALERT_SELECT} WHERE 1=1`
      let countQuery = `SELECT COUNT(*) AS total FROM events e WHERE 1=1`
      const params: any[] = []
      const countParams: any[] = []
      let i = 1

      if (isAssignedOnlyScoped(req.authUser!.roles)) {
        params.push(req.authUser!.username);      query      += ` AND e.device_id IN (${ASSIGNED_DEVICE_SUBQUERY}$${i})`
        countParams.push(req.authUser!.username);  countQuery += ` AND e.device_id IN (${ASSIGNED_DEVICE_SUBQUERY}$${i})`
        i++
      }
      if (agent_id) {
        params.push(agent_id);      query      += ` AND e.device_id = $${i}::uuid`
        countParams.push(agent_id); countQuery += ` AND e.device_id = $${i}::uuid`
        i++
      }
      if (severity) {
        params.push(String(severity).toLowerCase());      query      += ` AND LOWER(e.severity) = $${i}`
        countParams.push(String(severity).toLowerCase()); countQuery += ` AND LOWER(e.severity) = $${i}`
        i++
      }
      if (resolved !== undefined) {
        const isResolved = resolved === 'true'
        params.push(isResolved);      query      += ` AND COALESCE((e.event_payload->>'resolved')::boolean, false) = $${i}`
        countParams.push(isResolved); countQuery += ` AND COALESCE((e.event_payload->>'resolved')::boolean, false) = $${i}`
        i++
      }

      const safeLimit  = Math.max(1, Math.min(parseInt(String(limit),  10) || 20, 10000))
      const safeOffset = Math.max(0,            parseInt(String(offset), 10) || 0)

      params.push(safeLimit);  query += ` ORDER BY e.ts DESC LIMIT $${i++}`
      params.push(safeOffset); query += ` OFFSET $${i++}`

      const [{ rows }, countResult] = await Promise.all([
        dbPool.query(query, params),
        dbPool.query(countQuery, countParams),
      ])
      const total = parseInt(countResult.rows[0].total, 10)
      res.json({ success: true, data: rows, count: rows.length, total, hasMore: safeOffset + rows.length < total })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Alert counts per device
  router.get('/counts', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          e.device_id::text AS agent_id,
          d.hostname,
          d.network_id      AS server_name,
          COUNT(*)                                                                                        AS total,
          COUNT(CASE WHEN COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END)   AS active,
          COUNT(CASE WHEN LOWER(e.severity) = 'critical' AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS critical,
          COUNT(CASE WHEN LOWER(e.severity) IN ('major','warning') AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS warning,
          COUNT(CASE WHEN LOWER(e.severity) IN ('minor','info','informational') AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS info,
          MAX(e.ts) AS last_ts
        FROM events e
        LEFT JOIN devices d ON d.id = e.device_id
        GROUP BY e.device_id, d.hostname, d.network_id
        ORDER BY last_ts DESC NULLS LAST
      `)
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Alert counts per device, keyed by mgmt_ip — used by topology to attach
  // alert badges to ingest-API devices (which have no agent_id, only an IP).
  router.get('/by-device-ip', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          d.mgmt_ip::text AS device_ip,
          d.hostname,
          COUNT(*)                                                                                                        AS total,
          COUNT(CASE WHEN COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END)                   AS active,
          COUNT(CASE WHEN LOWER(e.severity) = 'critical'                       AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS critical,
          COUNT(CASE WHEN LOWER(e.severity) IN ('major','warning')             AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS warning,
          COUNT(CASE WHEN LOWER(e.severity) IN ('minor','info','informational') AND COALESCE((e.event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS info
        FROM events e
        JOIN devices d ON d.id = e.device_id
        WHERE d.mgmt_ip IS NOT NULL
        GROUP BY d.mgmt_ip, d.hostname
        ORDER BY active DESC
      `)
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Alert stats
  router.get('/stats', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          COUNT(*) AS total_alerts,
          COUNT(CASE WHEN COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END)   AS active_alerts,
          COUNT(CASE WHEN LOWER(severity) = 'critical'                    AND COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS critical_alerts,
          COUNT(CASE WHEN LOWER(severity) IN ('major','warning')          AND COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS warning_alerts,
          COUNT(CASE WHEN LOWER(severity) IN ('minor','info','informational') AND COALESCE((event_payload->>'resolved')::boolean, false) = false THEN 1 END) AS info_alerts
        FROM events
        WHERE ts >= NOW() - INTERVAL '24 hours'
      `)
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Alerts by network (server)
  router.get('/by-server/:serverId', async (req, res) => {
    try {
      const { rows } = await dbPool.query(
        `${ALERT_SELECT} JOIN devices dj ON dj.id = e.device_id AND dj.network_id = $1
         ORDER BY e.ts DESC LIMIT 1000`,
        [req.params.serverId]
      )
      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Deduplicated latest active alerts
  router.get('/latest', async (req, res) => {
    try {
      const { agent_id, severity, limit = 100, offset = 0 } = req.query
      let whereClause = `WHERE COALESCE((e.event_payload->>'resolved')::boolean, false) = false`
      const params: any[] = []
      let i = 1

      if (isAssignedOnlyScoped(req.authUser!.roles)) {
        params.push(req.authUser!.username); whereClause += ` AND e.device_id IN (${ASSIGNED_DEVICE_SUBQUERY}$${i++})`
      }
      if (agent_id) { params.push(agent_id); whereClause += ` AND e.device_id = $${i++}::uuid` }
      if (severity) { params.push(String(severity).toLowerCase()); whereClause += ` AND LOWER(e.severity) = $${i++}` }

      const safeLimit  = Math.max(1, Math.min(parseInt(String(limit),  10) || 100, 10000))
      const safeOffset = Math.max(0,            parseInt(String(offset), 10) || 0)

      const query = `
        WITH deduped AS (
          SELECT e.*,
            ROW_NUMBER() OVER (
              PARTITION BY e.device_id, e.event_name, LOWER(e.severity)
              ORDER BY e.ts DESC
            ) AS rn,
            COUNT(*) OVER (
              PARTITION BY e.device_id, e.event_name, LOWER(e.severity)
            )::int AS occurrence_count,
            MIN(e.ts) OVER (
              PARTITION BY e.device_id, e.event_name, LOWER(e.severity)
            ) AS first_seen
          FROM events e
          ${whereClause}
        )
        SELECT
          dd.id::text                                                        AS id,
          dd.device_id::text                                                 AS agent_id,
          dd.ts                                                              AS timestamp,
          dd.event_name                                                      AS metric_type,
          COALESCE(dd.event_payload->>'message', dd.event_name)             AS message,
          UPPER(dd.severity)                                                 AS severity,
          COALESCE((dd.event_payload->>'threshold_value')::float, 0)        AS threshold,
          COALESCE((dd.event_payload->>'actual_value')::float, 0)           AS value,
          dd.ts                                                              AS created_at,
          d.hostname                                                         AS server_name,
          dd.occurrence_count,
          dd.first_seen
        FROM deduped dd
        LEFT JOIN devices d ON d.id = dd.device_id
        WHERE dd.rn = 1
        ORDER BY dd.ts DESC
        LIMIT $${i++} OFFSET $${i++}
      `
      params.push(safeLimit, safeOffset)

      const countQuery = `
        WITH deduped AS (
          SELECT e.*,
            ROW_NUMBER() OVER (
              PARTITION BY e.device_id, e.event_name, LOWER(e.severity)
              ORDER BY e.ts DESC
            ) AS rn
          FROM events e
          ${whereClause}
        )
        SELECT COUNT(*)::int AS total FROM deduped WHERE rn = 1
      `

      const [{ rows }, countResult] = await Promise.all([
        dbPool.query(query, params),
        dbPool.query(countQuery, params.slice(0, i - 3)),
      ])
      const total = countResult.rows[0].total
      res.json({ success: true, data: rows, count: rows.length, total, hasMore: safeOffset + rows.length < total })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Bulk resolve — MUST be before /:alertId/resolve
  router.post('/bulk/resolve', async (req, res) => {
    try {
      const { alert_ids } = req.body
      if (!Array.isArray(alert_ids) || alert_ids.length === 0) {
        return res.status(400).json({ success: false, error: 'alert_ids array is required' })
      }
      const placeholders = alert_ids.map((_, idx) => `$${idx + 1}::uuid`).join(', ')
      const { rowCount } = await dbPool.query(
        `UPDATE events
         SET event_payload = COALESCE(event_payload, '{}') ||
           jsonb_build_object('resolved', true, 'resolved_at', now()::text)
         WHERE id IN (${placeholders})
           AND COALESCE((event_payload->>'resolved')::boolean, false) = false`,
        alert_ids
      )
      res.json({ success: true, data: { resolved_count: rowCount } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Resolve single alert
  router.post('/:alertId/resolve', async (req, res) => {
    try {
      const { rowCount } = await dbPool.query(
        `UPDATE events
         SET event_payload = COALESCE(event_payload, '{}') ||
           jsonb_build_object('resolved', true, 'resolved_at', now()::text)
         WHERE id = $1::uuid
           AND COALESCE((event_payload->>'resolved')::boolean, false) = false`,
        [req.params.alertId]
      )
      if (rowCount === 0) {
        return res.status(404).json({ success: false, error: 'Alert not found or already resolved' })
      }
      res.json({ success: true, message: 'Alert resolved' })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}
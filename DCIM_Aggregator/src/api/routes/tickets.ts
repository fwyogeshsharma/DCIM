import { Router } from 'express'
import { Pool } from 'pg'
import { isAssignedOnlyScoped } from '../../config/rbac'

// Maps a critical_tickets row (+ joined device hostname) to the shape the UI reads.
const TICKET_SELECT = `
  SELECT
    t.id::text          AS id,
    t.ticket_number,
    t.org_id,
    t.datacenter_id,
    t.event_id::text    AS event_id,
    t.device_id::text   AS device_id,
    COALESCE(t.source_hostname, d.hostname) AS source_hostname,
    t.severity,
    t.priority,
    t.status,
    t.category,
    t.title,
    t.description,
    t.assigned_to,
    t.root_cause,
    t.resolution,
    t.sla_due_at,
    t.acknowledged_at,
    t.resolved_at,
    t.closed_at,
    t.attributes,
    t.created_at,
    t.updated_at
  FROM critical_tickets t
  LEFT JOIN devices d ON d.id = t.device_id
`

// Open = not yet resolved/closed.
const OPEN_STATUSES = `('open', 'acknowledged', 'in_progress')`

// Records one entry in the ticket_activity audit trail.
async function logActivity(
  dbPool: Pool,
  ticketId: string,
  activityType: string,
  opts: { actor?: string; fromStatus?: string; toStatus?: string; message?: string } = {}
) {
  await dbPool.query(
    `INSERT INTO ticket_activity (ticket_id, activity_type, actor, from_status, to_status, message)
     VALUES ($1::uuid, $2, $3, $4, $5, $6)`,
    [ticketId, activityType, opts.actor ?? null, opts.fromStatus ?? null, opts.toStatus ?? null, opts.message ?? null]
  )
}

export function createTicketsRouter(dbPool: Pool): Router {
  const router = Router()

  // ── List tickets ────────────────────────────────────────────────────────
  router.get('/', async (req, res) => {
    try {
      const { status, priority, assigned_to, category, q, limit = 50, offset = 0 } = req.query
      let where = `WHERE 1=1`
      const params: any[] = []
      let i = 1

      // status=open is a convenience alias for "anything not resolved/closed".
      if (status === 'open') {
        where += ` AND t.status IN ${OPEN_STATUSES}`
      } else if (status) {
        params.push(status); where += ` AND t.status = $${i++}`
      }
      if (priority)    { params.push(priority);    where += ` AND t.priority = $${i++}` }
      // Technicians (assigned_only scope) can only ever see their own tickets —
      // this overrides any assigned_to the client tries to pass. Everyone else
      // (root/admin/other roles) keeps the requested filter, if any.
      if (isAssignedOnlyScoped(req.authUser!.roles)) {
        params.push(req.authUser!.username); where += ` AND t.assigned_to = $${i++}`
      } else if (assigned_to) {
        params.push(assigned_to); where += ` AND t.assigned_to = $${i++}`
      }
      if (category)    { params.push(category);    where += ` AND t.category = $${i++}` }
      if (q) {
        params.push(`%${q}%`)
        where += ` AND (t.title ILIKE $${i} OR t.ticket_number ILIKE $${i} OR t.description ILIKE $${i})`
        i++
      }

      const safeLimit  = Math.max(1, Math.min(parseInt(String(limit),  10) || 50, 500))
      const safeOffset = Math.max(0,            parseInt(String(offset), 10) || 0)

      const listQuery = `
        ${TICKET_SELECT}
        ${where}
        ORDER BY
          CASE t.status WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END,
          t.priority ASC,
          t.created_at DESC
        LIMIT $${i++} OFFSET $${i++}
      `
      params.push(safeLimit, safeOffset)

      const countQuery = `SELECT COUNT(*)::int AS total FROM critical_tickets t ${where}`

      const [{ rows }, countResult] = await Promise.all([
        dbPool.query(listQuery, params),
        dbPool.query(countQuery, params.slice(0, params.length - 2)),
      ])
      const total = countResult.rows[0].total
      res.json({ success: true, data: rows, count: rows.length, total, hasMore: safeOffset + rows.length < total })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Ticket counts by status / priority ──────────────────────────────────
  router.get('/stats', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          COUNT(*)::int                                                              AS total,
          COUNT(CASE WHEN status IN ${OPEN_STATUSES} THEN 1 END)::int                AS open,
          COUNT(CASE WHEN status = 'open' THEN 1 END)::int                           AS unacked,
          COUNT(CASE WHEN status = 'in_progress' THEN 1 END)::int                    AS in_progress,
          COUNT(CASE WHEN status = 'resolved' THEN 1 END)::int                       AS resolved,
          COUNT(CASE WHEN status = 'closed' THEN 1 END)::int                         AS closed,
          COUNT(CASE WHEN status IN ${OPEN_STATUSES} AND assigned_to IS NULL THEN 1 END)::int AS unassigned,
          COUNT(CASE WHEN status IN ${OPEN_STATUSES} AND priority = 'P1' THEN 1 END)::int     AS p1_open
        FROM critical_tickets
      `)
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Distinct assignees (for the assign dropdown) ─────────────────────────
  router.get('/assignees', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT assigned_to AS name, COUNT(*)::int AS open_tickets
        FROM critical_tickets
        WHERE assigned_to IS NOT NULL AND status IN ${OPEN_STATUSES}
        GROUP BY assigned_to
        ORDER BY open_tickets DESC, assigned_to
      `)
      res.json({ success: true, data: rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Single ticket + activity trail ───────────────────────────────────────
  router.get('/:id', async (req, res) => {
    try {
      const [ticketResult, activityResult] = await Promise.all([
        dbPool.query(`${TICKET_SELECT} WHERE t.id = $1::uuid`, [req.params.id]),
        dbPool.query(
          `SELECT id::text, ticket_id::text, activity_type, actor, from_status, to_status, message, created_at
           FROM ticket_activity WHERE ticket_id = $1::uuid ORDER BY created_at ASC`,
          [req.params.id]
        ),
      ])
      if (ticketResult.rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Ticket not found' })
      }
      const ticket = ticketResult.rows[0]
      if (isAssignedOnlyScoped(req.authUser!.roles) && ticket.assigned_to !== req.authUser!.username) {
        return res.status(403).json({ success: false, error: 'This ticket is not assigned to you' })
      }
      res.json({ success: true, data: { ...ticket, activity: activityResult.rows } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Create a ticket from a critical alert/event ──────────────────────────
  // Body: { event_id, priority?, category?, assigned_to?, actor?, title?, description? }
  // Pulls device/severity/message from the events row when present. If an OPEN
  // ticket already exists for the event, returns it instead of duplicating.
  router.post('/from-alert', async (req, res) => {
    try {
      const { event_id, priority, category, assigned_to, actor, title, description } = req.body
      if (!event_id) {
        return res.status(400).json({ success: false, error: 'event_id is required' })
      }

      // Dedup: one open ticket per originating event.
      const existing = await dbPool.query(
        `${TICKET_SELECT} WHERE t.event_id = $1::uuid AND t.status IN ${OPEN_STATUSES} LIMIT 1`,
        [event_id]
      )
      if (existing.rows.length > 0) {
        return res.status(200).json({ success: true, data: existing.rows[0], deduped: true })
      }

      // Hydrate from the event/device.
      const ev = await dbPool.query(
        `SELECT
           e.id::text                                          AS event_id,
           e.device_id::text                                   AS device_id,
           COALESCE(e.source_hostname, d.hostname)             AS source_hostname,
           LOWER(e.severity)                                   AS severity,
           e.event_name,
           COALESCE(e.event_payload->>'message', e.event_name) AS message,
           d.org_id,
           d.datacenter_id
         FROM events e
         LEFT JOIN devices d ON d.id = e.device_id
         WHERE e.id = $1::uuid`,
        [event_id]
      )
      const row = ev.rows[0]
      if (!row) {
        return res.status(404).json({ success: false, error: 'Alert/event not found' })
      }

      const insert = await dbPool.query(
        `INSERT INTO critical_tickets
           (org_id, datacenter_id, event_id, device_id, source_hostname,
            severity, priority, status, category, title, description, assigned_to)
         VALUES ($1, $2, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12)
         RETURNING id::text`,
        [
          row.org_id ?? 'default',
          row.datacenter_id ?? 'default',
          row.event_id,
          row.device_id,
          row.source_hostname,
          row.severity || 'critical',
          priority || 'P1',
          assigned_to ? 'acknowledged' : 'open',
          category || 'other',
          title || row.message || row.event_name || 'Critical alert',
          description ?? row.message ?? null,
          assigned_to ?? null,
        ]
      )
      const newId = insert.rows[0].id

      await logActivity(dbPool, newId, 'created', {
        actor, message: `Ticket created from alert ${row.event_name ?? event_id}`,
      })
      if (assigned_to) {
        await logActivity(dbPool, newId, 'assignment', { actor, toStatus: 'acknowledged', message: `Assigned to ${assigned_to}` })
      }

      const created = await dbPool.query(`${TICKET_SELECT} WHERE t.id = $1::uuid`, [newId])
      res.status(201).json({ success: true, data: created.rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Create a ticket manually ─────────────────────────────────────────────
  router.post('/', async (req, res) => {
    try {
      const {
        org_id, datacenter_id, device_id, event_id, source_hostname,
        severity, priority, category, title, description, assigned_to, sla_due_at, actor,
      } = req.body
      if (!title) {
        return res.status(400).json({ success: false, error: 'title is required' })
      }
      const insert = await dbPool.query(
        `INSERT INTO critical_tickets
           (org_id, datacenter_id, device_id, event_id, source_hostname,
            severity, priority, status, category, title, description, assigned_to, sla_due_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
         RETURNING id::text`,
        [
          org_id || 'default',
          datacenter_id || 'default',
          device_id ?? null,
          event_id ?? null,
          source_hostname ?? null,
          severity || 'critical',
          priority || 'P1',
          assigned_to ? 'acknowledged' : 'open',
          category || 'other',
          title,
          description ?? null,
          assigned_to ?? null,
          sla_due_at ?? null,
        ]
      )
      const newId = insert.rows[0].id
      await logActivity(dbPool, newId, 'created', { actor, message: 'Ticket created manually' })
      if (assigned_to) {
        await logActivity(dbPool, newId, 'assignment', { actor, message: `Assigned to ${assigned_to}` })
      }
      const created = await dbPool.query(`${TICKET_SELECT} WHERE t.id = $1::uuid`, [newId])
      res.status(201).json({ success: true, data: created.rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Update a ticket (status / priority / category / fields) ──────────────
  router.patch('/:id', async (req, res) => {
    try {
      const id = req.params.id
      const { status, priority, category, assigned_to, root_cause, resolution, title, description, actor } = req.body

      const current = await dbPool.query(`SELECT status, assigned_to FROM critical_tickets WHERE id = $1::uuid`, [id])
      if (current.rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Ticket not found' })
      }
      const prevStatus = current.rows[0].status

      const sets: string[] = ['updated_at = now()']
      const params: any[] = []
      let i = 1
      const setCol = (col: string, val: any) => { params.push(val); sets.push(`${col} = $${i++}`) }

      if (priority   !== undefined) setCol('priority', priority)
      if (category   !== undefined) setCol('category', category)
      if (assigned_to !== undefined) setCol('assigned_to', assigned_to)
      if (root_cause !== undefined) setCol('root_cause', root_cause)
      if (resolution !== undefined) setCol('resolution', resolution)
      if (title      !== undefined) setCol('title', title)
      if (description !== undefined) setCol('description', description)

      // Status transitions stamp the matching lifecycle timestamp once.
      if (status !== undefined && status !== prevStatus) {
        setCol('status', status)
        if (status === 'acknowledged') sets.push(`acknowledged_at = COALESCE(acknowledged_at, now())`)
        if (status === 'in_progress') sets.push(`acknowledged_at = COALESCE(acknowledged_at, now())`)
        if (status === 'resolved')    sets.push(`resolved_at = COALESCE(resolved_at, now())`)
        if (status === 'closed')      sets.push(`closed_at = COALESCE(closed_at, now())`)
      }

      params.push(id)
      await dbPool.query(`UPDATE critical_tickets SET ${sets.join(', ')} WHERE id = $${i}::uuid`, params)

      if (status !== undefined && status !== prevStatus) {
        await logActivity(dbPool, id, 'status_change', { actor, fromStatus: prevStatus, toStatus: status })
      }
      if (assigned_to !== undefined && assigned_to !== current.rows[0].assigned_to) {
        await logActivity(dbPool, id, 'assignment', {
          actor, message: assigned_to ? `Assigned to ${assigned_to}` : 'Unassigned',
        })
      }

      const updated = await dbPool.query(`${TICKET_SELECT} WHERE t.id = $1::uuid`, [id])
      res.json({ success: true, data: updated.rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Assign a ticket to a person ──────────────────────────────────────────
  router.post('/:id/assign', async (req, res) => {
    try {
      const { assigned_to, actor } = req.body
      const { rowCount } = await dbPool.query(
        `UPDATE critical_tickets
         SET assigned_to = $1,
             status = CASE WHEN status = 'open' AND $1 IS NOT NULL THEN 'acknowledged' ELSE status END,
             acknowledged_at = CASE WHEN acknowledged_at IS NULL AND $1 IS NOT NULL THEN now() ELSE acknowledged_at END,
             updated_at = now()
         WHERE id = $2::uuid`,
        [assigned_to ?? null, req.params.id]
      )
      if (rowCount === 0) {
        return res.status(404).json({ success: false, error: 'Ticket not found' })
      }
      await logActivity(dbPool, req.params.id, 'assignment', {
        actor, message: assigned_to ? `Assigned to ${assigned_to}` : 'Unassigned',
      })
      const updated = await dbPool.query(`${TICKET_SELECT} WHERE t.id = $1::uuid`, [req.params.id])
      res.json({ success: true, data: updated.rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Add a comment ─────────────────────────────────────────────────────────
  router.post('/:id/comment', async (req, res) => {
    try {
      const { message, actor } = req.body
      if (!message) {
        return res.status(400).json({ success: false, error: 'message is required' })
      }
      const exists = await dbPool.query(`SELECT 1 FROM critical_tickets WHERE id = $1::uuid`, [req.params.id])
      if (exists.rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Ticket not found' })
      }
      await logActivity(dbPool, req.params.id, 'comment', { actor, message })
      const activity = await dbPool.query(
        `SELECT id::text, ticket_id::text, activity_type, actor, from_status, to_status, message, created_at
         FROM ticket_activity WHERE ticket_id = $1::uuid ORDER BY created_at ASC`,
        [req.params.id]
      )
      res.json({ success: true, data: activity.rows })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

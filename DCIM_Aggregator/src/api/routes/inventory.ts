import { Router } from 'express'
import { Pool } from 'pg'

export function createInventoryRouter(dbPool: Pool): Router {
  const router = Router()

  // ── Summary ────────────────────────────────────────────────────────────────
  router.get('/summary', async (req, res) => {
    try {
      const { org_id = 'default', datacenter_id } = req.query

      const conditions: string[] = ['org_id = $1']
      const params: any[] = [org_id]
      let i = 2

      if (datacenter_id) { conditions.push(`datacenter_id = $${i++}`); params.push(datacenter_id) }

      const { rows: devRows } = await dbPool.query(`
        SELECT
          COUNT(*)::int                                                                     AS total,
          COUNT(CASE WHEN status = 'idle'           THEN 1 END)::int                       AS idle,
          COUNT(CASE WHEN status = 'in_use'         THEN 1 END)::int                       AS in_use,
          COUNT(CASE WHEN status = 'maintenance'    THEN 1 END)::int                       AS maintenance,
          COUNT(CASE WHEN status = 'decommissioned' THEN 1 END)::int                       AS decommissioned,
          COALESCE(SUM(cpu_cores),   0)::int                                               AS total_cpu_cores,
          COALESCE(SUM(ram_gb),      0)                                                    AS total_ram_gb,
          COALESCE(SUM(storage_gb),  0)                                                    AS total_storage_gb,
          COALESCE(SUM(CASE WHEN status = 'idle' THEN cpu_cores   ELSE 0 END), 0)::int    AS available_cpu_cores,
          COALESCE(SUM(CASE WHEN status = 'idle' THEN ram_gb      ELSE 0 END), 0)          AS available_ram_gb,
          COALESCE(SUM(CASE WHEN status = 'idle' THEN storage_gb  ELSE 0 END), 0)          AS available_storage_gb,
          COALESCE(SUM(storage_gb) - SUM(COALESCE(storage_used_gb, 0)), 0)                 AS storage_remaining_gb
        FROM dc_inventory_devices
        WHERE ${conditions.join(' AND ')}
      `, params)

      const { rows: linkRows } = await dbPool.query(`
        SELECT COUNT(*)::int AS total_links,
               COUNT(CASE WHEN status = 'active'       THEN 1 END)::int AS active_links,
               COUNT(CASE WHEN status = 'disconnected' THEN 1 END)::int AS disconnected_links
        FROM dc_inventory_links
        WHERE org_id = $1 ${datacenter_id ? `AND datacenter_id = $2` : ''}
      `, params)

      res.json({ success: true, data: { devices: devRows[0], links: linkRows[0] } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Device analytics (metrics from monitoring system) ─────────────────────
  // Must be before /:id to avoid routing conflict.
  router.get('/devices/:id/analytics', async (req, res) => {
    try {
      const { id } = req.params
      const { hours = '24' } = req.query
      const safeHours = Math.min(parseInt(String(hours)) || 24, 720)

      // Look up linked monitoring device_id
      const { rows: invRows } = await dbPool.query(
        'SELECT device_id FROM dc_inventory_devices WHERE id = $1::uuid',
        [id]
      )
      if (invRows.length === 0) {
        return res.status(404).json({ success: false, error: 'Inventory device not found' })
      }

      const monitoringId = invRows[0].device_id
      if (!monitoringId) {
        return res.json({ success: true, data: { linked: false, metrics: [], latest: [] } })
      }

      const [timeseriesResult, latestResult] = await Promise.all([
        dbPool.query(`
          SELECT
            time_bucket('5 minutes', ts) AS bucket,
            metric_name,
            AVG(value)::double precision AS avg_value,
            MAX(value)::double precision AS max_value,
            MIN(value)::double precision AS min_value
          FROM metrics
          WHERE device_id = $1::uuid
            AND ts >= NOW() - INTERVAL '${safeHours} hours'
            AND metric_name IN ('cpu_usage','memory_usage','disk_usage','power_draw',
                                'bytes_in','bytes_out','packets_in','packets_out')
          GROUP BY bucket, metric_name
          ORDER BY bucket ASC
        `, [monitoringId]),
        dbPool.query(`
          SELECT DISTINCT ON (metric_name)
            metric_name, value, ts AS timestamp, tag
          FROM metrics
          WHERE device_id = $1::uuid
          ORDER BY metric_name, ts DESC
        `, [monitoringId]),
      ])

      res.json({
        success: true,
        data: {
          linked: true,
          monitoring_device_id: monitoringId,
          metrics: timeseriesResult.rows,
          latest: latestResult.rows,
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── List devices ───────────────────────────────────────────────────────────
  router.get('/devices', async (req, res) => {
    try {
      const { org_id = 'default', datacenter_id, status, device_type, search, limit = '500' } = req.query
      const safeLimit = Math.min(parseInt(String(limit)) || 500, 2000)

      const conditions: string[] = ['d.org_id = $1']
      const params: any[] = [org_id]
      let i = 2

      if (datacenter_id) { conditions.push(`d.datacenter_id = $${i++}`); params.push(datacenter_id) }
      if (status)        { conditions.push(`d.status = $${i++}`);        params.push(status) }
      if (device_type)   { conditions.push(`d.device_type = $${i++}`);   params.push(device_type) }
      if (search) {
        conditions.push(`(d.hostname ILIKE $${i} OR d.vendor ILIKE $${i} OR d.model ILIKE $${i} OR d.serial_number ILIKE $${i})`)
        params.push(`%${search}%`)
        i++
      }

      params.push(safeLimit)
      // Live CPU/RAM utilisation derived from the latest raw metrics, so the
      // inventory list shows real numbers rather than only the stored snapshot.
      // Two producers: servers (UCD-SNMP server.*) and network gear (gNMI
      // system.*). Mirrors the JS derivation in DCIM_UI src/lib/deviceHealth.ts:
      //   CPU% = system.cpu_utilization_percent
      //          | (100 − server.cpu_idle_percent)
      //          | (server.cpu_user_percent + server.cpu_system_percent)
      //   RAM% = system.memory_utilization_percent
      //          | system.memory_used_bytes / system.memory_total_bytes
      //          | (server.memory_total_kb − server.memory_available_kb) / server.memory_total_kb
      const { rows } = await dbPool.query(`
        WITH latest_health AS (
          SELECT DISTINCT ON (m.device_id, m.metric_name)
            m.device_id, m.metric_name, m.value
          FROM metrics m
          WHERE m.metric_name IN (
            'system.cpu_utilization_percent', 'system.memory_utilization_percent',
            'system.memory_total_bytes',      'system.memory_used_bytes',
            'server.cpu_percent',             'server.memory_used_percent',
            'server.cpu_idle_percent',        'server.cpu_user_percent', 'server.cpu_system_percent',
            'server.memory_total_kb',         'server.memory_available_kb'
          )
            AND m.ts >= NOW() - INTERVAL '2 hours'
          ORDER BY m.device_id, m.metric_name, m.ts DESC
        ),
        health AS (
          SELECT
            device_id,
            COALESCE(
              cpu_util,
              cpu_direct,
              CASE WHEN cpu_idle IS NOT NULL THEN 100 - cpu_idle END,
              CASE WHEN cpu_user IS NOT NULL OR cpu_sys IS NOT NULL
                   THEN COALESCE(cpu_user, 0) + COALESCE(cpu_sys, 0) END
            ) AS cpu_pct,
            COALESCE(
              mem_util,
              mem_used_pct,
              CASE WHEN mem_total_b > 0 THEN mem_used_b / mem_total_b * 100 END,
              CASE WHEN mem_total_kb > 0 THEN (mem_total_kb - mem_avail_kb) / mem_total_kb * 100 END
            ) AS ram_pct,
            -- Total installed RAM in GB: system.memory_total_bytes (gNMI) ÷ 1024³,
            -- else server.memory_total_kb (UCD-SNMP) ÷ 1024².
            COALESCE(
              CASE WHEN mem_total_b  > 0 THEN mem_total_b  / 1073741824.0 END,
              CASE WHEN mem_total_kb > 0 THEN mem_total_kb / 1048576.0 END
            ) AS ram_gb
          FROM (
            SELECT
              device_id,
              MAX(value) FILTER (WHERE metric_name = 'system.cpu_utilization_percent')    AS cpu_util,
              MAX(value) FILTER (WHERE metric_name = 'server.cpu_percent')                AS cpu_direct,
              MAX(value) FILTER (WHERE metric_name = 'server.memory_used_percent')        AS mem_used_pct,
              MAX(value) FILTER (WHERE metric_name = 'server.cpu_idle_percent')           AS cpu_idle,
              MAX(value) FILTER (WHERE metric_name = 'server.cpu_user_percent')           AS cpu_user,
              MAX(value) FILTER (WHERE metric_name = 'server.cpu_system_percent')         AS cpu_sys,
              MAX(value) FILTER (WHERE metric_name = 'system.memory_utilization_percent') AS mem_util,
              MAX(value) FILTER (WHERE metric_name = 'system.memory_total_bytes')         AS mem_total_b,
              MAX(value) FILTER (WHERE metric_name = 'system.memory_used_bytes')          AS mem_used_b,
              MAX(value) FILTER (WHERE metric_name = 'server.memory_total_kb')            AS mem_total_kb,
              MAX(value) FILTER (WHERE metric_name = 'server.memory_available_kb')        AS mem_avail_kb
            FROM latest_health
            GROUP BY device_id
          ) agg
        )
        SELECT
          d.id::text,
          d.org_id,
          d.datacenter_id,
          d.device_id::text       AS monitoring_device_id,
          d.hostname,
          d.device_type,
          d.vendor,
          d.model,
          d.serial_number,
          d.datacenter_name,
          d.room,
          d.rack,
          d.rack_unit,
          d.rack_unit_height,
          d.status,
          d.specs,
          d.cpu_cores,
          COALESCE(ROUND(h.ram_gb::numeric, 1)::double precision, d.ram_gb) AS ram_gb,
          d.storage_gb,
          d.power_capacity_w,
          d.port_count,
          ROUND(LEAST(100, GREATEST(0, COALESCE(h.cpu_pct, d.cpu_used_pct)))::numeric, 1)::double precision AS cpu_used_pct,
          ROUND(LEAST(100, GREATEST(0, COALESCE(h.ram_pct, d.ram_used_pct)))::numeric, 1)::double precision AS ram_used_pct,
          d.storage_used_gb,
          d.power_draw_w,
          d.notes,
          d.created_at,
          d.updated_at,
          -- Live monitoring status (if linked)
          CASE
            WHEN md.last_seen_at >= NOW() - INTERVAL '300 seconds' THEN 'online'
            WHEN md.last_seen_at IS NOT NULL                        THEN 'offline'
            ELSE NULL
          END AS monitoring_status,
          md.hostname AS monitoring_hostname
        FROM dc_inventory_devices d
        LEFT JOIN devices md ON md.id = d.device_id
        LEFT JOIN health  h  ON h.device_id = d.device_id
        WHERE ${conditions.join(' AND ')}
        ORDER BY d.datacenter_name NULLS LAST, d.rack NULLS LAST, d.rack_unit NULLS LAST, d.hostname
        LIMIT $${i}
      `, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Create device ──────────────────────────────────────────────────────────
  router.post('/devices', async (req, res) => {
    try {
      const {
        org_id = 'default', datacenter_id = 'default',
        device_id,
        hostname, device_type = 'server',
        vendor, model, serial_number,
        datacenter_name, room, rack, rack_unit, rack_unit_height = 1,
        status = 'idle',
        specs = {},
        cpu_cores, ram_gb, storage_gb, power_capacity_w, port_count,
        cpu_used_pct, ram_used_pct, storage_used_gb, power_draw_w,
        notes,
      } = req.body

      if (!hostname) {
        return res.status(400).json({ success: false, error: 'hostname is required' })
      }

      const { rows } = await dbPool.query(`
        INSERT INTO dc_inventory_devices (
          org_id, datacenter_id, device_id,
          hostname, device_type, vendor, model, serial_number,
          datacenter_name, room, rack, rack_unit, rack_unit_height,
          status, specs,
          cpu_cores, ram_gb, storage_gb, power_capacity_w, port_count,
          cpu_used_pct, ram_used_pct, storage_used_gb, power_draw_w,
          notes
        ) VALUES (
          $1,$2,$3::uuid,
          $4,$5,$6,$7,$8,
          $9,$10,$11,$12,$13,
          $14,$15,
          $16,$17,$18,$19,$20,
          $21,$22,$23,$24,
          $25
        ) RETURNING *
      `, [
        org_id, datacenter_id, device_id || null,
        hostname, device_type, vendor || null, model || null, serial_number || null,
        datacenter_name || null, room || null, rack || null, rack_unit || null, rack_unit_height,
        status, JSON.stringify(specs),
        cpu_cores || null, ram_gb || null, storage_gb || null, power_capacity_w || null, port_count || null,
        cpu_used_pct || null, ram_used_pct || null, storage_used_gb || null, power_draw_w || null,
        notes || null,
      ])

      res.status(201).json({ success: true, data: rows[0] })
    } catch (error: any) {
      if (error.code === '23505') {
        return res.status(409).json({ success: false, error: 'A device with this hostname already exists in this datacenter' })
      }
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Get single device ──────────────────────────────────────────────────────
  router.get('/devices/:id', async (req, res) => {
    try {
      const { rows } = await dbPool.query(`
        SELECT
          d.*,
          d.id::text          AS id,
          d.device_id::text   AS monitoring_device_id,
          CASE
            WHEN md.last_seen_at >= NOW() - INTERVAL '300 seconds' THEN 'online'
            WHEN md.last_seen_at IS NOT NULL                        THEN 'offline'
            ELSE NULL
          END AS monitoring_status
        FROM dc_inventory_devices d
        LEFT JOIN devices md ON md.id = d.device_id
        WHERE d.id = $1::uuid
      `, [req.params.id])

      if (rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Device not found' })
      }
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Update device ──────────────────────────────────────────────────────────
  router.put('/devices/:id', async (req, res) => {
    try {
      const fields: Record<string, string> = {
        hostname: 'hostname', device_type: 'device_type', vendor: 'vendor',
        model: 'model', serial_number: 'serial_number',
        datacenter_name: 'datacenter_name', room: 'room', rack: 'rack',
        rack_unit: 'rack_unit', rack_unit_height: 'rack_unit_height',
        status: 'status', notes: 'notes',
        cpu_cores: 'cpu_cores', ram_gb: 'ram_gb', storage_gb: 'storage_gb',
        power_capacity_w: 'power_capacity_w', port_count: 'port_count',
        cpu_used_pct: 'cpu_used_pct', ram_used_pct: 'ram_used_pct',
        storage_used_gb: 'storage_used_gb', power_draw_w: 'power_draw_w',
        device_id: 'device_id',
      }

      const sets: string[] = []
      const params: any[] = []
      let i = 1

      for (const [bodyKey, col] of Object.entries(fields)) {
        if (bodyKey in req.body) {
          sets.push(`${col} = $${i++}`)
          params.push(req.body[bodyKey] ?? null)
        }
      }

      if ('specs' in req.body) {
        sets.push(`specs = $${i++}`)
        params.push(JSON.stringify(req.body.specs))
      }

      if (sets.length === 0) {
        return res.status(400).json({ success: false, error: 'No fields to update' })
      }

      sets.push('updated_at = NOW()')
      params.push(req.params.id)

      const { rows } = await dbPool.query(
        `UPDATE dc_inventory_devices SET ${sets.join(', ')} WHERE id = $${i}::uuid RETURNING *`,
        params
      )

      if (rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Device not found' })
      }
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Delete device ──────────────────────────────────────────────────────────
  router.delete('/devices/:id', async (req, res) => {
    try {
      const { rowCount } = await dbPool.query(
        'DELETE FROM dc_inventory_devices WHERE id = $1::uuid',
        [req.params.id]
      )
      if (rowCount === 0) {
        return res.status(404).json({ success: false, error: 'Device not found' })
      }
      res.json({ success: true })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── List links ─────────────────────────────────────────────────────────────
  router.get('/links', async (req, res) => {
    try {
      const { org_id = 'default', datacenter_id, device_id, status } = req.query
      const conditions: string[] = ['l.org_id = $1']
      const params: any[] = [org_id]
      let i = 2

      if (datacenter_id) { conditions.push(`l.datacenter_id = $${i++}`); params.push(datacenter_id) }
      if (status)        { conditions.push(`l.status = $${i++}`);        params.push(status) }
      if (device_id) {
        conditions.push(`(l.src_device_id = $${i}::uuid OR l.dst_device_id = $${i}::uuid)`)
        params.push(device_id); i++
      }

      const { rows } = await dbPool.query(`
        SELECT
          l.id::text,
          l.org_id,
          l.datacenter_id,
          l.src_device_id::text,
          l.dst_device_id::text,
          l.src_port,
          l.dst_port,
          l.link_type,
          l.speed_mbps,
          l.status,
          l.notes,
          l.created_at,
          l.updated_at,
          sd.hostname AS src_hostname,
          sd.device_type AS src_device_type,
          dd.hostname AS dst_hostname,
          dd.device_type AS dst_device_type
        FROM dc_inventory_links l
        JOIN dc_inventory_devices sd ON sd.id = l.src_device_id
        JOIN dc_inventory_devices dd ON dd.id = l.dst_device_id
        WHERE ${conditions.join(' AND ')}
        ORDER BY sd.hostname, dd.hostname
      `, params)

      res.json({ success: true, data: rows, count: rows.length })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Create link ────────────────────────────────────────────────────────────
  router.post('/links', async (req, res) => {
    try {
      const {
        org_id = 'default', datacenter_id = 'default',
        src_device_id, dst_device_id,
        src_port, dst_port,
        link_type = 'ethernet', speed_mbps,
        status = 'active', notes,
      } = req.body

      if (!src_device_id || !dst_device_id) {
        return res.status(400).json({ success: false, error: 'src_device_id and dst_device_id are required' })
      }

      const { rows } = await dbPool.query(`
        INSERT INTO dc_inventory_links (
          org_id, datacenter_id,
          src_device_id, dst_device_id,
          src_port, dst_port,
          link_type, speed_mbps, status, notes
        ) VALUES ($1,$2,$3::uuid,$4::uuid,$5,$6,$7,$8,$9,$10)
        RETURNING *
      `, [
        org_id, datacenter_id,
        src_device_id, dst_device_id,
        src_port || null, dst_port || null,
        link_type, speed_mbps || null, status, notes || null,
      ])

      res.status(201).json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Update link ────────────────────────────────────────────────────────────
  router.put('/links/:id', async (req, res) => {
    try {
      const allowed = ['src_port', 'dst_port', 'link_type', 'speed_mbps', 'status', 'notes']
      const sets: string[] = []
      const params: any[] = []
      let i = 1

      for (const key of allowed) {
        if (key in req.body) {
          sets.push(`${key} = $${i++}`)
          params.push(req.body[key] ?? null)
        }
      }

      if (sets.length === 0) {
        return res.status(400).json({ success: false, error: 'No fields to update' })
      }

      sets.push('updated_at = NOW()')
      params.push(req.params.id)

      const { rows } = await dbPool.query(
        `UPDATE dc_inventory_links SET ${sets.join(', ')} WHERE id = $${i}::uuid RETURNING *`,
        params
      )

      if (rows.length === 0) {
        return res.status(404).json({ success: false, error: 'Link not found' })
      }
      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Delete link ────────────────────────────────────────────────────────────
  router.delete('/links/:id', async (req, res) => {
    try {
      const { rowCount } = await dbPool.query(
        'DELETE FROM dc_inventory_links WHERE id = $1::uuid',
        [req.params.id]
      )
      if (rowCount === 0) {
        return res.status(404).json({ success: false, error: 'Link not found' })
      }
      res.json({ success: true })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // ── Sync from monitoring ───────────────────────────────────────────────────
  // Mirrors every row from the monitoring `devices` + `topology_links` tables
  // into the inventory tables so it shows up in the Inventory UI. Returns counts
  // of inserted/updated rows.
  //
  // Everything is normalised onto the 'default' org/datacenter: the Inventory UI
  // has no org selector and queries org_id = 'default' exclusively (and manual
  // inventory devices default to 'default' too). The monitoring `devices` table
  // carries whatever org_id/datacenter_id the collector ingested under — usually
  // NOT 'default' — so copying those through stored rows the UI could never see.
  router.post('/sync', async (req, res) => {
    const client = await dbPool.connect()
    try {
      await client.query('BEGIN')

      // 0. Drop stale monitoring-synced rows left under a non-default org/dc by
      //    earlier builds (they're invisible to the UI, and their duplicate
      //    device_id would make the device/link joins below match twice and trip
      //    "ON CONFLICT ... cannot affect row a second time"). Links CASCADE on
      //    device delete. Manually-created rows (device_id IS NULL) are untouched.
      await client.query(`
        DELETE FROM dc_inventory_devices
        WHERE device_id IS NOT NULL
          AND (org_id <> 'default' OR datacenter_id <> 'default')
      `)

      // 1. Devices. DISTINCT ON (hostname) collapses any same-hostname devices
      //    (different network/floor) to one row — the conflict target is
      //    (org_id, datacenter_id, hostname), so duplicates in a single INSERT
      //    would otherwise error. The most-recently-seen device wins.
      const devResult = await client.query(`
        INSERT INTO dc_inventory_devices (
          org_id, datacenter_id,
          device_id,
          hostname, device_type,
          vendor, model,
          datacenter_name, room,
          rack, rack_unit,
          status,
          power_capacity_w,
          notes
        )
        SELECT DISTINCT ON (d.hostname)
          'default',
          'default',
          d.id,
          d.hostname,
          d.device_type,
          d.vendor,
          d.model_name,
          d.datacenter,
          d.room,
          d.rack_num::TEXT,
          d.rack_unit,
          'in_use',
          d.power_draw_w,
          d.sys_description
        FROM devices d
        ORDER BY d.hostname, d.last_seen_at DESC NULLS LAST, d.id
        ON CONFLICT (org_id, datacenter_id, hostname) DO UPDATE SET
          device_id         = EXCLUDED.device_id,
          device_type       = EXCLUDED.device_type,
          vendor            = COALESCE(EXCLUDED.vendor,           dc_inventory_devices.vendor),
          model             = COALESCE(EXCLUDED.model,            dc_inventory_devices.model),
          datacenter_name   = COALESCE(EXCLUDED.datacenter_name,  dc_inventory_devices.datacenter_name),
          room              = COALESCE(EXCLUDED.room,             dc_inventory_devices.room),
          rack              = COALESCE(EXCLUDED.rack,             dc_inventory_devices.rack),
          rack_unit         = COALESCE(EXCLUDED.rack_unit,        dc_inventory_devices.rack_unit),
          power_capacity_w  = COALESCE(EXCLUDED.power_capacity_w, dc_inventory_devices.power_capacity_w),
          status            = CASE
                                WHEN dc_inventory_devices.status = 'decommissioned' THEN 'decommissioned'
                                ELSE 'in_use'
                              END,
          updated_at        = NOW()
      `)

      // 2. Links. DISTINCT ON (tl.id) guarantees one row per topology link even
      //    if a device_id maps to more than one inventory row, so the ON CONFLICT
      //    (topology_link_id) never sees the same key twice in one statement.
      const linkResult = await client.query(`
        INSERT INTO dc_inventory_links (
          org_id, datacenter_id,
          topology_link_id,
          src_device_id, dst_device_id,
          src_port, dst_port,
          link_type, speed_mbps,
          status
        )
        SELECT DISTINCT ON (tl.id)
          inv_src.org_id,
          inv_src.datacenter_id,
          tl.id,
          inv_src.id,
          inv_dst.id,
          NULLIF(tl.src_port_name, ''),
          NULLIF(tl.dst_port_name, ''),
          COALESCE(tl.link_type, 'ethernet'),
          tl.link_speed_mbps,
          CASE WHEN tl.is_active THEN 'active' ELSE 'disconnected' END
        FROM topology_links tl
        JOIN dc_inventory_devices inv_src ON inv_src.device_id = tl.src_device_id
        JOIN dc_inventory_devices inv_dst ON inv_dst.device_id = tl.dst_device_id
        ORDER BY tl.id
        ON CONFLICT (topology_link_id) WHERE topology_link_id IS NOT NULL
        DO UPDATE SET
          status     = EXCLUDED.status,
          speed_mbps = COALESCE(EXCLUDED.speed_mbps, dc_inventory_links.speed_mbps),
          updated_at = NOW()
      `)

      await client.query('COMMIT')
      res.json({
        success: true,
        data: {
          devices_synced: devResult.rowCount,
          links_synced:   linkResult.rowCount,
        },
      })
    } catch (error: any) {
      await client.query('ROLLBACK').catch(() => {})
      res.status(500).json({ success: false, error: error.message })
    } finally {
      client.release()
    }
  })

  return router
}
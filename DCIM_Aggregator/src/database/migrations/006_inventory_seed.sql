-- 006_inventory_seed.sql
-- Bootstraps the inventory tables from the live monitoring data:
--   1. Adds topology_link_id column to dc_inventory_links so each link can be
--      traced back to its originating topology_links row (used for upserts).
--   2. Adds unique indexes needed for ON CONFLICT upserts.
--   3. Seeds dc_inventory_devices from every row in the `devices` table,
--      marking each as status = 'in_use' (they are already deployed).
--   4. Seeds dc_inventory_links from every row in topology_links, joining
--      through dc_inventory_devices to resolve the inventory IDs.
--
-- Idempotent: safe to re-run. ON CONFLICT clauses make every INSERT a no-op
-- if the row already exists; the column/index additions use IF NOT EXISTS.

-- ── Schema additions ───────────────────────────────────────────────────────

ALTER TABLE dc_inventory_links
  ADD COLUMN IF NOT EXISTS topology_link_id UUID;

-- Unique index for upsert on the monitoring-originated link reference.
CREATE UNIQUE INDEX IF NOT EXISTS uix_inv_link_topology_id
  ON dc_inventory_links (topology_link_id)
  WHERE topology_link_id IS NOT NULL;

-- ── Seed: devices ──────────────────────────────────────────────────────────
-- Copy every monitored device into the inventory.
-- • device_id  links back to devices.id so the analytics panel can pull metrics.
-- • status     = 'in_use'  (already deployed and being monitored).
-- • rack       stores rack_num cast to text  (inventory rack column is TEXT).
-- • power_capacity_w uses power_draw_w as the best available approximation.
-- ON CONFLICT on the existing (org_id, datacenter_id, hostname) unique index:
--   update the monitoring link and physical fields, but never downgrade status
--   from 'decommissioned'.

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
SELECT DISTINCT ON (d.org_id, d.datacenter_id, d.hostname)
  d.org_id,
  d.datacenter_id,
  d.id                                              AS device_id,
  d.hostname,
  d.device_type,
  d.vendor,
  d.model_name                                      AS model,
  d.datacenter                                      AS datacenter_name,
  d.room,
  d.rack_num::TEXT                                  AS rack,
  d.rack_unit,
  'in_use'                                          AS status,
  d.power_draw_w                                    AS power_capacity_w,
  d.sys_description                                 AS notes
FROM devices d
-- DISTINCT ON keeps one row per conflict target: a single INSERT may not
-- propose two rows for the same (org_id, datacenter_id, hostname) — Postgres
-- rejects that with "ON CONFLICT DO UPDATE command cannot affect row a second
-- time". Prefer the most recently seen device.
ORDER BY d.org_id, d.datacenter_id, d.hostname,
         d.last_seen_at DESC NULLS LAST, d.updated_at DESC
ON CONFLICT (org_id, datacenter_id, hostname) DO UPDATE SET
  device_id         = EXCLUDED.device_id,
  device_type       = EXCLUDED.device_type,
  vendor            = COALESCE(EXCLUDED.vendor,            dc_inventory_devices.vendor),
  model             = COALESCE(EXCLUDED.model,             dc_inventory_devices.model),
  datacenter_name   = COALESCE(EXCLUDED.datacenter_name,   dc_inventory_devices.datacenter_name),
  room              = COALESCE(EXCLUDED.room,              dc_inventory_devices.room),
  rack              = COALESCE(EXCLUDED.rack,              dc_inventory_devices.rack),
  rack_unit         = COALESCE(EXCLUDED.rack_unit,         dc_inventory_devices.rack_unit),
  power_capacity_w  = COALESCE(EXCLUDED.power_capacity_w,  dc_inventory_devices.power_capacity_w),
  status            = CASE
                        WHEN dc_inventory_devices.status = 'decommissioned' THEN 'decommissioned'
                        ELSE 'in_use'
                      END,
  updated_at        = NOW();

-- ── Seed: links ───────────────────────────────────────────────────────────
-- Copy every topology_link into dc_inventory_links, resolving the inventory
-- device IDs by joining on the device_id (monitoring → inventory) reference
-- established in the devices seed above.
-- Uses topology_link_id as the conflict target so re-runs are safe.

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
  tl.id                                                     AS topology_link_id,
  inv_src.id                                                AS src_device_id,
  inv_dst.id                                                AS dst_device_id,
  NULLIF(tl.src_port_name, '')                              AS src_port,
  NULLIF(tl.dst_port_name, '')                              AS dst_port,
  COALESCE(tl.link_type, 'ethernet')                        AS link_type,
  tl.link_speed_mbps                                        AS speed_mbps,
  CASE WHEN tl.is_active THEN 'active' ELSE 'disconnected' END AS status
FROM topology_links tl
JOIN dc_inventory_devices inv_src ON inv_src.device_id = tl.src_device_id
JOIN dc_inventory_devices inv_dst ON inv_dst.device_id = tl.dst_device_id
-- DISTINCT ON (tl.id): if a device_id ever maps to more than one inventory
-- row, the joins fan out and one INSERT would hit the same topology_link_id
-- twice (same 21000 error as above). Keep one row per link.
ORDER BY tl.id
ON CONFLICT (topology_link_id) WHERE topology_link_id IS NOT NULL
DO UPDATE SET
  status     = EXCLUDED.status,
  speed_mbps = COALESCE(EXCLUDED.speed_mbps, dc_inventory_links.speed_mbps),
  updated_at = NOW();

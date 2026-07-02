-- 005_inventory.sql
-- Datacenter physical inventory management.
-- Tracks physical hardware assets (devices + links) independent of live
-- monitoring.  A device here may or may not have a corresponding row in the
-- `devices` monitoring table (soft reference via nullable device_id).
--
-- Tables:
--   dc_inventory_devices  — one row per physical asset (server, switch, …)
--   dc_inventory_links    — physical cable / interconnect between two assets
--
-- Views:
--   dc_inventory_summary  — per-datacenter capacity & utilisation rollup
--
-- Idempotent: safe to re-run against an existing schema.

-- ════════════════════════════════════════════════════════════════════════════
-- Table: dc_inventory_devices
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dc_inventory_devices (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id            TEXT         NOT NULL DEFAULT 'default',
    datacenter_id     TEXT         NOT NULL DEFAULT 'default',

    -- Soft reference to the live-monitored device; NULL when the asset is not
    -- yet registered in the monitoring system.
    device_id         UUID,

    hostname          TEXT         NOT NULL,
    device_type       TEXT         NOT NULL DEFAULT 'server',
    -- 'server' | 'switch' | 'router' | 'firewall' | 'storage' | 'pdu' | 'ups'
    -- | 'crac' | 'ap' | 'workstation' | 'camera' | 'iot' | 'custom'

    vendor            TEXT,
    model             TEXT,
    serial_number     TEXT,

    -- Physical location
    datacenter_name   TEXT,
    room              TEXT,
    rack              TEXT,
    rack_unit         SMALLINT,
    rack_unit_height  SMALLINT     NOT NULL DEFAULT 1,

    -- Lifecycle status
    -- 'idle' | 'in_use' | 'maintenance' | 'decommissioned'
    status            TEXT         NOT NULL DEFAULT 'idle',

    -- Free-form spec bag mirroring the TopologyEditor spec form.
    -- Keys: cpu_cores, ram_gb, storage_gb, power_capacity_w, port_count,
    --       model, serial_number, total_capacity, voltage, outlet_count, …
    specs             JSONB        NOT NULL DEFAULT '{}',

    -- Denormalised capacity columns for fast aggregate queries.
    cpu_cores         INTEGER,
    ram_gb            DOUBLE PRECISION,
    storage_gb        DOUBLE PRECISION,
    power_capacity_w  INTEGER,
    port_count        INTEGER,

    -- Latest utilisation snapshot (updated by metrics sync or manual entry).
    cpu_used_pct      DOUBLE PRECISION,
    ram_used_pct      DOUBLE PRECISION,
    storage_used_gb   DOUBLE PRECISION,
    power_draw_w      DOUBLE PRECISION,

    notes             TEXT,

    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uix_inv_device_hostname
    ON dc_inventory_devices (org_id, datacenter_id, hostname);

CREATE INDEX IF NOT EXISTS idx_inv_device_org_dc     ON dc_inventory_devices (org_id, datacenter_id);
CREATE INDEX IF NOT EXISTS idx_inv_device_type        ON dc_inventory_devices (device_type);
CREATE INDEX IF NOT EXISTS idx_inv_device_status      ON dc_inventory_devices (status);
CREATE INDEX IF NOT EXISTS idx_inv_device_monitoring  ON dc_inventory_devices (device_id)
    WHERE device_id IS NOT NULL;

-- ════════════════════════════════════════════════════════════════════════════
-- Table: dc_inventory_links
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS dc_inventory_links (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id          TEXT         NOT NULL DEFAULT 'default',
    datacenter_id   TEXT         NOT NULL DEFAULT 'default',

    src_device_id   UUID         NOT NULL REFERENCES dc_inventory_devices(id) ON DELETE CASCADE,
    dst_device_id   UUID         NOT NULL REFERENCES dc_inventory_devices(id) ON DELETE CASCADE,

    src_port        TEXT,
    dst_port        TEXT,

    -- 'ethernet' | 'fiber' | 'dac' | 'infiniband' | 'usb' | 'other'
    link_type       TEXT         NOT NULL DEFAULT 'ethernet',
    speed_mbps      INTEGER,

    -- 'active' | 'disconnected'
    status          TEXT         NOT NULL DEFAULT 'active',

    notes           TEXT,

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inv_link_src ON dc_inventory_links (src_device_id);
CREATE INDEX IF NOT EXISTS idx_inv_link_dst ON dc_inventory_links (dst_device_id);

-- ════════════════════════════════════════════════════════════════════════════
-- View: dc_inventory_summary
-- Per-datacenter capacity & availability rollup used by the summary cards.
-- ════════════════════════════════════════════════════════════════════════════

DROP VIEW IF EXISTS dc_inventory_summary CASCADE;
CREATE OR REPLACE VIEW dc_inventory_summary AS
SELECT
    org_id,
    datacenter_id,
    COUNT(*)::int                                                                 AS total,
    COUNT(CASE WHEN status = 'idle'          THEN 1 END)::int                    AS idle,
    COUNT(CASE WHEN status = 'in_use'        THEN 1 END)::int                    AS in_use,
    COUNT(CASE WHEN status = 'maintenance'   THEN 1 END)::int                    AS maintenance,
    COUNT(CASE WHEN status = 'decommissioned' THEN 1 END)::int                   AS decommissioned,

    COALESCE(SUM(cpu_cores),   0)::int                                           AS total_cpu_cores,
    COALESCE(SUM(ram_gb),      0)                                                AS total_ram_gb,
    COALESCE(SUM(storage_gb),  0)                                                AS total_storage_gb,
    COALESCE(SUM(power_capacity_w), 0)::int                                      AS total_power_w,

    COALESCE(SUM(CASE WHEN status = 'idle' THEN cpu_cores     ELSE 0 END), 0)::int AS available_cpu_cores,
    COALESCE(SUM(CASE WHEN status = 'idle' THEN ram_gb        ELSE 0 END), 0)       AS available_ram_gb,
    COALESCE(SUM(CASE WHEN status = 'idle' THEN storage_gb    ELSE 0 END), 0)       AS available_storage_gb,
    COALESCE(SUM(CASE WHEN status = 'idle' THEN power_capacity_w ELSE 0 END), 0)::int AS available_power_w,

    -- Used = total allocated to in_use devices (not utilisation %).
    COALESCE(SUM(CASE WHEN status = 'in_use' THEN cpu_cores   ELSE 0 END), 0)::int AS used_cpu_cores,
    COALESCE(SUM(CASE WHEN status = 'in_use' THEN ram_gb      ELSE 0 END), 0)       AS used_ram_gb,
    COALESCE(SUM(CASE WHEN status = 'in_use' THEN storage_gb  ELSE 0 END), 0)       AS used_storage_gb,

    -- Storage remaining across ALL devices (total capacity minus used_gb).
    COALESCE(SUM(storage_gb) - SUM(COALESCE(storage_used_gb, 0)), 0)             AS storage_remaining_gb

FROM dc_inventory_devices
GROUP BY org_id, datacenter_id;

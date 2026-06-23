-- 009_power_state_thresholds.sql
-- Two persistent stores for values that must NOT live in the time-series
-- metrics table (which expires under retention):
--
--   1. devices.power_state — chassis power is a fixed state, not a metric. EDR
--      polls it via Redfish and writes it here so the current value survives
--      retention and is read straight off the device row.
--
--   2. device_thresholds — per-device alert thresholds (HighCPU, HighMemory, …)
--      the user edits in the UI. They are config, not telemetry. EDR fetches
--      them from the simulator SNMP management plane (UDP 1161) and upserts here.

ALTER TABLE devices ADD COLUMN IF NOT EXISTS power_state SMALLINT; -- 1=On, 2=Off, NULL=unknown

-- Editable device-config columns the UI device page reads/writes (SNMP SET surface).
-- sys_contact (1.3.6.1.2.1.1.4.0) and floor (asset/location label, distinct from the
-- floor_id tenant key) were never added to the base schema — add them here so
-- GET/PUT /agents/:id/config can select them.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS sys_contact TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS floor       TEXT;

CREATE TABLE IF NOT EXISTS device_thresholds (
                                                 device_id   UUID              NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    rule        TEXT              NOT NULL,       -- HighCPU, HighMemory, HighTemperature, …
    value       DOUBLE PRECISION  NOT NULL,       -- some thresholds are fractional (airflow m/s, dewpoint °C)
    updated_at  TIMESTAMPTZ       NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, rule)
    );

-- Widen an already-created INTEGER column (idempotent: a no-op once it's DOUBLE PRECISION).
ALTER TABLE device_thresholds ALTER COLUMN value TYPE DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS ix_device_thresholds_device ON device_thresholds (device_id);

-- Migration 011: Align snmp_metrics columns exactly with Server schema.
-- Server uses: device_host TEXT, value REAL, metadata TEXT

-- 1. Rename device_ip → device_host
ALTER TABLE snmp_metrics RENAME COLUMN device_ip TO device_host;

-- 2. Add value DOUBLE PRECISION, populate from metric_value, then drop metric_value
ALTER TABLE snmp_metrics ADD COLUMN value DOUBLE PRECISION;
UPDATE snmp_metrics
   SET value = metric_value::DOUBLE PRECISION
 WHERE metric_value IS NOT NULL
   AND metric_value ~ '^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$';
ALTER TABLE snmp_metrics DROP COLUMN metric_value;

-- 3. Change metadata from JSONB to TEXT
ALTER TABLE snmp_metrics ALTER COLUMN metadata TYPE TEXT USING metadata::TEXT;

-- 4. Recreate unique index using updated column name device_host
DROP INDEX IF EXISTS idx_snmp_metrics_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_snmp_metrics_unique
    ON snmp_metrics (server_id, agent_id, device_host, metric_name, timestamp);

-- 5. Add device_host index to match Server's idx_snmp_device_time
CREATE INDEX IF NOT EXISTS idx_snmp_device_time ON snmp_metrics (device_host, timestamp);

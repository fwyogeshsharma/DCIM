-- Migration 010: Rename legacy snmp_metrics columns for existing installs.
-- Fresh installs get the correct names from 001; this handles DBs created
-- before the rename so the migration set stays fully idempotent.

DO $$
BEGIN
  -- device_ip -> device_host
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'snmp_metrics' AND column_name = 'device_ip'
  ) THEN
    ALTER TABLE snmp_metrics RENAME COLUMN device_ip TO device_host;
  END IF;

  -- metric_value -> value (VARCHAR -> DOUBLE PRECISION)
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'snmp_metrics' AND column_name = 'metric_value'
  ) THEN
    ALTER TABLE snmp_metrics RENAME COLUMN metric_value TO value;
    ALTER TABLE snmp_metrics ALTER COLUMN value TYPE DOUBLE PRECISION USING value::double precision;
  END IF;
END $$;

-- Add columns introduced after initial schema (idempotent)
ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS value_type TEXT;
ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS metadata TEXT;

-- Fix unique index: must be on device_host, not device_name or device_ip
DROP INDEX IF EXISTS idx_snmp_metrics_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_snmp_metrics_unique
  ON snmp_metrics (server_id, agent_id, device_host, metric_name, timestamp);

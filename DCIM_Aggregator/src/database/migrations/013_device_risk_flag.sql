-- Migration 013: device failure-risk flag
--
-- Flags assets whose live metrics have crossed their configured device_thresholds
-- (HighCPU / HighMemory / HighTemperature in v1) so the topology 2D/3D views and
-- the inventory table can surface devices that are "likely to fail" BEFORE they
-- actually go down. The flag is set/cleared at metric-ingest time with hysteresis
-- (see api/routes/ingest.ts → evaluateDeviceRisk). Distinct from `is_reachable`
-- (already-down) and from `events` alerts (historical record).

ALTER TABLE devices
  ADD COLUMN IF NOT EXISTS at_risk         BOOLEAN      NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS risk_reason     TEXT,
  ADD COLUMN IF NOT EXISTS risk_updated_at TIMESTAMPTZ;

-- Partial index: the topology/inventory reads only ever care about the flagged few.
CREATE INDEX IF NOT EXISTS ix_devices_at_risk ON devices (at_risk) WHERE at_risk;

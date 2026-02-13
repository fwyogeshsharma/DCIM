-- Migration: 002_server_tracking
-- Description: Add server tracking and alert deduplication features
-- Date: 2026-02-12

-- =====================================================
-- Create servers table for multi-server deployments
-- =====================================================
CREATE TABLE IF NOT EXISTS servers (
    id              SERIAL PRIMARY KEY,
    server_id       TEXT UNIQUE NOT NULL,
    server_name     TEXT NOT NULL,
    location        TEXT,
    environment     TEXT,
    hostname        TEXT,
    version         TEXT,
    status          TEXT DEFAULT 'active',
    last_seen       TIMESTAMP,
    first_seen      TIMESTAMP DEFAULT NOW(),
    metadata        TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_servers_server_id ON servers(server_id);
CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status);

-- =====================================================
-- Add server_id to existing tables
-- =====================================================
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE agent_status_history ADD COLUMN IF NOT EXISTS server_id TEXT;

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'aggregated_metrics') THEN
        ALTER TABLE aggregated_metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
    END IF;
END $$;

-- =====================================================
-- Add alert deduplication columns
-- =====================================================
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrence_count INTEGER DEFAULT 1;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- =====================================================
-- Backfill existing data with legacy server ID
-- =====================================================
UPDATE metrics SET server_id = 'legacy-server' WHERE server_id IS NULL;
UPDATE alerts SET server_id = 'legacy-server' WHERE server_id IS NULL;
UPDATE snmp_metrics SET server_id = 'legacy-server' WHERE server_id IS NULL;
UPDATE agent_status_history SET server_id = 'legacy-server' WHERE server_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'aggregated_metrics') THEN
        UPDATE aggregated_metrics SET server_id = 'legacy-server' WHERE server_id IS NULL;
    END IF;
END $$;

-- Backfill alert timestamps from existing timestamp field
UPDATE alerts
SET first_seen = COALESCE(first_seen, timestamp),
    last_seen = COALESCE(last_seen, timestamp),
    updated_at = COALESCE(updated_at, created_at)
WHERE first_seen IS NULL OR last_seen IS NULL OR updated_at IS NULL;

-- =====================================================
-- Make server_id NOT NULL after backfill
-- =====================================================
ALTER TABLE metrics ALTER COLUMN server_id SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN server_id SET NOT NULL;
ALTER TABLE snmp_metrics ALTER COLUMN server_id SET NOT NULL;
ALTER TABLE agent_status_history ALTER COLUMN server_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'aggregated_metrics') THEN
        ALTER TABLE aggregated_metrics ALTER COLUMN server_id SET NOT NULL;
    END IF;
END $$;

-- =====================================================
-- Create indexes for performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_metrics_server ON metrics(server_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_server ON alerts(server_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_server ON snmp_metrics(server_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_status_history_server ON agent_status_history(server_id, timestamp);

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'aggregated_metrics') THEN
        CREATE INDEX IF NOT EXISTS idx_aggregated_metrics_server ON aggregated_metrics(server_id, timestamp);
    END IF;
END $$;

-- Alert deduplication index (for fast duplicate detection)
CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(agent_id, metric_type, severity, resolved);

-- ⚠️ DEPRECATED: This manual migration script is no longer needed!
--
-- DCIM Server now includes AUTOMATIC MIGRATIONS that run on startup.
-- See MIGRATIONS.md for details.
--
-- To use automatic migrations:
-- 1. Just run: .\dcim-server.exe
-- 2. Migrations run automatically from migrations/ folder
-- 3. No manual SQL needed!
--
-- Database Migration Script (Manual - OLD WAY)
-- Adds server tracking, alert deduplication, and alert resolution features
-- Date: 2026-02-12

-- =====================================================
-- STEP 1: Create servers table
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
-- STEP 2: Add server_id to existing tables
-- =====================================================

-- Add server_id to metrics table
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS server_id TEXT;

-- Add server_id to alerts table
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS server_id TEXT;

-- Add server_id to snmp_metrics table
ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS server_id TEXT;

-- Add server_id to agent_status_history table (if exists)
ALTER TABLE agent_status_history ADD COLUMN IF NOT EXISTS server_id TEXT;

-- Add server_id to aggregated_metrics table (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'aggregated_metrics') THEN
        ALTER TABLE aggregated_metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
    END IF;
END $$;

-- =====================================================
-- STEP 3: Add alert deduplication columns
-- =====================================================

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrence_count INTEGER DEFAULT 1;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;

-- =====================================================
-- STEP 4: Add alert resolution columns
-- =====================================================

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_by TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_action TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_notes TEXT;

-- =====================================================
-- STEP 5: Backfill existing data
-- =====================================================

-- Backfill server_id with default value for existing records
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

-- Backfill alert timestamps
UPDATE alerts
SET first_seen = timestamp,
    last_seen = timestamp
WHERE first_seen IS NULL;

-- =====================================================
-- STEP 6: Make server_id NOT NULL (after backfill)
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
-- STEP 7: Create indexes for performance
-- =====================================================

-- Server ID indexes
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

-- Alert resolution indexes
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved, resolved_at);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved_by ON alerts(resolved_by);

-- =====================================================
-- STEP 8: Verification queries
-- =====================================================

-- Verify servers table
SELECT 'Servers table created:' as check_name,
       CASE WHEN EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'servers')
       THEN 'YES' ELSE 'NO' END as result;

-- Verify server_id columns added
SELECT 'metrics.server_id exists:' as check_name,
       CASE WHEN EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'metrics' AND column_name = 'server_id')
       THEN 'YES' ELSE 'NO' END as result
UNION ALL
SELECT 'alerts.server_id exists:',
       CASE WHEN EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'alerts' AND column_name = 'server_id')
       THEN 'YES' ELSE 'NO' END
UNION ALL
SELECT 'alerts.occurrence_count exists:',
       CASE WHEN EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'alerts' AND column_name = 'occurrence_count')
       THEN 'YES' ELSE 'NO' END
UNION ALL
SELECT 'alerts.resolved_by exists:',
       CASE WHEN EXISTS (SELECT FROM information_schema.columns WHERE table_name = 'alerts' AND column_name = 'resolved_by')
       THEN 'YES' ELSE 'NO' END;

-- Count existing records
SELECT 'Total metrics:' as metric, COUNT(*) as count FROM metrics
UNION ALL
SELECT 'Total alerts:', COUNT(*) FROM alerts
UNION ALL
SELECT 'Total servers:', COUNT(*) FROM servers;

COMMIT;

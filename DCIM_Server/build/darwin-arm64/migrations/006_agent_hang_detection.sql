-- Migration: 006_agent_hang_detection
-- Description: Add hang detection support - track response times and degraded status
-- Date: 2026-02-17

-- =====================================================
-- Add hang detection columns to agents table
-- =====================================================
DO $$
BEGIN
    -- Add last_response_time column (time between heartbeats)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'last_response_time'
    ) THEN
        ALTER TABLE agents ADD COLUMN last_response_time INTERVAL;
    END IF;

    -- Add average_response_time column (rolling average)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'avg_response_time'
    ) THEN
        ALTER TABLE agents ADD COLUMN avg_response_time INTERVAL;
    END IF;

    -- Add consecutive_slow_count column
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'consecutive_slow_count'
    ) THEN
        ALTER TABLE agents ADD COLUMN consecutive_slow_count INTEGER DEFAULT 0;
    END IF;

    -- Add previous_seen timestamp for calculating intervals
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'previous_seen'
    ) THEN
        ALTER TABLE agents ADD COLUMN previous_seen TIMESTAMP;
    END IF;
END $$;

-- =====================================================
-- Update agent status enum to include 'degraded'
-- =====================================================
-- Note: In PostgreSQL, we can't easily modify CHECK constraints
-- We'll handle status validation in application code
-- Valid statuses: online, offline, degraded, pending

-- =====================================================
-- Add comment for documentation
-- =====================================================
COMMENT ON COLUMN agents.last_response_time IS 'Time between last two heartbeats (actual vs expected)';
COMMENT ON COLUMN agents.avg_response_time IS 'Rolling average response time over last 10 heartbeats';
COMMENT ON COLUMN agents.consecutive_slow_count IS 'Count of consecutive slow responses (reset on normal response)';
COMMENT ON COLUMN agents.previous_seen IS 'Previous last_seen timestamp for calculating response intervals';

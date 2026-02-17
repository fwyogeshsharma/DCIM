-- Migration: 005_agent_shutdown_tracking
-- Description: Add agent_shutdown_events table to track graceful vs unexpected shutdowns
-- Date: 2026-02-17

-- =====================================================
-- Create agent_shutdown_events table
-- =====================================================
CREATE TABLE IF NOT EXISTS agent_shutdown_events (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    shutdown_type TEXT NOT NULL CHECK (shutdown_type IN ('graceful', 'unexpected', 'error')),
    shutdown_time TIMESTAMP NOT NULL,
    reason TEXT,
    metadata JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Create indexes for performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_shutdown_events_agent ON agent_shutdown_events(agent_id);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_server ON agent_shutdown_events(server_id);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_time ON agent_shutdown_events(shutdown_time DESC);
CREATE INDEX IF NOT EXISTS idx_shutdown_events_type ON agent_shutdown_events(shutdown_type);

-- =====================================================
-- Add comment for documentation
-- =====================================================
COMMENT ON TABLE agent_shutdown_events IS 'Tracks agent shutdown events to distinguish graceful vs unexpected shutdowns';
COMMENT ON COLUMN agent_shutdown_events.shutdown_type IS 'Type of shutdown: graceful (planned), unexpected (crash), error (with error details)';

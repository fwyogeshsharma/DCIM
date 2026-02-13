-- Migration: 004_add_agent_server_id
-- Description: Add server_id column to agents table to link agents to servers
-- Date: 2026-02-13

-- =====================================================
-- Add server_id to agents table
-- =====================================================
ALTER TABLE agents ADD COLUMN IF NOT EXISTS server_id TEXT;

-- =====================================================
-- Backfill existing agents with default server ID
-- =====================================================
UPDATE agents SET server_id = 'default-server' WHERE server_id IS NULL;

-- =====================================================
-- Make server_id NOT NULL after backfill
-- =====================================================
ALTER TABLE agents ALTER COLUMN server_id SET NOT NULL;

-- =====================================================
-- Create index for performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_agents_server ON agents(server_id);

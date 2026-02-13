-- Migration: 004_add_agent_server_id
-- Description: Add server_id column to agents table to link agents to servers
-- Date: 2026-02-13

-- =====================================================
-- Add server_id to agents table
-- =====================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'server_id'
    ) THEN
        ALTER TABLE agents ADD COLUMN server_id TEXT;
    END IF;
END $$;

-- =====================================================
-- Backfill existing agents with default server ID
-- =====================================================
UPDATE agents SET server_id = 'default-server' WHERE server_id IS NULL;

-- =====================================================
-- Make server_id NOT NULL after backfill
-- =====================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agents' AND column_name = 'server_id' AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE agents ALTER COLUMN server_id SET NOT NULL;
    END IF;
END $$;

-- =====================================================
-- Create index for performance
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_agents_server ON agents(server_id);

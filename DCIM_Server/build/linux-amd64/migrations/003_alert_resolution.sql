-- Migration: 003_alert_resolution
-- Description: Add alert resolution tracking - WHO fixed WHAT
-- Date: 2026-02-12

-- =====================================================
-- Add alert resolution columns
-- =====================================================
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_by TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_action TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_notes TEXT;

-- =====================================================
-- Create indexes for resolution queries
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_alerts_resolved_by ON alerts(resolved_by) WHERE resolved_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_resolved_at ON alerts(resolved_at) WHERE resolved_at IS NOT NULL;

-- =====================================================
-- Create composite index for resolution analytics
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_alerts_resolution_analytics ON alerts(resolved, resolved_at, resolved_by);

-- Migration 010: Add missing columns to snmp_metrics to match Server schema.
-- Server stores value_type (int/float/string indicator) and metadata (JSON).

ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS value_type TEXT;
ALTER TABLE snmp_metrics ADD COLUMN IF NOT EXISTS metadata JSONB;

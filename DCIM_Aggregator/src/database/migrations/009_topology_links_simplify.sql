-- Migration 009: Remove depth/port columns from topology_links.
-- Depth info is stored in snmp_metrics as topology_depth metric.

ALTER TABLE topology_links DROP COLUMN IF EXISTS source_depth;
ALTER TABLE topology_links DROP COLUMN IF EXISTS source_port;
ALTER TABLE topology_links DROP COLUMN IF EXISTS target_depth;
ALTER TABLE topology_links DROP COLUMN IF EXISTS target_port;

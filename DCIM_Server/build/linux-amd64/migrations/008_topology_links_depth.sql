-- Migration: 008_topology_links_depth
-- Description: Add depth and port columns to topology_links for UI hierarchy visualization
-- source_depth/target_depth: BFS level from seed (entry point = 0, server = -1)
-- source_port: local port index on source device (from LLDP suffix)
-- target_port: remote port name on target device (from LLDP)
-- Date: 2026-04-10

ALTER TABLE topology_links ADD COLUMN IF NOT EXISTS source_depth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE topology_links ADD COLUMN IF NOT EXISTS source_port  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE topology_links ADD COLUMN IF NOT EXISTS target_depth INTEGER NOT NULL DEFAULT 0;
ALTER TABLE topology_links ADD COLUMN IF NOT EXISTS target_port  TEXT    NOT NULL DEFAULT '';

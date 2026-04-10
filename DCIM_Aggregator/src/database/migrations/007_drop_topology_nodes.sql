-- Migration: 007_drop_topology_nodes
-- Description: Drop topology_nodes table — status is now computed from snmp_metrics directly
-- Date: 2026-04-10

DROP INDEX IF EXISTS idx_topology_nodes_server;
DROP INDEX IF EXISTS idx_topology_nodes_device;
DROP TABLE IF EXISTS topology_nodes;

-- Migration: 007_topology_nodes
-- Description: Add topology_nodes table to store SNMP walker discovered devices with online/offline status
-- Date: 2026-04-10

CREATE TABLE IF NOT EXISTS topology_nodes (
    id          BIGSERIAL PRIMARY KEY,
    server_id   TEXT NOT NULL,
    device_host TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'offline',
    last_seen   TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topology_nodes_device
    ON topology_nodes(server_id, device_host);

CREATE INDEX IF NOT EXISTS idx_topology_nodes_server
    ON topology_nodes(server_id, status);

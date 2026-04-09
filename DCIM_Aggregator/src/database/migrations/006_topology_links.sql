-- Migration: 006_topology_links
-- Description: Add topology_links table to store SNMP walker discovered connections
-- Date: 2026-04-09

CREATE TABLE IF NOT EXISTS topology_links (
    id          BIGSERIAL PRIMARY KEY,
    server_id   TEXT NOT NULL,
    source_ip   TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    target_ip   TEXT NOT NULL,
    target_name TEXT NOT NULL DEFAULT '',
    last_seen   TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topology_links_pair
    ON topology_links(server_id, source_ip, target_ip);

CREATE INDEX IF NOT EXISTS idx_topology_links_server
    ON topology_links(server_id, last_seen);

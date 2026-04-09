-- Migration: 005_snmp_traps
-- Description: Add snmp_traps table for SNMP trap event storage
-- Date: 2026-04-08

CREATE TABLE IF NOT EXISTS snmp_traps (
    id          BIGSERIAL PRIMARY KEY,
    server_id   TEXT NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    source_ip   TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    trap_type   TEXT NOT NULL,
    trap_oid    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    varbinds    JSONB,
    description TEXT NOT NULL DEFAULT '',
    resolved    BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at TIMESTAMP,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Timestamp is pre-truncated to seconds before insert, so plain UNIQUE works
CREATE UNIQUE INDEX IF NOT EXISTS idx_snmp_traps_dedup
    ON snmp_traps(server_id, source_ip, trap_oid, timestamp);

CREATE INDEX IF NOT EXISTS idx_snmp_traps_time ON snmp_traps(server_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_snmp_traps_ip   ON snmp_traps(source_ip, trap_type, resolved);

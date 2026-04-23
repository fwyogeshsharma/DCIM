-- Migration: 001_initial_schema
-- Description: Initial database schema for DCIM Server
-- Date: 2026-02-12

-- This migration is mostly a no-op as tables are created by database.go
-- But we ensure they exist with IF NOT EXISTS

-- Agents table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    agent_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    hostname TEXT,
    ip_address TEXT,
    version TEXT,
    status TEXT DEFAULT 'active',
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Metrics table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit TEXT,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Alerts table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    value DOUBLE PRECISION,
    threshold DOUBLE PRECISION,
    timestamp TIMESTAMP NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- SNMP Metrics table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS snmp_metrics (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    device_ip TEXT NOT NULL,
    oid TEXT NOT NULL,
    value TEXT NOT NULL,
    metric_type TEXT,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Agent status history table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS agent_status_history (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Aggregated metrics table (should already exist from database.go)
CREATE TABLE IF NOT EXISTS aggregated_metrics (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    interval TEXT NOT NULL,
    avg_value DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    count INTEGER,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create basic indexes
CREATE INDEX IF NOT EXISTS idx_metrics_agent_timestamp ON metrics(agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_type_timestamp ON metrics(metric_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_agent_timestamp ON alerts(agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved, severity);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_agent_timestamp ON snmp_metrics(agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_agent_status_history_agent ON agent_status_history(agent_id, timestamp);

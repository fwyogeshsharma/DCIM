-- Servers table
CREATE TABLE IF NOT EXISTS servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    url VARCHAR(500) NOT NULL,
    enabled BOOLEAN DEFAULT true,
    auth_type VARCHAR(50),
    auth_credentials JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Agents table
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    hostname VARCHAR(255),
    ip_address VARCHAR(45),
    status VARCHAR(50),
    certificate_cn VARCHAR(255),
    agent_group VARCHAR(100),
    approved BOOLEAN DEFAULT false,
    metadata JSONB,
    last_seen TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(server_id, agent_id)
);

-- Metrics table (will be converted to hypertable)
CREATE TABLE IF NOT EXISTS metrics (
    id BIGSERIAL,
    server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    metric_type VARCHAR(100) NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit VARCHAR(50),
    tags JSONB,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Alerts table
CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    severity VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    metric_type VARCHAR(100),
    threshold_value DOUBLE PRECISION,
    actual_value DOUBLE PRECISION,
    resolved BOOLEAN DEFAULT false,
    resolved_at TIMESTAMPTZ,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- User preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    server_id UUID REFERENCES servers(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, server_id)
);

-- SNMP metrics table
CREATE TABLE IF NOT EXISTS snmp_metrics (
    id BIGSERIAL,
    server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,
    device_name VARCHAR(255) NOT NULL,
    device_host VARCHAR(45) NOT NULL,
    metric_name VARCHAR(255) NOT NULL,
    value DOUBLE PRECISION,
    value_type TEXT,
    metadata TEXT,
    oid VARCHAR(255),
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_agents_server_id ON agents(server_id);
CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

CREATE INDEX IF NOT EXISTS idx_metrics_server_id ON metrics(server_id);
CREATE INDEX IF NOT EXISTS idx_metrics_agent_id ON metrics(agent_id);
CREATE INDEX IF NOT EXISTS idx_metrics_metric_type ON metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_server_id ON alerts(server_id);
CREATE INDEX IF NOT EXISTS idx_alerts_agent_id ON alerts(agent_id);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_snmp_metrics_server_id ON snmp_metrics(server_id);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_agent_id ON snmp_metrics(agent_id);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_timestamp ON snmp_metrics(timestamp DESC);

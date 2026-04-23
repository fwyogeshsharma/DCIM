-- Add unique constraints so ON CONFLICT DO NOTHING prevents duplicate data during sync cycles

CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_unique
  ON metrics (server_id, agent_id, metric_type, timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_snmp_metrics_unique
  ON snmp_metrics (server_id, agent_id, device_host, metric_name, timestamp);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique
  ON alerts (server_id, agent_id, metric_type, severity, timestamp);

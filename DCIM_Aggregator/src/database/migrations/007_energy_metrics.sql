-- 007_energy_metrics.sql
-- Dedicated hypertable for BACnet/IP energy telemetry (Verdigris EV2 power
-- meters), forwarded from DCS. Kept separate from the generic `metrics` table:
-- energy is high-cardinality (per-circuit) and billing/trend-relevant (kWh
-- accumulators kept long), so it gets its own retention and indexes. The ingest
-- route writes the per-device `energy_metrics[]` array here.

CREATE TABLE IF NOT EXISTS energy_metrics (
    device_id            UUID                NOT NULL,
    ts                   TIMESTAMPTZ         NOT NULL,
    metric_name          TEXT                NOT NULL,            -- e.g. energy.active_power_kw
    tag                  TEXT                NOT NULL DEFAULT '', -- raw secondary key (CktNN / PhA / H3)
    circuit              TEXT                NOT NULL DEFAULT '', -- CktNN for per-circuit rows, '' for panel
    phase                TEXT                NOT NULL DEFAULT '', -- PhA/PhB/PhC for phase rows, '' otherwise
    value                DOUBLE PRECISION    NOT NULL,
    attributes           JSONB,

    collector_agent      TEXT                NOT NULL DEFAULT 'EDR',
    collector_protocol   TEXT                NOT NULL DEFAULT 'BACNET'
);

SELECT create_hypertable('energy_metrics', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists       => TRUE);

CREATE UNIQUE INDEX IF NOT EXISTS uix_energy_metrics
    ON energy_metrics (device_id, metric_name, tag, ts);

CREATE INDEX IF NOT EXISTS idx_energy_device   ON energy_metrics (device_id, metric_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_energy_name      ON energy_metrics (metric_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_energy_circuit   ON energy_metrics (device_id, circuit, ts DESC);

SELECT add_retention_policy('energy_metrics', INTERVAL '365 days', if_not_exists => TRUE);

-- NOTE: this file formerly created the energy_metrics_5m continuous aggregate
-- here. It was removed before any deployment ever created it — metric
-- summarization now happens in the metricsSummary worker, which writes
-- avg/min/max documents to MongoDB.

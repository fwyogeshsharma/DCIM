-- Enable TimescaleDB extension (optional — skip gracefully when not installed)
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;

    -- Convert metrics table to hypertable (timeseries optimization)
    PERFORM create_hypertable('metrics', 'timestamp',
        chunk_time_interval => INTERVAL '1 day',
        if_not_exists => TRUE
    );

    -- Convert snmp_metrics to hypertable
    PERFORM create_hypertable('snmp_metrics', 'timestamp',
        chunk_time_interval => INTERVAL '1 day',
        if_not_exists => TRUE
    );

    -- Enable compression for older data (compress data older than 7 days)
    ALTER TABLE metrics SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'server_id, agent_id, metric_type'
    );

    PERFORM add_compression_policy('metrics', INTERVAL '7 days', if_not_exists => TRUE);

    ALTER TABLE snmp_metrics SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'server_id, agent_id, device_ip'
    );

    PERFORM add_compression_policy('snmp_metrics', INTERVAL '7 days', if_not_exists => TRUE);

    -- Continuous aggregates for performance (pre-aggregated views)
    CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_hourly
    WITH (timescaledb.continuous) AS
    SELECT
        time_bucket('1 hour', timestamp) AS bucket,
        server_id,
        agent_id,
        metric_type,
        AVG(value) as avg_value,
        MAX(value) as max_value,
        MIN(value) as min_value,
        COUNT(*) as sample_count
    FROM metrics
    GROUP BY bucket, server_id, agent_id, metric_type
    WITH NO DATA;

    -- Refresh policy for continuous aggregates
    PERFORM add_continuous_aggregate_policy('metrics_hourly',
        start_offset => INTERVAL '3 hours',
        end_offset => INTERVAL '1 hour',
        schedule_interval => INTERVAL '1 hour',
        if_not_exists => TRUE
    );

    -- Retention policy (keep raw data for 90 days)
    PERFORM add_retention_policy('metrics', INTERVAL '90 days', if_not_exists => TRUE);
    PERFORM add_retention_policy('snmp_metrics', INTERVAL '90 days', if_not_exists => TRUE);

EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'TimescaleDB not available — skipping hypertable setup: %', SQLERRM;
END
$$;

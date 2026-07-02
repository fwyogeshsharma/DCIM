-- 018_metric_compression.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Native TimescaleDB compression for the two high-volume hypertables.
-- Chunks older than 1 day are converted to columnar form (~90-95 % smaller);
-- ingest always lands in the current (uncompressed) chunk, so the write path
-- is unaffected. Range queries over compressed chunks stay fast because
-- segment_by matches the query filters (device_id, metric_name).
--
-- segmentby includes `tag`: TimescaleDB requires every column of a unique
-- index (uix_metrics / uix_energy_metrics are (device_id, metric_name, tag,
-- ts)) to appear in compress_segmentby or compress_orderby.
--
-- Idempotent: re-running ALTER TABLE with identical settings is a no-op, and
-- the policies use if_not_exists. Retention policies (001/007) drop whole
-- chunks regardless of compression state, so the two policies compose fine.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_name, tag',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('metrics', INTERVAL '1 day', if_not_exists => TRUE);

ALTER TABLE energy_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_name, tag',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('energy_metrics', INTERVAL '1 day', if_not_exists => TRUE);
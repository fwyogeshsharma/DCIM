-- 018_metric_compression.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Native TimescaleDB compression for the two high-volume hypertables. Chunks
-- older than 1 day are converted to columnar form (~90-95 % smaller); ingest
-- always lands in the current (uncompressed) chunk, so the write path is
-- unaffected.
--
-- ⚠ GATED — this migration only runs when MIGRATE_ENABLE_COMPRESSION=true
-- (enforced by migrate.ts; the file stays unrecorded/pending until then).
-- Reason: enabling a compression policy schedules a background job that
-- compresses the ENTIRE existing backlog of eligible chunks at once. With 30-day
-- (metrics) and 365-day (energy_metrics) retention over 1-day chunks that can be
-- hundreds of chunks, and compression is one of the most CPU-intensive things
-- TimescaleDB does — it will peg Postgres. Turning it on is therefore a
-- deliberate maintenance action, run during a quiet window, not a boot surprise.
--
-- segmentby = (device_id, metric_name): the columns range queries filter on.
-- `tag` was deliberately MOVED OUT of segmentby into orderby. As a segment key it
-- exploded cardinality (one segment per distinct tag), which makes compression
-- slower and the compressed chunks larger/less efficient. TimescaleDB only
-- requires every column of the unique index (uix_metrics / uix_energy_metrics =
-- device_id, metric_name, tag, ts) to appear in segmentby OR orderby, so keeping
-- `tag` in orderby still satisfies that constraint.
--
-- ⚠ Changing segmentby/orderby on a hypertable that ALREADY has compressed chunks
-- fails with "cannot change configuration on hypertable with compressed chunks".
-- If an earlier compression config was already applied on this database, first
-- decompress and drop the old policy in a maintenance window, then re-run:
--     SELECT remove_compression_policy('metrics', if_exists => TRUE);
--     SELECT decompress_chunk(c, true) FROM show_chunks('metrics') c;
--     -- (repeat both for 'energy_metrics'), then set MIGRATE_ENABLE_COMPRESSION=true
--
-- Idempotent: re-running ALTER TABLE with identical settings is a no-op and the
-- policies use if_not_exists. Retention policies (001/007) drop whole chunks
-- regardless of compression state, so the two policies compose fine.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_name',
    timescaledb.compress_orderby   = 'tag, ts DESC'
);

SELECT add_compression_policy('metrics', INTERVAL '1 day', if_not_exists => TRUE);

ALTER TABLE energy_metrics SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id, metric_name',
    timescaledb.compress_orderby   = 'tag, ts DESC'
);

SELECT add_compression_policy('energy_metrics', INTERVAL '1 day', if_not_exists => TRUE);

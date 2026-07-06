-- 017_drop_metric_rollups.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Removes the tiered long-horizon rollups formerly created by
-- 014_metric_rollups.sql (metrics_rollup_5m/1h/1d, energy_rollup_5m/1h/1d and
-- their *_dt convenience views). The tiered continuous-aggregate pyramid was
-- retired; this cleans it out of databases where 014 had already been applied.
--
-- Dropping a continuous aggregate also removes its refresh and retention
-- policies. Idempotent (IF EXISTS everywhere): a fresh database is a no-op.
--
-- The single-tier 5-minute aggregates (metrics_5m, energy_metrics_5m) were
-- kept at the time this file was written, but have since been retired too:
-- their creation was removed from 001_init.sql / 007_energy_metrics.sql (never
-- deployed anywhere, so no drop migration was needed). Metric summarization
-- now lives in the metricsSummary worker → MongoDB.
-- ─────────────────────────────────────────────────────────────────────────────

DROP VIEW IF EXISTS metrics_rollup_5m_dt;
DROP VIEW IF EXISTS metrics_rollup_1h_dt;
DROP VIEW IF EXISTS metrics_rollup_1d_dt;
DROP VIEW IF EXISTS energy_rollup_5m_dt;
DROP VIEW IF EXISTS energy_rollup_1h_dt;
DROP VIEW IF EXISTS energy_rollup_1d_dt;

-- Top of each tier first (1d reads 1h, 1h reads 5m).
DROP MATERIALIZED VIEW IF EXISTS metrics_rollup_1d CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_rollup_1h CASCADE;
DROP MATERIALIZED VIEW IF EXISTS metrics_rollup_5m CASCADE;
DROP MATERIALIZED VIEW IF EXISTS energy_rollup_1d CASCADE;
DROP MATERIALIZED VIEW IF EXISTS energy_rollup_1h CASCADE;
DROP MATERIALIZED VIEW IF EXISTS energy_rollup_5m CASCADE;
-- ─────────────────────────────────────────────────────────────────────────────
-- ONE-TIME cleanup — run manually against the Aggregator DB, NOT in migrations/.
--
-- The aggregator migration crash-loops on:
--   "cannot drop view metrics_rollup_5m because other objects depend on it"
-- because a DROP is issued without CASCADE while the continuous-aggregate
-- internals (_partial_view / _direct_view) and the *_dt convenience view still
-- depend on it.
--
-- Dropping the 5-minute aggregates WITH CASCADE clears the blocking state. After
-- this runs once, the migration's own (non-CASCADE) drop becomes a no-op via
-- IF EXISTS, and 014_metric_rollups.sql recreates the rollup chain WITH NO DATA;
-- the continuous-aggregate policies backfill it automatically.
--
-- CASCADE on metrics_rollup_5m also removes metrics_rollup_1h / _1d (they read
-- from it) and the *_dt views — all recreated by 014 on the next boot.
--
-- Apply once:
--   docker compose exec -T timescaledb psql -U dcim -d dcim_aggregator \
--     < DCIM_Aggregator/scripts/drop_legacy_5m_views.sql
-- then: docker compose up -d aggregator
-- ─────────────────────────────────────────────────────────────────────────────

-- Legacy 5m aggregates (001_init.sql / 007_energy_metrics.sql)
DROP MATERIALIZED VIEW IF EXISTS metrics_5m         CASCADE;
DROP MATERIALIZED VIEW IF EXISTS energy_metrics_5m  CASCADE;

-- New 5m rollups (014_metric_rollups.sql) — the one the migration chokes on.
-- CASCADE also clears the 1h/1d tiers and *_dt views built on top.
DROP MATERIALIZED VIEW IF EXISTS metrics_rollup_5m  CASCADE;
DROP MATERIALIZED VIEW IF EXISTS energy_rollup_5m   CASCADE;

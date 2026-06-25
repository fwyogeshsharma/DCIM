-- ml_schema.sql — tables owned by DCIM_ML, created idempotently on startup.
-- Live in the shared `dcim_aggregator` DB so the aggregator/UI can read them
-- later. Prefixed `ml_` to never collide with aggregator migrations.
-- DCIM_ML only WRITES these tables; it only READS the existing source tables.

-- ── Forecast points (one row per (scope, target, horizon-day)) ───────────────
CREATE TABLE IF NOT EXISTS ml_forecasts (
    id              BIGSERIAL    PRIMARY KEY,
    run_id          UUID         NOT NULL,
    scope           TEXT         NOT NULL,   -- 'global' | 'datacenter' | 'rack' | 'device'
    scope_id        TEXT         NOT NULL,   -- e.g. 'all', datacenter name, 'dc/rack', device uuid
    target          TEXT         NOT NULL,   -- 'cpu'|'memory'|'storage'|'power'|'rack_units'|'server_count'
    forecast_for    TIMESTAMPTZ  NOT NULL,   -- the date this prediction is about
    predicted_value DOUBLE PRECISION NOT NULL,
    lower_bound     DOUBLE PRECISION,
    upper_bound     DOUBLE PRECISION,
    unit            TEXT,
    model_name      TEXT,
    model_version   TEXT,
    generated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_forecasts_lookup
    ON ml_forecasts (scope, scope_id, target, forecast_for);
CREATE INDEX IF NOT EXISTS idx_ml_forecasts_run ON ml_forecasts (run_id);

-- ── Latest derived capacity numbers per scope (upsert target) ────────────────
CREATE TABLE IF NOT EXISTS ml_capacity_summary (
    scope                        TEXT NOT NULL,
    scope_id                     TEXT NOT NULL,
    run_id                       UUID NOT NULL,
    days_until_rack_full         INTEGER,
    rack_exhaustion_date         DATE,
    days_until_storage_full      INTEGER,
    storage_exhaustion_date      DATE,
    predicted_new_racks_30d      INTEGER,
    predicted_new_racks_60d      INTEGER,
    predicted_new_racks_90d      INTEGER,
    expected_server_growth_30d   INTEGER,
    expected_server_growth_60d   INTEGER,
    expected_server_growth_90d   INTEGER,
    power_headroom_pct           DOUBLE PRECISION,
    details                      JSONB NOT NULL DEFAULT '{}',
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_id)
);

-- ── Actionable recommendations ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ml_recommendations (
    id             BIGSERIAL   PRIMARY KEY,
    run_id         UUID        NOT NULL,
    scope          TEXT        NOT NULL,
    scope_id       TEXT        NOT NULL,
    category       TEXT        NOT NULL,   -- 'rack'|'storage'|'power'|'cooling'|'server'
    severity       TEXT        NOT NULL,   -- 'info'|'warning'|'critical'
    message        TEXT        NOT NULL,
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    predicted_date DATE,
    details        JSONB       NOT NULL DEFAULT '{}',
    status         TEXT        NOT NULL DEFAULT 'open',  -- 'open'|'ack'|'resolved'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_reco_lookup
    ON ml_recommendations (scope, scope_id, severity, status);
CREATE INDEX IF NOT EXISTS idx_ml_reco_run ON ml_recommendations (run_id);

-- ── Training-run audit (champion/challenger accuracy gate) ───────────────────
CREATE TABLE IF NOT EXISTS ml_model_runs (
    id            BIGSERIAL   PRIMARY KEY,
    run_id        UUID        NOT NULL,
    scope         TEXT        NOT NULL,
    scope_id      TEXT        NOT NULL,
    target        TEXT        NOT NULL,
    model_name    TEXT        NOT NULL,
    model_version TEXT,
    n_points      INTEGER,
    mae           DOUBLE PRECISION,
    mape          DOUBLE PRECISION,
    accepted      BOOLEAN     NOT NULL DEFAULT TRUE,
    notes         TEXT,
    trained_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ml_runs_lookup
    ON ml_model_runs (scope, scope_id, target, trained_at DESC);

-- Migration 019: servers registry
--
-- The aggregator polls one or more downstream DCIM servers (health checks,
-- trap sync, cert-based mTLS). ServerManager, the /servers route, and the
-- healthMonitor / trapsSync workers all read from this table, but no earlier
-- migration ever created it — so on a fresh DB every worker threw
-- `relation "servers" does not exist`. This creates it. Idempotent.

CREATE TABLE IF NOT EXISTS servers (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    name              TEXT         NOT NULL,
    url               TEXT         NOT NULL,
    enabled           BOOLEAN      NOT NULL DEFAULT true,

    auth_type         TEXT,
    auth_credentials  JSONB,
    metadata          JSONB,

    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_servers_enabled ON servers(enabled);

-- 000_compliance_maintenance_tickets.sql
-- Facilities / operations layer of the schema. Runs FIRST (lexicographically
-- ahead of 001_init.sql) and is fully self-contained:
--
--   compliance_records   — EPA / regulatory compliance & audit records per asset
--   maintenance_records  — preventive & corrective maintenance schedule + history
--   critical_tickets     — one ticket per critical event/alert that we generate
--   ticket_activity      — append-only audit trail (comments, status changes…)
--
-- Cross-references to devices/events use PLAIN nullable UUID columns WITHOUT a
-- foreign key — the same soft-reference pattern the `events` table uses (see
-- 001_init.sql and 004_event_link_endpoints.sql). This keeps the table
-- independent of device lifecycle (a device delete never cascades a compliance
-- record or ticket away) and lets this file run before `devices` exists.
--
-- The only hard FK is ticket_activity → critical_tickets, since both tables are
-- created together here.
--
-- Idempotent: re-running against a matching schema is a no-op.

-- ════════════════════════════════════════════════════════════════════════════
-- EPA COMPLIANCE & MAINTENANCE
-- ════════════════════════════════════════════════════════════════════════════

-- ────────────────────────────────────────────────────────────────────────────
-- Table: compliance_records
-- EPA / environmental & regulatory compliance state for a facility asset
-- (generators, UPS battery banks, CRAC/refrigerant systems, PUE reporting, …).
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS compliance_records (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id             TEXT         NOT NULL,
    datacenter_id      TEXT         NOT NULL,

    -- Soft reference to devices(id); NULL for facility-level (non-device) items.
    device_id          UUID,
    asset_name         TEXT         NOT NULL,

    -- e.g. 'epa_refrigerant' | 'epa_emissions' | 'epa_generator' |
    --      'energy_pue' | 'battery_disposal' | 'hazmat'
    compliance_type    TEXT         NOT NULL,
    -- Regulation / standard reference, e.g. 'Clean Air Act §608', 'EPA Tier 4'.
    regulation_ref     TEXT,

    -- 'compliant' | 'non_compliant' | 'pending' | 'in_progress' | 'expired'
    status             TEXT         NOT NULL DEFAULT 'pending',

    measured_value     DOUBLE PRECISION,
    threshold_value    DOUBLE PRECISION,
    unit               TEXT,

    last_audit_at      TIMESTAMPTZ,
    next_audit_due     TIMESTAMPTZ,

    certificate_ref    TEXT,
    responsible_party  TEXT,
    notes              TEXT,
    attributes         JSONB,

    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compliance_org_dc   ON compliance_records(org_id, datacenter_id);
CREATE INDEX IF NOT EXISTS idx_compliance_device   ON compliance_records(device_id);
CREATE INDEX IF NOT EXISTS idx_compliance_type     ON compliance_records(compliance_type);
CREATE INDEX IF NOT EXISTS idx_compliance_status   ON compliance_records(status);
CREATE INDEX IF NOT EXISTS idx_compliance_due      ON compliance_records(next_audit_due)
    WHERE next_audit_due IS NOT NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- Table: maintenance_records
-- Preventive & corrective maintenance schedule and history. May be tied to a
-- compliance record (e.g. a mandated annual generator emissions service).
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS maintenance_records (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    org_id                TEXT         NOT NULL,
    datacenter_id         TEXT         NOT NULL,

    -- Soft reference to devices(id); NULL for facility-level items.
    device_id             UUID,
    asset_name            TEXT         NOT NULL,

    -- Soft reference to compliance_records(id) when this is a compliance-driven
    -- maintenance task; NULL otherwise.
    compliance_record_id  UUID,

    -- 'preventive' | 'corrective' | 'inspection' | 'calibration' | 'emergency'
    maintenance_type      TEXT         NOT NULL DEFAULT 'preventive',

    title                 TEXT         NOT NULL,
    description           TEXT,

    -- 'scheduled' | 'in_progress' | 'completed' | 'overdue' | 'cancelled'
    status                TEXT         NOT NULL DEFAULT 'scheduled',

    scheduled_at          TIMESTAMPTZ,
    performed_at          TIMESTAMPTZ,
    next_due_at           TIMESTAMPTZ,

    performed_by          TEXT,
    cost                  NUMERIC(12, 2),
    attributes            JSONB,

    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_maint_org_dc      ON maintenance_records(org_id, datacenter_id);
CREATE INDEX IF NOT EXISTS idx_maint_device      ON maintenance_records(device_id);
CREATE INDEX IF NOT EXISTS idx_maint_compliance  ON maintenance_records(compliance_record_id);
CREATE INDEX IF NOT EXISTS idx_maint_status      ON maintenance_records(status);
CREATE INDEX IF NOT EXISTS idx_maint_due         ON maintenance_records(next_due_at)
    WHERE next_due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_maint_scheduled   ON maintenance_records(scheduled_at)
    WHERE scheduled_at IS NOT NULL;

-- ════════════════════════════════════════════════════════════════════════════
-- CRITICAL TICKETS
-- ════════════════════════════════════════════════════════════════════════════

-- Auto-incrementing human-readable ticket number source (TKT-000001, …).
CREATE SEQUENCE IF NOT EXISTS critical_ticket_seq START 1;

-- ────────────────────────────────────────────────────────────────────────────
-- Table: critical_tickets
-- One ticket per critical event/alert we generate. The triggering event/device
-- are soft-referenced (plain UUID, no FK) to stay decoupled from the
-- append-only `events` hypertable and from device deletes.
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS critical_tickets (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    ticket_number     TEXT         NOT NULL UNIQUE
                          DEFAULT ('TKT-' || lpad(nextval('critical_ticket_seq')::TEXT, 6, '0')),

    org_id            TEXT         NOT NULL,
    datacenter_id     TEXT         NOT NULL,

    -- Soft references to the originating event / device.
    event_id          UUID,
    device_id         UUID,
    source_hostname   TEXT,

    severity          TEXT         NOT NULL DEFAULT 'critical',
    -- 'P1' | 'P2' | 'P3' | 'P4'
    priority          TEXT         NOT NULL DEFAULT 'P1',
    -- 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'closed'
    status            TEXT         NOT NULL DEFAULT 'open',
    -- 'power' | 'cooling' | 'network' | 'compliance' | 'security' | 'other'
    category          TEXT         NOT NULL DEFAULT 'other',

    title             TEXT         NOT NULL,
    description       TEXT,

    assigned_to       TEXT,
    root_cause        TEXT,
    resolution        TEXT,

    sla_due_at        TIMESTAMPTZ,
    acknowledged_at   TIMESTAMPTZ,
    resolved_at       TIMESTAMPTZ,
    closed_at         TIMESTAMPTZ,

    attributes        JSONB,

    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tickets_org_dc    ON critical_tickets(org_id, datacenter_id);
CREATE INDEX IF NOT EXISTS idx_tickets_device    ON critical_tickets(device_id);
CREATE INDEX IF NOT EXISTS idx_tickets_event     ON critical_tickets(event_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status    ON critical_tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_priority  ON critical_tickets(priority, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_assignee  ON critical_tickets(assigned_to)
    WHERE assigned_to IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_open      ON critical_tickets(created_at DESC)
    WHERE status NOT IN ('resolved', 'closed');
-- One open ticket per originating event — prevents duplicate tickets for the
-- same critical event while still allowing a fresh ticket if a closed one
-- reopens as a new event.
CREATE UNIQUE INDEX IF NOT EXISTS uix_tickets_open_event
    ON critical_tickets(event_id)
    WHERE event_id IS NOT NULL AND status NOT IN ('resolved', 'closed');

-- ────────────────────────────────────────────────────────────────────────────
-- Table: ticket_activity
-- Append-only audit trail for a ticket: comments, status changes, assignments,
-- escalations. Hard FK to critical_tickets (same migration) with cascade delete.
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ticket_activity (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_id      UUID         NOT NULL REFERENCES critical_tickets(id) ON DELETE CASCADE,

    -- 'comment' | 'status_change' | 'assignment' | 'escalation' | 'created'
    activity_type  TEXT         NOT NULL DEFAULT 'comment',
    actor          TEXT,

    from_status    TEXT,
    to_status      TEXT,
    message        TEXT,
    attributes     JSONB,

    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_activity_ticket ON ticket_activity(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_activity_type   ON ticket_activity(activity_type);

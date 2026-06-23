-- 010_rule_changes.sql
-- DCS-side lifecycle/audit of every UI-originated device edit. This is the
-- companion to device_commands (008): device_commands is the short-lived WORK
-- QUEUE EDR drains; rule_changes is the durable RECORD of each change and the
-- state the Aggregator confirms against and shows on the UI.
--
-- Lifecycle:
--   pending   — pulled from the Aggregator ui_changes feed; queued for EDR
--   applied   — EDR wrote it to the device (ack received)
--   confirmed — device telemetry now reports new_value (round-trip proven)
--   failed    — EDR could not apply it (see error)

CREATE TABLE IF NOT EXISTS rule_changes (
                                            id            BIGSERIAL    PRIMARY KEY,
                                            org_id        TEXT         NOT NULL DEFAULT '',
                                            network_id    TEXT         NOT NULL DEFAULT '',

    -- Target + what changed. device_ip is the stable mgmt IP EDR applies against
    -- (SNMP community / Redfish host == device mgmt IP).
                                            device_ip     TEXT         NOT NULL,
                                            hostname      TEXT         NOT NULL DEFAULT '',
                                            field         TEXT         NOT NULL,            -- sysLocation, HighCPU, power_action, …
                                            old_value     TEXT         NOT NULL DEFAULT '',
                                            new_value     TEXT         NOT NULL DEFAULT '',

    -- Lifecycle.
                                            status        TEXT         NOT NULL DEFAULT 'pending'
                                            CHECK (status IN ('pending','applied','confirmed','failed')),
    error         TEXT         NOT NULL DEFAULT '',
    attempts      INT          NOT NULL DEFAULT 0,

    -- Link to the work-queue row (device_commands.id) that carries out the apply.
    command_id    BIGINT,

    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    applied_at    TIMESTAMPTZ,
    confirmed_at  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
    );

-- Up-path feed: DCS forwards status changes to the Aggregator ordered by
-- updated_at; cursor advances past the last sent row.
CREATE INDEX IF NOT EXISTS ix_rule_changes_updated
    ON rule_changes (updated_at);

-- Status-board lookups (pending work, failures).
CREATE INDEX IF NOT EXISTS ix_rule_changes_status
    ON rule_changes (status, updated_at);

-- Collapse repeat edits of the same field on the same device while still pending.
CREATE UNIQUE INDEX IF NOT EXISTS uix_rule_changes_pending
    ON rule_changes (device_ip, field)
    WHERE status = 'pending';

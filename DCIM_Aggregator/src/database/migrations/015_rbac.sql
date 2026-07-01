-- =============================================================================
-- 015_rbac.sql — Role-Based Access Control
-- Roles → feature mapping lives in src/config/rbac.yml (single source of truth).
-- This migration stores only WHICH roles a user holds + approval status +
-- session tokens + an audit trail. Idempotent (safe to re-run).
-- =============================================================================

-- Approval status + the role the user asked for at sign-up.
ALTER TABLE users ADD COLUMN IF NOT EXISTS status         TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE users ADD COLUMN IF NOT EXISTS requested_role TEXT;

-- Roles a user holds. `role` is a key defined in rbac.yml (e.g. 'infra_engineer').
-- Multiple rows per user = multiple roles (effective access = highest per feature).
CREATE TABLE IF NOT EXISTS user_roles (
    user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL,
    granted_by INTEGER     REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles (user_id);

-- Audit trail of approval / role-change actions.
CREATE TABLE IF NOT EXISTS role_approvals (
    id          SERIAL      PRIMARY KEY,
    user_id     INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approver_id INTEGER     REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT        NOT NULL,          -- 'approved' | 'rejected' | 'roles_changed'
    roles       TEXT,                          -- comma-separated roles at time of action
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_role_approvals_user ON role_approvals (user_id);

-- Opaque bearer tokens used to authenticate API calls (there was no session
-- mechanism before RBAC — login just returned the user object).
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT        PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

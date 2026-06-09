-- DCIM UI user accounts for authentication
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL       PRIMARY KEY,
    username      TEXT         UNIQUE NOT NULL,
    email         TEXT         UNIQUE NOT NULL,
    password_hash TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
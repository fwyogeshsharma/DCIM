# Database Migrations System

**Date:** 2026-02-13
**Status:** ✅ PRODUCTION READY

---

## Overview

DCIM Server now includes automatic database migration system that runs on server startup. No manual SQL scripts needed!

### Key Features

- ✅ **Automatic**: Migrations run on server startup
- ✅ **Safe**: Each migration runs in a transaction (all-or-nothing)
- ✅ **Tracked**: Uses `schema_migrations` table to track applied migrations
- ✅ **Idempotent**: Safe to run multiple times (uses IF NOT EXISTS, ALTER IF NOT EXISTS)
- ✅ **Versioned**: Migrations numbered sequentially (001, 002, 003...)
- ✅ **Included in Build**: Migrations folder packaged with executable

---

## How It Works

### Server Startup Flow

```
1. Server starts
2. Connects to database
3. Creates tables with InitSchema() (if fresh database)
4. Runs pending migrations from migrations/ folder
5. Records successful migrations in schema_migrations table
6. Server continues normal operation
```

### Migration Tracking Table

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Example:**
```sql
SELECT * FROM schema_migrations;

-- version | name              | applied_at
-- --------|-------------------|--------------------
-- 1       | initial_schema    | 2026-02-13 10:00:00
-- 2       | server_tracking   | 2026-02-13 10:00:01
-- 3       | alert_resolution  | 2026-02-13 10:00:02
```

---

## Migration Files

### Location
```
DCIM_Server/
  migrations/
    001_initial_schema.sql
    002_server_tracking.sql
    003_alert_resolution.sql
```

### Naming Convention

Format: `{version}_{description}.sql`

- **Version**: 3-digit number (001, 002, 003, ...)
- **Description**: Snake_case description
- **Extension**: `.sql`

**Examples:**
- ✅ `001_initial_schema.sql`
- ✅ `002_server_tracking.sql`
- ✅ `042_add_user_roles.sql`
- ❌ `1_schema.sql` (version must be 3 digits)
- ❌ `002_AddColumns.sql` (use snake_case)

### Migration File Structure

```sql
-- Migration: 002_server_tracking
-- Description: Add server tracking and alert deduplication features
-- Date: 2026-02-12

-- =====================================================
-- Create new tables
-- =====================================================
CREATE TABLE IF NOT EXISTS servers (
    id SERIAL PRIMARY KEY,
    server_id TEXT UNIQUE NOT NULL,
    ...
);

-- =====================================================
-- Add columns to existing tables
-- =====================================================
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrence_count INTEGER DEFAULT 1;

-- =====================================================
-- Backfill existing data
-- =====================================================
UPDATE metrics SET server_id = 'legacy-server' WHERE server_id IS NULL;

-- =====================================================
-- Make columns NOT NULL after backfill
-- =====================================================
ALTER TABLE metrics ALTER COLUMN server_id SET NOT NULL;

-- =====================================================
-- Create indexes
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_metrics_server ON metrics(server_id, timestamp);
```

**Important Guidelines:**
- ✅ Always use `IF NOT EXISTS` for CREATE TABLE
- ✅ Always use `IF NOT EXISTS` for ALTER TABLE ADD COLUMN (PostgreSQL)
- ✅ Backfill data BEFORE making columns NOT NULL
- ✅ Use comments to explain what each section does
- ✅ Keep migrations atomic (single purpose per migration)

---

## Current Migrations

### 001_initial_schema.sql
- Creates base tables (agents, metrics, alerts, snmp_metrics, etc.)
- Creates basic indexes
- **Status**: Mostly no-op since InitSchema() creates these tables

### 002_server_tracking.sql
- Creates `servers` table for multi-server tracking
- Adds `server_id` column to all data tables
- Adds alert deduplication columns (`occurrence_count`, `first_seen`, `last_seen`, `updated_at`)
- Backfills existing data with `'legacy-server'` ID
- Creates performance indexes

### 003_alert_resolution.sql
- Adds alert resolution tracking columns (`resolved_by`, `resolution_action`, `resolution_notes`)
- Creates indexes for resolution queries
- Enables audit trail for who fixed what

---

## Production Deployment

### Option 1: Build and Deploy (Recommended)

```powershell
# Build server (includes migrations folder)
cd DCIM_Server
.\build.ps1 -Platform windows

# Deploy
cd build\windows-amd64\
.\dcim-server.exe
```

**On First Run:**
```
[DATABASE] Connected to postgres database
[MIGRATIONS] Found 3 pending migrations (out of 3 total)
[MIGRATIONS] Running migration 001: initial_schema
[MIGRATIONS] ✓ Migration 001 completed successfully
[MIGRATIONS] Running migration 002: server_tracking
[MIGRATIONS] ✓ Migration 002 completed successfully
[MIGRATIONS] Running migration 003: alert_resolution
[MIGRATIONS] ✓ Migration 003 completed successfully
[MIGRATIONS] ✓ All migrations completed successfully
[SERVER] Server initialized with ID: Faber-abc12345
```

**On Subsequent Runs:**
```
[DATABASE] Connected to postgres database
[MIGRATIONS] All migrations up to date (3 total)
[SERVER] Server initialized with ID: Faber-abc12345
```

### Option 2: Manual Migration (Old Way)

If you need to run migrations manually for any reason:

```powershell
# Using the migration script
cd DCIM_Server
psql -d dcim_db -U postgres -f migrations/001_initial_schema.sql
psql -d dcim_db -U postgres -f migrations/002_server_tracking.sql
psql -d dcim_db -U postgres -f migrations/003_alert_resolution.sql
```

---

## Adding New Migrations

### Step 1: Create Migration File

```powershell
# Create new migration
cd DCIM_Server/migrations
New-Item -ItemType File -Name "004_your_feature.sql"
```

### Step 2: Write Migration SQL

```sql
-- Migration: 004_your_feature
-- Description: Add your feature description
-- Date: 2026-02-13

-- Create or alter tables
CREATE TABLE IF NOT EXISTS new_table (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

-- Add columns to existing tables
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS new_column TEXT;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_new_table_name ON new_table(name);
```

### Step 3: Test Migration

```powershell
# Test locally
cd DCIM_Server
go run . -config config.yaml

# Check logs for migration success
# Should see: "[MIGRATIONS] Running migration 004: your_feature"
```

### Step 4: Rebuild and Deploy

```powershell
# Build (migrations folder is automatically included)
.\build.ps1 -Platform windows

# Deploy to production
# Migrations run automatically on startup
```

---

## Best Practices

### DO ✅
- Use sequential version numbers (001, 002, 003...)
- Make migrations idempotent (safe to run multiple times)
- Add comments explaining what each section does
- Test migrations on dev database first
- Use transactions (migrations run in transactions automatically)
- Backfill data before making columns NOT NULL
- Create indexes AFTER data is loaded

### DON'T ❌
- Skip version numbers
- Edit already-applied migrations (create new one instead)
- Use DROP COLUMN (adds new migration to add column back if needed)
- Remove old migration files (they're part of history)
- Make destructive changes without backups
- Use database-specific syntax if possible (breaks portability)

---

## Troubleshooting

### Migration Failed During Startup

**Symptom:**
```
[ERROR] Failed to run migrations: failed to apply migration 002 (server_tracking): pq: column "server_id" already exists
```

**Cause:** Migration not idempotent (doesn't use IF NOT EXISTS)

**Fix:** Update migration to use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`

### Database Out of Sync

**Symptom:** Server expects columns that don't exist

**Check Migration Status:**
```sql
SELECT * FROM schema_migrations ORDER BY version;
```

**Fix:** Run missing migrations manually or let server run them on next startup

### Migration Stuck

**Symptom:** Migration never completes

**Cause:** Long-running migration on large table

**Fix:**
1. Check database locks: `SELECT * FROM pg_locks;`
2. Consider splitting migration into smaller chunks
3. Run migration during maintenance window

---

## Migration Files Backup

Always backup migration files:

```powershell
# Backup migrations folder
cd DCIM_Server
Copy-Item -Recurse migrations migrations_backup_$(Get-Date -Format 'yyyy-MM-dd')
```

---

## Database Rollback

Migrations don't have automatic rollback. For rollback:

1. **Restore from backup** (recommended)
   ```bash
   pg_restore -d dcim_db backup_file.dump
   ```

2. **Manually undo migration**
   ```sql
   -- Remove column added by migration
   ALTER TABLE metrics DROP COLUMN server_id;

   -- Remove migration record
   DELETE FROM schema_migrations WHERE version = 2;
   ```

---

## Testing Migrations

### Test on Fresh Database

```powershell
# Create test database
psql -U postgres -c "CREATE DATABASE dcim_test;"

# Update config.yaml to point to dcim_test
# Run server
go run . -config config.yaml

# Verify all migrations ran
psql -d dcim_test -c "SELECT * FROM schema_migrations;"
```

### Test on Existing Database Copy

```powershell
# Create database copy
psql -U postgres -c "CREATE DATABASE dcim_test_copy TEMPLATE dcim_db;"

# Update config to point to test copy
# Run server with new migration
go run . -config config.yaml

# Verify migration succeeded
```

---

## Integration with CI/CD

### Build Pipeline

```yaml
# Example GitHub Actions
- name: Build DCIM Server
  run: |
    cd DCIM_Server
    ./build.ps1 -Platform linux

# Migrations folder is automatically included in build
```

### Deployment Pipeline

```yaml
# Server startup automatically runs migrations
- name: Deploy Server
  run: |
    ./dcim-server -config config.yaml

# No manual migration steps needed!
```

---

## Summary

### What You Get

- ✅ Automatic migrations on server startup
- ✅ No manual SQL scripts to run
- ✅ Safe, transactional migrations
- ✅ Version tracking in database
- ✅ Idempotent migrations (safe to re-run)
- ✅ Included in build package
- ✅ Production-ready deployment

### Migration Lifecycle

```
Create Migration → Test Locally → Rebuild Server → Deploy → Migrations Run Automatically ✅
```

---

**Implemented By:** Claude Code
**Date:** 2026-02-13
**Version:** 2.0.0
**Status:** PRODUCTION READY ✅

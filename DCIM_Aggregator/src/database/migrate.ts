import { Pool } from 'pg'
import fs from 'fs'
import path from 'path'
import { config } from '../config/database'
import { logger } from '../utils/logger'

// Migrations that must NOT run automatically. They perform an expensive, one-off
// operation that should be a deliberate maintenance action rather than a
// boot-time surprise. A gated file stays "pending" (unrecorded in the ledger)
// until its env flag is set to 'true', then it applies once like any other file.
const GATED_MIGRATIONS: Record<string, string> = {
  // Enabling compression schedules a background job that compresses the entire
  // existing chunk backlog at once — hundreds of chunks under 30d/365d retention,
  // and compression is very CPU-heavy. Flag it on for a maintenance window.
  '018_metric_compression.sql': 'MIGRATE_ENABLE_COMPRESSION',
}

async function runMigrations() {
  const pool = new Pool({
    host: config.postgres.host,
    port: config.postgres.port,
    database: config.postgres.database,
    user: config.postgres.user,
    password: config.postgres.password,
  })

  try {
    logger.info('Starting database migration...')

    // Migration ledger: every applied file is recorded here so each runs EXACTLY
    // ONCE, ever. Previously migrate re-ran every .sql file on every boot, which
    // re-executed heavy DML (006 inventory seed = full-table INSERT…SELECT sweeps)
    // and DDL (011 = an ALTER COLUMN TYPE table rewrite) each start. Combined with
    // `restart: unless-stopped`, a crash turned that into a loop that pegged
    // Postgres. The ledger ends the re-run entirely — pending files only.
    await pool.query(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        filename   TEXT        PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
      )
    `)

    // One-time legacy cleanup. This DROPs the base tables (and CASCADEs into every
    // continuous aggregate / materialized view built on them), which the
    // migrations below then rebuild WITH NO DATA. It also TRUNCATEs the ledger so
    // that a reset genuinely re-applies every file. Gate it behind an explicit
    // flag — running it on every boot wipes metric history and is never needed
    // for a normal start.
    if (process.env.MIGRATE_RESET_LEGACY === 'true') {
      await pool.query(`
        DROP TABLE IF EXISTS snmp_traps        CASCADE;
        DROP TABLE IF EXISTS snmp_metrics      CASCADE;
        DROP TABLE IF EXISTS alerts            CASCADE;
        DROP TABLE IF EXISTS metrics           CASCADE;
        DROP TABLE IF EXISTS topology_links    CASCADE;
        DROP TABLE IF EXISTS agents            CASCADE;
        DROP TABLE IF EXISTS servers           CASCADE;
        DROP VIEW  IF EXISTS topology_view     CASCADE;
        DROP VIEW  IF EXISTS device_inventory  CASCADE;
        DROP MATERIALIZED VIEW IF EXISTS metrics_5m CASCADE;
        TRUNCATE schema_migrations;
      `)
      logger.warn('MIGRATE_RESET_LEGACY=true → dropped legacy tables/views and cleared the migration ledger')
    }

    // Run every not-yet-applied migration file in lexicographic order
    // (000_…, 001_…, …). Each view-defining migration now DROPs its own views
    // before (re)creating them, so it is self-contained and safe on the one-time
    // catch-up pass that a pre-existing database gets when the ledger is first
    // introduced. Any NEW migration that redefines a view must follow the same
    // pattern (DROP VIEW IF EXISTS … CASCADE before CREATE), because the ledger
    // means files no longer re-run to "repair" a globally-dropped view.
    const migrationsDir = path.join(__dirname, 'migrations')
    const files = fs
      .readdirSync(migrationsDir)
      .filter((f) => f.endsWith('.sql'))
      .sort()

    const { rows: appliedRows } = await pool.query<{ filename: string }>(
      'SELECT filename FROM schema_migrations'
    )
    const applied = new Set(appliedRows.map((r) => r.filename))

    let ran = 0
    for (const file of files) {
      if (applied.has(file)) continue

      const gateFlag = GATED_MIGRATIONS[file]
      if (gateFlag && process.env[gateFlag] !== 'true') {
        // Left unrecorded on purpose — it will apply the first time the flag is set.
        logger.info(`⏭  Skipping gated migration ${file} (set ${gateFlag}=true to apply)`)
        continue
      }

      const sql = fs.readFileSync(path.join(migrationsDir, file), 'utf-8')
      await pool.query(sql)
      await pool.query(
        'INSERT INTO schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING',
        [file]
      )
      logger.info(`✓ Applied ${file}`)
      ran++
    }

    logger.info(
      ran === 0
        ? 'Migrations up to date (nothing to apply)'
        : `Migration completed successfully (${ran} applied)`
    )
  } catch (error: any) {
    logger.error(`Migration failed: ${error.message}`)
    process.exit(1)
  } finally {
    await pool.end()
  }
}

if (require.main === module) {
  runMigrations()
}

export { runMigrations }

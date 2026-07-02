import { Pool } from 'pg'
import fs from 'fs'
import path from 'path'
import { config } from '../config/database'
import { logger } from '../utils/logger'

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

    // One-time legacy cleanup. This DROPs `metrics` (and CASCADEs into every
    // continuous aggregate / materialized view built on it — metrics_5m),
    // which the migrations below then rebuild
    // WITH NO DATA. Running it on every boot wipes metric history AND makes the
    // rebuild so slow the aggregator healthcheck times out (containers go
    // "unhealthy"). All migrations are idempotent (IF NOT EXISTS), so this block
    // is NOT needed for normal runs — gate it behind an explicit flag.
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
      `)
      logger.warn('MIGRATE_RESET_LEGACY=true → dropped legacy tables & materialized views')
    } else {
      logger.info('Skipping legacy drop (materialized views & data preserved). Set MIGRATE_RESET_LEGACY=true to force a rebuild.')
    }

    // Plain (non-materialized) VIEWs hold no data and are cheap to rebuild. Some
    // are redefined across migrations (001 and 003 both define topology_view /
    // topology_tree, the later one adding columns). Postgres CREATE OR REPLACE
    // VIEW cannot drop/reorder an existing view's columns (error 42P16), so on a
    // re-run the earlier, smaller definition fails against the already-extended
    // view. Dropping just these views each run keeps migrations idempotent
    // WITHOUT touching materialized views / continuous aggregates or table data.
    await pool.query(`
      DROP VIEW IF EXISTS topology_view        CASCADE;
      DROP VIEW IF EXISTS topology_tree        CASCADE;
      DROP VIEW IF EXISTS device_inventory     CASCADE;
      DROP VIEW IF EXISTS dc_inventory_summary CASCADE;
    `)
    logger.info('Dropped plain views for clean re-create (no data affected)')

    // Run every migration file in lexicographic order (001_init.sql,
    // 002_topology_relation.sql, …). Each file is idempotent, so re-running is
    // a no-op against an already-migrated database.
    const migrationsDir = path.join(__dirname, 'migrations')
    const files = fs
      .readdirSync(migrationsDir)
      .filter((f) => f.endsWith('.sql'))
      .sort()

    for (const file of files) {
      const sql = fs.readFileSync(path.join(migrationsDir, file), 'utf-8')
      await pool.query(sql)
      logger.info(`✓ Applied ${file}`)
    }

    logger.info('Migration completed successfully')
  } catch (error) {
    logger.error('Migration failed:', error)
    process.exit(1)
  } finally {
    await pool.end()
  }
}

if (require.main === module) {
  runMigrations()
}

export { runMigrations }
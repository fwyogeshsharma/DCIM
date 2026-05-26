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

    // Drop legacy tables that conflict with the new unified schema
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
    logger.info('Dropped legacy tables (if any)')

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
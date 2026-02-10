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
    logger.info('Starting database migrations...')

    const migrationsDir = path.join(__dirname, 'migrations')
    const migrationFiles = fs.readdirSync(migrationsDir).sort()

    for (const file of migrationFiles) {
      if (!file.endsWith('.sql')) continue

      logger.info(`Running migration: ${file}`)
      const sqlPath = path.join(migrationsDir, file)
      const sql = fs.readFileSync(sqlPath, 'utf-8')

      await pool.query(sql)
      logger.info(`✓ Migration completed: ${file}`)
    }

    logger.info('All migrations completed successfully')
  } catch (error) {
    logger.error('Migration failed:', error)
    process.exit(1)
  } finally {
    await pool.end()
  }
}

// Run if called directly
if (require.main === module) {
  runMigrations()
}

export { runMigrations }

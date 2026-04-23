import { Pool } from 'pg'
import fs from 'fs'
import path from 'path'
import { config } from '../config/database'
import { logger } from '../utils/logger'

async function runMigrations(pool?: Pool) {
  const ownPool = !pool
  const db = pool ?? new Pool({
    host: config.postgres.host,
    port: config.postgres.port,
    database: config.postgres.database,
    user: config.postgres.user,
    password: config.postgres.password,
  })

  try {
    logger.info('Running database migrations...')

    // In Docker the SQL files live in the mounted src tree, not in dist.
    const distMigrations = path.join(__dirname, 'migrations')
    const srcMigrations = path.join(__dirname, '../../src/database/migrations')
    const migrationsDir = fs.existsSync(distMigrations) ? distMigrations : srcMigrations
    const migrationFiles = fs.readdirSync(migrationsDir).sort()

    for (const file of migrationFiles) {
      if (!file.endsWith('.sql')) continue

      const sqlPath = path.join(migrationsDir, file)
      const sql = fs.readFileSync(sqlPath, 'utf-8')

      await db.query(sql)
      logger.info(`✓ ${file}`)
    }

    logger.info('All migrations completed')
  } catch (error) {
    logger.error('Migration failed:', error)
    process.exit(1)
  } finally {
    if (ownPool) await db.end()
  }
}

// Run if called directly (npm run migrate)
if (require.main === module) {
  runMigrations()
}

export { runMigrations }

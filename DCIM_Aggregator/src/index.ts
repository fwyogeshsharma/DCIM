import express from 'express'
import cors from 'cors'
import { Pool } from 'pg'
import { createClient } from 'redis'
import { config } from './config/database'
import { setupRoutes } from './api/routes'
import { startWorkers } from './workers'
import { logger } from './utils/logger'
import { errorHandler, notFoundHandler } from './api/middleware/errorHandler'

const app = express()
const port = config.server.port

// Database connection
export const dbPool = new Pool({
  host: config.postgres.host,
  port: config.postgres.port,
  database: config.postgres.database,
  user: config.postgres.user,
  password: config.postgres.password,
  max: 20,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 5000,
})

// Disable TimescaleDB vectorized aggregation (incompatible with character varying columns)
dbPool.on('connect', (client) => {
  client.query("SET timescaledb.enable_vectorized_aggregation = off").catch(() => {})
})

// Redis connection
export const redisClient = createClient({
  url: config.redis.url,
})

// Middleware
app.use(
  cors({
    origin: '*',
    credentials: true,
  })
)
app.use(express.json({ limit: '10mb' }))
app.use(express.urlencoded({ extended: true }))

// Request logging
app.use((req, res, next) => {
  logger.debug(`${req.method} ${req.path}`)
  next()
})

// Health check
app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    service: 'DCIM Aggregator',
    timestamp: new Date().toISOString(),
  })
})

// API routes
setupRoutes(app, dbPool, redisClient as any)

// Error handlers
app.use(notFoundHandler)
app.use(errorHandler)

// Start server
async function start() {
  try {
    // Test database connection
    const dbClient = await dbPool.connect()
    logger.info('PostgreSQL connected')
    dbClient.release()

    // Connect Redis
    await redisClient.connect()
    logger.info('Redis connected')

    // Start background workers
    startWorkers(dbPool, redisClient as any)

    // Start HTTP server
    app.listen(port, () => {
      logger.info(`DCIM Aggregator listening on port ${port}`)
      logger.info(`Environment: ${config.server.env}`)
      logger.info(`Health check: http://localhost:${port}/health`)
    })
  } catch (error) {
    logger.error('Failed to start aggregator:', error)
    process.exit(1)
  }
}

// Graceful shutdown
process.on('SIGTERM', async () => {
  logger.info('SIGTERM received, shutting down gracefully...')
  await dbPool.end()
  await redisClient.quit()
  process.exit(0)
})

process.on('SIGINT', async () => {
  logger.info('SIGINT received, shutting down gracefully...')
  await dbPool.end()
  await redisClient.quit()
  process.exit(0)
})

start()

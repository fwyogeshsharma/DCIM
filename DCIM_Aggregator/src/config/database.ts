import dotenv from 'dotenv'

dotenv.config()

export const config = {
  postgres: {
    host: process.env.POSTGRES_HOST || 'localhost',
    port: parseInt(process.env.POSTGRES_PORT || '5435'),
    database: process.env.POSTGRES_DB || 'dcim_aggregator',
    user: process.env.POSTGRES_USER || 'dcim',
    password: process.env.POSTGRES_PASSWORD || 'dcim_pas',
  },
  redis: {
    url: process.env.REDIS_URL || 'redis://localhost:6379',
  },
  server: {
    port: parseInt(process.env.PORT || '3002'),
    env: process.env.NODE_ENV || 'development',
  },
  dcimServers: (process.env.DCIM_SERVER_URLS || '')
    .split(',')
    .map((u) => u.trim())
    .filter(Boolean),
}

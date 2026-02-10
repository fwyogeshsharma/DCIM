import dotenv from 'dotenv'

dotenv.config()

export const config = {
  postgres: {
    host: process.env.POSTGRES_HOST || 'localhost',
    port: parseInt(process.env.POSTGRES_PORT || '5432'),
    database: process.env.POSTGRES_DB || 'dcim_aggregator',
    user: process.env.POSTGRES_USER || 'dcim',
    password: process.env.POSTGRES_PASSWORD || 'dcim_password',
  },
  redis: {
    url: process.env.REDIS_URL || 'redis://localhost:6379',
  },
  server: {
    port: parseInt(process.env.PORT || '3002'),
    env: process.env.NODE_ENV || 'development',
  },
}

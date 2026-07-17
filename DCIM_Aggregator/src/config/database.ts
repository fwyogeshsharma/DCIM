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
  mongo: {
    url: process.env.MONGO_URL || 'mongodb://localhost:27017',
    database: process.env.MONGO_DB || 'dcim_aggregator',
  },
  metricsSummary: {
    // How often the metricsSummary worker runs and how wide each summarized
    // window is (one MongoDB document per window). Must divide 60 evenly.
    intervalMinutes: parseInt(process.env.METRICS_SUMMARY_INTERVAL_MINUTES || '5'),
    // TTL for the fine-grained (5m) summary documents. Short by design: every
    // hour they are rolled up into hourly documents, so they only need to
    // survive long enough to be queried recently and rolled up.
    retention5mHours: parseInt(process.env.METRICS_SUMMARY_5M_RETENTION_HOURS || '24'),
    // TTL for the hourly rollup documents — the middle tier. Once a day they
    // are rolled up into daily documents, so a week of hourly resolution is
    // plenty for dashboards before the daily tier takes over.
    hourlyRetentionDays: parseInt(process.env.METRICS_SUMMARY_HOURLY_RETENTION_DAYS || '7'),
    // TTL for the daily rollup documents — the terminal long-horizon tier.
    // Falls back to the pre-tiering METRICS_SUMMARY_RETENTION_DAYS variable.
    dailyRetentionDays: parseInt(
      process.env.METRICS_SUMMARY_DAILY_RETENTION_DAYS ||
        process.env.METRICS_SUMMARY_RETENTION_DAYS ||
        '180'
    ),
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

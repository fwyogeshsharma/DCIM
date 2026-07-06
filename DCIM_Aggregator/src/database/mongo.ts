import { MongoClient, Db } from 'mongodb'
import { config } from '../config/database'
import { logger } from '../utils/logger'

// Lazy singleton MongoDB connection. Mongo only stores the periodic metric
// summary documents (metricsSummary worker) — the API and every other worker
// keep using TimescaleDB/Redis — so an unreachable Mongo must never take the
// aggregator down. Callers get a rejected promise and decide how to degrade.
let client: MongoClient | null = null
let connecting: Promise<MongoClient> | null = null

async function getClient(): Promise<MongoClient> {
  if (client) return client
  if (!connecting) {
    connecting = new MongoClient(config.mongo.url, {
      serverSelectionTimeoutMS: 5000,
    })
      .connect()
      .then((c) => {
        client = c
        logger.info(`MongoDB connected (${config.mongo.url}/${config.mongo.database})`)
        return c
      })
      .catch((err) => {
        connecting = null // allow the next caller to retry
        throw err
      })
  }
  return connecting
}

export async function getMongoDb(): Promise<Db> {
  return (await getClient()).db(config.mongo.database)
}

export async function closeMongo(): Promise<void> {
  if (client) {
    await client.close()
    client = null
    connecting = null
  }
}
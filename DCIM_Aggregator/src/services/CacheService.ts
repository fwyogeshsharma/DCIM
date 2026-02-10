import { RedisClientType } from 'redis'
import { logger } from '../utils/logger'

export class CacheService {
  constructor(private redisClient: RedisClientType) {}

  async get<T>(key: string): Promise<T | null> {
    try {
      const cached = await this.redisClient.get(key)
      if (cached) {
        return JSON.parse(cached) as T
      }
      return null
    } catch (error) {
      logger.error(`Cache get error for key ${key}:`, error)
      return null
    }
  }

  async set(key: string, value: any, ttl: number = 60): Promise<void> {
    try {
      await this.redisClient.setEx(key, ttl, JSON.stringify(value))
    } catch (error) {
      logger.error(`Cache set error for key ${key}:`, error)
    }
  }

  async del(key: string): Promise<void> {
    try {
      await this.redisClient.del(key)
    } catch (error) {
      logger.error(`Cache delete error for key ${key}:`, error)
    }
  }

  async invalidatePattern(pattern: string): Promise<void> {
    try {
      const keys = await this.redisClient.keys(pattern)
      if (keys.length > 0) {
        await this.redisClient.del(keys)
        logger.info(`Invalidated ${keys.length} cache keys matching ${pattern}`)
      }
    } catch (error) {
      logger.error(`Cache invalidate pattern error:`, error)
    }
  }

  async setServerHealth(serverId: string, health: any): Promise<void> {
    await this.set(`server:health:${serverId}`, health, 60)
  }

  async getServerHealth(serverId: string): Promise<any> {
    return this.get(`server:health:${serverId}`)
  }
}

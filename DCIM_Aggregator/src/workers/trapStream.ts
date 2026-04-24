import { Pool } from 'pg'
import { TrapStreamService } from '../services/TrapStreamService'
import { logger } from '../utils/logger'

export let trapStreamService: TrapStreamService | null = null
let dbPool: Pool

export function initTrapStreamWorker(pool: Pool): void {
  dbPool = pool
  trapStreamService = new TrapStreamService(pool)
}

export async function startTrapStreamWorker(): Promise<void> {
  if (!trapStreamService) return

  await trapStreamService.loadInitialState()

  try {
    const { rows: servers } = await dbPool.query(
      'SELECT id, url FROM servers WHERE enabled = true'
    )
    for (const server of servers) {
      trapStreamService.connectToServer(server.id, server.url)
    }
    logger.info(`TrapStreamService: subscribed to ${servers.length} server SSE stream(s)`)
  } catch (err: any) {
    logger.error('TrapStreamService: failed to load servers:', err.message)
  }
}

import https from 'https'
import { Pool } from 'pg'
import { Response } from 'express'
import { getAgentForServer } from '../utils/certManager'
import { logger } from '../utils/logger'

export interface LiveTrap {
  server_id: string
  source_ip: string
  device_name: string
  trap_type: string
  trap_oid: string
  severity: string
  description: string
  timestamp: string
  varbinds: Record<string, unknown>
  resolved: boolean
}

type TrapKey = string // `${serverId}|${sourceIp}|${trapType}` or `...${trapType}|${ifIndex}`

const OID_IF_DESCR = '1.3.6.1.2.1.2.2.1.2.'

function extractIfIndex(varbinds?: Record<string, unknown>): string {
  if (!varbinds) return ''
  for (const oid of Object.keys(varbinds)) {
    if (oid.startsWith(OID_IF_DESCR)) return oid.slice(OID_IF_DESCR.length)
  }
  return ''
}

function trapKey(serverId: string, sourceIp: string, trapType: string, varbinds?: Record<string, unknown>): TrapKey {
  const ifIdx = extractIfIndex(varbinds)
  return ifIdx ? `${serverId}|${sourceIp}|${trapType}|${ifIdx}` : `${serverId}|${sourceIp}|${trapType}`
}

export class TrapStreamService {
  private activeTrapMap = new Map<TrapKey, LiveTrap>()
  private uiClients = new Set<Response>()

  constructor(private dbPool: Pool) {}

  /** Seed in-memory map from DB so clients connecting before any live event get real state */
  async loadInitialState(): Promise<void> {
    try {
      const { rows } = await this.dbPool.query(`
        SELECT server_id, source_ip, device_name, trap_type, trap_oid,
               severity, description, timestamp, varbinds
        FROM snmp_traps
        WHERE resolved = false
        ORDER BY timestamp DESC
        LIMIT 1000
      `)
      for (const row of rows) {
        const key = trapKey(row.server_id, row.source_ip, row.trap_type, row.varbinds || {})
        this.activeTrapMap.set(key, {
          server_id: row.server_id,
          source_ip: row.source_ip,
          device_name: row.device_name,
          trap_type: row.trap_type,
          trap_oid: row.trap_oid,
          severity: row.severity,
          description: row.description,
          timestamp: row.timestamp,
          varbinds: row.varbinds || {},
          resolved: false,
        })
      }
      logger.info(`TrapStreamService: loaded ${this.activeTrapMap.size} active traps from DB`)
    } catch (err: any) {
      logger.error('TrapStreamService: failed to load initial state:', err.message)
    }
  }

  getActiveTraps(): LiveTrap[] {
    return Array.from(this.activeTrapMap.values())
  }

  /** Register a UI SSE client and push current snapshot, then stream live updates */
  addClient(res: Response): void {
    res.setHeader('Content-Type', 'text/event-stream')
    res.setHeader('Cache-Control', 'no-cache')
    res.setHeader('Connection', 'keep-alive')
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.flushHeaders()

    const snapshot = this.getActiveTraps()
    res.write(`event: init\ndata: ${JSON.stringify(snapshot)}\n\n`)

    this.uiClients.add(res)
    logger.debug(`TrapStreamService: UI client connected (${this.uiClients.size} total)`)

    res.on('close', () => {
      this.uiClients.delete(res)
      logger.debug(`TrapStreamService: UI client disconnected (${this.uiClients.size} total)`)
    })
  }

  private broadcastToUI(eventName: string, data: unknown): void {
    const payload = `event: ${eventName}\ndata: ${JSON.stringify(data)}\n\n`
    for (const res of this.uiClients) {
      try {
        res.write(payload)
      } catch {
        this.uiClients.delete(res)
      }
    }
  }

  onTrapEvent(serverId: string, trap: any): void {
    const key = trapKey(serverId, trap.source_ip, trap.trap_type, trap.varbinds || {})
    const live: LiveTrap = {
      server_id: serverId,
      source_ip: trap.source_ip,
      device_name: trap.device_name || trap.source_ip,
      trap_type: trap.trap_type,
      trap_oid: trap.trap_oid || '',
      severity: trap.severity,
      description: trap.description || '',
      timestamp: trap.timestamp || new Date().toISOString(),
      varbinds: trap.varbinds || {},
      resolved: false,
    }
    this.activeTrapMap.set(key, live)
    this.broadcastToUI('trap_event', live)
    logger.debug(`TrapStreamService: trap_event ${trap.trap_type} from ${trap.source_ip}`)
  }

  onTrapResolve(serverId: string, data: { source_ip: string; trap_type: string }): void {
    // Remove all active traps for this device+trapType (any interface index)
    const prefix = `${serverId}|${data.source_ip}|${data.trap_type}`
    for (const key of [...this.activeTrapMap.keys()]) {
      if (key === prefix || key.startsWith(prefix + '|')) {
        this.activeTrapMap.delete(key)
      }
    }
    this.broadcastToUI('trap_resolve', { server_id: serverId, source_ip: data.source_ip, trap_type: data.trap_type })
    logger.debug(`TrapStreamService: trap_resolve ${data.trap_type} from ${data.source_ip}`)
  }

  /** Open a persistent SSE connection to a DCIM server and process trap events */
  connectToServer(serverId: string, serverUrl: string): void {
    const url = new URL('/api/v1/events', serverUrl)
    const tlsAgent = getAgentForServer(serverId)

    const attemptConnect = () => {
      logger.info(`TrapStreamService: connecting SSE ${url.href} for server ${serverId}`)

      const options: https.RequestOptions = {
        hostname: url.hostname,
        port: parseInt(url.port) || (url.protocol === 'https:' ? 443 : 80),
        path: url.pathname + (url.search || ''),
        method: 'GET',
        agent: tlsAgent,
        headers: { Accept: 'text/event-stream', Connection: 'keep-alive' },
      }

      const req = https.request(options, (res) => {
        if (res.statusCode !== 200) {
          logger.warn(`TrapStreamService: SSE returned HTTP ${res.statusCode} for server ${serverId}`)
          res.resume()
          setTimeout(attemptConnect, 30_000)
          return
        }

        logger.info(`TrapStreamService: SSE stream live for server ${serverId}`)
        let buf = ''

        res.on('data', (chunk: Buffer) => {
          buf += chunk.toString()
          const parts = buf.split('\n')
          buf = parts.pop() ?? ''

          let dataLine = ''
          for (const line of parts) {
            if (line.startsWith('data:')) {
              dataLine = line.slice(5).trim()
            } else if (line === '' && dataLine) {
              try {
                const msg = JSON.parse(dataLine)
                if (msg.type === 'trap_event' && msg.data) {
                  this.onTrapEvent(serverId, msg.data)
                } else if (msg.type === 'trap_resolve' && msg.data) {
                  this.onTrapResolve(serverId, msg.data)
                }
              } catch { /* ignore parse errors */ }
              dataLine = ''
            }
          }
        })

        res.on('end', () => {
          logger.warn(`TrapStreamService: SSE ended for server ${serverId}, reconnect in 10s`)
          // Resync from DB on reconnect to fill any gap while stream was down
          this.loadInitialState().then(() => setTimeout(attemptConnect, 10_000))
        })

        res.on('error', (err) => {
          logger.warn(`TrapStreamService: SSE stream error for server ${serverId}: ${err.message}`)
        })
      })

      req.on('error', (err) => {
        logger.warn(`TrapStreamService: connect failed for server ${serverId}: ${err.message}, retry in 30s`)
        setTimeout(attemptConnect, 30_000)
      })

      req.end()
    }

    attemptConnect()
  }
}

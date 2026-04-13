import { Pool } from 'pg'
import { HttpClient } from '../utils/httpClient'
import { getAgentForServer } from '../utils/certManager'
import { logger } from '../utils/logger'

export class DataSyncService {
  constructor(private dbPool: Pool) {}

  /**
   * Get a known agent_id for a server (needed for authenticated endpoints).
   * The Go server requires X-Agent-ID header for /metrics, /alerts, /snmp-metrics.
   */
  private async getAgentIdForServer(serverId: string): Promise<string | null> {
    try {
      const result = await this.dbPool.query(
        `SELECT agent_id FROM agents WHERE server_id = $1 LIMIT 1`,
        [serverId]
      )
      return result.rows.length > 0 ? result.rows[0].agent_id : null
    } catch {
      return null
    }
  }

  /**
   * Get ALL agent_ids for a server (for per-agent metric fetching).
   */
  private async getAllAgentIdsForServer(serverId: string): Promise<string[]> {
    try {
      const result = await this.dbPool.query(
        `SELECT agent_id FROM agents WHERE server_id = $1`,
        [serverId]
      )
      return result.rows.map((r: any) => r.agent_id)
    } catch {
      return []
    }
  }

  async syncAgentsFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 5000, agent)
      const response: any = await client.get('/agents')
      const agents = response.data || response

      if (!Array.isArray(agents)) {
        logger.warn(`Invalid agents response from ${serverId}`)
        return
      }

      // Upsert agents to database
      for (const agent of agents) {
        try {
          await this.dbPool.query(
            `
            INSERT INTO agents (server_id, agent_id, hostname, ip_address, status, certificate_cn, agent_group, approved, last_seen, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (server_id, agent_id)
            DO UPDATE SET
              hostname = EXCLUDED.hostname,
              ip_address = EXCLUDED.ip_address,
              status = EXCLUDED.status,
              certificate_cn = EXCLUDED.certificate_cn,
              agent_group = EXCLUDED.agent_group,
              approved = EXCLUDED.approved,
              last_seen = EXCLUDED.last_seen,
              updated_at = NOW()
          `,
            [
              serverId,
              agent.agent_id,
              agent.hostname,
              agent.ip_address,
              agent.status,
              agent.certificate_cn,
              agent.group,
              agent.approved,
              agent.last_seen || new Date(),
            ]
          )
        } catch (error) {
          logger.error(`Failed to upsert agent ${agent.agent_id}:`, error)
        }
      }

      logger.info(`Synced ${agents.length} agents from server ${serverId}`)
    } catch (error: any) {
      logger.error(`Failed to sync agents from ${serverId}:`, error.message)
    }
  }

  async syncMetricsFromServer(serverId: string, serverUrl: string, agentId?: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 15000, agent)

      // Get agent IDs for this server — we need to pass X-Agent-ID header
      const agentIds = agentId ? [agentId] : await this.getAllAgentIdsForServer(serverId)
      if (agentIds.length === 0) {
        logger.debug(`No agents known for server ${serverId}, skipping metrics sync`)
        return
      }

      // Fetch metrics per agent using /metrics?agent_id=X endpoint
      // (the /agents/{id}/metrics endpoint has a PostgreSQL bug on the Go server)
      // We request 7d window since the Go server defaults to 24h which may miss older data
      let allMetrics: any[] = []
      for (const aid of agentIds) {
        try {
          const params = new URLSearchParams()
          params.append('agent_id', aid)
          params.append('time_range', '7d')
          params.append('limit', '1000')
          const url = `/metrics?${params.toString()}`
          const response: any = await client.get(url, {
            headers: { 'X-Agent-ID': aid },
          })
          const data = response.data || response
          if (Array.isArray(data)) {
            allMetrics = allMetrics.concat(data)
          }
        } catch (err: any) {
          logger.debug(`Failed to fetch metrics for agent ${aid}: ${err.message}`)
        }
      }

      const metrics = allMetrics

      if (!Array.isArray(metrics) || metrics.length === 0) {
        return
      }

      // Batch insert metrics
      const values: string[] = []
      const placeholders: string[] = []
      const params_array: any[] = []
      let paramIndex = 1

      for (const m of metrics) {
        if (!m.agent_id || !m.metric_type || m.value === undefined || !m.timestamp) {
          continue
        }

        placeholders.push(
          `($${paramIndex}, $${paramIndex + 1}, $${paramIndex + 2}, $${paramIndex + 3}, $${paramIndex + 4}, $${paramIndex + 5})`
        )
        params_array.push(serverId, m.agent_id, m.metric_type, m.value, m.unit || '', m.timestamp)
        paramIndex += 6
      }

      if (placeholders.length > 0) {
        const query = `
          INSERT INTO metrics (server_id, agent_id, metric_type, value, unit, timestamp)
          VALUES ${placeholders.join(', ')}
          ON CONFLICT DO NOTHING
        `
        await this.dbPool.query(query, params_array)
      }

      logger.info(`Synced ${metrics.length} metrics from server ${serverId}`)
    } catch (error: any) {
      logger.error(`Failed to sync metrics from ${serverId}:`, error.message)
    }
  }

  async syncAlertsFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 15000, agent)

      // Fetch alerts per agent so every agent gets its fair share
      const agentIds = await this.getAllAgentIdsForServer(serverId)
      if (agentIds.length === 0) {
        logger.debug(`No agents known for server ${serverId}, skipping alerts sync`)
        return
      }

      let allAlerts: any[] = []
      for (const aid of agentIds) {
        try {
          const params = new URLSearchParams()
          params.append('agent_id', aid)
          params.append('time_range', '7d')
          params.append('limit', '1000')
          const url = `/alerts?${params.toString()}`
          const response: any = await client.get(url, {
            headers: { 'X-Agent-ID': aid },
          })
          const data = response.data || response
          if (Array.isArray(data)) {
            allAlerts = allAlerts.concat(data)
          }
        } catch (err: any) {
          logger.debug(`Failed to fetch alerts for agent ${aid}: ${err.message}`)
        }
      }

      if (allAlerts.length === 0) {
        return
      }

      for (const alert of allAlerts) {
        try {
          await this.dbPool.query(
            `
            INSERT INTO alerts (server_id, agent_id, severity, message, metric_type, threshold_value, actual_value, resolved, resolved_at, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT DO NOTHING
          `,
            [
              serverId,
              alert.agent_id,
              alert.severity,
              alert.message,
              alert.metric_type,
              alert.threshold,
              alert.value,
              alert.resolved || false,
              alert.resolved_at,
              alert.timestamp || new Date(),
            ]
          )
        } catch (error) {
          logger.error(`Failed to insert alert:`, error)
        }
      }

      logger.info(`Synced ${allAlerts.length} alerts from server ${serverId}`)
    } catch (error: any) {
      logger.error(`Failed to sync alerts from ${serverId}:`, error.message)
    }
  }

  async syncSNMPMetricsFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 5000, agent)

      // Get a known agent_id for authenticated access
      const agentId = await this.getAgentIdForServer(serverId)
      if (!agentId) {
        logger.debug(`No agents known for server ${serverId}, skipping SNMP sync`)
        return
      }

      const response: any = await client.get('/snmp-metrics?time_range=7d&limit=1000', {
        headers: { 'X-Agent-ID': agentId },
      })
      const metrics = response.data || response

      if (!Array.isArray(metrics) || metrics.length === 0) {
        return
      }

      // Batch insert SNMP metrics
      const placeholders: string[] = []
      const params_array: any[] = []
      let paramIndex = 1

      for (const m of metrics) {
        if (!m.agent_id || !m.device_name || !m.metric_name) {
          continue
        }

        placeholders.push(
          `($${paramIndex}, $${paramIndex + 1}, $${paramIndex + 2}, $${paramIndex + 3}, $${paramIndex + 4}, $${paramIndex + 5}, $${paramIndex + 6}, $${paramIndex + 7})`
        )
        params_array.push(
          serverId,
          m.agent_id,
          m.device_name,
          m.device_host || m.device_ip,
          m.metric_name,
          String(m.value ?? m.metric_value ?? ''),
          m.oid,
          m.timestamp || new Date()
        )
        paramIndex += 8
      }

      if (placeholders.length > 0) {
        const query = `
          INSERT INTO snmp_metrics (server_id, agent_id, device_name, device_ip, metric_name, metric_value, oid, timestamp)
          VALUES ${placeholders.join(', ')}
          ON CONFLICT DO NOTHING
        `
        await this.dbPool.query(query, params_array)
      }

      logger.info(`Synced ${metrics.length} SNMP metrics from server ${serverId}`)
    } catch (error: any) {
      logger.error(`Failed to sync SNMP metrics from ${serverId}:`, error.message)
    }
  }

  async syncTrapsFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 10000, agent)

      // Get a known agent_id for authenticated access
      const agentId = await this.getAgentIdForServer(serverId)
      if (!agentId) {
        logger.debug(`No agents known for server ${serverId}, skipping traps sync`)
        return
      }

      // Fetch last 500 traps — server returns latest-first; we use ON CONFLICT to dedup
      const response: any = await client.get('/traps?limit=500', {
        headers: { 'X-Agent-ID': agentId },
      })
      const traps: any[] = response.data || response

      if (!Array.isArray(traps) || traps.length === 0) return

      let inserted = 0
      for (const trap of traps) {
        try {
          const result = await this.dbPool.query(
            `INSERT INTO snmp_traps
               (server_id, timestamp, source_ip, device_name, trap_type, trap_oid,
                severity, varbinds, description, resolved, resolved_at)
             VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
             ON CONFLICT DO NOTHING`,
            [
              serverId,
              trap.timestamp || new Date(),
              trap.source_ip,
              trap.device_name || '',
              trap.trap_type,
              trap.trap_oid,
              trap.severity,
              trap.varbinds ? JSON.stringify(trap.varbinds) : null,
              trap.description || '',
              trap.resolved || false,
              trap.resolved_at || null,
            ]
          )
          if (result.rowCount && result.rowCount > 0) inserted++
        } catch {
          // skip individual insert errors (constraint violations etc.)
        }
      }

      if (inserted > 0) {
        logger.info(`Synced ${inserted} new traps from server ${serverId}`)
      }
    } catch (error: any) {
      logger.error(`Failed to sync traps from ${serverId}:`, error.message)
    }
  }

  async syncTopologyLinksFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const agent = getAgentForServer(serverId)
      const client = new HttpClient(serverUrl, 10000, agent)

      // Get a known agent_id for authenticated access
      const agentId = await this.getAgentIdForServer(serverId)
      if (!agentId) {
        logger.debug(`No agents known for server ${serverId}, skipping topology links sync`)
        return
      }

      const response: any = await client.get('/topology/links', {
        headers: { 'X-Agent-ID': agentId },
      })
      const links: any[] = response.data || response

      if (!Array.isArray(links) || links.length === 0) return

      let upserted = 0
      for (const link of links) {
        try {
          const result = await this.dbPool.query(
            `INSERT INTO topology_links (server_id, source_ip, source_name, source_depth, source_port, target_ip, target_name, target_depth, target_port, last_seen)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
             ON CONFLICT (server_id, source_ip, target_ip)
             DO UPDATE SET source_name  = EXCLUDED.source_name,
                           source_depth = EXCLUDED.source_depth,
                           source_port  = EXCLUDED.source_port,
                           target_name  = EXCLUDED.target_name,
                           target_depth = EXCLUDED.target_depth,
                           target_port  = EXCLUDED.target_port,
                           last_seen    = EXCLUDED.last_seen`,
            [
              serverId,
              link.source_ip,
              link.source_name || '',
              link.source_depth ?? 0,
              link.source_port ?? 0,
              link.target_ip,
              link.target_name || '',
              link.target_depth ?? 0,
              link.target_port || '',
              link.last_seen || new Date(),
            ]
          )
          if (result.rowCount && result.rowCount > 0) upserted++
        } catch {
          // skip individual errors
        }
      }

      if (upserted > 0) {
        logger.info(`Synced ${upserted} topology links from server ${serverId}`)
      }
    } catch (error: any) {
      logger.error(`Failed to sync topology links from ${serverId}:`, error.message)
    }
  }

}

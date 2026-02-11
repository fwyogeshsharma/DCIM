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
      const client = new HttpClient(serverUrl, 5000, agent)

      // Get a known agent_id for authenticated access
      const agentId = await this.getAgentIdForServer(serverId)
      if (!agentId) {
        logger.debug(`No agents known for server ${serverId}, skipping alerts sync`)
        return
      }

      const response: any = await client.get('/alerts?time_range=7d&limit=1000', {
        headers: { 'X-Agent-ID': agentId },
      })
      const alerts = response.data || response

      if (!Array.isArray(alerts)) {
        return
      }

      for (const alert of alerts) {
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

      logger.info(`Synced ${alerts.length} alerts from server ${serverId}`)
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
}

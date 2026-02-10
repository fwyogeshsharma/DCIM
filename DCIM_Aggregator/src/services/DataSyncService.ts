import { Pool } from 'pg'
import { HttpClient } from '../utils/httpClient'
import { logger } from '../utils/logger'

export class DataSyncService {
  constructor(private dbPool: Pool) {}

  async syncAgentsFromServer(serverId: string, serverUrl: string): Promise<void> {
    try {
      const client = new HttpClient(serverUrl)
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
              agent.agent_group,
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
      const client = new HttpClient(serverUrl)
      const params = new URLSearchParams()
      if (agentId) params.append('agent_id', agentId)
      params.append('limit', '1000')

      const url = `/metrics?${params.toString()}`
      const response: any = await client.get(url)
      const metrics = response.data || response

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
      const client = new HttpClient(serverUrl)
      const response: any = await client.get('/alerts')
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
              alert.threshold_value,
              alert.actual_value,
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
      const client = new HttpClient(serverUrl)
      const response: any = await client.get('/snmp/metrics')
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
          m.device_ip,
          m.metric_name,
          m.metric_value,
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

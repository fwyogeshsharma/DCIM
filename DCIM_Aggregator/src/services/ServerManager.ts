import { Pool } from 'pg'
import { logger } from '../utils/logger'

export interface ServerConfig {
  id?: string
  name: string
  url: string
  enabled?: boolean
  auth_type?: string
  auth_credentials?: any
  metadata?: any
}

export class ServerManager {
  constructor(private dbPool: Pool) {}

  async getAllServers(): Promise<ServerConfig[]> {
    const { rows } = await this.dbPool.query(
      'SELECT * FROM servers ORDER BY name'
    )
    return rows
  }

  async getEnabledServers(): Promise<ServerConfig[]> {
    const { rows } = await this.dbPool.query(
      'SELECT * FROM servers WHERE enabled = true ORDER BY name'
    )
    return rows
  }

  async getServerById(id: string): Promise<ServerConfig | null> {
    const { rows } = await this.dbPool.query(
      'SELECT * FROM servers WHERE id = $1',
      [id]
    )
    return rows[0] || null
  }

  async createServer(server: ServerConfig): Promise<ServerConfig> {
    const { rows } = await this.dbPool.query(
      `
      INSERT INTO servers (name, url, enabled, auth_type, auth_credentials, metadata)
      VALUES ($1, $2, $3, $4, $5, $6)
      RETURNING *
    `,
      [
        server.name,
        server.url,
        server.enabled !== false,
        server.auth_type,
        server.auth_credentials ? JSON.stringify(server.auth_credentials) : null,
        server.metadata ? JSON.stringify(server.metadata) : null,
      ]
    )
    logger.info(`Created server: ${server.name}`)
    return rows[0]
  }

  async updateServer(id: string, updates: Partial<ServerConfig>): Promise<ServerConfig> {
    const fields: string[] = []
    const values: any[] = []
    let paramIndex = 1

    if (updates.name !== undefined) {
      fields.push(`name = $${paramIndex++}`)
      values.push(updates.name)
    }
    if (updates.url !== undefined) {
      fields.push(`url = $${paramIndex++}`)
      values.push(updates.url)
    }
    if (updates.enabled !== undefined) {
      fields.push(`enabled = $${paramIndex++}`)
      values.push(updates.enabled)
    }
    if (updates.auth_type !== undefined) {
      fields.push(`auth_type = $${paramIndex++}`)
      values.push(updates.auth_type)
    }
    if (updates.auth_credentials !== undefined) {
      fields.push(`auth_credentials = $${paramIndex++}`)
      values.push(JSON.stringify(updates.auth_credentials))
    }
    if (updates.metadata !== undefined) {
      fields.push(`metadata = $${paramIndex++}`)
      values.push(JSON.stringify(updates.metadata))
    }

    fields.push(`updated_at = NOW()`)
    values.push(id)

    const { rows } = await this.dbPool.query(
      `UPDATE servers SET ${fields.join(', ')} WHERE id = $${paramIndex} RETURNING *`,
      values
    )

    logger.info(`Updated server: ${id}`)
    return rows[0]
  }

  async deleteServer(id: string): Promise<void> {
    await this.dbPool.query('DELETE FROM servers WHERE id = $1', [id])
    logger.info(`Deleted server: ${id}`)
  }

  async toggleServerStatus(id: string, enabled: boolean): Promise<void> {
    await this.dbPool.query(
      'UPDATE servers SET enabled = $1, updated_at = NOW() WHERE id = $2',
      [enabled, id]
    )
    logger.info(`Server ${id} ${enabled ? 'enabled' : 'disabled'}`)
  }
}

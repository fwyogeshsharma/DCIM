import { Router } from 'express'
import { Pool } from 'pg'
import multer from 'multer'
import { ServerManager } from '../../services/ServerManager'
import { CacheService } from '../../services/CacheService'
import axios from 'axios'
import { httpsAgent } from '../../utils/httpClient'
import {
  saveServerCerts,
  deleteServerCerts,
  getAgentForServer,
  serverHasCerts,
  ensureCertsBaseDir,
} from '../../utils/certManager'

const upload = multer({ storage: multer.memoryStorage() })
const certFields = upload.fields([
  { name: 'caCert', maxCount: 1 },
  { name: 'clientCert', maxCount: 1 },
  { name: 'clientKey', maxCount: 1 },
])

export function createServersRouter(dbPool: Pool, cacheService: CacheService): Router {
  const router = Router()
  const serverManager = new ServerManager(dbPool)

  ensureCertsBaseDir()

  // Get all servers
  router.get('/', async (req, res) => {
    try {
      const servers = await serverManager.getAllServers()

      // Add health status and cert info to each server
      const serversWithHealth = await Promise.all(
        servers.map(async (server) => {
          const health = await cacheService.getServerHealth(server.id!)
          const hasCerts = serverHasCerts(server.id!)
          return { ...server, health, hasCerts }
        })
      )

      res.json({
        success: true,
        data: serversWithHealth,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Aggregated server health summary (must be before /:id)
  router.get('/health/summary', async (req, res) => {
    try {
      const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 5, 1), 50)

      // Get all enabled servers (lightweight columns only)
      const { rows: servers } = await dbPool.query(
        `SELECT id, name, url, metadata FROM servers WHERE enabled = true ORDER BY name`
      )

      const serverIds = servers.map((s: any) => s.id)
      const healthMap = await cacheService.getMultipleServerHealth(serverIds)

      let healthy = 0
      let offline = 0
      let tls_error = 0
      let unknown = 0
      const needsAttention: any[] = []

      for (const server of servers) {
        const health = healthMap[server.id]
        const status = health?.status || 'unknown'

        if (status === 'healthy') {
          healthy++
        } else if (status === 'tls_error') {
          tls_error++
          needsAttention.push({
            id: server.id,
            name: server.name,
            url: server.url,
            color: server.metadata?.color || '#3b82f6',
            status,
            error: health?.error || null,
            responseTime: health?.responseTime || null,
          })
        } else if (status === 'offline') {
          offline++
          needsAttention.push({
            id: server.id,
            name: server.name,
            url: server.url,
            color: server.metadata?.color || '#3b82f6',
            status,
            error: health?.error || null,
            responseTime: null,
          })
        } else {
          unknown++
          needsAttention.push({
            id: server.id,
            name: server.name,
            url: server.url,
            color: server.metadata?.color || '#3b82f6',
            status: 'unknown',
            error: null,
            responseTime: null,
          })
        }
      }

      // Sort: offline first, then tls_error, then unknown
      const statusOrder: Record<string, number> = { offline: 0, tls_error: 1, unknown: 2 }
      needsAttention.sort((a, b) => (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9))

      res.json({
        success: true,
        data: {
          total: servers.length,
          healthy,
          offline,
          tls_error,
          unknown,
          needs_attention: needsAttention.slice(0, limit),
        },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Get server by ID
  router.get('/:id', async (req, res) => {
    try {
      const server = await serverManager.getServerById(req.params.id)
      if (!server) {
        return res.status(404).json({ success: false, error: 'Server not found' })
      }

      const health = await cacheService.getServerHealth(server.id!)
      const hasCerts = serverHasCerts(server.id!)
      res.json({
        success: true,
        data: { ...server, health, hasCerts },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Create new server (supports multipart for cert upload)
  router.post('/', certFields, async (req, res) => {
    try {
      // Parse body — may come as JSON string in 'data' field (FormData) or as plain JSON
      let body = req.body
      if (typeof body.data === 'string') {
        body = JSON.parse(body.data)
      }

      const { name, url, enabled, auth_type, auth_credentials, metadata } = body

      if (!name || !url) {
        return res.status(400).json({
          success: false,
          error: 'Name and URL are required',
        })
      }

      const server = await serverManager.createServer({
        name,
        url,
        enabled,
        auth_type,
        auth_credentials,
        metadata,
      })

      // Save uploaded cert files
      const files = req.files as { [fieldname: string]: Express.Multer.File[] } | undefined
      if (files) {
        const certBuffers: { caCert?: Buffer; clientCert?: Buffer; clientKey?: Buffer } = {}
        if (files.caCert?.[0]) certBuffers.caCert = files.caCert[0].buffer
        if (files.clientCert?.[0]) certBuffers.clientCert = files.clientCert[0].buffer
        if (files.clientKey?.[0]) certBuffers.clientKey = files.clientKey[0].buffer

        if (certBuffers.caCert || certBuffers.clientCert || certBuffers.clientKey) {
          saveServerCerts(server.id!, certBuffers)
        }
      }

      res.status(201).json({
        success: true,
        data: { ...server, hasCerts: serverHasCerts(server.id!) },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Update server (supports multipart for cert upload)
  router.put('/:id', certFields, async (req, res) => {
    try {
      let body = req.body
      if (typeof body.data === 'string') {
        body = JSON.parse(body.data)
      }

      const server = await serverManager.updateServer(req.params.id, body)

      // Save uploaded cert files (overwrites existing)
      const files = req.files as { [fieldname: string]: Express.Multer.File[] } | undefined
      if (files) {
        const certBuffers: { caCert?: Buffer; clientCert?: Buffer; clientKey?: Buffer } = {}
        if (files.caCert?.[0]) certBuffers.caCert = files.caCert[0].buffer
        if (files.clientCert?.[0]) certBuffers.clientCert = files.clientCert[0].buffer
        if (files.clientKey?.[0]) certBuffers.clientKey = files.clientKey[0].buffer

        if (certBuffers.caCert || certBuffers.clientCert || certBuffers.clientKey) {
          saveServerCerts(req.params.id, certBuffers)
        }
      }

      res.json({
        success: true,
        data: { ...server, hasCerts: serverHasCerts(req.params.id) },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Delete server
  router.delete('/:id', async (req, res) => {
    try {
      deleteServerCerts(req.params.id)
      await serverManager.deleteServer(req.params.id)
      res.json({
        success: true,
        message: 'Server deleted',
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Test server connection (uses per-server certs)
  router.get('/:id/health', async (req, res) => {
    try {
      const server = await serverManager.getServerById(req.params.id)
      if (!server) {
        return res.status(404).json({ success: false, error: 'Server not found' })
      }

      const agent = getAgentForServer(server.id!)

      const startTime = Date.now()
      try {
        const baseUrl = server.url.replace(/\/api\/v\d+\/?$/, '').replace('localhost', '127.0.0.1')
        await axios.get(`${baseUrl}/health`, { timeout: 5000, httpsAgent: agent })
        const responseTime = Date.now() - startTime

        const health = {
          status: 'healthy',
          responseTime,
          timestamp: new Date().toISOString(),
        }

        await cacheService.setServerHealth(server.id!, health)

        res.json({
          success: true,
          data: health,
        })
      } catch (error: any) {
        const health = {
          status: 'offline',
          error: error.message || error.errors?.map((e: any) => e.message).join('; ') || error.code || 'connection failed',
          timestamp: new Date().toISOString(),
        }

        await cacheService.setServerHealth(server.id!, health)

        res.json({
          success: true,
          data: health,
        })
      }
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Toggle server status
  router.post('/:id/toggle', async (req, res) => {
    try {
      const { enabled } = req.body
      await serverManager.toggleServerStatus(req.params.id, enabled)
      res.json({
        success: true,
        message: `Server ${enabled ? 'enabled' : 'disabled'}`,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  return router
}

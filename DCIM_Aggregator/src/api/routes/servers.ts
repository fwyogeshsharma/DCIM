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
        const baseUrl = server.url.replace(/\/api\/v\d+\/?$/, '')
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
          error: error.message,
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

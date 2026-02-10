import { Router } from 'express'
import { Pool } from 'pg'
import { ServerManager } from '../../services/ServerManager'
import { CacheService } from '../../services/CacheService'
import axios from 'axios'

export function createServersRouter(dbPool: Pool, cacheService: CacheService): Router {
  const router = Router()
  const serverManager = new ServerManager(dbPool)

  // Get all servers
  router.get('/', async (req, res) => {
    try {
      const servers = await serverManager.getAllServers()

      // Add health status to each server
      const serversWithHealth = await Promise.all(
        servers.map(async (server) => {
          const health = await cacheService.getServerHealth(server.id!)
          return { ...server, health }
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
      res.json({
        success: true,
        data: { ...server, health },
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Create new server
  router.post('/', async (req, res) => {
    try {
      const { name, url, enabled, auth_type, auth_credentials, metadata } = req.body

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

      res.status(201).json({
        success: true,
        data: server,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Update server
  router.put('/:id', async (req, res) => {
    try {
      const server = await serverManager.updateServer(req.params.id, req.body)
      res.json({
        success: true,
        data: server,
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Delete server
  router.delete('/:id', async (req, res) => {
    try {
      await serverManager.deleteServer(req.params.id)
      res.json({
        success: true,
        message: 'Server deleted',
      })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message })
    }
  })

  // Test server connection
  router.get('/:id/health', async (req, res) => {
    try {
      const server = await serverManager.getServerById(req.params.id)
      if (!server) {
        return res.status(404).json({ success: false, error: 'Server not found' })
      }

      const startTime = Date.now()
      try {
        await axios.get(`${server.url}/agents`, { timeout: 5000 })
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

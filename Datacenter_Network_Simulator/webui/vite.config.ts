import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import http from 'http'

// Disable keep-alive between Vite proxy and uvicorn — fresh TCP per request.
const proxyAgent = new http.Agent({ keepAlive: false })

// Force Connection: close on browser↔Vite + Vite↔uvicorn hops. Windows
// AddIPAddress churn breaks idle loopback keep-alive sockets; reusing one
// triggers a ~2 min TCP retransmission timeout. Fresh sockets avoid this.
function forceClose(proxy: import('http-proxy').default) {
  proxy.on('proxyReq', (proxyReq) => {
    try { proxyReq.setHeader('Connection', 'close') } catch { /* noop */ }
  })
  proxy.on('proxyRes', (proxyRes, _req, res) => {
    proxyRes.headers['connection'] = 'close'
    try { (res as import('http').ServerResponse).setHeader('Connection', 'close') } catch { /* noop */ }
  })
}

export default defineConfig({
  plugins: [react()],
  base: '/web/',
  server: {
    port: 5173,
    proxy: {
      '/assets': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        agent: proxyAgent,
      },
      '/api/events': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        agent: proxyAgent,
        configure: (proxy) => {
          forceClose(proxy)
          proxy.on('error', (err: NodeJS.ErrnoException) => {
            if (err.code === 'ECONNRESET' || err.code === 'ECONNREFUSED') return
            console.error('[proxy /api/events]', err.message)
          })
        },
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        agent: proxyAgent,
        proxyTimeout: 15000,
        configure: (proxy) => {
          forceClose(proxy)
          proxy.on('error', (err: NodeJS.ErrnoException, _req, res) => {
            const transient = err.code === 'ECONNRESET' || err.code === 'ECONNREFUSED'
              || err.code === 'ETIMEDOUT'
              || (err.message || '').includes('timeout')
              || (err.message || '').includes('ECONNRESET')
            if (transient) {
              try {
                if (!res.headersSent) {
                  (res as import('http').ServerResponse).writeHead(503, {
                    'Content-Type': 'application/json',
                    'Connection': 'close',
                  })
                  res.end(JSON.stringify({ detail: 'proxy_connection_reset' }))
                }
              } catch { /* response already finished */ }
              return
            }
            console.error('[proxy /api]', err.message)
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})

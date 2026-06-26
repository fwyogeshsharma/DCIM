import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  esbuild: {
    minifyIdentifiers: false,
  },
  server: {
    port: 5173,
    proxy: {
      // ML service — direct proxy to avoid double-hop through proxy-server.js.
      // Must come before the generic /api entry so Vite matches it first.
      '/api/v1/ml': {
        target: process.env.ML_URL || 'http://localhost:5002',
        changeOrigin: true,
        rewrite: (p) => {
          const rest = p.replace(/^\/api\/v1\/ml/, '')
          return rest === '/health' || rest === '' ? '/health' : `/api/v1${rest}`
        },
      },
      '/api': { target: process.env.PROXY_URL || 'http://localhost:3001', changeOrigin: true },
      '/orchestrator': { target: process.env.ORCHESTRATOR_URL || 'http://localhost:8099', changeOrigin: true, rewrite: (p) => p.replace(/^\/orchestrator/, '') },
    },
  },
  preview: {
    port: 5173,
    allowedHosts: true,
    proxy: {
      // Same ML direct proxy for the production container (vite preview mode).
      // In Docker, ML_URL is set to http://dcim-ml:8000 via docker-compose env.
      '/api/v1/ml': {
        target: process.env.ML_URL || 'http://localhost:5002',
        changeOrigin: true,
        rewrite: (p) => {
          const rest = p.replace(/^\/api\/v1\/ml/, '')
          return rest === '/health' || rest === '' ? '/health' : `/api/v1${rest}`
        },
      },
      '/api': { target: process.env.PROXY_URL || 'http://localhost:3001', changeOrigin: true },
      '/orchestrator': { target: process.env.ORCHESTRATOR_URL || 'http://localhost:8099', changeOrigin: true, rewrite: (p) => p.replace(/^\/orchestrator/, '') },
    },
  },
})

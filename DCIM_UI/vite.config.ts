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
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.PROXY_URL || 'http://localhost:3001', changeOrigin: true },
      '/orchestrator': { target: process.env.ORCHESTRATOR_URL || 'http://localhost:8099', changeOrigin: true, rewrite: (p) => p.replace(/^\/orchestrator/, '') },
    },
  },
  preview: {
    port: 5173,
    allowedHosts: 'all',
    proxy: {
      '/api': { target: process.env.PROXY_URL || 'http://localhost:3001', changeOrigin: true },
      '/orchestrator': { target: process.env.ORCHESTRATOR_URL || 'http://localhost:8099', changeOrigin: true, rewrite: (p) => p.replace(/^\/orchestrator/, '') },
    },
  },
})

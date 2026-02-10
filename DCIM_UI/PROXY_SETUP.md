# DCIM UI Proxy Server Setup

## Overview

The DCIM UI uses a proxy server to handle mutual TLS (mTLS) authentication with the DCIM server. This is necessary because browsers cannot send client certificates programmatically.

## Architecture

```
Browser (HTTP) → Proxy Server (HTTP:3001) → DCIM Server (HTTPS:8443 with mTLS)
```

- **Browser**: Connects to UI via HTTP on port 5173 (Vite dev server)
- **Proxy Server**: Runs on port 3001, handles mTLS authentication
- **DCIM Server**: Runs on port 8443, requires client certificates

## Quick Start

### 1. Install Dependencies

```bash
npm install
```

This installs:
- `express`: Web server for the proxy
- `cors`: CORS middleware
- `concurrently`: Run multiple processes

### 2. Configure Environment

The `.env` file is already configured to use the proxy:

```env
VITE_API_URL=http://localhost:3001/api/v1
```

### 3. Start Everything

Run both the proxy server and the UI dev server:

```bash
npm run dev:full
```

This starts:
- ✅ Proxy server on http://localhost:3001
- ✅ UI dev server on http://localhost:5173

Or run them separately:

```bash
# Terminal 1: Start the proxy server
npm run proxy

# Terminal 2: Start the UI dev server
npm run dev
```

## Certificate Setup

The proxy server requires client certificates to authenticate with the DCIM server. Certificates should be placed in `certs/`:

```
DCIM_UI/certs/
├── client.crt  # Client certificate
├── client.key  # Client private key
└── ca.crt      # CA certificate
```

These certificates are already present in your setup.

## Configuration

The proxy server can be configured in `proxy-server.js`:

```javascript
const CONFIG = {
  proxy: {
    port: 3001,        // Proxy server port
    host: '0.0.0.0'    // Listen on all interfaces
  },
  dcim: {
    host: 'localhost', // DCIM server host
    port: 8443,        // DCIM server port
    baseUrl: '/api/v1' // API base path
  },
  certs: {
    clientCert: './certs/client.crt',
    clientKey: './certs/client.key',
    ca: './certs/ca.crt'
  }
}
```

## Health Check

Check if the proxy server is running:

```bash
curl http://localhost:3001/health
```

Expected response:
```json
{
  "status": "ok",
  "proxy": "running",
  "dcimServer": "localhost:8443",
  "certificates": {
    "loaded": true,
    "clientCert": "./certs/client.crt",
    "ca": "./certs/ca.crt"
  }
}
```

## Troubleshooting

### Proxy server won't start

1. **Check if port 3001 is already in use**:
   ```bash
   netstat -ano | findstr :3001
   ```

2. **Check if certificates exist**:
   ```bash
   ls certs/
   ```
   Should show: `client.crt`, `client.key`, `ca.crt`

3. **Check proxy server logs** for detailed error messages

### UI can't connect to API

1. **Verify proxy server is running**:
   ```bash
   curl http://localhost:3001/health
   ```

2. **Check .env file**:
   ```env
   VITE_API_URL=http://localhost:3001/api/v1
   ```

3. **Check browser console** for network errors

4. **Verify DCIM server is running** on port 8443:
   ```bash
   curl -k https://localhost:8443/api/v1/health
   ```

### Certificate errors

If you see certificate verification errors:

1. **Check certificate paths** in `proxy-server.js`
2. **Verify certificates are valid**:
   ```bash
   openssl x509 -in certs/client.crt -text -noout
   ```
3. **Check certificate permissions** (should be readable)

### CORS errors

If you see CORS errors in the browser console:

1. The proxy server has CORS enabled for all origins in development
2. Check that requests are going to `http://localhost:3001/api/v1`
3. Verify the proxy server is running and healthy

## Topology Not Showing

If the topology page is blank or not showing:

1. **Check if agents data is loading**:
   - Open browser DevTools (F12)
   - Go to Network tab
   - Look for request to `/api/v1/agents`
   - Check if it returns data

2. **Check browser console for errors**:
   - D3.js errors
   - API fetch errors
   - React rendering errors

3. **Verify agents are registered**:
   ```bash
   curl http://localhost:3001/api/v1/agents
   ```

4. **Try refreshing the page** after ensuring proxy and DCIM server are running

## Development Workflow

### Recommended: Use npm run dev:full

This is the easiest way to develop:

```bash
npm run dev:full
```

This single command starts both the proxy server and the UI dev server with colored output.

### Manual Setup

If you prefer to run services separately:

1. Start DCIM Server (in another terminal):
   ```bash
   cd ../DCIM_Server
   python run_server.py
   ```

2. Start Proxy Server:
   ```bash
   cd DCIM_UI
   npm run proxy
   ```

3. Start UI Dev Server:
   ```bash
   cd DCIM_UI
   npm run dev
   ```

4. Open browser: http://localhost:5173

## Production Deployment

For production, you'll want to:

1. **Build the UI**:
   ```bash
   npm run build
   ```

2. **Serve the built files** with nginx or similar

3. **Run proxy server** as a service:
   ```bash
   # Install pm2
   npm install -g pm2

   # Start proxy server
   pm2 start proxy-server.js --name dcim-proxy

   # Save process list
   pm2 save

   # Setup startup script
   pm2 startup
   ```

4. **Configure reverse proxy** (nginx) to handle:
   - Static files → Built UI files
   - `/api/v1/*` → Proxy server (port 3001)

## Scripts Reference

| Command | Description |
|---------|-------------|
| `npm run dev` | Start UI dev server only (port 5173) |
| `npm run proxy` | Start proxy server only (port 3001) |
| `npm run dev:full` | Start both proxy and UI dev server |
| `npm run build` | Build UI for production |
| `npm run preview` | Preview production build |
| `npm run lint` | Run ESLint |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:3001/api/v1` | API endpoint for the UI |
| `VITE_AI_API_URL` | `http://localhost:5000/api` | AI service endpoint |
| `VITE_OPENAI_API_KEY` | - | OpenAI API key (optional) |

## Notes

- The proxy server runs on **HTTP** (not HTTPS) because it's for local development
- The proxy server handles all mTLS complexity
- The UI connects to the proxy via simple HTTP requests
- All API requests are automatically forwarded to the DCIM server with proper authentication

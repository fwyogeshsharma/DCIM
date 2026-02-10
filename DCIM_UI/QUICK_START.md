# DCIM UI Quick Start Guide

## 🚀 Quick Start (TL;DR)

```bash
# Option 1: Using npm script (Recommended)
npm run dev:full

# Option 2: Using batch file (Windows)
.\start-dev.bat

# Option 3: Using PowerShell (Windows)
.\start-dev.ps1
```

Then open your browser to: **http://localhost:5173**

That's it! The proxy server will handle mTLS authentication automatically.

---

## 📋 What's Running?

When you run `npm run dev:full`, these services start:

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| **UI Dev Server** | 5173 | http://localhost:5173 | React frontend (what you see) |
| **Proxy Server** | 3001 | http://localhost:3001 | Handles mTLS to DCIM Server |
| **DCIM Server** | 8443 | https://localhost:8443 | Backend API (should already be running) |

## 🔍 How It Works

```
┌─────────┐      HTTP       ┌───────────┐     HTTPS+mTLS    ┌──────────────┐
│ Browser │ ──────────────> │   Proxy   │ ────────────────> │ DCIM Server  │
│ (5173)  │                 │  (3001)   │                   │   (8443)     │
└─────────┘                 └───────────┘                   └──────────────┘
```

1. Your browser connects to the UI at `http://localhost:5173`
2. The UI makes API calls to `http://localhost:3001/api/v1`
3. The proxy server forwards requests to `https://localhost:8443/api/v1` **with client certificates**
4. DCIM server authenticates using mTLS and responds
5. Proxy forwards the response back to your browser

## ✅ Prerequisites

### 1. DCIM Server Must Be Running

Make sure the DCIM server is running on port 8443:

```bash
# Check if server is running
netstat -ano | findstr :8443

# If not running, start it
cd ..\DCIM_Server
python run_server.py
```

### 2. Certificates Must Exist

Certificates should be in `certs/` directory:
- ✅ `client.crt` - Client certificate
- ✅ `client.key` - Client private key
- ✅ `ca.crt` - CA certificate

These are already set up in your project.

### 3. Dependencies Installed

```bash
npm install
```

## 🎯 Step-by-Step First Time Setup

### Step 1: Install Dependencies
```bash
cd DCIM_UI
npm install
```

### Step 2: Check Environment
```bash
# Verify .env file has correct configuration
cat .env
```

Should contain:
```env
VITE_API_URL=http://localhost:3001/api/v1
VITE_AI_API_URL=http://localhost:5000/api
```

### Step 3: Start DCIM Server (if not running)
```bash
cd ..\DCIM_Server
python run_server.py
```

### Step 4: Start UI with Proxy
```bash
cd ..\DCIM_UI
npm run dev:full
```

### Step 5: Open Browser
Go to: http://localhost:5173

## 🐛 Troubleshooting

### Topology Not Showing

**Symptoms**: Blank topology page, no network graph visible

**Solutions**:

1. **Check if API is responding**:
   ```bash
   # Test proxy server
   curl http://localhost:3001/health

   # Test agents endpoint
   curl http://localhost:3001/api/v1/agents
   ```

2. **Check browser console** (F12):
   - Look for network errors
   - Look for API fetch failures
   - Look for D3.js errors

3. **Verify agents are registered**:
   - At least one agent must be registered and approved
   - Check the Agents page in the UI
   - Or use API: `curl http://localhost:3001/api/v1/agents`

4. **Restart everything**:
   ```bash
   # Stop current services (Ctrl+C)
   # Then restart
   npm run dev:full
   ```

### Port Already in Use

**Error**: `EADDRINUSE: address already in use 0.0.0.0:3001`

**Solution**:
```bash
# Find process using port 3001
netstat -ano | findstr :3001

# Kill the process (replace PID with actual process ID)
powershell -Command "Stop-Process -Id <PID> -Force"

# Try again
npm run dev:full
```

### Certificate Errors

**Error**: Failed to load certificates

**Solution**:
```bash
# Check if certificates exist
ls certs/

# Should show: client.crt, client.key, ca.crt

# If missing, copy from DCIM_Server or regenerate
```

### Can't Connect to DCIM Server

**Error**: `ECONNREFUSED` or connection timeout

**Solution**:

1. **Check if DCIM server is running**:
   ```bash
   netstat -ano | findstr :8443
   ```

2. **If not running, start it**:
   ```bash
   cd ..\DCIM_Server
   python run_server.py
   ```

3. **Check server logs** for errors

### API Returns 403 Forbidden

**Cause**: Client certificate not accepted by server

**Solution**:

1. **Verify certificates match**:
   - Proxy uses `certs/client.crt`
   - Server must trust the CA that signed it

2. **Check server configuration**:
   - Server must have mTLS enabled
   - Server must trust the CA certificate

3. **Regenerate certificates** if needed

## 🔧 Available Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start **only** the UI dev server (no proxy) |
| `npm run proxy` | Start **only** the proxy server |
| `npm run dev:full` | Start **both** proxy and UI (recommended) |
| `npm run build` | Build UI for production |
| `npm run preview` | Preview production build |
| `npm run lint` | Run linter |

## 📁 Project Structure

```
DCIM_UI/
├── src/                    # React source code
│   ├── pages/
│   │   └── Topology.tsx   # Network topology visualization
│   ├── lib/
│   │   └── api.ts         # API client
│   └── hooks/
│       └── useAgents.ts   # React Query hooks
├── certs/                 # mTLS certificates
│   ├── client.crt
│   ├── client.key
│   └── ca.crt
├── proxy-server.js        # mTLS proxy server
├── .env                   # Environment configuration
├── package.json          # Dependencies and scripts
└── vite.config.ts        # Vite configuration
```

## 🎨 Features

### Network Topology
- **Interactive visualization** using D3.js force-directed graph
- **Real-time updates** showing agent status
- **Node details** on click
- **Zoom and pan** controls
- **Color-coded status**:
  - 🟣 Purple: DCIM Server
  - 🟢 Green: Online Agents
  - 🔴 Red: Offline Agents

### Dashboard Features
- Agent monitoring
- Metrics visualization
- Alert management
- SNMP device tracking
- Real-time SSE updates

## 🌐 URLs Reference

| Service | URL | Purpose |
|---------|-----|---------|
| UI | http://localhost:5173 | Main application |
| Proxy | http://localhost:3001 | mTLS proxy |
| Proxy Health | http://localhost:3001/health | Health check |
| DCIM Server | https://localhost:8443 | Backend API |
| API Docs | https://localhost:8443/docs | Swagger/OpenAPI |

## 💡 Tips

1. **Use `npm run dev:full`** for development - it's the easiest way
2. **Keep browser DevTools open** (F12) to see network requests and errors
3. **Check proxy server logs** - they show all API requests being forwarded
4. **Refresh the page** if topology doesn't load initially
5. **Register at least one agent** to see the topology visualization

## 📖 Further Reading

- [PROXY_SETUP.md](./PROXY_SETUP.md) - Detailed proxy server documentation
- [../BUILD_AND_RUN.md](../BUILD_AND_RUN.md) - Full system build guide
- [../TROUBLESHOOTING.md](../TROUBLESHOOTING.md) - Comprehensive troubleshooting

## 🆘 Getting Help

If you encounter issues:

1. Check browser console (F12)
2. Check proxy server logs
3. Check DCIM server logs
4. Review [TROUBLESHOOTING.md](../TROUBLESHOOTING.md)
5. Verify all services are running:
   ```bash
   netstat -ano | findstr ":5173 :3001 :8443"
   ```

---

**Happy developing! 🎉**

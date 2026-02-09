# DCIM UI - Server Connection Guide

## Issue Fixed ✅

The server was configured to require client certificates (`client_auth: "require_and_verify"`), which prevented the browser from connecting.

### Change Made:
Updated `DCIM_Server/build/windows-amd64/config.yaml`:
```yaml
client_auth: "none"  # Changed from "require_and_verify"
```

## 🔄 Next Steps:

### 1. Restart the DCIM Server

**Stop the current server** (Press Ctrl+C in the server terminal), then restart it:

```bash
cd E:\Projects\DCIM\DCIM_Server\build\windows-amd64
dcim-server.exe -config config.yaml
```

You should see in the startup logs:
```
[SERVER] TLS configured: ClientAuth=none
```
(Instead of `ClientAuth=require_and_verify`)

### 2. Verify Server Endpoints

The UI needs these GET endpoints to be available on the server:

| Endpoint | Purpose | Used By |
|----------|---------|---------|
| `GET /api/v1/agents` | List all agents | Dashboard, Agents page |
| `GET /api/v1/agents/{id}` | Get agent details | Agent detail page |
| `GET /api/v1/agents/{id}/metrics/latest` | Latest metrics | Agent detail page |
| `GET /api/v1/alerts` | List all alerts | Dashboard, Alerts page |
| `GET /api/v1/alerts/{id}` | Get alert details | Alert detail page |
| `GET /api/v1/metrics` | Query metrics | Charts, Analytics |
| `GET /api/v1/dashboard` | Dashboard summary | Dashboard page |
| `GET /health` | Health check | System monitoring |

### 3. Test Connection

Once the server is restarted, open your browser console (F12) and navigate to:
- **http://localhost:5173/app/dashboard**

Check the Network tab for API calls:
- ✅ Should see successful requests to `/api/v1/agents`, `/api/v1/alerts`
- ✅ Status code should be `200 OK`
- ❌ If you see certificate errors, the server still requires certs
- ❌ If you see 404 errors, the server doesn't have GET endpoints

## 📊 Expected Data Flow

```
UI (React)
    ↓ GET /api/v1/agents
Vite Proxy (localhost:5173)
    ↓ Forward to https://localhost:8443/api/v1/agents
DCIM Server
    ↓ Query Database (PostgreSQL)
    ↓ Return JSON
UI Updates
```

## 🔍 Troubleshooting

### Issue: "SSL/TLS handshake failed"
**Cause**: Server still requires client certificates
**Fix**: Verify config.yaml has `client_auth: "none"` and restart server

### Issue: "404 Not Found" on /api/v1/agents
**Cause**: Server doesn't have GET endpoints implemented
**Fix**: Server needs to implement GET endpoints for:
- Agents list
- Alerts list
- Metrics query
- Dashboard summary

### Issue: "Network Error" or "Failed to fetch"
**Cause**: CORS or proxy configuration issue
**Fix**:
1. Check server logs for CORS errors
2. Verify Vite proxy in `vite.config.ts` is correct:
   ```ts
   proxy: {
     '/api': {
       target: 'https://localhost:8443',
       changeOrigin: true,
       secure: false,  // Allow self-signed certs
     },
   }
   ```

### Issue: "Empty data" but no errors
**Cause**: No data in database yet
**Fix**: Wait for agent to send more data, or check database directly:
```sql
-- Check if data exists
SELECT COUNT(*) FROM agents;
SELECT COUNT(*) FROM alerts;
SELECT COUNT(*) FROM metrics;
```

## 📝 Current Server API Status

Based on your server logs, we can see:
- ✅ **POST /api/v1/metrics** - Working (receiving data from agent)
- ✅ **POST /api/v1/alerts** - Working (receiving alerts from agent)
- ✅ **POST /api/v1/register** - Working (agent registration)
- ✅ **GET /health** - Available

**Missing (needed by UI)**:
- ❓ **GET /api/v1/agents** - Need to verify
- ❓ **GET /api/v1/alerts** - Need to verify
- ❓ **GET /api/v1/metrics** - Need to verify

## 🎯 What You Should See

### After Server Restart:

**Server Logs:**
```
[SERVER] TLS configured: ClientAuth=none, MinVersion=1.2
[SERVER] Server starting with TLS on https://0.0.0.0:8443
```

**UI Dashboard:**
- Agent count: 1 (DESKTOP-AU1P9BD-1770626891)
- Online agents: 1
- Total alerts: Multiple (based on logs)
- Recent alerts list populated

**Browser Network Tab:**
```
GET /api/v1/agents → 200 OK
GET /api/v1/alerts → 200 OK
```

## 📚 Database Data

From your server logs, you have:
- **1 Agent**: `DESKTOP-AU1P9BD-1770626891`
- **Multiple Alerts**: Severity levels (CRITICAL, WARNING, INFO)
- **Metrics**: 100 metrics per batch, sent every 2 minutes

This data should appear in the UI once the connection is established!

---

## Quick Test Command

After restarting the server, test the connection:

```bash
# Test health endpoint (should work without certs now)
curl -k https://localhost:8443/health

# Expected response:
# {"status":"ok","timestamp":"..."}
```

---

**Next**: Restart the server and refresh the UI!

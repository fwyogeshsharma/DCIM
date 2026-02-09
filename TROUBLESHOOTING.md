# DCIM UI - Troubleshooting Guide

## Issue: Website Shows No Data

### Quick Diagnostic

Run this PowerShell script to test all endpoints:

```powershell
cd E:\Projects\DCIM
.\test_api.ps1
```

### Step-by-Step Fix

#### 1. Verify Backend Configuration

**Check CORS Settings:**
Open `E:\Projects\DCIM\DCIM_Server\config.yaml` and verify:

```yaml
cors:
  enabled: true
  allowed_origins: ["http://localhost:3000", "http://localhost:5173"]
```

**Check mTLS Settings (for development):**

```yaml
tls:
  enabled: true
  client_auth: "none"  # Should be "none" for development
```

#### 2. Restart Services in Correct Order

**Terminal 1: Backend**
```bash
cd E:\Projects\DCIM\DCIM_Server
# Stop current server (Ctrl+C)
.\dcim-server.exe
```

Wait for: `Server started successfully on https://0.0.0.0:8443`

**Terminal 2: Frontend**
```bash
cd E:\Projects\DCIM\DCIM_UI
npm run dev
```

Wait for: `Local: http://localhost:5173/`

#### 3. Test Backend Directly

Open PowerShell and run:

```powershell
# Test server health
Invoke-RestMethod -Uri "https://localhost:8443/api/v1/agents" -SkipCertificateCheck
```

**Expected Output:**
- `[]` (empty array) = Server works, but no agents registered
- JSON with agents = Server works with data
- Error = Server not accessible

#### 4. Check Browser Console

1. Open website: `http://localhost:5173`
2. Press `F12` (Developer Tools)
3. Go to **Console** tab
4. Look for errors:

**Common Errors & Fixes:**

❌ **CORS Error:**
```
Access to fetch at 'https://localhost:8443/api/v1/agents' from origin
'http://localhost:5173' has been blocked by CORS policy
```
**Fix:** Update `config.yaml` CORS settings (see above) and restart server

❌ **SSL Error:**
```
net::ERR_CERT_AUTHORITY_INVALID
```
**Fix:** This is expected with self-signed certificates. The proxy should handle it.
If the error persists, check `vite.config.ts`:
```typescript
server: {
  proxy: {
    '/api': {
      target: 'https://localhost:8443',
      changeOrigin: true,
      secure: false,  // Important for self-signed certs
    },
  },
}
```

❌ **Connection Refused:**
```
GET http://localhost:5173/api/v1/agents net::ERR_CONNECTION_REFUSED
```
**Fix:** Backend is not running. Start DCIM_Server.

❌ **404 Not Found:**
```
GET http://localhost:5173/api/v1/agents 404 (Not Found)
```
**Fix:** Check API base path in backend config matches `/api/v1`

#### 5. Check Network Tab

In Developer Tools:
1. Go to **Network** tab
2. Refresh page (F5)
3. Look for requests to `/api/v1/agents`

**What to check:**
- Status Code: Should be `200 OK`
- Response: Should show JSON data (even if empty array `[]`)
- Headers: Check CORS headers are present

#### 6. Verify PostgreSQL is Running

The backend uses PostgreSQL. Check if it's running:

```powershell
# Windows
Get-Service -Name postgresql*

# Or check if port is open
Test-NetConnection -ComputerName localhost -Port 5432
```

**If PostgreSQL is not running:**

```powershell
# Start PostgreSQL service (Windows)
Start-Service postgresql-x64-XX  # XX is your version number

# Or manually start PostgreSQL
```

Check database connection in `config.yaml`:
```yaml
database:
  type: "postgres"
  postgres:
    host: "localhost"
    port: 5432
    user: "postgres"
    password: "postgres"
    database: "dcim_db"
```

#### 7. Check if Data Exists

**Connect to PostgreSQL:**

```bash
# Windows (adjust path to your PostgreSQL installation)
"C:\Program Files\PostgreSQL\XX\bin\psql.exe" -U postgres -d dcim_db

# Run queries
SELECT COUNT(*) FROM agents;
SELECT COUNT(*) FROM metrics;
SELECT COUNT(*) FROM alerts;
```

**If tables are empty:**
- You need to register agents first
- Start a DCIM_Agent to send metrics
- The UI will remain empty until agents report data

#### 8. Register a Test Agent

If no agents exist, you can register one manually:

```powershell
# Create a test agent
$body = @{
    agent_id = "test-agent-001"
    hostname = "test-server"
    ip_address = "192.168.1.100"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://localhost:8443/api/v1/metrics" `
    -Method POST `
    -Body $body `
    -ContentType "application/json" `
    -SkipCertificateCheck
```

Or start a DCIM_Agent:
```bash
cd E:\Projects\DCIM\DCIM_Agent
.\dcim-agent.exe
```

#### 9. Check Vite Proxy Configuration

Verify `E:\Projects\DCIM\DCIM_UI\vite.config.ts`:

```typescript
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'https://localhost:8443',
        changeOrigin: true,
        secure: false,  // Must be false for self-signed certs
      },
    },
  },
})
```

#### 10. Clear Browser Cache

Sometimes cached responses cause issues:

1. Open Developer Tools (F12)
2. Right-click Refresh button
3. Select "Empty Cache and Hard Reload"

Or:
- Chrome: `Ctrl+Shift+Delete` → Clear cached images and files
- Firefox: `Ctrl+Shift+Delete` → Cached Web Content

## Common Scenarios

### Scenario 1: Backend Running, Frontend Shows "Loading..."

**Cause:** API requests are failing

**Fix:**
1. Check browser console for errors
2. Verify CORS settings
3. Restart backend with updated config

### Scenario 2: All Services Running, Still No Data

**Cause:** No agents registered or sending data

**Fix:**
1. Check database: `SELECT COUNT(*) FROM agents;`
2. Register a test agent (see step 8)
3. Start DCIM_Agent to send metrics

### Scenario 3: CORS Errors Despite Correct Config

**Cause:** Backend not restarted after config change

**Fix:**
1. Stop backend (Ctrl+C)
2. Wait 5 seconds
3. Restart backend
4. Hard refresh browser (Ctrl+F5)

### Scenario 4: Frontend Shows Errors in Console

**Cause:** TypeScript or build errors

**Fix:**
```bash
cd DCIM_UI
rm -rf node_modules
npm install
npm run dev
```

## Debug Checklist

Before asking for help, verify:

- [ ] PostgreSQL is running
- [ ] DCIM_Server is running (check port 8443)
- [ ] Frontend dev server is running (check port 5173)
- [ ] CORS includes `http://localhost:5173`
- [ ] client_auth is set to `"none"` in config.yaml
- [ ] Backend config.yaml was saved
- [ ] Backend was restarted after config changes
- [ ] Browser cache was cleared
- [ ] No errors in browser console
- [ ] Network tab shows 200 OK for API requests
- [ ] Database has tables created (check with psql)

## Still Not Working?

### Collect Debug Information

1. **Backend logs:**
```bash
cat E:\Projects\DCIM\DCIM_Server\logs\dcim_server.log | tail -50
```

2. **Browser console errors:**
Press F12 → Console → Copy all red errors

3. **Network requests:**
Press F12 → Network → Filter: Fetch/XHR → Check status codes

4. **Configuration:**
```bash
cat E:\Projects\DCIM\DCIM_Server\config.yaml | grep -A 10 "cors:"
```

### Test Minimal Setup

Create a test file `test.html`:

```html
<!DOCTYPE html>
<html>
<body>
    <h1>DCIM API Test</h1>
    <button onclick="testAPI()">Test API</button>
    <pre id="result"></pre>

    <script>
    async function testAPI() {
        try {
            const response = await fetch('https://localhost:8443/api/v1/agents', {
                method: 'GET',
            });
            const data = await response.json();
            document.getElementById('result').textContent = JSON.stringify(data, null, 2);
        } catch (error) {
            document.getElementById('result').textContent = 'Error: ' + error.message;
        }
    }
    </script>
</body>
</html>
```

Open in browser and click "Test API". If this works but the React app doesn't, the issue is in the frontend code.

## Quick Reset

If all else fails, reset everything:

```bash
# Stop all services (Ctrl+C in all terminals)

# Reset frontend
cd E:\Projects\DCIM\DCIM_UI
rm -rf node_modules dist
npm install
npm run dev

# Check backend config
cd E:\Projects\DCIM\DCIM_Server
# Verify config.yaml settings
.\dcim-server.exe

# Clear browser completely
# Chrome: Settings → Privacy → Clear browsing data → All time
```

Then try again from Step 1.

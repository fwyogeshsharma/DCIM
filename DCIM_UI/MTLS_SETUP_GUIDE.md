# DCIM UI - mTLS Certificate Setup Guide

## 🔐 Overview

This guide explains how to set up mutual TLS (mTLS) authentication between the DCIM UI and Server using client certificates, just like agents do.

## 📋 Architecture

```
┌─────────────┐         HTTP          ┌──────────────────┐      mTLS/HTTPS      ┌──────────────┐
│   Browser   │ ────────────────────> │   Proxy Server   │ ───────────────────> │ DCIM Server  │
│  (React UI) │                       │  (Node.js)       │  (with client certs)  │  (Go/mTLS)   │
└─────────────┘                       └──────────────────┘                       └──────────────┘
  localhost:5173                        localhost:3001                            localhost:8443
```

**Why a proxy server?**
- Browsers cannot directly load certificate files from JavaScript for security reasons
- The Node.js proxy handles mTLS certificate authentication
- The browser communicates with the proxy over regular HTTP

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd DCIM_UI
npm install
```

This installs:
- `express` - Web server for proxy
- `cors` - Cross-origin resource sharing
- `concurrently` - Run multiple commands simultaneously

### 2. Copy Certificates

Copy your certificates to `DCIM_UI/certs/` directory:

```bash
# From your certificate location (e.g., agent certs)
cp /path/to/client.crt DCIM_UI/certs/
cp /path/to/client.key DCIM_UI/certs/
cp /path/to/ca.crt DCIM_UI/certs/
```

**Certificate Requirements:**
- `client.crt` - Client certificate (PEM format)
- `client.key` - Client private key (PEM format)
- `ca.crt` - CA certificate that signed the server cert (PEM format)

**Alternative: Use .pfx/.p12 file:**
```bash
cp /path/to/client.pfx DCIM_UI/certs/
```

Then edit `proxy-server.js` to uncomment the pfx configuration:
```javascript
// Comment out these lines:
// cert: fs.readFileSync(...),
// key: fs.readFileSync(...),

// Uncomment these lines:
pfx: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.pfx)),
passphrase: CONFIG.certs.passphrase,
```

### 3. Verify Certificate Files

```bash
# Check if certificates exist
ls -la DCIM_UI/certs/

# Should show:
# client.crt
# client.key
# ca.crt
```

### 4. Update Server Configuration

Ensure your DCIM server is configured to accept client certificates:

**File**: `DCIM_Server/build/windows-amd64/config.yaml`

```yaml
tls:
  enabled: true
  client_auth: "require_and_verify"  # Require client certificates
  ca_cert_path: "./certs/ca.crt"     # Must match UI's CA
```

Restart the DCIM server after configuration changes.

### 5. Start Both Servers

**Option A: Run both servers together (Recommended)**
```bash
cd DCIM_UI
npm run dev:full
```

This starts:
- Proxy server on http://localhost:3001
- UI dev server on http://localhost:5173

**Option B: Run servers separately**

Terminal 1 - Proxy Server:
```bash
cd DCIM_UI
npm run proxy
```

Terminal 2 - UI Dev Server:
```bash
cd DCIM_UI
npm run dev
```

### 6. Verify Setup

**Test proxy health:**
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

**Test API connection:**
```bash
curl http://localhost:3001/api/v1/agents
```

Should return JSON data (not 401 or certificate errors).

### 7. Open UI

Navigate to: **http://localhost:5173**

The dashboard should now load data from the server using mTLS authentication!

## 📁 Directory Structure

```
DCIM_UI/
├── certs/                      # Certificate files (git ignored)
│   ├── client.crt             # Client certificate
│   ├── client.key             # Client private key
│   ├── ca.crt                 # CA certificate
│   └── client.pfx             # Alternative: PKCS#12 format
│
├── proxy-server.js            # mTLS proxy server
├── .env                       # Environment configuration
├── package.json               # Updated with proxy scripts
└── vite.config.ts             # No longer needs proxy config
```

## 🔧 Configuration

### Environment Variables (.env)

```bash
# API Base URL - Points to proxy server
VITE_API_URL=http://localhost:3001/api/v1
```

### Proxy Server Configuration (proxy-server.js)

```javascript
const CONFIG = {
  proxy: {
    port: 3001,              // Proxy server port
    host: '0.0.0.0'
  },

  dcim: {
    host: 'localhost',       // DCIM server host
    port: 8443,              // DCIM server port
    baseUrl: '/api/v1'
  },

  certs: {
    clientCert: './certs/client.crt',
    clientKey: './certs/client.key',
    ca: './certs/ca.crt',

    // For .pfx format:
    // pfx: './certs/client.pfx',
    // passphrase: 'your-passphrase'
  }
};
```

## 🔍 Troubleshooting

### Error: "Failed to load certificates"

**Cause**: Certificate files not found or incorrect paths

**Fix**:
1. Verify files exist: `ls -la DCIM_UI/certs/`
2. Check file permissions: `chmod 600 DCIM_UI/certs/*.key`
3. Verify paths in `proxy-server.js` CONFIG section

### Error: "DEPTH_ZERO_SELF_SIGNED_CERT"

**Cause**: Server uses self-signed certificate

**Fix**: Update proxy-server.js:
```javascript
rejectUnauthorized: false  // Change from true to false
```

**Security Note**: Only use this in development! Production should use proper CA-signed certificates.

### Error: "UNABLE_TO_VERIFY_LEAF_SIGNATURE"

**Cause**: CA certificate doesn't match server certificate

**Fix**:
1. Ensure `ca.crt` is the same CA that signed the server certificate
2. Verify certificate chain: `openssl verify -CAfile ca.crt server.crt`

### Error: "ECONNREFUSED"

**Cause**: DCIM server not running or wrong host/port

**Fix**:
1. Check if server is running: `curl -k https://localhost:8443/health`
2. Verify host/port in proxy-server.js CONFIG

### Error: "Certificate has expired"

**Cause**: Client or server certificate has expired

**Fix**: Generate new certificates or extend expiration date

### Proxy starts but UI gets 401 errors

**Cause**: Client certificate not accepted by server

**Fix**:
1. Verify server's `client_auth` is set to accept certificates
2. Check that client cert is signed by CA that server trusts
3. Review server logs for certificate validation errors

### UI shows "Connection Error"

**Cause**: Proxy server not running or wrong API URL

**Fix**:
1. Ensure proxy is running: `curl http://localhost:3001/health`
2. Check .env has correct `VITE_API_URL`
3. Restart UI dev server after changing .env: `npm run dev`

## 🔐 Certificate Generation (If needed)

If you need to generate new certificates:

### Using OpenSSL

```bash
# 1. Generate CA private key and certificate
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt \
  -subj "/CN=DCIM-CA"

# 2. Generate client private key
openssl genrsa -out client.key 4096

# 3. Generate client certificate signing request (CSR)
openssl req -new -key client.key -out client.csr \
  -subj "/CN=dcim-ui-client"

# 4. Sign client certificate with CA
openssl x509 -req -days 365 -in client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt

# 5. Verify certificate
openssl verify -CAfile ca.crt client.crt
```

### Convert to .pfx format (optional)

```bash
openssl pkcs12 -export -out client.pfx \
  -inkey client.key -in client.crt -certfile ca.crt \
  -passout pass:yourpassword
```

## 📊 Verification Checklist

Before reporting issues, verify:

- [ ] Proxy server starts without certificate errors
- [ ] `curl http://localhost:3001/health` returns success
- [ ] DCIM server is running with mTLS enabled
- [ ] Server's `ca.crt` matches proxy's `ca.crt`
- [ ] Client certificate is valid (not expired)
- [ ] Client certificate is signed by trusted CA
- [ ] .env file has correct proxy URL
- [ ] UI dev server restarted after .env changes
- [ ] Browser console shows no CORS errors
- [ ] Network tab shows requests to localhost:3001 (not localhost:8443)

## 🎯 Expected Data Flow

### Successful Request
```
1. Browser → GET http://localhost:5173/app/dashboard
2. React app → GET http://localhost:3001/api/v1/agents
3. Proxy → GET https://localhost:8443/api/v1/agents (with client cert)
4. DCIM Server → Verifies client cert → Returns data
5. Proxy → Forwards response
6. UI → Displays data
```

### With Logs

**Proxy logs:**
```
[2026-02-09T14:30:00.000Z] GET /api/v1/agents
  → Forwarding to: https://localhost:8443/api/v1/agents
  ← Response: 200
```

**Server logs:**
```
[SERVER] -> GET /api/v1/agents (Client: dcim-ui-client, IP: [::1]:12345)
[SERVER] <- GET /api/v1/agents completed in 5.2ms
```

## 🔄 Development vs Production

### Development Setup (Current)
- HTTP between browser and proxy (localhost only)
- mTLS between proxy and server
- Self-signed certificates OK
- `rejectUnauthorized: false` OK

### Production Setup (Future)
- HTTPS everywhere with proper certificates
- API gateway with JWT/OAuth + mTLS
- Browser authentication separate from mTLS
- `rejectUnauthorized: true` enforced

## 📝 NPM Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start UI only (port 5173) |
| `npm run proxy` | Start proxy only (port 3001) |
| `npm run dev:full` | Start both proxy + UI together |
| `npm run build` | Build production UI |

## 🛡️ Security Notes

### Development
- Using self-signed certificates is OK
- HTTP between browser and proxy is OK on localhost
- Certificates can be in plain files

### Production
- Use CA-signed certificates
- HTTPS everywhere (no HTTP)
- Store certificates securely (environment variables, secrets manager)
- Rotate certificates regularly
- Monitor certificate expiration
- Implement proper access controls

## ✅ Success Indicators

When everything is working:

1. **Proxy Console:**
   ```
   ✅ Proxy server running on http://0.0.0.0:3001
   🔒 Forwarding to DCIM server: https://localhost:8443
   📜 Using client certificate: ./certs/client.crt
   ```

2. **Browser Console:** No 401 or certificate errors

3. **Dashboard:** Shows real data from server

4. **Network Tab:** All API calls return 200 OK

---

**Need Help?** Check logs in this order:
1. Proxy console output
2. Browser Developer Console (F12)
3. Browser Network tab
4. DCIM server logs

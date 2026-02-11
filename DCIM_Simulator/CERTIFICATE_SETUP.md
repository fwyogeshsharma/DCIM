# Client Certificate Setup for mTLS API

## Overview

The cooling metrics API (`/api/v1/cooling-metrics`) requires **mutual TLS (mTLS) authentication** using client certificates.

## Certificate Files Location

Certificates are stored in: `src/assets/certs/`
- `ca.crt` - Certificate Authority
- `client.crt` - Client certificate
- `client.key` - Client private key

## Browser-Based Certificate Authentication

**IMPORTANT:** JavaScript in browsers cannot directly access certificate files for security reasons. Client certificates must be imported into the browser's certificate store.

### Method 1: Import Certificate into Browser (Recommended for Development)

#### Chrome / Edge (Windows)

1. **Convert to PKCS#12 format** (browsers need .p12/.pfx):
   ```bash
   # Run in project root
   openssl pkcs12 -export -out client.p12 \
     -inkey src/assets/certs/client.key \
     -in src/assets/certs/client.crt \
     -certfile src/assets/certs/ca.crt

   # You'll be prompted for a password - remember it!
   ```

2. **Import into Chrome/Edge:**
   - Open: `chrome://settings/certificates` or `edge://settings/certificates`
   - Click "Your certificates" tab
   - Click "Import"
   - Select the `client.p12` file
   - Enter the password you set

3. **Test:**
   - Navigate to your API endpoint
   - Browser will prompt to select a certificate
   - Select the imported client certificate

#### Firefox

1. **Convert to PKCS#12** (same as above if not done)

2. **Import into Firefox:**
   - Open: `about:preferences#privacy`
   - Scroll to "Certificates" → Click "View Certificates"
   - Click "Your Certificates" tab
   - Click "Import"
   - Select `client.p12` and enter password

#### Safari (macOS)

1. **Convert to PKCS#12** (same as above)

2. **Import into Keychain:**
   - Double-click `client.p12`
   - Keychain Access will open
   - Enter password and import
   - Make certificate trusted for SSL

### Method 2: Backend Proxy (Recommended for Production)

Since browsers cannot programmatically use certificate files, create a Node.js/Express proxy:

#### Create Proxy Server

```javascript
// proxy-server.js
const express = require('express');
const https = require('https');
const fs = require('fs');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

// Load certificates
const httpsAgent = new https.Agent({
  cert: fs.readFileSync('./src/assets/certs/client.crt'),
  key: fs.readFileSync('./src/assets/certs/client.key'),
  ca: fs.readFileSync('./src/assets/certs/ca.crt')
});

// Proxy endpoint
app.post('/api/proxy/cooling-metrics', async (req, res) => {
  try {
    const response = await fetch('https://your-api-server.com/api/v1/cooling-metrics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
      agent: httpsAgent
    });
    const data = await response.json();
    res.json(data);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.listen(3000, () => {
  console.log('Proxy server running on http://localhost:3000');
});
```

#### Update Angular Environment

```typescript
// src/environments/environment.ts
export const environment = {
  production: false,
  apiUrl: 'http://localhost:3000/api/proxy' // Proxy instead of direct API
};
```

### Method 3: Angular CLI Proxy Configuration (Development Only)

Create `proxy.conf.json`:

```json
{
  "/api": {
    "target": "https://your-api-server.com",
    "secure": true,
    "changeOrigin": true,
    "logLevel": "debug"
  }
}
```

Update `angular.json`:

```json
{
  "serve": {
    "options": {
      "proxyConfig": "proxy.conf.json"
    }
  }
}
```

**Note:** This doesn't handle client certificates - use Method 1 or 2.

## Current Implementation

The Angular service is configured to:
- Use `HttpClient` with `withCredentials: true`
- Send requests to the API endpoint
- Rely on the browser to present client certificates during TLS handshake

## Configuration

### Update API Base URL

Edit `src/environments/environment.ts`:

```typescript
export const environment = {
  production: false,
  apiUrl: 'https://your-actual-api-server.com', // Update this!
  certPath: 'assets/certs'
};
```

### Service Usage

The service is automatically injected and used when you click "Send to API" button.

## Testing

1. **Browser Certificate Imported:**
   ```
   ✅ Click "Send to API"
   ✅ Browser prompts for certificate selection
   ✅ Data sent successfully
   ```

2. **No Certificate:**
   ```
   ❌ SSL/TLS handshake fails
   ❌ "Cannot connect to server" error
   ```

3. **Wrong Certificate:**
   ```
   ❌ 403 Forbidden or SSL error
   ```

## Troubleshooting

### Issue: "Cannot connect to server"

**Causes:**
- Server not running
- Certificate not imported
- Wrong API URL
- CORS issues

**Solutions:**
1. Verify server is running: `curl https://your-api-server.com/api/v1/cooling-metrics`
2. Check browser certificate: `chrome://settings/certificates`
3. Check browser console for errors
4. Verify API URL in `environment.ts`

### Issue: "ERR_CERT_AUTHORITY_INVALID"

**Cause:** CA certificate not trusted

**Solution:**
- Import `ca.crt` as a trusted CA in browser/system
- Or use a backend proxy that trusts the CA

### Issue: "CORS Error"

**Cause:** API server not allowing browser origin

**Solution:**
- Configure CORS on API server
- Or use backend proxy (recommended)

## Security Notes

⚠️ **NEVER commit private keys to version control!**

Add to `.gitignore`:
```
src/assets/certs/*.key
src/assets/certs/*.p12
src/assets/certs/*.pfx
```

Keep only:
- `ca.crt` (public CA)
- `client.crt` (public certificate)

Distribute `client.key` securely through other means.

## Production Deployment

For production, use **Method 2 (Backend Proxy)**:

1. ✅ Secure certificate storage (not in browser)
2. ✅ No client-side certificate management
3. ✅ No CORS issues
4. ✅ Better security control

## API Response Format

Expected successful response:
```json
{
  "status": "success",
  "message": "Metrics received",
  "timestamp": "2026-02-11T14:30:00Z"
}
```

Error response:
```json
{
  "status": "error",
  "message": "Authentication failed",
  "code": "INVALID_CERT"
}
```

## Further Reading

- [MDN: Client-side certificates](https://developer.mozilla.org/en-US/docs/Web/Security/Transport_Layer_Security)
- [Node.js HTTPS with client certificates](https://nodejs.org/api/https.html#https_https_request_options_callback)
- [OpenSSL PKCS12 commands](https://www.openssl.org/docs/man1.1.1/man1/openssl-pkcs12.html)

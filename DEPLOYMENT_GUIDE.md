# DCIM Enterprise Monitoring - Deployment Guide

Complete deployment guide for the DCIM UI with backend integration.

## Architecture Overview

```
User Browser
     ↓ (HTTPS)
  Nginx Reverse Proxy
     ├─→ /api/* → DCIM_Server:8443 (mTLS)
     ├─→ /ai/* → Prediction Service:5000
     └─→ / → Static Files (React Build)
```

## Prerequisites

### Server Requirements
- **Frontend Server**: 2GB RAM, 20GB disk, Ubuntu 20.04+ or Windows Server
- **Backend Server**: 4GB RAM, 50GB disk (already deployed)
- **Python**: 3.9+ for prediction service

### Software Required
- Node.js 18+
- Nginx 1.18+
- Python 3.9+
- Git

## Phase 1: Backend Preparation

### 1.1 Verify DCIM_Server is Running

```bash
cd E:\Projects\DCIM\DCIM_Server
./dcim-server.exe
```

Test the API:
```bash
curl -k https://localhost:8443/api/v1/agents
```

### 1.2 Configure CORS in Backend

Edit `E:\Projects\DCIM\DCIM_Server\config.yaml`:

```yaml
api:
  cors:
    enabled: true
    allowed_origins:
      - "https://dcim.yourdomain.com"
      - "http://localhost:5173"  # For development
    allowed_methods:
      - GET
      - POST
      - PUT
      - DELETE
      - OPTIONS
    allowed_headers:
      - "*"
```

Restart DCIM_Server.

## Phase 2: Frontend Build & Deployment

### 2.1 Build the Frontend

```bash
cd E:\Projects\DCIM\DCIM_UI

# Install dependencies (if not already done)
npm install

# Build for production
npm run build
```

The build output will be in `dist/` folder.

### 2.2 Deploy Static Files

**Option A: Windows IIS**

1. Install IIS with URL Rewrite module
2. Create new website pointing to `dist/` folder
3. Configure URL rewrite rule for SPA routing

**Option B: Linux with Nginx** (Recommended)

```bash
# Copy files to web directory
sudo mkdir -p /var/www/dcim-ui
sudo cp -r dist/* /var/www/dcim-ui/
sudo chown -R www-data:www-data /var/www/dcim-ui
```

## Phase 3: Python Prediction Service

### 3.1 Install Dependencies

```bash
cd E:\Projects\DCIM\prediction_service
pip install -r requirements.txt
```

### 3.2 Test the Service

```bash
python app.py
```

Verify:
```bash
curl http://localhost:5000/api/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "DCIM Prediction Service"
}
```

### 3.3 Production Deployment

**Windows:**

Create a Windows Service using NSSM:
```bash
nssm install DCIMPredictionService "C:\Python39\python.exe" "E:\Projects\DCIM\prediction_service\app.py"
nssm start DCIMPredictionService
```

**Linux:**

Create systemd service `/etc/systemd/system/dcim-prediction.service`:

```ini
[Unit]
Description=DCIM Prediction Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/dcim/prediction_service
Environment=FLASK_APP=app.py
ExecStart=/usr/bin/gunicorn -w 4 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable dcim-prediction
sudo systemctl start dcim-prediction
```

## Phase 4: Nginx Configuration

### 4.1 Create Nginx Config

Create `/etc/nginx/sites-available/dcim-ui`:

```nginx
upstream dcim_backend {
    server 192.168.1.100:8443;  # Replace with DCIM_Server IP
}

upstream ai_service {
    server localhost:5000;
}

server {
    listen 443 ssl http2;
    server_name dcim.yourdomain.com;

    # SSL Configuration
    ssl_certificate /etc/ssl/certs/dcim.crt;
    ssl_certificate_key /etc/ssl/private/dcim.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Serve frontend
    location / {
        root /var/www/dcim-ui;
        try_files $uri $uri/ /index.html;

        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
    }

    # Proxy to DCIM_Server with mTLS
    location /api/ {
        proxy_pass https://dcim_backend;

        # mTLS Configuration
        proxy_ssl_certificate /etc/ssl/certs/client.crt;
        proxy_ssl_certificate_key /etc/ssl/private/client.key;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca.crt;
        proxy_ssl_verify on;

        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE Support
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }

    # Proxy to AI service
    location /ai/ {
        proxy_pass http://ai_service/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/json;
}

# HTTP redirect
server {
    listen 80;
    server_name dcim.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

### 4.2 Enable Site

```bash
sudo ln -s /etc/nginx/sites-available/dcim-ui /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## Phase 5: SSL/TLS Configuration

### 5.1 Generate SSL Certificates

**Option A: Let's Encrypt (Free)**

```bash
sudo certbot --nginx -d dcim.yourdomain.com
```

**Option B: Self-Signed (Development)**

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/ssl/private/dcim.key \
  -out /etc/ssl/certs/dcim.crt
```

### 5.2 Configure mTLS Certificates

Copy the client certificates from DCIM_Server:

```bash
sudo cp E:\Projects\DCIM\DCIM_Server\certs\client\client.crt /etc/ssl/certs/
sudo cp E:\Projects\DCIM\DCIM_Server\certs\client\client.key /etc/ssl/private/
sudo cp E:\Projects\DCIM\DCIM_Server\certs\ca\ca.crt /etc/ssl/certs/
```

## Phase 6: Environment Configuration

### 6.1 Create Production .env

In the deployment server, create `.env.production`:

```env
# Backend API
VITE_API_URL=/api/v1

# AI Service
VITE_AI_API_URL=/ai

# OpenAI (Optional - for NL queries)
# VITE_OPENAI_API_KEY=sk-your-key-here
```

### 6.2 Build with Production Config

```bash
cp .env.production .env
npm run build
```

## Phase 7: Verification & Testing

### 7.1 Verify Services

```bash
# Check frontend
curl -I https://dcim.yourdomain.com

# Check backend API
curl -k https://dcim.yourdomain.com/api/v1/agents

# Check AI service
curl https://dcim.yourdomain.com/ai/health

# Check SSE
curl -N https://dcim.yourdomain.com/api/v1/events
```

### 7.2 Browser Testing

1. Navigate to `https://dcim.yourdomain.com`
2. Verify dashboard loads
3. Check agent list populates
4. Test real-time updates (SSE)
5. Verify charts render
6. Test theme toggle
7. Check alerts page

### 7.3 Network Tab Verification

Open browser DevTools → Network:
- Verify API calls go to `/api/v1/*`
- Check SSE connection to `/api/v1/events`
- Verify static assets load from root

## Phase 8: Monitoring & Maintenance

### 8.1 Log Locations

**Frontend Nginx:**
- Access: `/var/log/nginx/access.log`
- Error: `/var/log/nginx/error.log`

**Backend:**
- DCIM_Server: `E:\Projects\DCIM\DCIM_Server\logs\`

**Prediction Service:**
- `journalctl -u dcim-prediction -f`

### 8.2 Health Checks

Create monitoring script `/opt/dcim/healthcheck.sh`:

```bash
#!/bin/bash

# Check frontend
curl -f https://dcim.yourdomain.com || echo "Frontend DOWN"

# Check backend
curl -f -k https://localhost:8443/api/v1/health || echo "Backend DOWN"

# Check AI service
curl -f http://localhost:5000/api/health || echo "AI Service DOWN"
```

Run via cron every 5 minutes:
```
*/5 * * * * /opt/dcim/healthcheck.sh
```

## Troubleshooting

### Issue: UI Shows "Loading..." Forever

**Diagnosis:**
```bash
# Check browser console for CORS errors
# Check Nginx error log
tail -f /var/log/nginx/error.log

# Verify backend is accessible
curl -k https://dcim-backend-ip:8443/api/v1/agents
```

**Solution:**
- Update CORS configuration in backend `config.yaml`
- Restart DCIM_Server

### Issue: SSE Not Working

**Diagnosis:**
```bash
# Test SSE directly
curl -N -k https://dcim-backend-ip:8443/api/v1/events
```

**Solution:**
- Verify `proxy_buffering off` in Nginx
- Check `proxy_read_timeout` is set high (86400s)
- Ensure EventSource URL is correct

### Issue: 502 Bad Gateway

**Diagnosis:**
```bash
# Check if backend is running
netstat -tulpn | grep 8443

# Check Nginx can reach backend
telnet dcim-backend-ip 8443
```

**Solution:**
- Verify backend IP in Nginx config
- Check firewall rules
- Verify mTLS certificates are valid

### Issue: AI Features Not Working

**Diagnosis:**
```bash
# Check prediction service
curl http://localhost:5000/api/health

# Check service logs
journalctl -u dcim-prediction -n 50
```

**Solution:**
- Restart prediction service
- Check Python dependencies
- Verify Nginx proxy configuration

## Security Hardening

### 9.1 Firewall Rules

```bash
# Allow only necessary ports
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### 9.2 Nginx Security Headers

Add to server block:

```nginx
add_header X-Frame-Options "SAMEORIGIN";
add_header X-Content-Type-Options "nosniff";
add_header X-XSS-Protection "1; mode=block";
add_header Referrer-Policy "strict-origin-when-cross-origin";
```

### 9.3 Rate Limiting

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/ {
    limit_req zone=api burst=20;
    # ... rest of config
}
```

## Backup & Recovery

### 10.1 Backup Frontend

```bash
tar -czf dcim-ui-$(date +%Y%m%d).tar.gz /var/www/dcim-ui
```

### 10.2 Backup Configuration

```bash
tar -czf nginx-config-$(date +%Y%m%d).tar.gz /etc/nginx/sites-available/dcim-ui
```

### 10.3 Recovery

```bash
# Restore frontend
tar -xzf dcim-ui-20250206.tar.gz -C /

# Restore Nginx config
tar -xzf nginx-config-20250206.tar.gz -C /
sudo nginx -t && sudo systemctl reload nginx
```

## Performance Optimization

### 11.1 Enable HTTP/2

Already enabled in config with `http2` directive.

### 11.2 Enable Brotli Compression

```bash
sudo apt install nginx-module-brotli
```

Add to nginx.conf:
```nginx
brotli on;
brotli_types text/plain text/css application/javascript application/json;
```

### 11.3 CDN Integration (Optional)

For global deployment, integrate CloudFlare or AWS CloudFront:
- Point CDN to your origin server
- Configure caching rules for static assets
- Update DNS to point to CDN

## Conclusion

The DCIM Enterprise Monitoring UI is now deployed and integrated with the backend. Access it at:

**URL:** `https://dcim.yourdomain.com`

**Default Credentials:** (Use backend credentials)

For support, refer to the README files in each component directory.

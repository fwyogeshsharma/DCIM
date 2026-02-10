# DCIM UI Helper Scripts

Quick reference for all available helper scripts.

## 🚀 Starting Services

### Start Everything (Recommended)
```bash
npm run dev:full
```
Starts both the proxy server and UI dev server together.

**OR** use the batch file:
```bash
.\start-dev.bat
```

### Start with Auto-Restart
```bash
.\restart-dev.bat
```
Automatically stops any existing proxy server, then starts everything fresh.

### Start Individual Services
```bash
# Proxy server only
npm run proxy

# UI dev server only
npm run dev
```

---

## 🛑 Stopping Services

### Stop Proxy Server
```bash
# PowerShell
.\stop-proxy.ps1

# Batch file
.\stop-proxy.bat
```

### Stop All (Manual)
Press **Ctrl+C** in the terminal where services are running.

---

## ✅ Health Checks

### Check All Services
```bash
# PowerShell
.\check-services.ps1

# Batch file
.\check-services.bat
```

Shows status of:
- DCIM Server (port 8443)
- Proxy Server (port 3001)
- UI Dev Server (port 5173)

### Manual Port Check
```bash
netstat -ano | findstr ":3001 :5173 :8443"
```

---

## 📋 Available Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| **start-dev.bat** | Start proxy + UI | `.\start-dev.bat` |
| **start-dev.ps1** | Start proxy + UI (PowerShell) | `.\start-dev.ps1` |
| **restart-dev.bat** | Stop existing & restart | `.\restart-dev.bat` |
| **stop-proxy.ps1** | Stop proxy server | `.\stop-proxy.ps1` |
| **stop-proxy.bat** | Stop proxy server (batch) | `.\stop-proxy.bat` |
| **start-proxy-only.bat** | Start only proxy | `.\start-proxy-only.bat` |
| **check-services.ps1** | Health check | `.\check-services.ps1` |
| **check-services.bat** | Health check (batch) | `.\check-services.bat` |

---

## 🔧 NPM Scripts

From `package.json`:

| Command | Description |
|---------|-------------|
| `npm run dev` | Start UI dev server only |
| `npm run proxy` | Start proxy server only |
| `npm run dev:full` | Start both proxy and UI |
| `npm run build` | Build for production |
| `npm run preview` | Preview production build |
| `npm run lint` | Run ESLint |

---

## ⚠️ Common Issues

### "Port 3001 already in use"
**Solution:**
```bash
.\stop-proxy.bat
# Then try starting again
npm run dev:full
```

Or use the restart script:
```bash
.\restart-dev.bat
```

### "Cannot connect to API"
**Check if all services are running:**
```bash
.\check-services.bat
```

**Expected output:**
```
[OK] DCIM Server     is RUNNING on port 8443
[OK] Proxy Server    is RUNNING on port 3001
[OK] UI Dev Server   is RUNNING on port 5173
```

### Services not stopping
**Manual kill:**
```bash
# Find process using port 3001
netstat -ano | findstr :3001

# Kill process (replace <PID> with actual process ID)
powershell -Command "Stop-Process -Id <PID> -Force"
```

---

## 🎯 Recommended Workflow

### Daily Development

1. **Start services:**
   ```bash
   npm run dev:full
   ```

2. **Open browser:**
   ```
   http://localhost:5173
   ```

3. **When done, stop services:**
   Press `Ctrl+C` in the terminal

### When You Get Errors

1. **Check what's running:**
   ```bash
   .\check-services.bat
   ```

2. **If proxy port conflict:**
   ```bash
   .\restart-dev.bat
   ```

3. **If nothing works:**
   ```bash
   .\stop-proxy.bat
   # Wait a moment
   npm run dev:full
   ```

---

## 📁 File Locations

All scripts are in: `E:\Projects\DCIM\DCIM_UI\`

- Configuration: `.env`
- Proxy server: `proxy-server.js`
- Vite config: `vite.config.ts`
- Package scripts: `package.json`

---

## 🆘 Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| Port 3001 in use | `.\stop-proxy.bat` |
| UI can't connect to API | `.\check-services.bat` |
| Topology not loading | Check browser console, verify agent data |
| Certificate errors | Check `certs/` directory |
| Want fresh start | `.\restart-dev.bat` |

---

**Pro Tip:** Use `.\restart-dev.bat` for a guaranteed clean start every time!

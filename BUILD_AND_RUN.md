# DCIM System - Build and Run Guide

Simple guide for building and running the DCIM system locally and in production.

## System Overview

This system consists of 3 interdependent projects:

1. **DCIM_UI** - React/TypeScript web interface (runs on port 5173)
2. **DCIM_Server** - Go backend server (runs on port 8443)
3. **DCIM_Agent** - Go monitoring agent (deployed on monitored machines)

## Prerequisites

### All Projects
- Git
- Text editor

### DCIM_UI
- Node.js 18+ and npm
- Modern web browser

### DCIM_Server & DCIM_Agent
- Go 1.21+
- OpenSSL (for certificates)

### Verify Installation
```powershell
node --version
npm --version
go version
openssl version
```

---

## Quick Start (Local Development)

### 1. Generate Certificates (Required)

Certificates are needed for secure communication between Server and Agent.

**Recommended: Generate in DCIM_Server**
```powershell
cd DCIM_Server
.\scripts\generate-certs.ps1
```

**Features:**
- ✅ Automatic OpenSSL configuration fix
- ✅ Comprehensive error checking
- ✅ Verifies all files created successfully
- ✅ Clear progress and error messages

**Alternative: Generate in DCIM_Agent and copy**
```powershell
cd DCIM_Agent\scripts
.\generate-certs.ps1
cd ..\..
Copy-Item DCIM_Agent\certs\* DCIM_Server\certs\
```

Both options create the same certificates. Agents will need `ca.crt` for mTLS.

### 2. Start DCIM_Server

```powershell
cd DCIM_Server

# Build and run
go build -o dcim-server.exe .\main.go
.\dcim-server.exe -config config.yaml
```

Server will start on: `https://localhost:8443`

### 3. Start DCIM_Agent (Optional)

```powershell
cd DCIM_Agent

# Build and run
go build -o dcim-agent.exe .\main.go
.\dcim-agent.exe -config config.yaml
```

### 4. Start DCIM_UI

```powershell
cd DCIM_UI

# Install dependencies (first time only)
npm install

# Start development server
npm run dev
```

UI will open at: `http://localhost:5173`

---

## Build Commands

### DCIM_UI (Node.js/React)

```powershell
cd DCIM_UI

# Install dependencies
npm install

# Development mode (with hot reload)
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

**Output:** Production build in `dist/` folder

### DCIM_Server (Go)

```powershell
cd DCIM_Server

# Development - Run directly
go run . -config config.yaml

# Build for Windows
go build -o dcim-server.exe .

# Build for Linux
$env:GOOS="linux"; $env:GOARCH="amd64"; go build -o dcim-server .

# Build for production (optimized)
go build -ldflags "-s -w" -o dcim-server.exe .
```

**Or use build script:**
```powershell
# Build for all platforms (Windows, Linux, macOS)
.\build.ps1

# Build for specific platform only
.\build.ps1 -Platform windows
.\build.ps1 -Platform linux
.\build.ps1 -Platform macos
```

**Build Script Options:**

The `build.ps1` script supports multiple platforms:

```powershell
# Build for all platforms (default)
.\build.ps1

# Build for specific platform only
.\build.ps1 -Platform windows   # Windows AMD64
.\build.ps1 -Platform linux     # Linux AMD64
.\build.ps1 -Platform macos     # macOS (both AMD64 and ARM64)

# Custom output directory
.\build.ps1 -OutputDir "dist"
```

**Output locations:**
- `build/windows-amd64/` - Windows executable
- `build/linux-amd64/` - Linux executable
- `build/darwin-amd64/` - macOS Intel executable
- `build/darwin-arm64/` - macOS Apple Silicon executable

Each build directory contains:
- `dcim-server` or `dcim-server.exe` - Server binary
- `config.yaml` - Configuration template
- `certs/` - Empty directory for certificates
- `README.txt` - Quick start instructions

### DCIM_Agent (Go)

```powershell
cd DCIM_Agent

# Development - Run directly
go run . -config config.yaml

# Build for Windows
go build -o dcim-agent.exe .

# Build for Linux
$env:GOOS="linux"; $env:GOARCH="amd64"; go build -o dcim-agent .

# Build deployment package (creates .zip and .tar.gz)
.\build.ps1 -Target dist -Version "1.0.0"
```

**Build Script Options:**

The `build.ps1` script supports multiple targets:

```powershell
# Create deployment packages for all platforms
.\build.ps1 -Target dist -Version "1.0.0"

# Build for specific platform only
.\build.ps1 -Target windows -Version "1.0.0"
.\build.ps1 -Target linux -Version "1.0.0"
.\build.ps1 -Target macos-amd64 -Version "1.0.0"
.\build.ps1 -Target macos-arm64 -Version "1.0.0"

# Build for all platforms (no packaging)
.\build.ps1 -Target all -Version "1.0.0"

# Other targets
.\build.ps1 -Target clean     # Remove build artifacts
.\build.ps1 -Target deps      # Install dependencies
.\build.ps1 -Target test      # Run tests
```

**Output locations:**
- `build/` - Compiled binaries and files for each platform
- `dist/` - Distribution packages (.zip for Windows, .tar.gz for Linux/macOS)

---

## Running Locally

### Development Setup (All Components)

**Terminal 1 - Server:**
```powershell
cd DCIM_Server
.\dcim-server.exe -config config.yaml
```

**Terminal 2 - UI:**
```powershell
cd DCIM_UI
npm run dev
```

**Terminal 3 - Agent (Optional):**
```powershell
cd DCIM_Agent
.\dcim-agent.exe -config config.yaml
```

### Access Points
- UI: http://localhost:5173
- Server API: https://localhost:8443
- Health Check: https://localhost:8443/health

---

## Production Deployment

### DCIM_UI

```powershell
cd DCIM_UI

# Build production bundle
npm run build

# Deploy the 'dist' folder to web server (nginx, Apache, IIS)
# Example: Copy to nginx html directory (adjust path as needed)
Copy-Item -Recurse dist\* C:\nginx\html\
```

**Nginx config example:**
```nginx
server {
    listen 80;
    server_name dcim.example.com;
    root /var/www/dcim-ui;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api {
        proxy_pass https://localhost:8443;
    }
}
```

### DCIM_Server

```powershell
cd DCIM_Server

# Build production binary
go build -ldflags "-s -w" -o dcim-server.exe .

# Copy to deployment location (example path - adjust as needed)
$deployPath = "C:\Production\DCIM"
Copy-Item dcim-server.exe $deployPath\
Copy-Item config.yaml $deployPath\
Copy-Item -Recurse certs $deployPath\
Copy-Item license.json $deployPath\

# Run as Windows service (install service first)
# Or use PowerShell to run in background
cd $deployPath
.\dcim-server.exe -config config.yaml
```

**Linux deployment:**
```bash
# Build for Linux
GOOS=linux GOARCH=amd64 go build -ldflags "-s -w" -o dcim-server .

# Deploy
scp dcim-server user@server:/opt/dcim/
scp config.yaml user@server:/opt/dcim/
scp -r certs user@server:/opt/dcim/

# Run as systemd service
sudo systemctl start dcim-server
sudo systemctl enable dcim-server
```

### DCIM_Agent

```powershell
cd DCIM_Agent

# Build deployment package
.\build.ps1 -Target dist -Version "1.0.0"

# Deploy to target machine
# 1. Copy dist/dcim-agent-windows-*.zip to target machine
# 2. Extract to desired location (e.g., C:\DCIM\Agent\)
# 3. Edit config.yaml (update server URL)
# 4. Install as Windows service:

cd <installation-directory>
.\dcim-agent.exe install
.\dcim-agent.exe start
```

**Linux deployment:**
```bash
# Extract package
tar -xzf dcim-agent-linux-*.tar.gz -C /opt/dcim-agent

# Edit config
nano /opt/dcim-agent/config.yaml

# Install as systemd service
cd /opt/dcim-agent
sudo ./dcim-agent install
sudo systemctl start dcim-agent
sudo systemctl enable dcim-agent
```

---

## Configuration

### DCIM_UI

**Environment variables** (create `.env` file):
```env
VITE_API_URL=https://your-server.com:8443/api/v1
```

### DCIM_Server

**Edit `config.yaml`:**
```yaml
server:
  address: "0.0.0.0"
  port: 8443

tls:
  enabled: true
  server_cert_path: "./certs/server.crt"
  server_key_path: "./certs/server.key"
  ca_cert_path: "./certs/ca.crt"

database:
  type: "sqlite"  # or "postgres" for production
  sqlite:
    path: "./data/dcim_server.db"

license:
  mode: "file"
  file_path: "./license.json"
  enforce: true
```

### DCIM_Agent

**Edit `config.yaml`:**
```yaml
server:
  url: "https://your-server:8443/api/v1"
  tls:
    enabled: true
    client_cert_path: "./certs/client.crt"
    client_key_path: "./certs/client.key"
    ca_cert_path: "./certs/ca.crt"

agent:
  collect_interval: 30s
  send_interval: 120s
```

---

## Common Issues

### Port Already in Use
```powershell
# Windows - Find and kill process
netstat -ano | findstr :8443
taskkill /PID <PID> /F

# Or use the helper script
cd DCIM_Server
.\kill-port-8443.ps1
```

### Certificate Errors
```powershell
# Regenerate certificates
cd DCIM_Agent\scripts
.\generate-certs.ps1

# Copy to server
Copy-Item ..\certs\* ..\..\DCIM_Server\certs\
```

### DCIM_UI Build Fails
```powershell
# Clean and reinstall
cd DCIM_UI
rm -rf node_modules
rm package-lock.json
npm install
```

### Database Locked (SQLite)
```powershell
# Stop all instances
taskkill /IM dcim-server.exe /F

# Restart
.\dcim-server.exe -config config.yaml
```

---

## Health Checks

### Check Server Status
```powershell
curl.exe -k https://localhost:8443/health
```

### Check Agent Status
```powershell
# View logs
Get-Content DCIM_Agent\agent.log -Tail 50

# Check database
ls DCIM_Agent\agent.db
```

### Check UI
```
Open browser: http://localhost:5173
```

---

## Quick Reference

### Running Components

| Component | Port | Protocol | Command |
|-----------|------|----------|---------|
| DCIM_UI | 5173 | HTTP | `npm run dev` |
| DCIM_Server | 8443 | HTTPS | `.\dcim-server.exe` |
| DCIM_Agent | - | - | `.\dcim-agent.exe` |

### Build Commands

| Task | Command |
|------|---------|
| Build UI for production | `cd DCIM_UI && npm run build` |
| Build Server | `cd DCIM_Server && go build -o dcim-server.exe .` |
| Build Server (all platforms) | `cd DCIM_Server && .\build.ps1` |
| Build Server for Windows only | `cd DCIM_Server && .\build.ps1 -Platform windows` |
| Build Server for Linux only | `cd DCIM_Server && .\build.ps1 -Platform linux` |
| Build Agent | `cd DCIM_Agent && go build -o dcim-agent.exe .` |
| Build Agent Package (all platforms) | `cd DCIM_Agent && .\build.ps1 -Target dist -Version "1.0.0"` |
| Build Agent for Windows only | `cd DCIM_Agent && .\build.ps1 -Target windows -Version "1.0.0"` |
| Build Agent for Linux only | `cd DCIM_Agent && .\build.ps1 -Target linux -Version "1.0.0"` |

---

## Project Structure

```
DCIM/
├── DCIM_UI/              # React web interface
│   ├── src/              # Source code
│   ├── package.json      # Dependencies
│   └── vite.config.ts    # Build config
│
├── DCIM_Server/          # Go backend server
│   ├── internal/         # Internal packages
│   ├── config.yaml       # Configuration
│   ├── certs/            # SSL certificates
│   └── main.go           # Main entry point
│
├── DCIM_Agent/           # Go monitoring agent
│   ├── internal/         # Internal packages
│   ├── config.yaml       # Configuration
│   ├── certs/            # SSL certificates
│   └── main.go           # Main file
│
├── BUILD_AND_RUN.md      # This file
└── LICENSE_MANAGEMENT.md # License guide
```

---

For license management, see [LICENSE_MANAGEMENT.md](LICENSE_MANAGEMENT.md)

Last Updated: 2026-02-04

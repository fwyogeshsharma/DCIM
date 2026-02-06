# DCIM - Data Center Infrastructure Monitoring

Complete monitoring solution for data centers and network infrastructure.

## System Components

This project consists of 3 interdependent applications:

### 1. DCIM_UI (Web Interface)
- **Technology**: React + TypeScript + Vite
- **Port**: 5173 (development), configurable (production)
- **Purpose**: Web-based dashboard for viewing metrics, alerts, and devices
- **Documentation**: [DCIM_UI/README.md](DCIM_UI/README.md)

### 2. DCIM_Server (Backend API)
- **Technology**: Go
- **Port**: 8443 (HTTPS with mTLS)
- **Purpose**: Central server receiving and storing data from agents
- **Features**: License management, multi-agent support, database storage
- **Documentation**: [DCIM_Server/README.md](DCIM_Server/README.md)

### 3. DCIM_Agent (Monitoring Agent)
- **Technology**: Go
- **Deployment**: Installed on monitored machines
- **Purpose**: Collects system metrics and SNMP device data
- **Features**: mTLS authentication, local SQLite storage, alert generation
- **Documentation**: [DCIM_Agent/README.md](DCIM_Agent/README.md)

## Quick Start

### Prerequisites
- Node.js 18+ (for DCIM_UI)
- Go 1.21+ (for DCIM_Server and DCIM_Agent)
- OpenSSL (for certificates)

### 1. Generate Certificates
```powershell
# Generate in DCIM_Server (improved with auto-fix)
cd DCIM_Server
.\scripts\generate-certs.ps1

# Certificates will be created in DCIM_Server/certs/
# The script automatically fixes OpenSSL configuration issues
# Copy ca.crt to agents when deploying them
```

### 2. Start Server
```powershell
cd DCIM_Server
go build -o dcim-server.exe .
.\dcim-server.exe -config config.yaml
```

### 3. Start UI
```powershell
cd DCIM_UI
npm install
npm run dev
```

Access the UI at: http://localhost:5173

## Documentation

- **[BUILD_AND_RUN.md](BUILD_AND_RUN.md)** - How to build and run locally/production
- **[LICENSE_MANAGEMENT.md](LICENSE_MANAGEMENT.md)** - License generation and renewal

## Project Structure

```
DCIM/
│
├── DCIM_UI/                    # React web interface
│   ├── src/                    # Source code
│   ├── package.json            # Node.js dependencies
│   └── README.md               # UI documentation
│
├── DCIM_Server/                # Go backend server
│   ├── internal/               # Internal packages
│   ├── config.yaml             # Server configuration
│   ├── certs/                  # SSL/TLS certificates
│   ├── main.go                 # Main entry point
│   ├── README.md               # Server documentation
│   └── CERTIFICATE_MANAGEMENT.md
│
├── DCIM_Agent/                 # Go monitoring agent
│   ├── internal/               # Internal packages
│   ├── config.yaml             # Agent configuration
│   ├── certs/                  # SSL/TLS certificates
│   ├── scripts/                # Installation scripts
│   ├── main.go                 # Main entry point
│   └── README.md               # Agent documentation
│
├── README.md                   # This file
├── BUILD_AND_RUN.md            # Build and run guide
└── LICENSE_MANAGEMENT.md       # License guide
```

## How It Works

```
┌─────────────────┐
│   DCIM_UI       │  Browser Dashboard
│  (Port 5173)    │  View metrics & alerts
└────────┬────────┘
         │ HTTP API
         ▼
┌─────────────────┐
│  DCIM_Server    │  Central Server
│  (Port 8443)    │  Store & manage data
└────────┬────────┘
         │ mTLS
    ┌────┴────┬────────┐
    ▼         ▼        ▼
┌────────┐ ┌────────┐ ┌────────┐
│ Agent1 │ │ Agent2 │ │ AgentN │  Monitor machines
└────────┘ └────────┘ └────────┘  Collect metrics
```

## Features

- Secure mTLS authentication
- Multi-agent monitoring
- SNMP device monitoring
- Real-time metrics collection
- Alert management
- License-based limits
- SQLite/PostgreSQL/MySQL support
- Web-based dashboard
- Cross-platform (Windows, Linux, macOS)

## Common Commands

### Build All
```powershell
# UI
cd DCIM_UI && npm run build

# Server
cd DCIM_Server && go build -o dcim-server.exe .\main.go

# Agent
cd DCIM_Agent && go build -o dcim-agent.exe .\main.go
```

### Run All (Development)
```powershell
# Terminal 1 - Server
cd DCIM_Server && .\dcim-server.exe

# Terminal 2 - UI
cd DCIM_UI && npm run dev

# Terminal 3 - Agent (optional)
cd DCIM_Agent && .\dcim-agent.exe
```

### Health Checks
```powershell
# Server health
curl.exe -k https://localhost:8443/health

# UI
# Open browser: http://localhost:5173
```

## Configuration Files

### DCIM_Server/config.yaml
- Server address and port
- TLS/mTLS settings
- Database configuration
- License settings
- Agent registration rules

### DCIM_Agent/config.yaml
- Server URL and connection settings
- Collection intervals
- SNMP device configuration
- Alert thresholds

### DCIM_UI/.env (optional)
- API endpoint URL
- Other environment variables

## Support

For detailed information:
- **Build instructions**: [BUILD_AND_RUN.md](BUILD_AND_RUN.md)
- **License management**: [LICENSE_MANAGEMENT.md](LICENSE_MANAGEMENT.md)
- **Server details**: [DCIM_Server/README.md](DCIM_Server/README.md)
- **Agent details**: [DCIM_Agent/README.md](DCIM_Agent/README.md)

---

Version: 1.0.0
Last Updated: 2026-02-04

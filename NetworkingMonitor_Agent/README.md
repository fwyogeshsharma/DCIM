# Network Monitor Agent

Enterprise-grade network monitoring agent with mTLS security.

## Quick Start

```powershell
# 1. Generate certificates
cd scripts
.\generate-certs.ps1

# 2. Build agent
cd ..
go build -o network-monitor-agent.exe ./cmd/agent

# 3. Run test server (Terminal 1)
cd examples\test-server
go run .\main-mtls.go

# 4. Run agent (Terminal 2)
cd ..\..
.\network-monitor-agent.exe
```

## Documentation

**📖 [PROJECT_README.md](PROJECT_README.md)** - Complete project documentation
- Overview and features
- Technology stack
- Database schema
- Project structure
- Configuration
- Security (mTLS)
- API endpoints

**🚀 [BUILD_AND_DEPLOY.md](BUILD_AND_DEPLOY.md)** - Build and deployment guide
- Prerequisites
- Certificate generation
- Build instructions
- Testing procedures
- Deployment steps
- Service installation
- Troubleshooting

## Key Features

- ✅ System metrics collection (CPU, memory, disk, network, temperature)
- ✅ SNMP device monitoring
- ✅ Alert engine with thresholds
- ✅ SQLite local storage
- ✅ mTLS (mutual TLS) authentication
- ✅ Cross-platform (Windows, Linux, macOS)
- ✅ Windows Service / Linux daemon support

## Technology

- **Language**: Go 1.21+
- **Database**: SQLite 3
- **Security**: mTLS with X.509 certificates
- **Libraries**: gopsutil, gosnmp, sqlite3, kardianos/service

## Quick Commands

```powershell
# Generate certificates
.\scripts\generate-certs.ps1

# Fix certificate issues
.\fix-certificates.bat

# Build agent
go build -o network-monitor-agent.exe ./cmd/agent

# Build deployment package
.\build.ps1 -Target dist -Version "1.0.0"

# Run test server
cd examples\test-server && go run .\main-mtls.go

# Run agent
.\network-monitor-agent.exe

# Install as service
.\network-monitor-agent.exe install
.\network-monitor-agent.exe start
```

## Project Structure

```
NetworkingMonitor_Client/
├── cmd/agent/              # Application entry point
├── internal/               # Core application code
│   ├── agent/              # Main agent logic
│   ├── collector/          # System metrics collection
│   ├── config/             # Configuration management
│   ├── logger/             # Logging
│   ├── sender/             # mTLS communication
│   ├── storage/            # SQLite database
│   └── snmp/               # SNMP management
├── examples/test-server/   # mTLS test server
├── scripts/                # Installation scripts
├── certs/                  # SSL/TLS certificates
├── config.yaml             # Configuration
└── README.md               # This file
```

## Support

For detailed information, see:
- [PROJECT_README.md](PROJECT_README.md) - Complete project documentation
- [BUILD_AND_DEPLOY.md](BUILD_AND_DEPLOY.md) - Build and deployment guide

---

**Version**: 1.0.0
**Last Updated**: January 29, 2026

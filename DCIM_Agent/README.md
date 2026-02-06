# DICM Agent

Enterprise-grade Data Center Infrastructure Management (DICM) agent with mTLS security.

## Quick Start

```powershell
# 1. Generate certificates
cd scripts
.\generate-certs.ps1

# 2. Build agent
cd ..
go build -o dcim-agent.exe .

# 3. Run test server (Terminal 1)
cd examples\test-server
go run .\main-mtls.go

# 4. Run agent (Terminal 2)
cd ..\..
.\dcim-agent.exe
```

## Documentation

See the root [BUILD_AND_RUN.md](../BUILD_AND_RUN.md) for:
- Build instructions
- Local development setup
- Production deployment
- Certificate management
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
go build -o dcim-agent.exe .

# Build deployment package (all platforms)
.\build.ps1 -Target dist -Version "1.0.0"

# Build for specific platform
.\build.ps1 -Target windows -Version "1.0.0"
.\build.ps1 -Target linux -Version "1.0.0"

# Run test server
cd examples\test-server && go run .\main-mtls.go

# Run agent
.\dcim-agent.exe

# Install as service
.\dcim-agent.exe install
.\dcim-agent.exe start
```

## Project Structure

```
DCIM_Agent/
├── main.go                 # Application entry point
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

For complete system documentation:
- [../BUILD_AND_RUN.md](../BUILD_AND_RUN.md) - Build and deployment guide
- [../LICENSE_MANAGEMENT.md](../LICENSE_MANAGEMENT.md) - License management

---

**Version**: 1.0.0
**Last Updated**: January 29, 2026

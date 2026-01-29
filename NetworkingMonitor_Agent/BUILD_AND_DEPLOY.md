# Build and Deployment Guide

Complete guide for building, testing, and deploying the Network Monitor Agent.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Certificate Generation](#certificate-generation)
3. [Build Agent](#build-agent)
4. [Build Deployment Package](#build-deployment-package)
5. [Test Connection](#test-connection)
6. [Deploy to Target Machine](#deploy-to-target-machine)
7. [Install as Service](#install-as-service)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

**Windows:**
- Go 1.21 or higher
- OpenSSL (for certificate generation)
- Git Bash (optional, for shell scripts)

**Linux/macOS:**
- Go 1.21 or higher
- OpenSSL

### Verify Installation

```powershell
# Check Go version
go version

# Check OpenSSL version
openssl version
```

---

## Certificate Generation

Certificates are **required** for mTLS authentication.

### Generate New Certificates

**PowerShell (Windows):**
```powershell
PS C:\...\NetworkingMonitor_Client> cd scripts
PS C:\...\scripts> .\generate-certs.ps1
```

**Prompts:**
- Server hostname: `localhost` (or your server's hostname/IP)
- Agent identifier: Press `Enter` (uses computer name)

**Bash (Linux/macOS):**
```bash
cd scripts
chmod +x generate-certs.sh
./generate-certs.sh
```

### Fix Certificate Issues

If you encounter certificate errors (legacy CN, missing SAN, CA errors):

```powershell
PS C:\...\NetworkingMonitor_Client> .\fix-certificates.bat
```

This will:
1. Delete old certificates
2. Generate new certificates with proper extensions
3. Fix "certificate relies on legacy Common Name field" errors

### Verify Certificates

```powershell
# Check if certificates exist
dir certs

# Verify certificate chain
openssl verify -CAfile certs\ca.crt certs\server.crt
openssl verify -CAfile certs\ca.crt certs\client.crt

# Check SAN (Subject Alternative Names)
openssl x509 -in certs\server.crt -text -noout | findstr "Subject Alternative"
```

**Expected output:**
```
server.crt: OK
client.crt: OK
X509v3 Subject Alternative Name:
    DNS:localhost, IP Address:127.0.0.1
```

---

## Build Agent

### Method 1: Build Single Executable (Quick)

```powershell
PS C:\...\NetworkingMonitor_Client> go build -o network-monitor-agent.exe ./cmd/agent
```

**Output:** `network-monitor-agent.exe` in current directory

### Method 2: Build with Version Info

```powershell
$version = "1.0.0"
$buildTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
go build -ldflags "-X main.version=$version -X main.buildTime=$buildTime" -o network-monitor-agent.exe ./cmd/agent
```

### Method 3: Build Optimized (Smaller Binary)

```powershell
go build -ldflags "-s -w" -o network-monitor-agent.exe ./cmd/agent
```

Flags:
- `-s`: Strip symbol table
- `-w`: Strip DWARF debug info
- Reduces binary size by ~30-40%

---

## Build Deployment Package

Build complete package with certificates, config, and scripts.

### Build All Platforms

```powershell
PS C:\...\NetworkingMonitor_Client> .\build.ps1 -Target dist -Version "1.0.0"
```

**Output:** `dist/` directory containing:
```
dist/
├── network-monitor-agent-windows-amd64-1.0.0.zip
├── network-monitor-agent-linux-amd64-1.0.0.tar.gz
├── network-monitor-agent-macos-amd64-1.0.0.tar.gz
└── network-monitor-agent-macos-arm64-1.0.0.tar.gz
```

### Build Windows Only

```powershell
PS C:\...\NetworkingMonitor_Client> .\build.ps1 -Target windows -Version "1.0.0"
```

**Output:** `build/windows/` directory

### Package Contents

Each package includes:
```
network-monitor-agent-v1.0.0/
├── network-monitor-agent.exe      # Agent binary
├── config.yaml                    # Configuration file
├── certs/                         # SSL/TLS certificates
│   ├── ca.crt                     # Certificate Authority
│   ├── client.crt                 # Client certificate
│   └── client.key                 # Client private key
├── scripts/
│   ├── install-windows.bat        # Service installer
│   └── uninstall-windows.bat      # Service uninstaller
└── logs/                          # Log directory (empty)
```

---

## Test Connection

### Start Test Server

The test server simulates a monitoring server with mTLS.

```powershell
# Terminal 1 - Start test server
PS C:\...\NetworkingMonitor_Client> cd examples\test-server
PS C:\...\test-server> go run .\main-mtls.go
```

**Expected output:**
```
================================================================================
  Network Monitor Test Server with mTLS
================================================================================

✓ Loaded CA certificate from: ../../certs/ca.crt
✓ Loaded server certificate from: ../../certs/server.crt
✓ Loaded server key from: ../../certs/server.key

🔐 mTLS Configuration:
   - Client authentication: REQUIRED
   - Minimum TLS version: 1.2
   - Certificate verification: ENABLED

🚀 Server starting on https://localhost:8443
```

### Test with curl

**Git Bash or Linux:**
```bash
curl -k -v https://localhost:8443/api/v1/metrics \
  --cert certs/client.crt \
  --key certs/client.key \
  --cacert certs/ca.crt \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: test-agent" \
  -d '{"test": "data"}'
```

**PowerShell (absolute paths):**
```powershell
curl.exe -k -v https://localhost:8443/api/v1/metrics `
  --cert C:\Anupam\Faber\Projects\NetworkingMonitor_Client\certs\client.crt `
  --key C:\Anupam\Faber\Projects\NetworkingMonitor_Client\certs\client.key `
  --cacert C:\Anupam\Faber\Projects\NetworkingMonitor_Client\certs\ca.crt `
  -H "Content-Type: application/json" `
  -H "X-Agent-ID: test-agent" `
  -d "{\"test\": \"data\"}"
```

**Expected response:**
```json
{
  "success": true,
  "message": "Received 0 metrics",
  "accepted": 0,
  "rejected": 0
}
```

### Run Agent Against Test Server

```powershell
# Terminal 2 - Run agent
PS C:\...\NetworkingMonitor_Client> .\network-monitor-agent.exe
```

**Expected agent output:**
```
[INFO] Configuring mTLS (Mutual TLS) authentication...
[INFO] ✓ Loaded client certificate from: ./certs/client.crt
[INFO]   Client cert Subject: Faber
[INFO]   TLS ServerName: localhost
[INFO] ✓ mTLS client configured successfully
[INFO] Agent started successfully
[INFO] Sent 10 metrics successfully
```

**Expected server output:**
```
➡️  POST /api/v1/metrics (Agent: xxx)
🔐 TLS Connection Details:
   - Protocol Version: 771
   - Handshake Complete: true
   - Client Certificate:
     - Subject: Faber
📊 Received 10 metrics from agent xxx
⬅️  POST /api/v1/metrics completed in 50ms
```

---

## Deploy to Target Machine

### Step 1: Extract Package

Extract the built package to target machine:

```powershell
# Extract ZIP (Windows)
Expand-Archive network-monitor-agent-windows-amd64-1.0.0.zip -DestinationPath C:\NetworkMonitor

# Extract tar.gz (Linux)
tar -xzf network-monitor-agent-linux-amd64-1.0.0.tar.gz -C /opt/network-monitor
```

**Package structure:**
```
C:\NetworkMonitor\
├── network-monitor-agent.exe
├── config.yaml
├── certs\
│   ├── ca.crt
│   ├── client.crt
│   └── client.key
└── scripts\
```

### Step 2: Configure for Your Environment

Edit `config.yaml`:

```yaml
server:
  url: "https://your-server.com:8443/api/v1"  # Update to your server
  tls:
    enabled: true
    client_cert_path: "./certs/client.crt"     # Relative paths work
    client_key_path: "./certs/client.key"
    ca_cert_path: "./certs/ca.crt"

agent:
  collect_interval: 30s
  send_interval: 120s

snmp_manager:
  enabled: true                                 # Enable/disable SNMP
  devices:
    - name: "router1"
      host: "192.168.1.1"                       # Update to your devices
      community: "public"
```

### Step 3: Test Run

```powershell
# Run from package directory
PS C:\NetworkMonitor> .\network-monitor-agent.exe
```

Or with custom config:

```powershell
PS C:\NetworkMonitor> .\network-monitor-agent.exe -config config.yaml
```

### Step 4: Check Logs

```powershell
# View logs
PS C:\NetworkMonitor> Get-Content agent.log -Tail 50 -Wait

# Check database
PS C:\NetworkMonitor> dir agent.db
```

---

## Install as Service

### Windows Service

**Install:**
```powershell
PS C:\NetworkMonitor> .\network-monitor-agent.exe install
```

**Start:**
```powershell
PS C:\NetworkMonitor> .\network-monitor-agent.exe start

# Or use Windows Services
net start NetworkMonitorAgent
```

**Stop:**
```powershell
PS C:\NetworkMonitor> .\network-monitor-agent.exe stop

# Or
net stop NetworkMonitorAgent
```

**Uninstall:**
```powershell
PS C:\NetworkMonitor> .\network-monitor-agent.exe uninstall
```

**Using Scripts:**
```powershell
# Install
PS C:\NetworkMonitor> .\scripts\install-windows.bat

# Uninstall
PS C:\NetworkMonitor> .\scripts\uninstall-windows.bat
```

### Linux Systemd Service

**Install:**
```bash
cd /opt/network-monitor
sudo ./scripts/install-linux.sh
```

**Manage service:**
```bash
# Start
sudo systemctl start network-monitor-agent

# Stop
sudo systemctl stop network-monitor-agent

# Enable on boot
sudo systemctl enable network-monitor-agent

# Check status
sudo systemctl status network-monitor-agent

# View logs
sudo journalctl -u network-monitor-agent -f
```

**Uninstall:**
```bash
sudo ./scripts/uninstall-linux.sh
```

---

## Troubleshooting

### Certificate Errors

**Error:** `x509: certificate relies on legacy Common Name field, use SANs instead`

**Solution:**
```powershell
PS C:\...\NetworkingMonitor_Client> .\fix-certificates.bat
```

This regenerates certificates with Subject Alternative Names (SANs).

---

**Error:** `x509: invalid signature: parent certificate cannot sign this kind of certificate`

**Cause:** CA certificate missing CA:TRUE extension

**Solution:** Regenerate certificates using `fix-certificates.bat`

---

**Error:** `x509: certificate signed by unknown authority`

**Cause:** Server and client using different CA certificates

**Solution:**
1. Ensure both use same `ca.crt`
2. Verify certificate chain:
   ```powershell
   openssl verify -CAfile certs\ca.crt certs\client.crt
   ```

---

**Error:** `failed to load client certificate: open ./certs/client.crt: no such file`

**Cause:** Running agent from wrong directory

**Solution:**
```powershell
# Run from directory containing certs/ folder
cd C:\NetworkMonitor
.\network-monitor-agent.exe
```

Or use absolute paths in config.yaml.

---

### Connection Errors

**Error:** `connection refused`

**Cause:** Server not running or wrong URL

**Solution:**
1. Check server is running
2. Verify URL in config.yaml
3. Check firewall allows port 8443

---

**Error:** `TLS handshake error: bad certificate`

**Cause:** Client certificate not valid or not trusted by server

**Solution:**
1. Verify server has same `ca.crt`
2. Check certificate is not expired:
   ```powershell
   openssl x509 -in certs\client.crt -noout -dates
   ```
3. Regenerate if needed

---

### Build Errors

**Error:** `go: command not found`

**Solution:** Install Go from https://go.dev/dl/

---

**Error:** `openssl: command not found`

**Solution (Windows):**
- Install from https://slproweb.com/products/Win32OpenSSL.html
- Or use Git Bash which includes OpenSSL

---

**Error:** `main redeclared in this block`

**Cause:** Running `go build .` which builds all files

**Solution:** Use correct command:
```powershell
go build -o network-monitor-agent.exe ./cmd/agent
```

---

### Runtime Errors

**Error:** `database is locked`

**Cause:** Multiple agent instances or database corruption

**Solution:**
1. Stop all agent instances
2. Delete `agent.db` to reset
3. Restart agent

---

**Error:** `SNMP timeout`

**Cause:** SNMP device unreachable or wrong community string

**Solution:**
1. Verify device IP is correct
2. Check SNMP is enabled on device
3. Verify community string matches
4. Test with snmpwalk:
   ```powershell
   snmpwalk -v2c -c public 192.168.1.1 system
   ```

---

## Quick Reference

### Common Commands

```powershell
# Generate certificates
PS> cd scripts
PS> .\generate-certs.ps1

# Fix certificate issues
PS> .\fix-certificates.bat

# Build agent
PS> go build -o network-monitor-agent.exe ./cmd/agent

# Build deployment package
PS> .\build.ps1 -Target dist -Version "1.0.0"

# Run test server
PS> cd examples\test-server
PS> go run .\main-mtls.go

# Run agent
PS> .\network-monitor-agent.exe

# Run agent with custom config
PS> .\network-monitor-agent.exe -config C:\path\to\config.yaml

# Install as service
PS> .\network-monitor-agent.exe install
PS> .\network-monitor-agent.exe start

# View logs
PS> Get-Content agent.log -Tail 50 -Wait

# Test with curl
PS> curl.exe -k -v https://localhost:8443/api/v1/metrics `
      --cert C:\...\certs\client.crt `
      --key C:\...\certs\client.key `
      --cacert C:\...\certs\ca.crt `
      -H "Content-Type: application/json" `
      -H "X-Agent-ID: test-agent" `
      -d "{\"test\": \"data\"}"
```

### File Locations

```
Development:
  C:\...\NetworkingMonitor_Client\
    ├── certs\                    Certificate files
    ├── config.yaml               Configuration
    ├── agent.db                  Database
    ├── agent.log                 Logs
    └── network-monitor-agent.exe Binary

Deployment:
  C:\NetworkMonitor\
    ├── certs\
    ├── config.yaml
    └── network-monitor-agent.exe

  /opt/network-monitor/ (Linux)
    ├── certs/
    ├── config.yaml
    └── network-monitor-agent
```

### Port Usage

- **8443**: HTTPS (mTLS) - Agent → Server
- **161**: SNMP - Agent → Monitored Devices

---

## Deployment Checklist

Before deploying to production:

- [ ] Generate unique certificates per agent
- [ ] Configure correct server URL in config.yaml
- [ ] Test connection to server
- [ ] Verify SNMP devices are reachable
- [ ] Set appropriate thresholds in config.yaml
- [ ] Test alert generation
- [ ] Configure as service (for auto-start)
- [ ] Set up log rotation
- [ ] Document certificate expiration (1 year)
- [ ] Plan certificate renewal process

---

**Last Updated**: January 29, 2026
**Version**: 1.0.0

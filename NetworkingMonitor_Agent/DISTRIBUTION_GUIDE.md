# Distribution Guide - Network Monitor Agent

This guide explains how to build, package, and distribute the Network Monitor Agent to clients who don't have Go or any development tools installed.

---

## Table of Contents

1. [Developer Setup (One-Time)](#developer-setup-one-time)
2. [Building for Distribution](#building-for-distribution)
3. [Configuring for Production](#configuring-for-production)
4. [Creating Distribution Packages](#creating-distribution-packages)
5. [What to Send to Clients](#what-to-send-to-clients)
6. [Client Installation Instructions](#client-installation-instructions)
7. [Testing Before Distribution](#testing-before-distribution)
8. [Common Issues and Solutions](#common-issues-and-solutions)

---

## Developer Setup (One-Time)

These steps only need to be done ONCE on your development machine.

### 1. Install Go

**Windows:**
- Download from: https://go.dev/dl/
- Download the `.msi` installer (e.g., `go1.21.x.windows-amd64.msi`)
- Run installer with default settings
- Close and reopen PowerShell
- Verify: `go version`

**Linux:**
```bash
wget https://go.dev/dl/go1.21.x.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.21.x.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc
go version
```

**macOS:**
```bash
# Using Homebrew
brew install go

# Or download from https://go.dev/dl/
```

### 2. Download Project Dependencies

```powershell
# Windows PowerShell
cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client
go mod tidy
```

```bash
# Linux/macOS
cd /path/to/NetworkingMonitor_Client
go mod tidy
```

**What this does:**
- Downloads all required packages (SQLite, gopsutil, yaml, service wrapper)
- Creates `go.sum` file with checksums
- Takes 1-2 minutes on first run

### 3. Verify Build System Works

```powershell
# Windows
go build -o network-monitor-agent.exe .

# Should create network-monitor-agent.exe (15-25 MB)
```

```bash
# Linux
go build -o network-monitor-agent .

# macOS
go build -o network-monitor-agent .
```

**Success Indicators:**
- No error messages
- Executable file created
- File size is 15-25 MB (if smaller, build failed)

---

## Building for Distribution

### Quick Build (Single Platform)

**Windows:**
```powershell
# Build for Windows
go build -ldflags "-X main.version=1.0.0 -X main.buildTime=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')" -o network-monitor-agent.exe .
```

**Linux:**
```bash
# Build for Linux
go build -ldflags "-X main.version=1.0.0 -X main.buildTime=$(date -u +%Y-%m-%dT%H:%M:%SZ)" -o network-monitor-agent .
```

### Build All Platforms (Recommended)

**Using PowerShell Script (Windows):**
```powershell
# Build for all platforms
.\build.ps1 -Target all -Version "1.0.0"

# Output in build\ directory:
#   build\windows\network-monitor-agent.exe
#   build\linux\network-monitor-agent
#   build\macos-amd64\network-monitor-agent
#   build\macos-arm64\network-monitor-agent
```

**Using Makefile (Linux/macOS):**
```bash
# Build for all platforms
make build-all VERSION=1.0.0

# Output in build/ directory
```

**Manual Cross-Compilation:**
```powershell
# From Windows, build for:

# Windows
$env:GOOS="windows"; $env:GOARCH="amd64"
go build -o build\windows\network-monitor-agent.exe .

# Linux
$env:GOOS="linux"; $env:GOARCH="amd64"
go build -o build\linux\network-monitor-agent .

# macOS Intel
$env:GOOS="darwin"; $env:GOARCH="amd64"
go build -o build\macos-amd64\network-monitor-agent .

# macOS Apple Silicon
$env:GOOS="darwin"; $env:GOARCH="arm64"
go build -o build\macos-arm64\network-monitor-agent .
```

---

## Configuring for Production

### Before Building

Edit `config.yaml` to set production defaults:

```yaml
# Server settings - IMPORTANT: Change this!
server:
  url: "https://your-monitoring-server.com/api/v1"  # ← CHANGE THIS
  timeout: 30s
  retry_attempts: 3
  retry_delay: 5s

# Agent settings
agent:
  collect_interval: 60s    # How often to collect metrics
  send_interval: 300s      # How often to send to server (5 min)
  batch_size: 50

# Alert thresholds - Adjust based on your needs
alerts:
  cpu:
    warning: 80.0
    critical: 95.0
  memory:
    warning: 85.0
    critical: 95.0
  disk:
    warning: 85.0
    critical: 95.0
  temperature:
    warning: 75.0
    critical: 85.0

# Database settings
database:
  path: "./agent.db"
  retention_days: 30

# Logging
logging:
  level: "info"     # Use "debug" for troubleshooting
  file: "./agent.log"
  max_size_mb: 50
  max_backups: 3
```

**Critical Settings to Change:**
1. `server.url` - Your actual monitoring server URL
2. `alerts` - Adjust thresholds for your environment
3. `logging.level` - Use "info" for production, "debug" for troubleshooting

### Configuration Checklist

Before building for distribution:

- [ ] Server URL points to production server (not localhost)
- [ ] Server URL uses HTTPS (not HTTP)
- [ ] Alert thresholds are appropriate for your environment
- [ ] Collection/send intervals match your requirements
- [ ] Log level is set to "info" (not "debug")
- [ ] Database and log paths are appropriate

---

## Creating Distribution Packages

### Method 1: PowerShell Script (Recommended)

**Build and Package Everything:**
```powershell
# Creates distribution packages for all platforms
.\build.ps1 -Target dist -Version "1.0.0"

# Output in dist\ directory:
#   network-monitor-agent-windows-amd64-1.0.0.zip
#   network-monitor-agent-linux-amd64-1.0.0.tar.gz
#   network-monitor-agent-macos-amd64-1.0.0.tar.gz
#   network-monitor-agent-macos-arm64-1.0.0.tar.gz
```

**Each package contains:**
- Binary executable (network-monitor-agent.exe or network-monitor-agent)
- Configuration file (config.yaml)
- Installation script (install-windows.bat or install-linux.sh or install-macos.sh)
- Uninstallation script (uninstall-*.bat or uninstall-*.sh)

### Method 2: Manual Packaging

**Windows Package:**
```powershell
# 1. Build
go build -o network-monitor-agent.exe .

# 2. Create distribution folder
mkdir dist-windows
copy network-monitor-agent.exe dist-windows\
copy config.yaml dist-windows\
copy scripts\install-windows.bat dist-windows\
copy scripts\uninstall-windows.bat dist-windows\

# 3. Create README for clients
@"
Network Monitor Agent - Installation

1. Extract all files to a folder
2. Edit config.yaml and set your server URL
3. Right-click install-windows.bat and select "Run as administrator"
4. The service will be installed and started automatically

To uninstall:
1. Right-click uninstall-windows.bat and select "Run as administrator"

For support: support@yourcompany.com
"@ | Out-File -FilePath dist-windows\README.txt -Encoding UTF8

# 4. Create zip file
Compress-Archive -Path dist-windows\* -DestinationPath network-monitor-agent-windows-v1.0.0.zip

# 5. Send network-monitor-agent-windows-v1.0.0.zip to client
```

**Linux Package:**
```bash
# 1. Build
GOOS=linux GOARCH=amd64 go build -o network-monitor-agent .

# 2. Create distribution folder
mkdir -p dist-linux
cp network-monitor-agent dist-linux/
cp config.yaml dist-linux/
cp scripts/install-linux.sh dist-linux/
cp scripts/uninstall-linux.sh dist-linux/
chmod +x dist-linux/*.sh

# 3. Create README
cat > dist-linux/README.txt <<EOF
Network Monitor Agent - Installation

1. Extract all files
2. Edit config.yaml and set your server URL
3. Run: sudo ./install-linux.sh
4. The service will be installed and started automatically

To uninstall:
Run: sudo ./uninstall-linux.sh

For support: support@yourcompany.com
EOF

# 4. Create tar.gz
tar -czf network-monitor-agent-linux-v1.0.0.tar.gz -C dist-linux .

# 5. Send network-monitor-agent-linux-v1.0.0.tar.gz to client
```

### Method 3: Using Makefile

```bash
# Linux/macOS only
make dist VERSION=1.0.0

# Creates packages in dist/ directory
```

---

## What to Send to Clients

### Package Contents

Each client receives ONE file:
- Windows: `network-monitor-agent-windows-amd64-1.0.0.zip`
- Linux: `network-monitor-agent-linux-amd64-1.0.0.tar.gz`
- macOS (Intel): `network-monitor-agent-macos-amd64-1.0.0.tar.gz`
- macOS (Apple Silicon): `network-monitor-agent-macos-arm64-1.0.0.tar.gz`

### Client Instructions Template

Create a simple email or document for clients:

```
Subject: Network Monitor Agent - Installation Package

Hello,

Please find attached the Network Monitor Agent installation package.

INSTALLATION STEPS:

Windows:
1. Extract the ZIP file to a folder
2. Right-click "install-windows.bat" and select "Run as administrator"
3. Wait for "Installation Complete!" message
4. The agent is now running as a Windows service

Linux:
1. Extract the tar.gz file: tar -xzf network-monitor-agent-linux-*.tar.gz
2. Run: sudo ./install-linux.sh
3. The agent is now running as a systemd service

macOS:
1. Extract the tar.gz file: tar -xzf network-monitor-agent-macos-*.tar.gz
2. Run: sudo ./install-macos.sh
3. The agent is now running as a launchd service

VERIFICATION:

Windows: Open Services (services.msc) and look for "Network Monitor Agent"
Linux: Run "systemctl status network-monitor-agent"
macOS: Run "sudo launchctl list | grep network-monitor-agent"

REQUIREMENTS:
- No special software needed
- Administrator/root access required for installation
- Internet connection to reach monitoring server

If you have any issues, please contact: support@yourcompany.com

Best regards,
Your Team
```

---

## Client Installation Instructions

### Windows Client Steps

**What client needs:**
- Windows 10 or Server 2016+
- Administrator privileges
- Internet connection

**Installation:**
1. Extract `network-monitor-agent-windows-amd64-1.0.0.zip`
2. (Optional) Edit `config.yaml` if server URL needs customization
3. Right-click `install-windows.bat` → "Run as administrator"
4. Wait for success message

**Verification:**
```
1. Press Win+R
2. Type: services.msc
3. Look for "Network Monitor Agent"
4. Status should be "Running"
```

**Logs Location:**
- `C:\Program Files\NetworkMonitorAgent\agent.log`

**Uninstallation:**
1. Navigate to: `C:\Program Files\NetworkMonitorAgent`
2. Right-click `uninstall-windows.bat` → "Run as administrator"

### Linux Client Steps

**What client needs:**
- systemd-based Linux (Ubuntu 16.04+, CentOS 7+, Debian 9+)
- root/sudo access
- Internet connection

**Installation:**
```bash
# 1. Extract
tar -xzf network-monitor-agent-linux-amd64-1.0.0.tar.gz
cd network-monitor-agent-linux-amd64

# 2. (Optional) Edit config
nano config.yaml

# 3. Install
sudo ./install-linux.sh
```

**Verification:**
```bash
# Check status
systemctl status network-monitor-agent

# View logs
journalctl -u network-monitor-agent -f
```

**Logs Location:**
- `/var/log/network-monitor-agent/agent.log`
- Also in systemd journal: `journalctl -u network-monitor-agent`

**Uninstallation:**
```bash
sudo /opt/network-monitor-agent/uninstall-linux.sh
```

### macOS Client Steps

**What client needs:**
- macOS 10.15+
- Administrator privileges
- Internet connection

**Installation:**
```bash
# 1. Extract
tar -xzf network-monitor-agent-macos-*.tar.gz
cd network-monitor-agent-macos-*

# 2. (Optional) Edit config
nano config.yaml

# 3. Install
sudo ./install-macos.sh
```

**Verification:**
```bash
# Check if running
sudo launchctl list | grep network-monitor-agent

# View logs
tail -f /usr/local/var/log/network-monitor-agent/agent.log
```

**Logs Location:**
- `/usr/local/var/log/network-monitor-agent/agent.log`

**Uninstallation:**
```bash
sudo /usr/local/opt/network-monitor-agent/uninstall-macos.sh
```

---

## Testing Before Distribution

### Pre-Distribution Testing Checklist

Before sending to clients, test the package:

**1. Test Standalone Execution**

```powershell
# Windows
mkdir test-deployment
copy network-monitor-agent.exe test-deployment\
copy config.yaml test-deployment\
cd test-deployment

# Run directly (not as service)
.\network-monitor-agent.exe -config config.yaml

# Watch for:
# - [INFO] Agent starting
# - [INFO] Collected and stored metrics
# - [INFO] Sent X metrics successfully
```

**2. Test Service Installation**

```powershell
# Windows (as Administrator)
cd test-deployment
copy ..\scripts\install-windows.bat .
.\install-windows.bat

# Verify in services.msc
# Should see "Network Monitor Agent" running
```

**3. Test Server Communication**

Make sure your monitoring server is running and check:
- Agent logs show successful sends
- Server receives data
- Alerts work (generate high CPU load)

**4. Test Offline Behavior**

```powershell
# 1. Stop monitoring server
# 2. Let agent run for 5 minutes
# 3. Check agent.db file is growing (data being stored)
# 4. Start monitoring server
# 5. Watch agent catch up and send accumulated data
```

**5. Test Configuration Changes**

```powershell
# 1. Edit config.yaml (change collection interval)
# 2. Restart service
# 3. Verify new settings take effect
```

### Test Matrix

Test on clean machines if possible:

| Platform | Installation | Service Start | Metrics | Alerts | Uninstall |
|----------|-------------|---------------|---------|---------|-----------|
| Windows 10 | [ ] | [ ] | [ ] | [ ] | [ ] |
| Windows 11 | [ ] | [ ] | [ ] | [ ] | [ ] |
| Windows Server 2019 | [ ] | [ ] | [ ] | [ ] | [ ] |
| Ubuntu 20.04 | [ ] | [ ] | [ ] | [ ] | [ ] |
| Ubuntu 22.04 | [ ] | [ ] | [ ] | [ ] | [ ] |
| CentOS 8 | [ ] | [ ] | [ ] | [ ] | [ ] |
| macOS 12 (Intel) | [ ] | [ ] | [ ] | [ ] | [ ] |
| macOS 13 (Apple Silicon) | [ ] | [ ] | [ ] | [ ] | [ ] |

---

## Common Issues and Solutions

### Issue: "go: command not found"

**Problem:** Go is not installed or not in PATH

**Solution:**
```powershell
# Verify Go installation
go version

# If not found, reinstall Go and restart terminal
```

### Issue: Build fails with "undefined: cpu.LoadAvg"

**Problem:** Using wrong gopsutil API

**Solution:** Already fixed in the code. Use `load.Avg()` instead of `cpu.LoadAvg()`

### Issue: Client gets "not a valid application for this OS platform"

**Problem:** Wrong architecture or incomplete build

**Solution:**
```powershell
# Check file size
dir network-monitor-agent.exe

# Should be 15-25 MB. If smaller, build failed.
# Rebuild with verbose output:
go build -v -o network-monitor-agent.exe .
```

### Issue: "missing go.sum entry"

**Problem:** Dependencies not downloaded

**Solution:**
```powershell
go mod tidy
go build -o network-monitor-agent.exe .
```

### Issue: Client can't connect to server

**Problem:** Wrong URL in config or firewall blocking

**Solution:**
1. Check `config.yaml` has correct server URL
2. Test connectivity: `curl https://your-server.com/api/v1/metrics`
3. Check firewall allows outbound HTTPS
4. Check agent logs for specific error

### Issue: Service won't start on client machine

**Problem:** Various causes

**Solution:**
1. Check Windows Event Viewer (Windows)
2. Check `journalctl -u network-monitor-agent` (Linux)
3. Verify config file exists and is valid YAML
4. Try running manually first: `.\network-monitor-agent.exe -config config.yaml`

### Issue: High memory usage

**Problem:** Database growing too large

**Solution:**
```yaml
# In config.yaml, reduce retention
database:
  retention_days: 7  # Instead of 30
```

---

## Release Checklist

Before each release:

### Pre-Build
- [ ] Update version number in build command
- [ ] Update `config.yaml` with production server URL
- [ ] Update alert thresholds if needed
- [ ] Update log level to "info"
- [ ] Review CHANGELOG (if you maintain one)

### Build
- [ ] Run `go mod tidy`
- [ ] Build for all platforms
- [ ] Verify all executables created successfully
- [ ] Check file sizes (15-25 MB each)

### Testing
- [ ] Test on at least one Windows machine
- [ ] Test on at least one Linux machine
- [ ] Verify service installation works
- [ ] Verify metrics reach server
- [ ] Verify alerts work
- [ ] Test uninstallation

### Packaging
- [ ] Create distribution packages for all platforms
- [ ] Include all required files (exe, config, scripts)
- [ ] Add README.txt to each package
- [ ] Name packages with version number

### Documentation
- [ ] Update client installation instructions
- [ ] Update support contact information
- [ ] Document any configuration changes
- [ ] Note any breaking changes

### Distribution
- [ ] Upload to file sharing location
- [ ] Send download links to clients
- [ ] Send installation instructions
- [ ] Notify support team of new release

---

## Quick Reference

### Build Commands

```powershell
# Windows - Single platform
go build -o network-monitor-agent.exe .

# Windows - All platforms
.\build.ps1 -Target all -Version "1.0.0"

# Windows - Create distribution packages
.\build.ps1 -Target dist -Version "1.0.0"
```

```bash
# Linux/macOS - Single platform
go build -o network-monitor-agent .

# Linux/macOS - All platforms
make build-all VERSION=1.0.0

# Linux/macOS - Create distribution packages
make dist VERSION=1.0.0
```

### File Locations After Installation

**Windows:**
- Binary: `C:\Program Files\NetworkMonitorAgent\network-monitor-agent.exe`
- Config: `C:\Program Files\NetworkMonitorAgent\config.yaml`
- Database: `C:\Program Files\NetworkMonitorAgent\agent.db`
- Logs: `C:\Program Files\NetworkMonitorAgent\agent.log`

**Linux:**
- Binary: `/opt/network-monitor-agent/network-monitor-agent`
- Config: `/etc/network-monitor-agent/config.yaml`
- Database: `/var/lib/network-monitor-agent/agent.db`
- Logs: `/var/log/network-monitor-agent/agent.log`

**macOS:**
- Binary: `/usr/local/opt/network-monitor-agent/network-monitor-agent`
- Config: `/usr/local/etc/network-monitor-agent/config.yaml`
- Database: `/usr/local/var/network-monitor-agent/agent.db`
- Logs: `/usr/local/var/log/network-monitor-agent/agent.log`

---

## Support Resources

### For You (Developer)
- `README.md` - Complete documentation
- `ARCHITECTURE.md` - Technical design
- `QUICKSTART.md` - Quick testing guide
- This file - Distribution guide

### For Clients
- Installation scripts (automated)
- `config.yaml` (simple configuration)
- Log files (troubleshooting)
- Support contact (you!)

---

## Summary

**Your Workflow (Each Release):**
1. Edit `config.yaml` with production settings
2. Run: `.\build.ps1 -Target dist -Version "X.Y.Z"`
3. Test one package on a clean machine
4. Upload packages to distribution server
5. Send download link + instructions to clients

**Client Workflow:**
1. Download package
2. Extract files
3. Run installer as administrator
4. Done!

**Key Point:** Clients need ZERO technical knowledge. They just run the installer. All the complexity (Go, dependencies, compilation) happens on YOUR machine during the build process.

---

## Need Help?

If you encounter issues not covered here:
1. Check `README.md` for general documentation
2. Check `ARCHITECTURE.md` for technical details
3. Review `QUICKSTART.md` for testing procedures
4. Check agent logs for specific errors
5. Test with the included test server first

---

**Last Updated:** 2026-01-20
**Version:** 1.0.0




 # 1. Navigate to where your executable IS located
  cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client

  # 2. Uninstall the broken service
  .\network-monitor-agent.exe -service uninstall

  # 3. Reinstall with correct paths
  .\network-monitor-agent.exe -service install

  # 4. Start the service
  .\network-monitor-agent.exe -service start
  
  
  
  
   📋 View Service Logs and Status

  Method 1: View Logs in PowerShell (Recommended)

  # Navigate to where the service is installed
  cd C:\Users\anupa\OneDrive\Desktop\windows

  # View last 20 lines of the log
  Get-Content agent.log -Tail 20

  # Or view last 50 lines for more detail
  Get-Content agent.log -Tail 50

  # Watch logs in REAL-TIME (like tail -f on Linux)
  Get-Content agent.log -Wait -Tail 20

  The -Wait flag will keep the window open and show new log entries as they appear (press Ctrl+C to stop).

  ---
  Method 2: Open Log in Notepad

  # Open in Notepad
  notepad agent.log

  # Or use default text editor
  start agent.log

  ---
  🔍 What to Look For in Logs

  ✅ Success Messages (Good Signs)

  [INFO] Agent starting (ID: Faber-123456, Name: Faber)
  [INFO] System info: Faber (windows x86_64) - 8 cores, 7834 MB RAM
  [INFO] Agent running. Collection interval: 60s, Send interval: 300s
  [INFO] Collected and stored metrics: CPU=12.34%, Memory=56.78%, Disks=2
  [INFO] Processing 8 unsent metrics
  [INFO] Sent 8 metrics successfully

  This means:
  - ✅ Service is running
  - ✅ Collecting metrics every 60 seconds
  - ✅ Storing data locally
  - ✅ Successfully sending to server

  ---
  ⚠️ Warning Messages (Alerts Generated)

  [WARN] Generated 1 alerts requiring immediate transmission
  [INFO] Alert sent: WARNING - cpu WARNING: 82.30% (threshold: 80.00%)

  This means:
  - ⚠️ System exceeded a threshold
  - ✅ Alert was sent immediately to server

  ---
  ❌ Error Messages (Problems)

  Connection Errors:
  [ERROR] Failed to send metrics: Post "http://localhost:8080/api/v1/metrics": dial tcp 127.0.0.1:8080:
  connect: connection refused
  [ERROR] Failed to send immediate alerts: failed to send alerts after 3 attempts

  This means:
  - ❌ Can't reach the monitoring server
  - ⚠️ Data is being stored locally (will be sent when server is available)

  Configuration Errors:
  [ERROR] Failed to load config: open config.yaml: no such file or directory

  This means:
  - ❌ Config file missing or wrong path

  ---
  🖥️ Quick Status Check Script

  Save this as check-agent.ps1 or just run it directly:

● Write(check-agent.ps1)
  ⎿  Wrote 1 lines to check-agent.ps1
     # Network Monitor Agent - Status Check Script
     # Shows service status, recent logs, and connection health
     param(
         [string]$LogPath = "C:\Users\anupa\OneDrive\Desktop\windows\agent.log"
     )
     Write-Host "========================================" -ForegroundColor Cyan
     Write-Host "Network Monitor Agent - Status Check" -ForegroundColor Cyan
     Write-Host "========================================" -ForegroundColor Cyan

● Perfect! Now run this status check script:

  🚀 Quick Status Check

  # Run the status check script
  cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client
  .\check-agent.ps1

  Or if your logs are in a different location:
  .\check-agent.ps1 -LogPath "C:\Users\anupa\OneDrive\Desktop\windows\agent.log"

  This will show you:
  - ✅ Service status (running/stopped)
  - ✅ Recent activity summary
  - ✅ Error count and recent errors
  - ✅ Successful transmissions
  - ✅ Alert history
  - ✅ Connection status (online/offline)
  - ✅ Database size

  ---
  📊 Manual Commands (Quick Reference)

  View Recent Logs

  # Last 20 lines
  Get-Content C:\Users\anupa\OneDrive\Desktop\windows\agent.log -Tail 20

  # Last 50 lines
  Get-Content C:\Users\anupa\OneDrive\Desktop\windows\agent.log -Tail 50

  Watch Logs in Real-Time

  # Live log viewing (like tail -f)
  Get-Content C:\Users\anupa\OneDrive\Desktop\windows\agent.log -Wait -Tail 20

  # Press Ctrl+C to stop watching

  Search for Specific Messages

  # Find all errors
  Get-Content agent.log | Select-String -Pattern "\[ERROR\]"

  # Find successful sends
  Get-Content agent.log | Select-String -Pattern "Sent.*successfully"

  # Find alerts
  Get-Content agent.log | Select-String -Pattern "alerts"

  # Find connection errors
  Get-Content agent.log | Select-String -Pattern "connection refused|timeout"

  Open Log in Notepad

  notepad C:\Users\anupa\OneDrive\Desktop\windows\agent.log

  ---
  �� What You Should See (Healthy Agent)

  If everything is working correctly, you should see logs like this:

  2026/01/20 18:49:47 [INFO] Agent starting (ID: Faber-1737385787, Name: Faber)
  2026/01/20 18:49:47 [INFO] System info: Faber (windows x86_64) - 8 cores, 7834 MB RAM
  2026/01/20 18:49:47 [INFO] Agent running. Collection interval: 60s, Send interval: 300s
  2026/01/20 18:50:47 [INFO] Collected and stored metrics: CPU=15.23%, Memory=45.67%, Disks=2
  2026/01/20 18:51:47 [INFO] Collected and stored metrics: CPU=12.34%, Memory=46.12%, Disks=2
  2026/01/20 18:54:47 [INFO] Processing 8 unsent metrics
  2026/01/20 18:54:47 [INFO] Sent 8 metrics successfully

  ---
  ⚠️ Common Issues You Might See

  Issue 1: Connection Errors

  [ERROR] Failed to send metrics: dial tcp 127.0.0.1:8080: connect: connection refused

  Meaning: Can't reach the monitoring server

  Solutions:
  - Check if test server is running
  - Check config.yaml - is the server URL correct?
  - Check firewall isn't blocking outbound connections

  Important: Even with connection errors, the agent is still working! It's storing data locally and will send
  it when the server becomes available.

  ---
  Issue 2: Server URL Not Set

  [ERROR] Post "http://localhost:8080/api/v1/metrics": connection refused

  Meaning: Trying to send to localhost but no server is running there

  Solution:
  1. Either start the test server:
  cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client\examples\test-server
  go run main.go
  2. Or update config.yaml with your production server URL:
  notepad C:\Users\anupa\OneDrive\Desktop\windows\config.yaml
  # Change: url: "http://localhost:8080/api/v1"
  # To: url: "https://your-production-server.com/api/v1"

  # Then restart service:
  Restart-Service NetworkMonitorAgent

  ---
  Issue 3: No Recent Activity

  # Empty log file or very old timestamps

  Meaning: Service might not be running or started recently

  Solution:
  # Check service is running
  Get-Service NetworkMonitorAgent

  # If stopped, start it:
  Start-Service NetworkMonitorAgent

  # Wait 2 minutes, then check logs again

  ---
  🔄 Quick Commands Cheat Sheet

  # === VIEWING LOGS ===
  # Quick view
  Get-Content agent.log -Tail 20

  # Live monitoring
  Get-Content agent.log -Wait -Tail 20

  # Find errors
  Get-Content agent.log | Select-String "ERROR"

  # === SERVICE MANAGEMENT ===
  # Check status
  Get-Service NetworkMonitorAgent

  # Restart
  Restart-Service NetworkMonitorAgent

  # Stop
  Stop-Service NetworkMonitorAgent

  # Start
  Start-Service NetworkMonitorAgent

  # === COMPREHENSIVE CHECK ===
  .\check-agent.ps1

  ---
  🎬 Try This Now

  Run these commands to see what your agent is doing:

  # 1. Navigate to the log directory
  cd C:\Users\anupa\OneDrive\Desktop\windows

  # 2. Run status check
  ..\..\..\check-agent.ps1 -LogPath ".\agent.log"

  # 3. Watch live logs for 30 seconds
  Get-Content agent.log -Wait -Tail 20
  # (Press Ctrl+C after 30 seconds)
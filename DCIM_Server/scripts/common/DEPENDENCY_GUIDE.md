# Dependency Guide - What Each Script Needs

This guide clarifies **exactly what dependencies each script requires** and which scripts can run without Go.

---

## ✅ Scripts That DON'T Need Go

These scripts can run on any machine without Go installed:

### 1. **generate-certs.ps1 / generate-certs.sh**
- ✅ **Requires:** OpenSSL only
- ❌ **Does NOT require:** Go, PostgreSQL, or any other tools
- **Purpose:** Generate mTLS certificates for secure communication
- **Use case:** Run on any machine to create certificates, then distribute them

### 2. **check-cert-expiry.ps1 / check-cert-expiry.sh**
- ✅ **Requires:** OpenSSL only
- ❌ **Does NOT require:** Go, PostgreSQL, or any other tools
- **Purpose:** Check certificate expiration dates
- **Use case:** Monitor certificate health on any machine

### 3. **setup-postgres.ps1 / setup-postgres.sh**
- ✅ **Requires:** PostgreSQL and psql command-line tool only
- ❌ **Does NOT require:** Go or OpenSSL
- **Purpose:** Setup PostgreSQL database
- **Use case:** Initialize database on the server machine

### 4. **renew-server-cert.ps1 / renew-client-cert.ps1**
- ✅ **Requires:** OpenSSL + existing CA certificates
- ❌ **Does NOT require:** Go or PostgreSQL
- **Purpose:** Renew certificates without regenerating CA
- **Use case:** Periodic certificate renewal

---

## ⚠️ Scripts That DO Need Go

These scripts compile Go source code and **require Go to be installed**:

### 1. **build.ps1 / build.sh** (in DCIM_Server root, not scripts/)
- ✅ **Requires:** Go compiler
- **Purpose:** Compile DCIM Server from source code
- **Use case:** Development machines or build servers only
- **Note:** You don't need to run this on production machines!

### 2. **../DCIM_Agent/build.ps1** (Agent build script)
- ✅ **Requires:** Go compiler
- **Purpose:** Compile DCIM Agent from source code
- **Use case:** Development machines or build servers only
- **Note:** You don't need to run this on agent machines!

---

## 🎯 Common Deployment Scenarios

### Scenario 1: Deploying Pre-Built Server
**You already have `dcim-server.exe` or `dcim-server` binary**

**On Windows:**
```powershell
# Generate certificates
cd DCIM_Server
.\scripts\generate-certs.ps1

# Setup database
.\scripts\setup-postgres.ps1

# Run server (NO GO NEEDED!)
.\build\windows-amd64\dcim-server.exe
```

**On Linux:**
```bash
# Generate certificates
cd DCIM_Server
chmod +x scripts/generate-certs.sh
./scripts/generate-certs.sh

# Setup database
chmod +x scripts/setup-postgres.sh
./scripts/setup-postgres.sh

# Run server (NO GO NEEDED!)
./build/linux-amd64/dcim-server
```

**Dependencies needed:** OpenSSL, PostgreSQL (if using Postgres)
**Go needed:** ❌ NO

---

### Scenario 2: Deploying Pre-Built Agent
**You already have `dcim-agent.exe` or `dcim-agent` binary**

**On Windows:**
```cmd
# Just run the installer
cd DCIM_Agent\build\windows
install-windows.bat
```

**On Linux:**
```bash
# Just run the installer
cd DCIM_Agent/build/linux
sudo chmod +x install-linux.sh
sudo ./install-linux.sh
```

**Dependencies needed:** Certificates (from server)
**Go needed:** ❌ NO

---

### Scenario 3: Building from Source
**You want to compile the code yourself**

**Requirements:**
- ✅ Go 1.20 or higher
- ✅ OpenSSL (for certificates)
- ✅ PostgreSQL (if using Postgres database)

**On Windows:**
```powershell
# Build server
cd DCIM_Server
.\build.ps1

# Build agent
cd ..\DCIM_Agent
.\build.ps1 -Target all
```

**On Linux/Mac:**
```bash
# Build server
cd DCIM_Server
chmod +x build.sh
./build.sh

# Build agent
cd ../DCIM_Agent
chmod +x build.ps1  # Note: currently only PowerShell version exists
./build.ps1 -Target all
```

**Go needed:** ✅ YES (only for building, not for running)

---

## 📦 Recommended Workflow

### For Development Machine (has Go):
1. Install: Go, OpenSSL, PostgreSQL
2. Build server: `./build.ps1` or `./build.sh`
3. Build agent: `cd ../DCIM_Agent && ./build.ps1 -Target dist`
4. Generate certificates: `./scripts/generate-certs.ps1`
5. Setup database: `./scripts/setup-postgres.ps1`
6. Package binaries from `build/` directories
7. Distribute packages to production machines

### For Production Server (no Go needed):
1. Install: OpenSSL, PostgreSQL
2. Copy pre-built `dcim-server` binary from dev machine
3. Run: `./scripts/generate-certs.sh` (or use existing certs)
4. Run: `./scripts/setup-postgres.sh`
5. Run server: `./dcim-server`

### For Agent Machines (no Go needed):
1. Install: Nothing! (maybe OpenSSL if generating certs locally)
2. Copy pre-built `dcim-agent` binary from dev machine
3. Copy certificates from server
4. Run installer: `./install-linux.sh` or `install-windows.bat`

---

## 🔍 Dependency Installation

### Installing OpenSSL

**Windows:**
```powershell
# Download from: https://slproweb.com/products/Win32OpenSSL.html
# Or use Chocolatey:
choco install openssl
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get update
sudo apt-get install openssl
```

**Linux (RHEL/CentOS):**
```bash
sudo yum install openssl
```

**macOS:**
```bash
brew install openssl
```

---

### Installing PostgreSQL

**Windows:**
```powershell
# Download from: https://www.postgresql.org/download/windows/
# Or use winget:
winget install PostgreSQL.PostgreSQL
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install postgresql postgresql-client
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**Linux (RHEL/CentOS):**
```bash
sudo yum install postgresql-server postgresql
sudo postgresql-setup initdb
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**macOS:**
```bash
brew install postgresql
brew services start postgresql
```

---

### Installing Go (ONLY if building from source)

**Windows:**
```powershell
# Download from: https://golang.org/dl/
# Or use Chocolatey:
choco install golang
```

**Linux:**
```bash
# Download from: https://golang.org/dl/
wget https://go.dev/dl/go1.21.5.linux-amd64.tar.gz
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.21.5.linux-amd64.tar.gz
echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
source ~/.bashrc
```

**macOS:**
```bash
brew install go
```

---

## 🆘 Troubleshooting

### "Go not found" when running generate-certs.sh
**Solution:** You don't need Go for this! It only needs OpenSSL. Check if OpenSSL is installed:
```bash
openssl version
```

### "Go not found" when running setup-postgres.sh
**Solution:** You don't need Go for this! It only needs PostgreSQL. Check if psql is installed:
```bash
psql --version
```

### "Go not found" when running build.ps1
**Solution:** This is correct - build scripts DO need Go. Install Go or use pre-built binaries instead.

### Scripts asking for specific changes
**Solution:** Make sure you're using the correct script version:
- Windows: Use `.ps1` files
- Linux/Mac: Use `.sh` files (and make them executable with `chmod +x`)

---

## 📊 Quick Reference Table

| Script | Go? | OpenSSL? | PostgreSQL? | Purpose |
|--------|-----|----------|-------------|---------|
| `generate-certs.*` | ❌ | ✅ | ❌ | Generate certificates |
| `check-cert-expiry.*` | ❌ | ✅ | ❌ | Check cert expiration |
| `setup-postgres.*` | ❌ | ❌ | ✅ | Setup database |
| `renew-*-cert.*` | ❌ | ✅ | ❌ | Renew certificates |
| `build.ps1/sh` | ✅ | ❌ | ❌ | **Compile source code** |
| `dcim-server` binary | ❌ | ❌ | ✅ | **Run server** |
| `dcim-agent` binary | ❌ | ❌ | ❌ | **Run agent** |

---

**Key Takeaway:** Once you have pre-built binaries, you don't need Go on production machines. Only the certificate and database setup scripts are needed, which only require OpenSSL and PostgreSQL respectively.

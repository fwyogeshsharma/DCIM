# Script Index - All Available Scripts

Complete list of all scripts in the DCIM_Server/scripts directory with their purposes and platform support.

---

## 🔐 Certificate Management Scripts

### 1. generate-certs (Full Certificate Generation)
**Purpose:** Generate complete certificate set (CA + Server + Client)

- **Windows:** `generate-certs.ps1`
- **Linux/Mac:** `generate-certs.sh`
- **Dependencies:** OpenSSL
- **Interactive:** Yes (asks for hostname, validity periods)
- **Output:** `certs/ca.crt`, `certs/ca.key`, `certs/server.crt`, `certs/server.key`, `certs/client.crt`, `certs/client.key`

**Usage:**
```bash
# Windows
.\scripts\generate-certs.ps1

# Linux/Mac
chmod +x scripts/generate-certs.sh
./scripts/generate-certs.sh
```

---

### 2. generate-client-cert (Additional Client Certificates)
**Purpose:** Generate additional client certificates for new agents

- **Windows:** `generate-client-cert.ps1 -AgentName "agent-name"`
- **Linux/Mac:** `generate-client-cert.sh -a "agent-name"`
- **Dependencies:** OpenSSL, existing CA certificates
- **Interactive:** Yes (asks for validity period)
- **Output:** `certs/agents/<agent-name>/client.crt`, `client.key`, `ca.crt`

**Usage:**
```bash
# Windows
.\scripts\generate-client-cert.ps1 -AgentName "agent-02"

# Linux/Mac
./scripts/generate-client-cert.sh -a "agent-02"
./scripts/generate-client-cert.sh --agent "agent-02" --days 730
```

---

### 3. renew-server-cert (Renew Server Certificate)
**Purpose:** Renew server certificate using existing CA

- **Windows:** `renew-server-cert.ps1`
- **Linux/Mac:** `renew-server-cert.sh`
- **Dependencies:** OpenSSL, existing CA certificates
- **Interactive:** Yes (asks for hostname)
- **Output:** New `certs/server.crt` and `certs/server.key`

**Usage:**
```bash
# Windows
.\scripts\renew-server-cert.ps1
.\scripts\renew-server-cert.ps1 -ValidityDays 730

# Linux/Mac
./scripts/renew-server-cert.sh
./scripts/renew-server-cert.sh --days 730 --no-backup
```

---

### 4. renew-client-cert (Renew Client Certificate)
**Purpose:** Renew client certificate for a specific agent

- **Windows:** `renew-client-cert.ps1 -AgentName "agent-name"`
- **Linux/Mac:** `renew-client-cert.sh -a "agent-name"`
- **Dependencies:** OpenSSL, existing CA certificates
- **Interactive:** Yes (asks for validity period)
- **Output:** New certificate in `certs/agents/<agent-name>/`

**Usage:**
```bash
# Windows
.\scripts\renew-client-cert.ps1 -AgentName "agent-02"

# Linux/Mac
./scripts/renew-client-cert.sh -a "agent-02"
./scripts/renew-client-cert.sh --agent "FABER" --days 365
```

---

### 5. check-cert-expiry (Certificate Expiry Checker)
**Purpose:** Check all certificates for expiration dates

- **Windows:** `check-cert-expiry.ps1`
- **Linux/Mac:** `check-cert-expiry.sh`
- **Dependencies:** OpenSSL
- **Interactive:** No (read-only)
- **Output:** Console report with expiry dates and warnings

**Usage:**
```bash
# Windows
.\scripts\check-cert-expiry.ps1

# Linux/Mac
./scripts/check-cert-expiry.sh
```

---

## 🗄️ Database Management Scripts

### 6. setup-postgres (PostgreSQL Setup)
**Purpose:** Setup PostgreSQL database for DCIM Server

- **Windows:** `setup-postgres.ps1`
- **Linux/Mac:** `setup-postgres.sh`
- **Dependencies:** PostgreSQL, psql
- **Interactive:** Yes (asks for password, SSL settings)
- **Output:** Creates `dcim_db` database

**Usage:**
```bash
# Windows
.\scripts\setup-postgres.ps1

# Linux/Mac
./scripts/setup-postgres.sh
```

---

### 7. fix-postgres-path (Windows PostgreSQL Path Fix)
**Purpose:** Fix PostgreSQL PATH issues on Windows

- **Windows:** `fix-postgres-path.ps1`
- **Linux/Mac:** N/A (not needed)
- **Dependencies:** PostgreSQL (Windows)
- **Interactive:** Yes
- **Output:** Updates system PATH

**Usage:**
```powershell
# Windows only
.\scripts\fix-postgres-path.ps1
```

---

## 📚 Documentation Files

### 8. SCRIPTS_README.md
Complete guide for all scripts with installation instructions for all platforms

### 9. DEPENDENCY_GUIDE.md
Detailed explanation of which scripts need which dependencies

### 10. SCRIPT_INDEX.md (This File)
Quick reference index of all available scripts

### 11. CERTIFICATE_GENERATION_IMPROVEMENTS.md
Technical notes on certificate generation improvements

---

## 🚀 Build Scripts (In DCIM_Server Root Directory)

### build.ps1 (Windows Build Script)
**Purpose:** Build DCIM Server for Windows, Linux, and macOS

- **Location:** `DCIM_Server/build.ps1`
- **Dependencies:** Go compiler
- **Usage:** `.\build.ps1` or `.\build.ps1 -Platform linux`

### build.sh (Linux/Mac Build Script)
**Purpose:** Build DCIM Server for Windows, Linux, and macOS

- **Location:** `DCIM_Server/build.sh`
- **Dependencies:** Go compiler
- **Usage:** `./build.sh` or `./build.sh -p linux`

---

## 📋 Quick Command Reference

### Make All Bash Scripts Executable (First Time)
```bash
chmod +x scripts/*.sh
chmod +x build.sh
```

### Complete First-Time Setup
```bash
# 1. Generate certificates
./scripts/generate-certs.sh

# 2. Setup database (if using PostgreSQL)
./scripts/setup-postgres.sh

# 3. Build server (requires Go)
./build.sh

# 4. Run server
./build/linux-amd64/dcim-server
```

### Add New Agent
```bash
# Generate certificates for new agent
./scripts/generate-client-cert.sh -a "agent-03"

# Package for distribution
cd certs/agents/agent-03
tar -czf ../agent-03-certs.tar.gz .

# Copy to agent machine
scp ../agent-03-certs.tar.gz user@agent:/tmp/
```

### Renew Expiring Certificates
```bash
# Check what's expiring
./scripts/check-cert-expiry.sh

# Renew server certificate
./scripts/renew-server-cert.sh

# Renew client certificate
./scripts/renew-client-cert.sh -a "agent-name"
```

---

## 🔍 Script Selection Guide

**I need to...**

- ✅ **Setup a new server from scratch** → Use `generate-certs.sh` + `setup-postgres.sh`
- ✅ **Add a new agent** → Use `generate-client-cert.sh`
- ✅ **Check certificate health** → Use `check-cert-expiry.sh`
- ✅ **Renew expiring server cert** → Use `renew-server-cert.sh`
- ✅ **Renew expiring agent cert** → Use `renew-client-cert.sh`
- ✅ **Setup database** → Use `setup-postgres.sh`
- ✅ **Build from source** → Use `build.sh` or `build.ps1` (requires Go)

---

## ⚠️ Important Notes

1. **Scripts in `scripts/` directory DO NOT require Go**
   - Only certificate and database management scripts
   - Can run on any machine with OpenSSL/PostgreSQL

2. **Build scripts DO require Go**
   - Located in DCIM_Server root (not scripts/)
   - Only needed on development machines
   - Production machines can use pre-built binaries

3. **Always run from DCIM_Server directory**
   ```bash
   cd DCIM_Server
   ./scripts/script-name.sh
   ```

4. **Use absolute paths in scripts**
   - All scripts expect to be run from DCIM_Server directory
   - They look for `certs/` directory in current location

---

## 📞 Getting Help

- **Script usage:** Add `-h` or `--help` flag
  ```bash
  ./scripts/generate-client-cert.sh --help
  ```

- **Dependency issues:** See `DEPENDENCY_GUIDE.md`

- **Setup guide:** See `SCRIPTS_README.md`

---

**Last Updated:** 2026-02-11

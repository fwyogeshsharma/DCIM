# DCIM Server Scripts - Organized by Platform

All utility scripts for managing DCIM Server, organized by operating system for easy access.

---

## 📁 Directory Structure

```
scripts/
├── windows/          # Windows scripts (.ps1 + .bat)
├── linux/            # Linux scripts (.sh)
├── macos/            # macOS scripts (.sh)
├── common/           # Documentation (all platforms)
└── README.md         # This file
```

---

## 🚀 Quick Start

### Windows Users
```cmd
cd scripts\windows
generate-certs.bat
```
or
```powershell
cd scripts\windows
.\generate-certs.ps1
```

### Linux Users
```bash
cd scripts/linux
chmod +x *.sh
./generate-certs.sh
```

### macOS Users
```bash
cd scripts/macos
chmod +x *.sh
./generate-certs.sh
```

---

## 📋 Available Scripts

### Certificate Management
| Script | Purpose | Windows | Linux | macOS |
|--------|---------|---------|-------|-------|
| **generate-certs** | Generate CA + Server + Client certificates | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |
| **generate-client-cert** | Generate additional client certificates | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |
| **renew-server-cert** | Renew server certificate | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |
| **renew-client-cert** | Renew client certificate | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |
| **check-cert-expiry** | Check certificate expiration | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |

### Database Management
| Script | Purpose | Windows | Linux | macOS |
|--------|---------|---------|-------|-------|
| **setup-postgres** | Setup PostgreSQL database | ✅ `.bat` `.ps1` | ✅ `.sh` | ✅ `.sh` |
| **fix-postgres-path** | Fix PostgreSQL PATH | ✅ `.bat` `.ps1` | ❌ | ❌ |

---

## 📚 Documentation

All documentation is in the `common/` directory:

- **`SCRIPTS_README.md`** - Complete usage guide
- **`DEPENDENCY_GUIDE.md`** - Dependency requirements
- **`SCRIPT_INDEX.md`** - Quick reference index
- **`CERTIFICATE_GENERATION_IMPROVEMENTS.md`** - Technical notes

---

## 🎯 Usage Examples

### Windows

#### Using Batch Files (.bat) - Easiest
```cmd
REM Navigate to Windows scripts directory
cd C:\Anupam\Faber\Projects\DCIM\DCIM_Server\scripts\windows

REM Generate certificates
generate-certs.bat

REM Generate client certificate for agent
generate-client-cert.bat agent-02

REM Check certificate expiry
check-cert-expiry.bat

REM Setup PostgreSQL
setup-postgres.bat
```

#### Using PowerShell (.ps1) - More Control
```powershell
# Navigate to Windows scripts directory
cd C:\Anupam\Faber\Projects\DCIM\DCIM_Server\scripts\windows

# Generate certificates
.\generate-certs.ps1

# Generate client certificate with specific validity
.\generate-client-cert.ps1 -AgentName "agent-02" -ValidityDays 730

# Check certificate expiry
.\check-cert-expiry.ps1

# Setup PostgreSQL
.\setup-postgres.ps1
```

---

### Linux

```bash
# Navigate to Linux scripts directory
cd /path/to/DCIM_Server/scripts/linux

# Make scripts executable (first time only)
chmod +x *.sh

# Generate certificates
./generate-certs.sh

# Generate client certificate for agent
./generate-client-cert.sh -a agent-02

# Check certificate expiry
./check-cert-expiry.sh

# Setup PostgreSQL
./setup-postgres.sh
```

---

### macOS

```bash
# Navigate to macOS scripts directory
cd /path/to/DCIM_Server/scripts/macos

# Make scripts executable (first time only)
chmod +x *.sh

# Generate certificates
./generate-certs.sh

# Generate client certificate for agent
./generate-client-cert.sh -a agent-02

# Check certificate expiry
./check-cert-expiry.sh

# Setup PostgreSQL
./setup-postgres.sh
```

---

## 🔧 Dependencies

### All Scripts Require

**Certificate Scripts:**
- OpenSSL (no Go needed!)

**Database Scripts:**
- PostgreSQL and psql (no Go needed!)

**Build Scripts (separate, in DCIM_Server root):**
- Go compiler (only for building from source)

### Installation

**OpenSSL:**
```bash
# Windows
choco install openssl

# Linux (Debian/Ubuntu)
sudo apt-get install openssl

# Linux (RHEL/CentOS)
sudo yum install openssl

# macOS
brew install openssl
```

**PostgreSQL:**
```bash
# Windows
winget install PostgreSQL.PostgreSQL

# Linux (Debian/Ubuntu)
sudo apt-get install postgresql postgresql-client

# Linux (RHEL/CentOS)
sudo yum install postgresql-server postgresql

# macOS
brew install postgresql
```

---

## ⚠️ Important Notes

### 1. **Scripts DO NOT Require Go**
   - Certificate and database scripts only need OpenSSL/PostgreSQL
   - Only build scripts (in DCIM_Server root) need Go

### 2. **Run from Correct Directory**
   - Windows: Run from `scripts\windows\`
   - Linux: Run from `scripts/linux/`
   - macOS: Run from `scripts/macos/`

### 3. **Certificates Must Be Generated Before Running Server**
   ```bash
   # Generate certificates first
   ./generate-certs.sh  (or .bat on Windows)

   # Then run server
   cd ../..
   ./build/linux-amd64/dcim-server
   ```

### 4. **Batch vs PowerShell on Windows**
   - **Batch files (.bat)** - Easier to run, just double-click
   - **PowerShell files (.ps1)** - More features, better error handling

---

## 🆘 Troubleshooting

### "Permission denied" (Linux/Mac)
```bash
chmod +x script-name.sh
```

### "Execution Policy" Error (Windows PowerShell)
```powershell
# Use the .bat files instead, or:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### "OpenSSL not found"
- Install OpenSSL for your platform (see Dependencies above)
- Make sure it's in your system PATH

### "PostgreSQL connection failed"
- Verify PostgreSQL service is running
- Check credentials in config.yaml

---

## 📞 Getting Help

1. **Read the documentation:**
   ```bash
   # View platform-specific README
   cat common/SCRIPTS_README.md
   ```

2. **Use --help flag:**
   ```bash
   # Linux/Mac
   ./generate-certs.sh --help

   # Windows (PowerShell)
   .\generate-certs.ps1 -?
   ```

3. **Check dependency guide:**
   ```bash
   cat common/DEPENDENCY_GUIDE.md
   ```

---

## 🎯 Common Workflows

### First-Time Server Setup

**Windows:**
```cmd
cd scripts\windows
generate-certs.bat
setup-postgres.bat
cd ..\..
build\windows-amd64\dcim-server.exe
```

**Linux:**
```bash
cd scripts/linux
chmod +x *.sh
./generate-certs.sh
./setup-postgres.sh
cd ../..
./build/linux-amd64/dcim-server
```

---

### Add New Agent

**Windows:**
```cmd
cd scripts\windows
generate-client-cert.bat agent-03
```

**Linux:**
```bash
cd scripts/linux
./generate-client-cert.sh -a agent-03
```

---

### Check Certificate Health

**Windows:**
```cmd
cd scripts\windows
check-cert-expiry.bat
```

**Linux:**
```bash
cd scripts/linux
./check-cert-expiry.sh
```

---

## 📝 Script Comparison

| Feature | .bat (Windows) | .ps1 (Windows) | .sh (Linux/Mac) |
|---------|----------------|----------------|-----------------|
| **Ease of Use** | ⭐⭐⭐ Double-click | ⭐⭐ Command-line | ⭐⭐ Command-line |
| **Error Handling** | ⭐⭐ Basic | ⭐⭐⭐ Advanced | ⭐⭐⭐ Advanced |
| **Features** | ⭐⭐ Wrapper only | ⭐⭐⭐ Full-featured | ⭐⭐⭐ Full-featured |
| **Color Output** | ❌ No | ✅ Yes | ✅ Yes |
| **Interactive** | ✅ Yes | ✅ Yes | ✅ Yes |

**Recommendation:**
- **Windows beginners:** Use `.bat` files
- **Windows advanced:** Use `.ps1` files
- **Linux/Mac:** Use `.sh` files

---

**Last Updated:** 2026-02-11
**Version:** 2.0 (Organized Structure)

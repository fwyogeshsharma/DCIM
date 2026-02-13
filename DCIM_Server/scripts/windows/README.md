# Windows Scripts - DCIM Server

Certificate and database management scripts for Windows.

---

## 📋 Available Scripts

### Batch Files (.bat) - Recommended for Beginners
Just double-click or run from Command Prompt:

- **`generate-certs.bat`** - Generate all certificates (CA + Server + Client)
- **`generate-client-cert.bat <agent-name>`** - Generate client certificate for agent
- **`renew-server-cert.bat`** - Renew server certificate
- **`renew-client-cert.bat <agent-name>`** - Renew client certificate
- **`check-cert-expiry.bat`** - Check certificate expiration dates
- **`setup-postgres.bat`** - Setup PostgreSQL database
- **`fix-postgres-path.bat`** - Fix PostgreSQL PATH issues

### PowerShell Files (.ps1) - Advanced Features
More control and better error messages:

- **`generate-certs.ps1`** - Generate all certificates
- **`generate-client-cert.ps1`** - Generate client certificate
- **`renew-server-cert.ps1`** - Renew server certificate
- **`renew-client-cert.ps1`** - Renew client certificate
- **`check-cert-expiry.ps1`** - Check certificates
- **`setup-postgres.ps1`** - Setup database
- **`fix-postgres-path.ps1`** - Fix PATH

---

## 🚀 Quick Start

### Using Batch Files (Easiest)

```cmd
REM Open Command Prompt in this directory
cd C:\Anupam\Faber\Projects\DCIM\DCIM_Server\scripts\windows

REM Generate certificates
generate-certs.bat

REM Generate client certificate
generate-client-cert.bat agent-02

REM Check expiry
check-cert-expiry.bat
```

### Using PowerShell (More Features)

```powershell
# Open PowerShell in this directory
cd C:\Anupam\Faber\Projects\DCIM\DCIM_Server\scripts\windows

# Generate certificates
.\generate-certs.ps1

# Generate client certificate with specific validity
.\generate-client-cert.ps1 -AgentName "agent-02" -ValidityDays 730

# Check expiry
.\check-cert-expiry.ps1
```

---

## 🔧 Requirements

- **OpenSSL** - Download from: https://slproweb.com/products/Win32OpenSSL.html
- **PostgreSQL** (optional) - Download from: https://www.postgresql.org/download/windows/

**No Go compiler needed!** These scripts only require OpenSSL and PostgreSQL.

---

## 📖 Examples

### Complete Setup
```cmd
REM 1. Generate certificates
generate-certs.bat

REM 2. Setup database
setup-postgres.bat

REM 3. Go back to server directory
cd ..\..

REM 4. Run server
build\windows-amd64\dcim-server.exe
```

### Add New Agent
```cmd
REM Generate certificate for new agent
generate-client-cert.bat agent-03

REM Files will be in: ..\..\certs\agents\agent-03\
```

### Renew Certificates
```cmd
REM Check what's expiring
check-cert-expiry.bat

REM Renew server certificate
renew-server-cert.bat

REM Renew agent certificate
renew-client-cert.bat agent-02
```

---

## ⚠️ Troubleshooting

### Execution Policy Error (PowerShell)
**Use the .bat files instead!** They automatically bypass execution policy.

Or temporarily allow:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### OpenSSL Not Found
1. Download OpenSSL from: https://slproweb.com/products/Win32OpenSSL.html
2. Install "Win64 OpenSSL v3.x.x"
3. Add to PATH: `C:\Program Files\OpenSSL-Win64\bin`
4. Restart Command Prompt/PowerShell

### PostgreSQL Connection Failed
1. Check PostgreSQL service is running (Services app)
2. Verify password in `..\..\config.yaml`
3. Try: `psql -U postgres -h localhost`

---

## 📚 More Information

- **Complete Documentation:** `..\common\SCRIPTS_README.md`
- **Dependencies Guide:** `..\common\DEPENDENCY_GUIDE.md`
- **Script Index:** `..\common\SCRIPT_INDEX.md`

---

**Platform:** Windows 10/11/Server
**Script Types:** Batch (.bat) + PowerShell (.ps1)

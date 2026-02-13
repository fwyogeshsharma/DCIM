# DCIM Server Scripts - Cross-Platform Guide

This directory contains utility scripts for managing the DCIM Server. Each script is available in both **PowerShell** (`.ps1` for Windows) and **Bash** (`.sh` for Linux/Mac) versions.

## 📋 Available Scripts

### 1. Certificate Generation
Generates mTLS certificates (CA, server, client) for secure communication.

**Windows:**
```powershell
.\generate-certs.ps1
```

**Linux/Mac:**
```bash
chmod +x generate-certs.sh
./generate-certs.sh
```

**Dependencies:**
- OpenSSL

**What it does:**
- Generates Certificate Authority (CA)
- Generates server certificate with SAN support
- Generates client/agent certificate
- Creates certificate info file

---

### 2. Check Certificate Expiry
Checks all certificates for expiration and provides renewal recommendations.

**Windows:**
```powershell
.\check-cert-expiry.ps1
```

**Linux/Mac:**
```bash
chmod +x check-cert-expiry.sh
./check-cert-expiry.sh
```

**Dependencies:**
- OpenSSL

**What it does:**
- Scans all certificates in the `certs/` directory
- Shows expiry dates and days remaining
- Warns about certificates expiring within 30 days
- Provides renewal commands

---

### 3. PostgreSQL Setup
Sets up PostgreSQL database for DCIM Server.

**Windows:**
```powershell
.\setup-postgres.ps1
```

**Linux/Mac:**
```bash
chmod +x setup-postgres.sh
./setup-postgres.sh
```

**Dependencies:**
- PostgreSQL
- psql command-line tool

**What it does:**
- Checks PostgreSQL installation
- Verifies service is running
- Creates `dcim_db` database
- Validates config.yaml settings
- Optionally disables SSL for local development

---

### 4. Renew Server Certificate
Renews the server certificate using existing CA.

**Windows:**
```powershell
.\renew-server-cert.ps1
```

**Linux/Mac:**
```bash
chmod +x renew-server-cert.sh  # (create if not exists)
./renew-server-cert.sh
```

**Dependencies:**
- OpenSSL
- Existing CA certificates

---

### 5. Renew Client Certificate
Generates a new client/agent certificate.

**Windows:**
```powershell
.\renew-client-cert.ps1 -AgentName "agent-name"
```

**Linux/Mac:**
```bash
chmod +x renew-client-cert.sh  # (create if not exists)
./renew-client-cert.sh -AgentName "agent-name"
```

**Dependencies:**
- OpenSSL
- Existing CA certificates

---

## 🔧 Dependencies Installation

### OpenSSL

**Windows:**
1. Download from: https://slproweb.com/products/Win32OpenSSL.html
2. Install "Win64 OpenSSL"
3. Add to PATH (usually `C:\Program Files\OpenSSL-Win64\bin`)

**Linux (Debian/Ubuntu):**
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

### PostgreSQL

**Windows:**
1. Download from: https://www.postgresql.org/download/windows/
2. Or use: `winget install PostgreSQL.PostgreSQL`

**Linux (Debian/Ubuntu):**
```bash
sudo apt-get update
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

## 🚀 Quick Start Workflow

### First-Time Setup

1. **Install Dependencies**
   - Install OpenSSL
   - Install PostgreSQL (if using PostgreSQL database)

2. **Generate Certificates**
   ```bash
   # Windows
   .\scripts\generate-certs.ps1

   # Linux/Mac
   chmod +x scripts/generate-certs.sh
   ./scripts/generate-certs.sh
   ```

3. **Setup Database** (if using PostgreSQL)
   ```bash
   # Windows
   .\scripts\setup-postgres.ps1

   # Linux/Mac
   chmod +x scripts/setup-postgres.sh
   ./scripts/setup-postgres.sh
   ```

4. **Build Server**
   ```bash
   # Windows
   .\build.ps1

   # Linux/Mac
   ./build.sh  # (you may need to create this)
   ```

5. **Run Server**
   ```bash
   # Windows
   .\build\windows-amd64\dcim-server.exe

   # Linux
   ./build/linux-amd64/dcim-server

   # macOS
   ./build/darwin-amd64/dcim-server
   ```

---

## 📝 Notes

### Running Scripts on External Machines

**These scripts do NOT require Go to be installed.** They only require:
- OpenSSL (for certificate scripts)
- PostgreSQL/psql (for database scripts)

### Making Scripts Executable (Linux/Mac)

When you copy scripts to a new machine, make them executable:

```bash
chmod +x scripts/*.sh
```

### Certificate Distribution

After generating certificates:
1. Server needs: `ca.crt`, `server.crt`, `server.key`
2. Each agent needs: `ca.crt`, `client.crt`, `client.key`

Copy the agent certificates to each agent machine:
```bash
# On the server
scp certs/ca.crt certs/client.crt certs/client.key user@agent-machine:/path/to/agent/certs/
```

### Security Best Practices

- ⚠️ **Never commit `.key` files to version control**
- ⚠️ **Keep private keys secure with restricted permissions:**
  ```bash
  chmod 600 certs/*.key
  ```
- ⚠️ **Set calendar reminders to renew certificates before expiry**
- ⚠️ **Use strong passwords for PostgreSQL in production**

---

## 🐛 Troubleshooting

### "OpenSSL not found"
- Ensure OpenSSL is installed and in your PATH
- Restart your terminal after installation
- On Windows, check `C:\Program Files\OpenSSL-Win64\bin` is in PATH

### "PostgreSQL connection failed"
- Verify PostgreSQL service is running
- Check username/password
- For Linux: `sudo systemctl status postgresql`
- For Mac: `brew services list | grep postgresql`
- For Windows: Check Services app for "postgresql" service

### "Permission denied" on Linux/Mac
- Make scripts executable: `chmod +x script-name.sh`
- Use `sudo` if needed for system operations

### Certificate generation fails on Windows
- Clear OpenSSL config: `$env:OPENSSL_CONF = $null` in PowerShell
- Ensure Strawberry Perl isn't conflicting (script handles this automatically)

---

## 📚 Additional Resources

- [OpenSSL Documentation](https://www.openssl.org/docs/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [DCIM Server README](../README.md)
- [Build and Run Guide](../../BUILD_AND_RUN.md)

---

## 🆘 Getting Help

If scripts fail on external machines:

1. **Check dependencies are installed:**
   ```bash
   openssl version
   psql --version
   ```

2. **Read error messages carefully** - they usually indicate what's missing

3. **Check permissions** - make sure scripts are executable

4. **Verify network connectivity** - for database connections

5. **Review logs** - scripts provide detailed output about what they're doing

---

**Last Updated:** 2026-02-11

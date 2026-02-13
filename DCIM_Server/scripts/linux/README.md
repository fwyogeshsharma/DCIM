# Linux Scripts - DCIM Server

Certificate and database management scripts for Linux.

---

## 📋 Available Scripts

All scripts are bash (.sh) files:

- **`generate-certs.sh`** - Generate all certificates (CA + Server + Client)
- **`generate-client-cert.sh`** - Generate client certificate for agent
- **`renew-server-cert.sh`** - Renew server certificate
- **`renew-client-cert.sh`** - Renew client certificate
- **`check-cert-expiry.sh`** - Check certificate expiration dates
- **`setup-postgres.sh`** - Setup PostgreSQL database

---

## 🚀 Quick Start

```bash
# Navigate to this directory
cd /path/to/DCIM_Server/scripts/linux

# Make scripts executable (first time only)
chmod +x *.sh

# Generate certificates
./generate-certs.sh

# Generate client certificate for agent
./generate-client-cert.sh -a agent-02

# Check certificate expiry
./check-cert-expiry.sh

# Setup PostgreSQL database
./setup-postgres.sh
```

---

## 🔧 Requirements

### Debian/Ubuntu
```bash
# OpenSSL (for certificates)
sudo apt-get update
sudo apt-get install openssl

# PostgreSQL (for database)
sudo apt-get install postgresql postgresql-client
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### RHEL/CentOS/Fedora
```bash
# OpenSSL (for certificates)
sudo yum install openssl

# PostgreSQL (for database)
sudo yum install postgresql-server postgresql
sudo postgresql-setup initdb
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**No Go compiler needed!** These scripts only require OpenSSL and PostgreSQL.

---

## 📖 Examples

### Complete Setup
```bash
# 1. Generate certificates
./generate-certs.sh

# 2. Setup database
./setup-postgres.sh

# 3. Go back to server directory
cd ../..

# 4. Run server
./build/linux-amd64/dcim-server
```

### Add New Agent
```bash
# Generate certificate for new agent
./generate-client-cert.sh -a agent-03

# Files will be in: ../../certs/agents/agent-03/

# Package for distribution
cd ../../certs/agents/agent-03
tar -czf agent-03-certs.tar.gz .

# Copy to agent machine
scp agent-03-certs.tar.gz user@agent-machine:/tmp/
```

### Renew Certificates
```bash
# Check what's expiring
./check-cert-expiry.sh

# Renew server certificate
./renew-server-cert.sh

# Renew agent certificate with specific validity
./renew-client-cert.sh -a agent-02
```

---

## ⚠️ Troubleshooting

### Permission Denied
```bash
# Make script executable
chmod +x script-name.sh

# Or make all executable
chmod +x *.sh
```

### OpenSSL Not Found
```bash
# Debian/Ubuntu
sudo apt-get install openssl

# RHEL/CentOS
sudo yum install openssl

# Verify installation
openssl version
```

### PostgreSQL Connection Failed
```bash
# Check service status
sudo systemctl status postgresql

# Start service if not running
sudo systemctl start postgresql

# Check if listening
sudo netstat -tlnp | grep 5432

# Reset postgres password
sudo -u postgres psql
# Then: ALTER USER postgres PASSWORD 'your_password';
```

### Script Not Found
```bash
# Make sure you're in the correct directory
pwd
# Should show: /path/to/DCIM_Server/scripts/linux

# List files
ls -la *.sh
```

---

## 🎯 Command Reference

### Script Arguments

```bash
# Generate certificates (interactive)
./generate-certs.sh

# Generate client cert with agent name
./generate-client-cert.sh -a <agent-name>
./generate-client-cert.sh --agent <agent-name>

# Renew server cert with validity
./renew-server-cert.sh --days 730

# Renew client cert
./renew-client-cert.sh -a <agent-name>

# Check certificate expiry
./check-cert-expiry.sh

# Setup PostgreSQL
./setup-postgres.sh
```

### Get Help
```bash
# Most scripts support --help
./generate-certs.sh --help
./generate-client-cert.sh --help
```

---

## 📚 More Information

- **Complete Documentation:** `../common/SCRIPTS_README.md`
- **Dependencies Guide:** `../common/DEPENDENCY_GUIDE.md`
- **Script Index:** `../common/SCRIPT_INDEX.md`

---

**Platform:** Linux (Debian, Ubuntu, RHEL, CentOS, Fedora)
**Shell:** Bash
**Requirements:** OpenSSL, PostgreSQL (optional)

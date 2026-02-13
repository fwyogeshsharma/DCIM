# macOS Scripts - DCIM Server

Certificate and database management scripts for macOS.

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
cd /path/to/DCIM_Server/scripts/macos

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

### Using Homebrew (Recommended)
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# OpenSSL (for certificates)
brew install openssl

# PostgreSQL (for database)
brew install postgresql
brew services start postgresql
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

# 4. Run server (Intel Mac)
./build/darwin-amd64/dcim-server

# Or (Apple Silicon M1/M2)
./build/darwin-arm64/dcim-server
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

# Renew agent certificate
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
# Install via Homebrew
brew install openssl

# Verify installation
openssl version
```

### PostgreSQL Connection Failed
```bash
# Check service status
brew services list | grep postgresql

# Start service if not running
brew services start postgresql

# Check if listening
lsof -i :5432

# Access PostgreSQL shell
psql postgres
```

### "Command not found: psql"
```bash
# Add PostgreSQL to PATH
echo 'export PATH="/usr/local/opt/postgresql/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Or for bash
echo 'export PATH="/usr/local/opt/postgresql/bin:$PATH"' >> ~/.bash_profile
source ~/.bash_profile
```

### Script Not Found
```bash
# Make sure you're in the correct directory
pwd
# Should show: /path/to/DCIM_Server/scripts/macos

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

## 🍎 macOS-Specific Notes

### Apple Silicon (M1/M2) vs Intel
- Scripts work the same on both architectures
- Server binary differs:
  - Intel: `build/darwin-amd64/dcim-server`
  - Apple Silicon: `build/darwin-arm64/dcim-server`

### System Permissions
macOS may require additional permissions:
1. System Preferences → Security & Privacy
2. Allow Terminal/iTerm to access files

### OpenSSL Version
macOS includes LibreSSL by default. For better compatibility:
```bash
brew install openssl
```

---

## 📚 More Information

- **Complete Documentation:** `../common/SCRIPTS_README.md`
- **Dependencies Guide:** `../common/DEPENDENCY_GUIDE.md`
- **Script Index:** `../common/SCRIPT_INDEX.md`

---

**Platform:** macOS 11+ (Big Sur and later)
**Shell:** Bash/Zsh
**Requirements:** OpenSSL, PostgreSQL (optional)
**Architectures:** Intel (amd64) and Apple Silicon (arm64)

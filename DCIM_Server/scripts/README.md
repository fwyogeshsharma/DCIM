# DCIM Server - Scripts Directory

Certificate management and utility scripts for DCIM Server.

## Certificate Management Scripts

### Initial Setup

#### generate-certs.ps1
Generate complete mTLS certificate infrastructure (CA, server, initial client).

**Improved version with:**
- ✅ Automatic OpenSSL configuration fix (prevents config file errors)
- ✅ Comprehensive error checking after each step
- ✅ File verification (ensures .crt files are actually created)
- ✅ Clear progress indicators and error messages
- ✅ Automatic cleanup of temporary files

```powershell
.\generate-certs.ps1
```

**Creates:**
- `../certs/ca.crt` and `ca.key` - Certificate Authority (root)
- `../certs/server.crt` and `server.key` - Server certificate
- `../certs/client.crt` and `client.key` - Initial client certificate
- `../certs/CERTIFICATE_INFO.txt` - Certificate details and renewal dates

**When to use:** First-time setup or complete certificate regeneration

**Note:** The script automatically fixes common OpenSSL issues like:
- Missing or incorrect OPENSSL_CONF environment variable
- Config file path errors (Z:/extlib/... errors)
- Silent failures (now detected and reported)

---

#### generate-client-cert.ps1
Generate additional client certificates for new agents.

```powershell
.\generate-client-cert.ps1 -AgentName "agent-02"
```

**Creates:**
- `../certs/agents/agent-02.crt` - Client certificate
- `../certs/agents/agent-02.key` - Client private key

**When to use:** Adding new agents to the system

---

### Monitoring

#### check-cert-expiry.ps1
Check expiration status of all certificates.

```powershell
.\check-cert-expiry.ps1
```

**Output:**
- Days until expiry for each certificate
- Warning for certificates expiring within 30 days
- Recommendations for renewal

**When to use:** Regular monitoring (daily/weekly)

---

### Renewal

#### renew-server-cert.ps1
Renew the server certificate using existing CA.

```powershell
# Default: 1 year validity
.\renew-server-cert.ps1

# Custom validity
.\renew-server-cert.ps1 -ValidityDays 730

# Skip backup
.\renew-server-cert.ps1 -Backup:$false
```

**When to use:** Before server certificate expires (30 days warning)

---

#### renew-client-cert.ps1
Renew a client certificate using existing CA.

```powershell
# Renew default client certificate
.\renew-client-cert.ps1

# Renew specific agent certificate
.\renew-client-cert.ps1 -AgentName "agent-02"

# Custom validity
.\renew-client-cert.ps1 -ValidityDays 365
```

**When to use:** Before client certificate expires

---

## Database Scripts

### setup-postgres.ps1
Configure PostgreSQL database for DCIM Server.

```powershell
.\setup-postgres.ps1
```

**When to use:** Setting up PostgreSQL instead of SQLite

---

### fix-postgres-path.ps1
Fix PostgreSQL path issues on Windows.

```powershell
.\fix-postgres-path.ps1
```

**When to use:** PostgreSQL connection errors on Windows

---

## Certificate Workflow

### Initial Setup
```powershell
# 1. Generate all certificates
.\generate-certs.ps1

# 2. Start server (uses certificates automatically)
cd ..
.\dcim-server.exe -config config.yaml
```

### Adding New Agents
```powershell
# 1. Generate client certificate for new agent
.\generate-client-cert.ps1 -AgentName "agent-datacenter-2"

# 2. Copy to agent machine
# - certs/agents/agent-datacenter-2.crt
# - certs/agents/agent-datacenter-2.key
# - certs/ca.crt (CA certificate)
```

### Certificate Maintenance
```powershell
# Check expiry status monthly
.\check-cert-expiry.ps1

# Renew certificates before expiry
.\renew-server-cert.ps1
.\renew-client-cert.ps1
```

---

## Certificate Validity

**Recommended periods:**
- CA Certificate: 10 years
- Server Certificate: 1-2 years
- Client Certificates: 1 year

**Warning thresholds:**
- 30 days: Start planning renewal
- 7 days: Urgent renewal required

---

## Troubleshooting

### Certificate Not Found
```powershell
# Generate new certificates
.\generate-certs.ps1
```

### Certificate Expired
```powershell
# Check status
.\check-cert-expiry.ps1

# Renew expired certificate
.\renew-server-cert.ps1
```

### Agent Connection Fails
```powershell
# Verify certificates exist
dir ..\certs\

# Check certificate validity
.\check-cert-expiry.ps1

# Regenerate if needed
.\generate-certs.ps1
```

---

## File Locations

```
DCIM_Server/
├── scripts/                    # This directory
│   ├── generate-certs.ps1      # Initial certificate generation
│   ├── generate-client-cert.ps1 # Additional client certs
│   ├── check-cert-expiry.ps1   # Monitor expiration
│   ├── renew-server-cert.ps1   # Renew server cert
│   └── renew-client-cert.ps1   # Renew client cert
│
└── certs/                      # Certificate storage
    ├── ca.crt                  # CA certificate
    ├── ca.key                  # CA private key
    ├── server.crt              # Server certificate
    ├── server.key              # Server private key
    ├── client.crt              # Default client cert
    ├── client.key              # Client private key
    └── agents/                 # Additional client certs
        ├── agent-02.crt
        └── agent-02.key
```

---

For complete certificate management documentation, see [../CERTIFICATE_MANAGEMENT.md](../CERTIFICATE_MANAGEMENT.md)

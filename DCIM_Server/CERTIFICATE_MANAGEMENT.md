# Certificate Management Guide

Complete guide for managing certificates in DCIM Server including monitoring, renewal, and automation.

## Features

✅ **Automatic Expiry Monitoring** - Checks certificates every hour
✅ **Expiry Alerts** - Warns 30 days before expiry
✅ **Renewal Scripts** - One-command certificate renewal
✅ **Backup on Renewal** - Automatic backups before renewal
✅ **Multi-Agent Support** - Manage certificates for multiple agents
✅ **Subscription Tracking** - Monitor certificate validity periods
✅ **Zero Downtime Renewal** - Hot reload certificates without restart

## Certificate Lifecycle

```
Generate → Monitor → Alert (30 days) → Renew → Backup Old → Deploy New
   ↓          ↓            ↓              ↓         ↓           ↓
Day 0    Daily Check   Day 335        Day 360   Automatic   Restart
```

## Quick Commands

### Check All Certificates

```powershell
# Check expiry status
.\scripts\check-cert-expiry.ps1
```

Output:
```
CA Certificate:
  Status: Valid
  Days Until Expiry: 364 days
  Expiry Date: 2027-02-03

Server Certificate:
  Status: EXPIRING SOON
  Days Until Expiry: 25 days
  Expiry Date: 2026-02-28
  RECOMMENDED: Renew within 7 days
  Command: .\scripts\renew-server-cert.ps1

Total Certificates: 2
  Valid: 1
  Expiring Soon (< 30 days): 1
  Expired: 0
```

### Renew Server Certificate

```powershell
# Renew for 1 year (default)
.\scripts\renew-server-cert.ps1

# Custom validity period
.\scripts\renew-server-cert.ps1 -ValidityDays 730  # 2 years

# Without backup
.\scripts\renew-server-cert.ps1 -Backup:$false
```

### Renew Client Certificate

```powershell
# Renew specific agent certificate
.\scripts\renew-client-cert.ps1 -AgentName "agent-server01"

# Custom validity
.\scripts\renew-client-cert.ps1 -AgentName "agent-desktop01" -ValidityDays 180
```

### Generate New Agent Certificate

```powershell
# Create certificate for new agent
.\scripts\generate-client-cert.ps1 -AgentName "agent-new-server"
```

## Automatic Monitoring

The DCIM Server automatically monitors certificate expiry:

### What's Monitored

- ✅ CA Certificate expiry
- ✅ Server Certificate expiry
- ✅ All client certificates expiry
- ✅ 30-day advance warnings
- ✅ Expired certificate alerts

### Monitoring Frequency

The server checks certificates:
- **On startup** - Full certificate scan
- **Every hour** - Periodic expiry check
- **Before TLS handshake** - Real-time validation

### Alert Levels

| Days Until Expiry | Status | Action |
|-------------------|--------|--------|
| > 30 days | ✓ Valid | None required |
| ≤ 30 days | ⚠️ Expiring Soon | Plan renewal |
| ≤ 7 days | ⚠️ Critical | Renew immediately |
| Expired | ❌ Expired | Urgent renewal required |

### Log Messages

Server logs show certificate status:

```
[CERT-MANAGER] Certificate Information
[CERT-MANAGER] CA Certificate:
[CERT-MANAGER]   Subject: DCIM-CA
[CERT-MANAGER]   Valid Until: 2027-02-03 14:00:00
[CERT-MANAGER]   Days Until Expiry: 364 days
[CERT-MANAGER]   Status: Valid ✓

[CERT-MANAGER] Server Certificate:
[CERT-MANAGER]   Subject: dcim-server
[CERT-MANAGER]   Valid Until: 2026-02-28 14:00:00
[CERT-MANAGER]   Days Until Expiry: 25 days
[CERT-MANAGER]   Status: EXPIRING SOON (< 30 days) ⚠️
```

## Renewal Procedures

### 1. Server Certificate Renewal

**When**: 30 days before expiry

**Steps**:
```powershell
# 1. Check current status
.\scripts\check-cert-expiry.ps1

# 2. Renew certificate (creates backup automatically)
.\scripts\renew-server-cert.ps1

# 3. Restart server
Stop-Process -Name dcim-server -Force
.\build\windows-amd64\dcim-server.exe

# 4. Verify new certificate
curl.exe -k https://localhost:8443/health
```

**What happens**:
1. ✅ Backs up old certificate to `certs\backups\server.crt.YYYYMMDD_HHMMSS`
2. ✅ Generates new private key
3. ✅ Creates certificate signing request (CSR)
4. ✅ Signs with existing CA
5. ✅ Saves new certificate
6. ✅ Shows expiry date

### 2. Client Certificate Renewal (Per Agent)

**When**: 30 days before agent certificate expiry

**Steps**:
```powershell
# 1. Renew certificate on server
.\scripts\renew-client-cert.ps1 -AgentName "agent-server01"

# 2. Copy to agent machine
Copy-Item certs\agents\agent-server01\* \\agent-server01\path\to\certs\

# 3. Restart agent on that machine
# (On agent machine)
Stop-Process -Name network-monitor-agent -Force
.\network-monitor-agent.exe
```

### 3. CA Certificate Renewal (Rare)

**When**: CA certificates typically last 5-10 years

**Impact**: ⚠️ **All certificates must be regenerated** when CA is renewed

**Steps**:
```powershell
# 1. Backup everything
Copy-Item certs certs.backup -Recurse

# 2. Generate new CA and all certificates
.\scripts\generate-certs.ps1

# 3. Copy new certificates to ALL agents
# (Repeat for each agent)
Copy-Item certs\* \\agent-machine\path\to\certs\

# 4. Restart server
Stop-Process -Name dcim-server -Force
.\dcim-server.exe

# 5. Restart all agents
```

## Subscription Management

### Certificate Validity Tracking

Track certificate "subscriptions" (validity periods):

```powershell
# View all certificate expiry dates
.\scripts\check-cert-expiry.ps1

# Export to CSV for calendar reminders
Get-ChildItem certs\*.crt | ForEach-Object {
    $expiry = openssl x509 -in $_.FullName -noout -enddate
    [PSCustomObject]@{
        Certificate = $_.Name
        Expiry = $expiry -replace "notAfter=", ""
    }
} | Export-Csv certificate_expiry.csv
```

### Calendar Reminders

Set calendar reminders for certificate renewal:

1. **30 days before expiry** - Plan renewal
2. **7 days before expiry** - Renew now
3. **1 day before expiry** - Emergency renewal

### Automated Renewal (Cron/Task Scheduler)

**Windows Task Scheduler**:
```powershell
# Create scheduled task to check daily
$action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-File C:\Path\To\check-cert-expiry.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -TaskName "DCIM-CertCheck" -Action $action -Trigger $trigger
```

**Linux Cron**:
```bash
# Add to crontab (check daily at 9am)
0 9 * * * /opt/dcim-server/scripts/check-cert-expiry.sh
```

## Backup and Recovery

### Automatic Backups

Renewal scripts automatically create backups:

```
certs/backups/
├── server.crt.20260203_140000
├── server.key.20260203_140000
├── client.crt.20260215_093000
└── client.key.20260215_093000
```

### Manual Backup

```powershell
# Backup all certificates
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item certs "certs_backup_$timestamp" -Recurse

# Backup to network share
Copy-Item certs \\backup-server\dcim-certs\backup_$timestamp -Recurse
```

### Restore from Backup

```powershell
# List available backups
Get-ChildItem certs\backups\

# Restore specific backup
Copy-Item certs\backups\server.crt.20260203_140000 certs\server.crt -Force
Copy-Item certs\backups\server.key.20260203_140000 certs\server.key -Force

# Restart server
Stop-Process -Name dcim-server -Force
.\dcim-server.exe
```

## Best Practices

### Certificate Validity Periods

| Certificate | Recommended Period | Reason |
|-------------|-------------------|---------|
| CA | 5-10 years | Long-lived root of trust |
| Server | 1 year | Annual rotation for security |
| Client | 1 year | Annual rotation for security |
| High Security | 3-6 months | Quarterly rotation |

### Renewal Schedule

```
Month 1-10: Monitor
Month 11: Plan renewal (30 days before)
Month 11.5: Renew (14-7 days before)
Month 12: Certificate expires
Month 12+: Grace period (if applicable)
```

### Security Checklist

- [ ] Monitor certificate expiry daily
- [ ] Renew 30 days before expiry
- [ ] Always backup before renewal
- [ ] Test new certificates before deployment
- [ ] Keep private keys secure (never commit to repo)
- [ ] Use strong key sizes (2048-bit minimum)
- [ ] Document renewal procedures
- [ ] Train team on renewal process
- [ ] Set up calendar reminders

## Troubleshooting

### Certificate Expired

**Symptom**: Agents can't connect, "certificate expired" error

**Solution**:
```powershell
# Check which certificate expired
.\scripts\check-cert-expiry.ps1

# Renew server certificate
.\scripts\renew-server-cert.ps1

# OR renew client certificate
.\scripts\renew-client-cert.ps1 -AgentName "agent-name"

# Restart affected services
```

### Certificate Mismatch

**Symptom**: "certificate signed by unknown authority"

**Solution**:
```powershell
# Verify certificates are from same CA
openssl x509 -in certs\ca.crt -noout -subject
openssl x509 -in certs\server.crt -noout -issuer
# Subject and Issuer should match

# If different, regenerate all certificates
.\scripts\generate-certs.ps1
```

### Renewal Failed

**Symptom**: Renewal script fails

**Solution**:
```powershell
# Check CA certificate exists
Test-Path certs\ca.crt
Test-Path certs\ca.key

# Verify OpenSSL is installed
openssl version

# Check file permissions
Get-Acl certs\ca.key

# Manual renewal if needed
openssl genrsa -out certs\server.key 2048
openssl req -new -key certs\server.key -out certs\server.csr
openssl x509 -req -in certs\server.csr -CA certs\ca.crt -CAkey certs\ca.key -out certs\server.crt -days 365
```

## Integration with Agent

### Agent Certificate Features

The DCIM_Agent has similar features:

**Agent Features**:
- ✅ Certificate validation on startup
- ✅ Expiry date logging
- ✅ mTLS client authentication
- ✅ Certificate chain verification
- ✅ TLS 1.2/1.3 support

**Consistency**: Use same CA for server and all agents

### Agent Renewal Process

When renewing agent certificates:

1. **Server side**: `.\scripts\renew-client-cert.ps1 -AgentName "agent-01"`
2. **Copy to agent**: `Copy-Item certs\agents\agent-01\* \\agent-01\certs\`
3. **Restart agent**: On agent machine, restart the agent service
4. **Verify**: Check server logs for successful connection

## Compliance

### Audit Requirements

Many compliance frameworks require certificate management:

**PCI-DSS**:
- Annual certificate rotation ✅
- Certificate expiry monitoring ✅
- Secure key storage ✅

**SOC 2**:
- Certificate lifecycle management ✅
- Expiry notifications ✅
- Renewal procedures documented ✅

**ISO 27001**:
- Certificate inventory ✅
- Renewal schedule ✅
- Access control to private keys ✅

### Audit Trail

Keep records of certificate operations:

```powershell
# Log all certificate operations
$logFile = "logs\certificate_operations.log"

function Log-CertOperation {
    param($Operation, $Certificate, $User)

    $entry = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Operation - $Certificate - User: $User"
    Add-Content $logFile $entry
}

# Example usage
Log-CertOperation "RENEWED" "server.crt" $env:USERNAME
```

## API Integration (Future)

The certificate manager can be exposed via API:

```
GET /api/v1/certificates          # List all certificates
GET /api/v1/certificates/server   # Get server certificate info
GET /api/v1/certificates/expiry   # Check expiry status
POST /api/v1/certificates/renew   # Trigger renewal
GET /api/v1/certificates/backup   # Download certificate backup
```

## Summary

✅ **Automatic Monitoring** - Server checks certificates hourly
✅ **30-Day Alerts** - Warns before expiry
✅ **One-Command Renewal** - Simple renewal scripts
✅ **Automatic Backups** - Safe renewal process
✅ **Multi-Agent Support** - Manage all agent certificates
✅ **Subscription Tracking** - Monitor validity periods
✅ **Compliance Ready** - Meets audit requirements

### Quick Reference

```powershell
# Check expiry
.\scripts\check-cert-expiry.ps1

# Renew server
.\scripts\renew-server-cert.ps1

# Renew agent
.\scripts\renew-client-cert.ps1 -AgentName "agent-name"

# New agent
.\scripts\generate-client-cert.ps1 -AgentName "new-agent"

# Backup
Copy-Item certs certs.backup -Recurse
```

Your certificates are now fully managed with subscription-style tracking!

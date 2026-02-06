# DCIM License Management Guide

Simple guide for generating, renewing, and managing DCIM Server licenses.

## License Overview

DCIM Server uses a license file to control:
- Maximum number of agents
- Maximum number of SNMP devices
- Feature access
- Expiration date

**License file location:** `DCIM_Server/license.json`

---

## Generate New License

### Basic License (Default)

```powershell
cd DCIM_Server

# Generate default license (10 agents, 50 SNMP devices, 1 year)
go run . -generate-license
```

This creates `license.json` in the current directory.

### Custom License

```powershell
cd DCIM_Server

# Generate custom license
go run . -generate-license `
  -license-company "Your Company Name" `
  -license-email "admin@company.com" `
  -license-agents 100 `
  -license-snmp 500 `
  -license-years 1
```

**Or build the server first:**
```powershell
go build -o dcim-server.exe .

.\dcim-server.exe -generate-license `
  -license-company "Acme Corp" `
  -license-email "admin@acme.com" `
  -license-agents 50 `
  -license-snmp 200 `
  -license-years 2
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `-license-company` | Company name | "Example Company" |
| `-license-email` | Contact email | "admin@example.com" |
| `-license-agents` | Max agents | 100 |
| `-license-snmp` | Max SNMP devices | 500 |
| `-license-years` | Validity (years) | 1 |
| `-license-output` | Output file path | "license.json" |

---

## License File Format

**Example `license.json`:**
```json
{
  "license_key": "DCIM-YourCompany-1234567890",
  "company_name": "Your Company",
  "email": "admin@company.com",
  "max_agents": 100,
  "max_snmp_devices": 500,
  "features": [
    "basic_monitoring",
    "alerting",
    "snmp_monitoring",
    "advanced_analytics",
    "dashboard"
  ],
  "issued_at": "2024-02-03T00:00:00Z",
  "expires_at": "2025-02-03T00:00:00Z",
  "signature": "a1b2c3d4e5f6..."
}
```

---

## Renew License

### Option 1: Generate New License

```powershell
cd DCIM_Server

# Backup old license
Copy-Item license.json license.json.backup

# Generate new license (extends expiry)
go run . -generate-license `
  -license-company "Your Company" `
  -license-email "admin@company.com" `
  -license-agents 100 `
  -license-snmp 500 `
  -license-years 1

# Restart server to apply
taskkill /IM dcim-server.exe /F
.\dcim-server.exe -config config.yaml
```

### Option 2: Update Existing License

Manually edit `license.json`:
```json
{
  "expires_at": "2027-02-03T00:00:00Z"
}
```

**Note:** You'll need to regenerate the signature if you manually edit.
It's easier to just generate a new license.

---

## Upgrade License Limits

### Increase Agent Limit

```powershell
cd DCIM_Server

# Backup current license
Copy-Item license.json license.json.old

# Generate new license with higher limits
go run . -generate-license `
  -license-agents 200 `
  -license-snmp 500 `
  -license-years 1

# Restart server
.\dcim-server.exe -config config.yaml
```

### Increase SNMP Device Limit

```powershell
go run . -generate-license `
  -license-agents 100 `
  -license-snmp 1000 `
  -license-years 1
```

---

## Check License Status

### View License Info

**Method 1: Check server health endpoint**
```powershell
curl.exe -k https://localhost:8443/health
```

Response includes license info:
```json
{
  "status": "ok",
  "details": {
    "license": {
      "company": "Your Company",
      "max_agents": 100,
      "max_snmp_devices": 500,
      "expires_at": "2025-02-03T00:00:00Z",
      "expires_in_days": 365
    }
  }
}
```

**Method 2: Read license file**
```powershell
Get-Content DCIM_Server\license.json | ConvertFrom-Json | Format-List
```

**Method 3: Check server logs**
```powershell
Get-Content DCIM_Server\server.log | Select-String "license"
```

---

## Disable License Enforcement

For testing or development, you can disable license checks.

**Edit `config.yaml`:**
```yaml
license:
  mode: "file"
  file_path: "./license.json"
  enforce: false    # Disable enforcement
```

**Or set to disabled mode:**
```yaml
license:
  mode: "disabled"  # No license required
```

**Restart server:**
```powershell
.\dcim-server.exe -config config.yaml
```

---

## License Warnings

The server automatically checks license status every hour and warns when:

### 30 Days Before Expiry
```
[WARN] License expires in 30 days
```

### 7 Days Before Expiry
```
[WARN] License expires in 7 days - Please renew
```

### License Expired
```
[ERROR] License has expired
[ERROR] Server running in grace period (7 days)
```

### Grace Period Ended
```
[CRITICAL] License grace period ended - Server will reject new agents
```

---

## Grace Period

When license expires:
- Server continues running for 7 days (grace period)
- Existing agents continue working
- New agents are rejected
- Warnings logged every hour

**To extend:**
```powershell
# Generate new license immediately
go run . -generate-license -license-years 1
```

---

## Common Tasks

### Task 1: Initial Setup
```powershell
cd DCIM_Server
go run . -generate-license
.\dcim-server.exe -config config.yaml
```

### Task 2: Annual Renewal
```powershell
cd DCIM_Server
Copy-Item license.json license-2025.json.backup
go run . -generate-license -license-years 1
.\dcim-server.exe -config config.yaml
```

### Task 3: Upgrade from 10 to 100 Agents
```powershell
cd DCIM_Server
go run . -generate-license -license-agents 100
taskkill /IM dcim-server.exe /F
.\dcim-server.exe -config config.yaml
```

### Task 4: Emergency - Disable Licensing
```yaml
# Edit config.yaml
license:
  mode: "disabled"
```

### Task 5: Copy License to Another Server
```powershell
# Copy license file
Copy-Item DCIM_Server\license.json \\server2\DCIM_Server\license.json

# Restart remote server
```

---

## Troubleshooting

### License File Not Found
```
[ERROR] Failed to load license: open ./license.json: no such file
```

**Solution:**
```powershell
cd DCIM_Server
go run . -generate-license
```

### License Limit Reached
```
[ERROR] License limit reached: Cannot register new agent
```

**Solution:**
```powershell
# Generate license with higher limit
go run . -generate-license -license-agents 200
```

### License Expired
```
[ERROR] License has expired
```

**Solution:**
```powershell
# Generate new license
go run . -generate-license -license-years 1

# Restart server
```

### Invalid License Signature
```
[ERROR] License signature verification failed
```

**Solution:**
```powershell
# Regenerate license (don't manually edit)
go run . -generate-license
```

---

## Best Practices

1. **Backup Licenses**
   ```powershell
   Copy-Item license.json "license-backup-$(Get-Date -Format yyyy-MM-dd).json"
   ```

2. **Set Calendar Reminders**
   - 60 days before expiry: Plan renewal
   - 30 days before expiry: Generate new license
   - 7 days before expiry: Emergency renewal

3. **Monitor License Usage**
   ```powershell
   # Check current agent count
   curl.exe -k https://localhost:8443/health
   ```

4. **Document License Limits**
   - Keep track of max_agents
   - Keep track of max_snmp_devices
   - Note expiration date

5. **Test License Changes**
   - Test new license on development server first
   - Verify server starts correctly
   - Check health endpoint

---

## Quick Reference

```powershell
# Generate license
cd DCIM_Server
go run . -generate-license -license-agents 100

# Check license status
curl.exe -k https://localhost:8443/health

# Renew license
go run . -generate-license -license-years 1

# Disable enforcement (testing only)
# Edit config.yaml: license.enforce = false

# Restart server after license change
.\dcim-server.exe -config config.yaml
```

---

For build and deployment instructions, see [BUILD_AND_RUN.md](BUILD_AND_RUN.md)

Last Updated: 2026-02-04

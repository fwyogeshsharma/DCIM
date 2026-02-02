# Certificate Validity Period Guide

## Overview
The enhanced `generate-certs.ps1` script now allows you to customize the validity period for each certificate type (CA, Server, Client) during generation.

---

## Interactive Validity Period Selection

When running the script, you'll be prompted **three times** to set validity periods:

1. **Certificate Authority (CA)** - Default: 1 year
2. **Server Certificate** - Default: 1 year
3. **Client/Agent Certificate** - Default: 1 year

---

## Usage Examples

### Example 1: Accept All Defaults (Recommended for Most Cases)

```
Step 1/3: Certificate Authority (CA)
Certificate Validity Period for Certificate Authority (CA)
-------------------------------------------
Choose validity period:
  1. Days
  2. Months
  3. Years (default: 1 year)

Select option (1-3, or press Enter for default): [Press Enter]
[OK] Certificate will be valid for: 1 year (365 days)

Step 2/3: Server Certificate
Select option (1-3, or press Enter for default): [Press Enter]
[OK] Certificate will be valid for: 1 year (365 days)

Step 3/3: Client/Agent Certificate
Select option (1-3, or press Enter for default): [Press Enter]
[OK] Certificate will be valid for: 1 year (365 days)
```

**Result**: All certificates valid for 1 year

---

### Example 2: Long-lived CA (10 Years), Standard Server/Client (1 Year)

```
Step 1: CA Certificate
Select option: 3
Enter number of years: 10
[OK] Certificate will be valid for: 10 years (3650 days)

Step 2: Server Certificate
Select option: [Enter] (default 1 year)
[OK] Certificate will be valid for: 1 year (365 days)

Step 3: Client Certificate
Select option: [Enter] (default 1 year)
[OK] Certificate will be valid for: 1 year (365 days)
```

**Result**: CA 10 years (recommended for production), Server and Client 1 year

---

### Example 3: Short-lived Certificates for High Security

```
Step 1: CA Certificate
Select option: 3
Enter number of years: 5
[OK] Certificate will be valid for: 5 years (1825 days)

Step 2: Server Certificate
Select option: 2
Enter number of months: 3
[OK] Certificate will be valid for: 3 months (90 days)

Step 3: Client Certificate
Select option: 1
Enter number of days: 30
[OK] Certificate will be valid for: 30 days (30 days)
```

**Result**: CA 5 years, Server 3 months, Client 30 days (monthly rotation)

---

## Validity Period Options

### Option 1: Days
- **Range**: 1 to 36,500 days (100 years)
- **Use Cases**:
  - Short-lived certificates (1-90 days)
  - Testing environments
  - High-security scenarios requiring frequent rotation
- **Example**: 30 days, 90 days, 180 days

### Option 2: Months
- **Range**: 1 to 1,200 months (100 years)
- **Calculation**: Months × 30 days
- **Use Cases**:
  - Quarterly rotation (3 months)
  - Semi-annual rotation (6 months)
  - Standard certificates (12-24 months)
- **Example**: 3 months = 90 days, 12 months = 360 days

### Option 3: Years (Default)
- **Range**: 1 to 100 years
- **Calculation**: Years × 365 days
- **Use Cases**:
  - Long-lived CA certificates (5-10 years)
  - Standard server certificates (1-2 years)
  - Enterprise deployments
- **Example**: 1 year = 365 days, 10 years = 3650 days

---

## Recommended Validity Periods

**Note**: All certificates default to **1 year** validity. For production CA certificates, consider extending to 5-10 years.

### Production Environments

| Certificate Type | Recommended Period | Reasoning |
|-----------------|-------------------|-----------|
| **CA Certificate** | 5-10 years | Long-lived root of trust, rarely rotated |
| **Server Certificate** | 1 year (default) | Balance between security and maintenance |
| **Client Certificate** | 1 year (default) | Regular rotation for security |

### Development/Testing

| Certificate Type | Recommended Period | Reasoning |
|-----------------|-------------------|-----------|
| **CA Certificate** | 5 years | Sufficient for dev lifecycle |
| **Server Certificate** | 1 year | Match production patterns |
| **Client Certificate** | 90 days | Test rotation procedures |

### High Security / Zero Trust

| Certificate Type | Recommended Period | Reasoning |
|-----------------|-------------------|-----------|
| **CA Certificate** | 5 years | More frequent CA rotation |
| **Server Certificate** | 3-6 months | Quarterly/semi-annual rotation |
| **Client Certificate** | 30-90 days | Monthly/quarterly rotation |

---

## Certificate Expiry Tracking

After generation, the script creates `certs/CERTIFICATE_INFO.txt` with:

```
## Certificate Validity Summary:
  📜 CA Certificate:
     Valid for: 10 years
     Expires: 2036-02-02

  🖥️  Server Certificate:
     Valid for: 1 year
     Expires: 2027-02-02

  💻 Client Certificate:
     Valid for: 90 days
     Expires: 2026-05-03

## Renewal Reminders
Set calendar reminders 30 days before expiry:
- CA: Renew by 2036-01-03
- Server: Renew by 2027-01-03
- Client: Renew by 2026-04-03
```

---

## Best Practices

### 1. **Certificate Lifetime Guidelines**

✅ **Do:**
- Use long-lived CA certificates (5-10 years)
- Rotate server/client certificates annually or more frequently
- Set calendar reminders 30 days before expiry
- Test renewal procedures before expiry

❌ **Don't:**
- Create CA certificates shorter than 2 years
- Use certificates longer than 2 years for internet-facing servers
- Forget to track expiry dates
- Wait until last minute to renew

### 2. **Security Considerations**

**Short-lived certificates (30-90 days):**
- ✅ Higher security (limited exposure window)
- ✅ Limits damage from compromise
- ❌ More frequent rotation required
- ❌ Higher operational overhead

**Long-lived certificates (2+ years):**
- ✅ Less maintenance overhead
- ✅ Fewer rotation outages
- ❌ Longer exposure if compromised
- ❌ Against modern security best practices

### 3. **Automation Recommendations**

For production environments with short-lived certificates:
- Implement automated certificate renewal
- Use monitoring to alert 30 days before expiry
- Test renewal procedures in staging first
- Keep audit logs of certificate rotations

---

## Renewal Process

When certificates approach expiry:

### Method 1: Generate New Certificates (Complete Replacement)
```powershell
# Back up old certificates
Copy-Item certs certs.backup -Recurse

# Generate new certificates with desired validity
.\scripts\generate-certs.ps1

# Deploy new certificates to all agents/servers
# Update configurations if needed
```

### Method 2: Renew Individual Certificates

For server certificate only:
```powershell
# Generate new server certificate signed by existing CA
openssl genrsa -out certs/server.key 2048
openssl req -new -key certs/server.key -out certs/server.csr -config server.cnf
openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key \
  -CAcreateserial -out certs/server.crt -days 365 \
  -extfile server.cnf -extensions v3_req
```

---

## Common Scenarios

### Scenario 1: Testing Certificate Rotation
**Goal**: Test renewal procedures without waiting a year

**Solution**:
```
CA: 10 years (long-lived)
Server: 7 days
Client: 7 days
```

Test rotation after 5 days to ensure procedures work.

### Scenario 2: Compliance Requirements (PCI DSS, SOC 2)
**Goal**: Meet compliance requiring annual certificate rotation

**Solution**:
```
CA: 5-10 years
Server: 1 year (renew 30 days before expiry)
Client: 1 year (renew 30 days before expiry)
```

### Scenario 3: Zero Trust Architecture
**Goal**: Minimize trust window, frequent rotation

**Solution**:
```
CA: 5 years
Server: 90 days
Client: 30 days
```

Implement automated rotation scripts.

---

## Verification Commands

Check certificate validity period:
```powershell
# View CA certificate details
openssl x509 -in certs/ca.crt -noout -dates

# View server certificate details
openssl x509 -in certs/server.crt -noout -dates

# View client certificate details
openssl x509 -in certs/client.crt -noout -dates
```

Output example:
```
notBefore=Feb  2 15:30:00 2026 GMT
notAfter=Feb  2 15:30:00 2027 GMT
```

---

## Troubleshooting

### Issue: "Certificate expired" error
**Cause**: Certificate validity period has ended
**Solution**: Generate new certificates with appropriate validity period

### Issue: "Certificate not yet valid" error
**Cause**: System clock incorrect or certificate has future start date
**Solution**: Check system time synchronization

### Issue: "Unable to verify certificate" error
**Cause**: CA certificate expired before server/client certificates
**Solution**: Ensure CA validity period exceeds all signed certificates

---

## Summary

The enhanced certificate generation script provides flexibility to:
- ✅ Customize validity periods per certificate type
- ✅ Choose days, months, or years for easy specification
- ✅ Track expiry dates automatically
- ✅ Generate renewal reminders
- ✅ Maintain security best practices

**Quick Start**: Just press Enter at each prompt to use recommended defaults (CA: 10 years, Server/Client: 1 year)

For questions or issues, refer to `MTLS_CERTIFICATE_GUIDE.md` for complete mTLS setup instructions.

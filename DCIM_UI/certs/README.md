# Certificate Directory

This directory contains the TLS certificates used for mTLS authentication with the DCIM server.

## Required Files

### Option 1: PEM Format (Recommended)
```
client.crt    - Client certificate (PEM format)
client.key    - Client private key (PEM format)
ca.crt        - CA certificate (PEM format)
```

### Option 2: PKCS#12 Format
```
client.pfx    - Client certificate + key in PKCS#12 format
```

## How to Copy Certificates

### From Agent Certificates
If you're using the same certificates as your DCIM agent:

**Windows:**
```cmd
copy E:\Projects\DCIM\DCIM_Agent\build\windows\certs\client.crt .
copy E:\Projects\DCIM\DCIM_Agent\build\windows\certs\client.key .
copy E:\Projects\DCIM\DCIM_Agent\build\windows\certs\ca.crt .
```

**Linux/Mac:**
```bash
cp /path/to/agent/certs/client.crt .
cp /path/to/agent/certs/client.key .
cp /path/to/agent/certs/ca.crt .
```

### From Server Certificates
You can also use the server's client certificates if you have them.

## Verify Certificates

```bash
# Check certificate details
openssl x509 -in client.crt -text -noout

# Verify certificate chain
openssl verify -CAfile ca.crt client.crt

# Check if key matches certificate
openssl x509 -in client.crt -noout -modulus | openssl md5
openssl rsa -in client.key -noout -modulus | openssl md5
# (Both should output the same hash)
```

## Security

⚠️ **Important**: These files contain sensitive credentials!

- **Never commit** these files to git (they're in .gitignore)
- Keep file permissions restricted: `chmod 600 *.key`
- Use different certificates for each environment (dev, staging, prod)
- Rotate certificates regularly

## File Permissions (Linux/Mac)

```bash
chmod 600 client.key  # Private key: owner read/write only
chmod 644 client.crt  # Certificate: owner read/write, others read
chmod 644 ca.crt      # CA cert: owner read/write, others read
```

## Certificate Formats

### PEM Format (ASCII)
```
-----BEGIN CERTIFICATE-----
MIIDXTCCAkWgAwIBAgIJAKL...
-----END CERTIFICATE-----
```

### DER Format (Binary)
Convert to PEM if needed:
```bash
openssl x509 -inform der -in client.cer -out client.crt
```

### PKCS#12/PFX Format
Contains both certificate and key:
```bash
# Extract certificate
openssl pkcs12 -in client.pfx -clcerts -nokeys -out client.crt

# Extract private key
openssl pkcs12 -in client.pfx -nocerts -nodes -out client.key

# Extract CA certificate
openssl pkcs12 -in client.pfx -cacerts -nokeys -out ca.crt
```

## Troubleshooting

### "Certificate verification failed"
- Ensure ca.crt is the same CA that signed the server certificate
- Check certificate hasn't expired: `openssl x509 -in client.crt -noout -dates`

### "Permission denied"
- Check file exists: `ls -la`
- Ensure read permissions: `chmod 644 client.crt`

### "Unable to load client certificate"
- Verify file format: `file client.crt` (should say "PEM certificate")
- Check for BOM or encoding issues
- Try regenerating in PEM format

---

**After adding certificates, restart the proxy server:**
```bash
npm run proxy
```

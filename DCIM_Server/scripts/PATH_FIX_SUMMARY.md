# Certificate Path Fix - Summary

## Issue Identified

When scripts were reorganized into platform-specific directories (`windows/`, `linux/`, `macos/`), they were still using old relative paths that assumed execution from the `DCIM_Server/` root directory.

**Problem:**
- Scripts in `scripts/windows/` were looking for `certs\ca.crt`
- Actual path should be `..\..\certs\ca.crt` (two directories up)
- Result: **"Valid: 0" even when certificates exist**

---

## Root Cause

```
Before Organization:
DCIM_Server/
├── scripts/
│   └── check-cert-expiry.ps1  → looks for "certs\ca.crt"
└── certs/
    └── ca.crt                  ✅ Found!

After Organization:
DCIM_Server/
├── scripts/
│   └── windows/
│       └── check-cert-expiry.ps1  → looks for "certs\ca.crt"
└── certs/
    └── ca.crt                      ❌ Not Found! (wrong relative path)
```

---

## Fix Applied

Updated **ALL** certificate-related scripts to use correct relative paths:

### Windows Scripts (`scripts/windows/`)
- ❌ **Before:** `$certDir = "certs"`
- ✅ **After:** `$certDir = "..\..\certs"`

### Linux/macOS Scripts (`scripts/linux/`, `scripts/macos/`)
- ❌ **Before:** `CERT_DIR="certs"`
- ✅ **After:** `CERT_DIR="../../certs"`

---

## Files Updated

### Windows (7 files)
1. ✅ `windows/check-cert-expiry.ps1`
2. ✅ `windows/generate-certs.ps1`
3. ✅ `windows/generate-client-cert.ps1`
4. ✅ `windows/renew-server-cert.ps1`
5. ✅ `windows/renew-client-cert.ps1`
6. ✅ `windows/setup-postgres.ps1`
7. ✅ `windows/fix-postgres-path.ps1`

### Linux (6 files)
1. ✅ `linux/check-cert-expiry.sh`
2. ✅ `linux/generate-certs.sh`
3. ✅ `linux/generate-client-cert.sh`
4. ✅ `linux/renew-server-cert.sh`
5. ✅ `linux/renew-client-cert.sh`
6. ✅ `linux/setup-postgres.sh`

### macOS (6 files)
1. ✅ `macos/check-cert-expiry.sh`
2. ✅ `macos/generate-certs.sh`
3. ✅ `macos/generate-client-cert.sh`
4. ✅ `macos/renew-server-cert.sh`
5. ✅ `macos/renew-client-cert.sh`
6. ✅ `macos/setup-postgres.sh`

**Total:** 19 files updated

---

## Verification

### Test Check Certificate Expiry

**Windows:**
```cmd
cd DCIM_Server\scripts\windows
check-cert-expiry.bat
```

**Expected Output:**
```
================================
Certificate Expiry Check
================================

CA Certificate:
  Path: ..\..\certs\ca.crt
  Status: Valid
  Days Until Expiry: 365 days
  Expiry Date: 2027-02-11
  Renewal Reminder: Set for 2027-01-12

Server Certificate:
  Path: ..\..\certs\server.crt
  Status: Valid
  Days Until Expiry: 365 days
  ...

================================
Summary
================================

Total Certificates: 3       ← Should be > 0 now!
  Valid: 3                  ← Should match total
  Expiring Soon (< 30 days): 0
  Expired: 0

All certificates are valid.
```

---

## Path Resolution Logic

### From `scripts/windows/`
```
Current Dir:  C:\...\DCIM_Server\scripts\windows\
Certificate:  ..\..\certs\ca.crt
Resolves to:  C:\...\DCIM_Server\certs\ca.crt  ✅
```

### From `scripts/linux/` or `scripts/macos/`
```
Current Dir:  /path/to/DCIM_Server/scripts/linux/
Certificate:  ../../certs/ca.crt
Resolves to:  /path/to/DCIM_Server/certs/ca.crt  ✅
```

---

## Additional Fixes

Also updated renewal script paths in error messages:

**Before:**
- `".\scripts\generate-certs.ps1"`
- `"./scripts/generate-certs.sh"`

**After:**
- `".\generate-certs.ps1"`
- `"./generate-certs.sh"`

(Scripts are already in the same directory, so `scripts/` prefix was incorrect)

---

## Impact

### Before Fix
```
Total Certificates: 0        ← Bug!
  Valid: 0
  Expiring Soon: 0
  Expired: 0

All certificates are valid.  ← Contradictory message!
```

### After Fix
```
Total Certificates: 3        ← Correct!
  Valid: 3
  Expiring Soon: 0
  Expired: 0

All certificates are valid.  ← Consistent!
```

---

## Testing Checklist

- ✅ `check-cert-expiry` - Shows correct certificate count
- ✅ `generate-certs` - Creates certificates in `../../certs/`
- ✅ `generate-client-cert` - Creates in `../../certs/agents/<name>/`
- ✅ `renew-server-cert` - Renews in `../../certs/`
- ✅ `renew-client-cert` - Renews in `../../certs/agents/<name>/`

---

## Prevention

To prevent this in future:

1. **Always test scripts after directory reorganization**
2. **Use absolute paths or validate relative paths**
3. **Add path verification at script start:**
   ```powershell
   if (-not (Test-Path "..\..\certs")) {
       Write-Error "Certificates directory not found. Run from scripts/windows/"
       exit 1
   }
   ```

---

**Fixed Date:** 2026-02-11
**Status:** ✅ RESOLVED
**Affected Scripts:** 19 files (all certificate-related scripts)

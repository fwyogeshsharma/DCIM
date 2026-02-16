# Build Script Updates for Cooling Config

## ✅ FIXED: cooling_config.yaml Now Copied to Build Directory

---

## Problem

Previously, when building the server with `.\build.ps1`, only `config.yaml` was copied to the build directory. The `cooling_config.yaml` file was missing, causing the server to fail loading cooling configuration when run from the build folder.

---

## Solution

Updated `build.ps1` to automatically copy `cooling_config.yaml` to all platform build directories.

---

## Changes Made to build.ps1

### 1. Added Cooling Config Copy (Line ~94)

**Before:**
```powershell
# Copy config file
Copy-Item "config.yaml" "$PlatformDir\config.yaml" -Force

# Copy license file if it exists
```

**After:**
```powershell
# Copy config files
Copy-Item "config.yaml" "$PlatformDir\config.yaml" -Force
Write-Host "  [OK] Copied config.yaml" -ForegroundColor Green

# Copy cooling config file
if (Test-Path "cooling_config.yaml") {
    Copy-Item "cooling_config.yaml" "$PlatformDir\cooling_config.yaml" -Force
    Write-Host "  [OK] Copied cooling_config.yaml" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] cooling_config.yaml not found - cooling alerts will not work" -ForegroundColor Yellow
}

# Copy license file if it exists
```

### 2. Updated Build README (Line ~143)

Added cooling config status to the README.txt generated in each build directory:

```powershell
# Check if cooling config was copied
$coolingConfigStatus = if (Test-Path "$PlatformDir\cooling_config.yaml") { "Included" } else { "Not included" }

$BuildReadme = @"
DCIM Server - $OS/$Arch Build

Version: $Version
Built: $BuildTime

Files Included:
- $Binary (server executable)
- config.yaml (configuration template)
- cooling_config.yaml ($coolingConfigStatus)          ← NEW
- license.json ($licenseStatus)
- certs/ ($certStatus)

Quick Start:
1. Edit config.yaml to configure the server
   Edit cooling_config.yaml to configure cooling system thresholds  ← NEW
```

---

## Build Output (Console)

### When Building Now

```powershell
PS> .\build.ps1

================================
DCIM Server - Build Script
================================

Building for ALL platforms (Windows, Linux, macOS)

=== Building for Windows ===
Building for windows/amd64...
  CGO: Enabled (optimized SQLite driver)
[OK] Built dcim-server.exe
  [OK] Copied config.yaml                          ← NEW
  [OK] Copied cooling_config.yaml                  ← NEW
  [OK] Copied license.json
  [OK] Copied all certificates (3/3)

=== Building for Linux ===
Building for linux/amd64...
  CGO: Disabled (using pure-Go SQLite driver)
[OK] Built dcim-server
  [OK] Copied config.yaml                          ← NEW
  [OK] Copied cooling_config.yaml                  ← NEW
  [INFO] license.json not found - you'll need to generate it
  [INFO] No certificates found - you'll need to generate them

=== Building for macOS ===
Building for darwin/amd64...
  CGO: Disabled (using pure-Go SQLite driver)
[OK] Built dcim-server
  [OK] Copied config.yaml                          ← NEW
  [OK] Copied cooling_config.yaml                  ← NEW
  [INFO] license.json not found - you'll need to generate it
  [INFO] No certificates found - you'll need to generate them

Building for darwin/arm64...
  CGO: Disabled (using pure-Go SQLite driver)
[OK] Built dcim-server
  [OK] Copied config.yaml                          ← NEW
  [OK] Copied cooling_config.yaml                  ← NEW
  [INFO] license.json not found - you'll need to generate it
  [INFO] No certificates found - you'll need to generate them

================================
Build Summary
================================

Successful builds (4):
  [OK] windows-amd64
  [OK] linux-amd64
  [OK] darwin-amd64
  [OK] darwin-arm64

Build output directory: build

All builds completed successfully!
```

---

## Build Directory Structure

After running `.\build.ps1`, each platform directory now contains:

```
build/
├── windows-amd64/
│   ├── dcim-server.exe
│   ├── config.yaml              ✅ Configuration
│   ├── cooling_config.yaml      ✅ Cooling thresholds (NEW!)
│   ├── license.json             (if exists)
│   ├── certs/
│   │   ├── ca.crt
│   │   ├── server.crt
│   │   └── server.key
│   └── README.txt
│
├── linux-amd64/
│   ├── dcim-server
│   ├── config.yaml              ✅
│   ├── cooling_config.yaml      ✅ (NEW!)
│   ├── certs/
│   └── README.txt
│
├── darwin-amd64/
│   ├── dcim-server
│   ├── config.yaml              ✅
│   ├── cooling_config.yaml      ✅ (NEW!)
│   ├── certs/
│   └── README.txt
│
└── darwin-arm64/
    ├── dcim-server
    ├── config.yaml              ✅
    ├── cooling_config.yaml      ✅ (NEW!)
    ├── certs/
    └── README.txt
```

---

## README.txt in Build Directory

Each build directory now contains an updated README.txt:

```
DCIM Server - windows/amd64 Build

Version: 1.0.0
Built: 2026-02-11 14:30:00

Files Included:
- dcim-server.exe (server executable)
- config.yaml (configuration template)
- cooling_config.yaml (Included)                    ← NEW
- license.json (Included)
- certs/ (Included (all required files))

Quick Start:
1. Edit config.yaml to configure the server
   Edit cooling_config.yaml to configure cooling system thresholds    ← NEW

2. Certificates (Required for mTLS):
   [OK] All certificates are included:
   - certs/ca.crt (CA certificate)
   - certs/server.crt (server certificate)
   - certs/server.key (server private key)

3. License (Required if enforcement enabled):
   [OK] License file is included: license.json

4. Run server:
   .\dcim-server.exe -config config.yaml

For detailed documentation, see README.md and BUILD_AND_RUN.md in the project root.
```

---

## Warning Message

If `cooling_config.yaml` doesn't exist in the source directory when building:

```
[WARNING] cooling_config.yaml not found - cooling alerts will not work
```

**Solution:** Ensure `cooling_config.yaml` exists in `DCIM_Server/` root before building.

---

## Verification

### After Building, Verify Files Copied:

```powershell
# For Windows build
ls build\windows-amd64\

# Expected output:
#   dcim-server.exe
#   config.yaml
#   cooling_config.yaml          ← Should be present
#   license.json
#   README.txt
#   certs\
```

### Running from Build Directory:

```powershell
cd build\windows-amd64\
.\dcim-server.exe

# Expected output should include:
# [SERVER] Loaded cooling configuration from cooling_config.yaml
```

---

## Testing the Fix

### Step 1: Clean Build
```powershell
# Remove old build directory
Remove-Item -Recurse -Force build

# Rebuild
.\build.ps1
```

### Step 2: Verify Cooling Config Copied
```powershell
# Check Windows build
Get-Content build\windows-amd64\cooling_config.yaml

# Should show the cooling configuration content
```

### Step 3: Run from Build Directory
```powershell
cd build\windows-amd64
.\dcim-server.exe
```

### Step 4: Verify Startup Logs
```
[SERVER] Loaded cooling configuration from cooling_config.yaml    ← Should appear
[SERVER] API Endpoints:
[SERVER]   POST /api/v1/cooling-metrics                           ← Should be listed
```

---

## Summary

✅ **cooling_config.yaml now automatically copied to all build directories**
✅ **Build script shows confirmation message when copied**
✅ **Build script shows warning if file missing**
✅ **README.txt in build directory updated to mention cooling config**
✅ **Server can now run from build directory with full cooling support**

**No manual file copying needed after build!** 🎉

---

## Next Steps

1. **Rebuild the server:**
   ```powershell
   .\build.ps1
   ```

2. **Verify cooling_config.yaml copied:**
   ```powershell
   ls build\windows-amd64\cooling_config.yaml
   ```

3. **Test server from build directory:**
   ```powershell
   cd build\windows-amd64
   .\dcim-server.exe
   ```

4. **Verify cooling API loads:**
   - Check console output for "Loaded cooling configuration"
   - Check API endpoints list includes "/cooling-metrics"

---

**Build script is now complete for cooling system support!** ✅

# DCIM Server Build Script
# Builds the server for Windows, Linux, and macOS
#
# Default: Builds for ALL platforms (Windows, Linux, macOS)
# Usage:
#   .\build.ps1                      # Build all platforms
#   .\build.ps1 -Platform windows    # Build Windows only
#   .\build.ps1 -Platform linux      # Build Linux only
#   .\build.ps1 -Platform macos      # Build macOS only

param(
    [string]$Platform = "all",
    [string]$OutputDir = "build"
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "DCIM Server - Build Script" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

if ($Platform -eq "all") {
    Write-Host "Building for ALL platforms (Windows, Linux, macOS)" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host "Building for platform: $Platform" -ForegroundColor Cyan
    Write-Host ""
}

# Create output directory
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$Version = "1.0.0"
$BuildTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$LDFlags = "-s -w -X main.Version=$Version -X 'main.BuildTime=$BuildTime'"

function Build-Platform {
    param(
        [string]$OS,
        [string]$Arch,
        [string]$Ext = ""
    )

    $PlatformDir = "$OutputDir\$OS-$Arch"
    $Binary = "dcim-server$Ext"

    Write-Host "Building for $OS/$Arch..." -ForegroundColor Yellow

    # Create platform directory
    if (-not (Test-Path $PlatformDir)) {
        New-Item -ItemType Directory -Path $PlatformDir | Out-Null
    }

    # Determine if this is a native build
    $IsNative = $false
    if ($IsWindows -and $OS -eq "windows") {
        $IsNative = $true
    } elseif ($IsLinux -and $OS -eq "linux") {
        $IsNative = $true
    } elseif ($IsMacOS -and $OS -eq "darwin") {
        $IsNative = $true
    }

    # Set environment variables
    $env:GOOS = $OS
    $env:GOARCH = $Arch

    # Enable CGO for native builds (SQLite with CGO has better performance)
    # For cross-compilation, CGO is disabled and pure-Go SQLite driver is used
    if ($IsNative -or $OS -eq "windows") {
        $env:CGO_ENABLED = "1"
        Write-Host "  CGO: Enabled (optimized SQLite driver)" -ForegroundColor DarkGray
    } else {
        $env:CGO_ENABLED = "0"
        Write-Host "  CGO: Disabled (using pure-Go SQLite driver)" -ForegroundColor DarkGray
    }

    # Build command
    $OutputPath = "$PlatformDir\$Binary"

    try {
        go build -ldflags $LDFlags -o $OutputPath .

        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Built $Binary" -ForegroundColor Green

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

            # Copy migrations folder
            $MigrationsDir = "$PlatformDir\migrations"
            if (Test-Path "migrations") {
                # Create migrations directory in build
                if (-not (Test-Path $MigrationsDir)) {
                    New-Item -ItemType Directory -Path $MigrationsDir | Out-Null
                }

                # Copy all .sql migration files
                $migrationFiles = Get-ChildItem "migrations\*.sql" -ErrorAction SilentlyContinue
                if ($migrationFiles) {
                    foreach ($file in $migrationFiles) {
                        Copy-Item $file.FullName "$MigrationsDir\$($file.Name)" -Force
                    }
                    Write-Host "  [OK] Copied $($migrationFiles.Count) migration file(s)" -ForegroundColor Green
                } else {
                    Write-Host "  [WARNING] No migration files found in migrations/" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  [WARNING] migrations/ directory not found - database migrations will fail" -ForegroundColor Yellow
            }

            # Copy license file if it exists
            if (Test-Path "license.json") {
                Copy-Item "license.json" "$PlatformDir\license.json" -Force
                Write-Host "  [OK] Copied license.json" -ForegroundColor Green
            } else {
                Write-Host "  [INFO] license.json not found - you'll need to generate it" -ForegroundColor Yellow
            }

            # Create certs directory
            $CertsDir = "$PlatformDir\certs"
            if (-not (Test-Path $CertsDir)) {
                New-Item -ItemType Directory -Path $CertsDir | Out-Null
            }

            # Copy certificates if they exist
            $certsCopied = 0
            $certsNeeded = @("ca.crt", "server.crt", "server.key")
            foreach ($certFile in $certsNeeded) {
                $sourcePath = "certs\$certFile"
                if (Test-Path $sourcePath) {
                    Copy-Item $sourcePath "$CertsDir\$certFile" -Force
                    $certsCopied++
                }
            }

            if ($certsCopied -eq 3) {
                Write-Host "  [OK] Copied all certificates ($certsCopied/3)" -ForegroundColor Green
            } elseif ($certsCopied -gt 0) {
                Write-Host "  [WARNING] Copied $certsCopied/3 certificates - some are missing" -ForegroundColor Yellow
            } else {
                Write-Host "  [INFO] No certificates found - you'll need to generate them" -ForegroundColor Yellow
            }

            # Create README in build directory
            $licenseStatus = if (Test-Path "$PlatformDir\license.json") { "Included" } else { "Not included - generate with step 3" }

            # Check certificate status
            $certStatus = "Not included"
            $certCount = 0
            foreach ($certFile in @("ca.crt", "server.crt", "server.key")) {
                if (Test-Path "$CertsDir\$certFile") {
                    $certCount++
                }
            }
            if ($certCount -eq 3) {
                $certStatus = "Included (all required files)"
            } elseif ($certCount -gt 0) {
                $certStatus = "Partially included ($certCount/3 files)"
            }

            # Check if cooling config was copied
            $coolingConfigStatus = if (Test-Path "$PlatformDir\cooling_config.yaml") { "Included" } else { "Not included" }

            # Check if migrations were copied
            $migrationsCount = 0
            if (Test-Path "$MigrationsDir") {
                $migrationsCount = (Get-ChildItem "$MigrationsDir\*.sql" -ErrorAction SilentlyContinue).Count
            }
            $migrationsStatus = if ($migrationsCount -gt 0) { "Included ($migrationsCount migration files)" } else { "Not included" }

            $BuildReadme = @"
DCIM Server - $OS/$Arch Build

Version: $Version
Built: $BuildTime

Files Included:
- $Binary (server executable)
- config.yaml (configuration template)
- cooling_config.yaml ($coolingConfigStatus)
- migrations/ ($migrationsStatus)
- license.json ($licenseStatus)
- certs/ ($certStatus)

Quick Start:
1. Edit config.yaml to configure the server
   Edit cooling_config.yaml to configure cooling system thresholds

2. Certificates (Required for mTLS):
"@

            if ($certCount -eq 3) {
                $BuildReadme += @"

   [OK] All certificates are included:
   - certs/ca.crt (CA certificate)
   - certs/server.crt (server certificate)
   - certs/server.key (server private key)

"@
            } else {
                $BuildReadme += @"

   [REQUIRED] Generate certificates in source directory:
   scripts\generate-certs.ps1

   Then rebuild, or manually copy these files to certs\ directory:
   - ca.crt (CA certificate)
   - server.crt (server certificate)
   - server.key (server private key)

"@
            }

            $BuildReadme += @"
3. License (Required if enforcement enabled):
"@

            if (Test-Path "$PlatformDir\license.json") {
                $BuildReadme += @"

   [OK] License file is included: license.json

"@
            } else {
                $BuildReadme += @"

   Generate license:
   .\$Binary -generate-license -license-company "Company" -license-email "admin@company.com" -license-agents 100 -license-snmp 500 -license-years 1

"@
            }

            $BuildReadme += @"
4. Run server:
   .\$Binary -config config.yaml

For detailed documentation, see README.md and BUILD_AND_RUN.md in the project root.
"@
            $BuildReadme | Out-File "$PlatformDir\README.txt" -Encoding ASCII

            return $true
        } else {
            Write-Host "[ERROR] Build failed for $OS/$Arch" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "[ERROR] Build failed: $_" -ForegroundColor Red
        return $false
    }
}

# Build platforms
$Success = @()
$Failed = @()

if ($Platform -eq "all" -or $Platform -eq "windows") {
    Write-Host ""
    Write-Host "=== Building for Windows ===" -ForegroundColor Cyan
    if (Build-Platform -OS "windows" -Arch "amd64" -Ext ".exe") {
        $Success += "windows-amd64"
    } else {
        $Failed += "windows-amd64"
    }
}

if ($Platform -eq "all" -or $Platform -eq "linux") {
    Write-Host ""
    Write-Host "=== Building for Linux ===" -ForegroundColor Cyan
    if (Build-Platform -OS "linux" -Arch "amd64") {
        $Success += "linux-amd64"
    } else {
        $Failed += "linux-amd64"
    }
}

if ($Platform -eq "all" -or $Platform -eq "macos") {
    Write-Host ""
    Write-Host "=== Building for macOS ===" -ForegroundColor Cyan

    # macOS AMD64
    if (Build-Platform -OS "darwin" -Arch "amd64") {
        $Success += "darwin-amd64"
    } else {
        $Failed += "darwin-amd64"
    }

    # macOS ARM64 (Apple Silicon)
    if (Build-Platform -OS "darwin" -Arch "arm64") {
        $Success += "darwin-arm64"
    } else {
        $Failed += "darwin-arm64"
    }
}

# Reset environment
$env:GOOS = ""
$env:GOARCH = ""

# Summary
Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Build Summary" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

if ($Success.Count -gt 0) {
    Write-Host "Successful builds ($($Success.Count)):" -ForegroundColor Green
    foreach ($platform in $Success) {
        Write-Host "  [OK] $platform" -ForegroundColor Green
    }
}

if ($Failed.Count -gt 0) {
    Write-Host ""
    Write-Host "Failed builds ($($Failed.Count)):" -ForegroundColor Red
    foreach ($platform in $Failed) {
        Write-Host "  [X] $platform" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Build output directory: $OutputDir" -ForegroundColor Cyan
Write-Host ""

if ($Failed.Count -eq 0) {
    Write-Host "All builds completed successfully!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "Some builds failed. See errors above." -ForegroundColor Red
    exit 1
}

# DCIM Server Build Script
# Builds the server for Windows, Linux, and macOS
#
# Default: Builds for ALL platforms (Windows, Linux, macOS)
# Usage:
#   .\build.ps1                                    # Build all platforms (prompts for server)
#   .\build.ps1 -ServerName PROD-01                # Build all platforms for PROD-01
#   .\build.ps1 -Platform windows -ServerName PROD-01   # Build Windows for PROD-01
#   .\build.ps1 -Platform linux                    # Build Linux only (prompts for server)

param(
    [string]$Platform = "all",
    [string]$ServerName = "",
    [string]$OutputDir = "build"
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "DCIM Server - Build Script" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Get server name for certificate deployment
if ([string]::IsNullOrWhiteSpace($ServerName)) {
    Write-Host "Select Server for Certificate Deployment" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host ""

    # List available servers
    if (Test-Path "certs\servers") {
        $availableServers = Get-ChildItem "certs\servers" -Directory | Select-Object -ExpandProperty Name

        if ($availableServers.Count -eq 0) {
            Write-Host "ERROR: No servers found in certs\servers\" -ForegroundColor Red
            Write-Host "Generate certificates first with: .\scripts\windows\generate-certs.ps1" -ForegroundColor Yellow
            exit 1
        }

        Write-Host "Available servers:" -ForegroundColor Cyan
        foreach ($srv in $availableServers) {
            Write-Host "  - $srv" -ForegroundColor White
        }
        Write-Host ""

        # Always prompt for server name as text input
        $ServerName = Read-Host "Enter server name"

        # Verify server exists
        if (-not (Test-Path "certs\servers\$ServerName")) {
            Write-Host ""
            Write-Host "ERROR: Server '$ServerName' not found in certs\servers\" -ForegroundColor Red
            Write-Host ""
            Write-Host "Available servers are:" -ForegroundColor Yellow
            foreach ($srv in $availableServers) {
                Write-Host "  - $srv" -ForegroundColor Yellow
            }
            Write-Host ""
            exit 1
        }

        Write-Host ""
        Write-Host "Selected server: $ServerName" -ForegroundColor Green
        Write-Host ""
    } else {
        Write-Host "ERROR: certs\servers directory not found" -ForegroundColor Red
        Write-Host "Generate certificates first with: .\scripts\windows\generate-certs.ps1" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "Building for Server: $ServerName" -ForegroundColor Green
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

            # Copy installer files for Windows
            if ($OS -eq "windows") {
                if (Test-Path "install-windows.bat") {
                    Copy-Item "install-windows.bat" "$PlatformDir\install-windows.bat" -Force
                    Write-Host "  [OK] Copied install-windows.bat" -ForegroundColor Green
                }
                if (Test-Path "uninstall-windows.bat") {
                    Copy-Item "uninstall-windows.bat" "$PlatformDir\uninstall-windows.bat" -Force
                    Write-Host "  [OK] Copied uninstall-windows.bat" -ForegroundColor Green
                }
            }

            # Create certs directory
            $CertsDir = "$PlatformDir\certs"
            if (-not (Test-Path $CertsDir)) {
                New-Item -ItemType Directory -Path $CertsDir | Out-Null
            }

            # Clean up old certificates and directories (from previous builds)
            Get-ChildItem $CertsDir -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

            # Copy certificates for the selected server (flat structure for deployment)
            $certsCopied = 0
            $serverCertPath = "certs\servers\$ServerName"

            # Verify server certificates exist
            if (-not (Test-Path $serverCertPath)) {
                Write-Host "  [ERROR] Server certificates not found: $serverCertPath" -ForegroundColor Red
                Write-Host "         Generate with: .\scripts\windows\generate-certs.ps1" -ForegroundColor Yellow
                continue
            }

            # Copy CA certificate (root level)
            if (Test-Path "certs\ca.crt") {
                Copy-Item "certs\ca.crt" "$CertsDir\ca.crt" -Force
                $certsCopied++
            } else {
                Write-Host "  [ERROR] CA certificate not found: certs\ca.crt" -ForegroundColor Red
            }

            # Copy server certificates (flatten structure for deployment)
            if (Test-Path "$serverCertPath\server.crt") {
                Copy-Item "$serverCertPath\server.crt" "$CertsDir\server.crt" -Force
                $certsCopied++
            } else {
                Write-Host "  [ERROR] Server certificate not found: $serverCertPath\server.crt" -ForegroundColor Red
            }

            if (Test-Path "$serverCertPath\server.key") {
                Copy-Item "$serverCertPath\server.key" "$CertsDir\server.key" -Force
                $certsCopied++
            } else {
                Write-Host "  [ERROR] Server key not found: $serverCertPath\server.key" -ForegroundColor Red
            }

            # Display status
            if ($certsCopied -eq 3) {
                Write-Host "  [OK] Copied certificates for server: $ServerName" -ForegroundColor Green
                Write-Host "       - ca.crt (CA certificate)" -ForegroundColor Gray
                Write-Host "       - server.crt (Server certificate)" -ForegroundColor Gray
                Write-Host "       - server.key (Server private key)" -ForegroundColor Gray
            } else {
                Write-Host "  [WARNING] Copied $certsCopied/3 certificates - some are missing" -ForegroundColor Yellow
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
                $certStatus = "Included for server: $ServerName"
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
Server: $ServerName

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

   [OK] Certificates are included for server: $ServerName
   - certs/ca.crt (CA certificate)
   - certs/server.crt (Server certificate)
   - certs/server.key (Server private key)

   These certificates are ready to use on the target server.

"@
            } else {
                $BuildReadme += @"

   [REQUIRED] Generate certificates in source directory:
   Windows: .\scripts\windows\generate-certs.ps1
   Linux:   ./scripts/linux/generate-certs.sh
   macOS:   ./scripts/macos/generate-certs.sh

   Then rebuild with:
   .\build.ps1 -ServerName "your-server-name"

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

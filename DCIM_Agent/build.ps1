# DCIM Agent - PowerShell Build Script
# Cross-platform build script for Windows users

param(
    [string]$Version = "1.0.0",
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"

$BinaryName = "dcim-agent"
$BuildDir = "build"
$DistDir = "dist"
# Get UTC time (compatible with PowerShell 5.1+)
$BuildTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$LDFlags = "-X main.version=$Version -X main.buildTime=$BuildTime"

function Clean {
    Write-Host "Cleaning build artifacts..." -ForegroundColor Cyan
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
    if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
    if (Test-Path "coverage.out") { Remove-Item -Force "coverage.out" }
}

function InstallDeps {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    go mod download
    go mod tidy
}

function Verify-Binary {
    param(
        [string]$Path,
        [string]$ExpectedOS,
        [string]$ExpectedArch
    )

    if (!(Test-Path $Path)) {
        Write-Host "  [X] Binary not found: $Path" -ForegroundColor Red
        return $false
    }

    # Get file info
    $fileInfo = Get-Item $Path
    $sizeMB = [math]::Round($fileInfo.Length/1MB, 2)

    Write-Host "  [OK] Built: $($fileInfo.Name) ($sizeMB MB)" -ForegroundColor Green
    Write-Host "     Target: $ExpectedOS/$ExpectedArch" -ForegroundColor Gray

    # Verify it's a valid executable by checking magic bytes
    $absolutePath = $fileInfo.FullName
    $bytes = [System.IO.File]::ReadAllBytes($absolutePath)
    if ($ExpectedOS -eq "windows") {
        if ($bytes[0] -eq 0x4D -and $bytes[1] -eq 0x5A) {
            Write-Host "     Format: PE (Windows Executable)" -ForegroundColor Gray
        } else {
            Write-Host "     WARNING: Not a valid Windows PE executable!" -ForegroundColor Yellow
        }
    } elseif ($ExpectedOS -eq "linux") {
        if ($bytes[0] -eq 0x7F -and $bytes[1] -eq 0x45 -and $bytes[2] -eq 0x4C -and $bytes[3] -eq 0x46) {
            Write-Host "     Format: ELF (Linux Executable)" -ForegroundColor Gray
        }
    } elseif ($ExpectedOS -eq "darwin") {
        if (($bytes[0] -eq 0xCF -and $bytes[1] -eq 0xFA) -or ($bytes[0] -eq 0xCE -and $bytes[1] -eq 0xFA)) {
            Write-Host "     Format: Mach-O (macOS Executable)" -ForegroundColor Gray
        }
    }

    return $true
}

function Build-Windows {
    Write-Host "Building for Windows amd64..." -ForegroundColor Green
    $env:GOOS = "windows"
    $env:GOARCH = "amd64"
    $env:CGO_ENABLED = "0"

    $outDir = "$BuildDir\windows"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    go build -ldflags "$LDFlags -s -w" -o "$outDir\$BinaryName.exe" .

    # Verify the build
    Verify-Binary "$outDir\$BinaryName.exe" "windows" "amd64"

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-windows.bat" $outDir
    Copy-Item "scripts\uninstall-windows.bat" $outDir
    Copy-Item "config.yaml" $outDir

    # Copy certificates if they exist
    if (Test-Path "certs") {
        Write-Host "Copying certificates..." -ForegroundColor Gray
        Copy-Item -Recurse "certs" "$outDir\certs"
        Write-Host "Certificates included" -ForegroundColor Green
    } else {
        Write-Host "WARNING: No certificates found - package will need certs generated" -ForegroundColor Yellow
    }
}

function Build-Linux {
    Write-Host "Building for Linux amd64..." -ForegroundColor Green
    $env:GOOS = "linux"
    $env:GOARCH = "amd64"
    $env:CGO_ENABLED = "0"

    $outDir = "$BuildDir\linux"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    Write-Host "  Target: linux/amd64" -ForegroundColor Gray
    go build -v -ldflags "$LDFlags -s -w" -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-linux.sh" $outDir
    Copy-Item "scripts\uninstall-linux.sh" $outDir

    # Copy and modify config.yaml for Linux paths
    Write-Host "Preparing config.yaml for Linux..." -ForegroundColor Gray
    Copy-Item "config.yaml" "$outDir\config.yaml"

    # Read config content
    $configContent = Get-Content "$outDir\config.yaml" -Raw

    # Update paths for Linux deployment
    $configContent = $configContent -replace 'client_cert_path: "\./certs/client\.crt"', 'client_cert_path: "/etc/dcim-agent/certs/client.crt"'
    $configContent = $configContent -replace 'client_key_path: "\./certs/client\.key"', 'client_key_path: "/etc/dcim-agent/certs/client.key"'
    $configContent = $configContent -replace 'ca_cert_path: "\./certs/ca\.crt"', 'ca_cert_path: "/etc/dcim-agent/certs/ca.crt"'
    $configContent = $configContent -replace 'path: "\./agent\.db"', 'path: "/var/lib/dcim-agent/agent.db"'
    $configContent = $configContent -replace 'file: "\./agent\.log"', 'file: "/var/log/dcim-agent/agent.log"'

    # Write updated config
    Set-Content -Path "$outDir\config.yaml" -Value $configContent -NoNewline
    Write-Host "Config updated with Linux system paths" -ForegroundColor Green

    # Copy certificates if they exist
    if (Test-Path "certs") {
        Write-Host "Copying certificates..." -ForegroundColor Gray
        Copy-Item -Recurse "certs" "$outDir\certs"
        Write-Host "Certificates included" -ForegroundColor Green
    } else {
        Write-Host "WARNING: No certificates found - package will need certs generated" -ForegroundColor Yellow
    }
}

function Build-MacOS-AMD64 {
    Write-Host "Building for macOS amd64..." -ForegroundColor Green
    $env:GOOS = "darwin"
    $env:GOARCH = "amd64"
    $env:CGO_ENABLED = "0"

    $outDir = "$BuildDir\macos-amd64"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    Write-Host "  Target: darwin/amd64" -ForegroundColor Gray
    go build -v -ldflags "$LDFlags -s -w" -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-macos.sh" $outDir
    Copy-Item "scripts\uninstall-macos.sh" $outDir

    # Copy and modify config.yaml for macOS paths
    Write-Host "Preparing config.yaml for macOS..." -ForegroundColor Gray
    Copy-Item "config.yaml" "$outDir\config.yaml"

    # Read config content
    $configContent = Get-Content "$outDir\config.yaml" -Raw

    # Update paths for macOS deployment
    $configContent = $configContent -replace 'client_cert_path: "\./certs/client\.crt"', 'client_cert_path: "/etc/dcim-agent/certs/client.crt"'
    $configContent = $configContent -replace 'client_key_path: "\./certs/client\.key"', 'client_key_path: "/etc/dcim-agent/certs/client.key"'
    $configContent = $configContent -replace 'ca_cert_path: "\./certs/ca\.crt"', 'ca_cert_path: "/etc/dcim-agent/certs/ca.crt"'
    $configContent = $configContent -replace 'path: "\./agent\.db"', 'path: "/var/lib/dcim-agent/agent.db"'
    $configContent = $configContent -replace 'file: "\./agent\.log"', 'file: "/var/log/dcim-agent/agent.log"'

    # Write updated config
    Set-Content -Path "$outDir\config.yaml" -Value $configContent -NoNewline
    Write-Host "Config updated with macOS system paths" -ForegroundColor Green

    # Copy certificates if they exist
    if (Test-Path "certs") {
        Write-Host "Copying certificates..." -ForegroundColor Gray
        Copy-Item -Recurse "certs" "$outDir\certs"
        Write-Host "Certificates included" -ForegroundColor Green
    } else {
        Write-Host "WARNING: No certificates found - package will need certs generated" -ForegroundColor Yellow
    }
}

function Build-MacOS-ARM64 {
    Write-Host "Building for macOS arm64 Apple Silicon..." -ForegroundColor Green
    $env:GOOS = "darwin"
    $env:GOARCH = "arm64"
    $env:CGO_ENABLED = "0"

    $outDir = "$BuildDir\macos-arm64"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    Write-Host "  Target: darwin/arm64" -ForegroundColor Gray
    go build -v -ldflags "$LDFlags -s -w" -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-macos.sh" $outDir
    Copy-Item "scripts\uninstall-macos.sh" $outDir

    # Copy and modify config.yaml for macOS paths
    Write-Host "Preparing config.yaml for macOS..." -ForegroundColor Gray
    Copy-Item "config.yaml" "$outDir\config.yaml"

    # Read config content
    $configContent = Get-Content "$outDir\config.yaml" -Raw

    # Update paths for macOS deployment
    $configContent = $configContent -replace 'client_cert_path: "\./certs/client\.crt"', 'client_cert_path: "/etc/dcim-agent/certs/client.crt"'
    $configContent = $configContent -replace 'client_key_path: "\./certs/client\.key"', 'client_key_path: "/etc/dcim-agent/certs/client.key"'
    $configContent = $configContent -replace 'ca_cert_path: "\./certs/ca\.crt"', 'ca_cert_path: "/etc/dcim-agent/certs/ca.crt"'
    $configContent = $configContent -replace 'path: "\./agent\.db"', 'path: "/var/lib/dcim-agent/agent.db"'
    $configContent = $configContent -replace 'file: "\./agent\.log"', 'file: "/var/log/dcim-agent/agent.log"'

    # Write updated config
    Set-Content -Path "$outDir\config.yaml" -Value $configContent -NoNewline
    Write-Host "Config updated with macOS system paths" -ForegroundColor Green

    # Copy certificates if they exist
    if (Test-Path "certs") {
        Write-Host "Copying certificates..." -ForegroundColor Gray
        Copy-Item -Recurse "certs" "$outDir\certs"
        Write-Host "Certificates included" -ForegroundColor Green
    } else {
        Write-Host "WARNING: No certificates found - package will need certs generated" -ForegroundColor Yellow
    }
}

function Build-All {
    Build-Windows
    Build-Linux
    Build-MacOS-AMD64
    Build-MacOS-ARM64
}

function Create-Dist {
    Write-Host "Creating distribution packages..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

    # Windows
    Write-Host "Packaging Windows..." -ForegroundColor Gray
    Compress-Archive -Path "$BuildDir\windows\*" -DestinationPath "$DistDir\dcim-agent-windows-amd64-$Version.zip" -Force

    # Note: tar is available on Windows 10+ for creating .tar.gz
    # Linux
    Write-Host "Packaging Linux..." -ForegroundColor Gray
    tar -czf "$DistDir\dcim-agent-linux-amd64-$Version.tar.gz" -C "$BuildDir\linux" .

    # macOS (amd64)
    Write-Host "Packaging macOS amd64..." -ForegroundColor Gray
    tar -czf "$DistDir\dcim-agent-macos-amd64-$Version.tar.gz" -C "$BuildDir\macos-amd64" .

    # macOS (arm64)
    Write-Host "Packaging macOS arm64..." -ForegroundColor Gray
    tar -czf "$DistDir\dcim-agent-macos-arm64-$Version.tar.gz" -C "$BuildDir\macos-arm64" .

    Write-Host "Distribution packages created in $DistDir/" -ForegroundColor Green
}

function Run-Tests {
    Write-Host "Running tests..." -ForegroundColor Cyan
    go test -v -race -coverprofile=coverage.out ./...
}

# Main execution
Write-Host "DCIM Agent - Build Script" -ForegroundColor Yellow
Write-Host "Version: $Version" -ForegroundColor Yellow
Write-Host ""

switch ($Target) {
    "clean" {
        Clean
    }
    "deps" {
        InstallDeps
    }
    "windows" {
        Build-Windows
    }
    "linux" {
        Build-Linux
    }
    "macos-amd64" {
        Build-MacOS-AMD64
    }
    "macos-arm64" {
        Build-MacOS-ARM64
    }
    "all" {
        Clean
        InstallDeps
        Build-All
    }
    "dist" {
        Clean
        InstallDeps
        Build-All
        Create-Dist
    }
    "test" {
        Run-Tests
    }
    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        Write-Host ""
        Write-Host "Available targets:" -ForegroundColor Yellow
        Write-Host "  clean        - Remove build artifacts"
        Write-Host "  deps         - Install dependencies"
        Write-Host "  windows      - Build for Windows"
        Write-Host "  linux        - Build for Linux"
        Write-Host "  macos-amd64  - Build for macOS Intel"
        Write-Host "  macos-arm64  - Build for macOS Apple Silicon"
        Write-Host "  all          - Build for all platforms - default"
        Write-Host "  dist         - Create distribution packages"
        Write-Host "  test         - Run tests"
        Write-Host ""
        Write-Host 'Usage: .\build.ps1 -Target <target> -Version <version>'
        exit 1
    }
}

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green

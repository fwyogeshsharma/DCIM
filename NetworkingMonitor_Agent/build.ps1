# Network Monitor Agent - PowerShell Build Script
# Cross-platform build script for Windows users

param(
    [string]$Version = "1.0.0",
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"

$BinaryName = "network-monitor-agent"
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

function Build-Windows {
    Write-Host "Building for Windows amd64..." -ForegroundColor Green
    $env:GOOS = "windows"
    $env:GOARCH = "amd64"

    $outDir = "$BuildDir\windows"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    go build -ldflags $LDFlags -o "$outDir\$BinaryName.exe" .

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

    $outDir = "$BuildDir\linux"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    go build -ldflags $LDFlags -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-linux.sh" $outDir
    Copy-Item "scripts\uninstall-linux.sh" $outDir
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

function Build-MacOS-AMD64 {
    Write-Host "Building for macOS amd64..." -ForegroundColor Green
    $env:GOOS = "darwin"
    $env:GOARCH = "amd64"

    $outDir = "$BuildDir\macos-amd64"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    go build -ldflags $LDFlags -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-macos.sh" $outDir
    Copy-Item "scripts\uninstall-macos.sh" $outDir
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

function Build-MacOS-ARM64 {
    Write-Host "Building for macOS arm64 Apple Silicon..." -ForegroundColor Green
    $env:GOOS = "darwin"
    $env:GOARCH = "arm64"

    $outDir = "$BuildDir\macos-arm64"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    go build -ldflags $LDFlags -o "$outDir\$BinaryName" .

    Write-Host "Copying installer scripts..." -ForegroundColor Gray
    Copy-Item "scripts\install-macos.sh" $outDir
    Copy-Item "scripts\uninstall-macos.sh" $outDir
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
    Compress-Archive -Path "$BuildDir\windows\*" -DestinationPath "$DistDir\network-monitor-agent-windows-amd64-$Version.zip" -Force

    # Note: tar is available on Windows 10+ for creating .tar.gz
    # Linux
    Write-Host "Packaging Linux..." -ForegroundColor Gray
    tar -czf "$DistDir\network-monitor-agent-linux-amd64-$Version.tar.gz" -C "$BuildDir\linux" .

    # macOS (amd64)
    Write-Host "Packaging macOS amd64..." -ForegroundColor Gray
    tar -czf "$DistDir\network-monitor-agent-macos-amd64-$Version.tar.gz" -C "$BuildDir\macos-amd64" .

    # macOS (arm64)
    Write-Host "Packaging macOS arm64..." -ForegroundColor Gray
    tar -czf "$DistDir\network-monitor-agent-macos-arm64-$Version.tar.gz" -C "$BuildDir\macos-arm64" .

    Write-Host "Distribution packages created in $DistDir/" -ForegroundColor Green
}

function Run-Tests {
    Write-Host "Running tests..." -ForegroundColor Cyan
    go test -v -race -coverprofile=coverage.out ./...
}

# Main execution
Write-Host "Network Monitor Agent - Build Script" -ForegroundColor Yellow
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
        Write-Host "Usage: .\build.ps1 -Target <target> -Version <version>"
        exit 1
    }
}

Write-Host ""
Write-Host "Build complete!" -ForegroundColor Green

#!/bin/bash
# DCIM Server Build Script
# Builds the server for Windows, Linux, and macOS
#
# Default: Builds for ALL platforms (Windows, Linux, macOS)
# Usage:
#   ./build.sh                      # Build all platforms
#   ./build.sh -p windows           # Build Windows only
#   ./build.sh -p linux             # Build Linux only
#   ./build.sh -p macos             # Build macOS only

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

# Default values
PLATFORM="all"
OUTPUT_DIR="build"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--platform)
            PLATFORM="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -p, --platform PLATFORM  Platform to build (windows, linux, macos, all)"
            echo "  -o, --output DIR         Output directory (default: build)"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}================================"
echo "DCIM Server - Build Script"
echo -e "================================${NC}"
echo ""

# Check if Go is installed
if ! command -v go &> /dev/null; then
    echo -e "${RED}ERROR: Go is not installed!${NC}"
    echo ""
    echo -e "${YELLOW}Please install Go:${NC}"
    echo "  Linux:   sudo apt-get install golang  (or download from golang.org)"
    echo "  macOS:   brew install go"
    echo "  Windows: Download from https://golang.org/dl/"
    echo ""
    exit 1
fi

GO_VERSION=$(go version)
echo -e "${GREEN}[OK] Go found: $GO_VERSION${NC}"
echo ""

if [ "$PLATFORM" = "all" ]; then
    echo -e "${CYAN}Building for ALL platforms (Windows, Linux, macOS)${NC}"
    echo ""
else
    echo -e "${CYAN}Building for platform: $PLATFORM${NC}"
    echo ""
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

VERSION="1.0.0"
BUILD_TIME=$(date '+%Y-%m-%d %H:%M:%S')
LDFLAGS="-s -w -X main.Version=$VERSION -X 'main.BuildTime=$BUILD_TIME'"

build_platform() {
    local os=$1
    local arch=$2
    local ext=$3

    local platform_dir="$OUTPUT_DIR/$os-$arch"
    local binary="dcim-server$ext"

    echo -e "${YELLOW}Building for $os/$arch...${NC}"

    # Create platform directory
    mkdir -p "$platform_dir"

    # Determine if this is a native build
    local is_native=false
    local current_os=$(uname -s | tr '[:upper:]' '[:lower:]')

    case "$current_os" in
        linux*)
            [ "$os" = "linux" ] && is_native=true
            ;;
        darwin*)
            [ "$os" = "darwin" ] && is_native=true
            ;;
        mingw*|msys*|cygwin*)
            [ "$os" = "windows" ] && is_native=true
            ;;
    esac

    # Set environment variables
    export GOOS=$os
    export GOARCH=$arch

    # Enable CGO for native builds (better SQLite performance)
    # Disable for cross-compilation
    if [ "$is_native" = true ] || [ "$os" = "windows" ]; then
        export CGO_ENABLED=1
        echo -e "  ${GRAY}CGO: Enabled (optimized SQLite driver)${NC}"
    else
        export CGO_ENABLED=0
        echo -e "  ${GRAY}CGO: Disabled (using pure-Go SQLite driver)${NC}"
    fi

    # Build command
    local output_path="$platform_dir/$binary"

    if go build -ldflags "$LDFLAGS" -o "$output_path" .; then
        echo -e "  ${GREEN}[OK] Built $binary${NC}"

        # Copy config file
        cp config.yaml "$platform_dir/config.yaml"

        # Copy license file if it exists
        if [ -f "license.json" ]; then
            cp license.json "$platform_dir/license.json"
            echo -e "  ${GREEN}[OK] Copied license.json${NC}"
        else
            echo -e "  ${YELLOW}[INFO] license.json not found - you'll need to generate it${NC}"
        fi

        # Copy certificates if they exist
        if [ -d "certs" ]; then
            cp -r certs "$platform_dir/certs"
            echo -e "  ${GREEN}[OK] Copied certificates${NC}"
        else
            echo -e "  ${YELLOW}[WARNING] No certificates found${NC}"
            echo -e "  ${GRAY}Generate with: ./scripts/generate-certs.sh${NC}"
        fi

        echo -e "  ${GREEN}[OK] Build complete: $platform_dir/$binary${NC}"
    else
        echo -e "  ${RED}[FAILED] Build failed for $os/$arch${NC}"
        exit 1
    fi

    echo ""
}

# Build based on platform selection
case "$PLATFORM" in
    windows)
        build_platform "windows" "amd64" ".exe"
        ;;
    linux)
        build_platform "linux" "amd64" ""
        ;;
    macos)
        build_platform "darwin" "amd64" ""
        build_platform "darwin" "arm64" ""
        ;;
    all)
        build_platform "windows" "amd64" ".exe"
        build_platform "linux" "amd64" ""
        build_platform "darwin" "amd64" ""
        build_platform "darwin" "arm64" ""
        ;;
    *)
        echo -e "${RED}Unknown platform: $PLATFORM${NC}"
        echo "Valid platforms: windows, linux, macos, all"
        exit 1
        ;;
esac

echo -e "${GREEN}================================"
echo "Build Complete!"
echo -e "================================${NC}"
echo ""

echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo "1. Review the config.yaml in each build directory"
echo "2. Ensure certificates are generated (./scripts/generate-certs.sh)"
echo "3. Setup database if using PostgreSQL (./scripts/setup-postgres.sh)"
echo "4. Run the server:"
echo ""

if [ "$PLATFORM" = "windows" ] || [ "$PLATFORM" = "all" ]; then
    echo -e "   ${CYAN}Windows:${NC}"
    echo "   .\\build\\windows-amd64\\dcim-server.exe"
    echo ""
fi

if [ "$PLATFORM" = "linux" ] || [ "$PLATFORM" = "all" ]; then
    echo -e "   ${CYAN}Linux:${NC}"
    echo "   ./build/linux-amd64/dcim-server"
    echo ""
fi

if [ "$PLATFORM" = "macos" ] || [ "$PLATFORM" = "all" ]; then
    echo -e "   ${CYAN}macOS (Intel):${NC}"
    echo "   ./build/darwin-amd64/dcim-server"
    echo ""
    echo -e "   ${CYAN}macOS (Apple Silicon):${NC}"
    echo "   ./build/darwin-arm64/dcim-server"
    echo ""
fi

echo -e "${YELLOW}Documentation:${NC}"
echo "  - Build and Run Guide: ../BUILD_AND_RUN.md"
echo "  - Scripts Guide: ./scripts/SCRIPTS_README.md"
echo ""

# Network Monitor Agent - Makefile
# Cross-platform build and packaging

BINARY_NAME=dicm-agent
VERSION=1.0.0
BUILD_DIR=dist
PACKAGE_NAME=$(BINARY_NAME)-v$(VERSION)

# Go build flags
LDFLAGS=-ldflags "-s -w -X main.version=$(VERSION)"
CGO_ENABLED=0

# Detect OS
ifeq ($(OS),Windows_NT)
	BINARY_EXT=.exe
	PACKAGE_EXT=.zip
	RM=rmdir /s /q
	MKDIR=mkdir
	CP=xcopy /s /y
else
	BINARY_EXT=
	PACKAGE_EXT=.tar.gz
	RM=rm -rf
	MKDIR=mkdir -p
	CP=cp -r
endif

.PHONY: all build clean package test install help

all: clean build

help:
	@echo "Network Monitor Agent - Build Commands"
	@echo ""
	@echo "  make build          - Build agent binary only"
	@echo "  make package        - Build and create deployable package"
	@echo "  make clean          - Remove build artifacts"
	@echo "  make test           - Run tests"
	@echo "  make install        - Install agent to system"
	@echo ""
	@echo "Cross-compilation:"
	@echo "  make build-windows  - Build for Windows"
	@echo "  make build-linux    - Build for Linux"
	@echo "  make build-macos    - Build for macOS"
	@echo "  make build-all      - Build for all platforms"
	@echo ""

# Build agent for current platform
build:
	@echo "Building $(BINARY_NAME)..."
	CGO_ENABLED=$(CGO_ENABLED) go build $(LDFLAGS) -o $(BINARY_NAME)$(BINARY_EXT) ./cmd/agent
	@echo "Build complete: $(BINARY_NAME)$(BINARY_EXT)"

# Build for Windows
build-windows:
	@echo "Building for Windows..."
	GOOS=windows GOARCH=amd64 CGO_ENABLED=$(CGO_ENABLED) go build $(LDFLAGS) -o $(BUILD_DIR)/windows/$(BINARY_NAME).exe ./cmd/agent
	@echo "Windows build complete"

# Build for Linux
build-linux:
	@echo "Building for Linux..."
	GOOS=linux GOARCH=amd64 CGO_ENABLED=$(CGO_ENABLED) go build $(LDFLAGS) -o $(BUILD_DIR)/linux/$(BINARY_NAME) ./cmd/agent
	@echo "Linux build complete"

# Build for macOS
build-macos:
	@echo "Building for macOS..."
	GOOS=darwin GOARCH=amd64 CGO_ENABLED=$(CGO_ENABLED) go build $(LDFLAGS) -o $(BUILD_DIR)/macos/$(BINARY_NAME) ./cmd/agent
	@echo "macOS build complete"

# Build for all platforms
build-all: build-windows build-linux build-macos
	@echo "All platform builds complete"

# Create deployable package with certificates and config
package: build
	@echo "Creating deployment package..."
	$(MKDIR) $(BUILD_DIR)/$(PACKAGE_NAME)
	$(MKDIR) $(BUILD_DIR)/$(PACKAGE_NAME)/certs
	$(MKDIR) $(BUILD_DIR)/$(PACKAGE_NAME)/logs
	$(MKDIR) $(BUILD_DIR)/$(PACKAGE_NAME)/scripts

	# Copy binary
	$(CP) $(BINARY_NAME)$(BINARY_EXT) $(BUILD_DIR)/$(PACKAGE_NAME)/

	# Copy configuration
	$(CP) config.yaml $(BUILD_DIR)/$(PACKAGE_NAME)/

	# Copy certificates if they exist
	@if [ -d "certs" ]; then \
		$(CP) certs/* $(BUILD_DIR)/$(PACKAGE_NAME)/certs/; \
		echo "Certificates copied"; \
	else \
		echo "WARNING: No certificates found. Generate them with: cd scripts && ./generate-certs.sh"; \
	fi

	# Copy scripts
	$(CP) scripts/generate-certs.* $(BUILD_DIR)/$(PACKAGE_NAME)/scripts/

	# Copy documentation
	@for doc in README.md MTLS_*.md *.txt; do \
		if [ -f "$$doc" ]; then $(CP) $$doc $(BUILD_DIR)/$(PACKAGE_NAME)/; fi; \
	done

	@echo "Package created in $(BUILD_DIR)/$(PACKAGE_NAME)"
	@echo ""
	@echo "To deploy: Copy entire $(BUILD_DIR)/$(PACKAGE_NAME) folder to target machine"

# Run tests
test:
	@echo "Running tests..."
	go test -v ./...

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	$(RM) $(BINARY_NAME)$(BINARY_EXT) $(BUILD_DIR)
	@echo "Clean complete"

# Install agent (requires privileges)
install: build
	@echo "Installing $(BINARY_NAME)..."
ifeq ($(OS),Windows_NT)
	$(BINARY_NAME).exe install
	$(BINARY_NAME).exe start
else
	sudo cp $(BINARY_NAME) /usr/local/bin/
	sudo chmod +x /usr/local/bin/$(BINARY_NAME)
endif
	@echo "Installation complete"

DCIM Server - darwin/arm64 Build

Version: 1.0.0
Built: 2026-02-06 13:26:50

Files Included:
- dcim-server (server executable)
- config.yaml (configuration template)
- license.json (Included)
- certs/ (Included (all required files))

Quick Start:
1. Edit config.yaml to configure the server

2. Certificates (Required for mTLS):
   [OK] All certificates are included:
   - certs/ca.crt (CA certificate)
   - certs/server.crt (server certificate)
   - certs/server.key (server private key)
3. License (Required if enforcement enabled):
   [OK] License file is included: license.json
4. Run server:
   .\dcim-server -config config.yaml

For detailed documentation, see README.md and BUILD_AND_RUN.md in the project root.

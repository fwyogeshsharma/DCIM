DCIM Server - darwin/amd64 Build

Version: 1.0.0
Built: 2026-02-13 16:48:40
Server: JPR_MP_SERVER_WIN_HP_AS_01

Files Included:
- dcim-server (server executable)
- config.yaml (configuration template)
- cooling_config.yaml (Included)
- migrations/ (Included (3 migration files))
- license.json (Included)
- certs/ (Included for server: JPR_MP_SERVER_WIN_HP_AS_01)

Quick Start:
1. Edit config.yaml to configure the server
   Edit cooling_config.yaml to configure cooling system thresholds

2. Certificates (Required for mTLS):
   [OK] Certificates are included for server: JPR_MP_SERVER_WIN_HP_AS_01
   - certs/ca.crt (CA certificate)
   - certs/server.crt (Server certificate)
   - certs/server.key (Server private key)

   These certificates are ready to use on the target server.
3. License (Required if enforcement enabled):
   [OK] License file is included: license.json
4. Run server:
   .\dcim-server -config config.yaml

For detailed documentation, see README.md and BUILD_AND_RUN.md in the project root.

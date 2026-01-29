# Release Checklist - Network Monitor Agent

Print this and check off items for each release.

---

## Pre-Build Configuration

- [ ] Open `config.yaml`
- [ ] Change `server.url` to production server (not localhost)
- [ ] Verify URL uses HTTPS (not HTTP)
- [ ] Adjust alert thresholds if needed
- [ ] Set log level to "info" (not "debug")
- [ ] Save changes

---

## Build Process

- [ ] Open PowerShell in project directory
- [ ] Run: `go mod tidy`
- [ ] Run: `.\build.ps1 -Target dist -Version "X.Y.Z"`
- [ ] Wait for completion (takes 2-5 minutes)
- [ ] Check `dist\` folder for packages:
  - [ ] `network-monitor-agent-windows-amd64-X.Y.Z.zip`
  - [ ] `network-monitor-agent-linux-amd64-X.Y.Z.tar.gz`
  - [ ] `network-monitor-agent-macos-amd64-X.Y.Z.tar.gz`
  - [ ] `network-monitor-agent-macos-arm64-X.Y.Z.tar.gz`

---

## Testing (Windows Package)

- [ ] Extract Windows package to test folder
- [ ] Verify files present:
  - [ ] network-monitor-agent.exe (15-25 MB)
  - [ ] config.yaml
  - [ ] install-windows.bat
  - [ ] uninstall-windows.bat
- [ ] Open config.yaml and verify server URL is correct
- [ ] Right-click `install-windows.bat` → "Run as administrator"
- [ ] Wait for "Installation Complete!"
- [ ] Open Services (Win+R, services.msc)
- [ ] Find "Network Monitor Agent" - should be "Running"
- [ ] Check log file: `C:\Program Files\NetworkMonitorAgent\agent.log`
- [ ] Verify metrics reaching server (check server logs)
- [ ] Run uninstaller to clean up test

---

## Package Verification

For each platform package, verify:

### Windows Package
- [ ] Extracts without errors
- [ ] Contains all 4 files
- [ ] Config has production server URL
- [ ] README.txt is included (optional)
- [ ] Total package size: ~15-25 MB

### Linux Package
- [ ] Extracts without errors
- [ ] Contains all 4 files
- [ ] Scripts have execute permission
- [ ] Config has production server URL

### macOS Packages (both)
- [ ] Extracts without errors
- [ ] Contains all 4 files
- [ ] Scripts have execute permission
- [ ] Config has production server URL

---

## Documentation

- [ ] Update version number in README.md (optional)
- [ ] Document any configuration changes
- [ ] Update CHANGELOG.md (if you maintain one)
- [ ] Prepare release notes for clients

---

## Distribution

- [ ] Upload packages to file server / cloud storage
- [ ] Generate download links
- [ ] Test download links work
- [ ] Prepare client email with:
  - [ ] Download links
  - [ ] Installation instructions
  - [ ] Support contact
  - [ ] Expected server URL
- [ ] Send to clients
- [ ] Notify support team of release

---

## Post-Release

- [ ] Monitor server for incoming data from new installs
- [ ] Check for any client support requests
- [ ] Document any issues encountered
- [ ] Update this checklist if needed

---

## Quick Commands

```powershell
# Configure
notepad config.yaml
# Change server.url to production

# Build all platforms
.\build.ps1 -Target dist -Version "1.0.0"

# Test Windows package
cd dist
Expand-Archive network-monitor-agent-windows-amd64-1.0.0.zip -DestinationPath test
cd test
# Right-click install-windows.bat → Run as administrator

# Upload to server
# (Your file upload command here)
```

---

## Version History

| Date | Version | Tester | Notes |
|------|---------|--------|-------|
| | 1.0.0 | | Initial release |
| | | | |
| | | | |

---

## Common Issues During Release

**Build fails:**
- Run `go mod tidy` first
- Check Go is installed: `go version`

**Package too small (<5 MB):**
- Build failed, check error messages
- Rebuild with: `go build -v`

**Service won't start on test:**
- Check config.yaml is valid YAML
- Check server URL is reachable
- Check Windows Event Viewer

**Clients report connection errors:**
- Verify server URL in distributed config
- Check firewall rules
- Test with `curl` from client machine

---

**Release Manager:** _________________

**Date:** _________________

**Version:** _________________

**Status:** [ ] Complete  [ ] Issues Found  [ ] Aborted

# Network Monitor Agent - Installation Instructions

**Simple guide for clients who don't have technical background**

---

## What is this?

The Network Monitor Agent is a small program that:
- Monitors your computer's health (CPU, memory, disk space)
- Sends data to our monitoring server
- Alerts us if something goes wrong
- Runs quietly in the background

**You don't need to do anything after installation - it runs automatically!**

---

## Before You Start

**Requirements:**
- ✅ Administrator access on your computer
- ✅ Internet connection
- ✅ 5 minutes of time

**No special software needed** - the agent works on its own!

---

## Windows Installation

### Step 1: Extract Files

1. Find the downloaded file: `network-monitor-agent-windows-amd64-X.X.X.zip`
2. Right-click it → "Extract All"
3. Choose a location (Desktop is fine)
4. Click "Extract"

### Step 2: Install

1. Open the extracted folder
2. Find `install-windows.bat`
3. **Right-click** on it
4. Select **"Run as administrator"**
5. Click "Yes" when Windows asks for permission
6. Wait for the message "Installation Complete!"
7. Press any key to close the window

### Step 3: Verify (Optional)

1. Press `Windows key + R`
2. Type: `services.msc`
3. Press Enter
4. Look for "Network Monitor Agent"
5. Status should show "Running"

**That's it! The agent is now running.**

---

## Linux Installation

### Step 1: Extract Files

```bash
# Download the file and extract it
tar -xzf network-monitor-agent-linux-amd64-X.X.X.tar.gz

# Go into the folder
cd network-monitor-agent-linux-amd64
```

### Step 2: Install

```bash
# Run the installer (requires sudo/root)
sudo ./install-linux.sh
```

Wait for "Installation Complete!" message.

### Step 3: Verify (Optional)

```bash
# Check if it's running
systemctl status network-monitor-agent

# Should show "active (running)"
```

**That's it! The agent is now running.**

---

## macOS Installation

### Step 1: Extract Files

```bash
# Download the file and extract it
tar -xzf network-monitor-agent-macos-*.tar.gz

# Go into the folder
cd network-monitor-agent-macos-*
```

### Step 2: Install

```bash
# Run the installer (requires sudo)
sudo ./install-macos.sh
```

Enter your password when prompted.
Wait for "Installation Complete!" message.

### Step 3: Verify (Optional)

```bash
# Check if it's running
sudo launchctl list | grep network-monitor-agent

# Should show the agent in the list
```

**That's it! The agent is now running.**

---

## What Happens After Installation?

**Automatic Operation:**
- ✅ Starts automatically when computer boots
- ✅ Runs in the background (you won't see it)
- ✅ Collects system metrics every minute
- ✅ Sends data to monitoring server every 5 minutes
- ✅ Alerts our team if problems are detected

**You don't need to do anything!**

---

## Where is the Agent?

**Windows:**
- Program: `C:\Program Files\NetworkMonitorAgent\`
- Logs: `C:\Program Files\NetworkMonitorAgent\agent.log`

**Linux:**
- Program: `/opt/network-monitor-agent/`
- Logs: `/var/log/network-monitor-agent/agent.log`

**macOS:**
- Program: `/usr/local/opt/network-monitor-agent/`
- Logs: `/usr/local/var/log/network-monitor-agent/agent.log`

---

## Frequently Asked Questions

### Does this slow down my computer?

No. The agent uses less than 1% of CPU and about 50 MB of memory. You won't notice it running.

### Does it use a lot of internet bandwidth?

No. It sends about 10-50 KB per minute - less than loading a simple webpage.

### Can I see what it's doing?

Yes! Check the log file (see locations above). It shows what data is being collected and sent.

### What if my internet goes down?

The agent keeps working and stores data locally. When internet returns, it automatically sends everything that was missed.

### Does it collect personal information?

No. It only collects:
- CPU usage percentage
- Memory usage percentage
- Disk space usage
- Network traffic statistics
- System temperature (if available)

It does NOT collect:
- ❌ Personal files
- ❌ Passwords
- ❌ Browsing history
- ❌ Emails or documents
- ❌ Any personal data

### How do I stop it?

**Windows:**
1. Open Services (`services.msc`)
2. Find "Network Monitor Agent"
3. Right-click → Stop

**Linux:**
```bash
sudo systemctl stop network-monitor-agent
```

**macOS:**
```bash
sudo launchctl unload /Library/LaunchDaemons/com.faber.network-monitor-agent.plist
```

### How do I start it again?

**Windows:**
1. Open Services (`services.msc`)
2. Find "Network Monitor Agent"
3. Right-click → Start

**Linux:**
```bash
sudo systemctl start network-monitor-agent
```

**macOS:**
```bash
sudo launchctl load /Library/LaunchDaemons/com.faber.network-monitor-agent.plist
```

---

## Uninstalling

If you need to remove the agent:

**Windows:**
1. Go to: `C:\Program Files\NetworkMonitorAgent`
2. Right-click `uninstall-windows.bat`
3. Select "Run as administrator"
4. Choose whether to keep data or remove everything

**Linux:**
```bash
sudo /opt/network-monitor-agent/uninstall-linux.sh
```

**macOS:**
```bash
sudo /usr/local/opt/network-monitor-agent/uninstall-macos.sh
```

---

## Troubleshooting

### Installation failed

**Try these steps:**
1. Make sure you ran the installer as Administrator/sudo
2. Check that you have internet connection
3. Try running the installer again
4. Contact support (see below)

### Service not starting

**Windows:**
1. Check Windows Event Viewer for errors
2. Try running manually:
   ```
   cd "C:\Program Files\NetworkMonitorAgent"
   network-monitor-agent.exe -config config.yaml
   ```
3. Check if any error appears

**Linux/macOS:**
```bash
# Check logs for errors
journalctl -u network-monitor-agent -n 50    # Linux
tail -50 /usr/local/var/log/network-monitor-agent/agent.log    # macOS
```

### Not sending data to server

**Check these:**
1. Is your internet connection working?
2. Can you reach the server URL? (ask support for URL)
3. Is a firewall blocking outbound connections?
4. Check the log file for error messages

---

## Getting Help

If you have any problems:

**Email:** support@yourcompany.com
**Phone:** +1-XXX-XXX-XXXX
**Hours:** Monday-Friday, 9 AM - 5 PM

**When contacting support, please provide:**
- Your operating system (Windows 10, Ubuntu 20.04, etc.)
- The error message (if any)
- The log file (see locations above)

---

## Technical Details (Optional)

**For IT staff who want more information:**

- **Language:** Go (compiled binary, no runtime needed)
- **Database:** SQLite (embedded, no external DB)
- **Protocol:** HTTPS
- **Ports:** Outbound HTTPS (443) only
- **Security:** TLS 1.2+, no listening ports
- **Resource Usage:** <1% CPU, ~50 MB RAM
- **Disk Usage:** ~10 MB/day (auto-cleaned after 30 days)

**Configuration file:** `config.yaml` in installation directory
**Service management:** Standard OS tools (services.msc, systemctl, launchctl)

For detailed technical documentation, see:
- README.md - Complete documentation
- ARCHITECTURE.md - System design
- API_SPECIFICATION.md - Server API details

---

## Privacy & Security

**Data collected:**
- System metrics (CPU, memory, disk, network)
- Host information (hostname, OS version)
- Alert events

**Data NOT collected:**
- Personal files or documents
- Passwords or credentials
- User activity or behavior
- Application data

**Data transmission:**
- Encrypted with HTTPS/TLS
- Sent to company monitoring server only
- Not shared with third parties

**Local storage:**
- Data stored in encrypted database
- Automatic cleanup after 30 days
- Accessible only by system administrator

---

## Summary

**What you did:**
- ✅ Extracted files
- ✅ Ran installer as administrator
- ✅ Agent is now running

**What happens now:**
- ✅ Agent runs automatically in background
- ✅ Monitors system health
- ✅ Sends data to our monitoring team
- ✅ You don't need to do anything else!

**If you have questions or problems:**
- Check the troubleshooting section above
- Contact support: support@yourcompany.com

Thank you for installing the Network Monitor Agent!

---

**Version:** 1.0.0
**Last Updated:** 2026-01-20
**Support:** support@yourcompany.com

# Enable Full SNMP Monitoring on Ubuntu

Your Ubuntu machines (192.168.1.14 and 192.168.1.15) currently have **basic SNMP** enabled, which only provides:
- System uptime
- Number of users
- Number of processes

To get **meaningful system health metrics** like CPU, memory, disk, and temperature, you need to configure SNMP with extended MIBs.

---

## What You'll Get After Setup

✅ **CPU Usage** - Real-time CPU utilization percentages
✅ **Memory Usage** - RAM usage, available memory, buffers/cache
✅ **Disk Usage** - Disk space used/free for each partition
✅ **Load Average** - System load (1min, 5min, 15min)
✅ **Network Traffic** - Bytes in/out for network interfaces
✅ **Temperature** (if available) - CPU/system temperature sensors

---

## Setup Instructions

Run these commands on **both Ubuntu machines** (192.168.1.14 and 192.168.1.15):

### Step 1: Install SNMP Daemon with Full MIBs

```bash
# Install snmpd and additional tools
sudo apt update
sudo apt install -y snmpd snmp snmp-mibs-downloader

# Enable non-free MIBs (removes "mibs :" restriction)
sudo sed -i 's/^mibs :/#mibs :/g' /etc/snmp/snmp.conf
```

### Step 2: Configure SNMP Daemon

Edit the SNMP daemon configuration:

```bash
sudo nano /etc/snmp/snmpd.conf
```

**Replace the entire file with this configuration:**

```conf
# SNMP Daemon Configuration for System Monitoring

# Listen on all interfaces
agentaddress  udp:161

# Community string (change 'public' to something more secure in production)
rocommunity public

# System information
sysLocation    "Ubuntu Laptop"
sysContact     me@example.org

# Disk monitoring - Monitor root partition
disk / 10%

# Load monitoring thresholds
load 12 10 5

# Process monitoring (optional)
proc sshd

# Enable extended system information
extend uptime /bin/uptime
extend hostname /bin/hostname

# CPU/Memory/Disk/Network statistics
includeAllDisks 10%
```

### Step 3: Restart SNMP Daemon

```bash
sudo systemctl restart snmpd
sudo systemctl enable snmpd
sudo systemctl status snmpd
```

### Step 4: Verify Configuration

Test that SNMP is working with extended MIBs:

```bash
# Test from the Ubuntu machine itself
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021

# Test CPU idle percentage
snmpget -v2c -c public localhost 1.3.6.1.4.1.2021.11.11.0

# Test memory
snmpget -v2c -c public localhost 1.3.6.1.4.1.2021.4.11.0

# Test disk usage
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.9
```

If these commands return values (not "No Such Object"), the setup is successful!

---

## Updated Agent Configuration

After configuring SNMP on both Ubuntu machines, update your `config.yaml`:

```yaml
snmp_manager:
  enabled: true
  poll_interval: 60s
  devices:
    # HP 630 Notebook - Ubuntu Linux (with extended MIBs)
    - name: "hp-630-notebook"
      host: "192.168.1.14"
      port: 161
      community: "public"
      version: "2c"
      timeout: 5s
      retries: 3
      oids:
        # System uptime (basic)
        - oid: "1.3.6.1.2.1.1.3.0"
          name: "system_uptime"
          type: "counter"

        # CPU metrics (UCD-SNMP-MIB)
        - oid: "1.3.6.1.4.1.2021.11.9.0"      # CPU idle percentage
          name: "cpu_idle_pct"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.11.10.0"     # CPU user percentage
          name: "cpu_user_pct"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.11.11.0"     # CPU system percentage
          name: "cpu_system_pct"
          type: "gauge"

        # Memory metrics (UCD-SNMP-MIB)
        - oid: "1.3.6.1.4.1.2021.4.5.0"       # Total RAM (KB)
          name: "memory_total_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.6.0"       # Available RAM (KB)
          name: "memory_available_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.11.0"      # Total real memory (KB)
          name: "memory_real_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.14.0"      # Swap total (KB)
          name: "swap_total_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.15.0"      # Swap available (KB)
          name: "swap_available_kb"
          type: "gauge"

        # Load average (UCD-SNMP-MIB)
        - oid: "1.3.6.1.4.1.2021.10.1.3.1"    # Load average 1 min
          name: "load_avg_1min"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.10.1.3.2"    # Load average 5 min
          name: "load_avg_5min"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.10.1.3.3"    # Load average 15 min
          name: "load_avg_15min"
          type: "gauge"

        # Disk usage (UCD-SNMP-MIB) - Root partition
        - oid: "1.3.6.1.4.1.2021.9.1.6.1"     # Disk total (KB)
          name: "disk_root_total_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.9.1.7.1"     # Disk available (KB)
          name: "disk_root_available_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.9.1.9.1"     # Disk usage percentage
          name: "disk_root_usage_pct"
          type: "gauge"

    # HP Pavilion Notebook - Ubuntu Linux (with extended MIBs)
    - name: "hp-pavilion-notebook"
      host: "192.168.1.15"
      port: 161
      community: "public"
      version: "2c"
      timeout: 5s
      retries: 3
      oids:
        # Same OIDs as above
        - oid: "1.3.6.1.2.1.1.3.0"
          name: "system_uptime"
          type: "counter"
        - oid: "1.3.6.1.4.1.2021.11.9.0"
          name: "cpu_idle_pct"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.11.10.0"
          name: "cpu_user_pct"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.11.11.0"
          name: "cpu_system_pct"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.5.0"
          name: "memory_total_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.4.6.0"
          name: "memory_available_kb"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.10.1.3.1"
          name: "load_avg_1min"
          type: "gauge"
        - oid: "1.3.6.1.4.1.2021.9.1.9.1"
          name: "disk_root_usage_pct"
          type: "gauge"
```

---

## What the Metrics Mean

### CPU Metrics
- **cpu_idle_pct**: Percentage of CPU that's idle (higher = less load)
- **cpu_user_pct**: CPU used by user processes
- **cpu_system_pct**: CPU used by system/kernel

**Calculating CPU Usage:**
`CPU Usage % = 100 - cpu_idle_pct`

### Memory Metrics
- **memory_total_kb**: Total RAM installed
- **memory_available_kb**: RAM available for new processes
- **swap_available_kb**: Swap space available

**Calculating Memory Usage:**
`Memory Usage % = ((total - available) / total) * 100`

### Disk Metrics
- **disk_root_total_kb**: Total disk space on root partition
- **disk_root_available_kb**: Free disk space
- **disk_root_usage_pct**: Percentage of disk used

### Load Average
- **load_avg_1min**: Average system load over last 1 minute
- **load_avg_5min**: Average system load over last 5 minutes
- **load_avg_15min**: Average system load over last 15 minutes

**Understanding Load:**
- Load < CPU cores = System not overloaded
- Load = CPU cores = System fully utilized
- Load > CPU cores = System overloaded

---

## Temperature Monitoring (Advanced)

To monitor CPU temperature via SNMP:

### Install lm-sensors

```bash
sudo apt install -y lm-sensors
sudo sensors-detect --auto
sudo systemctl restart lm-sensors
```

### Configure SNMP to expose temperature

Edit `/etc/snmp/snmpd.conf` and add:

```conf
# Expose lm-sensors data
extend sensors /usr/bin/sensors
```

Restart snmpd:

```bash
sudo systemctl restart snmpd
```

### Query temperature

```bash
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.8.1
```

Temperature OIDs will be under `1.3.6.1.4.1.2021.8.1.*`

---

## Security Note

⚠️ **Change the community string from "public" to something secure!**

```conf
# In /etc/snmp/snmpd.conf
rocommunity MySecretString123
```

Then update your `config.yaml` accordingly.

---

## Troubleshooting

### SNMP not responding

```bash
# Check if snmpd is running
sudo systemctl status snmpd

# Check firewall
sudo ufw status
sudo ufw allow 161/udp

# Test locally first
snmpwalk -v2c -c public localhost
```

### "No Such Object" errors

This means the SNMP daemon doesn't have that MIB configured. Make sure:
1. You ran `apt install snmp-mibs-downloader`
2. You commented out `mibs :` in `/etc/snmp/snmp.conf`
3. You restarted snmpd: `sudo systemctl restart snmpd`

### Values show as 0 or empty

Some OIDs might not work on all systems. Use `snmpwalk` to discover what OIDs your system actually supports.

---

## Quick Test After Setup

After setting up SNMP on your Ubuntu machines, rebuild and restart the agent:

```powershell
# Rebuild agent
go build -o network-monitor-agent.exe .

# Run agent
.\network-monitor-agent.exe -config config.yaml
```

You should see logs like:

```
📡 SNMP: Querying device 'hp-630-notebook' (192.168.1.14)
✅ SNMP: [hp-630-notebook] cpu_idle_pct = 85.50 gauge
✅ SNMP: [hp-630-notebook] memory_available_kb = 2048000.00 gauge
✅ SNMP: [hp-630-notebook] load_avg_1min = 1.25 gauge
✅ SNMP: Collected 12 metrics from device 'hp-630-notebook'
✅ SNMP: Successfully polled 2/2 devices
```

---

## Summary

1. ✅ Install snmpd with extended MIBs on Ubuntu machines
2. ✅ Configure `/etc/snmp/snmpd.conf` with UCD-SNMP-MIB support
3. ✅ Restart snmpd and verify with snmpwalk
4. ✅ Update agent config.yaml with useful health OIDs
5. ✅ Rebuild agent and monitor meaningful system metrics!

Now you'll have **real system health monitoring** instead of just uptime and process counts! 🎉

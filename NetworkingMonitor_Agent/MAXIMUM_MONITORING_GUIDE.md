# Maximum Detail Monitoring Setup Guide

This guide will help you configure the agent and SNMP devices to capture **MAXIMUM details** about system health and performance.

---

## 📊 What You'll Monitor

### From Agent Machine (Windows)
✅ **CPU:** Overall usage + per-core usage (all 8 cores individually)
✅ **Memory:** Total, used, available, cached, buffers, swap
✅ **Disk:** Usage for all partitions + filesystem type + inodes
✅ **Disk I/O:** Read/write bytes and operations for each disk
✅ **Network:** Overall + per-interface traffic, packets, errors, drops
✅ **Temperature:** All sensors with high/critical thresholds
✅ **System:** Uptime, process count

### From SNMP Devices (Ubuntu Machines)
✅ **System Info:** Description, hostname, uptime, logged-in users, processes
✅ **CPU:** Idle%, user%, system%, raw counters (user, nice, system, idle, wait, interrupt)
✅ **Memory:** Total, available, swap, buffers, cache (in KB)
✅ **Load Average:** 1min, 5min, 15min
✅ **Disk:** Path, device, total, used, available, percent used, inode usage
✅ **Disk I/O:** Device, reads, writes, bytes read, bytes written
✅ **Network Interfaces:** Multiple interfaces with octets, packets, unicast/non-unicast, discards, errors (in/out)
✅ **TCP/UDP:** Active connections, segments, retransmissions, datagrams
✅ **Temperature:** (if configured) CPU and system sensors

---

## 🚀 Quick Start

### Step 1: Configure Ubuntu Machines for Maximum SNMP

Run these commands on **BOTH Ubuntu machines** (192.168.1.14 and 192.168.1.15):

```bash
# Install SNMP daemon with all MIBs
sudo apt update
sudo apt install -y snmpd snmp snmp-mibs-downloader lm-sensors

# Enable non-free MIBs
sudo sed -i 's/^mibs :/#mibs :/g' /etc/snmp/snmp.conf

# Detect hardware sensors
sudo sensors-detect --auto
```

### Step 2: Configure SNMP Daemon

Edit the SNMP configuration:

```bash
sudo nano /etc/snmp/snmpd.conf
```

**Replace entire file with this:**

```conf
###############################################################################
# SNMP Daemon Configuration - MAXIMUM MONITORING
###############################################################################

# Listen on all interfaces
agentaddress  udp:161

# Community string (CHANGE THIS in production!)
rocommunity public

# System information
sysLocation    "Ubuntu Laptop - Full Monitoring"
sysContact     me@example.org

###############################################################################
# DISK MONITORING
###############################################################################
# Monitor root partition (10% warning threshold)
disk / 10%

# Monitor /home if it's a separate partition
# disk /home 10%

# Include all disks
includeAllDisks 10%

###############################################################################
# LOAD MONITORING
###############################################################################
# Load thresholds: 12 (1min), 10 (5min), 5 (15min)
load 12 10 5

###############################################################################
# PROCESS MONITORING (Optional)
###############################################################################
# Monitor critical processes
proc sshd
proc cron

###############################################################################
# SYSTEM EXTENSIONS
###############################################################################
# Extended system information
extend uptime /bin/uptime
extend hostname /bin/hostname

# Disk usage details
extend df /bin/df -h

# Memory details
extend free /usr/bin/free

# Network stats
extend netstat /bin/netstat -s

# Temperature sensors (if lm-sensors installed)
extend sensors /usr/bin/sensors

###############################################################################
# CPU/MEMORY/DISK/NETWORK STATISTICS
###############################################################################
# Enable detailed system statistics
includeAllDisks 10%

# Enable disk I/O monitoring
diskio sda
diskio sdb
diskio nvme0n1

# (Adjust device names based on your system)
# To find device names, run: lsblk

###############################################################################
# NETWORK INTERFACE MONITORING
###############################################################################
# This is automatic - MIB-II interfaces will be available

###############################################################################
# TCP/UDP MONITORING
###############################################################################
# This is automatic - MIB-II TCP/UDP stats will be available

###############################################################################
# VIEW/ACCESS CONTROL
###############################################################################
# Allow full read access (for monitoring)
view systemonly included .1
```

### Step 3: Configure Disk I/O Monitoring

Find your disk devices:

```bash
lsblk
```

Output example:
```
NAME        MAJ:MIN RM   SIZE RO TYPE MOUNTPOINT
sda           8:0    0 238.5G  0 disk
├─sda1        8:1    0   512M  0 part /boot/efi
└─sda2        8:2    0   238G  0 part /
```

Edit `/etc/snmp/snmpd.conf` and update the diskio lines:

```conf
# For traditional SATA/SSD (sdX)
diskio sda

# For NVMe drives
diskio nvme0n1

# Add all your disks
```

### Step 4: Restart SNMP Daemon

```bash
sudo systemctl restart snmpd
sudo systemctl enable snmpd
sudo systemctl status snmpd
```

### Step 5: Verify SNMP is Working

Test locally on Ubuntu machine:

```bash
# Test system info
snmpget -v2c -c public localhost 1.3.6.1.2.1.1.5.0

# Test CPU
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.11

# Test memory
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.4

# Test disk
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.9

# Test network interfaces
snmpwalk -v2c -c public localhost 1.3.6.1.2.1.2.2.1

# Test disk I/O
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.13.15
```

If these return values, SNMP is configured correctly!

---

## 🔧 Configure Windows Agent

### Step 1: Use Maximum Details Configuration

Copy the comprehensive configuration:

```powershell
cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client

# Backup current config
copy config.yaml config.yaml.backup

# Use maximum details config
copy config_maximum_details.yaml config.yaml
```

### Step 2: Rebuild Agent

```powershell
# Rebuild with all new features
go build -o network-monitor-agent.exe .
```

### Step 3: Run Agent

```powershell
# Run agent
.\network-monitor-agent.exe -config config.yaml
```

### Step 4: Monitor Logs

Open a new PowerShell window:

```powershell
# Watch logs in real-time
Get-Content agent.log -Wait -Tail 50
```

---

## 📈 What You'll See

### Agent Logs (Windows Machine)

```
[INFO] Agent starting (ID: Faber-XXX, Name: Faber)
[INFO] Agent running. Collection interval: 30s, Send interval: 2m0s

[INFO] 📡 Polling 2 SNMP devices
[INFO] 📊 SNMP: Querying device 'hp-630-notebook' (192.168.1.14)
[INFO] ✅ SNMP: [hp-630-notebook] system_name = faber-HP-630-Notebook-PC string
[INFO] ✅ SNMP: [hp-630-notebook] cpu_idle_percent = 85.50 gauge
[INFO] ✅ SNMP: [hp-630-notebook] cpu_user_percent = 10.20 gauge
[INFO] ✅ SNMP: [hp-630-notebook] cpu_system_percent = 4.30 gauge
[INFO] ✅ SNMP: [hp-630-notebook] memory_total_kb = 8215126.00 gauge
[INFO] ✅ SNMP: [hp-630-notebook] memory_available_kb = 2048000.00 gauge
[INFO] ✅ SNMP: [hp-630-notebook] load_avg_1min = 1.25 gauge
[INFO] ✅ SNMP: [hp-630-notebook] disk_root_total_kb = 244198400.00 gauge
[INFO] ✅ SNMP: [hp-630-notebook] disk_root_percent_used = 65.00 gauge
[INFO] ✅ SNMP: [hp-630-notebook] interface1_octets_in = 123456789.00 counter
[INFO] ✅ SNMP: [hp-630-notebook] tcp_current_established = 25.00 gauge
[INFO] ✅ SNMP: Collected 65 metrics from device 'hp-630-notebook'
[INFO] ✅ SNMP: Successfully polled 2/2 devices

[INFO] Collected and stored metrics: CPU=20.90%, Memory=77.00%, Disks=1
[DEBUG] Collected per-core CPU usage: Core 0=25%, Core 1=18%, ...
[DEBUG] Network interface Wi-Fi: 573MB sent, 100MB received
[INFO] Processing 150 unsent metrics
[INFO] Sent 150 metrics successfully
[INFO] Processing 65 unsent SNMP metrics
[INFO] Sent 65 SNMP metrics successfully
```

### Test Server Output

```
📊 Received 150 metrics from agent Faber-XXX
   - cpu.usage: 20.90 percent
     Metadata: {"cores":8}
   - cpu.core_usage: 25.00 percent
     Metadata: {"core":0}
   - cpu.core_usage: 18.00 percent
     Metadata: {"core":1}
   - memory.usage: 77.00 percent
     Metadata: {"available":1837240320,"buffers":12345678,"cached":987654321,...}
   - disk.io: 500000000.00 bytes
     Metadata: {"device":"C:","read_bytes":300000000,"write_bytes":200000000,...}
   - network.interface: 673278697.00 bytes
     Metadata: {"bytes_recv":100000000,"bytes_sent":573278697,"drops_in":0,"drops_out":0,...}

📡 Received 65 SNMP metrics from agent Faber-XXX
   - hp-630-notebook.system_name: faber-HP-630-Notebook-PC (string)
     Device: 192.168.1.14, OID: 1.3.6.1.2.1.1.5.0
   - hp-630-notebook.cpu_idle_percent: 85.50 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.11.9.0
   - hp-630-notebook.memory_available_kb: 2048000.00 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.4.6.0
```

---

## 📊 Understanding the Metrics

### Agent Machine Metrics

#### CPU Metrics
- **cpu.usage**: Overall CPU usage percentage
- **cpu.core_usage**: Individual core usage (one entry per core)
- **Metadata.cores**: Total number of CPU cores

**Example Calculation:**
```
Total CPU: 20.90%
Core 0: 25%, Core 1: 18%, Core 2: 22%, Core 3: 15%
Core 4: 20%, Core 5: 19%, Core 6: 24%, Core 7: 23%
Average: (25+18+22+15+20+19+24+23)/8 ≈ 20.90%
```

#### Memory Metrics
- **memory.usage**: RAM usage percentage
- **Metadata.total**: Total RAM in bytes
- **Metadata.used**: Used RAM in bytes
- **Metadata.available**: Available RAM in bytes
- **Metadata.cached**: Cached memory in bytes
- **Metadata.buffers**: Buffer memory in bytes

**Example:**
```json
{
  "total": 8215126016,        // 7.8 GB
  "used": 6377885696,         // 6.1 GB
  "available": 1837240320,    // 1.7 GB
  "cached": 987654321,        // 941 MB
  "buffers": 123456789        // 117 MB
}
```

#### Network Metrics
- **network.bytes_sent**: Total bytes sent across all interfaces
- **network.bytes_recv**: Total bytes received
- **network.errors**: Total network errors (in + out)
- **network.drops**: Total packet drops (in + out)
- **network.interface**: Per-interface statistics

**Per-Interface Metadata:**
```json
{
  "interface": "Wi-Fi",
  "bytes_sent": 573278697,
  "bytes_recv": 100595452,
  "packets_sent": 423156,
  "packets_recv": 198765,
  "errors_in": 0,
  "errors_out": 0,
  "drops_in": 0,
  "drops_out": 0
}
```

#### Disk I/O Metrics
- **disk.io**: Combined read+write bytes
- **Metadata.device**: Disk device name
- **Metadata.read_bytes**: Bytes read from disk
- **Metadata.write_bytes**: Bytes written to disk
- **Metadata.read_count**: Number of read operations
- **Metadata.write_count**: Number of write operations

### SNMP Device Metrics

#### CPU Metrics (UCD-SNMP-MIB)
- **cpu_idle_percent**: CPU idle percentage (higher = less loaded)
- **cpu_user_percent**: CPU used by user processes
- **cpu_system_percent**: CPU used by kernel/system

**Calculate CPU Usage:**
```
CPU Usage = 100 - cpu_idle_percent
Example: 100 - 85.50 = 14.50% CPU usage
```

#### Memory Metrics (UCD-SNMP-MIB)
- **memory_total_kb**: Total RAM in kilobytes
- **memory_available_kb**: Available RAM in KB
- **memory_total_free_kb**: Free RAM in KB

**Calculate Memory Usage:**
```
Used KB = memory_total_kb - memory_available_kb
Usage % = (Used KB / Total KB) × 100

Example:
Total: 8,215,126 KB (8 GB)
Available: 2,048,000 KB (2 GB)
Used: 6,167,126 KB (6 GB)
Usage: (6,167,126 / 8,215,126) × 100 = 75.08%
```

#### Load Average Metrics
- **load_avg_1min**: Average system load over 1 minute
- **load_avg_5min**: Average system load over 5 minutes
- **load_avg_15min**: Average system load over 15 minutes

**Understanding Load:**
- Load represents number of processes waiting for CPU
- Compare to number of CPU cores
- Load < Cores = System OK
- Load = Cores = System fully utilized
- Load > Cores = System overloaded

**Example (4-core system):**
```
Load 1.25 on 4-core system = 31% utilization (good)
Load 4.00 on 4-core system = 100% utilization (maxed out)
Load 8.00 on 4-core system = 200% utilization (overloaded!)
```

#### Disk Metrics
- **disk_root_total_kb**: Total disk space in KB
- **disk_root_available_kb**: Available disk space in KB
- **disk_root_percent_used**: Percentage of disk used

#### Network Interface Metrics
- **interface1_octets_in**: Bytes received (counter, always increasing)
- **interface1_octets_out**: Bytes sent (counter)
- **interface1_errors_in**: Input errors
- **interface1_errors_out**: Output errors

**To calculate throughput:**
```
Sample at time T1: octets_in = 1,000,000,000
Sample at time T2 (60s later): octets_in = 1,005,000,000

Bytes in 60s = 1,005,000,000 - 1,000,000,000 = 5,000,000 bytes
Throughput = 5,000,000 / 60 = 83,333 bytes/sec = 666 Kbps
```

#### TCP/UDP Metrics
- **tcp_current_established**: Number of active TCP connections
- **tcp_retransmitted_segments**: TCP retransmissions (network quality indicator)
- **udp_datagrams_in**: UDP packets received
- **udp_datagrams_out**: UDP packets sent

---

## 🔍 Troubleshooting

### SNMP "No Such Object" Errors

If some OIDs return "No Such Object":

1. **Check if snmpd has UCD-SNMP-MIB support:**
   ```bash
   snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021
   ```

2. **If empty, reinstall with full MIBs:**
   ```bash
   sudo apt install --reinstall snmpd snmp-mibs-downloader
   sudo sed -i 's/^mibs :/#mibs :/g' /etc/snmp/snmp.conf
   sudo systemctl restart snmpd
   ```

3. **Verify configuration:**
   ```bash
   sudo snmpd -f -Le -Dread_config
   ```

### Interface Numbers Wrong

Network interface OIDs use index numbers (1, 2, 3...). To find the correct index:

```bash
# List all interfaces
snmpwalk -v2c -c public localhost 1.3.6.1.2.1.2.2.1.2

# Output:
# IF-MIB::ifDescr.1 = STRING: lo
# IF-MIB::ifDescr.2 = STRING: eth0
# IF-MIB::ifDescr.3 = STRING: wlan0

# Use the index number in OIDs:
# Interface 2 (eth0): 1.3.6.1.2.1.2.2.1.10.2
# Interface 3 (wlan0): 1.3.6.1.2.1.2.2.1.10.3
```

Update config.yaml with correct interface numbers.

### Disk I/O Not Working

Check device names:

```bash
cat /proc/diskstats
lsblk
```

Update `/etc/snmp/snmpd.conf`:

```conf
diskio sda
diskio sdb
diskio nvme0n1
# Add your actual disk devices
```

Restart snmpd:

```bash
sudo systemctl restart snmpd
```

---

## 📈 Performance Impact

### Agent Machine
- **Collection Interval**: 30 seconds
- **CPU Impact**: ~0.5-1% CPU usage
- **Memory**: ~50-100 MB RAM
- **Disk**: ~1-5 MB/hour for database

### SNMP Devices
- **Poll Interval**: 30 seconds
- **CPU Impact**: <0.1% CPU usage per query
- **Network**: ~5-10 KB per poll cycle
- **Total**: ~60 KB/minute for 2 devices with 65 metrics each

---

## 🎯 Next Steps

### 1. Fine-tune Collection Intervals

For less critical monitoring:
```yaml
agent:
  collect_interval: 60s  # Every minute
  send_interval: 300s    # Every 5 minutes

snmp_manager:
  poll_interval: 60s     # Every minute
```

For real-time monitoring:
```yaml
agent:
  collect_interval: 10s  # Every 10 seconds
  send_interval: 30s     # Every 30 seconds

snmp_manager:
  poll_interval: 15s     # Every 15 seconds
```

### 2. Add More Devices

Add additional SNMP devices to config.yaml:
```yaml
    - name: "router"
      host: "192.168.1.1"
      # ... OIDs ...

    - name: "switch"
      host: "192.168.1.10"
      # ... OIDs ...
```

### 3. Set Debug Logging

To see every metric collected:

```yaml
logging:
  level: "debug"
```

---

## ✅ Checklist

- [ ] Ubuntu machines have snmpd installed and configured
- [ ] UCD-SNMP-MIB support verified (snmpwalk shows data)
- [ ] Disk I/O devices configured correctly
- [ ] Interface numbers identified
- [ ] Agent rebuilt with new code (`go build`)
- [ ] config.yaml updated (or using config_maximum_details.yaml)
- [ ] Test server running on port 8080
- [ ] Agent running and collecting data
- [ ] Logs show successful SNMP polls
- [ ] Test server receiving detailed metrics

---

## 🎉 Success!

When everything is working, you'll see:

**From Agent Machine:**
- 150+ metrics every 30 seconds
- Per-core CPU, per-interface network, disk I/O
- Temperature sensors, detailed memory stats

**From SNMP Devices:**
- 65+ metrics per device every 30 seconds
- CPU idle/user/system percentages
- Memory total/available
- Load averages
- Disk usage and I/O
- Network interface traffic and errors
- TCP/UDP connection stats

**Total Monitoring:**
- ~280+ metrics every 30 seconds
- ~560 metrics per minute
- ~33,600 metrics per hour
- **Comprehensive system health visibility!** 🚀

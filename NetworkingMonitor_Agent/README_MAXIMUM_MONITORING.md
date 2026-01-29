# ✨ Maximum Detail Monitoring - Complete Setup

## 🎯 What This Does

This configuration transforms your monitoring system to capture **MAXIMUM possible details** from:
- **1 Windows Agent Machine** (where the agent runs)
- **2 Ubuntu SNMP Devices** (192.168.1.14 and 192.168.1.15)

---

## 📊 Total Monitoring Capacity

### Windows Agent Machine (~150 metrics/30sec)
- ✅ Overall CPU + per-core usage (all 8 cores)
- ✅ Memory (total, used, available, cached, buffers, swap)
- ✅ Disk usage for all partitions + inodes + filesystem types
- ✅ Disk I/O (reads, writes, operations per device)
- ✅ Network overall + per-interface traffic
- ✅ Network errors, drops, packets
- ✅ Temperature sensors with thresholds
- ✅ System uptime + process count

### Ubuntu SNMP Devices (~65 metrics/30sec each)
- ✅ System: Description, hostname, uptime, users, processes
- ✅ CPU: Idle%, user%, system%, raw counters
- ✅ Memory: Total, available, swap, buffers (KB)
- ✅ Load: 1min, 5min, 15min averages
- ✅ Disk: Total, available, used, percent, inodes
- ✅ Disk I/O: Device, reads, writes, byte counters
- ✅ Network: Multiple interfaces with octets, packets, errors, discards
- ✅ TCP/UDP: Established connections, retransmissions, datagrams

**TOTAL: ~280 metrics every 30 seconds = 560/min = 33,600/hour** 🚀

---

## 🚀 Three-Step Setup

### Step 1: Setup Ubuntu SNMP (5 minutes)

**On BOTH Ubuntu machines (192.168.1.14 and 192.168.1.15):**

```bash
# 1. Install packages
sudo apt update
sudo apt install -y snmpd snmp snmp-mibs-downloader lm-sensors

# 2. Enable MIBs
sudo sed -i 's/^mibs :/#mibs :/g' /etc/snmp/snmp.conf

# 3. Install configuration
sudo cp /path/to/snmpd.conf.ubuntu /etc/snmp/snmpd.conf
# OR manually copy the contents from snmpd.conf.ubuntu

# 4. Update disk I/O devices in the config
lsblk  # Check your disk names
sudo nano /etc/snmp/snmpd.conf
# Update diskio lines with your actual disks (sda, nvme0n1, etc.)

# 5. Restart SNMP daemon
sudo systemctl restart snmpd
sudo systemctl enable snmpd

# 6. Verify it works
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021.11
```

✅ **Success:** If the verify command shows CPU data, SNMP is working!

### Step 2: Configure Windows Agent (2 minutes)

```powershell
cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client

# Backup your current config
copy config.yaml config.yaml.backup

# Use the maximum monitoring configuration
copy config_maximum_details.yaml config.yaml

# IMPORTANT: Rebuild agent with new code
go build -o network-monitor-agent.exe .
```

### Step 3: Run Everything (1 minute)

**Terminal 1 - Test Server:**
```powershell
cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client\examples\test-server
go run main.go
```

**Terminal 2 - Agent:**
```powershell
cd C:\Anupam\Faber\Projects\NetworkingMonitor_Client
.\network-monitor-agent.exe -config config.yaml
```

**Terminal 3 - Watch Logs:**
```powershell
Get-Content C:\Anupam\Faber\Projects\NetworkingMonitor_Client\agent.log -Wait -Tail 50
```

---

## ✅ What Success Looks Like

### Agent Logs:
```
[INFO] Agent starting (ID: Faber-XXX, Name: Faber)
[INFO] SNMP Manager enabled with 2 devices

📡 SNMP: Polling 2 SNMP devices
📊 SNMP: Querying device 'hp-630-notebook' (192.168.1.14)
✅ SNMP: [hp-630-notebook] system_name = faber-HP-630-Notebook-PC string
✅ SNMP: [hp-630-notebook] cpu_idle_percent = 85.50 gauge
✅ SNMP: [hp-630-notebook] cpu_user_percent = 10.20 gauge
✅ SNMP: [hp-630-notebook] cpu_system_percent = 4.30 gauge
✅ SNMP: [hp-630-notebook] memory_total_kb = 8215126.00 gauge
✅ SNMP: [hp-630-notebook] memory_available_kb = 2048000.00 gauge
✅ SNMP: [hp-630-notebook] load_avg_1min = 1.25 gauge
✅ SNMP: [hp-630-notebook] disk_root_total_kb = 244198400.00 gauge
✅ SNMP: [hp-630-notebook] disk_root_percent_used = 65.00 gauge
✅ SNMP: [hp-630-notebook] interface1_octets_in = 123456789.00 counter
✅ SNMP: [hp-630-notebook] interface1_octets_out = 987654321.00 counter
✅ SNMP: [hp-630-notebook] tcp_current_established = 25.00 gauge
✅ SNMP: Collected 65 metrics from device 'hp-630-notebook'

📊 SNMP: Querying device 'hp-pavilion-notebook' (192.168.1.15)
✅ SNMP: [hp-pavilion-notebook] cpu_idle_percent = 92.30 gauge
... (65 more metrics)
✅ SNMP: Collected 65 metrics from device 'hp-pavilion-notebook'

✅ SNMP: Successfully polled 2/2 devices

Collected and stored metrics: CPU=20.90%, Memory=77.00%, Disks=1
Processing 150 unsent metrics
Sent 150 metrics successfully
Processing 130 unsent SNMP metrics
Sent 130 SNMP metrics successfully
```

### Test Server Output:
```
📊 Received 150 metrics from agent Faber-XXX
   - cpu.usage: 20.90 percent
     Metadata: {"cores":8}
   - cpu.core_usage: 25.00 percent (Core 0)
   - cpu.core_usage: 18.00 percent (Core 1)
   - cpu.core_usage: 22.00 percent (Core 2)
   ... (all 8 cores)
   - memory.usage: 77.00 percent
     Metadata: {"available":1837240320,"buffers":123456789,"cached":987654321,...}
   - disk.io: 500000000.00 bytes
     Metadata: {"device":"C:","read_bytes":300000000,"write_bytes":200000000,...}
   - network.interface: Wi-Fi 673278697.00 bytes
     Metadata: {"bytes_recv":100595452,"bytes_sent":573278697,"drops_in":0,...}

📡 Received 130 SNMP metrics from agent Faber-XXX
   - hp-630-notebook.system_name: faber-HP-630-Notebook-PC (string)
     Device: 192.168.1.14, OID: 1.3.6.1.2.1.1.5.0
   - hp-630-notebook.cpu_idle_percent: 85.50 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.11.9.0
   - hp-630-notebook.cpu_user_percent: 10.20 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.11.10.0
   - hp-630-notebook.memory_available_kb: 2048000.00 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.4.6.0
   - hp-630-notebook.load_avg_1min: 1.25 (gauge)
     Device: 192.168.1.14, OID: 1.3.6.1.4.1.2021.10.1.3.1
   - hp-pavilion-notebook.cpu_idle_percent: 92.30 (gauge)
     Device: 192.168.1.15, OID: 1.3.6.1.4.1.2021.11.9.0
   ... (130 metrics total)
```

---

## 📁 File Reference

| File | Purpose |
|------|---------|
| `config_maximum_details.yaml` | **Use this** - Agent configuration with all metrics |
| `snmpd.conf.ubuntu` | **Copy to Ubuntu** - SNMP daemon configuration |
| `QUICK_START_MAXIMUM_MONITORING.md` | Quick setup guide (5 minutes) |
| `MAXIMUM_MONITORING_GUIDE.md` | Detailed guide with explanations |
| `SNMP_UBUNTU_SETUP.md` | SNMP setup instructions |

---

## 🔧 Customization

### Adjust Collection Frequency

Edit `config.yaml`:

```yaml
agent:
  collect_interval: 30s   # How often to collect from Windows machine
  send_interval: 120s     # How often to send to server

snmp_manager:
  poll_interval: 30s      # How often to poll SNMP devices
```

**Recommendations:**
- **Real-time monitoring:** 10s collect, 30s send, 15s SNMP poll
- **Balanced monitoring:** 30s collect, 120s send, 30s SNMP poll (default)
- **Light monitoring:** 60s collect, 300s send, 60s SNMP poll

### Remove Specific Metrics

Edit `config_maximum_details.yaml` and remove unwanted OIDs.

### Add More Devices

Add additional SNMP devices:

```yaml
snmp_manager:
  devices:
    - name: "hp-630-notebook"
      host: "192.168.1.14"
      # ... existing config ...

    - name: "hp-pavilion-notebook"
      host: "192.168.1.15"
      # ... existing config ...

    - name: "my-router"
      host: "192.168.1.1"
      port: 161
      community: "public"
      version: "2c"
      oids:
        - oid: "1.3.6.1.2.1.1.3.0"
          name: "uptime"
          type: "counter"
        # ... add relevant OIDs ...
```

---

## 🐛 Troubleshooting

### 1. SNMP Returns "No Such Object"

```bash
# On Ubuntu, verify MIB support
snmpwalk -v2c -c public localhost 1.3.6.1.4.1.2021

# If empty:
sudo apt install --reinstall snmpd snmp-mibs-downloader
sudo sed -i 's/^mibs :/#mibs :/g' /etc/snmp/snmp.conf
sudo systemctl restart snmpd
```

### 2. Device Names Not Showing

```powershell
# Rebuild agent with latest code
go build -o network-monitor-agent.exe .
```

### 3. Wrong Network Interface Numbers

```bash
# Find interface indexes on Ubuntu
snmpwalk -v2c -c public localhost 1.3.6.1.2.1.2.2.1.2

# Output shows:
# IF-MIB::ifDescr.1 = STRING: lo
# IF-MIB::ifDescr.2 = STRING: eth0
# IF-MIB::ifDescr.3 = STRING: wlan0

# Use these numbers in OIDs (replace .1 with .2 or .3)
```

### 4. Disk I/O Not Working

```bash
# Check disk device names
lsblk
cat /proc/diskstats

# Update /etc/snmp/snmpd.conf
sudo nano /etc/snmp/snmpd.conf

# Add your disks:
diskio sda
diskio nvme0n1

# Restart
sudo systemctl restart snmpd
```

### 5. High CPU/Memory Usage

Reduce collection frequency in `config.yaml`:

```yaml
agent:
  collect_interval: 60s
snmp_manager:
  poll_interval: 60s
```

---

## 📊 Understanding the Data

### Agent Machine Metrics

**CPU:**
- `cpu.usage` = Overall CPU usage
- `cpu.core_usage` = Per-core usage (8 separate metrics)

**Memory:**
- Calculate usage: `(total - available) / total × 100`
- `cached` = File cache (can be reclaimed)
- `buffers` = Block device cache

**Disk I/O:**
- Monitor `read_bytes` and `write_bytes` over time
- High `read_count` + low `read_bytes` = Many small reads
- Monitor for disk bottlenecks

**Network:**
- Interface-level stats show which adapter is busy
- `errors` and `drops` indicate network problems

### SNMP Device Metrics

**CPU Usage Calculation:**
```
CPU Usage % = 100 - cpu_idle_percent
Example: 100 - 85.5 = 14.5% CPU usage
```

**Memory Usage Calculation:**
```
Used KB = memory_total_kb - memory_available_kb
Usage % = (Used / Total) × 100
```

**Load Average:**
- Compare to CPU core count
- Load 1.25 on 4-core system = 31% utilization ✅
- Load 5.00 on 4-core system = 125% utilization ⚠️

**Network Throughput:**
```
Bytes at T1 = 1,000,000,000
Bytes at T2 (30s later) = 1,005,000,000
Throughput = (1,005M - 1,000M) / 30s = 166,667 bytes/sec ≈ 1.3 Mbps
```

---

## 🎯 Next Steps

1. ✅ **Monitor for a few hours** - Let the system collect data
2. ✅ **Review logs** - Check for any errors or warnings
3. ✅ **Optimize** - Adjust collection intervals based on needs
4. ✅ **Expand** - Add more SNMP devices (routers, switches, printers)
5. ✅ **Integrate** - Connect to your monitoring dashboard
6. ✅ **Alert** - Configure alerts for critical thresholds

---

## 📚 Documentation

- `QUICK_START_MAXIMUM_MONITORING.md` - 5-minute quick start
- `MAXIMUM_MONITORING_GUIDE.md` - Complete guide with details
- `SNMP_UBUNTU_SETUP.md` - SNMP setup instructions
- `config_maximum_details.yaml` - Configuration file
- `snmpd.conf.ubuntu` - SNMP daemon configuration

---

## 🎉 Success!

You're now monitoring:
- **8 CPU cores individually**
- **Detailed memory statistics**
- **Per-interface network traffic**
- **Disk I/O operations**
- **130+ SNMP metrics from 2 Ubuntu machines**
- **280+ total metrics every 30 seconds**

**Comprehensive system health visibility achieved!** 🚀📊✨

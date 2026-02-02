package collector

import (
	"fmt"
	"runtime"
	"sync"
	"time"

	"github.com/faber/network-monitor-agent/internal/hardware"
	"github.com/faber/network-monitor-agent/internal/hyperv"
	"github.com/faber/network-monitor-agent/internal/network"
	"github.com/faber/network-monitor-agent/internal/os"
	"github.com/faber/network-monitor-agent/internal/sensors"
	"github.com/faber/network-monitor-agent/internal/storage"
	"github.com/shirou/gopsutil/v3/cpu"
	"github.com/shirou/gopsutil/v3/disk"
	"github.com/shirou/gopsutil/v3/host"
	"github.com/shirou/gopsutil/v3/load"
	"github.com/shirou/gopsutil/v3/mem"
	"github.com/shirou/gopsutil/v3/net"
)

type Collector struct {
	storage *storage.Storage

	// Previous metrics for delta calculations
	prevDiskIO map[string]*disk.IOCountersStat
	prevTime   time.Time
	mu         sync.Mutex
}

type SystemMetrics struct {
	Timestamp      time.Time
	CPU            CPUMetrics
	CPUPerCore     []CPUCoreMetrics
	Memory         MemoryMetrics
	Disk           []DiskMetrics
	DiskIO         []DiskIOMetrics
	Network        NetworkMetrics
	NetworkPerIface []NetworkInterfaceMetrics
	Temperature    []TempMetrics
	Uptime         uint64
	ProcessCount   int

	// Phase 1: Enhanced NIC Details
	NetworkInterfaces []*network.NetworkInterfaceDetail

	// Phase 2: Firmware & Hardware Info
	BIOSInfo         *hardware.BIOSInfo
	MotherboardInfo  *hardware.MotherboardInfo
	ComponentFirmware []*hardware.ComponentFirmware

	// Phase 3: OS Patch Level
	PatchLevel       *os.PatchLevel

	// Phase 4: Hardware Sensors (Temperature, Voltage, Fans, Power)
	SensorData       *sensors.SensorData

	// Phase 5: Advanced Monitoring (Hyper-V)
	HyperVInfo       *hyperv.HyperVInfo
}

type CPUMetrics struct {
	UsagePercent float64
	LoadAvg1     float64
	LoadAvg5     float64
	LoadAvg15    float64
	Cores        int
}

type CPUCoreMetrics struct {
	Core         int
	UsagePercent float64
}

type MemoryMetrics struct {
	Total       uint64
	Used        uint64
	Available   uint64
	UsedPercent float64
	SwapTotal   uint64
	SwapUsed    uint64
	SwapPercent float64
	Cached      uint64
	Buffers     uint64
}

type DiskMetrics struct {
	Device      string
	MountPoint  string
	FSType      string
	Total       uint64
	Used        uint64
	Free        uint64
	UsedPercent float64
	InodesTotal uint64
	InodesUsed  uint64
	InodesFree  uint64
}

type DiskIOMetrics struct {
	Device       string
	ReadBytes    uint64
	WriteBytes   uint64
	ReadCount    uint64
	WriteCount   uint64
	ReadTime     uint64 // milliseconds
	WriteTime    uint64 // milliseconds
	IOTime       uint64 // milliseconds
	WeightedIO   uint64 // weighted time
	IOPS         float64 // I/O operations per second
	ReadIOPS     float64
	WriteIOPS    float64
	Throughput   float64 // MB/s
	AvgLatency   float64 // milliseconds
	QueueDepth   float64
	Utilization  float64 // percentage
}

type NetworkMetrics struct {
	BytesSent   uint64
	BytesRecv   uint64
	PacketsSent uint64
	PacketsRecv uint64
	ErrorsIn    uint64
	ErrorsOut   uint64
	DropsIn     uint64
	DropsOut    uint64
}

type NetworkInterfaceMetrics struct {
	Interface   string
	BytesSent   uint64
	BytesRecv   uint64
	PacketsSent uint64
	PacketsRecv uint64
	ErrorsIn    uint64
	ErrorsOut   uint64
	DropsIn     uint64
	DropsOut    uint64
}

type TempMetrics struct {
	Sensor      string
	Temperature float64
	High        float64
	Critical    float64
}

func New(store *storage.Storage) *Collector {
	return &Collector{
		storage:    store,
		prevDiskIO: make(map[string]*disk.IOCountersStat),
		prevTime:   time.Now(),
	}
}

// Collect gathers all system metrics
func (c *Collector) Collect() (*SystemMetrics, error) {
	metrics := &SystemMetrics{
		Timestamp: time.Now(),
	}

	// Get CPU core count
	if cpuCounts, err := cpu.Counts(true); err == nil {
		metrics.CPU.Cores = cpuCounts
	}

	// Collect overall CPU metrics
	cpuPercent, err := cpu.Percent(time.Second, false)
	if err == nil && len(cpuPercent) > 0 {
		metrics.CPU.UsagePercent = cpuPercent[0]
	}

	// Collect per-core CPU usage
	cpuPerCore, err := cpu.Percent(time.Second, true)
	if err == nil {
		for i, percent := range cpuPerCore {
			metrics.CPUPerCore = append(metrics.CPUPerCore, CPUCoreMetrics{
				Core:         i,
				UsagePercent: percent,
			})
		}
	}

	// Load averages (not available on Windows)
	if runtime.GOOS != "windows" {
		if loadAvg, err := load.Avg(); err == nil {
			metrics.CPU.LoadAvg1 = loadAvg.Load1
			metrics.CPU.LoadAvg5 = loadAvg.Load5
			metrics.CPU.LoadAvg15 = loadAvg.Load15
		}
	}

	// Collect memory metrics (extended)
	if vmStat, err := mem.VirtualMemory(); err == nil {
		metrics.Memory.Total = vmStat.Total
		metrics.Memory.Used = vmStat.Used
		metrics.Memory.Available = vmStat.Available
		metrics.Memory.UsedPercent = vmStat.UsedPercent
		metrics.Memory.Cached = vmStat.Cached
		metrics.Memory.Buffers = vmStat.Buffers
	}

	if swapStat, err := mem.SwapMemory(); err == nil {
		metrics.Memory.SwapTotal = swapStat.Total
		metrics.Memory.SwapUsed = swapStat.Used
		metrics.Memory.SwapPercent = swapStat.UsedPercent
	}

	// Collect disk metrics (extended)
	partitions, err := disk.Partitions(false)
	if err == nil {
		for _, partition := range partitions {
			usage, err := disk.Usage(partition.Mountpoint)
			if err != nil {
				continue
			}
			metrics.Disk = append(metrics.Disk, DiskMetrics{
				Device:      partition.Device,
				MountPoint:  partition.Mountpoint,
				FSType:      partition.Fstype,
				Total:       usage.Total,
				Used:        usage.Used,
				Free:        usage.Free,
				UsedPercent: usage.UsedPercent,
				InodesTotal: usage.InodesTotal,
				InodesUsed:  usage.InodesUsed,
				InodesFree:  usage.InodesFree,
			})
		}
	}

	// Collect disk I/O statistics with advanced metrics
	if diskIO, err := disk.IOCounters(); err == nil {
		c.mu.Lock()
		currentTime := time.Now()
		timeDelta := currentTime.Sub(c.prevTime).Seconds()

		for device, stats := range diskIO {
			ioMetric := DiskIOMetrics{
				Device:     device,
				ReadBytes:  stats.ReadBytes,
				WriteBytes: stats.WriteBytes,
				ReadCount:  stats.ReadCount,
				WriteCount: stats.WriteCount,
				ReadTime:   stats.ReadTime,
				WriteTime:  stats.WriteTime,
				IOTime:     stats.IoTime,
				WeightedIO: stats.WeightedIO,
			}

			// Calculate rate-based metrics if we have previous data
			if prev, exists := c.prevDiskIO[device]; exists && timeDelta > 0 {
				// Calculate IOPS
				readOps := float64(stats.ReadCount - prev.ReadCount)
				writeOps := float64(stats.WriteCount - prev.WriteCount)
				ioMetric.ReadIOPS = readOps / timeDelta
				ioMetric.WriteIOPS = writeOps / timeDelta
				ioMetric.IOPS = ioMetric.ReadIOPS + ioMetric.WriteIOPS

				// Calculate throughput (MB/s)
				readBytes := float64(stats.ReadBytes - prev.ReadBytes)
				writeBytes := float64(stats.WriteBytes - prev.WriteBytes)
				totalBytes := readBytes + writeBytes
				ioMetric.Throughput = (totalBytes / timeDelta) / (1024 * 1024)

				// Calculate average latency (ms)
				totalOps := readOps + writeOps
				if totalOps > 0 {
					readTimeMs := float64(stats.ReadTime - prev.ReadTime)
					writeTimeMs := float64(stats.WriteTime - prev.WriteTime)
					ioMetric.AvgLatency = (readTimeMs + writeTimeMs) / totalOps
				}

				// Calculate queue depth (weighted I/O time / total time)
				weightedIODelta := float64(stats.WeightedIO - prev.WeightedIO)
				ioMetric.QueueDepth = weightedIODelta / (timeDelta * 1000) // Convert to ms

				// Calculate utilization (percentage of time disk was busy)
				ioTimeDelta := float64(stats.IoTime - prev.IoTime)
				ioMetric.Utilization = (ioTimeDelta / (timeDelta * 1000)) * 100
				if ioMetric.Utilization > 100 {
					ioMetric.Utilization = 100
				}
			}

			metrics.DiskIO = append(metrics.DiskIO, ioMetric)

			// Store current stats for next iteration
			statsCopy := stats
			c.prevDiskIO[device] = &statsCopy
		}

		c.prevTime = currentTime
		c.mu.Unlock()
	}

	// Collect overall network metrics
	if netIO, err := net.IOCounters(false); err == nil && len(netIO) > 0 {
		metrics.Network.BytesSent = netIO[0].BytesSent
		metrics.Network.BytesRecv = netIO[0].BytesRecv
		metrics.Network.PacketsSent = netIO[0].PacketsSent
		metrics.Network.PacketsRecv = netIO[0].PacketsRecv
		metrics.Network.ErrorsIn = netIO[0].Errin
		metrics.Network.ErrorsOut = netIO[0].Errout
		metrics.Network.DropsIn = netIO[0].Dropin
		metrics.Network.DropsOut = netIO[0].Dropout
	}

	// Collect per-interface network metrics
	if netIOPerIface, err := net.IOCounters(true); err == nil {
		for _, iface := range netIOPerIface {
			metrics.NetworkPerIface = append(metrics.NetworkPerIface, NetworkInterfaceMetrics{
				Interface:   iface.Name,
				BytesSent:   iface.BytesSent,
				BytesRecv:   iface.BytesRecv,
				PacketsSent: iface.PacketsSent,
				PacketsRecv: iface.PacketsRecv,
				ErrorsIn:    iface.Errin,
				ErrorsOut:   iface.Errout,
				DropsIn:     iface.Dropin,
				DropsOut:    iface.Dropout,
			})
		}
	}

	// Collect temperature sensors (extended)
	if temps, err := host.SensorsTemperatures(); err == nil {
		for _, temp := range temps {
			metrics.Temperature = append(metrics.Temperature, TempMetrics{
				Sensor:      temp.SensorKey,
				Temperature: temp.Temperature,
				High:        temp.High,
				Critical:    temp.Critical,
			})
		}
	}

	// Collect uptime
	if uptime, err := host.Uptime(); err == nil {
		metrics.Uptime = uptime
	}

	// Collect process count
	if hostInfo, err := host.Info(); err == nil {
		metrics.ProcessCount = int(hostInfo.Procs)
	}

	// Phase 1: Collect enhanced NIC details
	if nicDetails, err := network.GetAllInterfaceDetails(); err == nil {
		metrics.NetworkInterfaces = nicDetails
	}

	// Phase 2: Collect firmware and hardware info
	if biosInfo, err := hardware.GetBIOSInfo(); err == nil {
		metrics.BIOSInfo = biosInfo
	}
	if moboInfo, err := hardware.GetMotherboardInfo(); err == nil {
		metrics.MotherboardInfo = moboInfo
	}
	if firmware, err := hardware.GetComponentFirmware(); err == nil {
		metrics.ComponentFirmware = firmware
	}

	// Phase 3: Collect OS patch level
	if patchLevel, err := os.GetPatchLevel(); err == nil {
		metrics.PatchLevel = patchLevel
	}

	// Phase 4: Collect hardware sensors (temperature, voltage, fans, power)
	if sensorData, err := sensors.GetAllSensors(); err == nil {
		metrics.SensorData = sensorData
	}

	// Phase 5: Collect Hyper-V information (Windows only)
	if hyperv.IsHyperVEnabled() {
		if hypervInfo, err := hyperv.GetHyperVInfo(); err == nil {
			metrics.HyperVInfo = hypervInfo
		}
	}

	return metrics, nil
}

// Store saves collected metrics to the database
func (c *Collector) Store(metrics *SystemMetrics) error {
	// Store overall CPU metrics
	if err := c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "cpu.usage",
		Value:      metrics.CPU.UsagePercent,
		Unit:       "percent",
		Metadata: map[string]interface{}{
			"cores": metrics.CPU.Cores,
		},
	}); err != nil {
		return fmt.Errorf("save cpu metric: %w", err)
	}

	// Store per-core CPU metrics
	for _, core := range metrics.CPUPerCore {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "cpu.core_usage",
			Value:      core.UsagePercent,
			Unit:       "percent",
			Metadata: map[string]interface{}{
				"core": core.Core,
			},
		})
	}

	if metrics.CPU.LoadAvg1 > 0 {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "cpu.load1",
			Value:      metrics.CPU.LoadAvg1,
			Unit:       "load",
		})
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "cpu.load5",
			Value:      metrics.CPU.LoadAvg5,
			Unit:       "load",
		})
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "cpu.load15",
			Value:      metrics.CPU.LoadAvg15,
			Unit:       "load",
		})
	}

	// Store memory metrics (extended)
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "memory.usage",
		Value:      metrics.Memory.UsedPercent,
		Unit:       "percent",
		Metadata: map[string]interface{}{
			"total":     metrics.Memory.Total,
			"used":      metrics.Memory.Used,
			"available": metrics.Memory.Available,
			"cached":    metrics.Memory.Cached,
			"buffers":   metrics.Memory.Buffers,
		},
	})

	if metrics.Memory.SwapTotal > 0 {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "memory.swap",
			Value:      metrics.Memory.SwapPercent,
			Unit:       "percent",
			Metadata: map[string]interface{}{
				"total": metrics.Memory.SwapTotal,
				"used":  metrics.Memory.SwapUsed,
			},
		})
	}

	// Store disk metrics (extended)
	for _, disk := range metrics.Disk {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "disk.usage",
			Value:      disk.UsedPercent,
			Unit:       "percent",
			Metadata: map[string]interface{}{
				"device":       disk.Device,
				"mountpoint":   disk.MountPoint,
				"fstype":       disk.FSType,
				"total":        disk.Total,
				"used":         disk.Used,
				"free":         disk.Free,
				"inodes_total": disk.InodesTotal,
				"inodes_used":  disk.InodesUsed,
				"inodes_free":  disk.InodesFree,
			},
		})
	}

	// Store disk I/O metrics
	for _, diskIO := range metrics.DiskIO {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "disk.io",
			Value:      float64(diskIO.ReadBytes + diskIO.WriteBytes),
			Unit:       "bytes",
			Metadata: map[string]interface{}{
				"device":      diskIO.Device,
				"read_bytes":  diskIO.ReadBytes,
				"write_bytes": diskIO.WriteBytes,
				"read_count":  diskIO.ReadCount,
				"write_count": diskIO.WriteCount,
			},
		})
	}

	// Store overall network metrics (extended)
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.bytes_sent",
		Value:      float64(metrics.Network.BytesSent),
		Unit:       "bytes",
	})
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.bytes_recv",
		Value:      float64(metrics.Network.BytesRecv),
		Unit:       "bytes",
	})
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.packets_sent",
		Value:      float64(metrics.Network.PacketsSent),
		Unit:       "packets",
	})
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.packets_recv",
		Value:      float64(metrics.Network.PacketsRecv),
		Unit:       "packets",
	})
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.errors",
		Value:      float64(metrics.Network.ErrorsIn + metrics.Network.ErrorsOut),
		Unit:       "count",
		Metadata: map[string]interface{}{
			"errors_in":  metrics.Network.ErrorsIn,
			"errors_out": metrics.Network.ErrorsOut,
		},
	})
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "network.drops",
		Value:      float64(metrics.Network.DropsIn + metrics.Network.DropsOut),
		Unit:       "count",
		Metadata: map[string]interface{}{
			"drops_in":  metrics.Network.DropsIn,
			"drops_out": metrics.Network.DropsOut,
		},
	})

	// Store per-interface network metrics
	for _, iface := range metrics.NetworkPerIface {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "network.interface",
			Value:      float64(iface.BytesSent + iface.BytesRecv),
			Unit:       "bytes",
			Metadata: map[string]interface{}{
				"interface":    iface.Interface,
				"bytes_sent":   iface.BytesSent,
				"bytes_recv":   iface.BytesRecv,
				"packets_sent": iface.PacketsSent,
				"packets_recv": iface.PacketsRecv,
				"errors_in":    iface.ErrorsIn,
				"errors_out":   iface.ErrorsOut,
				"drops_in":     iface.DropsIn,
				"drops_out":    iface.DropsOut,
			},
		})
	}

	// Store temperature metrics (extended)
	for _, temp := range metrics.Temperature {
		c.storage.SaveMetric(&storage.Metric{
			Timestamp:  metrics.Timestamp,
			MetricType: "temperature",
			Value:      temp.Temperature,
			Unit:       "celsius",
			Metadata: map[string]interface{}{
				"sensor":   temp.Sensor,
				"high":     temp.High,
				"critical": temp.Critical,
			},
		})
	}

	// Store uptime
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "system.uptime",
		Value:      float64(metrics.Uptime),
		Unit:       "seconds",
	})

	// Store process count
	c.storage.SaveMetric(&storage.Metric{
		Timestamp:  metrics.Timestamp,
		MetricType: "system.process_count",
		Value:      float64(metrics.ProcessCount),
		Unit:       "count",
	})

	// Store hardware sensor data
	if metrics.SensorData != nil {
		// Store temperature sensors
		for _, temp := range metrics.SensorData.Temperatures {
			c.storage.SaveMetric(&storage.Metric{
				Timestamp:  metrics.Timestamp,
				MetricType: "sensor.temperature",
				Value:      temp.Current,
				Unit:       "celsius",
				Metadata: map[string]interface{}{
					"name":      temp.Name,
					"component": temp.Component,
					"label":     temp.Label,
					"high":      temp.High,
					"critical":  temp.Critical,
				},
			})
		}

		// Store voltage sensors
		for _, volt := range metrics.SensorData.Voltages {
			c.storage.SaveMetric(&storage.Metric{
				Timestamp:  metrics.Timestamp,
				MetricType: "sensor.voltage",
				Value:      volt.Current,
				Unit:       "volts",
				Metadata: map[string]interface{}{
					"name":    volt.Name,
					"label":   volt.Label,
					"min":     volt.Min,
					"max":     volt.Max,
					"nominal": volt.Nominal,
				},
			})
		}

		// Store fan sensors
		for _, fan := range metrics.SensorData.Fans {
			c.storage.SaveMetric(&storage.Metric{
				Timestamp:  metrics.Timestamp,
				MetricType: "sensor.fan",
				Value:      float64(fan.Current),
				Unit:       "rpm",
				Metadata: map[string]interface{}{
					"name":    fan.Name,
					"label":   fan.Label,
					"min":     fan.Min,
					"max":     fan.Max,
					"percent": fan.Percent,
				},
			})
		}

		// Store power consumption
		if metrics.SensorData.PowerConsumption != nil {
			pc := metrics.SensorData.PowerConsumption
			c.storage.SaveMetric(&storage.Metric{
				Timestamp:  metrics.Timestamp,
				MetricType: "sensor.power.total",
				Value:      pc.TotalWatts,
				Unit:       "watts",
				Metadata: map[string]interface{}{
					"cpu_watts":       pc.CPUWatts,
					"gpu_watts":       pc.GPUWatts,
					"battery_percent": pc.BatteryPercent,
					"battery_status":  pc.BatteryStatus,
					"ac_connected":    pc.ACConnected,
				},
			})

			// Store per-component power
			for component, watts := range pc.ComponentWatts {
				c.storage.SaveMetric(&storage.Metric{
					Timestamp:  metrics.Timestamp,
					MetricType: "sensor.power.component",
					Value:      watts,
					Unit:       "watts",
					Metadata: map[string]interface{}{
						"component": component,
					},
				})
			}
		}

		// Store water cooling info
		if metrics.SensorData.WaterCooling != nil && metrics.SensorData.WaterCooling.Detected {
			wc := metrics.SensorData.WaterCooling
			c.storage.SaveMetric(&storage.Metric{
				Timestamp:  metrics.Timestamp,
				MetricType: "sensor.watercooling",
				Value:      float64(wc.PumpSpeed),
				Unit:       "rpm",
				Metadata: map[string]interface{}{
					"type":          wc.Type,
					"manufacturer":  wc.Manufacturer,
					"model":         wc.Model,
					"coolant_temp":  wc.CoolantTemp,
					"flow_rate":     wc.FlowRate,
				},
			})
		}
	}

	return nil
}

// CollectSystemInfo gathers static system information
func (c *Collector) CollectSystemInfo(agentID string) (*storage.SystemInfo, error) {
	info := &storage.SystemInfo{
		AgentID: agentID,
	}

	if hostInfo, err := host.Info(); err == nil {
		info.Hostname = hostInfo.Hostname
		info.OS = hostInfo.OS
		info.Platform = hostInfo.Platform
		info.Architecture = hostInfo.KernelArch
	}

	if cpuInfo, err := cpu.Info(); err == nil && len(cpuInfo) > 0 {
		info.CPUModel = cpuInfo[0].ModelName
		info.CPUCores = int(cpuInfo[0].Cores)
	}

	if vmStat, err := mem.VirtualMemory(); err == nil {
		info.TotalMemory = vmStat.Total
	}

	info.UpdatedAt = time.Now()
	return info, nil
}

package collector

import (
	"fmt"
	"runtime"
	"time"

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
	Device      string
	ReadBytes   uint64
	WriteBytes  uint64
	ReadCount   uint64
	WriteCount  uint64
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
		storage: store,
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

	// Collect disk I/O statistics
	if diskIO, err := disk.IOCounters(); err == nil {
		for device, stats := range diskIO {
			metrics.DiskIO = append(metrics.DiskIO, DiskIOMetrics{
				Device:     device,
				ReadBytes:  stats.ReadBytes,
				WriteBytes: stats.WriteBytes,
				ReadCount:  stats.ReadCount,
				WriteCount: stats.WriteCount,
			})
		}
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

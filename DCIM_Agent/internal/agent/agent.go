package agent

import (
	"fmt"
	"sync"
	"time"

	"github.com/faberlabs/dicm-agent/internal/alerts"
	"github.com/faberlabs/dicm-agent/internal/anomaly"
	"github.com/faberlabs/dicm-agent/internal/collector"
	"github.com/faberlabs/dicm-agent/internal/config"
	"github.com/faberlabs/dicm-agent/internal/logger"
	"github.com/faberlabs/dicm-agent/internal/rca"
	"github.com/faberlabs/dicm-agent/internal/sender"
	"github.com/faberlabs/dicm-agent/internal/snmp"
	"github.com/faberlabs/dicm-agent/internal/storage"
)

type Agent struct {
	config          *config.Config
	logger          *logger.Logger
	storage         *storage.Storage
	collector       *collector.Collector
	snmpManager     *snmp.Manager
	alertEngine     *alerts.AlertEngine
	anomalyDetector *anomaly.Detector
	rcaAnalyzer     *rca.Analyzer
	sender          *sender.Sender
	stopCh          chan struct{}
	wg              sync.WaitGroup
	running         bool
	runningMutex    sync.Mutex
}

func New(cfg *config.Config, log *logger.Logger) (*Agent, error) {
	// Initialize storage
	store, err := storage.New(cfg.Database.Path)
	if err != nil {
		return nil, fmt.Errorf("initialize storage: %w", err)
	}

	// Create collector
	col := collector.New(store)

	// Create alert engine
	alertEng := alerts.New(cfg.Alerts, store)

	// Create anomaly detector
	anomalyDetectorCfg := &anomaly.Config{
		Enabled:             true,
		SensitivityLevel:    "medium",
		BaselineWindowHours: 24,
		MinDataPoints:       20,
	}
	anomalyDet := anomaly.New(store, anomalyDetectorCfg)

	// Create RCA analyzer
	rcaAnalyzer := rca.New()

	// Create sender
	snd := sender.New(cfg.Server, cfg.Agent.ID, store, log)

	// Create SNMP manager if enabled
	var snmpMgr *snmp.Manager
	if cfg.SNMPManager.Enabled {
		snmpMgr = snmp.NewManager(cfg.SNMPManager, store, log)
		log.Infof("SNMP Manager enabled with %d devices", len(cfg.SNMPManager.Devices))
	}

	agent := &Agent{
		config:          cfg,
		logger:          log,
		storage:         store,
		collector:       col,
		snmpManager:     snmpMgr,
		alertEngine:     alertEng,
		anomalyDetector: anomalyDet,
		rcaAnalyzer:     rcaAnalyzer,
		sender:          snd,
		stopCh:          make(chan struct{}),
	}

	// Collect and store system info on startup
	if err := agent.collectSystemInfo(); err != nil {
		log.Warnf("Failed to collect system info: %v", err)
	}

	return agent, nil
}

func (a *Agent) Run() {
	a.runningMutex.Lock()
	if a.running {
		a.runningMutex.Unlock()
		return
	}
	a.running = true
	a.runningMutex.Unlock()

	a.logger.Infof("Agent starting (ID: %s, Name: %s)", a.config.Agent.ID, a.config.Agent.Name)

	// Start collection loop
	a.wg.Add(1)
	go a.collectionLoop()

	// Start sender loop
	a.wg.Add(1)
	go a.senderLoop()

	// Start cleanup loop
	a.wg.Add(1)
	go a.cleanupLoop()

	// Start SNMP polling loop if enabled
	if a.snmpManager != nil {
		a.wg.Add(1)
		go a.snmpPollLoop()
	}

	a.logger.Infof("Agent running. Collection interval: %v, Send interval: %v",
		a.config.Agent.CollectInterval, a.config.Agent.SendInterval)
}

func (a *Agent) Stop() {
	a.runningMutex.Lock()
	if !a.running {
		a.runningMutex.Unlock()
		return
	}
	a.runningMutex.Unlock()

	a.logger.Infof("Agent stopping...")
	close(a.stopCh)

	// Wait for goroutines with timeout
	done := make(chan struct{})
	go func() {
		a.wg.Wait()
		close(done)
	}()

	// Wait up to 5 seconds for goroutines to finish
	select {
	case <-done:
		a.logger.Infof("All goroutines stopped gracefully")
	case <-time.After(5 * time.Second):
		a.logger.Warnf("Shutdown timeout reached, some goroutines may still be running")
	}

	if err := a.storage.Close(); err != nil {
		a.logger.Errorf("Failed to close storage: %v", err)
	}

	if err := a.logger.Close(); err != nil {
		fmt.Printf("Failed to close logger: %v\n", err)
	}

	a.runningMutex.Lock()
	a.running = false
	a.runningMutex.Unlock()

	a.logger.Infof("Agent stopped")
}

// collectionLoop periodically collects metrics
func (a *Agent) collectionLoop() {
	defer a.wg.Done()

	ticker := time.NewTicker(a.config.Agent.CollectInterval)
	defer ticker.Stop()

	// Collect immediately on startup
	a.collectAndProcess()

	for {
		select {
		case <-ticker.C:
			a.collectAndProcess()
		case <-a.stopCh:
			return
		}
	}
}

// collectAndProcess collects metrics, evaluates alerts, and handles immediate sends
func (a *Agent) collectAndProcess() {
	// Collect metrics
	metrics, err := a.collector.Collect()
	if err != nil {
		a.logger.Errorf("Failed to collect metrics: %v", err)
		return
	}

	// Store metrics
	if err := a.collector.Store(metrics); err != nil {
		a.logger.Errorf("Failed to store metrics: %v", err)
		return
	}

	// Detect anomalies
	anomalies, err := a.anomalyDetector.DetectAnomalies(metrics)
	if err != nil {
		a.logger.Errorf("Failed to detect anomalies: %v", err)
	}

	// Perform root cause analysis
	var rootCauses []*rca.RootCause
	if len(anomalies) > 0 || metrics.Memory.UsedPercent > 85 {
		rootCauses, err = a.rcaAnalyzer.AnalyzeRootCause(anomalies, metrics)
		if err != nil {
			a.logger.Errorf("Failed to analyze root causes: %v", err)
		}
	}

	// Evaluate alerts
	generatedAlerts, err := a.alertEngine.EvaluateMetrics(metrics)
	if err != nil {
		a.logger.Errorf("Failed to evaluate alerts: %v", err)
		return
	}

	// Always show comprehensive system status
	a.logSystemStatus(metrics, generatedAlerts, anomalies, rootCauses)

	// Send critical/warning alerts immediately
	if len(generatedAlerts) > 0 {
		var immediateAlerts []*storage.Alert
		for _, alert := range generatedAlerts {
			if alerts.ShouldSendImmediately(alert) {
				immediateAlerts = append(immediateAlerts, alert)
			}
		}

		if len(immediateAlerts) > 0 {
			// Send in separate goroutine to not block collection
			go func() {
				if err := a.sender.SendAlerts(immediateAlerts); err != nil {
					a.logger.Errorf("Failed to send immediate alerts: %v", err)
					// Alerts remain in database for retry
				}
			}()
		}
	}
}

// logSystemStatus provides comprehensive visibility into system health
func (a *Agent) logSystemStatus(
	metrics *collector.SystemMetrics,
	alerts []*storage.Alert,
	anomalies []*anomaly.Anomaly,
	rootCauses []*rca.RootCause,
) {
	a.logger.Infof("========================================")
	a.logger.Infof("📊 SYSTEM HEALTH REPORT")
	a.logger.Infof("========================================")

	// CPU Status
	cpuStatus := "✅ NORMAL"
	cpuIcon := "✅"
	if metrics.CPU.UsagePercent >= a.config.Alerts.CPU.Critical {
		cpuStatus = "🔥 CRITICAL"
		cpuIcon = "🔥"
	} else if metrics.CPU.UsagePercent >= a.config.Alerts.CPU.Warning {
		cpuStatus = "⚠️  WARNING"
		cpuIcon = "⚠️"
	}
	a.logger.Infof("%s CPU: %.2f%% (%d cores) - %s", cpuIcon, metrics.CPU.UsagePercent, metrics.CPU.Cores, cpuStatus)

	// Show per-core usage
	if len(metrics.CPUPerCore) > 0 {
		coreUsage := "   Cores: "
		for i, core := range metrics.CPUPerCore {
			if i > 0 && i%4 == 0 {
				a.logger.Infof("%s", coreUsage)
				coreUsage = "          "
			}
			coreUsage += fmt.Sprintf("C%d:%.1f%% ", core.Core, core.UsagePercent)
		}
		a.logger.Infof("%s", coreUsage)
	}

	// Memory Status
	memStatus := "✅ NORMAL"
	memIcon := "✅"
	if metrics.Memory.UsedPercent >= a.config.Alerts.Memory.Critical {
		memStatus = "🔥 CRITICAL"
		memIcon = "🔥"
	} else if metrics.Memory.UsedPercent >= a.config.Alerts.Memory.Warning {
		memStatus = "⚠️  WARNING"
		memIcon = "⚠️"
	}
	usedGB := float64(metrics.Memory.Used) / (1024 * 1024 * 1024)
	totalGB := float64(metrics.Memory.Total) / (1024 * 1024 * 1024)
	availGB := float64(metrics.Memory.Available) / (1024 * 1024 * 1024)
	a.logger.Infof("%s Memory: %.2f%% (%.2f GB / %.2f GB, %.2f GB available) - %s",
		memIcon, metrics.Memory.UsedPercent, usedGB, totalGB, availGB, memStatus)

	if metrics.Memory.SwapTotal > 0 {
		swapUsedGB := float64(metrics.Memory.SwapUsed) / (1024 * 1024 * 1024)
		swapTotalGB := float64(metrics.Memory.SwapTotal) / (1024 * 1024 * 1024)
		a.logger.Infof("   Swap: %.2f%% (%.2f GB / %.2f GB)", metrics.Memory.SwapPercent, swapUsedGB, swapTotalGB)
	}

	// Disk Status
	a.logger.Infof("💾 Disk Usage:")
	for _, disk := range metrics.Disk {
		diskStatus := "✅ OK"
		diskIcon := "✅"
		if disk.UsedPercent >= a.config.Alerts.Disk.Critical {
			diskStatus = "🔥 CRITICAL"
			diskIcon = "🔥"
		} else if disk.UsedPercent >= a.config.Alerts.Disk.Warning {
			diskStatus = "⚠️  WARNING"
			diskIcon = "⚠️"
		}
		usedGB := float64(disk.Used) / (1024 * 1024 * 1024)
		totalGB := float64(disk.Total) / (1024 * 1024 * 1024)
		freeGB := float64(disk.Free) / (1024 * 1024 * 1024)
		a.logger.Infof("   %s %s: %.2f%% (%.2f GB / %.2f GB, %.2f GB free) - %s",
			diskIcon, disk.MountPoint, disk.UsedPercent, usedGB, totalGB, freeGB, diskStatus)
	}

	// Disk I/O Performance
	if len(metrics.DiskIO) > 0 {
		a.logger.Infof("💿 Disk I/O Performance:")
		for _, diskIO := range metrics.DiskIO {
			// Always show disk I/O info
			ioIcon := "✅"
			ioStatus := "OK"

			// Check for slow disk (high latency or high utilization)
			if diskIO.AvgLatency > 50 {
				ioIcon = "⚠️"
				ioStatus = "HIGH LATENCY"
			}
			if diskIO.Utilization > 90 {
				ioIcon = "🔥"
				ioStatus = "SATURATED"
			}

			// Show basic I/O stats
			a.logger.Infof("   %s %s: Read: %d ops (%d bytes), Write: %d ops (%d bytes)",
				ioIcon, diskIO.Device, diskIO.ReadCount, diskIO.ReadBytes, diskIO.WriteCount, diskIO.WriteBytes)

			// Show rate-based metrics if available (after first collection)
			if diskIO.IOPS > 0 || diskIO.Throughput > 0 {
				a.logger.Infof("      IOPS: %.0f (R:%.0f W:%.0f), Throughput: %.2f MB/s",
					diskIO.IOPS, diskIO.ReadIOPS, diskIO.WriteIOPS, diskIO.Throughput)
				if diskIO.AvgLatency > 0 {
					a.logger.Infof("      Latency: %.2fms avg, Queue Depth: %.2f, Util: %.1f%% - %s",
						diskIO.AvgLatency, diskIO.QueueDepth, diskIO.Utilization, ioStatus)
				}
			} else {
				a.logger.Infof("      ℹ️  Rate metrics available after next collection cycle")
			}
		}
	}

	// Network Status
	sentMB := float64(metrics.Network.BytesSent) / (1024 * 1024)
	recvMB := float64(metrics.Network.BytesRecv) / (1024 * 1024)
	a.logger.Infof("🌐 Network: ↑ %.2f MB sent, ↓ %.2f MB received", sentMB, recvMB)
	if metrics.Network.ErrorsIn+metrics.Network.ErrorsOut > 0 {
		a.logger.Warnf("   ⚠️  Network Errors: %d in, %d out", metrics.Network.ErrorsIn, metrics.Network.ErrorsOut)
	}
	if metrics.Network.DropsIn+metrics.Network.DropsOut > 0 {
		a.logger.Warnf("   ⚠️  Packet Drops: %d in, %d out", metrics.Network.DropsIn, metrics.Network.DropsOut)
	}

	// Temperature Status
	if len(metrics.Temperature) > 0 {
		a.logger.Infof("🌡️  Temperature:")
		for _, temp := range metrics.Temperature {
			tempStatus := "✅ OK"
			tempIcon := "✅"
			if temp.Temperature >= a.config.Alerts.Temperature.Critical {
				tempStatus = "🔥 CRITICAL"
				tempIcon = "🔥"
			} else if temp.Temperature >= a.config.Alerts.Temperature.Warning {
				tempStatus = "⚠️  HIGH"
				tempIcon = "⚠️"
			}
			a.logger.Infof("   %s %s: %.2f°C - %s", tempIcon, temp.Sensor, temp.Temperature, tempStatus)
		}
	}

	// System Info
	uptimeHours := float64(metrics.Uptime) / 3600
	a.logger.Infof("⏱️  Uptime: %.2f hours | Processes: %d", uptimeHours, metrics.ProcessCount)

	// Phase 1: Network Interface Details
	if len(metrics.NetworkInterfaces) > 0 {
		a.logger.Infof("========================================")
		a.logger.Infof("🔌 NETWORK INTERFACE DETAILS")
		a.logger.Infof("========================================")
		for _, nic := range metrics.NetworkInterfaces {
			if nic.Name == "lo" || nic.Name == "Loopback Pseudo-Interface 1" {
				continue // Skip loopback
			}
			a.logger.Infof("Interface: %s", nic.Name)
			if nic.HardwareAddr != "" {
				a.logger.Infof("   MAC: %s", nic.HardwareAddr)
			}
			if len(nic.IPv4Addresses) > 0 {
				a.logger.Infof("   IPv4: %v", nic.IPv4Addresses)
			}
			if nic.LinkSpeed > 0 {
				a.logger.Infof("   Speed: %d Mbps (%s)", nic.LinkSpeed, nic.LinkSpeedStr)
			}
			if nic.Duplex != "" {
				a.logger.Infof("   Duplex: %s | Auto-Neg: %v", nic.Duplex, nic.AutoNeg)
			}
			if nic.LinkState != "" {
				a.logger.Infof("   Link State: %s", nic.LinkState)
			}
			if nic.SwitchName != "" {
				a.logger.Infof("   🔗 Switch: %s (Port: %s)", nic.SwitchName, nic.SwitchPort)
			}
			if nic.Driver != "" {
				a.logger.Infof("   Driver: %s v%s", nic.Driver, nic.DriverVersion)
			}
			if nic.FirmwareVersion != "" {
				a.logger.Infof("   Firmware: %s", nic.FirmwareVersion)
			}
			a.logger.Infof("")
		}
	}

	// Phase 2: Firmware & Hardware Info
	if metrics.BIOSInfo != nil || metrics.MotherboardInfo != nil {
		a.logger.Infof("========================================")
		a.logger.Infof("💻 HARDWARE & FIRMWARE INFO")
		a.logger.Infof("========================================")
		if metrics.BIOSInfo != nil {
			bios := metrics.BIOSInfo
			a.logger.Infof("BIOS: %s v%s (%s)", bios.Vendor, bios.Version, bios.ReleaseDate)
			if bios.BIOSMode != "" {
				a.logger.Infof("   Mode: %s | Secure Boot: %s", bios.BIOSMode, bios.SecureBootState)
			}
		}
		if metrics.MotherboardInfo != nil {
			mobo := metrics.MotherboardInfo
			a.logger.Infof("Motherboard: %s %s", mobo.Manufacturer, mobo.Product)
			if mobo.UUID != "" {
				a.logger.Infof("   UUID: %s", mobo.UUID)
			}
		}
		if len(metrics.ComponentFirmware) > 0 {
			a.logger.Infof("Component Firmware:")
			for _, fw := range metrics.ComponentFirmware {
				a.logger.Infof("   %s %s: %s", fw.Component, fw.Model, fw.Firmware)
			}
		}
		a.logger.Infof("")
	}

	// Phase 3: OS Patch Level
	if metrics.PatchLevel != nil {
		a.logger.Infof("========================================")
		a.logger.Infof("🔧 OS PATCH LEVEL")
		a.logger.Infof("========================================")
		patch := metrics.PatchLevel
		a.logger.Infof("OS: %s %s (Build: %s)", patch.OSName, patch.Version, patch.BuildNumber)

		if patch.Windows != nil {
			a.logger.Infof("Windows Edition: %s", patch.Windows.OSEdition)
			a.logger.Infof("Updates Installed: %d", patch.Windows.UpdateCount)
			if len(patch.Windows.Updates) > 0 {
				a.logger.Infof("Recent Updates:")
				maxShow := 5
				if len(patch.Windows.Updates) < maxShow {
					maxShow = len(patch.Windows.Updates)
				}
				for i := 0; i < maxShow; i++ {
					update := patch.Windows.Updates[i]
					a.logger.Infof("   - %s (%s)", update.HotFixID, update.InstalledOn)
				}
			}
		}

		if patch.Linux != nil {
			a.logger.Infof("Distribution: %s %s", patch.Linux.Distribution, patch.Linux.DistroVersion)
			a.logger.Infof("Kernel: %s", patch.Linux.KernelVersion)
			a.logger.Infof("Package Manager: %s", patch.Linux.PackageManager)
			a.logger.Infof("Packages Installed: %d", len(patch.Linux.Packages))
			if patch.Linux.UpdatesAvailable > 0 {
				a.logger.Infof("⚠️  Updates Available: %d (Security: %d)",
					patch.Linux.UpdatesAvailable, patch.Linux.SecurityUpdates)
			}
		}
		a.logger.Infof("")
	}

	// Phase 4: Hardware Sensors
	if metrics.SensorData != nil {
		a.logger.Infof("========================================")
		a.logger.Infof("🔬 HARDWARE SENSORS")
		a.logger.Infof("========================================")

		// Temperature Sensors
		a.logger.Infof("🌡️  Temperatures:")
		if len(metrics.SensorData.Temperatures) > 0 {
			foundRealSensor := false
			for _, temp := range metrics.SensorData.Temperatures {
				if temp.Current == 0 && temp.Label != "" && temp.Name != "" {
					// This is a note/message about sensor availability
					a.logger.Infof("   ℹ️  %s", temp.Label)
					continue
				}
				foundRealSensor = true
				tempIcon := "✅"
				tempStatus := "OK"
				if temp.Critical > 0 && temp.Current >= temp.Critical {
					tempIcon = "🔥"
					tempStatus = "CRITICAL"
				} else if temp.High > 0 && temp.Current >= temp.High {
					tempIcon = "⚠️"
					tempStatus = "HIGH"
				}
				thresholds := ""
				if temp.High > 0 {
					thresholds = fmt.Sprintf(" (High: %.1f°C", temp.High)
					if temp.Critical > 0 {
						thresholds += fmt.Sprintf(", Crit: %.1f°C)", temp.Critical)
					} else {
						thresholds += ")"
					}
				}
				a.logger.Infof("   %s [%s] %s: %.1f°C - %s%s",
					tempIcon, temp.Component, temp.Name, temp.Current, tempStatus, thresholds)
			}
			if !foundRealSensor {
				a.logger.Infof("   ℹ️  Temperature sensor feature available but no sensors detected")
			}
		} else {
			a.logger.Infof("   ℹ️  Temperature sensor feature available but no sensors detected")
		}

		// Voltage Sensors
		a.logger.Infof("⚡ Voltages:")
		if len(metrics.SensorData.Voltages) > 0 {
			foundRealSensor := false
			for _, volt := range metrics.SensorData.Voltages {
				if volt.Current == 0 && volt.Label != "" {
					// This is a note/message about sensor availability
					a.logger.Infof("   ℹ️  %s", volt.Label)
					continue
				}
				foundRealSensor = true
				voltIcon := "✅"
				nominal := ""
				if volt.Nominal > 0 {
					deviation := ((volt.Current - volt.Nominal) / volt.Nominal) * 100
					if deviation > 5 || deviation < -5 {
						voltIcon = "⚠️"
					}
					nominal = fmt.Sprintf(" (Nominal: %.2fV, Dev: %+.1f%%)", volt.Nominal, deviation)
				}
				a.logger.Infof("   %s %s: %.2fV%s", voltIcon, volt.Name, volt.Current, nominal)
			}
			if !foundRealSensor {
				a.logger.Infof("   ℹ️  Voltage sensor feature available but no sensors detected")
			}
		} else {
			a.logger.Infof("   ℹ️  Voltage sensor feature available but no sensors detected")
		}

		// Fan Sensors
		a.logger.Infof("💨 Fans:")
		if len(metrics.SensorData.Fans) > 0 {
			foundRealSensor := false
			for _, fan := range metrics.SensorData.Fans {
				if fan.Current == 0 && fan.Label != "" {
					// This is a note/message about sensor availability
					a.logger.Infof("   ℹ️  %s", fan.Label)
					continue
				}
				foundRealSensor = true
				fanIcon := "✅"
				fanStatus := "OK"
				if fan.Min > 0 && fan.Current < fan.Min {
					fanIcon = "⚠️"
					fanStatus = "LOW"
				}
				percent := ""
				if fan.Percent > 0 {
					percent = fmt.Sprintf(" (%d%%)", fan.Percent)
				}
				a.logger.Infof("   %s %s: %d RPM%s - %s", fanIcon, fan.Name, fan.Current, percent, fanStatus)
			}
			if !foundRealSensor {
				a.logger.Infof("   ℹ️  Fan sensor feature available but no sensors detected")
			}
		} else {
			a.logger.Infof("   ℹ️  Fan sensor feature available but no sensors detected")
		}

		// Power Supply Info
		a.logger.Infof("🔌 Power Supply:")
		if metrics.SensorData.PowerSupply != nil {
			ps := metrics.SensorData.PowerSupply
			hasInfo := false
			if ps.Manufacturer != "" && ps.Manufacturer != "Unknown" {
				a.logger.Infof("   Manufacturer: %s", ps.Manufacturer)
				hasInfo = true
			}
			if ps.Model != "" && ps.Model != "Unknown" && ps.Model != "Check system documentation" && ps.Model != "Check System Information" {
				a.logger.Infof("   Model: %s", ps.Model)
				hasInfo = true
			}
			if ps.MaxPower > 0 {
				a.logger.Infof("   Max Power: %d W", ps.MaxPower)
				hasInfo = true
			}
			if ps.Efficiency != "" {
				a.logger.Infof("   Efficiency: %s", ps.Efficiency)
				hasInfo = true
			}
			if ps.Status != "" && ps.Status != "Unknown" {
				statusIcon := "✅"
				if ps.Status == "Warning" {
					statusIcon = "⚠️"
				} else if ps.Status == "Critical" {
					statusIcon = "🔥"
				}
				a.logger.Infof("   Status: %s %s", statusIcon, ps.Status)
				hasInfo = true
			}
			if !hasInfo {
				a.logger.Infof("   ℹ️  Power supply feature available but detailed info not accessible")
			}
		} else {
			a.logger.Infof("   ℹ️  Power supply feature available but no info detected")
		}

		// Power Consumption
		a.logger.Infof("⚡ Power Consumption:")
		if metrics.SensorData.PowerConsumption != nil {
			pc := metrics.SensorData.PowerConsumption
			hasData := false
			if pc.TotalWatts > 0 {
				a.logger.Infof("   Total: %.1f W", pc.TotalWatts)
				hasData = true
			}
			if pc.CPUWatts > 0 {
				a.logger.Infof("   CPU: %.1f W", pc.CPUWatts)
				hasData = true
			}
			if pc.GPUWatts > 0 {
				a.logger.Infof("   GPU: %.1f W", pc.GPUWatts)
				hasData = true
			}
			if len(pc.ComponentWatts) > 0 {
				for component, watts := range pc.ComponentWatts {
					if watts > 0 {
						a.logger.Infof("   %s: %.1f W", component, watts)
						hasData = true
					}
				}
			}
			if pc.BatteryPercent > 0 {
				batteryIcon := "🔋"
				if pc.BatteryPercent < 20 {
					batteryIcon = "🪫"
				}
				acStatus := ""
				if pc.ACConnected {
					acStatus = " (AC Connected)"
				}
				a.logger.Infof("   %s Battery: %d%% - %s%s",
					batteryIcon, pc.BatteryPercent, pc.BatteryStatus, acStatus)
				hasData = true
			} else if !pc.ACConnected {
				a.logger.Infof("   ⚠️  AC Not Connected")
				hasData = true
			}
			if !hasData {
				a.logger.Infof("   ℹ️  Power consumption feature available but no data detected (desktop system)")
			}
		} else {
			a.logger.Infof("   ℹ️  Power consumption feature available but no data detected")
		}

		// Water Cooling
		a.logger.Infof("💧 Water Cooling / AIO:")
		if metrics.SensorData.WaterCooling != nil && metrics.SensorData.WaterCooling.Detected {
			wc := metrics.SensorData.WaterCooling
			a.logger.Infof("   ✅ Detected: %s", wc.Type)
			if wc.Manufacturer != "" {
				a.logger.Infof("   Manufacturer: %s", wc.Manufacturer)
			}
			if wc.Model != "" {
				a.logger.Infof("   Model: %s", wc.Model)
			}
			if wc.PumpSpeed > 0 {
				a.logger.Infof("   Pump Speed: %d RPM", wc.PumpSpeed)
			}
			if wc.CoolantTemp > 0 {
				a.logger.Infof("   Coolant Temp: %.1f°C", wc.CoolantTemp)
			}
			if wc.FlowRate > 0 {
				a.logger.Infof("   Flow Rate: %.2f L/min", wc.FlowRate)
			}
		} else {
			a.logger.Infof("   ℹ️  No water cooling or AIO detected (air cooling in use)")
		}

		a.logger.Infof("")
	}

	// Phase 5: Hyper-V Monitoring
	if metrics.HyperVInfo != nil && metrics.HyperVInfo.Enabled {
		a.logger.Infof("========================================")
		a.logger.Infof("🖥️  HYPER-V VIRTUAL MACHINES")
		a.logger.Infof("========================================")
		hv := metrics.HyperVInfo
		a.logger.Infof("Host: %s | Total VMs: %d (Running: %d, Stopped: %d)",
			hv.HostName, hv.TotalVMs, hv.RunningVMs, hv.StoppedVMs)
		a.logger.Infof("Host Resources: %d CPUs, %.1f GB RAM (%.1f GB free)",
			hv.LogicalProcessors,
			float64(hv.TotalMemoryMB)/1024,
			float64(hv.FreeMemoryMB)/1024)

		if len(hv.VirtualMachines) > 0 {
			a.logger.Infof("Virtual Machines:")
			for _, vm := range hv.VirtualMachines {
				vmIcon := "✅"
				if vm.State != "Running" {
					vmIcon = "⏸️"
				}
				a.logger.Infof("   %s %s: %s", vmIcon, vm.Name, vm.State)
				if vm.State == "Running" {
					a.logger.Infof("      CPU: %d%%, Memory: %d MB (Demand: %d MB), Uptime: %d sec",
						vm.CPUUsage, vm.MemoryAssigned, vm.MemoryDemand, vm.Uptime)
					if vm.Heartbeat != "" {
						a.logger.Infof("      Heartbeat: %s, Status: %s", vm.Heartbeat, vm.Status)
					}
				}
			}
		}
		a.logger.Infof("")
	}

	// Anomaly Detection Results
	a.logger.Infof("========================================")
	a.logger.Infof("🔍 ANOMALY DETECTION")
	a.logger.Infof("========================================")
	if len(anomalies) > 0 {
		a.logger.Warnf("⚠️  %d ANOMALIES DETECTED:", len(anomalies))
		for i, anom := range anomalies {
			icon := "ℹ️"
			if anom.Severity == "CRITICAL" {
				icon = "🔥"
			} else if anom.Severity == "HIGH" {
				icon = "⚠️"
			} else if anom.Severity == "MEDIUM" {
				icon = "⚠️"
			}
			a.logger.Warnf("%d. %s [%s] %s", i+1, icon, anom.Severity, anom.Description)
			a.logger.Warnf("   Value: %.2f (Expected: %.2f ± %.2f std dev)",
				anom.Value, anom.Expected, anom.Deviation)
		}
	} else {
		// Show baseline learning status
		baselines := a.anomalyDetector.GetBaselines()
		if len(baselines) == 0 {
			a.logger.Infof("ℹ️  Establishing statistical baselines (learning mode)")
			a.logger.Infof("   Anomaly detection will be active after 20+ data points")
		} else {
			// Count how many baselines have enough data
			readyCount := 0
			for _, baseline := range baselines {
				if baseline.DataPoints >= 20 {
					readyCount++
				}
			}
			if readyCount < len(baselines) {
				a.logger.Infof("ℹ️  Building baselines: %d/%d metrics ready", readyCount, len(baselines))
				a.logger.Infof("   Collecting data for anomaly detection...")
			} else {
				a.logger.Infof("✅ No anomalies detected - All metrics within normal ranges")
			}
		}
	}
	a.logger.Infof("")

	// Root Cause Analysis Results
	a.logger.Infof("========================================")
	a.logger.Infof("🔬 ROOT CAUSE ANALYSIS")
	a.logger.Infof("========================================")
	if len(rootCauses) > 0 {
		a.logger.Warnf("⚠️  %d ISSUES IDENTIFIED:", len(rootCauses))
		for i, cause := range rootCauses {
			confidenceIcon := "💡"
			if cause.Confidence >= 0.9 {
				confidenceIcon = "🎯"
			}
			a.logger.Warnf("%d. %s %s (Confidence: %.0f%%)",
				i+1, confidenceIcon, cause.Issue, cause.Confidence*100)
			a.logger.Warnf("   %s", cause.Description)

			if len(cause.PossibleReasons) > 0 {
				a.logger.Infof("   Possible Reasons:")
				for _, reason := range cause.PossibleReasons {
					a.logger.Infof("     - %s", reason)
				}
			}

			if len(cause.Recommendations) > 0 {
				a.logger.Infof("   Recommendations:")
				maxRecs := 3
				if len(cause.Recommendations) < maxRecs {
					maxRecs = len(cause.Recommendations)
				}
				for j := 0; j < maxRecs; j++ {
					a.logger.Infof("     • %s", cause.Recommendations[j])
				}
				if len(cause.Recommendations) > maxRecs {
					a.logger.Infof("     ... and %d more", len(cause.Recommendations)-maxRecs)
				}
			}
			a.logger.Infof("")
		}
	} else {
		a.logger.Infof("✅ No critical issues identified - System operating normally")
		a.logger.Infof("   RCA actively monitoring metric correlations")
	}
	a.logger.Infof("")

	// Alert Summary
	if len(alerts) > 0 {
		a.logger.Warnf("========================================")
		a.logger.Warnf("🚨 ACTIVE ALERTS: %d", len(alerts))
		a.logger.Warnf("========================================")
		for i, alert := range alerts {
			icon := "ℹ️"
			if alert.Severity == "WARNING" {
				icon = "⚠️"
			} else if alert.Severity == "CRITICAL" {
				icon = "🔥"
			}
			a.logger.Warnf("%d. %s [%s] %s", i+1, icon, alert.Severity, alert.Message)
		}
		a.logger.Warnf("========================================")
	} else {
		a.logger.Infof("========================================")
		a.logger.Infof("✅ ALL SYSTEMS NORMAL - No Active Alerts")
		a.logger.Infof("========================================")
	}
	a.logger.Infof("")
}

// senderLoop periodically sends batched metrics and retries failed sends
func (a *Agent) senderLoop() {
	defer a.wg.Done()

	ticker := time.NewTicker(a.config.Agent.SendInterval)
	defer ticker.Stop()

	// Send queued data immediately on startup (to catch up after restart)
	a.processQueue()

	for {
		select {
		case <-ticker.C:
			a.processQueue()
		case <-a.stopCh:
			return
		}
	}
}

// processQueue sends unsent data to the server
func (a *Agent) processQueue() {
	if err := a.sender.ProcessQueue(a.config.Agent.BatchSize); err != nil {
		a.logger.Errorf("Failed to process send queue: %v", err)
	}
}

// cleanupLoop periodically cleans up old data
func (a *Agent) cleanupLoop() {
	defer a.wg.Done()

	ticker := time.NewTicker(24 * time.Hour) // Run daily
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			a.logger.Infof("Running data cleanup (retention: %d days)", a.config.Database.RetentionDays)
			if err := a.storage.CleanupOldData(a.config.Database.RetentionDays); err != nil {
				a.logger.Errorf("Failed to cleanup old data: %v", err)
			}
		case <-a.stopCh:
			return
		}
	}
}

// collectSystemInfo gathers and stores system information
func (a *Agent) collectSystemInfo() error {
	info, err := a.collector.CollectSystemInfo(a.config.Agent.ID)
	if err != nil {
		return err
	}

	if err := a.storage.SaveSystemInfo(info); err != nil {
		return err
	}

	a.logger.Infof("System info: %s (%s %s) - %d cores, %d MB RAM",
		info.Hostname, info.OS, info.Architecture, info.CPUCores, info.TotalMemory/(1024*1024))

	return nil
}

// snmpPollLoop periodically polls SNMP devices
func (a *Agent) snmpPollLoop() {
	defer a.wg.Done()

	ticker := time.NewTicker(a.config.SNMPManager.PollInterval)
	defer ticker.Stop()

	// Poll immediately on startup
	a.logger.Infof("Starting SNMP polling (interval: %v)", a.config.SNMPManager.PollInterval)
	a.snmpManager.PollDevices()

	for {
		select {
		case <-ticker.C:
			a.snmpManager.PollDevices()
		case <-a.stopCh:
			a.logger.Infof("SNMP polling stopped")
			return
		}
	}
}

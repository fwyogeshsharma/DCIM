package agent

import (
	"fmt"
	"sync"
	"time"

	"github.com/faber/network-monitor-agent/internal/alerts"
	"github.com/faber/network-monitor-agent/internal/collector"
	"github.com/faber/network-monitor-agent/internal/config"
	"github.com/faber/network-monitor-agent/internal/logger"
	"github.com/faber/network-monitor-agent/internal/sender"
	"github.com/faber/network-monitor-agent/internal/snmp"
	"github.com/faber/network-monitor-agent/internal/storage"
)

type Agent struct {
	config       *config.Config
	logger       *logger.Logger
	storage      *storage.Storage
	collector    *collector.Collector
	snmpManager  *snmp.Manager
	alertEngine  *alerts.AlertEngine
	sender       *sender.Sender
	stopCh       chan struct{}
	wg           sync.WaitGroup
	running      bool
	runningMutex sync.Mutex
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

	// Create sender
	snd := sender.New(cfg.Server, cfg.Agent.ID, store, log)

	// Create SNMP manager if enabled
	var snmpMgr *snmp.Manager
	if cfg.SNMPManager.Enabled {
		snmpMgr = snmp.NewManager(cfg.SNMPManager, store, log)
		log.Infof("SNMP Manager enabled with %d devices", len(cfg.SNMPManager.Devices))
	}

	agent := &Agent{
		config:      cfg,
		logger:      log,
		storage:     store,
		collector:   col,
		snmpManager: snmpMgr,
		alertEngine: alertEng,
		sender:      snd,
		stopCh:      make(chan struct{}),
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
	a.wg.Wait()

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

	// Evaluate alerts
	generatedAlerts, err := a.alertEngine.EvaluateMetrics(metrics)
	if err != nil {
		a.logger.Errorf("Failed to evaluate alerts: %v", err)
		return
	}

	// Always show comprehensive system status
	a.logSystemStatus(metrics, generatedAlerts)

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
func (a *Agent) logSystemStatus(metrics *collector.SystemMetrics, alerts []*storage.Alert) {
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

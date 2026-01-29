package snmp

import (
	"fmt"
	"time"

	"github.com/faber/network-monitor-agent/internal/config"
	"github.com/faber/network-monitor-agent/internal/logger"
	"github.com/faber/network-monitor-agent/internal/storage"
	"github.com/gosnmp/gosnmp"
)

type Manager struct {
	config  config.SNMPManagerConfig
	storage *storage.Storage
	logger  *logger.Logger
}

func NewManager(cfg config.SNMPManagerConfig, store *storage.Storage, log *logger.Logger) *Manager {
	return &Manager{
		config:  cfg,
		storage: store,
		logger:  log,
	}
}

// PollDevices queries all configured SNMP devices
func (m *Manager) PollDevices() error {
	m.logger.Infof("========================================")
	m.logger.Infof("📡 SNMP DEVICE MONITORING")
	m.logger.Infof("========================================")
	m.logger.Infof("Polling %d SNMP devices...", len(m.config.Devices))
	m.logger.Infof("")

	successCount := 0
	failCount := 0
	deviceReports := make(map[string]*deviceReport)

	for _, deviceCfg := range m.config.Devices {
		report := &deviceReport{
			name:    deviceCfg.Name,
			host:    deviceCfg.Host,
			metrics: make(map[string]float64),
		}
		deviceReports[deviceCfg.Name] = report

		if err := m.pollDeviceWithReport(deviceCfg, report); err != nil {
			m.logger.Errorf("❌ SNMP: Device '%s' (%s) - %v", deviceCfg.Name, deviceCfg.Host, err)
			report.failed = true
			report.error = err.Error()
			failCount++
			continue
		}
		successCount++
	}

	// Display comprehensive reports for each device
	for _, deviceCfg := range m.config.Devices {
		report := deviceReports[deviceCfg.Name]
		m.displayDeviceReport(report)
	}

	m.logger.Infof("========================================")
	if successCount > 0 {
		m.logger.Infof("✅ SNMP: Successfully polled %d/%d devices", successCount, len(m.config.Devices))
	}
	if failCount > 0 {
		m.logger.Warnf("⚠️  SNMP: Failed to poll %d/%d devices", failCount, len(m.config.Devices))
	}
	m.logger.Infof("========================================")
	m.logger.Infof("")

	return nil
}

func (m *Manager) pollDevice(deviceCfg config.SNMPDeviceConfig) error {
	report := &deviceReport{
		name:    deviceCfg.Name,
		host:    deviceCfg.Host,
		metrics: make(map[string]float64),
	}
	return m.pollDeviceWithReport(deviceCfg, report)
}

func (m *Manager) pollDeviceWithReport(deviceCfg config.SNMPDeviceConfig, report *deviceReport) error {
	// Create SNMP connection
	snmpClient := &gosnmp.GoSNMP{
		Target:    deviceCfg.Host,
		Port:      deviceCfg.Port,
		Community: deviceCfg.Community,
		Version:   m.parseVersion(deviceCfg.Version),
		Timeout:   deviceCfg.Timeout,
		Retries:   deviceCfg.Retries,
	}

	err := snmpClient.Connect()
	if err != nil {
		return fmt.Errorf("connection failed: %w", err)
	}
	defer snmpClient.Conn.Close()

	timestamp := time.Now()
	metricsCollected := 0

	// Query each OID
	for _, oidCfg := range deviceCfg.OIDs {
		result, err := snmpClient.Get([]string{oidCfg.OID})
		if err != nil {
			m.logger.Debugf("⚠️  SNMP: Device '%s' - Failed to get %s: %v", deviceCfg.Name, oidCfg.Name, err)
			continue
		}

		for _, variable := range result.Variables {
			// Check for SNMP error types (NoSuchObject, NoSuchInstance, etc.)
			if variable.Type == gosnmp.NoSuchObject {
				m.logger.Debugf("⚠️  SNMP: Device '%s' - OID %s (%s) not available (NoSuchObject)",
					deviceCfg.Name, oidCfg.Name, oidCfg.OID)
				continue
			}
			if variable.Type == gosnmp.NoSuchInstance {
				m.logger.Debugf("⚠️  SNMP: Device '%s' - OID %s (%s) not available (NoSuchInstance)",
					deviceCfg.Name, oidCfg.Name, oidCfg.OID)
				continue
			}
			if variable.Type == gosnmp.EndOfMibView {
				m.logger.Debugf("⚠️  SNMP: Device '%s' - OID %s (%s) not available (EndOfMibView)",
					deviceCfg.Name, oidCfg.Name, oidCfg.OID)
				continue
			}

			value := m.convertValue(variable)

			// Skip if value is 0 and type suggests it might be invalid
			// This catches cases where convertValue returns 0 for unsupported types
			if value == 0 && (variable.Type != gosnmp.Integer &&
				variable.Type != gosnmp.Gauge32 &&
				variable.Type != gosnmp.Counter32 &&
				variable.Type != gosnmp.Counter64) {
				m.logger.Debugf("⚠️  SNMP: Device '%s' - Skipping %s: unsupported type %v returned 0",
					deviceCfg.Name, oidCfg.Name, variable.Type)
				continue
			}

			report.metrics[oidCfg.Name] = value

			// Store in database
			metric := &storage.SNMPMetric{
				Timestamp:  timestamp,
				DeviceName: deviceCfg.Name,
				DeviceHost: deviceCfg.Host,
				OID:        oidCfg.OID,
				MetricName: oidCfg.Name,
				Value:      value,
				ValueType:  oidCfg.Type,
				CreatedAt:  time.Now(),
			}

			if err := m.storage.SaveSNMPMetric(metric); err != nil {
				m.logger.Errorf("❌ SNMP: Failed to save metric: %v", err)
			} else {
				metricsCollected++
			}
		}
	}

	report.metricsCollected = metricsCollected
	return nil
}

func (m *Manager) displayDeviceReport(report *deviceReport) {
	if report.failed {
		m.logger.Errorf("----------------------------------------")
		m.logger.Errorf("❌ Device: %s (%s) - FAILED", report.name, report.host)
		m.logger.Errorf("   Error: %s", report.error)
		m.logger.Errorf("----------------------------------------")
		return
	}

	m.logger.Infof("----------------------------------------")
	m.logger.Infof("✅ Device: %s (%s)", report.name, report.host)
	m.logger.Infof("----------------------------------------")

	// CPU Status
	if cpuIdle, ok := report.metrics["cpu_idle_percent"]; ok {
		cpuUser, hasUser := report.metrics["cpu_user_percent"]
		cpuSystem, hasSystem := report.metrics["cpu_system_percent"]
		cpuUsage := 100 - cpuIdle

		// Detect if values look incorrect (known SNMP bug: idle=0, system>90, user=0)
		// This is a known bug in UCD-SNMP-MIB on many Ubuntu systems
		looksIncorrect := (cpuIdle == 0 && hasSystem && cpuSystem > 90 && hasUser && cpuUser == 0)

		if !looksIncorrect {
			// Values look reasonable, display CPU with normal threshold logic
			cpuStatus := "✅ NORMAL"
			cpuIcon := "✅"
			if cpuUsage >= 95 {
				cpuStatus = "🔥 CRITICAL"
				cpuIcon = "🔥"
			} else if cpuUsage >= 80 {
				cpuStatus = "⚠️  WARNING"
				cpuIcon = "⚠️"
			}
			m.logger.Infof("%s CPU: %.2f%% usage (%.2f%% idle) - %s", cpuIcon, cpuUsage, cpuIdle, cpuStatus)

			if hasUser && hasSystem {
				m.logger.Infof("   User: %.2f%%, System: %.2f%%", cpuUser, cpuSystem)
			}
		}
		// If values look wrong, skip displaying CPU entirely - Load Average shows actual CPU load
	}

	// Memory Status
	if memTotal, ok := report.metrics["memory_total_kb"]; ok {
		if memAvail, ok := report.metrics["memory_available_kb"]; ok {
			memUsed := memTotal - memAvail
			memUsagePercent := (memUsed / memTotal) * 100

			memStatus := "✅ NORMAL"
			memIcon := "✅"
			if memUsagePercent >= 95 {
				memStatus = "🔥 CRITICAL"
				memIcon = "🔥"
			} else if memUsagePercent >= 85 {
				memStatus = "⚠️  WARNING"
				memIcon = "⚠️"
			}

			memTotalGB := memTotal / (1024 * 1024)
			memUsedGB := memUsed / (1024 * 1024)
			memAvailGB := memAvail / (1024 * 1024)

			m.logger.Infof("%s Memory: %.2f%% (%.2f GB / %.2f GB, %.2f GB available) - %s",
				memIcon, memUsagePercent, memUsedGB, memTotalGB, memAvailGB, memStatus)
		}
	}

	// Load Average
	if load1, ok := report.metrics["load_avg_1min"]; ok {
		loadStatus := "✅ OK"
		loadIcon := "✅"
		if load1 >= 8 {
			loadStatus = "🔥 HIGH"
			loadIcon = "🔥"
		} else if load1 >= 4 {
			loadStatus = "⚠️  ELEVATED"
			loadIcon = "⚠️"
		}

		if load5, ok := report.metrics["load_avg_5min"]; ok {
			if load15, ok := report.metrics["load_avg_15min"]; ok {
				m.logger.Infof("%s Load: %.2f (1m), %.2f (5m), %.2f (15m) - %s",
					loadIcon, load1, load5, load15, loadStatus)
			}
		}
	}

	// Disk Status
	if diskTotal, ok := report.metrics["disk_root_total_kb"]; ok {
		if diskPercent, ok := report.metrics["disk_root_percent_used"]; ok {
			diskStatus := "✅ OK"
			diskIcon := "✅"
			if diskPercent >= 95 {
				diskStatus = "🔥 CRITICAL"
				diskIcon = "🔥"
			} else if diskPercent >= 85 {
				diskStatus = "⚠️  WARNING"
				diskIcon = "⚠️"
			}

			diskTotalGB := diskTotal / (1024 * 1024)
			diskUsed := diskTotal * diskPercent / 100
			diskUsedGB := diskUsed / (1024 * 1024)

			if diskAvail, ok := report.metrics["disk_root_available_kb"]; ok {
				diskAvailGB := diskAvail / (1024 * 1024)
				m.logger.Infof("%s Disk /: %.2f%% (%.2f GB / %.2f GB, %.2f GB free) - %s",
					diskIcon, diskPercent, diskUsedGB, diskTotalGB, diskAvailGB, diskStatus)
			}
		}
	}

	// Network Interface
	if ifOctetsIn, ok := report.metrics["interface1_octets_in"]; ok {
		if ifOctetsOut, ok := report.metrics["interface1_octets_out"]; ok {
			inMB := ifOctetsIn / (1024 * 1024)
			outMB := ifOctetsOut / (1024 * 1024)
			m.logger.Infof("🌐 Network: ↓ %.2f MB received, ↑ %.2f MB sent", inMB, outMB)

			if ifErrorsIn, ok := report.metrics["interface1_errors_in"]; ok {
				if ifErrorsOut, ok := report.metrics["interface1_errors_out"]; ok {
					if ifErrorsIn > 0 || ifErrorsOut > 0 {
						m.logger.Warnf("   ⚠️  Errors: %.0f in, %.0f out", ifErrorsIn, ifErrorsOut)
					}
				}
			}
		}
	}

	// TCP Connections
	if tcpEstablished, ok := report.metrics["tcp_current_established"]; ok {
		connIcon := "✅"
		connStatus := "OK"
		if tcpEstablished >= 1000 {
			connIcon = "⚠️"
			connStatus = "HIGH"
		}
		m.logger.Infof("%s TCP Connections: %.0f established - %s", connIcon, tcpEstablished, connStatus)

		if tcpRetrans, ok := report.metrics["tcp_retransmitted_segments"]; ok {
			if tcpRetrans > 1000 {
				m.logger.Warnf("   ⚠️  TCP Retransmissions: %.0f (may indicate network issues)", tcpRetrans)
			}
		}
	}

	// System Info
	if uptime, ok := report.metrics["system_uptime_ticks"]; ok {
		uptimeSeconds := uptime / 100 // Timeticks are in 1/100th seconds
		uptimeHours := uptimeSeconds / 3600
		uptimeDays := uptimeHours / 24
		m.logger.Infof("⏱️  Uptime: %.2f hours (%.2f days)", uptimeHours, uptimeDays)
	}

	if processes, ok := report.metrics["system_processes_running"]; ok {
		if users, ok := report.metrics["system_users_logged_in"]; ok {
			m.logger.Infof("👥 Users: %.0f logged in | Processes: %.0f running", users, processes)
		}
	}

	// Temperature Sensors
	tempSensors := []struct {
		valueKey string
		nameKey  string
		num      int
	}{
		{"temp_sensor1_value", "temp_sensor1_name", 1},
		{"temp_sensor2_value", "temp_sensor2_name", 2},
		{"temp_sensor3_value", "temp_sensor3_name", 3},
		{"temp_sensor4_value", "temp_sensor4_name", 4},
	}

	hasTempData := false
	for _, sensor := range tempSensors {
		if tempValue, ok := report.metrics[sensor.valueKey]; ok {
			if !hasTempData {
				m.logger.Infof("🌡️  Temperature Sensors:")
				hasTempData = true
			}

			// Convert from millidegrees to degrees if value is > 200
			// (LM-SENSORS-MIB reports in millidegrees Celsius)
			if tempValue > 200 {
				tempValue = tempValue / 1000
			}

			tempStatus := "✅ OK"
			tempIcon := "✅"
			if tempValue >= 85 {
				tempStatus = "🔥 CRITICAL"
				tempIcon = "🔥"
			} else if tempValue >= 75 {
				tempStatus = "⚠️  HIGH"
				tempIcon = "⚠️"
			}

			sensorName := fmt.Sprintf("Sensor %d", sensor.num)
			// Note: sensor names are strings and can't be easily accessed as float64
			// If you need the actual sensor name, you'll need to handle string OIDs separately
			m.logger.Infof("   %s %s: %.1f°C - %s", tempIcon, sensorName, tempValue, tempStatus)
		}
	}

	m.logger.Infof("📊 Total: %d metrics collected", report.metricsCollected)

	// Warn if very few metrics collected (indicates SNMP not fully configured)
	if report.metricsCollected < 10 {
		m.logger.Warnf("")
		m.logger.Warnf("⚠️  NOTE: Only %d metrics collected from %s", report.metricsCollected, report.name)
		m.logger.Warnf("   Extended metrics (CPU, Memory, Disk) require UCD-SNMP-MIB configuration.")
		m.logger.Warnf("   See SNMP_UBUNTU_SETUP.md or snmpd.conf.ubuntu for setup instructions.")
		m.logger.Warnf("")
	} else {
		m.logger.Infof("")
	}
}

type deviceReport struct {
	name             string
	host             string
	metrics          map[string]float64
	metricsCollected int
	failed           bool
	error            string
}

func (m *Manager) parseVersion(version string) gosnmp.SnmpVersion {
	switch version {
	case "1":
		return gosnmp.Version1
	case "2c":
		return gosnmp.Version2c
	case "3":
		return gosnmp.Version3
	default:
		m.logger.Warnf("Unknown SNMP version '%s', defaulting to 2c", version)
		return gosnmp.Version2c
	}
}

func (m *Manager) convertValue(variable gosnmp.SnmpPDU) float64 {
	switch variable.Type {
	case gosnmp.Counter32:
		return float64(gosnmp.ToBigInt(variable.Value).Uint64())
	case gosnmp.Counter64:
		return float64(gosnmp.ToBigInt(variable.Value).Uint64())
	case gosnmp.Gauge32:
		return float64(gosnmp.ToBigInt(variable.Value).Uint64())
	case gosnmp.Integer:
		return float64(gosnmp.ToBigInt(variable.Value).Int64())
	case gosnmp.Uinteger32:
		return float64(gosnmp.ToBigInt(variable.Value).Uint64())
	case gosnmp.TimeTicks:
		return float64(gosnmp.ToBigInt(variable.Value).Uint64())
	default:
		m.logger.Debugf("Unsupported SNMP type: %v, returning 0", variable.Type)
		return 0
	}
}

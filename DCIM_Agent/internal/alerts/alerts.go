package alerts

import (
	"fmt"
	"time"

	"github.com/faberlabs/dicm-agent/internal/collector"
	"github.com/faberlabs/dicm-agent/internal/config"
	"github.com/faberlabs/dicm-agent/internal/storage"
)

const (
	SeverityInfo     = "INFO"
	SeverityWarning  = "WARNING"
	SeverityCritical = "CRITICAL"
)

type AlertEngine struct {
	config  config.AlertsConfig
	storage *storage.Storage
}

func New(cfg config.AlertsConfig, store *storage.Storage) *AlertEngine {
	return &AlertEngine{
		config:  cfg,
		storage: store,
	}
}

// EvaluateMetrics checks metrics against thresholds and generates alerts
func (a *AlertEngine) EvaluateMetrics(metrics *collector.SystemMetrics) ([]*storage.Alert, error) {
	var alerts []*storage.Alert

	// Check CPU usage
	if alert := a.checkThreshold(
		"cpu",
		metrics.CPU.UsagePercent,
		a.config.CPU,
		metrics.Timestamp,
	); alert != nil {
		alerts = append(alerts, alert)
	}

	// Check memory usage
	if alert := a.checkThreshold(
		"memory",
		metrics.Memory.UsedPercent,
		a.config.Memory,
		metrics.Timestamp,
	); alert != nil {
		alerts = append(alerts, alert)
	}

	// Check disk usage
	for _, disk := range metrics.Disk {
		if alert := a.checkThreshold(
			fmt.Sprintf("disk.%s", disk.MountPoint),
			disk.UsedPercent,
			a.config.Disk,
			metrics.Timestamp,
		); alert != nil {
			alert.Message = fmt.Sprintf("Disk %s usage is %.2f%%", disk.MountPoint, disk.UsedPercent)
			alerts = append(alerts, alert)
		}
	}

	// Check temperature sensors
	for _, temp := range metrics.Temperature {
		if alert := a.checkThreshold(
			fmt.Sprintf("temperature.%s", temp.Sensor),
			temp.Temperature,
			a.config.Temperature,
			metrics.Timestamp,
		); alert != nil {
			alert.Message = fmt.Sprintf("Temperature sensor %s is %.2f°C", temp.Sensor, temp.Temperature)
			alerts = append(alerts, alert)
		}
	}

	// Store all generated alerts
	for _, alert := range alerts {
		if err := a.storage.SaveAlert(alert); err != nil {
			return nil, fmt.Errorf("save alert: %w", err)
		}
	}

	return alerts, nil
}

// checkThreshold evaluates a metric value against warning/critical thresholds
func (a *AlertEngine) checkThreshold(
	metricType string,
	value float64,
	threshold config.ThresholdConfig,
	timestamp time.Time,
) *storage.Alert {
	var severity string
	var thresholdValue float64

	if value >= threshold.Critical {
		severity = SeverityCritical
		thresholdValue = threshold.Critical
	} else if value >= threshold.Warning {
		severity = SeverityWarning
		thresholdValue = threshold.Warning
	} else {
		return nil
	}

	return &storage.Alert{
		Timestamp:  timestamp,
		Severity:   severity,
		MetricType: metricType,
		Value:      value,
		Threshold:  thresholdValue,
		Message:    fmt.Sprintf("%s %s: %.2f%% (threshold: %.2f%%)", metricType, severity, value, thresholdValue),
		CreatedAt:  time.Now(),
	}
}

// ShouldSendImmediately returns true if the alert should be sent immediately
func ShouldSendImmediately(alert *storage.Alert) bool {
	// Send WARNING and CRITICAL alerts immediately
	return alert.Severity == SeverityWarning || alert.Severity == SeverityCritical
}

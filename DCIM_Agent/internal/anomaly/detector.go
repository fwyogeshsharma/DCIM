package anomaly

import (
	"fmt"
	"math"
	"sync"
	"time"

	"github.com/faberlabs/dicm-agent/internal/collector"
	"github.com/faberlabs/dicm-agent/internal/storage"
)

// AnomalyType represents the type of anomaly detected
type AnomalyType string

const (
	AnomalyTypeCPUSpike       AnomalyType = "cpu_spike"
	AnomalyTypeMemoryLeak     AnomalyType = "memory_leak"
	AnomalyTypeDiskSlow       AnomalyType = "disk_slow"
	AnomalyTypeDiskIOSpike    AnomalyType = "disk_io_spike"
	AnomalyTypeNetworkAnomaly AnomalyType = "network_anomaly"
	AnomalyTypeTemperatureSpike AnomalyType = "temperature_spike"
	AnomalyTypeProcessSpike   AnomalyType = "process_spike"
)

// Anomaly represents a detected anomaly
type Anomaly struct {
	Type        AnomalyType       `json:"type"`
	Severity    string            `json:"severity"` // "LOW", "MEDIUM", "HIGH", "CRITICAL"
	Description string            `json:"description"`
	Value       float64           `json:"value"`
	Expected    float64           `json:"expected"`
	Deviation   float64           `json:"deviation"`
	Timestamp   time.Time         `json:"timestamp"`
	Metadata    map[string]interface{} `json:"metadata"`
}

// Detector monitors metrics for anomalies using statistical analysis
type Detector struct {
	storage *storage.Storage
	config  *Config

	// Baselines for various metrics
	baselines map[string]*Baseline
	mu        sync.RWMutex
}

// Config holds anomaly detection configuration
type Config struct {
	Enabled             bool    `yaml:"enabled"`
	SensitivityLevel    string  `yaml:"sensitivity"` // "low", "medium", "high"
	BaselineWindowHours int     `yaml:"baseline_window_hours"`
	DeviationThreshold  float64 `yaml:"deviation_threshold"` // Number of standard deviations
	MinDataPoints       int     `yaml:"min_data_points"`
}

// Baseline represents statistical baseline for a metric
type Baseline struct {
	MetricName  string
	Mean        float64
	StdDev      float64
	Min         float64
	Max         float64
	DataPoints  int
	LastUpdated time.Time
}

// New creates a new anomaly detector
func New(store *storage.Storage, cfg *Config) *Detector {
	if cfg == nil {
		cfg = &Config{
			Enabled:             true,
			SensitivityLevel:    "medium",
			BaselineWindowHours: 24,
			DeviationThreshold:  3.0,
			MinDataPoints:       20,
		}
	}

	// Adjust deviation threshold based on sensitivity
	switch cfg.SensitivityLevel {
	case "low":
		cfg.DeviationThreshold = 4.0
	case "high":
		cfg.DeviationThreshold = 2.0
	default: // medium
		cfg.DeviationThreshold = 3.0
	}

	return &Detector{
		storage:   store,
		config:    cfg,
		baselines: make(map[string]*Baseline),
	}
}

// DetectAnomalies analyzes metrics and detects anomalies
func (d *Detector) DetectAnomalies(metrics *collector.SystemMetrics) ([]*Anomaly, error) {
	if !d.config.Enabled {
		return nil, nil
	}

	var anomalies []*Anomaly

	// CPU anomalies
	if cpuAnomalies := d.detectCPUAnomalies(metrics); len(cpuAnomalies) > 0 {
		anomalies = append(anomalies, cpuAnomalies...)
	}

	// Memory anomalies
	if memAnomalies := d.detectMemoryAnomalies(metrics); len(memAnomalies) > 0 {
		anomalies = append(anomalies, memAnomalies...)
	}

	// Disk anomalies
	if diskAnomalies := d.detectDiskAnomalies(metrics); len(diskAnomalies) > 0 {
		anomalies = append(anomalies, diskAnomalies...)
	}

	// Network anomalies
	if netAnomalies := d.detectNetworkAnomalies(metrics); len(netAnomalies) > 0 {
		anomalies = append(anomalies, netAnomalies...)
	}

	// Temperature anomalies
	if tempAnomalies := d.detectTemperatureAnomalies(metrics); len(tempAnomalies) > 0 {
		anomalies = append(anomalies, tempAnomalies...)
	}

	// Process count anomalies
	if procAnomalies := d.detectProcessAnomalies(metrics); len(procAnomalies) > 0 {
		anomalies = append(anomalies, procAnomalies...)
	}

	return anomalies, nil
}

// detectCPUAnomalies detects CPU-related anomalies
func (d *Detector) detectCPUAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	baseline := d.getOrCreateBaseline("cpu.usage")
	if baseline.DataPoints < d.config.MinDataPoints {
		return anomalies
	}

	// Check for sudden spike
	if metrics.CPU.UsagePercent > baseline.Mean+baseline.StdDev*d.config.DeviationThreshold {
		deviation := (metrics.CPU.UsagePercent - baseline.Mean) / baseline.StdDev
		severity := d.calculateSeverity(deviation)

		anomalies = append(anomalies, &Anomaly{
			Type:        AnomalyTypeCPUSpike,
			Severity:    severity,
			Description: fmt.Sprintf("CPU usage spike detected: %.2f%% (expected: %.2f%% ± %.2f%%)",
				metrics.CPU.UsagePercent, baseline.Mean, baseline.StdDev),
			Value:       metrics.CPU.UsagePercent,
			Expected:    baseline.Mean,
			Deviation:   deviation,
			Timestamp:   metrics.Timestamp,
			Metadata: map[string]interface{}{
				"cores":     metrics.CPU.Cores,
				"load_avg1": metrics.CPU.LoadAvg1,
			},
		})
	}

	// Update baseline with new data
	d.updateBaseline("cpu.usage", metrics.CPU.UsagePercent)

	return anomalies
}

// detectMemoryAnomalies detects memory-related anomalies
func (d *Detector) detectMemoryAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	baseline := d.getOrCreateBaseline("memory.usage")
	if baseline.DataPoints < d.config.MinDataPoints {
		d.updateBaseline("memory.usage", metrics.Memory.UsedPercent)
		return anomalies
	}

	// Check for memory leak (gradual increase over time)
	if metrics.Memory.UsedPercent > baseline.Mean+baseline.StdDev*2 {
		// Memory increasing more than expected
		deviation := (metrics.Memory.UsedPercent - baseline.Mean) / baseline.StdDev

		if deviation > d.config.DeviationThreshold {
			severity := d.calculateSeverity(deviation)

			anomalies = append(anomalies, &Anomaly{
				Type:        AnomalyTypeMemoryLeak,
				Severity:    severity,
				Description: fmt.Sprintf("Possible memory leak: %.2f%% usage (expected: %.2f%% ± %.2f%%)",
					metrics.Memory.UsedPercent, baseline.Mean, baseline.StdDev),
				Value:       metrics.Memory.UsedPercent,
				Expected:    baseline.Mean,
				Deviation:   deviation,
				Timestamp:   metrics.Timestamp,
				Metadata: map[string]interface{}{
					"used_gb":      float64(metrics.Memory.Used) / (1024 * 1024 * 1024),
					"available_gb": float64(metrics.Memory.Available) / (1024 * 1024 * 1024),
					"swap_percent": metrics.Memory.SwapPercent,
				},
			})
		}
	}

	d.updateBaseline("memory.usage", metrics.Memory.UsedPercent)
	return anomalies
}

// detectDiskAnomalies detects disk-related anomalies
func (d *Detector) detectDiskAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	for _, diskIO := range metrics.DiskIO {
		metricName := fmt.Sprintf("disk.io.%s", diskIO.Device)
		totalIO := float64(diskIO.ReadCount + diskIO.WriteCount)

		baseline := d.getOrCreateBaseline(metricName)
		if baseline.DataPoints < d.config.MinDataPoints {
			d.updateBaseline(metricName, totalIO)
			continue
		}

		// Check for I/O spike
		if totalIO > baseline.Mean+baseline.StdDev*d.config.DeviationThreshold {
			deviation := (totalIO - baseline.Mean) / baseline.StdDev
			severity := d.calculateSeverity(deviation)

			anomalies = append(anomalies, &Anomaly{
				Type:        AnomalyTypeDiskIOSpike,
				Severity:    severity,
				Description: fmt.Sprintf("Disk I/O spike on %s: %.0f ops (expected: %.0f ± %.0f)",
					diskIO.Device, totalIO, baseline.Mean, baseline.StdDev),
				Value:       totalIO,
				Expected:    baseline.Mean,
				Deviation:   deviation,
				Timestamp:   metrics.Timestamp,
				Metadata: map[string]interface{}{
					"device":      diskIO.Device,
					"read_count":  diskIO.ReadCount,
					"write_count": diskIO.WriteCount,
					"read_bytes":  diskIO.ReadBytes,
					"write_bytes": diskIO.WriteBytes,
				},
			})
		}

		d.updateBaseline(metricName, totalIO)
	}

	return anomalies
}

// detectNetworkAnomalies detects network-related anomalies
func (d *Detector) detectNetworkAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	// Check for error spikes
	totalErrors := float64(metrics.Network.ErrorsIn + metrics.Network.ErrorsOut)
	if totalErrors > 0 {
		baseline := d.getOrCreateBaseline("network.errors")
		if baseline.DataPoints >= d.config.MinDataPoints {
			if totalErrors > baseline.Mean+baseline.StdDev*d.config.DeviationThreshold {
				deviation := (totalErrors - baseline.Mean) / baseline.StdDev
				severity := d.calculateSeverity(deviation)

				anomalies = append(anomalies, &Anomaly{
					Type:        AnomalyTypeNetworkAnomaly,
					Severity:    severity,
					Description: fmt.Sprintf("Network error spike: %.0f errors (expected: %.0f ± %.0f)",
						totalErrors, baseline.Mean, baseline.StdDev),
					Value:       totalErrors,
					Expected:    baseline.Mean,
					Deviation:   deviation,
					Timestamp:   metrics.Timestamp,
					Metadata: map[string]interface{}{
						"errors_in":  metrics.Network.ErrorsIn,
						"errors_out": metrics.Network.ErrorsOut,
						"drops_in":   metrics.Network.DropsIn,
						"drops_out":  metrics.Network.DropsOut,
					},
				})
			}
		}
		d.updateBaseline("network.errors", totalErrors)
	}

	return anomalies
}

// detectTemperatureAnomalies detects temperature-related anomalies
func (d *Detector) detectTemperatureAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	if metrics.SensorData == nil {
		return anomalies
	}

	for _, temp := range metrics.SensorData.Temperatures {
		if temp.Current == 0 {
			continue
		}

		metricName := fmt.Sprintf("temperature.%s", temp.Component)
		baseline := d.getOrCreateBaseline(metricName)

		if baseline.DataPoints < d.config.MinDataPoints {
			d.updateBaseline(metricName, temp.Current)
			continue
		}

		// Check for sudden temperature spike
		if temp.Current > baseline.Mean+baseline.StdDev*d.config.DeviationThreshold {
			deviation := (temp.Current - baseline.Mean) / baseline.StdDev
			severity := d.calculateSeverity(deviation)

			anomalies = append(anomalies, &Anomaly{
				Type:        AnomalyTypeTemperatureSpike,
				Severity:    severity,
				Description: fmt.Sprintf("%s temperature spike: %.1f°C (expected: %.1f°C ± %.1f°C)",
					temp.Component, temp.Current, baseline.Mean, baseline.StdDev),
				Value:       temp.Current,
				Expected:    baseline.Mean,
				Deviation:   deviation,
				Timestamp:   metrics.Timestamp,
				Metadata: map[string]interface{}{
					"component": temp.Component,
					"name":      temp.Name,
					"high":      temp.High,
					"critical":  temp.Critical,
				},
			})
		}

		d.updateBaseline(metricName, temp.Current)
	}

	return anomalies
}

// detectProcessAnomalies detects process count anomalies
func (d *Detector) detectProcessAnomalies(metrics *collector.SystemMetrics) []*Anomaly {
	var anomalies []*Anomaly

	baseline := d.getOrCreateBaseline("process.count")
	if baseline.DataPoints < d.config.MinDataPoints {
		d.updateBaseline("process.count", float64(metrics.ProcessCount))
		return anomalies
	}

	processCount := float64(metrics.ProcessCount)
	if processCount > baseline.Mean+baseline.StdDev*d.config.DeviationThreshold {
		deviation := (processCount - baseline.Mean) / baseline.StdDev
		severity := d.calculateSeverity(deviation)

		anomalies = append(anomalies, &Anomaly{
			Type:        AnomalyTypeProcessSpike,
			Severity:    severity,
			Description: fmt.Sprintf("Process count spike: %d processes (expected: %.0f ± %.0f)",
				metrics.ProcessCount, baseline.Mean, baseline.StdDev),
			Value:       processCount,
			Expected:    baseline.Mean,
			Deviation:   deviation,
			Timestamp:   metrics.Timestamp,
		})
	}

	d.updateBaseline("process.count", processCount)
	return anomalies
}

// getOrCreateBaseline gets existing baseline or creates new one
func (d *Detector) getOrCreateBaseline(metricName string) *Baseline {
	d.mu.Lock()
	defer d.mu.Unlock()

	if baseline, exists := d.baselines[metricName]; exists {
		return baseline
	}

	baseline := &Baseline{
		MetricName:  metricName,
		LastUpdated: time.Now(),
	}
	d.baselines[metricName] = baseline
	return baseline
}

// updateBaseline updates baseline with new data point
func (d *Detector) updateBaseline(metricName string, value float64) {
	d.mu.Lock()
	defer d.mu.Unlock()

	baseline := d.baselines[metricName]

	// Update using incremental mean and standard deviation calculation
	n := float64(baseline.DataPoints)
	oldMean := baseline.Mean

	// Update mean
	baseline.Mean = (n*baseline.Mean + value) / (n + 1)

	// Update standard deviation (incremental)
	if baseline.DataPoints == 0 {
		baseline.StdDev = 0
		baseline.Min = value
		baseline.Max = value
	} else {
		// Welford's online algorithm for variance
		delta := value - oldMean
		delta2 := value - baseline.Mean
		variance := (n*baseline.StdDev*baseline.StdDev + delta*delta2) / (n + 1)
		baseline.StdDev = math.Sqrt(variance)

		if value < baseline.Min {
			baseline.Min = value
		}
		if value > baseline.Max {
			baseline.Max = value
		}
	}

	baseline.DataPoints++
	baseline.LastUpdated = time.Now()
}

// calculateSeverity determines severity based on deviation
func (d *Detector) calculateSeverity(deviation float64) string {
	absDeviation := math.Abs(deviation)

	if absDeviation >= 5 {
		return "CRITICAL"
	} else if absDeviation >= 4 {
		return "HIGH"
	} else if absDeviation >= 3 {
		return "MEDIUM"
	}
	return "LOW"
}

// GetBaselines returns all current baselines
func (d *Detector) GetBaselines() map[string]*Baseline {
	d.mu.RLock()
	defer d.mu.RUnlock()

	// Return a copy
	baselines := make(map[string]*Baseline)
	for k, v := range d.baselines {
		baselineCopy := *v
		baselines[k] = &baselineCopy
	}
	return baselines
}

// ResetBaselines clears all baselines
func (d *Detector) ResetBaselines() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.baselines = make(map[string]*Baseline)
}

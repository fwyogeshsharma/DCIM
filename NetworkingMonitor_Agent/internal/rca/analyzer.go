package rca

import (
	"fmt"
	"strings"
	"time"

	"github.com/faber/network-monitor-agent/internal/anomaly"
	"github.com/faber/network-monitor-agent/internal/collector"
)

// RootCause represents an identified root cause
type RootCause struct {
	Issue           string                 `json:"issue"`
	Confidence      float64                `json:"confidence"` // 0.0 to 1.0
	Description     string                 `json:"description"`
	PossibleReasons []string               `json:"possible_reasons"`
	Recommendations []string               `json:"recommendations"`
	RelatedMetrics  map[string]interface{} `json:"related_metrics"`
	Timestamp       time.Time              `json:"timestamp"`
}

// Analyzer performs root cause analysis
type Analyzer struct{}

// New creates a new RCA analyzer
func New() *Analyzer {
	return &Analyzer{}
}

// AnalyzeRootCause analyzes anomalies and metrics to determine root causes
func (a *Analyzer) AnalyzeRootCause(
	anomalies []*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) ([]*RootCause, error) {
	var rootCauses []*RootCause

	// Analyze correlations between different anomalies
	if len(anomalies) > 0 {
		// Group anomalies by type
		anomalyMap := make(map[anomaly.AnomalyType][]*anomaly.Anomaly)
		for _, anom := range anomalies {
			anomalyMap[anom.Type] = append(anomalyMap[anom.Type], anom)
		}

		// Check for correlated patterns
		rootCauses = append(rootCauses, a.analyzeHighCPUCorrelations(anomalyMap, metrics)...)
		rootCauses = append(rootCauses, a.analyzeMemoryPressure(anomalyMap, metrics)...)
		rootCauses = append(rootCauses, a.analyzeDiskBottlenecks(anomalyMap, metrics)...)
		rootCauses = append(rootCauses, a.analyzeNetworkIssues(anomalyMap, metrics)...)
		rootCauses = append(rootCauses, a.analyzeThermalIssues(anomalyMap, metrics)...)
	}

	// Even without anomalies, check for problematic patterns
	if len(anomalies) == 0 {
		rootCauses = append(rootCauses, a.analyzeSystemHealth(metrics)...)
	}

	return rootCauses, nil
}

// analyzeHighCPUCorrelations analyzes CPU-related issues
func (a *Analyzer) analyzeHighCPUCorrelations(
	anomalies map[anomaly.AnomalyType][]*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) []*RootCause {
	var causes []*RootCause

	cpuAnomalies := anomalies[anomaly.AnomalyTypeCPUSpike]
	if len(cpuAnomalies) == 0 {
		return causes
	}

	// High CPU + High Disk I/O = Process thrashing or disk-bound operations
	diskAnomalies := anomalies[anomaly.AnomalyTypeDiskIOSpike]
	if len(diskAnomalies) > 0 {
		causes = append(causes, &RootCause{
			Issue:      "CPU and Disk Thrashing",
			Confidence: 0.85,
			Description: "High CPU usage combined with excessive disk I/O indicates system thrashing or disk-bound operations",
			PossibleReasons: []string{
				"Insufficient RAM causing excessive page swapping",
				"Database or application performing heavy disk operations",
				"Antivirus or backup software scanning files",
				"Disk-intensive batch processing",
			},
			Recommendations: []string{
				"Check memory usage and consider adding more RAM",
				"Identify processes with high I/O wait time",
				"Review disk queue depth and latency",
				"Schedule disk-intensive tasks during off-peak hours",
				"Consider SSD upgrade for better I/O performance",
			},
			RelatedMetrics: map[string]interface{}{
				"cpu_percent":    metrics.CPU.UsagePercent,
				"memory_percent": metrics.Memory.UsedPercent,
				"swap_percent":   metrics.Memory.SwapPercent,
			},
			Timestamp: time.Now(),
		})
	}

	// High CPU + High Memory = Resource exhaustion
	memAnomalies := anomalies[anomaly.AnomalyTypeMemoryLeak]
	if len(memAnomalies) > 0 || metrics.Memory.UsedPercent > 85 {
		causes = append(causes, &RootCause{
			Issue:      "Resource Exhaustion",
			Confidence: 0.80,
			Description: "High CPU and memory usage indicates resource exhaustion",
			PossibleReasons: []string{
				"Memory leak in application",
				"Too many concurrent processes",
				"Insufficient system resources for workload",
				"Runaway process consuming resources",
			},
			Recommendations: []string{
				"Identify top CPU and memory consuming processes",
				"Check for memory leaks in applications",
				"Consider scaling resources (CPU/RAM)",
				"Implement resource limits for applications",
				"Review application logs for errors",
			},
			RelatedMetrics: map[string]interface{}{
				"cpu_percent":    metrics.CPU.UsagePercent,
				"memory_percent": metrics.Memory.UsedPercent,
				"process_count":  metrics.ProcessCount,
			},
			Timestamp: time.Now(),
		})
	}

	// High CPU + Temperature spike = Thermal throttling
	tempAnomalies := anomalies[anomaly.AnomalyTypeTemperatureSpike]
	if len(tempAnomalies) > 0 {
		causes = append(causes, &RootCause{
			Issue:      "Thermal Throttling",
			Confidence: 0.90,
			Description: "High CPU usage causing temperature spikes may lead to thermal throttling",
			PossibleReasons: []string{
				"Inadequate cooling system",
				"Dust buildup in cooling system",
				"Thermal paste degradation",
				"High ambient temperature",
				"CPU running at sustained high load",
			},
			Recommendations: []string{
				"Check CPU cooling system (fans, heatsink)",
				"Clean dust from cooling components",
				"Verify thermal paste condition",
				"Monitor CPU frequency for throttling",
				"Improve case airflow",
				"Consider upgrading cooling solution",
			},
			RelatedMetrics: map[string]interface{}{
				"cpu_percent": metrics.CPU.UsagePercent,
			},
			Timestamp: time.Now(),
		})
	}

	return causes
}

// analyzeMemoryPressure analyzes memory-related issues
func (a *Analyzer) analyzeMemoryPressure(
	anomalies map[anomaly.AnomalyType][]*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) []*RootCause {
	var causes []*RootCause

	memAnomalies := anomalies[anomaly.AnomalyTypeMemoryLeak]
	if len(memAnomalies) == 0 && metrics.Memory.UsedPercent < 85 {
		return causes
	}

	// High memory + High swap = Memory pressure
	if metrics.Memory.SwapPercent > 50 {
		causes = append(causes, &RootCause{
			Issue:      "Memory Pressure and Swapping",
			Confidence: 0.95,
			Description: "System is swapping excessively due to memory pressure",
			PossibleReasons: []string{
				"Insufficient physical RAM for workload",
				"Memory leak in application",
				"Too many applications running simultaneously",
				"Large cache or buffer usage",
			},
			Recommendations: []string{
				"Add more physical RAM",
				"Identify and fix memory leaks",
				"Close unnecessary applications",
				"Tune application memory limits",
				"Review swap configuration",
				"Consider using memory compression",
			},
			RelatedMetrics: map[string]interface{}{
				"memory_percent": metrics.Memory.UsedPercent,
				"swap_percent":   metrics.Memory.SwapPercent,
				"available_gb":   float64(metrics.Memory.Available) / (1024 * 1024 * 1024),
			},
			Timestamp: time.Now(),
		})
	}

	// Memory leak detection
	if len(memAnomalies) > 0 {
		causes = append(causes, &RootCause{
			Issue:      "Possible Memory Leak",
			Confidence: 0.75,
			Description: "Memory usage is gradually increasing beyond normal baseline",
			PossibleReasons: []string{
				"Application not releasing memory properly",
				"Circular references preventing garbage collection",
				"Caching without size limits",
				"Resource leak (file handles, connections)",
			},
			Recommendations: []string{
				"Profile application memory usage",
				"Review application logs for memory-related errors",
				"Check for resource leaks in code",
				"Implement memory monitoring and alerts",
				"Consider restarting affected services",
				"Update applications to latest versions",
			},
			RelatedMetrics: map[string]interface{}{
				"memory_percent": metrics.Memory.UsedPercent,
				"cached_gb":      float64(metrics.Memory.Cached) / (1024 * 1024 * 1024),
			},
			Timestamp: time.Now(),
		})
	}

	return causes
}

// analyzeDiskBottlenecks analyzes disk performance issues
func (a *Analyzer) analyzeDiskBottlenecks(
	anomalies map[anomaly.AnomalyType][]*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) []*RootCause {
	var causes []*RootCause

	diskAnomalies := anomalies[anomaly.AnomalyTypeDiskIOSpike]
	slowAnomalies := anomalies[anomaly.AnomalyTypeDiskSlow]

	if len(diskAnomalies) == 0 && len(slowAnomalies) == 0 {
		return causes
	}

	// Analyze disk usage patterns
	var highUsageDisks []string
	for _, disk := range metrics.Disk {
		if disk.UsedPercent > 90 {
			highUsageDisks = append(highUsageDisks, disk.MountPoint)
		}
	}

	if len(highUsageDisks) > 0 {
		causes = append(causes, &RootCause{
			Issue:      "Disk Space Exhaustion",
			Confidence: 0.90,
			Description: fmt.Sprintf("Disks near capacity: %s", strings.Join(highUsageDisks, ", ")),
			PossibleReasons: []string{
				"Log files consuming excessive space",
				"Temporary files not being cleaned",
				"Database growth without maintenance",
				"Backup files accumulating",
				"Application data growth",
			},
			Recommendations: []string{
				"Clean up old log files and temporary files",
				"Implement log rotation policies",
				"Review and archive old data",
				"Set up disk space monitoring and alerts",
				"Increase disk capacity or add storage",
				"Identify largest files and directories",
			},
			RelatedMetrics: map[string]interface{}{
				"high_usage_disks": highUsageDisks,
			},
			Timestamp: time.Now(),
		})
	}

	if len(diskAnomalies) > 0 {
		causes = append(causes, &RootCause{
			Issue:      "Disk I/O Bottleneck",
			Confidence: 0.85,
			Description: "Excessive disk I/O operations detected",
			PossibleReasons: []string{
				"Database performing heavy read/write operations",
				"Multiple applications accessing disk simultaneously",
				"Slow disk hardware (HDD vs SSD)",
				"Insufficient disk cache",
				"Fragmented filesystem",
			},
			Recommendations: []string{
				"Identify processes with high disk I/O",
				"Optimize database queries and indexing",
				"Consider upgrading to SSD storage",
				"Implement read caching",
				"Schedule I/O-intensive tasks appropriately",
				"Check disk health and SMART status",
			},
			RelatedMetrics: map[string]interface{}{
				"disk_io_count": len(metrics.DiskIO),
			},
			Timestamp: time.Now(),
		})
	}

	return causes
}

// analyzeNetworkIssues analyzes network-related issues
func (a *Analyzer) analyzeNetworkIssues(
	anomalies map[anomaly.AnomalyType][]*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) []*RootCause {
	var causes []*RootCause

	netAnomalies := anomalies[anomaly.AnomalyTypeNetworkAnomaly]
	if len(netAnomalies) == 0 {
		return causes
	}

	totalErrors := metrics.Network.ErrorsIn + metrics.Network.ErrorsOut
	totalDrops := metrics.Network.DropsIn + metrics.Network.DropsOut

	if totalErrors > 0 || totalDrops > 0 {
		causes = append(causes, &RootCause{
			Issue:      "Network Quality Issues",
			Confidence: 0.80,
			Description: fmt.Sprintf("Network errors: %d, Packet drops: %d", totalErrors, totalDrops),
			PossibleReasons: []string{
				"Faulty network cable or connector",
				"Network congestion",
				"Incompatible network settings (duplex mismatch)",
				"Driver issues",
				"Network hardware failure",
				"Electromagnetic interference",
			},
			Recommendations: []string{
				"Check network cable connections",
				"Verify network interface statistics",
				"Test with different network cable",
				"Update network drivers",
				"Check switch/router port status",
				"Monitor network bandwidth utilization",
				"Verify duplex and speed settings",
			},
			RelatedMetrics: map[string]interface{}{
				"errors_in":  metrics.Network.ErrorsIn,
				"errors_out": metrics.Network.ErrorsOut,
				"drops_in":   metrics.Network.DropsIn,
				"drops_out":  metrics.Network.DropsOut,
			},
			Timestamp: time.Now(),
		})
	}

	return causes
}

// analyzeThermalIssues analyzes temperature-related issues
func (a *Analyzer) analyzeThermalIssues(
	anomalies map[anomaly.AnomalyType][]*anomaly.Anomaly,
	metrics *collector.SystemMetrics,
) []*RootCause {
	var causes []*RootCause

	tempAnomalies := anomalies[anomaly.AnomalyTypeTemperatureSpike]
	if len(tempAnomalies) == 0 {
		return causes
	}

	if metrics.SensorData != nil && len(metrics.SensorData.Temperatures) > 0 {
		var hotComponents []string
		for _, temp := range metrics.SensorData.Temperatures {
			if temp.Critical > 0 && temp.Current >= temp.Critical*0.9 {
				hotComponents = append(hotComponents, fmt.Sprintf("%s (%.1f°C)", temp.Component, temp.Current))
			}
		}

		if len(hotComponents) > 0 {
			causes = append(causes, &RootCause{
				Issue:      "Critical Temperature Warning",
				Confidence: 0.95,
				Description: fmt.Sprintf("Components near thermal limits: %s", strings.Join(hotComponents, ", ")),
				PossibleReasons: []string{
					"Inadequate cooling system",
					"Blocked air vents",
					"Failed cooling fans",
					"Dried thermal paste",
					"High ambient temperature",
					"Overclocking",
				},
				Recommendations: []string{
					"Immediately check cooling system operation",
					"Verify all fans are spinning",
					"Clean dust from heatsinks and fans",
					"Check air intake and exhaust vents",
					"Reapply thermal paste if needed",
					"Reduce system load if possible",
					"Consider emergency shutdown if temperature continues rising",
				},
				RelatedMetrics: map[string]interface{}{
					"hot_components": hotComponents,
				},
				Timestamp: time.Now(),
			})
		}
	}

	return causes
}

// analyzeSystemHealth checks for issues even without anomalies
func (a *Analyzer) analyzeSystemHealth(metrics *collector.SystemMetrics) []*RootCause {
	var causes []*RootCause

	// Check for potential issues that might not trigger anomalies yet
	if metrics.Memory.UsedPercent > 90 {
		causes = append(causes, &RootCause{
			Issue:      "High Memory Usage",
			Confidence: 0.70,
			Description: fmt.Sprintf("Memory usage is critically high: %.2f%%", metrics.Memory.UsedPercent),
			PossibleReasons: []string{
				"System approaching memory limits",
				"May lead to performance degradation soon",
			},
			Recommendations: []string{
				"Monitor memory usage closely",
				"Prepare to free up memory if needed",
				"Identify memory-intensive processes",
			},
			RelatedMetrics: map[string]interface{}{
				"memory_percent": metrics.Memory.UsedPercent,
			},
			Timestamp: time.Now(),
		})
	}

	return causes
}

package network

import (
	"fmt"
	"runtime"

	"github.com/shirou/gopsutil/v3/net"
)

// getStatistics populates network statistics for an interface
func (d *NetworkInterfaceDetail) getStatistics(ifaceName string) error {
	// Get IO counters per interface
	counters, err := net.IOCounters(true)
	if err != nil {
		return err
	}

	// Find matching interface
	for _, counter := range counters {
		if counter.Name == ifaceName {
			d.BytesSent = counter.BytesSent
			d.BytesRecv = counter.BytesRecv
			d.PacketsSent = counter.PacketsSent
			d.PacketsRecv = counter.PacketsRecv
			d.ErrorsIn = counter.Errin
			d.ErrorsOut = counter.Errout
			d.DropsIn = counter.Dropin
			d.DropsOut = counter.Dropout

			return nil
		}
	}

	return fmt.Errorf("interface not found in counters: %s", ifaceName)
}

// PortStatistics contains detailed port statistics
type PortStatistics struct {
	Interface string    `json:"interface"`

	// Basic counters
	RXFrames uint64 `json:"rx_frames"`
	TXFrames uint64 `json:"tx_frames"`
	RXBytes  uint64 `json:"rx_bytes"`
	TXBytes  uint64 `json:"tx_bytes"`

	// Error counters
	RXErrors  uint64 `json:"rx_errors"`
	TXErrors  uint64 `json:"tx_errors"`
	RXDropped uint64 `json:"rx_dropped"`
	TXDropped uint64 `json:"tx_dropped"`

	// Detailed errors (Linux ethtool)
	RXCRCErrors     uint64 `json:"rx_crc_errors"`
	RXFrameErrors   uint64 `json:"rx_frame_errors"`
	RXFIFOErrors    uint64 `json:"rx_fifo_errors"`
	RXMissedErrors  uint64 `json:"rx_missed_errors"`
	TXAbortedErrors uint64 `json:"tx_aborted_errors"`
	TXCarrierErrors uint64 `json:"tx_carrier_errors"`
	TXFIFOErrors    uint64 `json:"tx_fifo_errors"`
	TXWindowErrors  uint64 `json:"tx_window_errors"`
	Collisions      uint64 `json:"collisions"`

	// Flow control
	RXFlowControl uint64 `json:"rx_flow_control"`
	TXFlowControl uint64 `json:"tx_flow_control"`

	// Pause frames
	RXPauseFrames uint64 `json:"rx_pause_frames"`
	TXPauseFrames uint64 `json:"tx_pause_frames"`

	// Additional platform-specific stats
	Extra map[string]uint64 `json:"extra,omitempty"`
}

// GetPortStatistics retrieves detailed port statistics
func GetPortStatistics(ifaceName string) (*PortStatistics, error) {
	stats := &PortStatistics{
		Interface: ifaceName,
		Extra:     make(map[string]uint64),
	}

	// Get basic statistics from gopsutil
	counters, err := net.IOCounters(true)
	if err != nil {
		return nil, err
	}

	for _, counter := range counters {
		if counter.Name == ifaceName {
			stats.RXFrames = counter.PacketsRecv
			stats.TXFrames = counter.PacketsSent
			stats.RXBytes = counter.BytesRecv
			stats.TXBytes = counter.BytesSent
			stats.RXErrors = counter.Errin
			stats.TXErrors = counter.Errout
			stats.RXDropped = counter.Dropin
			stats.TXDropped = counter.Dropout
			break
		}
	}

	// Get platform-specific detailed statistics
	switch runtime.GOOS {
	case "linux":
		if err := stats.getLinuxDetailedStats(ifaceName); err != nil {
			// Non-fatal, continue with basic stats
		}
	case "windows":
		if err := stats.getWindowsDetailedStats(ifaceName); err != nil {
			// Non-fatal, continue with basic stats
		}
	}

	return stats, nil
}

// getLinuxDetailedStats gets detailed statistics from ethtool on Linux
func (s *PortStatistics) getLinuxDetailedStats(ifaceName string) error {
	ethtoolStats, err := GetEthtoolStatistics(ifaceName)
	if err != nil {
		return err
	}

	// Map common ethtool statistics to our structure
	// Note: Exact stat names vary by driver
	s.mapEthtoolStats(ethtoolStats)

	return nil
}

// mapEthtoolStats maps ethtool statistics to PortStatistics fields
func (s *PortStatistics) mapEthtoolStats(stats map[string]uint64) {
	// Common mappings (driver-dependent)
	statMappings := map[string]*uint64{
		"rx_crc_errors":        &s.RXCRCErrors,
		"rx_frame_errors":      &s.RXFrameErrors,
		"rx_fifo_errors":       &s.RXFIFOErrors,
		"rx_missed_errors":     &s.RXMissedErrors,
		"tx_aborted_errors":    &s.TXAbortedErrors,
		"tx_carrier_errors":    &s.TXCarrierErrors,
		"tx_fifo_errors":       &s.TXFIFOErrors,
		"tx_window_errors":     &s.TXWindowErrors,
		"collisions":           &s.Collisions,
		"tx_flow_control_xon":  &s.TXFlowControl,
		"rx_flow_control_xon":  &s.RXFlowControl,
		"tx_pause_frames":      &s.TXPauseFrames,
		"rx_pause_frames":      &s.RXPauseFrames,
	}

	// Try various naming conventions
	for ethtoolName, value := range stats {
		// Direct mapping
		if ptr, ok := statMappings[ethtoolName]; ok {
			*ptr = value
			continue
		}

		// Alternative naming patterns
		switch {
		case contains(ethtoolName, "crc_err"):
			s.RXCRCErrors += value
		case contains(ethtoolName, "frame_err"):
			s.RXFrameErrors += value
		case contains(ethtoolName, "collision"):
			s.Collisions += value
		case contains(ethtoolName, "carrier_err"):
			s.TXCarrierErrors += value
		default:
			// Store unknown stats in Extra
			s.Extra[ethtoolName] = value
		}
	}
}

// getWindowsDetailedStats gets detailed statistics from WMI on Windows
func (s *PortStatistics) getWindowsDetailedStats(ifaceName string) error {
	stats, err := GetWindowsNetworkStats(ifaceName)
	if err != nil {
		return err
	}

	// Add Windows-specific stats to Extra
	for key, value := range stats {
		s.Extra[key] = value
	}

	return nil
}

// GetAllPortStatistics retrieves statistics for all interfaces
func GetAllPortStatistics() ([]*PortStatistics, error) {
	counters, err := net.IOCounters(true)
	if err != nil {
		return nil, err
	}

	var allStats []*PortStatistics

	for _, counter := range counters {
		stats, err := GetPortStatistics(counter.Name)
		if err != nil {
			continue
		}
		allStats = append(allStats, stats)
	}

	return allStats, nil
}

// CalculateUtilization calculates link utilization percentage
func (s *PortStatistics) CalculateUtilization(linkSpeedMbps int, duration float64) float64 {
	if linkSpeedMbps == 0 || duration == 0 {
		return 0
	}

	// Convert link speed to bytes per second
	linkSpeedBytesPerSec := float64(linkSpeedMbps) * 1000000 / 8

	// Calculate total bytes transferred
	totalBytes := float64(s.RXBytes + s.TXBytes)

	// Calculate bytes per second
	bytesPerSec := totalBytes / duration

	// Calculate utilization percentage
	utilization := (bytesPerSec / linkSpeedBytesPerSec) * 100

	if utilization > 100 {
		utilization = 100
	}

	return utilization
}

// GetErrorRate calculates error rate as percentage
func (s *PortStatistics) GetErrorRate() float64 {
	totalPackets := s.RXFrames + s.TXFrames
	if totalPackets == 0 {
		return 0
	}

	totalErrors := s.RXErrors + s.TXErrors
	return (float64(totalErrors) / float64(totalPackets)) * 100
}

// GetDropRate calculates drop rate as percentage
func (s *PortStatistics) GetDropRate() float64 {
	totalPackets := s.RXFrames + s.TXFrames
	if totalPackets == 0 {
		return 0
	}

	totalDrops := s.RXDropped + s.TXDropped
	return (float64(totalDrops) / float64(totalPackets)) * 100
}

// HasErrors returns true if there are any errors
func (s *PortStatistics) HasErrors() bool {
	return s.RXErrors > 0 || s.TXErrors > 0 ||
		   s.RXCRCErrors > 0 || s.TXCarrierErrors > 0 ||
		   s.Collisions > 0
}


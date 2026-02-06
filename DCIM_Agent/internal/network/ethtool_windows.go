//go:build windows

package network

import (
	"fmt"

	"github.com/StackExchange/wmi"
)

// NetworkAdapter represents Win32_NetworkAdapter WMI class
type NetworkAdapter struct {
	Name                string
	MACAddress          string
	Speed               uint64
	NetConnectionStatus uint16
	AdapterType         string
	Manufacturer        string
	ProductName         string
	ServiceName         string
	PNPDeviceID         string
	DeviceID            string
}

// NetworkAdapterConfiguration represents Win32_NetworkAdapterConfiguration
type NetworkAdapterConfiguration struct {
	Index              uint32
	IPAddress          []string
	IPSubnet           []string
	DefaultIPGateway   []string
	DNSServerSearchOrder []string
	DHCPEnabled        bool
	MACAddress         string
}

// getWindowsDetails populates Windows-specific NIC details using WMI
func (d *NetworkInterfaceDetail) getWindowsDetails(ifaceName string) error {
	// Get adapter information
	adapter, err := getWindowsAdapter(ifaceName)
	if err != nil {
		return err
	}

	// Populate from adapter
	d.Vendor = adapter.Manufacturer
	d.Model = adapter.ProductName
	d.Driver = adapter.ServiceName

	// Convert speed from bps to Mbps
	if adapter.Speed > 0 {
		d.LinkSpeed = int(adapter.Speed / 1000000)
		d.LinkSpeedStr = d.FormatLinkSpeed()
	}

	// Link state from connection status
	switch adapter.NetConnectionStatus {
	case 2: // Connected
		d.LinkState = "up"
		d.OperState = "UP"
		d.LinkDetected = true
	case 7: // Media disconnected
		d.LinkState = "down"
		d.OperState = "DOWN"
		d.LinkDetected = false
	default:
		d.LinkState = "unknown"
		d.OperState = "UNKNOWN"
	}

	// Get IP configuration
	config, err := getWindowsAdapterConfig(adapter.DeviceID)
	if err == nil {
		if len(config.IPAddress) > 0 {
			for _, ip := range config.IPAddress {
				if isIPv4(ip) {
					d.IPv4Addresses = append(d.IPv4Addresses, ip)
				} else {
					d.IPv6Addresses = append(d.IPv6Addresses, ip)
				}
			}
		}

		if len(config.DefaultIPGateway) > 0 {
			d.DefaultGateway = config.DefaultIPGateway[0]
		}

		d.DNSServers = config.DNSServerSearchOrder
	}

	// Windows typically uses full-duplex for modern NICs
	if d.LinkSpeed >= 100 {
		d.Duplex = "full"
	}

	// Get driver version from registry or WMI
	d.getWindowsDriverVersion(adapter)

	return nil
}

// getWindowsAdapter retrieves network adapter information via WMI
func getWindowsAdapter(ifaceName string) (*NetworkAdapter, error) {
	var adapters []NetworkAdapter

	// Try to match by name or description
	query := fmt.Sprintf("SELECT * FROM Win32_NetworkAdapter WHERE NetConnectionID='%s'", ifaceName)
	err := wmi.Query(query, &adapters)

	if err != nil || len(adapters) == 0 {
		// Try matching by name
		query = fmt.Sprintf("SELECT * FROM Win32_NetworkAdapter WHERE Name='%s'", ifaceName)
		err = wmi.Query(query, &adapters)
	}

	if err != nil {
		return nil, fmt.Errorf("WMI query failed: %w", err)
	}

	if len(adapters) == 0 {
		return nil, fmt.Errorf("adapter not found: %s", ifaceName)
	}

	return &adapters[0], nil
}

// getWindowsAdapterConfig retrieves IP configuration via WMI
func getWindowsAdapterConfig(deviceID string) (*NetworkAdapterConfiguration, error) {
	var configs []NetworkAdapterConfiguration

	query := fmt.Sprintf("SELECT * FROM Win32_NetworkAdapterConfiguration WHERE Index=%s", deviceID)
	err := wmi.Query(query, &configs)

	if err != nil {
		return nil, err
	}

	if len(configs) == 0 {
		return nil, fmt.Errorf("configuration not found")
	}

	return &configs[0], nil
}

// getWindowsDriverVersion attempts to get driver version
func (d *NetworkInterfaceDetail) getWindowsDriverVersion(adapter *NetworkAdapter) {
	// Try to get from Win32_PnPSignedDriver
	type PnPDriver struct {
		DeviceID      string
		DriverVersion string
		DriverDate    string
		InfName       string
	}

	var drivers []PnPDriver
	query := fmt.Sprintf("SELECT * FROM Win32_PnPSignedDriver WHERE DeviceID='%s'",
		escapeWMIString(adapter.PNPDeviceID))

	if err := wmi.Query(query, &drivers); err == nil && len(drivers) > 0 {
		d.DriverVersion = drivers[0].DriverVersion
	}
}

// isIPv4 checks if the IP address is IPv4
func isIPv4(ip string) bool {
	// Simple check - IPv4 contains dots, IPv6 contains colons
	return len(ip) > 0 && !contains(ip, ":")
}

// contains checks if string contains substring
func contains(s, substr string) bool {
	return len(s) > 0 && len(substr) > 0 &&
		   (s == substr || len(s) > len(substr) &&
		   (s[:len(substr)] == substr || s[len(s)-len(substr):] == substr ||
		   containsMiddle(s, substr)))
}

func containsMiddle(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// escapeWMIString escapes special characters for WMI queries
func escapeWMIString(s string) string {
	// Replace backslashes with double backslashes for WMI
	result := ""
	for _, c := range s {
		if c == '\\' {
			result += "\\\\"
		} else {
			result += string(c)
		}
	}
	return result
}

// GetWindowsNetworkStats retrieves network statistics
func GetWindowsNetworkStats(ifaceName string) (map[string]uint64, error) {
	// Use Win32_PerfFormattedData_Tcpip_NetworkInterface
	type NetworkStats struct {
		Name                string
		BytesReceivedPersec uint64
		BytesSentPersec     uint64
		PacketsReceivedPersec uint64
		PacketsSentPersec   uint64
		CurrentBandwidth    uint64
		OutputQueueLength   uint64
	}

	var stats []NetworkStats
	query := fmt.Sprintf("SELECT * FROM Win32_PerfFormattedData_Tcpip_NetworkInterface WHERE Name='%s'",
		ifaceName)

	err := wmi.Query(query, &stats)
	if err != nil {
		return nil, err
	}

	if len(stats) == 0 {
		return nil, fmt.Errorf("no stats found")
	}

	result := make(map[string]uint64)
	result["bytes_recv_per_sec"] = stats[0].BytesReceivedPersec
	result["bytes_sent_per_sec"] = stats[0].BytesSentPersec
	result["packets_recv_per_sec"] = stats[0].PacketsReceivedPersec
	result["packets_sent_per_sec"] = stats[0].PacketsSentPersec
	result["current_bandwidth"] = stats[0].CurrentBandwidth
	result["output_queue_length"] = stats[0].OutputQueueLength

	return result, nil
}

// GetWindowsNetworkStats retrieves detailed network statistics for an interface

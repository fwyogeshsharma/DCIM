package network

import (
	"encoding/json"
	"fmt"
	"net"
	"runtime"
	"time"
)

// NetworkInterfaceDetail contains comprehensive NIC information
type NetworkInterfaceDetail struct {
	// Basic Info
	Name         string    `json:"name"`
	Index        int       `json:"index"`
	HardwareAddr string    `json:"hardware_addr"`
	Timestamp    time.Time `json:"timestamp"`

	// IP Configuration
	IPv4Addresses  []string `json:"ipv4_addresses"`
	IPv6Addresses  []string `json:"ipv6_addresses"`
	DefaultGateway string   `json:"default_gateway"`
	DNSServers     []string `json:"dns_servers"`

	// Physical Layer
	LinkSpeed     int    `json:"link_speed_mbps"`      // Mbps: 10, 100, 1000, 10000
	LinkSpeedStr  string `json:"link_speed_str"`       // "1 Gbps", "10 Gbps"
	Duplex        string `json:"duplex"`               // "full", "half", "unknown"
	AutoNeg       bool   `json:"auto_neg"`             // Auto-negotiation enabled
	AutoNegActive bool   `json:"auto_neg_active"`      // Auto-negotiation succeeded
	LinkState     string `json:"link_state"`           // "up", "down"
	LinkDetected  bool   `json:"link_detected"`        // Physical link present

	// Switch Communication (LLDP)
	SwitchPort  string `json:"switch_port"`  // Connected switch port
	SwitchName  string `json:"switch_name"`  // Switch hostname
	SwitchDescr string `json:"switch_descr"` // Switch description
	VLANs       []int  `json:"vlans"`        // VLAN IDs

	// Hardware Details
	Driver          string `json:"driver"`           // "e1000e", "igb", "ixgbe"
	DriverVersion   string `json:"driver_version"`   // Driver version
	FirmwareVersion string `json:"firmware_version"` // NIC firmware
	Vendor          string `json:"vendor"`           // "Intel", "Broadcom"
	Model           string `json:"model"`            // "I350 Gigabit"
	PCIAddress      string `json:"pci_address"`      // "0000:02:00.0"
	PCILaneWidth    string `json:"pci_lane_width"`   // "x4", "x8"
	PCISpeed        string `json:"pci_speed"`        // "2.5 GT/s", "5 GT/s"

	// Network Configuration
	MTU        int      `json:"mtu"`         // 1500, 9000 (jumbo frames)
	Flags      []string `json:"flags"`       // UP, BROADCAST, MULTICAST
	TXQueueLen int      `json:"tx_queue_len"` // Transmit queue length

	// Statistics
	BytesSent      uint64 `json:"bytes_sent"`
	BytesRecv      uint64 `json:"bytes_recv"`
	PacketsSent    uint64 `json:"packets_sent"`
	PacketsRecv    uint64 `json:"packets_recv"`
	ErrorsIn       uint64 `json:"errors_in"`
	ErrorsOut      uint64 `json:"errors_out"`
	DropsIn        uint64 `json:"drops_in"`
	DropsOut       uint64 `json:"drops_out"`
	CollisionCount uint64 `json:"collision_count"`
	CarrierErrors  uint64 `json:"carrier_errors"`

	// Status
	OperState  string    `json:"oper_state"`  // "UP", "DOWN", "UNKNOWN"
	AdminState string    `json:"admin_state"` // "UP", "DOWN"
	LastChange time.Time `json:"last_change"` // Last state change

	// Transceiver Info (SFP/SFP+)
	TransceiverType   string  `json:"transceiver_type"`   // "SFP", "SFP+", "QSFP"
	TransceiverVendor string  `json:"transceiver_vendor"`
	TransceiverPN     string  `json:"transceiver_pn"` // Part number
	CableLength       int     `json:"cable_length"`   // In meters
	OpticalPower      float64 `json:"optical_power"`  // dBm
}

// LinkInfo contains link-specific information
type LinkInfo struct {
	LinkSpeed     int
	LinkSpeedStr  string
	Duplex        string
	AutoNeg       bool
	AutoNegActive bool
	LinkDetected  bool
}

// GetAllInterfaceDetails returns detailed information for all network interfaces
func GetAllInterfaceDetails() ([]*NetworkInterfaceDetail, error) {
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil, fmt.Errorf("get interfaces: %w", err)
	}

	var details []*NetworkInterfaceDetail

	for _, iface := range interfaces {
		detail, err := GetInterfaceDetail(iface.Name)
		if err != nil {
			// Log error but continue with other interfaces
			continue
		}
		details = append(details, detail)
	}

	return details, nil
}

// GetInterfaceDetail returns comprehensive details for a specific interface
func GetInterfaceDetail(ifaceName string) (*NetworkInterfaceDetail, error) {
	iface, err := net.InterfaceByName(ifaceName)
	if err != nil {
		return nil, fmt.Errorf("get interface %s: %w", ifaceName, err)
	}

	detail := &NetworkInterfaceDetail{
		Name:         iface.Name,
		Index:        iface.Index,
		HardwareAddr: iface.HardwareAddr.String(),
		MTU:          iface.MTU,
		Flags:        parseFlags(iface.Flags),
		Timestamp:    time.Now(),
	}

	// Get IP addresses
	if err := detail.getIPAddresses(iface); err != nil {
		// Non-fatal, continue
	}

	// Get platform-specific details
	switch runtime.GOOS {
	case "linux":
		if err := detail.getLinuxDetails(ifaceName); err != nil {
			// Non-fatal, log and continue
		}
	case "windows":
		if err := detail.getWindowsDetails(ifaceName); err != nil {
			// Non-fatal, log and continue
		}
	}

	// Get statistics
	if err := detail.getStatistics(ifaceName); err != nil {
		// Non-fatal, continue
	}

	// Get LLDP neighbor info (if available)
	if lldp, err := GetLLDPNeighbor(ifaceName); err == nil {
		detail.SwitchPort = lldp.SwitchPort
		detail.SwitchName = lldp.SwitchName
		detail.SwitchDescr = lldp.SwitchDescr
		detail.VLANs = lldp.VLANs
	}

	return detail, nil
}

// getIPAddresses extracts all IP addresses from the interface
func (d *NetworkInterfaceDetail) getIPAddresses(iface *net.Interface) error {
	addrs, err := iface.Addrs()
	if err != nil {
		return err
	}

	for _, addr := range addrs {
		ipNet, ok := addr.(*net.IPNet)
		if !ok {
			continue
		}

		ip := ipNet.IP
		if ip.To4() != nil {
			d.IPv4Addresses = append(d.IPv4Addresses, ip.String())
		} else if ip.To16() != nil {
			d.IPv6Addresses = append(d.IPv6Addresses, ip.String())
		}
	}

	return nil
}

// parseFlags converts interface flags to string slice
func parseFlags(flags net.Flags) []string {
	var result []string

	if flags&net.FlagUp != 0 {
		result = append(result, "UP")
	}
	if flags&net.FlagBroadcast != 0 {
		result = append(result, "BROADCAST")
	}
	if flags&net.FlagLoopback != 0 {
		result = append(result, "LOOPBACK")
	}
	if flags&net.FlagPointToPoint != 0 {
		result = append(result, "POINTOPOINT")
	}
	if flags&net.FlagMulticast != 0 {
		result = append(result, "MULTICAST")
	}

	return result
}

// ToJSON converts NetworkInterfaceDetail to JSON string
func (d *NetworkInterfaceDetail) ToJSON() (string, error) {
	data, err := json.Marshal(d)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

// FormatLinkSpeed formats link speed for display
func (d *NetworkInterfaceDetail) FormatLinkSpeed() string {
	if d.LinkSpeed == 0 {
		return "Unknown"
	}

	if d.LinkSpeed >= 1000 {
		return fmt.Sprintf("%d Gbps", d.LinkSpeed/1000)
	}
	return fmt.Sprintf("%d Mbps", d.LinkSpeed)
}

// IsUp returns true if the interface is operationally up
func (d *NetworkInterfaceDetail) IsUp() bool {
	return d.OperState == "UP" || d.LinkState == "up"
}

// HasIPv4 returns true if interface has IPv4 address
func (d *NetworkInterfaceDetail) HasIPv4() bool {
	return len(d.IPv4Addresses) > 0
}

// HasIPv6 returns true if interface has IPv6 address
func (d *NetworkInterfaceDetail) HasIPv6() bool {
	return len(d.IPv6Addresses) > 0
}

// GetPrimaryIPv4 returns the first IPv4 address or empty string
func (d *NetworkInterfaceDetail) GetPrimaryIPv4() string {
	if len(d.IPv4Addresses) > 0 {
		return d.IPv4Addresses[0]
	}
	return ""
}

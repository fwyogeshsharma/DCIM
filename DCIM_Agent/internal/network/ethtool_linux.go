//go:build linux

package network

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

// getLinuxDetails populates Linux-specific NIC details using ethtool and sysfs
func (d *NetworkInterfaceDetail) getLinuxDetails(ifaceName string) error {
	// Get link info from ethtool
	if linkInfo, err := getEthtoolLinkInfo(ifaceName); err == nil {
		d.LinkSpeed = linkInfo.LinkSpeed
		d.LinkSpeedStr = linkInfo.LinkSpeedStr
		d.Duplex = linkInfo.Duplex
		d.AutoNeg = linkInfo.AutoNeg
		d.AutoNegActive = linkInfo.AutoNegActive
		d.LinkDetected = linkInfo.LinkDetected
	}

	// Get driver info from ethtool
	if driverInfo, err := getEthtoolDriverInfo(ifaceName); err == nil {
		d.Driver = driverInfo.Driver
		d.DriverVersion = driverInfo.Version
		d.FirmwareVersion = driverInfo.FirmwareVersion
		d.PCIAddress = driverInfo.BusInfo
	}

	// Get operational state from sysfs
	d.getLinuxOperState(ifaceName)

	// Get TX queue length from sysfs
	d.getTXQueueLen(ifaceName)

	// Get vendor/model from PCI
	d.getLinuxVendorModel(ifaceName)

	return nil
}

// getEthtoolLinkInfo executes ethtool to get link information
func getEthtoolLinkInfo(ifaceName string) (*LinkInfo, error) {
	cmd := exec.Command("ethtool", ifaceName)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ethtool failed: %w", err)
	}

	info := &LinkInfo{}
	scanner := bufio.NewScanner(strings.NewReader(string(output)))

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "Speed:") {
			speedStr := strings.TrimSpace(strings.TrimPrefix(line, "Speed:"))
			info.LinkSpeed = parseLinkSpeed(speedStr)
			info.LinkSpeedStr = speedStr
		}

		if strings.HasPrefix(line, "Duplex:") {
			duplex := strings.TrimSpace(strings.TrimPrefix(line, "Duplex:"))
			info.Duplex = strings.ToLower(duplex)
		}

		if strings.HasPrefix(line, "Auto-negotiation:") {
			autoNeg := strings.TrimSpace(strings.TrimPrefix(line, "Auto-negotiation:"))
			info.AutoNeg = strings.ToLower(autoNeg) == "on"
		}

		if strings.HasPrefix(line, "Link detected:") {
			link := strings.TrimSpace(strings.TrimPrefix(line, "Link detected:"))
			info.LinkDetected = strings.ToLower(link) == "yes"
		}
	}

	return info, nil
}

// parseLinkSpeed converts speed string to Mbps integer
func parseLinkSpeed(speedStr string) int {
	speedStr = strings.ToLower(strings.TrimSpace(speedStr))

	// Handle "Unknown!" or similar
	if strings.Contains(speedStr, "unknown") {
		return 0
	}

	// Parse Gbps: "1Gb/s" or "10Gb/s"
	if strings.Contains(speedStr, "gb/s") {
		gbpsStr := strings.TrimSuffix(speedStr, "gb/s")
		if gbps, err := strconv.ParseFloat(gbpsStr, 64); err == nil {
			return int(gbps * 1000)
		}
	}

	// Parse Mbps: "100Mb/s" or "1000Mb/s"
	if strings.Contains(speedStr, "mb/s") {
		mbpsStr := strings.TrimSuffix(speedStr, "mb/s")
		if mbps, err := strconv.ParseFloat(mbpsStr, 64); err == nil {
			return int(mbps)
		}
	}

	return 0
}

// DriverInfo contains driver information from ethtool
type DriverInfo struct {
	Driver          string
	Version         string
	FirmwareVersion string
	BusInfo         string
	ExpansionROM    string
}

// getEthtoolDriverInfo executes ethtool -i to get driver information
func getEthtoolDriverInfo(ifaceName string) (*DriverInfo, error) {
	cmd := exec.Command("ethtool", "-i", ifaceName)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ethtool -i failed: %w", err)
	}

	info := &DriverInfo{}
	scanner := bufio.NewScanner(strings.NewReader(string(output)))

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}

		key := strings.TrimSpace(parts[0])
		value := strings.TrimSpace(parts[1])

		switch key {
		case "driver":
			info.Driver = value
		case "version":
			info.Version = value
		case "firmware-version":
			info.FirmwareVersion = value
		case "bus-info":
			info.BusInfo = value
		case "expansion-rom-version":
			info.ExpansionROM = value
		}
	}

	return info, nil
}

// getLinuxOperState reads operational state from sysfs
func (d *NetworkInterfaceDetail) getLinuxOperState(ifaceName string) {
	// Read /sys/class/net/<interface>/operstate
	operStatePath := fmt.Sprintf("/sys/class/net/%s/operstate", ifaceName)
	if data, err := os.ReadFile(operStatePath); err == nil {
		d.OperState = strings.ToUpper(strings.TrimSpace(string(data)))

		if d.OperState == "UP" {
			d.LinkState = "up"
		} else {
			d.LinkState = "down"
		}
	}

	// Read carrier state
	carrierPath := fmt.Sprintf("/sys/class/net/%s/carrier", ifaceName)
	if data, err := os.ReadFile(carrierPath); err == nil {
		carrier := strings.TrimSpace(string(data))
		d.LinkDetected = carrier == "1"
	}
}

// getTXQueueLen reads TX queue length from sysfs
func (d *NetworkInterfaceDetail) getTXQueueLen(ifaceName string) {
	txQueuePath := fmt.Sprintf("/sys/class/net/%s/tx_queue_len", ifaceName)
	if data, err := os.ReadFile(txQueuePath); err == nil {
		queueLen := strings.TrimSpace(string(data))
		if val, err := strconv.Atoi(queueLen); err == nil {
			d.TXQueueLen = val
		}
	}
}

// getLinuxVendorModel reads vendor and model from PCI
func (d *NetworkInterfaceDetail) getLinuxVendorModel(ifaceName string) {
	if d.PCIAddress == "" {
		return
	}

	var vendorID string

	// Read vendor
	vendorPath := fmt.Sprintf("/sys/class/net/%s/device/vendor", ifaceName)
	if data, err := os.ReadFile(vendorPath); err == nil {
		vendorID = strings.TrimSpace(string(data))
		d.Vendor = mapPCIVendor(vendorID)
	}

	// Read device (model)
	devicePath := fmt.Sprintf("/sys/class/net/%s/device/device", ifaceName)
	if data, err := os.ReadFile(devicePath); err == nil {
		deviceID := strings.TrimSpace(string(data))
		d.Model = mapPCIDevice(vendorID, deviceID)
	}

	// Read PCI speed and width
	d.getLinuxPCIInfo(ifaceName)
}

// getLinuxPCIInfo reads PCI lane width and speed
func (d *NetworkInterfaceDetail) getLinuxPCIInfo(ifaceName string) {
	// Read current link width
	widthPath := fmt.Sprintf("/sys/class/net/%s/device/current_link_width", ifaceName)
	if data, err := os.ReadFile(widthPath); err == nil {
		width := strings.TrimSpace(string(data))
		d.PCILaneWidth = fmt.Sprintf("x%s", width)
	}

	// Read current link speed
	speedPath := fmt.Sprintf("/sys/class/net/%s/device/current_link_speed", ifaceName)
	if data, err := os.ReadFile(speedPath); err == nil {
		speed := strings.TrimSpace(string(data))
		// Speed is in format like "8.0 GT/s" or "5 GT/s"
		d.PCISpeed = speed
	}
}

// mapPCIVendor maps PCI vendor ID to vendor name
func mapPCIVendor(vendorID string) string {
	vendors := map[string]string{
		"0x8086": "Intel Corporation",
		"0x14e4": "Broadcom Inc.",
		"0x10ec": "Realtek Semiconductor Co., Ltd.",
		"0x1969": "Qualcomm Atheros",
		"0x168c": "Qualcomm Atheros",
		"0x15b3": "Mellanox Technologies",
		"0x1077": "QLogic Corp.",
		"0x19a2": "Emulex Corporation",
	}

	if vendor, ok := vendors[vendorID]; ok {
		return vendor
	}
	return vendorID
}

// mapPCIDevice maps PCI device ID to model name (simplified)
func mapPCIDevice(vendorID, deviceID string) string {
	// This is a simplified mapping - in production, use a PCI ID database
	// or read from /usr/share/hwdata/pci.ids

	key := fmt.Sprintf("%s:%s", vendorID, deviceID)
	devices := map[string]string{
		"0x8086:0x10d3": "Intel 82574L Gigabit Network Connection",
		"0x8086:0x1521": "Intel I350 Gigabit Network Connection",
		"0x8086:0x1533": "Intel I210 Gigabit Network Connection",
		"0x14e4:0x165f": "Broadcom NetXtreme BCM5720 Gigabit Ethernet",
		"0x10ec:0x8168": "Realtek RTL8111/8168/8411 PCI Express Gigabit Ethernet",
	}

	if device, ok := devices[key]; ok {
		return device
	}
	return deviceID
}

// GetEthtoolStatistics gets detailed statistics from ethtool -S
func GetEthtoolStatistics(ifaceName string) (map[string]uint64, error) {
	cmd := exec.Command("ethtool", "-S", ifaceName)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ethtool -S failed: %w", err)
	}

	stats := make(map[string]uint64)
	scanner := bufio.NewScanner(strings.NewReader(string(output)))

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "NIC statistics:") {
			continue
		}

		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}

		key := strings.TrimSpace(parts[0])
		valueStr := strings.TrimSpace(parts[1])

		if value, err := strconv.ParseUint(valueStr, 10, 64); err == nil {
			stats[key] = value
		}
	}

	return stats, nil
}

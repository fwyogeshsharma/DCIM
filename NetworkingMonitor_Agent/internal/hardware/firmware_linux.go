//go:build linux

package hardware

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// getNetworkCardFirmware gets NIC firmware on Linux
func getNetworkCardFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Use ethtool to get firmware for each interface
	interfaces, err := getNetworkInterfaces()
	if err != nil {
		return firmware, nil
	}

	for _, iface := range interfaces {
		if nicFw, err := getNICFirmwareLinux(iface); err == nil {
			firmware = append(firmware, nicFw)
		}
	}

	return firmware, nil
}

// getNICFirmwareLinux gets firmware for a specific NIC
func getNICFirmwareLinux(iface string) (*ComponentFirmware, error) {
	cmd := exec.Command("ethtool", "-i", iface)
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	fw := &ComponentFirmware{
		Component: "NIC",
		Location:  iface,
	}

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
			fw.Driver = value
		case "version":
			// This is driver version, not firmware
			fw.Driver = fmt.Sprintf("%s %s", fw.Driver, value)
		case "firmware-version":
			fw.Firmware = value
		case "bus-info":
			fw.Location = value
		}
	}

	// Get vendor/model from lspci if we have PCI address
	if fw.Location != "" && strings.HasPrefix(fw.Location, "0000:") {
		if vendor, model := getPCIDeviceInfo(fw.Location); vendor != "" {
			fw.Vendor = vendor
			fw.Model = model
		}
	}

	return fw, nil
}

// getStorageFirmware gets storage controller/disk firmware on Linux
func getStorageFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Get NVMe firmware
	if nvmeFirmware, err := getNVMeFirmware(); err == nil {
		firmware = append(firmware, nvmeFirmware...)
	}

	// Get SATA/SAS disk firmware via smartctl
	if diskFirmware, err := getDiskFirmware(); err == nil {
		firmware = append(firmware, diskFirmware...)
	}

	// Get RAID controller firmware
	if raidFirmware, err := getRAIDFirmware(); err == nil {
		firmware = append(firmware, raidFirmware...)
	}

	return firmware, nil
}

// getNVMeFirmware gets NVMe SSD firmware
func getNVMeFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Find NVMe devices
	cmd := exec.Command("nvme", "list")
	output, err := cmd.Output()
	if err != nil {
		return firmware, nil // nvme-cli not installed or no NVMe devices
	}

	lines := strings.Split(string(output), "\n")
	for _, line := range lines {
		if strings.HasPrefix(line, "/dev/nvme") {
			parts := strings.Fields(line)
			if len(parts) >= 4 {
				device := parts[0]

				fw := &ComponentFirmware{
					Component: "Storage",
					Vendor:    "NVMe",
					Location:  device,
				}

				// Get detailed info
				if nvmeInfo, err := getNVMeDetailedInfo(device); err == nil {
					fw.Model = nvmeInfo.Model
					fw.Firmware = nvmeInfo.Firmware
					fw.SerialNumber = nvmeInfo.Serial
				}

				firmware = append(firmware, fw)
			}
		}
	}

	return firmware, nil
}

// NVMeInfo contains NVMe device information
type NVMeInfo struct {
	Model    string
	Serial   string
	Firmware string
}

// getNVMeDetailedInfo gets detailed NVMe information
func getNVMeDetailedInfo(device string) (*NVMeInfo, error) {
	cmd := exec.Command("nvme", "id-ctrl", device)
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	info := &NVMeInfo{}
	scanner := bufio.NewScanner(strings.NewReader(string(output)))

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "mn") {
			// Model number
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				info.Model = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "sn") {
			// Serial number
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				info.Serial = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "fr") {
			// Firmware revision
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				info.Firmware = strings.TrimSpace(parts[1])
			}
		}
	}

	return info, nil
}

// getDiskFirmware gets SATA/SAS disk firmware via smartctl
func getDiskFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// This requires smartmontools
	// Check if smartctl is available
	if _, err := exec.LookPath("smartctl"); err != nil {
		return firmware, nil // smartmontools not installed
	}

	// Scan for disks
	cmd := exec.Command("smartctl", "--scan")
	output, err := cmd.Output()
	if err != nil {
		return firmware, nil
	}

	lines := strings.Split(string(output), "\n")
	for _, line := range lines {
		if line == "" {
			continue
		}

		parts := strings.Fields(line)
		if len(parts) > 0 {
			device := parts[0]

			if diskFw, err := getDiskDetailedInfo(device); err == nil {
				firmware = append(firmware, diskFw)
			}
		}
	}

	return firmware, nil
}

// getDiskDetailedInfo gets detailed disk information via smartctl
func getDiskDetailedInfo(device string) (*ComponentFirmware, error) {
	cmd := exec.Command("smartctl", "-i", device)
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	fw := &ComponentFirmware{
		Component: "Storage",
		Location:  device,
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "Device Model:") || strings.HasPrefix(line, "Model Family:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Model = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Serial Number:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.SerialNumber = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Firmware Version:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Firmware = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Vendor:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Vendor = strings.TrimSpace(parts[1])
			}
		}
	}

	return fw, nil
}

// getRAIDFirmware gets RAID controller firmware
func getRAIDFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Try MegaRAID
	if megaraidFw, err := getMegaRAIDFirmware(); err == nil {
		firmware = append(firmware, megaraidFw)
	}

	// Try Linux MD RAID (software RAID)
	// Note: Software RAID doesn't have firmware

	return firmware, nil
}

// getMegaRAIDFirmware gets LSI/Broadcom MegaRAID controller firmware
func getMegaRAIDFirmware() (*ComponentFirmware, error) {
	// Check if storcli or megacli is available
	var cmd *exec.Cmd

	if _, err := exec.LookPath("storcli64"); err == nil {
		cmd = exec.Command("storcli64", "/c0", "show")
	} else if _, err := exec.LookPath("MegaCli64"); err == nil {
		cmd = exec.Command("MegaCli64", "-AdpAllInfo", "-a0")
	} else {
		return nil, fmt.Errorf("no RAID CLI tool found")
	}

	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	fw := &ComponentFirmware{
		Component: "RAID",
		Vendor:    "LSI/Broadcom",
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.Contains(line, "FW Package Build") || strings.Contains(line, "FW Version") {
			parts := strings.Split(line, "=")
			if len(parts) == 2 {
				fw.Firmware = strings.TrimSpace(parts[1])
			}
		} else if strings.Contains(line, "Product Name") {
			parts := strings.Split(line, "=")
			if len(parts) == 2 {
				fw.Model = strings.TrimSpace(parts[1])
			}
		}
	}

	return fw, nil
}

// getLinuxFirmware gets additional Linux-specific firmware
func getLinuxFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Get BMC/IPMI firmware (if available)
	if bmcFw, err := getBMCFirmware(); err == nil {
		firmware = append(firmware, bmcFw)
	}

	return firmware, nil
}

// getBMCFirmware gets BMC/iDRAC/iLO firmware via IPMI
func getBMCFirmware() (*ComponentFirmware, error) {
	// Check if ipmitool is available
	if _, err := exec.LookPath("ipmitool"); err != nil {
		return nil, fmt.Errorf("ipmitool not found")
	}

	cmd := exec.Command("ipmitool", "mc", "info")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	fw := &ComponentFirmware{
		Component: "BMC",
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if strings.HasPrefix(line, "Firmware Revision") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Firmware = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Manufacturer Name") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Vendor = strings.TrimSpace(parts[1])
			}
		} else if strings.HasPrefix(line, "Product Name") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				fw.Model = strings.TrimSpace(parts[1])
			}
		}
	}

	return fw, nil
}

// getNetworkInterfaces gets list of network interfaces
func getNetworkInterfaces() ([]string, error) {
	cmd := exec.Command("ls", "/sys/class/net")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	interfaces := strings.Fields(string(output))
	var result []string

	// Filter out loopback
	for _, iface := range interfaces {
		if iface != "lo" {
			result = append(result, iface)
		}
	}

	return result, nil
}

// getPCIDeviceInfo gets vendor and model from lspci
func getPCIDeviceInfo(pciAddr string) (string, string) {
	cmd := exec.Command("lspci", "-s", pciAddr, "-vm")
	output, err := cmd.Output()
	if err != nil {
		return "", ""
	}

	var vendor, model string
	scanner := bufio.NewScanner(strings.NewReader(string(output)))

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "Vendor:") {
			vendor = strings.TrimSpace(strings.TrimPrefix(line, "Vendor:"))
		} else if strings.HasPrefix(line, "Device:") {
			model = strings.TrimSpace(strings.TrimPrefix(line, "Device:"))
		}
	}

	return vendor, model
}

// getBIOSInfoLinux retrieves BIOS info on Linux via DMI
func getBIOSInfoLinux() (*BIOSInfo, error) {
	info := &BIOSInfo{
		Timestamp: time.Now(),
	}

	dmiPath := "/sys/class/dmi/id"

	// BIOS vendor
	if data, err := os.ReadFile(dmiPath + "/bios_vendor"); err == nil {
		info.Vendor = strings.TrimSpace(string(data))
	}

	// BIOS version
	if data, err := os.ReadFile(dmiPath + "/bios_version"); err == nil {
		info.Version = strings.TrimSpace(string(data))
	}

	// BIOS date
	if data, err := os.ReadFile(dmiPath + "/bios_date"); err == nil {
		info.ReleaseDate = strings.TrimSpace(string(data))
	}

	// BIOS release/revision
	if data, err := os.ReadFile(dmiPath + "/bios_release"); err == nil {
		info.Revision = strings.TrimSpace(string(data))
	}

	// Detect UEFI mode
	if _, err := os.Stat("/sys/firmware/efi"); err == nil {
		info.BIOSMode = "UEFI"
	} else {
		info.BIOSMode = "Legacy"
	}

	// Secure Boot state (UEFI only)
	if info.BIOSMode == "UEFI" {
		if data, err := os.ReadFile("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"); err == nil {
			// Last byte indicates Secure Boot state: 1=enabled, 0=disabled
			if len(data) > 0 && data[len(data)-1] == 1 {
				info.SecureBootState = "Enabled"
			} else {
				info.SecureBootState = "Disabled"
			}
		}
	}

	return info, nil
}

// getMotherboardInfoLinux retrieves motherboard info via DMI
func getMotherboardInfoLinux() (*MotherboardInfo, error) {
	info := &MotherboardInfo{
		Timestamp: time.Now(),
	}

	dmiPath := "/sys/class/dmi/id"

	// Board vendor/manufacturer
	if data, err := os.ReadFile(dmiPath + "/board_vendor"); err == nil {
		info.Manufacturer = strings.TrimSpace(string(data))
	}

	// Board name/product
	if data, err := os.ReadFile(dmiPath + "/board_name"); err == nil {
		info.Product = strings.TrimSpace(string(data))
	}

	// Board version
	if data, err := os.ReadFile(dmiPath + "/board_version"); err == nil {
		info.Version = strings.TrimSpace(string(data))
	}

	// Board serial number
	if data, err := os.ReadFile(dmiPath + "/board_serial"); err == nil {
		info.SerialNumber = strings.TrimSpace(string(data))
	}

	// Board asset tag
	if data, err := os.ReadFile(dmiPath + "/board_asset_tag"); err == nil {
		info.AssetTag = strings.TrimSpace(string(data))
	}

	// System UUID
	if data, err := os.ReadFile(dmiPath + "/product_uuid"); err == nil {
		info.UUID = strings.TrimSpace(string(data))
	}

	return info, nil
}

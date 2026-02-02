package hardware

import (
	"fmt"
	"runtime"
	"time"
)

// BIOSInfo contains BIOS/UEFI information
type BIOSInfo struct {
	Vendor          string    `json:"vendor"`
	Version         string    `json:"version"`
	ReleaseDate     string    `json:"release_date"`
	Revision        string    `json:"revision"`
	BIOSMode        string    `json:"bios_mode"`         // "UEFI" or "Legacy"
	SecureBootState string    `json:"secure_boot_state"` // "Enabled" or "Disabled"
	SerialNumber    string    `json:"serial_number"`
	Timestamp       time.Time `json:"timestamp"`
}

// MotherboardInfo contains motherboard information
type MotherboardInfo struct {
	Manufacturer string    `json:"manufacturer"`
	Product      string    `json:"product"`
	Version      string    `json:"version"`
	SerialNumber string    `json:"serial_number"`
	AssetTag     string    `json:"asset_tag"`
	UUID         string    `json:"uuid"`
	Timestamp    time.Time `json:"timestamp"`
}

// SystemInfo contains comprehensive system information
type SystemInfo struct {
	BIOS        *BIOSInfo            `json:"bios"`
	Motherboard *MotherboardInfo     `json:"motherboard"`
	Firmware    []*ComponentFirmware `json:"firmware"`
	Timestamp   time.Time            `json:"timestamp"`
}

// ComponentFirmware contains firmware information for a component
type ComponentFirmware struct {
	Component    string `json:"component"`     // "NIC", "RAID", "HBA", "BMC", "Storage"
	Vendor       string `json:"vendor"`
	Model        string `json:"model"`
	Firmware     string `json:"firmware"`      // Firmware version
	Driver       string `json:"driver"`        // Driver version (if applicable)
	SerialNumber string `json:"serial_number"`
	Location     string `json:"location"`      // PCI address, slot, etc.
}

// GetBIOSInfo retrieves BIOS/UEFI information
func GetBIOSInfo() (*BIOSInfo, error) {
	switch runtime.GOOS {
	case "windows":
		return getBIOSInfoWindows()
	case "linux":
		return getBIOSInfoLinux()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetMotherboardInfo retrieves motherboard information
func GetMotherboardInfo() (*MotherboardInfo, error) {
	switch runtime.GOOS {
	case "windows":
		return getMotherboardInfoWindows()
	case "linux":
		return getMotherboardInfoLinux()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetAllSystemInfo retrieves all system information
func GetAllSystemInfo() (*SystemInfo, error) {
	info := &SystemInfo{
		Timestamp: time.Now(),
	}

	var err error

	// Get BIOS info
	info.BIOS, err = GetBIOSInfo()
	if err != nil {
		return nil, fmt.Errorf("get BIOS info: %w", err)
	}

	// Get motherboard info
	info.Motherboard, err = GetMotherboardInfo()
	if err != nil {
		return nil, fmt.Errorf("get motherboard info: %w", err)
	}

	// Get component firmware
	info.Firmware, err = GetComponentFirmware()
	if err != nil {
		// Non-fatal, continue
		info.Firmware = nil
	}

	return info, nil
}

// GetComponentFirmware retrieves firmware for all components
func GetComponentFirmware() ([]*ComponentFirmware, error) {
	var allFirmware []*ComponentFirmware

	// Get platform-specific firmware
	switch runtime.GOOS {
	case "windows":
		if fw, err := getWindowsFirmware(); err == nil {
			allFirmware = append(allFirmware, fw...)
		}
	case "linux":
		if fw, err := getLinuxFirmware(); err == nil {
			allFirmware = append(allFirmware, fw...)
		}
	}

	return allFirmware, nil
}

// Platform-specific functions (implemented in firmware_windows.go and firmware_linux.go)
// - getBIOSInfoWindows() *BIOSInfo
// - getBIOSInfoLinux() *BIOSInfo
// - getMotherboardInfoWindows() *MotherboardInfo
// - getMotherboardInfoLinux() *MotherboardInfo
// - getWindowsFirmware() []*ComponentFirmware
// - getLinuxFirmware() []*ComponentFirmware

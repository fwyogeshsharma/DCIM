//go:build !linux

package hardware

import (
	"fmt"
)

// getBIOSInfoLinux is a stub for non-Linux platforms
func getBIOSInfoLinux() (*BIOSInfo, error) {
	return nil, fmt.Errorf("Linux BIOS info not available on this platform")
}

// getMotherboardInfoLinux is a stub for non-Linux platforms
func getMotherboardInfoLinux() (*MotherboardInfo, error) {
	return nil, fmt.Errorf("Linux motherboard info not available on this platform")
}

// getLinuxFirmware is a stub for non-Linux platforms
func getLinuxFirmware() ([]*ComponentFirmware, error) {
	return nil, nil // Return empty list on non-Linux
}

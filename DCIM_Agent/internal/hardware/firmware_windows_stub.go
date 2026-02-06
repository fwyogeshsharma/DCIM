//go:build !windows

package hardware

import (
	"fmt"
)

// getBIOSInfoWindows is a stub for non-Windows platforms
func getBIOSInfoWindows() (*BIOSInfo, error) {
	return nil, fmt.Errorf("Windows BIOS info not available on this platform")
}

// getMotherboardInfoWindows is a stub for non-Windows platforms
func getMotherboardInfoWindows() (*MotherboardInfo, error) {
	return nil, fmt.Errorf("Windows motherboard info not available on this platform")
}

// getWindowsFirmware is a stub for non-Windows platforms
func getWindowsFirmware() ([]*ComponentFirmware, error) {
	return nil, nil // Return empty list on non-Windows
}

// detectBIOSMode is a stub for non-Windows platforms
func detectBIOSMode() string {
	return "Unknown"
}

// getSecureBootState is a stub for non-Windows platforms
func getSecureBootState() string {
	return "Unknown"
}

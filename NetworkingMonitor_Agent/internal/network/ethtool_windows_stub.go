//go:build !windows

package network

import "strings"

// getWindowsDetails is a stub for non-Windows platforms
func (d *NetworkInterfaceDetail) getWindowsDetails(ifaceName string) error {
	// Not implemented on non-Windows platforms
	return nil
}

// GetWindowsNetworkStats is a stub for non-Windows platforms
func GetWindowsNetworkStats(ifaceName string) (map[string]uint64, error) {
	// Not implemented on non-Windows platforms
	return make(map[string]uint64), nil
}

// contains checks if a string contains a substring (case-insensitive)
func contains(s, substr string) bool {
	return strings.Contains(strings.ToLower(s), strings.ToLower(substr))
}

//go:build !linux

package network

// getLinuxDetails is a stub for non-Linux platforms
func (d *NetworkInterfaceDetail) getLinuxDetails(ifaceName string) error {
	// Not implemented on non-Linux platforms
	return nil
}

// GetEthtoolStatistics is a stub for non-Linux platforms
func GetEthtoolStatistics(ifaceName string) (map[string]uint64, error) {
	// Not implemented on non-Linux platforms
	return make(map[string]uint64), nil
}

//go:build windows

package hardware

import (
	"fmt"
	"strings"
	"time"

	"github.com/StackExchange/wmi"
)

// getNetworkCardFirmware gets NIC firmware on Windows
func getNetworkCardFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Win32_NetworkAdapter
	type NetworkAdapter struct {
		Name                string
		Manufacturer        string
		ProductName         string
		ServiceName         string
		PNPDeviceID         string
		MACAddress          string
	}

	var adapters []NetworkAdapter
	err := wmi.Query("SELECT * FROM Win32_NetworkAdapter WHERE PhysicalAdapter=TRUE", &adapters)
	if err != nil {
		return firmware, nil
	}

	for _, adapter := range adapters {
		fw := &ComponentFirmware{
			Component: "NIC",
			Vendor:    adapter.Manufacturer,
			Model:     adapter.ProductName,
			Driver:    adapter.ServiceName,
			Location:  adapter.Name,
		}

		// Get firmware version from PnP device
		if adapter.PNPDeviceID != "" {
			if fwVersion, err := getDeviceFirmwareVersion(adapter.PNPDeviceID); err == nil {
				fw.Firmware = fwVersion
			}

			// Get driver version
			if drvVersion, err := getDriverVersion(adapter.PNPDeviceID); err == nil {
				fw.Driver = fmt.Sprintf("%s %s", fw.Driver, drvVersion)
			}
		}

		firmware = append(firmware, fw)
	}

	return firmware, nil
}

// getStorageFirmware gets storage controller/disk firmware on Windows
func getStorageFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Get disk drive firmware
	type DiskDrive struct {
		Model            string
		SerialNumber     string
		FirmwareRevision string
		Manufacturer     string
		InterfaceType    string
		DeviceID         string
	}

	var drives []DiskDrive
	err := wmi.Query("SELECT * FROM Win32_DiskDrive", &drives)
	if err == nil {
		for _, drive := range drives {
			fw := &ComponentFirmware{
				Component:    "Storage",
				Vendor:       drive.Manufacturer,
				Model:        drive.Model,
				Firmware:     drive.FirmwareRevision,
				SerialNumber: drive.SerialNumber,
				Location:     drive.DeviceID,
			}

			firmware = append(firmware, fw)
		}
	}

	// Get RAID controller info (if any)
	if raidFirmware, err := getWindowsRAIDFirmware(); err == nil {
		firmware = append(firmware, raidFirmware...)
	}

	return firmware, nil
}

// getWindowsRAIDFirmware gets RAID controller firmware on Windows
func getWindowsRAIDFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Try to get RAID controller via WMI
	// Win32_SCSIController for RAID controllers
	type SCSIController struct {
		Name             string
		Manufacturer     string
		DriverVersion    string
		DeviceID         string
		ProtocolSupported uint16
	}

	var controllers []SCSIController
	err := wmi.Query("SELECT * FROM Win32_SCSIController", &controllers)
	if err != nil {
		return firmware, nil
	}

	for _, ctrl := range controllers {
		// Filter for RAID controllers (not regular SATA/SCSI)
		if contains(ctrl.Name, "RAID") || contains(ctrl.Name, "MegaRAID") ||
		   contains(ctrl.Name, "LSI") || contains(ctrl.Name, "Adaptec") {

			fw := &ComponentFirmware{
				Component: "RAID",
				Vendor:    ctrl.Manufacturer,
				Model:     ctrl.Name,
				Driver:    ctrl.DriverVersion,
				Location:  ctrl.DeviceID,
			}

			// Try to get firmware version
			if fwVersion, err := getRAIDFirmwareFromRegistry(ctrl.DeviceID); err == nil {
				fw.Firmware = fwVersion
			}

			firmware = append(firmware, fw)
		}
	}

	return firmware, nil
}

// getWindowsFirmware gets additional Windows-specific firmware
func getWindowsFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Get GPU firmware
	if gpuFirmware, err := getGPUFirmware(); err == nil {
		firmware = append(firmware, gpuFirmware...)
	}

	// Get USB controller firmware
	if usbFirmware, err := getUSBControllerFirmware(); err == nil {
		firmware = append(firmware, usbFirmware...)
	}

	return firmware, nil
}

// getGPUFirmware gets GPU firmware/driver information
func getGPUFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Win32_VideoController
	type VideoController struct {
		Name                 string
		AdapterCompatibility string
		DriverVersion        string
		VideoProcessor       string
		PNPDeviceID          string
	}

	var gpus []VideoController
	err := wmi.Query("SELECT * FROM Win32_VideoController", &gpus)
	if err != nil {
		return firmware, nil
	}

	for _, gpu := range gpus {
		fw := &ComponentFirmware{
			Component: "GPU",
			Vendor:    gpu.AdapterCompatibility,
			Model:     gpu.Name,
			Driver:    gpu.DriverVersion,
			Location:  gpu.PNPDeviceID,
		}

		// GPU "firmware" is typically the VBIOS version
		// This is harder to get on Windows without vendor-specific tools

		firmware = append(firmware, fw)
	}

	return firmware, nil
}

// getUSBControllerFirmware gets USB controller information
func getUSBControllerFirmware() ([]*ComponentFirmware, error) {
	var firmware []*ComponentFirmware

	// Win32_USBController
	type USBController struct {
		Name         string
		Manufacturer string
		DeviceID     string
	}

	var controllers []USBController
	err := wmi.Query("SELECT * FROM Win32_USBController", &controllers)
	if err != nil {
		return firmware, nil
	}

	for _, ctrl := range controllers {
		fw := &ComponentFirmware{
			Component: "USB",
			Vendor:    ctrl.Manufacturer,
			Model:     ctrl.Name,
			Location:  ctrl.DeviceID,
		}

		firmware = append(firmware, fw)
	}

	return firmware, nil
}

// getDeviceFirmwareVersion gets firmware version for a PnP device
func getDeviceFirmwareVersion(pnpDeviceID string) (string, error) {
	// Query Win32_PnPEntity for firmware info
	// Most devices don't expose firmware version directly via WMI
	// This would require reading from device-specific locations

	// For now, return empty - in production, this would query
	// device-specific locations or use vendor tools

	return "", fmt.Errorf("not implemented")
}

// getDriverVersion gets driver version for a PnP device
func getDriverVersion(pnpDeviceID string) (string, error) {
	// Win32_PnPSignedDriver
	type PnPDriver struct {
		DeviceID      string
		DriverVersion string
		DriverDate    string
	}

	escapedID := escapeWMIString(pnpDeviceID)
	query := fmt.Sprintf("SELECT * FROM Win32_PnPSignedDriver WHERE DeviceID='%s'", escapedID)

	var drivers []PnPDriver
	err := wmi.Query(query, &drivers)
	if err != nil || len(drivers) == 0 {
		return "", fmt.Errorf("driver not found")
	}

	return drivers[0].DriverVersion, nil
}

// getRAIDFirmwareFromRegistry gets RAID firmware from registry
func getRAIDFirmwareFromRegistry(deviceID string) (string, error) {
	// This would require reading Windows Registry
	// Different RAID controllers store firmware in different locations
	// Would need vendor-specific logic

	return "", fmt.Errorf("not implemented")
}

// contains checks if string contains substring
func contains(s, substr string) bool {
	return len(s) >= len(substr) && containsMiddle(s, substr)
}

// containsMiddle checks if string contains substring anywhere
func containsMiddle(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		match := true
		for j := 0; j < len(substr); j++ {
			if s[i+j] != substr[j] {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}

// escapeWMIString escapes special characters for WMI queries
func escapeWMIString(s string) string {
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

// getBIOSInfoWindows retrieves BIOS info on Windows via WMI
func getBIOSInfoWindows() (*BIOSInfo, error) {
	// Win32_BIOS class
	type Win32_BIOS struct {
		Manufacturer         string
		SMBIOSBIOSVersion    string
		ReleaseDate          *time.Time
		SerialNumber         string
		Version              string
		SMBIOSMajorVersion   uint16
		SMBIOSMinorVersion   uint16
	}

	var bios []Win32_BIOS
	err := wmi.Query("SELECT * FROM Win32_BIOS", &bios)
	if err != nil {
		return nil, fmt.Errorf("WMI query failed: %w", err)
	}

	if len(bios) == 0 {
		return nil, fmt.Errorf("no BIOS information found")
	}

	info := &BIOSInfo{
		Vendor:       bios[0].Manufacturer,
		Version:      bios[0].SMBIOSBIOSVersion,
		SerialNumber: bios[0].SerialNumber,
		Timestamp:    time.Now(),
	}

	if bios[0].ReleaseDate != nil {
		info.ReleaseDate = bios[0].ReleaseDate.Format("2006-01-02")
	}

	if bios[0].SMBIOSMajorVersion > 0 {
		info.Revision = fmt.Sprintf("%d.%d",
			bios[0].SMBIOSMajorVersion,
			bios[0].SMBIOSMinorVersion)
	}

	// Determine UEFI vs Legacy
	info.BIOSMode = detectBIOSMode()

	// Get Secure Boot state
	info.SecureBootState = getSecureBootState()

	return info, nil
}

// getMotherboardInfoWindows retrieves motherboard info via WMI
func getMotherboardInfoWindows() (*MotherboardInfo, error) {
	// Win32_BaseBoard class
	type Win32_BaseBoard struct {
		Manufacturer string
		Product      string
		Version      string
		SerialNumber string
		Tag          string
	}

	var board []Win32_BaseBoard
	err := wmi.Query("SELECT * FROM Win32_BaseBoard", &board)
	if err != nil {
		return nil, fmt.Errorf("WMI query failed: %w", err)
	}

	if len(board) == 0 {
		return nil, fmt.Errorf("no motherboard information found")
	}

	info := &MotherboardInfo{
		Manufacturer: board[0].Manufacturer,
		Product:      board[0].Product,
		Version:      board[0].Version,
		SerialNumber: board[0].SerialNumber,
		AssetTag:     board[0].Tag,
		Timestamp:    time.Now(),
	}

	// Get system UUID
	type Win32_ComputerSystemProduct struct {
		UUID string
	}

	var product []Win32_ComputerSystemProduct
	err = wmi.Query("SELECT UUID FROM Win32_ComputerSystemProduct", &product)
	if err == nil && len(product) > 0 {
		info.UUID = product[0].UUID
	}

	return info, nil
}

// detectBIOSMode detects UEFI vs Legacy mode on Windows
func detectBIOSMode() string {
	// Check if running in UEFI mode
	type Win32_ComputerSystem struct {
		BootupState string
	}

	var systems []Win32_ComputerSystem
	err := wmi.Query("SELECT BootupState FROM Win32_ComputerSystem", &systems)
	if err == nil && len(systems) > 0 {
		if strings.Contains(strings.ToLower(systems[0].BootupState), "uefi") {
			return "UEFI"
		}
	}

	return "Legacy"
}

// getSecureBootState gets Secure Boot state on Windows
func getSecureBootState() string {
	// Registry path: HKLM\SYSTEM\CurrentControlSet\Control\SecureBoot\State
	// Value: UEFISecureBootEnabled (1 = enabled, 0 = disabled)
	return "Unknown"
}


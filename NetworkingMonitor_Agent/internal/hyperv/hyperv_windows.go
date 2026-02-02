//go:build windows

package hyperv

import (
	"fmt"
	"time"

	"github.com/StackExchange/wmi"
)

// VMInfo represents a Hyper-V virtual machine
type VMInfo struct {
	Name                string    `json:"name"`
	VMId                string    `json:"vm_id"`
	State               string    `json:"state"`
	Status              string    `json:"status"`
	CPUUsage            uint16    `json:"cpu_usage"`
	MemoryAssigned      uint64    `json:"memory_assigned_mb"`
	MemoryDemand        uint64    `json:"memory_demand_mb"`
	Uptime              uint64    `json:"uptime_seconds"`
	Heartbeat           string    `json:"heartbeat"`
	IntegrationServices string    `json:"integration_services"`
	Version             string    `json:"version"`
	Generation          uint16    `json:"generation"`
	Timestamp           time.Time `json:"timestamp"`
}

// VMNetworkAdapter represents VM network adapter stats
type VMNetworkAdapter struct {
	VMName       string `json:"vm_name"`
	AdapterName  string `json:"adapter_name"`
	BytesReceived uint64 `json:"bytes_received"`
	BytesSent    uint64 `json:"bytes_sent"`
	PacketsReceived uint64 `json:"packets_received"`
	PacketsSent  uint64 `json:"packets_sent"`
}

// VMDiskInfo represents VM disk information
type VMDiskInfo struct {
	VMName     string `json:"vm_name"`
	Path       string `json:"path"`
	ReadBytes  uint64 `json:"read_bytes"`
	WriteBytes uint64 `json:"write_bytes"`
	ReadOps    uint64 `json:"read_ops"`
	WriteOps   uint64 `json:"write_ops"`
}

// HyperVInfo represents comprehensive Hyper-V host information
type HyperVInfo struct {
	Enabled            bool           `json:"enabled"`
	HostName           string         `json:"host_name"`
	VirtualMachines    []*VMInfo      `json:"virtual_machines"`
	TotalVMs           int            `json:"total_vms"`
	RunningVMs         int            `json:"running_vms"`
	StoppedVMs         int            `json:"stopped_vms"`
	LogicalProcessors  uint32         `json:"logical_processors"`
	TotalMemoryMB      uint64         `json:"total_memory_mb"`
	FreeMemoryMB       uint64         `json:"free_memory_mb"`
	Timestamp          time.Time      `json:"timestamp"`
}

// WMI structures for Hyper-V queries
type Msvm_ComputerSystem struct {
	ElementName               string
	Name                      string
	EnabledState              uint16
	HealthState               uint16
	OnTimeInMilliseconds      uint64
	ProcessID                 uint32
}

type Msvm_ProcessorSettingData struct {
	InstanceID             string
	VirtualQuantity        uint64
	Reservation            uint64
	Limit                  uint64
}

type Msvm_MemorySettingData struct {
	InstanceID             string
	VirtualQuantity        uint64
	Reservation            uint64
	Limit                  uint64
}

type Msvm_SummaryInformation struct {
	Name                    string
	ElementName             string
	EnabledState            uint16
	HealthState             uint16
	MemoryUsage             uint64
	MemoryAvailable         uint64
	ProcessorLoad           uint16
	Heartbeat               uint16
	NumberOfProcessors      uint16
	Snapshots               []string
}

// IsHyperVEnabled checks if Hyper-V is enabled on this system
func IsHyperVEnabled() bool {
	var computerSystems []Msvm_ComputerSystem
	query := "SELECT * FROM Msvm_ComputerSystem WHERE Caption = 'Virtual Machine'"
	err := wmi.QueryNamespace(query, &computerSystems, `root\virtualization\v2`)
	return err == nil
}

// GetHyperVInfo retrieves comprehensive Hyper-V information
func GetHyperVInfo() (*HyperVInfo, error) {
	if !IsHyperVEnabled() {
		return nil, fmt.Errorf("Hyper-V not enabled or not available")
	}

	info := &HyperVInfo{
		Enabled:   true,
		Timestamp: time.Now(),
	}

	// Get all VMs
	vms, err := GetVirtualMachines()
	if err != nil {
		return nil, fmt.Errorf("failed to get VMs: %w", err)
	}

	info.VirtualMachines = vms
	info.TotalVMs = len(vms)

	// Count running/stopped VMs
	for _, vm := range vms {
		if vm.State == "Running" {
			info.RunningVMs++
		} else if vm.State == "Stopped" || vm.State == "Off" {
			info.StoppedVMs++
		}
	}

	// Get host information
	type Win32_ComputerSystem struct {
		Name                  string
		NumberOfLogicalProcessors uint32
		TotalPhysicalMemory   uint64
	}

	var hostInfo []Win32_ComputerSystem
	err = wmi.Query("SELECT * FROM Win32_ComputerSystem", &hostInfo)
	if err == nil && len(hostInfo) > 0 {
		info.HostName = hostInfo[0].Name
		info.LogicalProcessors = hostInfo[0].NumberOfLogicalProcessors
		info.TotalMemoryMB = hostInfo[0].TotalPhysicalMemory / (1024 * 1024)
	}

	// Get free memory
	type Win32_PerfFormattedData_PerfOS_Memory struct {
		AvailableMBytes uint64
	}

	var memInfo []Win32_PerfFormattedData_PerfOS_Memory
	err = wmi.Query("SELECT * FROM Win32_PerfFormattedData_PerfOS_Memory", &memInfo)
	if err == nil && len(memInfo) > 0 {
		info.FreeMemoryMB = memInfo[0].AvailableMBytes
	}

	return info, nil
}

// GetVirtualMachines retrieves all Hyper-V VMs
func GetVirtualMachines() ([]*VMInfo, error) {
	var vms []*VMInfo

	// Query Msvm_ComputerSystem for VMs
	var computerSystems []Msvm_ComputerSystem
	query := "SELECT * FROM Msvm_ComputerSystem WHERE Caption = 'Virtual Machine'"
	err := wmi.QueryNamespace(query, &computerSystems, `root\virtualization\v2`)
	if err != nil {
		return nil, fmt.Errorf("failed to query VMs: %w", err)
	}

	for _, cs := range computerSystems {
		vm := &VMInfo{
			Name:      cs.ElementName,
			VMId:      cs.Name,
			State:     getVMState(cs.EnabledState),
			Status:    getHealthState(cs.HealthState),
			Uptime:    cs.OnTimeInMilliseconds / 1000, // Convert to seconds
			Timestamp: time.Now(),
		}

		// Try to get summary information for more details
		summaryInfo, err := getVMSummaryInformation(cs.Name)
		if err == nil {
			vm.CPUUsage = summaryInfo.ProcessorLoad
			vm.MemoryAssigned = summaryInfo.MemoryAvailable
			vm.MemoryDemand = summaryInfo.MemoryUsage
			vm.Heartbeat = getHeartbeatStatus(summaryInfo.Heartbeat)
		}

		vms = append(vms, vm)
	}

	return vms, nil
}

// getVMSummaryInformation retrieves summary information for a VM
func getVMSummaryInformation(vmId string) (*Msvm_SummaryInformation, error) {
	// This is a simplified version - full implementation would use
	// Msvm_VirtualSystemManagementService.GetSummaryInformation method
	var summaryInfo []Msvm_SummaryInformation
	query := fmt.Sprintf("SELECT * FROM Msvm_SummaryInformation WHERE Name = '%s'", vmId)
	err := wmi.QueryNamespace(query, &summaryInfo, `root\virtualization\v2`)
	if err != nil || len(summaryInfo) == 0 {
		return &Msvm_SummaryInformation{}, err
	}
	return &summaryInfo[0], nil
}

// GetVMNetworkAdapters retrieves network adapter statistics for all VMs
func GetVMNetworkAdapters() ([]*VMNetworkAdapter, error) {
	// Query Msvm_EthernetPortAllocationSettingData for VM network adapters
	type Msvm_EthernetSwitchPortBandwidthData struct {
		ElementName string
		BytesReceived uint64
		BytesSent    uint64
		PacketsReceived uint64
		PacketsSent  uint64
	}

	var adapters []*VMNetworkAdapter
	// Simplified - would need proper WMI queries for network stats
	return adapters, nil
}

// GetVMDiskInfo retrieves disk information for all VMs
func GetVMDiskInfo() ([]*VMDiskInfo, error) {
	// Query Msvm_StorageAllocationSettingData for VM disks
	var disks []*VMDiskInfo
	// Simplified - would need proper WMI queries for disk stats
	return disks, nil
}

// Helper functions for state mapping
func getVMState(enabledState uint16) string {
	switch enabledState {
	case 2:
		return "Running"
	case 3:
		return "Stopped"
	case 32768:
		return "Paused"
	case 32769:
		return "Saved"
	case 32770:
		return "Starting"
	case 32771:
		return "Snapshotting"
	case 32773:
		return "Saving"
	case 32774:
		return "Stopping"
	case 32776:
		return "Pausing"
	case 32777:
		return "Resuming"
	default:
		return "Unknown"
	}
}

func getHealthState(healthState uint16) string {
	switch healthState {
	case 5:
		return "OK"
	case 20:
		return "Major Failure"
	case 25:
		return "Critical Failure"
	default:
		return fmt.Sprintf("Unknown (%d)", healthState)
	}
}

func getHeartbeatStatus(heartbeat uint16) string {
	switch heartbeat {
	case 2:
		return "OK"
	case 6:
		return "Error"
	case 12:
		return "No Contact"
	case 13:
		return "Lost Communication"
	default:
		return "Unknown"
	}
}

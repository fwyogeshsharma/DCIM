//go:build !windows

package hyperv

import (
	"fmt"
	"time"
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

// IsHyperVEnabled is a stub for non-Windows platforms
func IsHyperVEnabled() bool {
	return false
}

// GetHyperVInfo is a stub for non-Windows platforms
func GetHyperVInfo() (*HyperVInfo, error) {
	return nil, fmt.Errorf("Hyper-V not available on this platform")
}

// GetVirtualMachines is a stub for non-Windows platforms
func GetVirtualMachines() ([]*VMInfo, error) {
	return nil, fmt.Errorf("Hyper-V not available on this platform")
}

// GetVMNetworkAdapters is a stub for non-Windows platforms
func GetVMNetworkAdapters() ([]*VMNetworkAdapter, error) {
	return nil, fmt.Errorf("Hyper-V not available on this platform")
}

// GetVMDiskInfo is a stub for non-Windows platforms
func GetVMDiskInfo() ([]*VMDiskInfo, error) {
	return nil, fmt.Errorf("Hyper-V not available on this platform")
}

package network

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"runtime"
	"strings"
)

// LLDPNeighbor represents LLDP neighbor information (switch/router)
type LLDPNeighbor struct {
	Interface    string   `json:"interface"`
	ChassisID    string   `json:"chassis_id"`
	SwitchName   string   `json:"switch_name"`
	SwitchDescr  string   `json:"switch_descr"`
	SwitchPort   string   `json:"switch_port"`
	PortDescr    string   `json:"port_descr"`
	VLANs        []int    `json:"vlans"`
	Capabilities []string `json:"capabilities"`
	MgmtAddress  string   `json:"mgmt_address"`
}

// GetLLDPNeighbor retrieves LLDP neighbor information for an interface
func GetLLDPNeighbor(ifaceName string) (*LLDPNeighbor, error) {
	if runtime.GOOS != "linux" {
		return nil, fmt.Errorf("LLDP only supported on Linux")
	}

	return getLLDPLinux(ifaceName)
}

// getLLDPLinux gets LLDP information using lldpcli on Linux
func getLLDPLinux(ifaceName string) (*LLDPNeighbor, error) {
	// Check if lldpcli is available
	if _, err := exec.LookPath("lldpcli"); err != nil {
		// lldpd not installed
		return nil, fmt.Errorf("lldpcli not found (install lldpd)")
	}

	// Try JSON output first (newer versions)
	neighbor, err := getLLDPJSON(ifaceName)
	if err == nil {
		return neighbor, nil
	}

	// Fall back to text parsing
	return getLLDPText(ifaceName)
}

// getLLDPJSON parses JSON output from lldpcli
func getLLDPJSON(ifaceName string) (*LLDPNeighbor, error) {
	cmd := exec.Command("lldpcli", "show", "neighbors", "ports", ifaceName, "-f", "json")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var data map[string]interface{}
	if err := json.Unmarshal(output, &data); err != nil {
		return nil, err
	}

	neighbor := &LLDPNeighbor{
		Interface: ifaceName,
	}

	// Navigate JSON structure
	// Structure: {"lldp":{"interface":[{"name":"eth0","port":{...},"chassis":{...}}]}}
	lldp, ok := data["lldp"].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("invalid JSON structure")
	}

	interfaces, ok := lldp["interface"].([]interface{})
	if !ok || len(interfaces) == 0 {
		return nil, fmt.Errorf("no interface data")
	}

	ifaceData, ok := interfaces[0].(map[string]interface{})
	if !ok {
		return nil, fmt.Errorf("invalid interface data")
	}

	// Extract chassis information
	if chassis, ok := ifaceData["chassis"].(map[string]interface{}); ok {
		if id, ok := chassis["id"].(map[string]interface{}); ok {
			if value, ok := id["value"].(string); ok {
				neighbor.ChassisID = value
			}
		}

		if name, ok := chassis["name"].(map[string]interface{}); ok {
			if value, ok := name["value"].(string); ok {
				neighbor.SwitchName = value
			}
		}

		if descr, ok := chassis["descr"].(map[string]interface{}); ok {
			if value, ok := descr["value"].(string); ok {
				neighbor.SwitchDescr = value
			}
		}

		if mgmt, ok := chassis["mgmt-ip"].(map[string]interface{}); ok {
			if value, ok := mgmt["value"].(string); ok {
				neighbor.MgmtAddress = value
			}
		}

		// Capabilities
		if cap, ok := chassis["capability"].([]interface{}); ok {
			for _, c := range cap {
				if capMap, ok := c.(map[string]interface{}); ok {
					if enabled, ok := capMap["enabled"].(bool); enabled && ok {
						if capType, ok := capMap["type"].(string); ok {
							neighbor.Capabilities = append(neighbor.Capabilities, capType)
						}
					}
				}
			}
		}
	}

	// Extract port information
	if port, ok := ifaceData["port"].(map[string]interface{}); ok {
		if id, ok := port["id"].(map[string]interface{}); ok {
			if value, ok := id["value"].(string); ok {
				neighbor.SwitchPort = value
			}
		}

		if descr, ok := port["descr"].(map[string]interface{}); ok {
			if value, ok := descr["value"].(string); ok {
				neighbor.PortDescr = value
			}
		}
	}

	// Extract VLAN information
	if vlan, ok := ifaceData["vlan"].([]interface{}); ok {
		for _, v := range vlan {
			if vlanMap, ok := v.(map[string]interface{}); ok {
				if vlanID, ok := vlanMap["vlan-id"].(float64); ok {
					neighbor.VLANs = append(neighbor.VLANs, int(vlanID))
				}
			}
		}
	}

	return neighbor, nil
}

// getLLDPText parses text output from lldpcli (fallback)
func getLLDPText(ifaceName string) (*LLDPNeighbor, error) {
	cmd := exec.Command("lldpcli", "show", "neighbors", "ports", ifaceName)
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	neighbor := &LLDPNeighbor{
		Interface: ifaceName,
	}

	lines := strings.Split(string(output), "\n")
	inChassis := false
	inPort := false

	for _, line := range lines {
		line = strings.TrimSpace(line)

		if strings.Contains(line, "Chassis:") {
			inChassis = true
			inPort = false
			continue
		}

		if strings.Contains(line, "Port:") {
			inPort = true
			inChassis = false
			continue
		}

		if inChassis {
			if strings.HasPrefix(line, "ChassisID:") {
				neighbor.ChassisID = extractValue(line)
			} else if strings.HasPrefix(line, "SysName:") {
				neighbor.SwitchName = extractValue(line)
			} else if strings.HasPrefix(line, "SysDescr:") {
				neighbor.SwitchDescr = extractValue(line)
			} else if strings.HasPrefix(line, "MgmtIP:") {
				neighbor.MgmtAddress = extractValue(line)
			} else if strings.HasPrefix(line, "Capability:") {
				caps := extractValue(line)
				neighbor.Capabilities = strings.Split(caps, ",")
			}
		}

		if inPort {
			if strings.HasPrefix(line, "PortID:") {
				neighbor.SwitchPort = extractValue(line)
			} else if strings.HasPrefix(line, "PortDescr:") {
				neighbor.PortDescr = extractValue(line)
			}
		}
	}

	return neighbor, nil
}

// extractValue extracts value from "Key: Value" format
func extractValue(line string) string {
	parts := strings.SplitN(line, ":", 2)
	if len(parts) == 2 {
		return strings.TrimSpace(parts[1])
	}
	return ""
}

// GetAllLLDPNeighbors retrieves LLDP neighbors for all interfaces
func GetAllLLDPNeighbors() ([]*LLDPNeighbor, error) {
	if runtime.GOOS != "linux" {
		return nil, fmt.Errorf("LLDP only supported on Linux")
	}

	// Get all interfaces with LLDP neighbors
	cmd := exec.Command("lldpcli", "show", "neighbors", "-f", "json")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var data map[string]interface{}
	if err := json.Unmarshal(output, &data); err != nil {
		// Try text parsing
		return getAllLLDPText()
	}

	var neighbors []*LLDPNeighbor

	lldp, ok := data["lldp"].(map[string]interface{})
	if !ok {
		return neighbors, nil
	}

	interfaces, ok := lldp["interface"].([]interface{})
	if !ok {
		return neighbors, nil
	}

	for _, iface := range interfaces {
		ifaceMap, ok := iface.(map[string]interface{})
		if !ok {
			continue
		}

		name, ok := ifaceMap["name"].(string)
		if !ok {
			continue
		}

		neighbor, err := getLLDPJSON(name)
		if err == nil {
			neighbors = append(neighbors, neighbor)
		}
	}

	return neighbors, nil
}

// getAllLLDPText parses text output for all interfaces (fallback)
func getAllLLDPText() ([]*LLDPNeighbor, error) {
	cmd := exec.Command("lldpcli", "show", "neighbors")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var neighbors []*LLDPNeighbor
	lines := strings.Split(string(output), "\n")

	var currentInterface string
	var currentNeighbor *LLDPNeighbor

	for _, line := range lines {
		line = strings.TrimSpace(line)

		if strings.HasPrefix(line, "Interface:") {
			if currentNeighbor != nil {
				neighbors = append(neighbors, currentNeighbor)
			}

			currentInterface = strings.TrimSpace(strings.TrimPrefix(line, "Interface:"))
			currentNeighbor = &LLDPNeighbor{
				Interface: currentInterface,
			}
			continue
		}

		if currentNeighbor != nil {
			if strings.HasPrefix(line, "SysName:") {
				currentNeighbor.SwitchName = extractValue(line)
			} else if strings.HasPrefix(line, "PortID:") {
				currentNeighbor.SwitchPort = extractValue(line)
			}
		}
	}

	if currentNeighbor != nil {
		neighbors = append(neighbors, currentNeighbor)
	}

	return neighbors, nil
}

// IsLLDPAvailable checks if LLDP daemon is running
func IsLLDPAvailable() bool {
	if runtime.GOOS != "linux" {
		return false
	}

	_, err := exec.LookPath("lldpcli")
	return err == nil
}

// EnableLLDPOnInterface enables LLDP on a specific interface
func EnableLLDPOnInterface(ifaceName string) error {
	if runtime.GOOS != "linux" {
		return fmt.Errorf("LLDP only supported on Linux")
	}

	// Configure lldpd to listen on interface
	cmd := exec.Command("lldpcli", "configure", "system", "interface", "pattern", ifaceName)
	return cmd.Run()
}

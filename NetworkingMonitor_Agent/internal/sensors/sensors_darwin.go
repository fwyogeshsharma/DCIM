//go:build darwin

package sensors

import (
	"fmt"
)

// getTemperaturesDarwin retrieves temperature sensors on macOS (stub)
func getTemperaturesDarwin() ([]*TemperatureSensor, error) {
	// macOS doesn't expose sensor data easily without private frameworks
	// Would need IOKit or third-party tools like iStat Menus
	return []*TemperatureSensor{
		{
			Name:      "System",
			Component: "Unknown",
			Current:   0,
			Label:     "macOS sensor monitoring requires third-party tools (iStat Menus, Macs Fan Control)",
		},
	}, nil
}

// getVoltagesDarwin retrieves voltage sensors on macOS (stub)
func getVoltagesDarwin() ([]*VoltageSensor, error) {
	return []*VoltageSensor{
		{
			Name:    "Note",
			Current: 0,
			Label:   "macOS voltage monitoring not available without IOKit framework",
		},
	}, nil
}

// getFansDarwin retrieves fan speed sensors on macOS (stub)
func getFansDarwin() ([]*FanSensor, error) {
	return []*FanSensor{
		{
			Name:    "Note",
			Current: 0,
			Label:   "macOS fan monitoring not available without IOKit framework",
		},
	}, nil
}

// getPowerSupplyDarwin retrieves power supply information on macOS (stub)
func getPowerSupplyDarwin() (*PowerSupplyInfo, error) {
	info := &PowerSupplyInfo{
		RailVoltages: make(map[string]float64),
		Status:       "Unknown",
	}

	info.Manufacturer = "Apple"
	info.Model = "Check System Information"

	return info, nil
}

// getPowerConsumptionDarwin retrieves power consumption on macOS (stub)
func getPowerConsumptionDarwin() (*PowerConsumption, error) {
	consumption := &PowerConsumption{
		ComponentWatts: make(map[string]float64),
	}

	// macOS doesn't expose detailed power metrics without IOKit
	// Could potentially read from pmset for battery info on MacBooks
	consumption.ACConnected = true
	consumption.TotalWatts = 0

	return consumption, nil
}

// getWaterCoolingDarwin detects water cooling on macOS (stub)
func getWaterCoolingDarwin() (*WaterCoolingInfo, error) {
	// Macs typically don't have water cooling (except some Mac Pro models)
	return nil, fmt.Errorf("no water cooling detected")
}

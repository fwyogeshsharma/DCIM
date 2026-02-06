//go:build !darwin

package sensors

import (
	"fmt"
)

// getTemperaturesDarwin is a stub for non-Darwin platforms
func getTemperaturesDarwin() ([]*TemperatureSensor, error) {
	return nil, fmt.Errorf("Darwin temperature sensors not available on this platform")
}

// getVoltagesDarwin is a stub for non-Darwin platforms
func getVoltagesDarwin() ([]*VoltageSensor, error) {
	return nil, fmt.Errorf("Darwin voltage sensors not available on this platform")
}

// getFansDarwin is a stub for non-Darwin platforms
func getFansDarwin() ([]*FanSensor, error) {
	return nil, fmt.Errorf("Darwin fan sensors not available on this platform")
}

// getPowerSupplyDarwin is a stub for non-Darwin platforms
func getPowerSupplyDarwin() (*PowerSupplyInfo, error) {
	return nil, fmt.Errorf("Darwin power supply info not available on this platform")
}

// getPowerConsumptionDarwin is a stub for non-Darwin platforms
func getPowerConsumptionDarwin() (*PowerConsumption, error) {
	return nil, fmt.Errorf("Darwin power consumption not available on this platform")
}

// getWaterCoolingDarwin is a stub for non-Darwin platforms
func getWaterCoolingDarwin() (*WaterCoolingInfo, error) {
	return nil, fmt.Errorf("Darwin water cooling detection not available on this platform")
}

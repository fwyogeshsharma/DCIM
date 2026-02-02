//go:build !windows

package sensors

import (
	"fmt"
)

// getTemperaturesWindows is a stub for non-Windows platforms
func getTemperaturesWindows() ([]*TemperatureSensor, error) {
	return nil, fmt.Errorf("Windows temperature sensors not available on this platform")
}

// getVoltagesWindows is a stub for non-Windows platforms
func getVoltagesWindows() ([]*VoltageSensor, error) {
	return nil, fmt.Errorf("Windows voltage sensors not available on this platform")
}

// getFansWindows is a stub for non-Windows platforms
func getFansWindows() ([]*FanSensor, error) {
	return nil, fmt.Errorf("Windows fan sensors not available on this platform")
}

// getPowerSupplyWindows is a stub for non-Windows platforms
func getPowerSupplyWindows() (*PowerSupplyInfo, error) {
	return nil, fmt.Errorf("Windows power supply info not available on this platform")
}

// getPowerConsumptionWindows is a stub for non-Windows platforms
func getPowerConsumptionWindows() (*PowerConsumption, error) {
	return nil, fmt.Errorf("Windows power consumption not available on this platform")
}

// getWaterCoolingWindows is a stub for non-Windows platforms
func getWaterCoolingWindows() (*WaterCoolingInfo, error) {
	return nil, fmt.Errorf("Windows water cooling detection not available on this platform")
}

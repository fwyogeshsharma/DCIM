//go:build !linux

package sensors

import (
	"fmt"
)

// getTemperaturesLinux is a stub for non-Linux platforms
func getTemperaturesLinux() ([]*TemperatureSensor, error) {
	return nil, fmt.Errorf("Linux temperature sensors not available on this platform")
}

// getVoltagesLinux is a stub for non-Linux platforms
func getVoltagesLinux() ([]*VoltageSensor, error) {
	return nil, fmt.Errorf("Linux voltage sensors not available on this platform")
}

// getFansLinux is a stub for non-Linux platforms
func getFansLinux() ([]*FanSensor, error) {
	return nil, fmt.Errorf("Linux fan sensors not available on this platform")
}

// getPowerSupplyLinux is a stub for non-Linux platforms
func getPowerSupplyLinux() (*PowerSupplyInfo, error) {
	return nil, fmt.Errorf("Linux power supply info not available on this platform")
}

// getPowerConsumptionLinux is a stub for non-Linux platforms
func getPowerConsumptionLinux() (*PowerConsumption, error) {
	return nil, fmt.Errorf("Linux power consumption not available on this platform")
}

// getWaterCoolingLinux is a stub for non-Linux platforms
func getWaterCoolingLinux() (*WaterCoolingInfo, error) {
	return nil, fmt.Errorf("Linux water cooling detection not available on this platform")
}

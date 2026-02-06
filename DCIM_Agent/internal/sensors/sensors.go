package sensors

import (
	"fmt"
	"runtime"
	"time"
)

// SensorData contains comprehensive hardware sensor information
type SensorData struct {
	Temperatures   []*TemperatureSensor   `json:"temperatures"`
	Voltages       []*VoltageSensor       `json:"voltages"`
	Fans           []*FanSensor           `json:"fans"`
	PowerSupply    *PowerSupplyInfo       `json:"power_supply"`
	PowerConsumption *PowerConsumption    `json:"power_consumption"`
	WaterCooling   *WaterCoolingInfo      `json:"water_cooling,omitempty"`
	Timestamp      time.Time              `json:"timestamp"`
}

// TemperatureSensor represents a temperature sensor
type TemperatureSensor struct {
	Name        string  `json:"name"`        // "CPU Package", "GPU", "NVMe SSD", etc.
	Component   string  `json:"component"`   // "CPU", "GPU", "Disk", "Motherboard"
	Current     float64 `json:"current"`     // Current temperature in Celsius
	High        float64 `json:"high"`        // High threshold
	Critical    float64 `json:"critical"`    // Critical threshold
	Max         float64 `json:"max"`         // Maximum recorded
	Label       string  `json:"label"`       // Sensor label (e.g., "Core 0", "Junction")
}

// VoltageSensor represents a voltage sensor
type VoltageSensor struct {
	Name     string  `json:"name"`     // "CPU Core", "12V Rail", etc.
	Current  float64 `json:"current"`  // Current voltage in Volts
	Min      float64 `json:"min"`      // Minimum voltage
	Max      float64 `json:"max"`      // Maximum voltage
	Nominal  float64 `json:"nominal"`  // Nominal/expected voltage
	Label    string  `json:"label"`    // Sensor label
}

// FanSensor represents a fan speed sensor
type FanSensor struct {
	Name    string `json:"name"`    // "CPU Fan", "Case Fan 1", etc.
	Current int    `json:"current"` // Current speed in RPM
	Min     int    `json:"min"`     // Minimum speed
	Max     int    `json:"max"`     // Maximum speed
	Percent int    `json:"percent"` // Speed as percentage
	Label   string `json:"label"`   // Sensor label
}

// PowerSupplyInfo contains power supply information
type PowerSupplyInfo struct {
	Manufacturer string  `json:"manufacturer"`
	Model        string  `json:"model"`
	SerialNumber string  `json:"serial_number"`
	MaxPower     int     `json:"max_power_watts"`     // PSU max wattage
	Efficiency   string  `json:"efficiency"`          // "80+ Gold", "80+ Platinum", etc.
	RailVoltages map[string]float64 `json:"rail_voltages"` // "12V", "5V", "3.3V"
	Status       string  `json:"status"`              // "OK", "Warning", "Critical"
}

// PowerConsumption contains power consumption data
type PowerConsumption struct {
	TotalWatts    float64            `json:"total_watts"`     // Total system power
	CPUWatts      float64            `json:"cpu_watts"`       // CPU package power
	GPUWatts      float64            `json:"gpu_watts"`       // GPU power
	ComponentWatts map[string]float64 `json:"component_watts"` // Per-component power
	BatteryPercent int                `json:"battery_percent,omitempty"` // Battery level (laptops)
	BatteryStatus  string             `json:"battery_status,omitempty"`  // "Charging", "Discharging", "Full"
	ACConnected    bool               `json:"ac_connected"`    // AC power connected
}

// WaterCoolingInfo contains water cooling / AIO information
type WaterCoolingInfo struct {
	Detected      bool    `json:"detected"`
	Type          string  `json:"type"`           // "AIO", "Custom Loop"
	PumpSpeed     int     `json:"pump_speed"`     // RPM
	FlowRate      float64 `json:"flow_rate"`      // L/min
	CoolantTemp   float64 `json:"coolant_temp"`   // Celsius
	Manufacturer  string  `json:"manufacturer"`   // "Corsair", "NZXT", etc.
	Model         string  `json:"model"`
}

// GetAllSensors retrieves all sensor data for the current platform
func GetAllSensors() (*SensorData, error) {
	data := &SensorData{
		Timestamp: time.Now(),
	}

	var err error

	// Get temperature sensors
	data.Temperatures, err = GetTemperatures()
	if err != nil {
		// Non-fatal, continue
	}

	// Get voltage sensors
	data.Voltages, err = GetVoltages()
	if err != nil {
		// Non-fatal, continue
	}

	// Get fan sensors
	data.Fans, err = GetFans()
	if err != nil {
		// Non-fatal, continue
	}

	// Get power supply info
	data.PowerSupply, err = GetPowerSupply()
	if err != nil {
		// Non-fatal, continue
	}

	// Get power consumption
	data.PowerConsumption, err = GetPowerConsumption()
	if err != nil {
		// Non-fatal, continue
	}

	// Get water cooling info (if present)
	data.WaterCooling, err = GetWaterCooling()
	if err != nil {
		// Non-fatal, water cooling might not be present
		data.WaterCooling = nil
	}

	return data, nil
}

// GetTemperatures retrieves temperature sensor data
func GetTemperatures() ([]*TemperatureSensor, error) {
	switch runtime.GOOS {
	case "windows":
		return getTemperaturesWindows()
	case "linux":
		return getTemperaturesLinux()
	case "darwin":
		return getTemperaturesDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetVoltages retrieves voltage sensor data
func GetVoltages() ([]*VoltageSensor, error) {
	switch runtime.GOOS {
	case "windows":
		return getVoltagesWindows()
	case "linux":
		return getVoltagesLinux()
	case "darwin":
		return getVoltagesDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetFans retrieves fan speed sensor data
func GetFans() ([]*FanSensor, error) {
	switch runtime.GOOS {
	case "windows":
		return getFansWindows()
	case "linux":
		return getFansLinux()
	case "darwin":
		return getFansDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetPowerSupply retrieves power supply information
func GetPowerSupply() (*PowerSupplyInfo, error) {
	switch runtime.GOOS {
	case "windows":
		return getPowerSupplyWindows()
	case "linux":
		return getPowerSupplyLinux()
	case "darwin":
		return getPowerSupplyDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetPowerConsumption retrieves power consumption data
func GetPowerConsumption() (*PowerConsumption, error) {
	switch runtime.GOOS {
	case "windows":
		return getPowerConsumptionWindows()
	case "linux":
		return getPowerConsumptionLinux()
	case "darwin":
		return getPowerConsumptionDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// GetWaterCooling detects and retrieves water cooling information
func GetWaterCooling() (*WaterCoolingInfo, error) {
	switch runtime.GOOS {
	case "windows":
		return getWaterCoolingWindows()
	case "linux":
		return getWaterCoolingLinux()
	case "darwin":
		return getWaterCoolingDarwin()
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// Platform-specific functions (implemented in platform-specific files)
// - getTemperaturesWindows() []*TemperatureSensor
// - getTemperaturesLinux() []*TemperatureSensor
// - getTemperaturesDarwin() []*TemperatureSensor
// - getVoltagesWindows() []*VoltageSensor
// - getVoltagesLinux() []*VoltageSensor
// - getVoltagesDarwin() []*VoltageSensor
// - getFansWindows() []*FanSensor
// - getFansLinux() []*FanSensor
// - getFansDarwin() []*FanSensor
// - getPowerSupplyWindows() *PowerSupplyInfo
// - getPowerSupplyLinux() *PowerSupplyInfo
// - getPowerSupplyDarwin() *PowerSupplyInfo
// - getPowerConsumptionWindows() *PowerConsumption
// - getPowerConsumptionLinux() *PowerConsumption
// - getPowerConsumptionDarwin() *PowerConsumption
// - getWaterCoolingWindows() *WaterCoolingInfo
// - getWaterCoolingLinux() *WaterCoolingInfo
// - getWaterCoolingDarwin() *WaterCoolingInfo

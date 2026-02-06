//go:build windows

package sensors

import (
	"fmt"
	"strings"

	"github.com/StackExchange/wmi"
	"github.com/shirou/gopsutil/v3/host"
)

// Windows WMI structures
type Win32_TemperatureProbe struct {
	Name               string
	CurrentReading     uint32
	MaxReadingRange    uint32
	MinReadingRange    uint32
	Status             string
}

type Win32_VoltageProbe struct {
	Name               string
	CurrentReading     uint32
	MaxReadingRange    uint32
	MinReadingRange    uint32
	NominalReading     uint32
}

type Win32_Fan struct {
	Name              string
	DesiredSpeed      uint64
	VariableSpeed     bool
}

type Win32_Battery struct {
	EstimatedChargeRemaining uint16
	BatteryStatus            uint16
	EstimatedRunTime         uint32
}

// getTemperaturesWindows retrieves temperature sensors on Windows
func getTemperaturesWindows() ([]*TemperatureSensor, error) {
	var sensors []*TemperatureSensor

	// Try gopsutil first (works on most systems)
	if temps, err := host.SensorsTemperatures(); err == nil {
		for _, temp := range temps {
			sensor := &TemperatureSensor{
				Name:      temp.SensorKey,
				Component: detectComponent(temp.SensorKey),
				Current:   temp.Temperature,
				High:      temp.High,
				Critical:  temp.Critical,
				Label:     temp.SensorKey,
			}
			sensors = append(sensors, sensor)
		}
	}

	// Try WMI Win32_TemperatureProbe (less common but worth trying)
	var wmiTemps []Win32_TemperatureProbe
	err := wmi.Query("SELECT * FROM Win32_TemperatureProbe", &wmiTemps)
	if err == nil {
		for _, temp := range wmiTemps {
			// Win32_TemperatureProbe returns in tenths of degrees Kelvin
			// Convert to Celsius: (reading / 10) - 273.15
			if temp.CurrentReading > 0 {
				celsius := float64(temp.CurrentReading)/10.0 - 273.15
				sensor := &TemperatureSensor{
					Name:     temp.Name,
					Current:  celsius,
					High:     float64(temp.MaxReadingRange)/10.0 - 273.15,
					Label:    temp.Name,
					Component: detectComponent(temp.Name),
				}
				sensors = append(sensors, sensor)
			}
		}
	}

	// If no sensors found, return a note
	if len(sensors) == 0 {
		sensors = append(sensors, &TemperatureSensor{
			Name:      "System",
			Component: "Unknown",
			Current:   0,
			Label:     "No sensors detected - install OpenHardwareMonitor for detailed readings",
		})
	}

	return sensors, nil
}

// getVoltagesWindows retrieves voltage sensors on Windows
func getVoltagesWindows() ([]*VoltageSensor, error) {
	var sensors []*VoltageSensor

	// Try WMI Win32_VoltageProbe
	var wmiVolts []Win32_VoltageProbe
	err := wmi.Query("SELECT * FROM Win32_VoltageProbe", &wmiVolts)
	if err == nil {
		for _, volt := range wmiVolts {
			// Win32_VoltageProbe returns in millivolts
			if volt.CurrentReading > 0 {
				sensor := &VoltageSensor{
					Name:    volt.Name,
					Current: float64(volt.CurrentReading) / 1000.0,
					Min:     float64(volt.MinReadingRange) / 1000.0,
					Max:     float64(volt.MaxReadingRange) / 1000.0,
					Nominal: float64(volt.NominalReading) / 1000.0,
					Label:   volt.Name,
				}
				sensors = append(sensors, sensor)
			}
		}
	}

	// Add standard voltage rails (these are typical for PC power supplies)
	if len(sensors) == 0 {
		sensors = append(sensors, &VoltageSensor{
			Name:    "Note",
			Current: 0,
			Label:   "Install OpenHardwareMonitor or HWiNFO for detailed voltage readings",
		})
	}

	return sensors, nil
}

// getFansWindows retrieves fan speed sensors on Windows
func getFansWindows() ([]*FanSensor, error) {
	var sensors []*FanSensor

	// Try WMI Win32_Fan
	var wmiFans []Win32_Fan
	err := wmi.Query("SELECT * FROM Win32_Fan", &wmiFans)
	if err == nil {
		for _, fan := range wmiFans {
			sensor := &FanSensor{
				Name:    fan.Name,
				Current: int(fan.DesiredSpeed),
				Label:   fan.Name,
			}
			sensors = append(sensors, sensor)
		}
	}

	if len(sensors) == 0 {
		sensors = append(sensors, &FanSensor{
			Name:    "Note",
			Current: 0,
			Label:   "Install OpenHardwareMonitor or HWiNFO for fan speed readings",
		})
	}

	return sensors, nil
}

// getPowerSupplyWindows retrieves power supply information on Windows
func getPowerSupplyWindows() (*PowerSupplyInfo, error) {
	info := &PowerSupplyInfo{
		RailVoltages: make(map[string]float64),
		Status:       "Unknown",
	}

	// WMI doesn't provide detailed PSU info
	// This would require manufacturer-specific tools or OpenHardwareMonitor
	info.Status = "OK"

	// Check if we can get any power supply info from WMI
	type Win32_PowerSupply struct {
		Name        string
		Description string
	}

	var psu []Win32_PowerSupply
	err := wmi.Query("SELECT * FROM Win32_PowerSupply", &psu)
	if err == nil && len(psu) > 0 {
		info.Model = psu[0].Name
		info.Manufacturer = psu[0].Description
	}

	return info, nil
}

// getPowerConsumptionWindows retrieves power consumption on Windows
func getPowerConsumptionWindows() (*PowerConsumption, error) {
	consumption := &PowerConsumption{
		ComponentWatts: make(map[string]float64),
	}

	// Check for battery (laptops)
	var batteries []Win32_Battery
	err := wmi.Query("SELECT * FROM Win32_Battery", &batteries)
	if err == nil && len(batteries) > 0 {
		battery := batteries[0]
		consumption.BatteryPercent = int(battery.EstimatedChargeRemaining)

		// BatteryStatus: 1=Discharging, 2=AC, 3=Fully Charged, etc.
		switch battery.BatteryStatus {
		case 1:
			consumption.BatteryStatus = "Discharging"
			consumption.ACConnected = false
		case 2:
			consumption.BatteryStatus = "Charging"
			consumption.ACConnected = true
		case 3:
			consumption.BatteryStatus = "Full"
			consumption.ACConnected = true
		default:
			consumption.BatteryStatus = "Unknown"
		}
	} else {
		// Desktop - AC always connected
		consumption.ACConnected = true
	}

	// Note: Accurate power consumption requires specialized tools
	// Windows doesn't expose this directly via WMI
	consumption.TotalWatts = 0 // Would need RAPL on Intel or similar

	return consumption, nil
}

// getWaterCoolingWindows detects water cooling on Windows
func getWaterCoolingWindows() (*WaterCoolingInfo, error) {
	info := &WaterCoolingInfo{
		Detected: false,
	}

	// Check for known AIO cooler manufacturers in USB devices or WMI
	type Win32_USBController struct {
		Name        string
		Description string
		Manufacturer string
	}

	var usb []Win32_USBController
	err := wmi.Query("SELECT * FROM Win32_USBControllerDevice", &usb)
	if err == nil {
		for _, device := range usb {
			name := strings.ToLower(device.Name + " " + device.Description + " " + device.Manufacturer)

			// Check for known AIO cooler brands
			if strings.Contains(name, "corsair") && (strings.Contains(name, "h100") || strings.Contains(name, "h150")) {
				info.Detected = true
				info.Type = "AIO"
				info.Manufacturer = "Corsair"
				break
			}
			if strings.Contains(name, "nzxt") && strings.Contains(name, "kraken") {
				info.Detected = true
				info.Type = "AIO"
				info.Manufacturer = "NZXT"
				break
			}
			if strings.Contains(name, "cooler master") && strings.Contains(name, "masterliquid") {
				info.Detected = true
				info.Type = "AIO"
				info.Manufacturer = "Cooler Master"
				break
			}
		}
	}

	if !info.Detected {
		return nil, fmt.Errorf("no water cooling detected")
	}

	return info, nil
}

// detectComponent tries to determine which component a sensor belongs to
func detectComponent(name string) string {
	nameLower := strings.ToLower(name)

	if strings.Contains(nameLower, "cpu") || strings.Contains(nameLower, "processor") || strings.Contains(nameLower, "core") {
		return "CPU"
	}
	if strings.Contains(nameLower, "gpu") || strings.Contains(nameLower, "graphics") || strings.Contains(nameLower, "video") {
		return "GPU"
	}
	if strings.Contains(nameLower, "nvme") || strings.Contains(nameLower, "ssd") || strings.Contains(nameLower, "disk") {
		return "Disk"
	}
	if strings.Contains(nameLower, "motherboard") || strings.Contains(nameLower, "system") || strings.Contains(nameLower, "chipset") {
		return "Motherboard"
	}

	return "Unknown"
}

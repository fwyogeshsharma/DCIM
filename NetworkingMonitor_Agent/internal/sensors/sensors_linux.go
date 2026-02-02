//go:build linux

package sensors

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/shirou/gopsutil/v3/host"
)

// getTemperaturesLinux retrieves temperature sensors on Linux via hwmon
func getTemperaturesLinux() ([]*TemperatureSensor, error) {
	var sensors []*TemperatureSensor

	// Use gopsutil which reads from /sys/class/hwmon
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

	// Additionally scan /sys/class/hwmon for more sensors
	hwmonSensors, err := readHwmonTemperatures()
	if err == nil {
		sensors = append(sensors, hwmonSensors...)
	}

	return sensors, nil
}

// getVoltagesLinux retrieves voltage sensors on Linux via hwmon
func getVoltagesLinux() ([]*VoltageSensor, error) {
	var sensors []*VoltageSensor

	// Scan /sys/class/hwmon for voltage sensors
	hwmonPath := "/sys/class/hwmon"
	entries, err := os.ReadDir(hwmonPath)
	if err != nil {
		return sensors, err
	}

	for _, entry := range entries {
		devicePath := filepath.Join(hwmonPath, entry.Name())

		// Find voltage input files (in*_input)
		files, _ := filepath.Glob(filepath.Join(devicePath, "in*_input"))
		for _, file := range files {
			if value, err := readIntFile(file); err == nil {
				// Extract sensor number
				base := filepath.Base(file)
				sensorNum := strings.TrimPrefix(base, "in")
				sensorNum = strings.TrimSuffix(sensorNum, "_input")

				// Read label if available
				labelFile := filepath.Join(devicePath, fmt.Sprintf("in%s_label", sensorNum))
				label, _ := readStringFile(labelFile)
				if label == "" {
					label = fmt.Sprintf("in%s", sensorNum)
				}

				// Read min/max if available
				minFile := filepath.Join(devicePath, fmt.Sprintf("in%s_min", sensorNum))
				maxFile := filepath.Join(devicePath, fmt.Sprintf("in%s_max", sensorNum))
				minVal, _ := readIntFile(minFile)
				maxVal, _ := readIntFile(maxFile)

				sensor := &VoltageSensor{
					Name:    label,
					Current: float64(value) / 1000.0, // Convert mV to V
					Min:     float64(minVal) / 1000.0,
					Max:     float64(maxVal) / 1000.0,
					Label:   label,
				}
				sensors = append(sensors, sensor)
			}
		}
	}

	return sensors, nil
}

// getFansLinux retrieves fan speed sensors on Linux via hwmon
func getFansLinux() ([]*FanSensor, error) {
	var sensors []*FanSensor

	hwmonPath := "/sys/class/hwmon"
	entries, err := os.ReadDir(hwmonPath)
	if err != nil {
		return sensors, err
	}

	for _, entry := range entries {
		devicePath := filepath.Join(hwmonPath, entry.Name())

		// Find fan input files (fan*_input)
		files, _ := filepath.Glob(filepath.Join(devicePath, "fan*_input"))
		for _, file := range files {
			if value, err := readIntFile(file); err == nil {
				// Extract sensor number
				base := filepath.Base(file)
				sensorNum := strings.TrimPrefix(base, "fan")
				sensorNum = strings.TrimSuffix(sensorNum, "_input")

				// Read label if available
				labelFile := filepath.Join(devicePath, fmt.Sprintf("fan%s_label", sensorNum))
				label, _ := readStringFile(labelFile)
				if label == "" {
					label = fmt.Sprintf("Fan %s", sensorNum)
				}

				// Read min/max if available
				minFile := filepath.Join(devicePath, fmt.Sprintf("fan%s_min", sensorNum))
				maxFile := filepath.Join(devicePath, fmt.Sprintf("fan%s_max", sensorNum))
				minVal, _ := readIntFile(minFile)
				maxVal, _ := readIntFile(maxFile)

				sensor := &FanSensor{
					Name:    label,
					Current: value,
					Min:     minVal,
					Max:     maxVal,
					Label:   label,
				}
				sensors = append(sensors, sensor)
			}
		}
	}

	return sensors, nil
}

// getPowerSupplyLinux retrieves power supply information on Linux
func getPowerSupplyLinux() (*PowerSupplyInfo, error) {
	info := &PowerSupplyInfo{
		RailVoltages: make(map[string]float64),
		Status:       "OK",
	}

	// Linux doesn't expose PSU details in sysfs
	// Would need to parse dmidecode or vendor-specific tools
	info.Manufacturer = "Unknown"
	info.Model = "Check system documentation"

	return info, nil
}

// getPowerConsumptionLinux retrieves power consumption on Linux via RAPL
func getPowerConsumptionLinux() (*PowerConsumption, error) {
	consumption := &PowerConsumption{
		ComponentWatts: make(map[string]float64),
	}

	// Check for battery (laptops)
	batteryPath := "/sys/class/power_supply"
	if entries, err := os.ReadDir(batteryPath); err == nil {
		for _, entry := range entries {
			if strings.HasPrefix(entry.Name(), "BAT") {
				devicePath := filepath.Join(batteryPath, entry.Name())

				// Read battery capacity
				if capacity, err := readIntFile(filepath.Join(devicePath, "capacity")); err == nil {
					consumption.BatteryPercent = capacity
				}

				// Read battery status
				if status, err := readStringFile(filepath.Join(devicePath, "status")); err == nil {
					consumption.BatteryStatus = status
					consumption.ACConnected = (status == "Charging" || status == "Full")
				}
			}
		}
	}

	// Try to read RAPL (Intel Running Average Power Limit) for power consumption
	raplPath := "/sys/class/powercap/intel-rapl"
	if _, err := os.Stat(raplPath); err == nil {
		// Read package power
		if energy, err := readIntFile(filepath.Join(raplPath, "intel-rapl:0/energy_uj")); err == nil {
			// This is cumulative energy in microjoules
			// Would need to track delta over time to get watts
			consumption.CPUWatts = float64(energy) / 1000000.0
		}
	}

	return consumption, nil
}

// getWaterCoolingLinux detects water cooling on Linux
func getWaterCoolingLinux() (*WaterCoolingInfo, error) {
	info := &WaterCoolingInfo{
		Detected: false,
	}

	// Check for liquidctl devices (common AIO tool on Linux)
	// Also check hwmon for pump speeds

	hwmonPath := "/sys/class/hwmon"
	entries, err := os.ReadDir(hwmonPath)
	if err != nil {
		return nil, fmt.Errorf("no water cooling detected")
	}

	for _, entry := range entries {
		devicePath := filepath.Join(hwmonPath, entry.Name())
		name, _ := readStringFile(filepath.Join(devicePath, "name"))
		nameLower := strings.ToLower(name)

		// Check for known AIO cooler identifiers
		if strings.Contains(nameLower, "corsair") || strings.Contains(nameLower, "h100") ||
		   strings.Contains(nameLower, "nzxt") || strings.Contains(nameLower, "kraken") ||
		   strings.Contains(nameLower, "pump") {
			info.Detected = true
			info.Type = "AIO"
			info.Manufacturer = name

			// Try to read pump speed
			if pumpFile, err := filepath.Glob(filepath.Join(devicePath, "fan*_input")); err == nil && len(pumpFile) > 0 {
				if speed, err := readIntFile(pumpFile[0]); err == nil {
					info.PumpSpeed = speed
				}
			}
			break
		}
	}

	if !info.Detected {
		return nil, fmt.Errorf("no water cooling detected")
	}

	return info, nil
}

// Helper functions

func readHwmonTemperatures() ([]*TemperatureSensor, error) {
	var sensors []*TemperatureSensor

	hwmonPath := "/sys/class/hwmon"
	entries, err := os.ReadDir(hwmonPath)
	if err != nil {
		return sensors, err
	}

	for _, entry := range entries {
		devicePath := filepath.Join(hwmonPath, entry.Name())

		// Find temperature input files (temp*_input)
		files, _ := filepath.Glob(filepath.Join(devicePath, "temp*_input"))
		for _, file := range files {
			if value, err := readIntFile(file); err == nil {
				// Extract sensor number
				base := filepath.Base(file)
				sensorNum := strings.TrimPrefix(base, "temp")
				sensorNum = strings.TrimSuffix(sensorNum, "_input")

				// Read label if available
				labelFile := filepath.Join(devicePath, fmt.Sprintf("temp%s_label", sensorNum))
				label, _ := readStringFile(labelFile)
				if label == "" {
					label = fmt.Sprintf("temp%s", sensorNum)
				}

				// Read thresholds
				maxFile := filepath.Join(devicePath, fmt.Sprintf("temp%s_max", sensorNum))
				critFile := filepath.Join(devicePath, fmt.Sprintf("temp%s_crit", sensorNum))
				maxVal, _ := readIntFile(maxFile)
				critVal, _ := readIntFile(critFile)

				sensor := &TemperatureSensor{
					Name:      label,
					Component: detectComponent(label),
					Current:   float64(value) / 1000.0, // Convert millidegrees to degrees
					High:      float64(maxVal) / 1000.0,
					Critical:  float64(critVal) / 1000.0,
					Label:     label,
				}
				sensors = append(sensors, sensor)
			}
		}
	}

	return sensors, nil
}

func readIntFile(path string) (int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	value, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 0, err
	}
	return value, nil
}

func readStringFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

func detectComponent(name string) string {
	nameLower := strings.ToLower(name)

	if strings.Contains(nameLower, "cpu") || strings.Contains(nameLower, "processor") || strings.Contains(nameLower, "core") || strings.Contains(nameLower, "package") {
		return "CPU"
	}
	if strings.Contains(nameLower, "gpu") || strings.Contains(nameLower, "graphics") || strings.Contains(nameLower, "video") || strings.Contains(nameLower, "radeon") || strings.Contains(nameLower, "nvidia") {
		return "GPU"
	}
	if strings.Contains(nameLower, "nvme") || strings.Contains(nameLower, "ssd") || strings.Contains(nameLower, "disk") || strings.Contains(nameLower, "composite") {
		return "Disk"
	}
	if strings.Contains(nameLower, "motherboard") || strings.Contains(nameLower, "system") || strings.Contains(nameLower, "chipset") {
		return "Motherboard"
	}

	return "Unknown"
}

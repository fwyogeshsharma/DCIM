package server

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/faberlabs/dcim-server/internal/models"
	"gopkg.in/yaml.v3"
)

var (
	cachedCoolingConfig *models.CoolingConfig
	coolingConfigMutex  sync.RWMutex
)

// loadCoolingConfig loads the cooling configuration from cooling_config.yaml
func (s *Server) loadCoolingConfig() (*models.CoolingConfig, error) {
	coolingConfigMutex.RLock()
	if cachedCoolingConfig != nil {
		defer coolingConfigMutex.RUnlock()
		return cachedCoolingConfig, nil
	}
	coolingConfigMutex.RUnlock()

	coolingConfigMutex.Lock()
	defer coolingConfigMutex.Unlock()

	// Double-check after acquiring write lock
	if cachedCoolingConfig != nil {
		return cachedCoolingConfig, nil
	}

	// Get the current executable directory or working directory
	configPath := "cooling_config.yaml"

	// Try to find the config file
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		// Try in parent directory (if running from subdirectory)
		configPath = filepath.Join("..", "cooling_config.yaml")
		if _, err := os.Stat(configPath); os.IsNotExist(err) {
			// Try absolute path from DCIM_Server
			configPath = filepath.Join("DCIM_Server", "cooling_config.yaml")
			if _, err := os.Stat(configPath); os.IsNotExist(err) {
				return nil, fmt.Errorf("cooling_config.yaml not found")
			}
		}
	}

	// Read the YAML file
	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read cooling config: %w", err)
	}

	// Parse YAML
	var cfg models.CoolingConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to parse cooling config: %w", err)
	}

	// Validate configuration
	if err := validateCoolingConfig(&cfg); err != nil {
		return nil, fmt.Errorf("invalid cooling config: %w", err)
	}

	// Cache the configuration
	cachedCoolingConfig = &cfg

	s.logger.Printf("Loaded cooling configuration from %s", configPath)

	return &cfg, nil
}

// validateCoolingConfig validates the cooling configuration values
func validateCoolingConfig(cfg *models.CoolingConfig) error {
	// Validate temperature thresholds
	if cfg.Cooling.Temperature.InletMaxCondenserOn <= 0 {
		return fmt.Errorf("inlet_max_condenser_on must be positive")
	}

	if cfg.Cooling.Temperature.OutletMaxNormal <= cfg.Cooling.Temperature.InletMaxCondenserOn {
		return fmt.Errorf("outlet_max_normal must be greater than inlet_max_condenser_on")
	}

	if cfg.Cooling.Temperature.OutletMaxCritical <= cfg.Cooling.Temperature.OutletMaxNormal {
		return fmt.Errorf("outlet_max_critical must be greater than outlet_max_normal")
	}

	// Validate pressure thresholds
	if cfg.Cooling.Pressure.NormalMin <= 0 {
		return fmt.Errorf("normal_min pressure must be positive")
	}

	if cfg.Cooling.Pressure.NormalMax <= cfg.Cooling.Pressure.NormalMin {
		return fmt.Errorf("normal_max pressure must be greater than normal_min")
	}

	if cfg.Cooling.Pressure.NormalOperating < cfg.Cooling.Pressure.NormalMin ||
		cfg.Cooling.Pressure.NormalOperating > cfg.Cooling.Pressure.NormalMax {
		return fmt.Errorf("normal_operating pressure must be within normal_min and normal_max range")
	}

	// Validate validation settings
	if cfg.Cooling.Validation.MaxTemperature <= cfg.Cooling.Validation.MinTemperature {
		return fmt.Errorf("max_temperature must be greater than min_temperature")
	}

	if cfg.Cooling.Validation.MaxPressure <= cfg.Cooling.Validation.MinPressure {
		return fmt.Errorf("max_pressure must be greater than min_pressure")
	}

	return nil
}

// ReloadCoolingConfig reloads the cooling configuration from disk
// Useful for updating thresholds without restarting the server
func (s *Server) ReloadCoolingConfig() error {
	coolingConfigMutex.Lock()
	defer coolingConfigMutex.Unlock()

	cachedCoolingConfig = nil

	_, err := s.loadCoolingConfig()
	if err != nil {
		return err
	}

	s.logger.Println("Cooling configuration reloaded successfully")
	return nil
}

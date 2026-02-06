package config

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Server      ServerConfig      `yaml:"server"`
	Agent       AgentConfig       `yaml:"agent"`
	Datacenter  DatacenterConfig  `yaml:"datacenter"`
	SNMPManager SNMPManagerConfig `yaml:"snmp_manager"`
	Database    DatabaseConfig    `yaml:"database"`
	Alerts      AlertsConfig      `yaml:"alerts"`
	Logging     LoggingConfig     `yaml:"logging"`
}

type ServerConfig struct {
	URL           string        `yaml:"url"`
	Timeout       time.Duration `yaml:"timeout"`
	RetryAttempts int           `yaml:"retry_attempts"`
	RetryDelay    time.Duration `yaml:"retry_delay"`
	TLS           TLSConfig     `yaml:"tls"`
}

type TLSConfig struct {
	Enabled            bool   `yaml:"enabled"`
	ClientCertPath     string `yaml:"client_cert_path"`
	ClientKeyPath      string `yaml:"client_key_path"`
	CACertPath         string `yaml:"ca_cert_path"`
	InsecureSkipVerify bool   `yaml:"insecure_skip_verify"` // Only for testing/development
	MinTLSVersion      string `yaml:"min_tls_version"`      // "1.2" or "1.3"
}

type AgentConfig struct {
	ID              string        `yaml:"id"`
	Name            string        `yaml:"name"`
	CollectInterval time.Duration `yaml:"collect_interval"`
	SendInterval    time.Duration `yaml:"send_interval"`
	BatchSize       int           `yaml:"batch_size"`
}

type DatabaseConfig struct {
	Path          string `yaml:"path"`
	RetentionDays int    `yaml:"retention_days"`
}

type AlertsConfig struct {
	CPU         ThresholdConfig `yaml:"cpu"`
	Memory      ThresholdConfig `yaml:"memory"`
	Disk        ThresholdConfig `yaml:"disk"`
	Temperature ThresholdConfig `yaml:"temperature"`
}

type ThresholdConfig struct {
	Warning  float64 `yaml:"warning"`
	Critical float64 `yaml:"critical"`
}

type LoggingConfig struct {
	Level      string `yaml:"level"`
	File       string `yaml:"file"`
	MaxSizeMB  int    `yaml:"max_size_mb"`
	MaxBackups int    `yaml:"max_backups"`
}

// SNMPManagerConfig configures SNMP device polling
type SNMPManagerConfig struct {
	Enabled      bool                `yaml:"enabled"`
	PollInterval time.Duration       `yaml:"poll_interval"`
	Devices      []SNMPDeviceConfig  `yaml:"devices"`
}

// SNMPDeviceConfig defines an SNMP device to monitor
type SNMPDeviceConfig struct {
	Name      string            `yaml:"name"`
	Host      string            `yaml:"host"`
	Port      uint16            `yaml:"port"`
	Community string            `yaml:"community"`
	Version   string            `yaml:"version"` // "1", "2c", "3"
	Timeout   time.Duration     `yaml:"timeout"`
	Retries   int               `yaml:"retries"`
	OIDs      []SNMPOIDConfig   `yaml:"oids"`
}

// SNMPOIDConfig defines an OID to query
type SNMPOIDConfig struct {
	OID  string `yaml:"oid"`
	Name string `yaml:"name"`
	Type string `yaml:"type"` // counter, gauge, string
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config file: %w", err)
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	// Set defaults
	if cfg.Agent.ID == "" {
		cfg.Agent.ID = generateAgentID()
	}
	if cfg.Agent.Name == "" {
		hostname, _ := os.Hostname()
		cfg.Agent.Name = hostname
	}

	return &cfg, nil
}

func generateAgentID() string {
	hostname, _ := os.Hostname()
	return fmt.Sprintf("%s-%d", hostname, time.Now().Unix())
}

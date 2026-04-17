package config

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"gopkg.in/yaml.v3"
)

// Config represents the entire server configuration
type Config struct {
	Server      ServerConfig      `yaml:"server"`
	ServerID    ServerIDConfig    `yaml:"server_id"`
	TLS         TLSConfig         `yaml:"tls"`
	Database    DatabaseConfig    `yaml:"database"`
	License     LicenseConfig     `yaml:"license"`
	Agents      AgentConfig       `yaml:"agents"`
	API         APIConfig         `yaml:"api"`
	Logging     LoggingConfig     `yaml:"logging"`
	Alerting    AlertingConfig    `yaml:"alerting"`
	Metrics     MetricsConfig     `yaml:"metrics"`
	Dashboard   DashboardConfig   `yaml:"dashboard"`
	Health      HealthConfig      `yaml:"health"`
	Performance PerformanceConfig `yaml:"performance"`
	Debug       DebugConfig       `yaml:"debug"`
	SNMPWalker  SNMPWalkerConfig  `yaml:"snmp_walker"`
	SNMPTrap    SNMPTrapConfig    `yaml:"snmp_trap"`
}

// ServerIDConfig contains server identification settings
type ServerIDConfig struct {
	ID           string `yaml:"id"`            // Unique server identifier (auto-generated if empty)
	Name         string `yaml:"name"`          // Human-readable server name
	Location     string `yaml:"location"`      // Physical location of server
	Environment  string `yaml:"environment"`   // dev, staging, production, etc.
	AutoGenerate bool   `yaml:"auto_generate"` // Auto-generate ID if not specified
}

// ServerConfig contains server settings
type ServerConfig struct {
	Address      string        `yaml:"address"`
	Port         int           `yaml:"port"`
	ReadTimeout  time.Duration `yaml:"read_timeout"`
	WriteTimeout time.Duration `yaml:"write_timeout"`
	IdleTimeout  time.Duration `yaml:"idle_timeout"`
	MaxBodySize  int64         `yaml:"max_body_size"`
}

// TLSConfig contains TLS/mTLS settings
type TLSConfig struct {
	Enabled        bool     `yaml:"enabled"`
	ServerCertPath string   `yaml:"server_cert_path"`
	ServerKeyPath  string   `yaml:"server_key_path"`
	CACertPath     string   `yaml:"ca_cert_path"`
	ClientAuth     string   `yaml:"client_auth"`
	MinTLSVersion  string   `yaml:"min_tls_version"`
	CipherSuites   []string `yaml:"cipher_suites"`
}

// DatabaseConfig contains database settings
type DatabaseConfig struct {
	Type      string          `yaml:"type"`
	SQLite    SQLiteConfig    `yaml:"sqlite"`
	Postgres  PostgresConfig  `yaml:"postgres"`
	MySQL     MySQLConfig     `yaml:"mysql"`
	Retention RetentionConfig `yaml:"retention"`
}

// SQLiteConfig contains SQLite-specific settings
type SQLiteConfig struct {
	Path            string        `yaml:"path"`
	MaxOpenConns    int           `yaml:"max_open_conns"`
	MaxIdleConns    int           `yaml:"max_idle_conns"`
	ConnMaxLifetime time.Duration `yaml:"conn_max_lifetime"`
}

// PostgresConfig contains PostgreSQL-specific settings
type PostgresConfig struct {
	Host            string        `yaml:"host"`
	Port            int           `yaml:"port"`
	User            string        `yaml:"user"`
	Password        string        `yaml:"password"`
	Database        string        `yaml:"database"`
	SSLMode         string        `yaml:"sslmode"`
	MaxOpenConns    int           `yaml:"max_open_conns"`
	MaxIdleConns    int           `yaml:"max_idle_conns"`
	ConnMaxLifetime time.Duration `yaml:"conn_max_lifetime"`
}

// MySQLConfig contains MySQL-specific settings
type MySQLConfig struct {
	Host            string        `yaml:"host"`
	Port            int           `yaml:"port"`
	User            string        `yaml:"user"`
	Password        string        `yaml:"password"`
	Database        string        `yaml:"database"`
	MaxOpenConns    int           `yaml:"max_open_conns"`
	MaxIdleConns    int           `yaml:"max_idle_conns"`
	ConnMaxLifetime time.Duration `yaml:"conn_max_lifetime"`
}

// RetentionConfig contains data retention settings
type RetentionConfig struct {
	MetricsDays     int           `yaml:"metrics_days"`
	AlertsDays      int           `yaml:"alerts_days"`
	AgentStatusDays int           `yaml:"agent_status_days"`
	CleanupInterval time.Duration `yaml:"cleanup_interval"`
}

// LicenseConfig contains license management settings
type LicenseConfig struct {
	Mode            string               `yaml:"mode"`
	FilePath        string               `yaml:"file_path"`
	Enforce         bool                 `yaml:"enforce"`
	GracePeriodDays int                  `yaml:"grace_period_days"`
	CheckInterval   time.Duration        `yaml:"check_interval"`
	Default         DefaultLicenseConfig `yaml:"default"`
}

// DefaultLicenseConfig contains default license limits
type DefaultLicenseConfig struct {
	MaxAgents      int      `yaml:"max_agents"`
	MaxSNMPDevices int      `yaml:"max_snmp_devices"`
	Features       []string `yaml:"features"`
}

// AgentConfig contains agent management settings
type AgentConfig struct {
	Connection   ConnectionConfig   `yaml:"connection"`
	Registration RegistrationConfig `yaml:"registration"`
	Validation   ValidationConfig   `yaml:"validation"`
}

// ConnectionConfig contains agent connection settings
type ConnectionConfig struct {
	HeartbeatTimeout     time.Duration `yaml:"heartbeat_timeout"`
	IdentificationMethod string        `yaml:"identification_method"`
}

// RegistrationConfig contains agent registration settings
type RegistrationConfig struct {
	AutoRegister    bool   `yaml:"auto_register"`
	RequireApproval bool   `yaml:"require_approval"`
	DefaultGroup    string `yaml:"default_group"`
}

// ValidationConfig contains data validation settings
type ValidationConfig struct {
	RejectOldMetrics    time.Duration `yaml:"reject_old_metrics"`
	RejectFutureMetrics time.Duration `yaml:"reject_future_metrics"`
	MaxMetricsPerBatch  int           `yaml:"max_metrics_per_batch"`
	MaxAlertsPerBatch   int           `yaml:"max_alerts_per_batch"`
}

// APIConfig contains API settings
type APIConfig struct {
	Version      string             `yaml:"version"`
	BasePath     string             `yaml:"base_path"`
	RateLimiting RateLimitingConfig `yaml:"rate_limiting"`
	Compression  CompressionConfig  `yaml:"compression"`
	CORS         CORSConfig         `yaml:"cors"`
}

// RateLimitingConfig contains rate limiting settings
type RateLimitingConfig struct {
	Enabled           bool `yaml:"enabled"`
	RequestsPerMinute int  `yaml:"requests_per_minute"`
	Burst             int  `yaml:"burst"`
}

// CompressionConfig contains compression settings
type CompressionConfig struct {
	Enabled bool `yaml:"enabled"`
	MinSize int  `yaml:"min_size"`
	Level   int  `yaml:"level"`
}

// CORSConfig contains CORS settings
type CORSConfig struct {
	Enabled        bool     `yaml:"enabled"`
	AllowedOrigins []string `yaml:"allowed_origins"`
	AllowedMethods []string `yaml:"allowed_methods"`
	AllowedHeaders []string `yaml:"allowed_headers"`
}

// LoggingConfig contains logging settings
type LoggingConfig struct {
	Level         string        `yaml:"level"`
	Output        string        `yaml:"output"`
	File          LogFileConfig `yaml:"file"`
	Format        string        `yaml:"format"`
	IncludeCaller bool          `yaml:"include_caller"`
	LogRequests   bool          `yaml:"log_requests"`
}

// LogFileConfig contains log file settings
type LogFileConfig struct {
	Path       string `yaml:"path"`
	MaxSizeMB  int    `yaml:"max_size_mb"`
	MaxBackups int    `yaml:"max_backups"`
	MaxAgeDays int    `yaml:"max_age_days"`
	Compress   bool   `yaml:"compress"`
}

// AlertingConfig contains alerting settings
type AlertingConfig struct {
	Enabled           bool                 `yaml:"enabled"`
	AggregationWindow time.Duration        `yaml:"aggregation_window"`
	Notifications     NotificationsConfig  `yaml:"notifications"`
	SeverityLevels    SeverityLevelsConfig `yaml:"severity_levels"`
}

// NotificationsConfig contains notification channel settings
type NotificationsConfig struct {
	Email   EmailConfig   `yaml:"email"`
	Webhook WebhookConfig `yaml:"webhook"`
	Slack   SlackConfig   `yaml:"slack"`
}

// EmailConfig contains email notification settings
type EmailConfig struct {
	Enabled      bool     `yaml:"enabled"`
	SMTPHost     string   `yaml:"smtp_host"`
	SMTPPort     int      `yaml:"smtp_port"`
	SMTPUser     string   `yaml:"smtp_user"`
	SMTPPassword string   `yaml:"smtp_password"`
	FromAddress  string   `yaml:"from_address"`
	ToAddresses  []string `yaml:"to_addresses"`
	UseTLS       bool     `yaml:"use_tls"`
}

// WebhookConfig contains webhook notification settings
type WebhookConfig struct {
	Enabled       bool              `yaml:"enabled"`
	URL           string            `yaml:"url"`
	Timeout       time.Duration     `yaml:"timeout"`
	RetryAttempts int               `yaml:"retry_attempts"`
	Headers       map[string]string `yaml:"headers"`
}

// SlackConfig contains Slack notification settings
type SlackConfig struct {
	Enabled    bool   `yaml:"enabled"`
	WebhookURL string `yaml:"webhook_url"`
	Channel    string `yaml:"channel"`
	Username   string `yaml:"username"`
}

// SeverityLevelsConfig contains severity-specific settings
type SeverityLevelsConfig struct {
	Critical SeverityConfig `yaml:"critical"`
	Warning  SeverityConfig `yaml:"warning"`
}

// SeverityConfig contains settings for a severity level
type SeverityConfig struct {
	EscalationThreshold int           `yaml:"escalation_threshold"`
	Cooldown            time.Duration `yaml:"cooldown"`
}

// MetricsConfig contains metrics aggregation settings
type MetricsConfig struct {
	Aggregation AggregationConfig `yaml:"aggregation"`
}

// AggregationConfig contains aggregation settings
type AggregationConfig struct {
	Enabled   bool                  `yaml:"enabled"`
	Intervals []AggregationInterval `yaml:"intervals"`
}

// AggregationInterval defines an aggregation interval
type AggregationInterval struct {
	Interval  time.Duration `yaml:"interval"`
	Retention time.Duration `yaml:"retention"`
}

// DashboardConfig contains dashboard settings
type DashboardConfig struct {
	Enabled bool       `yaml:"enabled"`
	Port    int        `yaml:"port"`
	Auth    AuthConfig `yaml:"auth"`
}

// AuthConfig contains authentication settings
type AuthConfig struct {
	Enabled        bool          `yaml:"enabled"`
	SessionTimeout time.Duration `yaml:"session_timeout"`
	AdminUser      string        `yaml:"admin_user"`
	AdminPassword  string        `yaml:"admin_password"`
}

// HealthConfig contains health check settings
type HealthConfig struct {
	Enabled       bool          `yaml:"enabled"`
	Path          string        `yaml:"path"`
	Detailed      bool          `yaml:"detailed"`
	CheckInterval time.Duration `yaml:"check_interval"`
}

// PerformanceConfig contains performance tuning settings
type PerformanceConfig struct {
	Workers WorkersConfig `yaml:"workers"`
	Buffers BuffersConfig `yaml:"buffers"`
	Batch   BatchConfig   `yaml:"batch"`
}

// WorkersConfig contains worker pool sizes
type WorkersConfig struct {
	MetricProcessors int `yaml:"metric_processors"`
	AlertProcessors  int `yaml:"alert_processors"`
	DatabaseWriters  int `yaml:"database_writers"`
}

// BuffersConfig contains buffer sizes
type BuffersConfig struct {
	Metrics int `yaml:"metrics"`
	Alerts  int `yaml:"alerts"`
}

// BatchConfig contains batch processing settings
type BatchConfig struct {
	MetricsBatchSize int           `yaml:"metrics_batch_size"`
	BatchTimeout     time.Duration `yaml:"batch_timeout"`
}

// DebugConfig contains debug settings
type DebugConfig struct {
	Enabled       bool   `yaml:"enabled"`
	Profiling     bool   `yaml:"profiling"`
	ProfilingPort int    `yaml:"profiling_port"`
	DumpData      bool   `yaml:"dump_data"`
	DumpPath      string `yaml:"dump_path"`
}

// SNMPTrapConfig holds settings for the built-in SNMP trap receiver
type SNMPTrapConfig struct {
	Enabled bool   `yaml:"enabled"`
	Port    uint16 `yaml:"port"` // UDP port to listen on (default 162; use 1162 to avoid needing root)
}

// SNMPWalkerConfig holds settings for the built-in SNMP topology walker
type SNMPWalkerConfig struct {
	Enabled          bool          `yaml:"enabled"`
	SeedIP           string        `yaml:"seed_ip"`
	Community        string        `yaml:"community"`
	Version          string        `yaml:"version"`
	Port             uint16        `yaml:"port"`
	MaxDepth         int           `yaml:"max_depth"`
	Timeout          time.Duration `yaml:"timeout"`
	Retries          int           `yaml:"retries"`
	Interval         time.Duration `yaml:"interval"`
	UseIPAsCommunity bool          `yaml:"use_ip_as_community"`
	// Subnets is an optional list of CIDRs whose IPs are all pre-seeded into
	// the BFS queue. This guarantees every host in the range is probed even
	// when LLDP/CDP neighbor tables don't link them back to the seed.
	Subnets []string `yaml:"subnets"`
}

// Load loads configuration from a YAML file
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	var config Config
	if err := yaml.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("failed to parse config file: %w", err)
	}

	// Validate configuration
	if err := config.Validate(); err != nil {
		return nil, fmt.Errorf("invalid configuration: %w", err)
	}

	return &config, nil
}

// Validate validates the configuration
func (c *Config) Validate() error {
	// Validate server settings
	if c.Server.Port < 1 || c.Server.Port > 65535 {
		return fmt.Errorf("invalid server port: %d", c.Server.Port)
	}

	// Validate TLS settings
	if c.TLS.Enabled {
		if c.TLS.ServerCertPath == "" {
			return fmt.Errorf("tls.server_cert_path is required when TLS is enabled")
		}
		if c.TLS.ServerKeyPath == "" {
			return fmt.Errorf("tls.server_key_path is required when TLS is enabled")
		}
		if c.TLS.ClientAuth == "require_and_verify" && c.TLS.CACertPath == "" {
			return fmt.Errorf("tls.ca_cert_path is required when client auth is require_and_verify")
		}
	}

	// Validate database settings
	if c.Database.Type != "sqlite" && c.Database.Type != "postgres" && c.Database.Type != "mysql" {
		return fmt.Errorf("invalid database type: %s (must be sqlite, postgres, or mysql)", c.Database.Type)
	}

	// Validate license settings
	if c.License.Mode != "file" && c.License.Mode != "database" && c.License.Mode != "disabled" {
		return fmt.Errorf("invalid license mode: %s", c.License.Mode)
	}

	return nil
}

// GetDatabaseConnectionString returns the database connection string
func (c *Config) GetDatabaseConnectionString() string {
	switch c.Database.Type {
	case "sqlite":
		return c.Database.SQLite.Path
	case "postgres":
		return fmt.Sprintf("host=%s port=%d user=%s password=%s dbname=%s sslmode=%s",
			c.Database.Postgres.Host,
			c.Database.Postgres.Port,
			c.Database.Postgres.User,
			c.Database.Postgres.Password,
			c.Database.Postgres.Database,
			c.Database.Postgres.SSLMode,
		)
	case "mysql":
		return fmt.Sprintf("%s:%s@tcp(%s:%d)/%s?parseTime=true",
			c.Database.MySQL.User,
			c.Database.MySQL.Password,
			c.Database.MySQL.Host,
			c.Database.MySQL.Port,
			c.Database.MySQL.Database,
		)
	default:
		return ""
	}
}

// GetServerAddress returns the full server address
func (c *Config) GetServerAddress() string {
	return fmt.Sprintf("%s:%d", c.Server.Address, c.Server.Port)
}

// GetMigrationsPath returns the path to the migrations directory
func (c *Config) GetMigrationsPath() string {
	// Get executable directory
	exePath, err := os.Executable()
	if err != nil {
		// Fallback to current directory if executable path cannot be determined
		return "./migrations"
	}

	// Get directory containing the executable
	exeDir := filepath.Dir(exePath)

	// Return migrations path relative to executable
	return filepath.Join(exeDir, "migrations")
}

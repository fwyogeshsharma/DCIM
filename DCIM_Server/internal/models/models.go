package models

import (
	"database/sql/driver"
	"encoding/json"
	"time"
)

// Agent represents a monitoring agent
type Agent struct {
	ID               int64     `json:"id" db:"id"`
	AgentID          string    `json:"agent_id" db:"agent_id"`                   // Unique agent identifier
	CertificateCN    string    `json:"certificate_cn" db:"certificate_cn"`       // Client certificate CN
	Hostname         string    `json:"hostname" db:"hostname"`
	IPAddress        string    `json:"ip_address" db:"ip_address"`
	Status           string    `json:"status" db:"status"`                       // online, offline, pending
	Group            string    `json:"group" db:"group"`
	LastSeen         time.Time `json:"last_seen" db:"last_seen"`
	FirstSeen        time.Time `json:"first_seen" db:"first_seen"`
	RegisteredAt     time.Time `json:"registered_at" db:"registered_at"`
	ApprovedAt       *time.Time `json:"approved_at,omitempty" db:"approved_at"`
	Approved         bool      `json:"approved" db:"approved"`
	TotalMetrics     int64     `json:"total_metrics" db:"total_metrics"`
	TotalAlerts      int64     `json:"total_alerts" db:"total_alerts"`
	Metadata         JSONMap   `json:"metadata,omitempty" db:"metadata"`
	CreatedAt        time.Time `json:"created_at" db:"created_at"`
	UpdatedAt        time.Time `json:"updated_at" db:"updated_at"`
}

// Metric represents a system metric
type Metric struct {
	ID         int64     `json:"id" db:"id"`
	AgentID    string    `json:"agent_id" db:"agent_id"`
	Timestamp  time.Time `json:"timestamp" db:"timestamp"`
	MetricType string    `json:"metric_type" db:"metric_type"`
	Value      float64   `json:"value" db:"value"`
	Unit       string    `json:"unit" db:"unit"`
	Metadata   JSONMap   `json:"metadata,omitempty" db:"metadata"`
	CreatedAt  time.Time `json:"created_at" db:"created_at"`
}

// Alert represents a system alert
type Alert struct {
	ID         int64     `json:"id" db:"id"`
	AgentID    string    `json:"agent_id" db:"agent_id"`
	Timestamp  time.Time `json:"timestamp" db:"timestamp"`
	Severity   string    `json:"severity" db:"severity"`           // INFO, WARNING, CRITICAL
	MetricType string    `json:"metric_type" db:"metric_type"`
	Value      float64   `json:"value" db:"value"`
	Threshold  float64   `json:"threshold" db:"threshold"`
	Message    string    `json:"message" db:"message"`
	RetryCount int       `json:"retry_count" db:"retry_count"`
	Resolved   bool      `json:"resolved" db:"resolved"`
	ResolvedAt *time.Time `json:"resolved_at,omitempty" db:"resolved_at"`
	CreatedAt  time.Time `json:"created_at" db:"created_at"`
}

// SNMPMetric represents an SNMP device metric
type SNMPMetric struct {
	ID         int64     `json:"id" db:"id"`
	AgentID    string    `json:"agent_id" db:"agent_id"`
	Timestamp  time.Time `json:"timestamp" db:"timestamp"`
	DeviceName string    `json:"device_name" db:"device_name"`
	DeviceHost string    `json:"device_host" db:"device_host"`
	OID        string    `json:"oid" db:"oid"`
	MetricName string    `json:"metric_name" db:"metric_name"`
	Value      float64   `json:"value" db:"value"`
	ValueType  string    `json:"value_type" db:"value_type"`       // gauge, counter, string
	Metadata   JSONMap   `json:"metadata,omitempty" db:"metadata"`
	CreatedAt  time.Time `json:"created_at" db:"created_at"`
}

// AgentStatus represents agent status history
type AgentStatus struct {
	ID        int64     `json:"id" db:"id"`
	AgentID   string    `json:"agent_id" db:"agent_id"`
	Status    string    `json:"status" db:"status"`               // online, offline
	Timestamp time.Time `json:"timestamp" db:"timestamp"`
	Reason    string    `json:"reason,omitempty" db:"reason"`
	CreatedAt time.Time `json:"created_at" db:"created_at"`
}

// License represents a license entry
type License struct {
	ID              int64     `json:"id" db:"id"`
	LicenseKey      string    `json:"license_key" db:"license_key"`
	CompanyName     string    `json:"company_name" db:"company_name"`
	Email           string    `json:"email" db:"email"`
	MaxAgents       int       `json:"max_agents" db:"max_agents"`
	MaxSNMPDevices  int       `json:"max_snmp_devices" db:"max_snmp_devices"`
	Features        JSONArray `json:"features" db:"features"`
	IssuedAt        time.Time `json:"issued_at" db:"issued_at"`
	ExpiresAt       time.Time `json:"expires_at" db:"expires_at"`
	Active          bool      `json:"active" db:"active"`
	CreatedAt       time.Time `json:"created_at" db:"created_at"`
	UpdatedAt       time.Time `json:"updated_at" db:"updated_at"`
}

// AggregatedMetric represents aggregated metric data
type AggregatedMetric struct {
	ID         int64     `json:"id" db:"id"`
	AgentID    string    `json:"agent_id" db:"agent_id"`
	MetricType string    `json:"metric_type" db:"metric_type"`
	Interval   string    `json:"interval" db:"interval"`           // 1m, 5m, 1h, 1d
	Timestamp  time.Time `json:"timestamp" db:"timestamp"`
	MinValue   float64   `json:"min_value" db:"min_value"`
	MaxValue   float64   `json:"max_value" db:"max_value"`
	AvgValue   float64   `json:"avg_value" db:"avg_value"`
	SumValue   float64   `json:"sum_value" db:"sum_value"`
	Count      int64     `json:"count" db:"count"`
	CreatedAt  time.Time `json:"created_at" db:"created_at"`
}

// JSONMap is a custom type for JSON map storage
type JSONMap map[string]interface{}

// Value implements the driver.Valuer interface
func (j JSONMap) Value() (driver.Value, error) {
	if j == nil {
		return nil, nil
	}
	return json.Marshal(j)
}

// Scan implements the sql.Scanner interface
func (j *JSONMap) Scan(value interface{}) error {
	if value == nil {
		*j = nil
		return nil
	}
	bytes, ok := value.([]byte)
	if !ok {
		return nil
	}
	return json.Unmarshal(bytes, j)
}

// JSONArray is a custom type for JSON array storage
type JSONArray []string

// Value implements the driver.Valuer interface
func (j JSONArray) Value() (driver.Value, error) {
	if j == nil {
		return nil, nil
	}
	return json.Marshal(j)
}

// Scan implements the sql.Scanner interface
func (j *JSONArray) Scan(value interface{}) error {
	if value == nil {
		*j = nil
		return nil
	}
	bytes, ok := value.([]byte)
	if !ok {
		return nil
	}
	return json.Unmarshal(bytes, j)
}

// Request/Response models for API

// MetricsRequest represents incoming metrics from an agent
type MetricsRequest struct {
	AgentID   string   `json:"agent_id"`
	Timestamp string   `json:"timestamp"`
	Metrics   []Metric `json:"metrics"`
}

// AlertsRequest represents incoming alerts from an agent
type AlertsRequest struct {
	AgentID   string  `json:"agent_id"`
	Timestamp string  `json:"timestamp"`
	Alerts    []Alert `json:"alerts"`
}

// SNMPMetricsRequest represents incoming SNMP metrics from an agent
type SNMPMetricsRequest struct {
	AgentID     string       `json:"agent_id"`
	Timestamp   string       `json:"timestamp"`
	SNMPMetrics []SNMPMetric `json:"snmp_metrics"`
}

// APIResponse represents a standard API response
type APIResponse struct {
	Success  bool        `json:"success"`
	Message  string      `json:"message"`
	Error    string      `json:"error,omitempty"`
	Data     interface{} `json:"data,omitempty"`
	Accepted int         `json:"accepted,omitempty"`
	Rejected int         `json:"rejected,omitempty"`
}

// HealthResponse represents health check response
type HealthResponse struct {
	Status       string                 `json:"status"`
	Timestamp    time.Time              `json:"timestamp"`
	Service      string                 `json:"service"`
	Version      string                 `json:"version"`
	Uptime       time.Duration          `json:"uptime"`
	TotalAgents  int                    `json:"total_agents"`
	OnlineAgents int                    `json:"online_agents"`
	Details      map[string]interface{} `json:"details,omitempty"`
}

// AgentRegistrationRequest represents agent registration request
type AgentRegistrationRequest struct {
	AgentID   string            `json:"agent_id"`
	Hostname  string            `json:"hostname"`
	IPAddress string            `json:"ip_address"`
	Metadata  map[string]string `json:"metadata,omitempty"`
}

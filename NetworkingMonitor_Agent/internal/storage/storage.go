package storage

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

type Storage struct {
	db *sql.DB
}

type Metric struct {
	ID         int64
	Timestamp  time.Time
	MetricType string
	Value      float64
	Unit       string
	Metadata   map[string]interface{}
	Sent       bool
	CreatedAt  time.Time
	SentAt     *time.Time
}

type Alert struct {
	ID         int64
	Timestamp  time.Time
	Severity   string
	MetricType string
	Value      float64
	Threshold  float64
	Message    string
	Sent       bool
	CreatedAt  time.Time
	SentAt     *time.Time
	RetryCount int
}

type SystemInfo struct {
	AgentID      string
	Hostname     string
	OS           string
	Platform     string
	Architecture string
	CPUModel     string
	CPUCores     int
	TotalMemory  uint64
	UpdatedAt    time.Time
}

type SNMPMetric struct {
	ID         int64                  `json:"id"`
	Timestamp  time.Time              `json:"timestamp"`
	DeviceName string                 `json:"device_name"`
	DeviceHost string                 `json:"device_host"`
	OID        string                 `json:"oid"`
	MetricName string                 `json:"metric_name"`
	Value      float64                `json:"value"`
	ValueType  string                 `json:"value_type"`
	Metadata   map[string]interface{} `json:"metadata,omitempty"`
	Sent       bool                   `json:"-"`
	CreatedAt  time.Time              `json:"created_at"`
	SentAt     *time.Time             `json:"sent_at,omitempty"`
}

func New(dbPath string) (*Storage, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}

	// Enable WAL mode for better concurrent performance
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return nil, fmt.Errorf("enable WAL mode: %w", err)
	}

	// Set busy timeout to 5 seconds to handle concurrent writes
	if _, err := db.Exec("PRAGMA busy_timeout=5000"); err != nil {
		return nil, fmt.Errorf("set busy timeout: %w", err)
	}

	// Set connection pool settings for better concurrency
	db.SetMaxOpenConns(1) // SQLite works best with single writer
	db.SetMaxIdleConns(1)

	// Create schema
	if _, err := db.Exec(schemaSQL); err != nil {
		return nil, fmt.Errorf("create schema: %w", err)
	}

	return &Storage{db: db}, nil
}

func (s *Storage) Close() error {
	return s.db.Close()
}

// SaveMetric stores a metric in the database
func (s *Storage) SaveMetric(m *Metric) error {
	metadata, _ := json.Marshal(m.Metadata)

	result, err := s.db.Exec(`
		INSERT INTO metrics (timestamp, metric_type, value, unit, metadata, created_at)
		VALUES (?, ?, ?, ?, ?, ?)
	`, m.Timestamp.Unix(), m.MetricType, m.Value, m.Unit, string(metadata), time.Now().Unix())

	if err != nil {
		return fmt.Errorf("insert metric: %w", err)
	}

	m.ID, _ = result.LastInsertId()
	return nil
}

// SaveAlert stores an alert in the database
func (s *Storage) SaveAlert(a *Alert) error {
	result, err := s.db.Exec(`
		INSERT INTO alerts (timestamp, severity, metric_type, value, threshold, message, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
	`, a.Timestamp.Unix(), a.Severity, a.MetricType, a.Value, a.Threshold, a.Message, time.Now().Unix())

	if err != nil {
		return fmt.Errorf("insert alert: %w", err)
	}

	a.ID, _ = result.LastInsertId()
	return nil
}

// GetUnsentAlerts retrieves alerts that haven't been sent yet
func (s *Storage) GetUnsentAlerts(limit int) ([]*Alert, error) {
	rows, err := s.db.Query(`
		SELECT id, timestamp, severity, metric_type, value, threshold, message, retry_count, created_at
		FROM alerts
		WHERE sent = 0
		ORDER BY timestamp ASC, severity DESC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, fmt.Errorf("query unsent alerts: %w", err)
	}
	defer rows.Close()

	var alerts []*Alert
	for rows.Next() {
		var a Alert
		var ts, createdAt int64
		err := rows.Scan(&a.ID, &ts, &a.Severity, &a.MetricType, &a.Value, &a.Threshold, &a.Message, &a.RetryCount, &createdAt)
		if err != nil {
			return nil, fmt.Errorf("scan alert: %w", err)
		}
		a.Timestamp = time.Unix(ts, 0)
		a.CreatedAt = time.Unix(createdAt, 0)
		alerts = append(alerts, &a)
	}

	return alerts, nil
}

// GetUnsentMetrics retrieves metrics that haven't been sent yet
func (s *Storage) GetUnsentMetrics(limit int) ([]*Metric, error) {
	rows, err := s.db.Query(`
		SELECT id, timestamp, metric_type, value, unit, metadata, created_at
		FROM metrics
		WHERE sent = 0
		ORDER BY timestamp ASC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, fmt.Errorf("query unsent metrics: %w", err)
	}
	defer rows.Close()

	var metrics []*Metric
	for rows.Next() {
		var m Metric
		var ts, createdAt int64
		var metadata string
		err := rows.Scan(&m.ID, &ts, &m.MetricType, &m.Value, &m.Unit, &metadata, &createdAt)
		if err != nil {
			return nil, fmt.Errorf("scan metric: %w", err)
		}
		m.Timestamp = time.Unix(ts, 0)
		m.CreatedAt = time.Unix(createdAt, 0)
		json.Unmarshal([]byte(metadata), &m.Metadata)
		metrics = append(metrics, &m)
	}

	return metrics, nil
}

// MarkMetricsSent marks metrics as sent
func (s *Storage) MarkMetricsSent(ids []int64) error {
	if len(ids) == 0 {
		return nil
	}

	query := "UPDATE metrics SET sent = 1, sent_at = ? WHERE id IN ("
	args := []interface{}{time.Now().Unix()}
	for i, id := range ids {
		if i > 0 {
			query += ","
		}
		query += "?"
		args = append(args, id)
	}
	query += ")"

	_, err := s.db.Exec(query, args...)
	return err
}

// MarkAlertsSent marks alerts as sent
func (s *Storage) MarkAlertsSent(ids []int64) error {
	if len(ids) == 0 {
		return nil
	}

	query := "UPDATE alerts SET sent = 1, sent_at = ? WHERE id IN ("
	args := []interface{}{time.Now().Unix()}
	for i, id := range ids {
		if i > 0 {
			query += ","
		}
		query += "?"
		args = append(args, id)
	}
	query += ")"

	_, err := s.db.Exec(query, args...)
	return err
}

// IncrementAlertRetry increments the retry count for an alert
func (s *Storage) IncrementAlertRetry(id int64) error {
	_, err := s.db.Exec("UPDATE alerts SET retry_count = retry_count + 1 WHERE id = ?", id)
	return err
}

// SaveSystemInfo stores or updates system information
func (s *Storage) SaveSystemInfo(info *SystemInfo) error {
	_, err := s.db.Exec(`
		INSERT INTO system_info (agent_id, hostname, os, platform, architecture, cpu_model, cpu_cores, total_memory, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, info.AgentID, info.Hostname, info.OS, info.Platform, info.Architecture, info.CPUModel, info.CPUCores, info.TotalMemory, time.Now().Unix())

	return err
}

// CleanupOldData removes data older than retention period
func (s *Storage) CleanupOldData(retentionDays int) error {
	cutoff := time.Now().AddDate(0, 0, -retentionDays).Unix()

	_, err := s.db.Exec("DELETE FROM metrics WHERE sent = 1 AND timestamp < ?", cutoff)
	if err != nil {
		return fmt.Errorf("cleanup metrics: %w", err)
	}

	_, err = s.db.Exec("DELETE FROM alerts WHERE sent = 1 AND timestamp < ?", cutoff)
	if err != nil {
		return fmt.Errorf("cleanup alerts: %w", err)
	}

	_, err = s.db.Exec("DELETE FROM snmp_metrics WHERE sent = 1 AND timestamp < ?", cutoff)
	if err != nil {
		return fmt.Errorf("cleanup SNMP metrics: %w", err)
	}

	_, err = s.db.Exec("DELETE FROM transmission_log WHERE timestamp < ?", cutoff)
	if err != nil {
		return fmt.Errorf("cleanup transmission log: %w", err)
	}

	return nil
}

// LogTransmission logs a transmission attempt
func (s *Storage) LogTransmission(recordType string, recordID int64, status, errorMsg string) error {
	_, err := s.db.Exec(`
		INSERT INTO transmission_log (timestamp, record_type, record_id, status, error_message, created_at)
		VALUES (?, ?, ?, ?, ?, ?)
	`, time.Now().Unix(), recordType, recordID, status, errorMsg, time.Now().Unix())
	return err
}

// SaveSNMPMetric stores an SNMP metric in the database
func (s *Storage) SaveSNMPMetric(m *SNMPMetric) error {
	metadata, _ := json.Marshal(m.Metadata)

	result, err := s.db.Exec(`
		INSERT INTO snmp_metrics (timestamp, device_name, device_host, oid, metric_name, value, value_type, metadata, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, m.Timestamp.Unix(), m.DeviceName, m.DeviceHost, m.OID, m.MetricName, m.Value, m.ValueType, string(metadata), time.Now().Unix())

	if err != nil {
		return fmt.Errorf("insert SNMP metric: %w", err)
	}

	m.ID, _ = result.LastInsertId()
	return nil
}

// GetUnsentSNMPMetrics retrieves SNMP metrics that haven't been sent yet
func (s *Storage) GetUnsentSNMPMetrics(limit int) ([]*SNMPMetric, error) {
	rows, err := s.db.Query(`
		SELECT id, timestamp, device_name, device_host, oid, metric_name, value, value_type, metadata, created_at
		FROM snmp_metrics
		WHERE sent = 0
		ORDER BY timestamp ASC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, fmt.Errorf("query unsent SNMP metrics: %w", err)
	}
	defer rows.Close()

	var metrics []*SNMPMetric
	for rows.Next() {
		var m SNMPMetric
		var ts, createdAt int64
		var metadata string

		err := rows.Scan(&m.ID, &ts, &m.DeviceName, &m.DeviceHost, &m.OID, &m.MetricName, &m.Value, &m.ValueType, &metadata, &createdAt)
		if err != nil {
			return nil, fmt.Errorf("scan SNMP metric: %w", err)
		}

		m.Timestamp = time.Unix(ts, 0)
		m.CreatedAt = time.Unix(createdAt, 0)
		if metadata != "" {
			json.Unmarshal([]byte(metadata), &m.Metadata)
		}

		metrics = append(metrics, &m)
	}

	return metrics, nil
}

// MarkSNMPMetricsSent marks SNMP metrics as sent
func (s *Storage) MarkSNMPMetricsSent(ids []int64) error {
	if len(ids) == 0 {
		return nil
	}

	query := "UPDATE snmp_metrics SET sent = 1, sent_at = ? WHERE id IN ("
	args := []interface{}{time.Now().Unix()}
	for i, id := range ids {
		if i > 0 {
			query += ","
		}
		query += "?"
		args = append(args, id)
	}
	query += ")"

	_, err := s.db.Exec(query, args...)
	return err
}

package database

import (
	"database/sql"
	"fmt"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
	"github.com/faberlabs/dcim-server/internal/models"

	// Database drivers
	_ "github.com/mattn/go-sqlite3"      // SQLite with CGO
	_ "modernc.org/sqlite"               // Pure-Go SQLite (fallback for cross-compilation)
	_ "github.com/lib/pq"                // PostgreSQL
	_ "github.com/go-sql-driver/mysql"  // MySQL
)

// Database represents the database connection and operations
type Database struct {
	db     *sql.DB
	config *config.DatabaseConfig
	dbType string
}

// New creates a new database connection
func New(cfg *config.Config) (*Database, error) {
	var db *sql.DB
	var err error

	connStr := cfg.GetDatabaseConnectionString()

	switch cfg.Database.Type {
	case "sqlite":
		// Try CGO-based driver first (better performance)
		db, err = sql.Open("sqlite3", connStr)
		if err != nil {
			// Fall back to pure-Go driver (for cross-compiled binaries without CGO)
			db, err = sql.Open("sqlite", connStr)
			if err != nil {
				return nil, fmt.Errorf("failed to open sqlite database: %w", err)
			}
		}
		// Configure SQLite connection
		db.SetMaxOpenConns(cfg.Database.SQLite.MaxOpenConns)
		db.SetMaxIdleConns(cfg.Database.SQLite.MaxIdleConns)
		db.SetConnMaxLifetime(cfg.Database.SQLite.ConnMaxLifetime)

	case "postgres":
		db, err = sql.Open("postgres", connStr)
		if err != nil {
			return nil, fmt.Errorf("failed to open postgres database: %w", err)
		}
		db.SetMaxOpenConns(cfg.Database.Postgres.MaxOpenConns)
		db.SetMaxIdleConns(cfg.Database.Postgres.MaxIdleConns)
		db.SetConnMaxLifetime(cfg.Database.Postgres.ConnMaxLifetime)

	case "mysql":
		db, err = sql.Open("mysql", connStr)
		if err != nil {
			return nil, fmt.Errorf("failed to open mysql database: %w", err)
		}
		db.SetMaxOpenConns(cfg.Database.MySQL.MaxOpenConns)
		db.SetMaxIdleConns(cfg.Database.MySQL.MaxIdleConns)
		db.SetConnMaxLifetime(cfg.Database.MySQL.ConnMaxLifetime)

	default:
		return nil, fmt.Errorf("unsupported database type: %s", cfg.Database.Type)
	}

	// Test connection
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("failed to ping database: %w", err)
	}

	database := &Database{
		db:     db,
		config: &cfg.Database,
		dbType: cfg.Database.Type,
	}

	// Initialize schema (creates tables if they don't exist)
	if err := database.InitSchema(); err != nil {
		return nil, fmt.Errorf("failed to initialize schema: %w", err)
	}

	// Run database migrations (adds new columns to existing tables)
	migrationsPath := cfg.GetMigrationsPath()
	if err := database.RunMigrations(migrationsPath); err != nil {
		return nil, fmt.Errorf("failed to run migrations: %w", err)
	}

	return database, nil
}

// Close closes the database connection
func (d *Database) Close() error {
	return d.db.Close()
}

// preparePlaceholders converts ? placeholders to database-specific format
func (d *Database) preparePlaceholders(query string) string {
	if d.dbType != "postgres" {
		return query
	}

	// Convert ? to $1, $2, $3, etc. for PostgreSQL
	var result string
	paramIndex := 1
	for i := 0; i < len(query); i++ {
		if query[i] == '?' {
			result += fmt.Sprintf("$%d", paramIndex)
			paramIndex++
		} else {
			result += string(query[i])
		}
	}
	return result
}

// InitSchema creates database tables if they don't exist
func (d *Database) InitSchema() error {
	schema := d.getSchema()

	for _, query := range schema {
		if _, err := d.db.Exec(query); err != nil {
			return fmt.Errorf("failed to execute schema query: %w", err)
		}
	}

	return nil
}

// getSchema returns the database schema for the configured database type
func (d *Database) getSchema() []string {
	switch d.dbType {
	case "postgres":
		return d.getPostgresSchema()
	case "mysql":
		return d.getMySQLSchema()
	default:
		return d.getSQLiteSchema()
	}
}

// getSQLiteSchema returns SQLite-specific schema
func (d *Database) getSQLiteSchema() []string {
	return []string{
		// Servers table - Track DCIM_Server instances
		`CREATE TABLE IF NOT EXISTS servers (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT UNIQUE NOT NULL,
			server_name TEXT NOT NULL,
			location TEXT,
			environment TEXT,
			hostname TEXT,
			version TEXT,
			status TEXT DEFAULT 'active',
			last_seen DATETIME,
			first_seen DATETIME,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for servers queries
		`CREATE INDEX IF NOT EXISTS idx_servers_id ON servers(server_id)`,
		`CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status, last_seen)`,

		// Agents table
		`CREATE TABLE IF NOT EXISTS agents (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			agent_id TEXT UNIQUE NOT NULL,
			server_id TEXT NOT NULL,
			certificate_cn TEXT,
			hostname TEXT,
			ip_address TEXT,
			status TEXT DEFAULT 'pending',
			group_name TEXT DEFAULT 'default',
			last_seen DATETIME,
			first_seen DATETIME,
			registered_at DATETIME,
			approved_at DATETIME,
			approved INTEGER DEFAULT 0,
			total_metrics INTEGER DEFAULT 0,
			total_alerts INTEGER DEFAULT 0,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Metrics table
		`CREATE TABLE IF NOT EXISTS metrics (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp DATETIME NOT NULL,
			metric_type TEXT NOT NULL,
			value REAL NOT NULL,
			unit TEXT,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for metrics queries
		// NOTE: idx_metrics_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_metrics_agent_time ON metrics(agent_id, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_metrics_type_time ON metrics(metric_type, timestamp)`,

		// Alerts table
		`CREATE TABLE IF NOT EXISTS alerts (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp DATETIME NOT NULL,
			severity TEXT NOT NULL,
			metric_type TEXT NOT NULL,
			value REAL NOT NULL,
			threshold REAL NOT NULL,
			message TEXT NOT NULL,
			occurrence_count INTEGER DEFAULT 1,
			first_seen DATETIME NOT NULL,
			last_seen DATETIME NOT NULL,
			retry_count INTEGER DEFAULT 0,
			resolved INTEGER DEFAULT 0,
			resolved_at DATETIME,
			resolved_by TEXT,
			resolution_action TEXT,
			resolution_notes TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for alerts queries
		// NOTE: idx_alerts_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_alerts_agent_time ON alerts(agent_id, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity, resolved)`,
		// NOTE: idx_alerts_dedup created by migration 002_server_tracking.sql

		// SNMP Metrics table
		`CREATE TABLE IF NOT EXISTS snmp_metrics (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp DATETIME NOT NULL,
			device_name TEXT NOT NULL,
			device_host TEXT NOT NULL,
			oid TEXT NOT NULL,
			metric_name TEXT NOT NULL,
			value REAL NOT NULL,
			value_type TEXT NOT NULL,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for SNMP metrics queries
		// NOTE: idx_snmp_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_snmp_agent_device ON snmp_metrics(agent_id, device_name, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_snmp_device_time ON snmp_metrics(device_host, timestamp)`,

		// Agent Status History table
		`CREATE TABLE IF NOT EXISTS agent_status_history (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			status TEXT NOT NULL,
			timestamp DATETIME NOT NULL,
			reason TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for status history
		// NOTE: idx_agent_status_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_agent_status_time ON agent_status_history(agent_id, timestamp)`,

		// Licenses table
		`CREATE TABLE IF NOT EXISTS licenses (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			license_key TEXT UNIQUE NOT NULL,
			company_name TEXT NOT NULL,
			email TEXT NOT NULL,
			max_agents INTEGER NOT NULL,
			max_snmp_devices INTEGER NOT NULL,
			features TEXT,
			issued_at DATETIME NOT NULL,
			expires_at DATETIME NOT NULL,
			active INTEGER DEFAULT 1,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Aggregated Metrics table
		`CREATE TABLE IF NOT EXISTS aggregated_metrics (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			metric_type TEXT NOT NULL,
			interval TEXT NOT NULL,
			timestamp DATETIME NOT NULL,
			min_value REAL NOT NULL,
			max_value REAL NOT NULL,
			avg_value REAL NOT NULL,
			sum_value REAL NOT NULL,
			count INTEGER NOT NULL,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for aggregated metrics
		// NOTE: idx_agg_metrics_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_agg_metrics ON aggregated_metrics(agent_id, metric_type, interval, timestamp)`,
	}
}

// getPostgresSchema returns PostgreSQL-specific schema
func (d *Database) getPostgresSchema() []string {
	return []string{
		// Servers table - Track DCIM_Server instances
		`CREATE TABLE IF NOT EXISTS servers (
			id SERIAL PRIMARY KEY,
			server_id TEXT UNIQUE NOT NULL,
			server_name TEXT NOT NULL,
			location TEXT,
			environment TEXT,
			hostname TEXT,
			version TEXT,
			status TEXT DEFAULT 'active',
			last_seen TIMESTAMP,
			first_seen TIMESTAMP,
			metadata TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for servers queries
		`CREATE INDEX IF NOT EXISTS idx_servers_id ON servers(server_id)`,
		`CREATE INDEX IF NOT EXISTS idx_servers_status ON servers(status, last_seen)`,

		// Agents table
		`CREATE TABLE IF NOT EXISTS agents (
			id SERIAL PRIMARY KEY,
			agent_id TEXT UNIQUE NOT NULL,
			server_id TEXT NOT NULL,
			certificate_cn TEXT,
			hostname TEXT,
			ip_address TEXT,
			status TEXT DEFAULT 'pending',
			group_name TEXT DEFAULT 'default',
			last_seen TIMESTAMP,
			first_seen TIMESTAMP,
			registered_at TIMESTAMP,
			approved_at TIMESTAMP,
			approved BOOLEAN DEFAULT false,
			total_metrics INTEGER DEFAULT 0,
			total_alerts INTEGER DEFAULT 0,
			metadata TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Metrics table
		`CREATE TABLE IF NOT EXISTS metrics (
			id SERIAL PRIMARY KEY,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp TIMESTAMP NOT NULL,
			metric_type TEXT NOT NULL,
			value REAL NOT NULL,
			unit TEXT,
			metadata TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for metrics queries
		// NOTE: idx_metrics_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_metrics_agent_time ON metrics(agent_id, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_metrics_type_time ON metrics(metric_type, timestamp)`,

		// Alerts table
		`CREATE TABLE IF NOT EXISTS alerts (
			id SERIAL PRIMARY KEY,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp TIMESTAMP NOT NULL,
			severity TEXT NOT NULL,
			metric_type TEXT NOT NULL,
			value REAL NOT NULL,
			threshold REAL NOT NULL,
			message TEXT NOT NULL,
			occurrence_count INTEGER DEFAULT 1,
			first_seen TIMESTAMP NOT NULL,
			last_seen TIMESTAMP NOT NULL,
			retry_count INTEGER DEFAULT 0,
			resolved BOOLEAN DEFAULT false,
			resolved_at TIMESTAMP,
			resolved_by TEXT,
			resolution_action TEXT,
			resolution_notes TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for alerts queries
		// NOTE: idx_alerts_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_alerts_agent_time ON alerts(agent_id, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity, resolved)`,
		// NOTE: idx_alerts_dedup created by migration 002_server_tracking.sql

		// SNMP Metrics table
		`CREATE TABLE IF NOT EXISTS snmp_metrics (
			id SERIAL PRIMARY KEY,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			timestamp TIMESTAMP NOT NULL,
			device_name TEXT NOT NULL,
			device_host TEXT NOT NULL,
			oid TEXT NOT NULL,
			metric_name TEXT NOT NULL,
			value REAL NOT NULL,
			value_type TEXT NOT NULL,
			metadata TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for SNMP metrics queries
		// NOTE: idx_snmp_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_snmp_agent_device ON snmp_metrics(agent_id, device_name, timestamp)`,
		`CREATE INDEX IF NOT EXISTS idx_snmp_device_time ON snmp_metrics(device_host, timestamp)`,

		// Agent Status History table
		`CREATE TABLE IF NOT EXISTS agent_status_history (
			id SERIAL PRIMARY KEY,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			status TEXT NOT NULL,
			timestamp TIMESTAMP NOT NULL,
			reason TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for status history
		// NOTE: idx_agent_status_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_agent_status_time ON agent_status_history(agent_id, timestamp)`,

		// Licenses table
		`CREATE TABLE IF NOT EXISTS licenses (
			id SERIAL PRIMARY KEY,
			license_key TEXT UNIQUE NOT NULL,
			company_name TEXT NOT NULL,
			email TEXT NOT NULL,
			max_agents INTEGER NOT NULL,
			max_snmp_devices INTEGER NOT NULL,
			features TEXT,
			issued_at TIMESTAMP NOT NULL,
			expires_at TIMESTAMP NOT NULL,
			active BOOLEAN DEFAULT true,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Aggregated Metrics table
		`CREATE TABLE IF NOT EXISTS aggregated_metrics (
			id SERIAL PRIMARY KEY,
			server_id TEXT NOT NULL,
			agent_id TEXT NOT NULL,
			metric_type TEXT NOT NULL,
			interval TEXT NOT NULL,
			timestamp TIMESTAMP NOT NULL,
			min_value REAL NOT NULL,
			max_value REAL NOT NULL,
			avg_value REAL NOT NULL,
			sum_value REAL NOT NULL,
			count INTEGER NOT NULL,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		)`,

		// Index for aggregated metrics
		// NOTE: idx_agg_metrics_server created by migration 002_server_tracking.sql
		`CREATE INDEX IF NOT EXISTS idx_agg_metrics ON aggregated_metrics(agent_id, metric_type, interval, timestamp)`,
	}
}

// getMySQLSchema returns MySQL-specific schema
func (d *Database) getMySQLSchema() []string {
	return []string{
		// Agents table
		`CREATE TABLE IF NOT EXISTS agents (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) UNIQUE NOT NULL,
			server_id VARCHAR(255) NOT NULL,
			certificate_cn VARCHAR(255),
			hostname VARCHAR(255),
			ip_address VARCHAR(45),
			status VARCHAR(50) DEFAULT 'pending',
			group_name VARCHAR(100) DEFAULT 'default',
			last_seen DATETIME,
			first_seen DATETIME,
			registered_at DATETIME,
			approved_at DATETIME,
			approved TINYINT DEFAULT 0,
			total_metrics INT DEFAULT 0,
			total_alerts INT DEFAULT 0,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
		)`,

		// Metrics table
		`CREATE TABLE IF NOT EXISTS metrics (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) NOT NULL,
			timestamp DATETIME NOT NULL,
			metric_type VARCHAR(100) NOT NULL,
			value DOUBLE NOT NULL,
			unit VARCHAR(50),
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			INDEX idx_metrics_agent_time (agent_id, timestamp),
			INDEX idx_metrics_type_time (metric_type, timestamp)
		)`,

		// Alerts table
		`CREATE TABLE IF NOT EXISTS alerts (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) NOT NULL,
			timestamp DATETIME NOT NULL,
			severity VARCHAR(50) NOT NULL,
			metric_type VARCHAR(100) NOT NULL,
			value DOUBLE NOT NULL,
			threshold DOUBLE NOT NULL,
			message TEXT NOT NULL,
			retry_count INT DEFAULT 0,
			resolved TINYINT DEFAULT 0,
			resolved_at DATETIME,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			INDEX idx_alerts_agent_time (agent_id, timestamp),
			INDEX idx_alerts_severity (severity, resolved)
		)`,

		// SNMP Metrics table
		`CREATE TABLE IF NOT EXISTS snmp_metrics (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) NOT NULL,
			timestamp DATETIME NOT NULL,
			device_name VARCHAR(255) NOT NULL,
			device_host VARCHAR(255) NOT NULL,
			oid VARCHAR(255) NOT NULL,
			metric_name VARCHAR(255) NOT NULL,
			value DOUBLE NOT NULL,
			value_type VARCHAR(50) NOT NULL,
			metadata TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			INDEX idx_snmp_agent_device (agent_id, device_name, timestamp),
			INDEX idx_snmp_device_time (device_host, timestamp)
		)`,

		// Agent Status History table
		`CREATE TABLE IF NOT EXISTS agent_status_history (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) NOT NULL,
			status VARCHAR(50) NOT NULL,
			timestamp DATETIME NOT NULL,
			reason TEXT,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			INDEX idx_agent_status_time (agent_id, timestamp)
		)`,

		// Licenses table
		`CREATE TABLE IF NOT EXISTS licenses (
			id INT AUTO_INCREMENT PRIMARY KEY,
			license_key VARCHAR(255) UNIQUE NOT NULL,
			company_name VARCHAR(255) NOT NULL,
			email VARCHAR(255) NOT NULL,
			max_agents INT NOT NULL,
			max_snmp_devices INT NOT NULL,
			features TEXT,
			issued_at DATETIME NOT NULL,
			expires_at DATETIME NOT NULL,
			active TINYINT DEFAULT 1,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
		)`,

		// Aggregated Metrics table
		`CREATE TABLE IF NOT EXISTS aggregated_metrics (
			id INT AUTO_INCREMENT PRIMARY KEY,
			agent_id VARCHAR(255) NOT NULL,
			metric_type VARCHAR(100) NOT NULL,
			interval VARCHAR(20) NOT NULL,
			timestamp DATETIME NOT NULL,
			min_value DOUBLE NOT NULL,
			max_value DOUBLE NOT NULL,
			avg_value DOUBLE NOT NULL,
			sum_value DOUBLE NOT NULL,
			count INT NOT NULL,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
			INDEX idx_agg_metrics (agent_id, metric_type, interval, timestamp)
		)`,
	}
}

// Agent operations

// RegisterAgent registers a new agent or updates existing one
// It first checks if an agent with the same hostname already exists
// If yes, it updates that agent instead of creating a new one
func (d *Database) RegisterAgent(agent *models.Agent) error {
	now := time.Now()

	// First, check if an agent with this hostname already exists
	existingAgent, err := d.GetAgentByHostname(agent.Hostname)
	if err == nil && existingAgent != nil {
		// Agent with same hostname exists - update it instead
		// Use the existing agent's ID but update other fields
		agent.AgentID = existingAgent.AgentID
		agent.FirstSeen = existingAgent.FirstSeen
		agent.RegisteredAt = existingAgent.RegisteredAt
	}

	query := `
		INSERT INTO agents (agent_id, server_id, certificate_cn, hostname, ip_address, status, group_name,
			first_seen, last_seen, registered_at, approved, metadata, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(agent_id) DO UPDATE SET
			server_id = excluded.server_id,
			certificate_cn = excluded.certificate_cn,
			hostname = excluded.hostname,
			ip_address = excluded.ip_address,
			status = excluded.status,
			last_seen = excluded.last_seen,
			updated_at = excluded.updated_at
	`

	// Convert placeholders for the current database type
	query = d.preparePlaceholders(query)

	_, err = d.db.Exec(query,
		agent.AgentID,
		agent.ServerID,
		agent.CertificateCN,
		agent.Hostname,
		agent.IPAddress,
		agent.Status,
		agent.Group,
		now,
		now,
		now,
		agent.Approved,
		agent.Metadata,
		now,
		now,
	)

	return err
}

// GetAgent retrieves an agent by ID
func (d *Database) GetAgent(agentID string) (*models.Agent, error) {
	query := d.preparePlaceholders(`SELECT id, agent_id, COALESCE(server_id, ''), COALESCE(certificate_cn, ''), COALESCE(hostname, ''), COALESCE(ip_address, ''), COALESCE(status, 'pending'), COALESCE(group_name, 'default'), COALESCE(last_seen, created_at), COALESCE(first_seen, created_at), COALESCE(registered_at, created_at), approved_at, COALESCE(approved, false), COALESCE(total_metrics, 0), COALESCE(total_alerts, 0), metadata, created_at, updated_at FROM agents WHERE agent_id = ?`)

	var agent models.Agent
	err := d.db.QueryRow(query, agentID).Scan(
		&agent.ID,
		&agent.AgentID,
		&agent.ServerID,
		&agent.CertificateCN,
		&agent.Hostname,
		&agent.IPAddress,
		&agent.Status,
		&agent.Group,
		&agent.LastSeen,
		&agent.FirstSeen,
		&agent.RegisteredAt,
		&agent.ApprovedAt,
		&agent.Approved,
		&agent.TotalMetrics,
		&agent.TotalAlerts,
		&agent.Metadata,
		&agent.CreatedAt,
		&agent.UpdatedAt,
	)

	if err != nil {
		if err == sql.ErrNoRows {
			return nil, fmt.Errorf("agent not found: %s", agentID)
		}
		return nil, err
	}

	return &agent, nil
}

// GetAgentByHostname retrieves an agent by hostname
func (d *Database) GetAgentByHostname(hostname string) (*models.Agent, error) {
	query := d.preparePlaceholders(`SELECT id, agent_id, COALESCE(server_id, ''), COALESCE(certificate_cn, ''), COALESCE(hostname, ''), COALESCE(ip_address, ''), COALESCE(status, 'pending'), COALESCE(group_name, 'default'), COALESCE(last_seen, created_at), COALESCE(first_seen, created_at), COALESCE(registered_at, created_at), approved_at, COALESCE(approved, false), COALESCE(total_metrics, 0), COALESCE(total_alerts, 0), metadata, created_at, updated_at FROM agents WHERE hostname = ? LIMIT 1`)

	var agent models.Agent
	err := d.db.QueryRow(query, hostname).Scan(
		&agent.ID,
		&agent.AgentID,
		&agent.ServerID,
		&agent.CertificateCN,
		&agent.Hostname,
		&agent.IPAddress,
		&agent.Status,
		&agent.Group,
		&agent.LastSeen,
		&agent.FirstSeen,
		&agent.RegisteredAt,
		&agent.ApprovedAt,
		&agent.Approved,
		&agent.TotalMetrics,
		&agent.TotalAlerts,
		&agent.Metadata,
		&agent.CreatedAt,
		&agent.UpdatedAt,
	)

	if err != nil {
		return nil, err
	}

	return &agent, nil
}

// UpdateAgentLastSeen updates the agent's last seen timestamp
func (d *Database) UpdateAgentLastSeen(agentID string) error {
	query := d.preparePlaceholders(`UPDATE agents SET last_seen = ?, updated_at = ? WHERE agent_id = ?`)
	_, err := d.db.Exec(query, time.Now(), time.Now(), agentID)
	return err
}

// GetAllAgents retrieves all agents
func (d *Database) GetAllAgents() ([]models.Agent, error) {
	query := `SELECT id, agent_id, COALESCE(server_id, ''), COALESCE(certificate_cn, ''), COALESCE(hostname, ''), COALESCE(ip_address, ''), COALESCE(status, 'pending'), COALESCE(group_name, 'default'), COALESCE(last_seen, created_at), COALESCE(first_seen, created_at), COALESCE(registered_at, created_at), approved_at, COALESCE(approved, false), COALESCE(total_metrics, 0), COALESCE(total_alerts, 0), metadata, created_at, updated_at FROM agents ORDER BY last_seen DESC`

	rows, err := d.db.Query(query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var agents []models.Agent
	for rows.Next() {
		var agent models.Agent
		err := rows.Scan(
			&agent.ID,
			&agent.AgentID,
			&agent.ServerID,
			&agent.CertificateCN,
			&agent.Hostname,
			&agent.IPAddress,
			&agent.Status,
			&agent.Group,
			&agent.LastSeen,
			&agent.FirstSeen,
			&agent.RegisteredAt,
			&agent.ApprovedAt,
			&agent.Approved,
			&agent.TotalMetrics,
			&agent.TotalAlerts,
			&agent.Metadata,
			&agent.CreatedAt,
			&agent.UpdatedAt,
		)
		if err != nil {
			return nil, err
		}
		agents = append(agents, agent)
	}

	return agents, nil
}

// GetAgentMetrics retrieves metrics for a specific agent with filters
func (d *Database) GetAgentMetrics(agentID string, timeRange time.Duration, metricType string, limit int) ([]models.Metric, error) {
	// Calculate time threshold
	since := time.Now().Add(-timeRange)

	// Build query with optional metric_type filter
	query := `SELECT id, agent_id, timestamp, metric_type, value, unit, metadata, created_at
			  FROM metrics
			  WHERE agent_id = ? AND timestamp >= ?`

	args := []interface{}{agentID, since}

	if metricType != "" {
		query += " AND metric_type = ?"
		args = append(args, metricType)
	}

	query += " ORDER BY timestamp DESC LIMIT ?"
	args = append(args, limit)

	rows, err := d.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var metrics []models.Metric
	for rows.Next() {
		var metric models.Metric
		err := rows.Scan(
			&metric.ID,
			&metric.AgentID,
			&metric.Timestamp,
			&metric.MetricType,
			&metric.Value,
			&metric.Unit,
			&metric.Metadata,
			&metric.CreatedAt,
		)
		if err != nil {
			return nil, err
		}
		metrics = append(metrics, metric)
	}

	return metrics, nil
}

// InsertMetrics inserts multiple metrics in a batch
func (d *Database) InsertMetrics(serverID string, metrics []models.Metric) error {
	if len(metrics) == 0 {
		return nil
	}

	tx, err := d.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	insertQuery := d.preparePlaceholders(`
		INSERT INTO metrics (server_id, agent_id, timestamp, metric_type, value, unit, metadata, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`)
	stmt, err := tx.Prepare(insertQuery)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, metric := range metrics {
		_, err := stmt.Exec(
			serverID,
			metric.AgentID,
			metric.Timestamp,
			metric.MetricType,
			metric.Value,
			metric.Unit,
			metric.Metadata,
			time.Now(),
		)
		if err != nil {
			return err
		}
	}

	// Update agent total metrics count
	if len(metrics) > 0 {
		agentID := metrics[0].AgentID
		updateQuery := d.preparePlaceholders(`
			UPDATE agents
			SET total_metrics = total_metrics + ?, updated_at = ?
			WHERE agent_id = ?
		`)
		_, err = tx.Exec(updateQuery, len(metrics), time.Now(), agentID)
		if err != nil {
			return err
		}
	}

	return tx.Commit()
}

// InsertAlerts inserts alerts with deduplication - increments count if alert exists
func (d *Database) InsertAlerts(serverID string, alerts []models.Alert) error {
	if len(alerts) == 0 {
		return nil
	}

	newAlertsCount := 0

	for _, alert := range alerts {
		// Check if same alert exists (not resolved)
		var existingID int64
		var existingCount int
		var firstSeen time.Time

		checkQuery := d.preparePlaceholders(`
			SELECT id, occurrence_count, first_seen
			FROM alerts
			WHERE agent_id = ?
			  AND metric_type = ?
			  AND severity = ?
			  AND resolved = ?
			LIMIT 1
		`)

		err := d.db.QueryRow(checkQuery, alert.AgentID, alert.MetricType, alert.Severity, false).
			Scan(&existingID, &existingCount, &firstSeen)

		if err == sql.ErrNoRows {
			// New alert - insert
			insertQuery := d.preparePlaceholders(`
				INSERT INTO alerts (
					server_id, agent_id, timestamp, severity, metric_type,
					value, threshold, message, occurrence_count,
					first_seen, last_seen, resolved, created_at, updated_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
			`)

			now := time.Now()
			_, err = d.db.Exec(insertQuery,
				serverID,
				alert.AgentID,
				alert.Timestamp,
				alert.Severity,
				alert.MetricType,
				alert.Value,
				alert.Threshold,
				alert.Message,
				alert.Timestamp,  // first_seen
				alert.Timestamp,  // last_seen
				alert.Resolved,
				now,              // created_at
				now,              // updated_at
			)

			if err != nil {
				return fmt.Errorf("failed to insert alert: %w", err)
			}

			newAlertsCount++
		} else if err != nil {
			return fmt.Errorf("failed to check existing alert: %w", err)
		} else {
			// Alert exists - increment count and update timestamps
			updateQuery := d.preparePlaceholders(`
				UPDATE alerts
				SET occurrence_count = occurrence_count + 1,
				    last_seen = ?,
				    updated_at = ?,
				    value = ?,
				    threshold = ?,
				    message = ?
				WHERE id = ?
			`)

			_, err = d.db.Exec(updateQuery,
				alert.Timestamp,  // last_seen
				time.Now(),       // updated_at
				alert.Value,      // update current value
				alert.Threshold,  // update threshold
				alert.Message,    // update message
				existingID,
			)

			if err != nil {
				return fmt.Errorf("failed to update alert count: %w", err)
			}
		}
	}

	// Update agent total alerts count (only for new alerts)
	if newAlertsCount > 0 && len(alerts) > 0 {
		agentID := alerts[0].AgentID
		updateQuery := d.preparePlaceholders(`
			UPDATE agents
			SET total_alerts = total_alerts + ?, updated_at = ?
			WHERE agent_id = ?
		`)
		_, err := d.db.Exec(updateQuery, newAlertsCount, time.Now(), agentID)
		if err != nil {
			return err
		}
	}

	return nil
}

// InsertSNMPMetrics inserts multiple SNMP metrics in a batch
func (d *Database) InsertSNMPMetrics(serverID string, metrics []models.SNMPMetric) error {
	if len(metrics) == 0 {
		return nil
	}

	tx, err := d.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	insertQuery := d.preparePlaceholders(`
		INSERT INTO snmp_metrics (server_id, agent_id, timestamp, device_name, device_host, oid,
			metric_name, value, value_type, metadata, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`)
	stmt, err := tx.Prepare(insertQuery)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, metric := range metrics {
		_, err := stmt.Exec(
			serverID,
			metric.AgentID,
			metric.Timestamp,
			metric.DeviceName,
			metric.DeviceHost,
			metric.OID,
			metric.MetricName,
			metric.Value,
			metric.ValueType,
			metric.Metadata,
			time.Now(),
		)
		if err != nil {
			return err
		}
	}

	return tx.Commit()
}

// GetAllMetrics retrieves all metrics with optional filters
func (d *Database) GetAllMetrics(agentID string, metricType string, timeRange time.Duration, limit int) ([]models.Metric, error) {
	since := time.Now().Add(-timeRange)

	query := `SELECT id, agent_id, timestamp, metric_type, value, unit, metadata, created_at
			  FROM metrics WHERE 1=1`
	args := []interface{}{}

	if agentID != "" {
		query += " AND agent_id = ?"
		args = append(args, agentID)
	}

	if metricType != "" {
		query += " AND metric_type = ?"
		args = append(args, metricType)
	}

	if timeRange > 0 {
		query += " AND timestamp >= ?"
		args = append(args, since)
	}

	query += " ORDER BY timestamp DESC LIMIT ?"
	args = append(args, limit)

	query = d.preparePlaceholders(query)
	rows, err := d.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var metrics []models.Metric
	for rows.Next() {
		var metric models.Metric
		err := rows.Scan(
			&metric.ID,
			&metric.AgentID,
			&metric.Timestamp,
			&metric.MetricType,
			&metric.Value,
			&metric.Unit,
			&metric.Metadata,
			&metric.CreatedAt,
		)
		if err != nil {
			return nil, err
		}
		metrics = append(metrics, metric)
	}

	return metrics, nil
}

// GetAllAlerts retrieves all alerts with optional filters
func (d *Database) GetAllAlerts(agentID string, severity string, resolved *bool, timeRange time.Duration, limit int) ([]models.Alert, error) {
	since := time.Now().Add(-timeRange)

	query := `SELECT id, agent_id, timestamp, severity, metric_type, value, threshold,
			  message, retry_count, resolved, resolved_at, created_at
			  FROM alerts WHERE 1=1`
	args := []interface{}{}

	if agentID != "" {
		query += " AND agent_id = ?"
		args = append(args, agentID)
	}

	if severity != "" {
		query += " AND severity = ?"
		args = append(args, severity)
	}

	if resolved != nil {
		query += " AND resolved = ?"
		args = append(args, *resolved)
	}

	if timeRange > 0 {
		query += " AND timestamp >= ?"
		args = append(args, since)
	}

	query += " ORDER BY timestamp DESC LIMIT ?"
	args = append(args, limit)

	query = d.preparePlaceholders(query)
	rows, err := d.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var alerts []models.Alert
	for rows.Next() {
		var alert models.Alert
		err := rows.Scan(
			&alert.ID,
			&alert.AgentID,
			&alert.Timestamp,
			&alert.Severity,
			&alert.MetricType,
			&alert.Value,
			&alert.Threshold,
			&alert.Message,
			&alert.RetryCount,
			&alert.Resolved,
			&alert.ResolvedAt,
			&alert.CreatedAt,
		)
		if err != nil {
			return nil, err
		}
		alerts = append(alerts, alert)
	}

	return alerts, nil
}

// GetAllSNMPMetrics retrieves all SNMP metrics with optional filters
func (d *Database) GetAllSNMPMetrics(agentID string, deviceName string, metricName string, timeRange time.Duration, limit int) ([]models.SNMPMetric, error) {
	since := time.Now().Add(-timeRange)

	query := `SELECT id, agent_id, timestamp, device_name, device_host, oid,
			  metric_name, value, value_type, metadata, created_at
			  FROM snmp_metrics WHERE 1=1`
	args := []interface{}{}

	if agentID != "" {
		query += " AND agent_id = ?"
		args = append(args, agentID)
	}

	if deviceName != "" {
		query += " AND device_name = ?"
		args = append(args, deviceName)
	}

	if metricName != "" {
		query += " AND metric_name = ?"
		args = append(args, metricName)
	}

	if timeRange > 0 {
		query += " AND timestamp >= ?"
		args = append(args, since)
	}

	query += " ORDER BY timestamp DESC LIMIT ?"
	args = append(args, limit)

	query = d.preparePlaceholders(query)
	rows, err := d.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var metrics []models.SNMPMetric
	for rows.Next() {
		var metric models.SNMPMetric
		err := rows.Scan(
			&metric.ID,
			&metric.AgentID,
			&metric.Timestamp,
			&metric.DeviceName,
			&metric.DeviceHost,
			&metric.OID,
			&metric.MetricName,
			&metric.Value,
			&metric.ValueType,
			&metric.Metadata,
			&metric.CreatedAt,
		)
		if err != nil {
			return nil, err
		}
		metrics = append(metrics, metric)
	}

	return metrics, nil
}

// GetAgentStatusHistory retrieves agent status history
func (d *Database) GetAgentStatusHistory(agentID string, timeRange time.Duration, limit int) ([]models.AgentStatus, error) {
	since := time.Now().Add(-timeRange)

	query := `SELECT id, agent_id, status, timestamp, reason, created_at
			  FROM agent_status_history WHERE 1=1`
	args := []interface{}{}

	if agentID != "" {
		query += " AND agent_id = ?"
		args = append(args, agentID)
	}

	if timeRange > 0 {
		query += " AND timestamp >= ?"
		args = append(args, since)
	}

	query += " ORDER BY timestamp DESC LIMIT ?"
	args = append(args, limit)

	query = d.preparePlaceholders(query)
	rows, err := d.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var history []models.AgentStatus
	for rows.Next() {
		var status models.AgentStatus
		err := rows.Scan(
			&status.ID,
			&status.AgentID,
			&status.Status,
			&status.Timestamp,
			&status.Reason,
			&status.CreatedAt,
		)
		if err != nil {
			return nil, err
		}
		history = append(history, status)
	}

	return history, nil
}

// CleanupOldData removes data older than retention period
func (d *Database) CleanupOldData(metricsDays, alertsDays, agentStatusDays int) error {
	now := time.Now()

	if metricsDays > 0 {
		metricsDate := now.AddDate(0, 0, -metricsDays)
		query := d.preparePlaceholders(`DELETE FROM metrics WHERE timestamp < ?`)
		_, err := d.db.Exec(query, metricsDate)
		if err != nil {
			return fmt.Errorf("failed to cleanup metrics: %w", err)
		}
	}

	if alertsDays > 0 {
		alertsDate := now.AddDate(0, 0, -alertsDays)
		query := d.preparePlaceholders(`DELETE FROM alerts WHERE timestamp < ? AND resolved = true`)
		_, err := d.db.Exec(query, alertsDate)
		if err != nil {
			return fmt.Errorf("failed to cleanup alerts: %w", err)
		}
	}

	if agentStatusDays > 0 {
		statusDate := now.AddDate(0, 0, -agentStatusDays)
		query := d.preparePlaceholders(`DELETE FROM agent_status_history WHERE timestamp < ?`)
		_, err := d.db.Exec(query, statusDate)
		if err != nil {
			return fmt.Errorf("failed to cleanup agent status: %w", err)
		}
	}

	return nil
}

// GetAgentCount returns the total number of registered agents
func (d *Database) GetAgentCount() (int, error) {
	var count int
	err := d.db.QueryRow(`SELECT COUNT(*) FROM agents WHERE approved = true`).Scan(&count)
	return count, err
}

// GetOnlineAgentCount returns the number of online agents
func (d *Database) GetOnlineAgentCount(timeout time.Duration) (int, error) {
	var count int
	cutoff := time.Now().Add(-timeout)
	query := d.preparePlaceholders(`SELECT COUNT(*) FROM agents WHERE last_seen > ? AND approved = true`)
	err := d.db.QueryRow(query, cutoff).Scan(&count)
	return count, err
}

// RegisterServer registers or updates a server instance
func (d *Database) RegisterServer(serverID, serverName, location, environment, hostname, version string) error {
	now := time.Now()

	// Check if server exists
	var existingID string
	query := d.preparePlaceholders(`SELECT server_id FROM servers WHERE server_id = ?`)
	err := d.db.QueryRow(query, serverID).Scan(&existingID)

	if err == sql.ErrNoRows {
		// Server doesn't exist, insert
		insertQuery := d.preparePlaceholders(`
			INSERT INTO servers (server_id, server_name, location, environment, hostname, version, status, first_seen, last_seen, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
		`)
		_, err = d.db.Exec(insertQuery, serverID, serverName, location, environment, hostname, version, now, now, now, now)
		if err != nil {
			return fmt.Errorf("failed to insert server: %w", err)
		}
	} else if err != nil {
		return fmt.Errorf("failed to query server: %w", err)
	} else {
		// Server exists, update last_seen and other fields
		updateQuery := d.preparePlaceholders(`
			UPDATE servers 
			SET server_name = ?, location = ?, environment = ?, hostname = ?, version = ?, 
			    last_seen = ?, updated_at = ?, status = 'active'
			WHERE server_id = ?
		`)
		_, err = d.db.Exec(updateQuery, serverName, location, environment, hostname, version, now, now, serverID)
		if err != nil {
			return fmt.Errorf("failed to update server: %w", err)
		}
	}

	return nil
}

// GetServer retrieves server information by server_id
func (d *Database) GetServer(serverID string) (map[string]interface{}, error) {
	query := d.preparePlaceholders(`
		SELECT server_id, server_name, location, environment, hostname, version, status, 
		       last_seen, first_seen, created_at, updated_at
		FROM servers 
		WHERE server_id = ?
	`)

	var serverName, location, environment, hostname, version, status string
	var lastSeen, firstSeen, createdAt, updatedAt time.Time

	err := d.db.QueryRow(query, serverID).Scan(
		&serverID, &serverName, &location, &environment, &hostname, &version, &status,
		&lastSeen, &firstSeen, &createdAt, &updatedAt,
	)

	if err != nil {
		return nil, err
	}

	return map[string]interface{}{
		"server_id":   serverID,
		"server_name": serverName,
		"location":    location,
		"environment": environment,
		"hostname":    hostname,
		"version":     version,
		"status":      status,
		"last_seen":   lastSeen,
		"first_seen":  firstSeen,
		"created_at":  createdAt,
		"updated_at":  updatedAt,
	}, nil
}

// UpdateServerLastSeen updates the last_seen timestamp for a server
func (d *Database) UpdateServerLastSeen(serverID string) error {
	query := d.preparePlaceholders(`UPDATE servers SET last_seen = ?, updated_at = ? WHERE server_id = ?`)
	_, err := d.db.Exec(query, time.Now(), time.Now(), serverID)
	return err
}

// ResolveAlert marks an alert as resolved with resolution details
func (d *Database) ResolveAlert(alertID int64, resolvedBy, resolutionAction, resolutionNotes string) error {
	query := d.preparePlaceholders(`
		UPDATE alerts
		SET resolved = ?,
		    resolved_at = ?,
		    resolved_by = ?,
		    resolution_action = ?,
		    resolution_notes = ?,
		    updated_at = ?
		WHERE id = ?
	`)

	now := time.Now()
	_, err := d.db.Exec(query, true, now, resolvedBy, resolutionAction, resolutionNotes, now, alertID)
	return err
}

// GetAlertByID retrieves a single alert by ID
func (d *Database) GetAlertByID(alertID int64) (map[string]interface{}, error) {
	query := d.preparePlaceholders(`
		SELECT id, server_id, agent_id, timestamp, severity, metric_type, value, threshold,
		       message, occurrence_count, first_seen, last_seen, resolved, resolved_at,
		       resolved_by, resolution_action, resolution_notes, created_at, updated_at
		FROM alerts
		WHERE id = ?
	`)

	var (
		id                                   int64
		serverID, agentID, severity          string
		metricType, message                  string
		value, threshold                     float64
		occurrenceCount, resolved            int
		timestamp, firstSeen, lastSeen       time.Time
		createdAt, updatedAt                 time.Time
		resolvedAt                           sql.NullTime
		resolvedBy, resolutionAction         sql.NullString
		resolutionNotes                      sql.NullString
	)

	err := d.db.QueryRow(query, alertID).Scan(
		&id, &serverID, &agentID, &timestamp, &severity, &metricType, &value, &threshold,
		&message, &occurrenceCount, &firstSeen, &lastSeen, &resolved, &resolvedAt,
		&resolvedBy, &resolutionAction, &resolutionNotes, &createdAt, &updatedAt,
	)

	if err != nil {
		return nil, err
	}

	alert := map[string]interface{}{
		"id":                id,
		"server_id":         serverID,
		"agent_id":          agentID,
		"timestamp":         timestamp,
		"severity":          severity,
		"metric_type":       metricType,
		"value":             value,
		"threshold":         threshold,
		"message":           message,
		"occurrence_count":  occurrenceCount,
		"first_seen":        firstSeen,
		"last_seen":         lastSeen,
		"resolved":          resolved == 1,
		"created_at":        createdAt,
		"updated_at":        updatedAt,
	}

	if resolvedAt.Valid {
		alert["resolved_at"] = resolvedAt.Time
	}
	if resolvedBy.Valid {
		alert["resolved_by"] = resolvedBy.String
	}
	if resolutionAction.Valid {
		alert["resolution_action"] = resolutionAction.String
	}
	if resolutionNotes.Valid {
		alert["resolution_notes"] = resolutionNotes.String
	}

	return alert, nil
}

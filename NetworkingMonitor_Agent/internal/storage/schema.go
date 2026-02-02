package storage

const schemaSQL = `
-- Datacenter metadata
CREATE TABLE IF NOT EXISTS datacenter_info (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	agent_id TEXT UNIQUE,

	-- Physical location
	location TEXT,
	datacenter TEXT,
	building TEXT,
	floor TEXT,
	room TEXT,
	row TEXT,
	rack TEXT,
	position TEXT,
	side TEXT,

	-- Asset information
	asset_tag TEXT,
	serial_number TEXT,
	barcode TEXT,
	owner_tag TEXT,

	-- Organizational
	owner TEXT,
	department TEXT,
	cost_center TEXT,
	project TEXT,
	service_tag TEXT,

	-- Environment
	environment TEXT,
	purpose TEXT,
	criticality TEXT,
	tier TEXT,

	-- Networking
	network_zone TEXT,
	vlans TEXT,
	subnet TEXT,

	-- Dates
	purchase_date INTEGER,
	warranty_expiry INTEGER,
	install_date INTEGER,

	-- Additional
	notes TEXT,
	tags TEXT,

	-- Contact
	primary_contact TEXT,
	contact_email TEXT,
	contact_phone TEXT,

	created_at INTEGER,
	updated_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_datacenter_agent ON datacenter_info(agent_id);
CREATE INDEX IF NOT EXISTS idx_datacenter_location ON datacenter_info(location);
CREATE INDEX IF NOT EXISTS idx_datacenter_rack ON datacenter_info(rack);
CREATE INDEX IF NOT EXISTS idx_datacenter_environment ON datacenter_info(environment);

-- Metrics table stores all collected system metrics
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    metric_type TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    metadata TEXT,
    sent INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_sent ON metrics(sent);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type);

-- Alerts table stores generated alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    severity TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    value REAL NOT NULL,
    threshold REAL NOT NULL,
    message TEXT NOT NULL,
    sent INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    sent_at INTEGER,
    retry_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_sent ON alerts(sent);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);

-- System info table stores host metadata
CREATE TABLE IF NOT EXISTS system_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    hostname TEXT NOT NULL,
    os TEXT NOT NULL,
    platform TEXT NOT NULL,
    architecture TEXT NOT NULL,
    cpu_model TEXT,
    cpu_cores INTEGER,
    total_memory INTEGER,
    updated_at INTEGER NOT NULL
);

-- Transmission log for debugging
CREATE TABLE IF NOT EXISTS transmission_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    record_type TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transmission_timestamp ON transmission_log(timestamp);

-- SNMP metrics table stores metrics collected from SNMP devices
CREATE TABLE IF NOT EXISTS snmp_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    device_host TEXT NOT NULL,
    oid TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    value_type TEXT,
    metadata TEXT,
    sent INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_snmp_metrics_timestamp ON snmp_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_device ON snmp_metrics(device_name);
CREATE INDEX IF NOT EXISTS idx_snmp_metrics_sent ON snmp_metrics(sent);

-- SNMP devices table tracks device polling status
CREATE TABLE IF NOT EXISTS snmp_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL UNIQUE,
    device_host TEXT NOT NULL,
    community TEXT NOT NULL,
    version TEXT NOT NULL,
    last_poll_success INTEGER,
    last_poll_time INTEGER,
    total_polls INTEGER DEFAULT 0,
    failed_polls INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
`

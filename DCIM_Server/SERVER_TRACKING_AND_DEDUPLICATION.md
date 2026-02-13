# Server Tracking and Data Deduplication Implementation

**Date:** 2026-02-12
**Status:** 🚧 In Progress - Schema Complete, Code Integration Pending

---

## Overview

This document describes the implementation of three critical features:
1. **Server Identification Tracking** - Track which DCIM_Server instance created database records
2. **Alert Deduplication** - Prevent duplicate alerts, use occurrence count instead
3. **Metrics Deduplication** - Control repeated metrics data

---

## Problem Statement

### Issue 1: No Server Tracking
**Current Situation:**
- When DCIM_Server inserts metrics, alerts, or other data, there's no record of which server instance created it
- In multi-server deployments, impossible to track data source
- Cannot identify which server is responsible for specific records

**Solution:**
- Add `servers` table to track all DCIM_Server instances
- Add `server_id` column to all data tables (metrics, alerts, snmp_metrics, etc.)
- Auto-generate unique server ID if not configured
- Track server metadata (name, location, environment)

### Issue 2: Duplicate Alerts
**Current Situation:**
- Same alert from same agent inserted multiple times
- Database fills with duplicate alert records
- Cannot see how long an alert has persisted
- No count of how many times alert occurred

**Solution:**
- Check if same alert exists (same agent_id, metric_type, severity, not resolved)
- If exists: increment `occurrence_count` and update `last_seen`, `updated_at`
- If new: insert with `occurrence_count=1`, `first_seen=now`, `last_seen=now`
- Track duration: `first_seen` to `last_seen`

### Issue 3: Repeated Metrics
**Current Situation:**
- Same metric value inserted repeatedly
- Storage inefficiency for unchanged values
- Database growth for redundant data

**Solution:** (Optional - can be implemented later)
- Compare new metric value with last stored value
- Only insert if value changed beyond threshold
- Or use time-based deduplication window

---

## Database Schema Changes

### ✅ 1. New Table: `servers`

```sql
CREATE TABLE servers (
    id                 SERIAL PRIMARY KEY,
    server_id          TEXT UNIQUE NOT NULL,      -- UUID or hostname-based ID
    server_name        TEXT NOT NULL,              -- Human-readable name
    location           TEXT,                       -- Physical location
    environment        TEXT,                       -- dev/staging/production
    hostname           TEXT,                       -- Server hostname
    version            TEXT,                       -- DCIM_Server version
    status             TEXT DEFAULT 'active',      -- active/inactive
    last_seen          TIMESTAMP,                  -- Last heartbeat
    first_seen         TIMESTAMP,                  -- First registration
    metadata           TEXT,                       -- JSON metadata
    created_at         TIMESTAMP DEFAULT NOW(),
    updated_at         TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_servers_id ON servers(server_id);
CREATE INDEX idx_servers_status ON servers(status, last_seen);
```

### ✅ 2. Updated Table: `metrics`

**Added Column:**
- `server_id TEXT NOT NULL` - References which server created this metric

**New Index:**
- `idx_metrics_server ON metrics(server_id, timestamp)`

### ✅ 3. Updated Table: `alerts`

**Added Columns:**
- `server_id TEXT NOT NULL` - References which server created this alert
- `occurrence_count INTEGER DEFAULT 1` - How many times alert occurred
- `first_seen TIMESTAMP NOT NULL` - When alert first appeared
- `last_seen TIMESTAMP NOT NULL` - When alert last occurred
- `updated_at TIMESTAMP DEFAULT NOW()` - Last update timestamp

**New Index:**
- `idx_alerts_server ON alerts(server_id, timestamp)`
- `idx_alerts_dedup ON alerts(agent_id, metric_type, severity, resolved)` - For deduplication lookup

### ✅ 4. Updated Tables: `snmp_metrics`, `agent_status_history`, `aggregated_metrics`

**Added Column to Each:**
- `server_id TEXT NOT NULL`

**New Indexes:**
- `idx_snmp_server ON snmp_metrics(server_id, timestamp)`
- `idx_agent_status_server ON agent_status_history(server_id, timestamp)`
- `idx_agg_metrics_server ON aggregated_metrics(server_id, timestamp)`

---

## Configuration Changes

### ✅ Added to `config.yaml`

```yaml
# Server Identification - For Multi-Server Deployments
server_id:
  # Unique server identifier (leave empty for auto-generation)
  # Auto-generated ID will be saved to ./data/server_id.txt
  id: ""

  # Human-readable server name
  name: "DCIM-Server-Primary"

  # Physical location (datacenter, region, etc.)
  location: "Primary-DC"

  # Environment (dev, staging, production)
  environment: "production"

  # Auto-generate server ID if not specified
  auto_generate: true
```

### ✅ Added to `internal/config/config.go`

```go
type ServerIDConfig struct {
    ID          string `yaml:"id"`
    Name        string `yaml:"name"`
    Location    string `yaml:"location"`
    Environment string `yaml:"environment"`
    AutoGenerate bool  `yaml:"auto_generate"`
}
```

---

## Code Changes Required

### ✅ Completed

1. ✅ Database schema updated (SQLite, PostgreSQL)
2. ✅ Configuration structs added
3. ✅ Server management functions added to `database.go`:
   - `RegisterServer()` - Register/update server instance
   - `GetServer()` - Retrieve server info
   - `UpdateServerLastSeen()` - Update heartbeat

### 🚧 Pending Implementation

#### 1. Server ID Generation and Registration

**File:** `DCIM_Server/main.go` or `internal/server/server.go`

**Required Changes:**
```go
func initializeServer(cfg *config.Config) (string, error) {
    var serverID string

    // Check if server ID is configured
    if cfg.ServerID.ID != "" {
        serverID = cfg.ServerID.ID
    } else if cfg.ServerID.AutoGenerate {
        // Try to load from file
        serverIDFile := "./data/server_id.txt"
        data, err := os.ReadFile(serverIDFile)
        if err == nil {
            serverID = strings.TrimSpace(string(data))
        } else {
            // Generate new server ID
            hostname, _ := os.Hostname()
            serverID = fmt.Sprintf("%s-%s", hostname, uuid.New().String()[:8])

            // Save to file
            os.WriteFile(serverIDFile, []byte(serverID), 0644)
        }
    } else {
        return "", fmt.Errorf("server_id not configured and auto_generate is false")
    }

    // Register server in database
    hostname, _ := os.Hostname()
    version := "2.0.0" // Get from build

    err := db.RegisterServer(
        serverID,
        cfg.ServerID.Name,
        cfg.ServerID.Location,
        cfg.ServerID.Environment,
        hostname,
        version,
    )

    return serverID, err
}
```

#### 2. Update Server Struct to Include server_id

**File:** `DCIM_Server/internal/server/server.go`

**Required Changes:**
```go
type Server struct {
    serverID   string              // ADD THIS
    config     *config.Config
    db         *database.Database
    // ... rest of fields
}

func New(cfg *config.Config) (*Server, error) {
    // ... existing code ...

    // Initialize server ID
    serverID, err := initializeServer(cfg, db)
    if err != nil {
        return nil, fmt.Errorf("failed to initialize server ID: %w", err)
    }

    server := &Server{
        serverID: serverID,  // ADD THIS
        config:   cfg,
        db:       db,
        // ... rest of initialization
    }

    // Start heartbeat to update last_seen
    go server.serverHeartbeat()

    return server, nil
}

func (s *Server) serverHeartbeat() {
    ticker := time.NewTicker(30 * time.Second)
    defer ticker.Stop()

    for range ticker.C {
        s.db.UpdateServerLastSeen(s.serverID)
    }
}
```

#### 3. Update InsertMetrics to Include server_id

**File:** `DCIM_Server/internal/database/database.go`

**Find the function:**
```go
func (d *Database) InsertMetrics(metrics []models.Metric) error
```

**Change to:**
```go
func (d *Database) InsertMetrics(serverID string, metrics []models.Metric) error
```

**Update the INSERT query:**
```go
query := d.preparePlaceholders(`
    INSERT INTO metrics (server_id, agent_id, timestamp, metric_type, value, unit, metadata, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
`)

// In the loop:
_, err = tx.Exec(query,
    serverID,           // ADD THIS
    metric.AgentID,
    metric.Timestamp,
    metric.MetricType,
    metric.Value,
    metric.Unit,
    metadata,
    metric.CreatedAt,
)
```

**Update all calls to InsertMetrics:**
```bash
# Find all calls:
grep -r "InsertMetrics" DCIM_Server/internal/server/

# Update each call from:
s.db.InsertMetrics(metrics)

# To:
s.db.InsertMetrics(s.serverID, metrics)
```

#### 4. Update InsertAlerts with Deduplication Logic

**File:** `DCIM_Server/internal/database/database.go`

**Find the function:**
```go
func (d *Database) InsertAlerts(alerts []models.Alert) error
```

**Replace with:**
```go
func (d *Database) InsertAlerts(serverID string, alerts []models.Alert) error {
    if len(alerts) == 0 {
        return nil
    }

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
              AND resolved = false
            LIMIT 1
        `)

        err := d.db.QueryRow(checkQuery, alert.AgentID, alert.MetricType, alert.Severity).
            Scan(&existingID, &existingCount, &firstSeen)

        if err == sql.ErrNoRows {
            // New alert - insert
            insertQuery := d.preparePlaceholders(`
                INSERT INTO alerts (
                    server_id, agent_id, timestamp, severity, metric_type,
                    value, threshold, message, occurrence_count,
                    first_seen, last_seen, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
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
                now,              // created_at
                now,              // updated_at
            )

            if err != nil {
                return fmt.Errorf("failed to insert alert: %w", err)
            }
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

    return nil
}
```

**Update all calls to InsertAlerts:**
```go
// From:
s.db.InsertAlerts(alerts)

// To:
s.db.InsertAlerts(s.serverID, alerts)
```

#### 5. Update Other Database Functions

**Similarly update:**
- `InsertSNMPMetrics()` - add serverID parameter
- `InsertAgentStatusHistory()` - add serverID parameter
- `InsertAggregatedMetrics()` - add serverID parameter

---

## Migration Strategy

### Option 1: Fresh Database (Recommended for Development)
1. Backup existing database
2. Drop and recreate schema (new schema will be created on startup)
3. Data starts fresh with server tracking

### Option 2: Migrate Existing Data
1. Add columns to existing tables (ALTER TABLE)
2. Backfill server_id with default value
3. Run migration script to populate servers table

**Migration Script Example:**
```sql
-- Add server_id column to existing tables (PostgreSQL)
ALTER TABLE metrics ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS server_id TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrence_count INTEGER DEFAULT 1;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS first_seen TIMESTAMP;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Backfill with default server ID
UPDATE metrics SET server_id = 'legacy-server' WHERE server_id IS NULL;
UPDATE alerts SET server_id = 'legacy-server' WHERE server_id IS NULL;

-- Make columns NOT NULL
ALTER TABLE metrics ALTER COLUMN server_id SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN server_id SET NOT NULL;

-- Backfill alert deduplication fields
UPDATE alerts
SET first_seen = timestamp,
    last_seen = timestamp,
    updated_at = created_at
WHERE first_seen IS NULL;

-- Create indexes
CREATE INDEX idx_alerts_dedup ON alerts(agent_id, metric_type, severity, resolved);
```

---

## Testing Plan

### 1. Test Server Registration
```bash
# Start server - should auto-generate server ID
./dcim-server

# Check server registered in database
psql -d dcim -c "SELECT * FROM servers;"

# Check server_id saved to file
cat data/server_id.txt
```

### 2. Test Metrics with Server Tracking
```bash
# Send metrics from agent
# Check database
psql -d dcim -c "SELECT server_id, agent_id, metric_type, value FROM metrics LIMIT 10;"

# Should see server_id populated
```

### 3. Test Alert Deduplication
```bash
# Send same alert multiple times
python test_condenser_failure.py  # Run 3 times

# Check database
psql -d dcim -c "SELECT agent_id, metric_type, severity, occurrence_count, first_seen, last_seen FROM alerts WHERE resolved = false;"

# Should see:
# - Only 1 alert record
# - occurrence_count = 3
# - first_seen = first occurrence time
# - last_seen = most recent occurrence time
```

### 4. Test Multi-Server Deployment
```bash
# Start Server 1 with config:
server_id:
  id: "server-1"
  name: "Primary-Server"

# Start Server 2 with config:
server_id:
  id: "server-2"
  name: "Secondary-Server"

# Send data to both
# Query database
psql -d dcim -c "SELECT server_id, COUNT(*) FROM metrics GROUP BY server_id;"

# Should see data from both servers
```

---

## Benefits

### Server Tracking
- ✅ Know which server instance created each record
- ✅ Track server health and uptime
- ✅ Support multi-server deployments
- ✅ Audit trail for data origin
- ✅ Identify problematic servers

### Alert Deduplication
- ✅ Reduce database size (fewer duplicate rows)
- ✅ See how long alert has persisted (first_seen → last_seen)
- ✅ Track alert frequency (occurrence_count)
- ✅ Better alert management
- ✅ Improved dashboard performance

### Metrics Deduplication (Future)
- ✅ Reduce storage for unchanged values
- ✅ Optimize database growth
- ✅ Maintain data accuracy

---

## Performance Impact

### Storage Savings (Alert Deduplication)
- **Before:** 100 identical alerts = 100 rows
- **After:** 100 identical alerts = 1 row (with count=100)
- **Savings:** ~99% reduction for repeated alerts

### Query Performance
- New indexes on server_id improve filtering
- Dedup index (idx_alerts_dedup) optimized for lookup
- Minimal overhead (~5-10ms per alert check)

---

## Next Steps

1. ✅ Database schema updated
2. ✅ Configuration added
3. ✅ Server management functions added
4. 🚧 Implement server ID initialization in main.go
5. 🚧 Update Server struct with serverID field
6. 🚧 Update InsertMetrics calls
7. 🚧 Update InsertAlerts with deduplication
8. 🚧 Update other Insert functions
9. 🚧 Test with both payloads
10. 🚧 Document in API changelog

---

## Files Modified

- ✅ `DCIM_Server/config.yaml` - Added server_id section
- ✅ `DCIM_Server/internal/config/config.go` - Added ServerIDConfig struct
- ✅ `DCIM_Server/internal/database/database.go` - Updated schema + added functions
- 🚧 `DCIM_Server/main.go` or `internal/server/server.go` - Server initialization
- 🚧 `DCIM_Server/internal/server/*.go` - Update all InsertX calls

---

**Status:** Ready for code integration and testing
**Complexity:** Medium-High (requires careful testing)
**Risk:** Low (backward compatible with migration strategy)

# DCIM Aggregator Testing Guide

Complete guide to test the multi-server DCIM architecture.

## Phase 1: Infrastructure Setup Tests

### Test 1.1: Docker Services

```bash
# Start services
cd E:\Projects\DCIM\DCIM_Aggregator
docker-compose up -d

# Check all services are running
docker-compose ps

# Expected output:
# NAME              STATUS              PORTS
# dcim-postgres     Up (healthy)        0.0.0.0:5432->5432/tcp
# dcim-redis        Up (healthy)        0.0.0.0:6379->6379/tcp
# dcim-aggregator   Up                  0.0.0.0:3002->3002/tcp
```

**Expected Result:** ✅ All services show "Up" or "Up (healthy)"

### Test 1.2: PostgreSQL Connection

```bash
# Test PostgreSQL connection
docker exec dcim-postgres pg_isready -U dcim

# Connect to database
docker exec -it dcim-postgres psql -U dcim -d dcim_aggregator

# Inside psql, run:
\dt

# Expected output: List of tables
# servers, agents, metrics, alerts, snmp_metrics, user_preferences
```

**Expected Result:** ✅ Connection successful, all tables exist

### Test 1.3: TimescaleDB Extension

```sql
-- Inside psql
SELECT * FROM timescaledb_information.hypertables;

-- Expected output: Should show 'metrics' and 'snmp_metrics' hypertables
```

**Expected Result:** ✅ Two hypertables created (metrics, snmp_metrics)

### Test 1.4: Redis Connection

```bash
# Test Redis connection
docker exec dcim-redis redis-cli ping

# Expected output: PONG
```

**Expected Result:** ✅ Redis responds with PONG

### Test 1.5: Aggregator Service Health

```bash
curl http://localhost:3002/health

# Expected output:
# {
#   "status": "healthy",
#   "service": "DCIM Aggregator",
#   "timestamp": "..."
# }
```

**Expected Result:** ✅ Service returns healthy status

## Phase 2: Server Management Tests

### Test 2.1: Add First Server

```bash
curl -X POST http://localhost:3002/api/v1/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test-Server-1",
    "url": "http://localhost:8080/api/v1",
    "enabled": true,
    "metadata": {
      "location": "Local",
      "environment": "test",
      "color": "#3b82f6"
    }
  }'

# Expected output:
# {
#   "success": true,
#   "data": {
#     "id": "...",
#     "name": "Test-Server-1",
#     "url": "http://localhost:8080/api/v1",
#     "enabled": true,
#     ...
#   }
# }
```

**Expected Result:** ✅ Server created, returns server object with ID

### Test 2.2: List All Servers

```bash
curl http://localhost:3002/api/v1/servers

# Expected output:
# {
#   "success": true,
#   "data": [
#     {
#       "id": "...",
#       "name": "Test-Server-1",
#       "enabled": true,
#       "health": { ... }
#     }
#   ]
# }
```

**Expected Result:** ✅ Returns array with the server we just added

### Test 2.3: Test Server Connection

```bash
# Replace <server-id> with actual ID from previous test
curl http://localhost:3002/api/v1/servers/<server-id>/health

# If DCIM_Server is running:
# {
#   "success": true,
#   "data": {
#     "status": "healthy",
#     "responseTime": 123
#   }
# }

# If DCIM_Server is not running:
# {
#   "success": true,
#   "data": {
#     "status": "offline",
#     "error": "..."
#   }
# }
```

**Expected Result:** ✅ Returns health status (healthy or offline based on DCIM_Server availability)

### Test 2.4: Update Server

```bash
curl -X PUT http://localhost:3002/api/v1/servers/<server-id> \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test-Server-Updated",
    "metadata": {
      "location": "Updated Location"
    }
  }'

# Expected output:
# {
#   "success": true,
#   "data": {
#     "id": "...",
#     "name": "Test-Server-Updated",
#     ...
#   }
# }
```

**Expected Result:** ✅ Server updated successfully

### Test 2.5: Toggle Server Status

```bash
# Disable server
curl -X POST http://localhost:3002/api/v1/servers/<server-id>/toggle \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Expected output:
# {
#   "success": true,
#   "message": "Server disabled"
# }

# Verify server is disabled
curl http://localhost:3002/api/v1/servers/<server-id>
```

**Expected Result:** ✅ Server enabled status toggled

## Phase 3: Data Sync Tests

**Prerequisites:** Start a DCIM_Server instance on port 8080 with some test agents

### Test 3.1: Wait for Agent Sync

```bash
# Wait 30 seconds for agent sync worker to run
echo "Waiting for agent sync..."
timeout /t 30 /nobreak

# Check if agents were synced
curl http://localhost:3002/api/v1/agents

# Expected output:
# {
#   "success": true,
#   "data": [ ... array of agents ... ],
#   "count": 5
# }
```

**Expected Result:** ✅ Agents from DCIM_Server are synced to aggregator database

### Test 3.2: Verify Agent Data in Database

```sql
-- Connect to PostgreSQL
docker exec -it dcim-postgres psql -U dcim -d dcim_aggregator

-- Check agents table
SELECT
  server_id,
  agent_id,
  hostname,
  status,
  last_seen
FROM agents
ORDER BY created_at DESC
LIMIT 10;

-- Expected: Should see agents with server_id matching your added server
```

**Expected Result:** ✅ Agents stored in database with correct server_id

### Test 3.3: Wait for Metrics Sync

```bash
# Wait 10 seconds for metrics sync worker to run
echo "Waiting for metrics sync..."
timeout /t 10 /nobreak

# Check if metrics were synced
curl "http://localhost:3002/api/v1/metrics?limit=10"

# Expected output:
# {
#   "success": true,
#   "data": [ ... array of metrics ... ],
#   "count": 10
# }
```

**Expected Result:** ✅ Metrics from DCIM_Server are synced to aggregator

### Test 3.4: Verify Metrics in TimescaleDB

```sql
-- Check metrics count
SELECT COUNT(*) FROM metrics WHERE timestamp >= NOW() - INTERVAL '1 hour';

-- Check metrics by server
SELECT
  server_id,
  agent_id,
  metric_type,
  value,
  timestamp
FROM metrics
ORDER BY timestamp DESC
LIMIT 20;

-- Check hypertable chunks
SELECT * FROM timescaledb_information.chunks WHERE hypertable_name = 'metrics';
```

**Expected Result:** ✅ Metrics stored in hypertable with correct partitioning

### Test 3.5: Test Metrics Aggregation

```bash
# Get aggregated hourly metrics
curl "http://localhost:3002/api/v1/metrics/aggregated?interval=1%20hour&time_range=24%20hours"

# Expected output:
# {
#   "success": true,
#   "data": [
#     {
#       "bucket": "2026-02-10 12:00:00+00",
#       "server_id": "...",
#       "agent_id": "...",
#       "metric_type": "cpu_usage",
#       "avg_value": 45.5,
#       "max_value": 89.2,
#       "min_value": 12.3,
#       "sample_count": 360
#     }
#   ]
# }
```

**Expected Result:** ✅ Returns aggregated metrics with time buckets

### Test 3.6: Test Alerts Sync

```bash
# Check if alerts were synced
curl http://localhost:3002/api/v1/alerts

# Expected output:
# {
#   "success": true,
#   "data": [ ... array of alerts ... ]
# }

# Get alert stats
curl http://localhost:3002/api/v1/alerts/stats

# Expected output:
# {
#   "success": true,
#   "data": {
#     "total_alerts": 25,
#     "active_alerts": 5,
#     "critical_alerts": 2,
#     "warning_alerts": 3
#   }
# }
```

**Expected Result:** ✅ Alerts synced and statistics calculated correctly

## Phase 4: Caching Tests

### Test 4.1: Verify Redis Cache

```bash
# Connect to Redis
docker exec -it dcim-redis redis-cli

# Check server health cache
KEYS server:health:*

# Get server health (replace with actual key)
GET server:health:<server-id>

# Expected output: JSON with health status
```

**Expected Result:** ✅ Server health data cached in Redis

### Test 4.2: Test Cache Performance

```bash
# First request (cold cache)
time curl "http://localhost:3002/api/v1/metrics?agent_id=test-agent&time_range=1h"

# Second request (warm cache, should be faster)
time curl "http://localhost:3002/api/v1/metrics?agent_id=test-agent&time_range=1h"
```

**Expected Result:** ✅ Second request completes faster due to caching

### Test 4.3: Test Cache Expiration

```bash
# Make request
curl "http://localhost:3002/api/v1/metrics?time_range=1h"

# Wait 15 seconds (cache TTL is 10s)
timeout /t 15 /nobreak

# Make same request again (should hit database, not cache)
curl "http://localhost:3002/api/v1/metrics?time_range=1h"
```

**Expected Result:** ✅ Cache expires after TTL, fresh data fetched

## Phase 5: Worker Tests

### Test 5.1: Health Monitor Worker

```bash
# Check aggregator logs
docker-compose logs -f aggregator | findstr "Health monitor"

# Expected output (every 30 seconds):
# Server Test-Server-1 is healthy (123ms)
# OR
# Server Test-Server-1 is offline
```

**Expected Result:** ✅ Health monitor runs every 30 seconds

### Test 5.2: Metrics Sync Worker

```bash
# Check aggregator logs
docker-compose logs -f aggregator | findstr "Metrics sync"

# Expected output (every 10 seconds):
# Synced 1000 metrics from server <id>
```

**Expected Result:** ✅ Metrics sync runs every 10 seconds

### Test 5.3: Agents Sync Worker

```bash
# Check aggregator logs
docker-compose logs -f aggregator | findstr "Agents sync"

# Expected output (every 30 seconds):
# Synced 5 agents from server <id>
```

**Expected Result:** ✅ Agents sync runs every 30 seconds

## Phase 6: Frontend Integration Tests

### Test 6.1: Server Management UI

```bash
# Start frontend
cd E:\Projects\DCIM\DCIM_UI
npm run dev
```

**Manual Steps:**
1. Open browser: `http://localhost:3000`
2. Navigate to **Servers** page
3. Click **Add Server**
4. Fill in form and submit
5. Verify server appears in list with color tag
6. Click **Test** button - should show healthy/offline status
7. Toggle **Enabled** switch - should disable server
8. Click **Edit** - should open form with existing data
9. Update server and save
10. Click **Delete** - should remove server (with confirmation)

**Expected Result:** ✅ All CRUD operations work correctly

### Test 6.2: Dashboard with Aggregated Data

**Manual Steps:**
1. Add 2-3 DCIM servers via Server Management
2. Wait 30 seconds for data sync
3. Navigate to **Dashboard**
4. Verify stats show aggregated counts from all servers
5. Check that agent list shows agents from multiple servers
6. Verify server name is displayed with each agent

**Expected Result:** ✅ Dashboard shows aggregated data from all servers

### Test 6.3: Agent Filtering by Server

**Manual Steps:**
1. Navigate to **Agents** page
2. Look for agents with different server names
3. Filter by server (if filter exists)
4. Verify agent details show source server

**Expected Result:** ✅ Can identify which server each agent belongs to

## Phase 7: Multi-Server Tests

**Prerequisites:** Set up 3 DCIM_Server instances on different ports

### Test 7.1: Add Multiple Servers

```bash
# Add Server 1
curl -X POST http://localhost:3002/api/v1/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DC-East",
    "url": "http://localhost:8080/api/v1",
    "enabled": true,
    "metadata": {"location": "New York", "color": "#3b82f6"}
  }'

# Add Server 2
curl -X POST http://localhost:3002/api/v1/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DC-West",
    "url": "http://localhost:8081/api/v1",
    "enabled": true,
    "metadata": {"location": "California", "color": "#10b981"}
  }'

# Add Server 3
curl -X POST http://localhost:3002/api/v1/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DC-Europe",
    "url": "http://localhost:8082/api/v1",
    "enabled": true,
    "metadata": {"location": "London", "color": "#f59e0b"}
  }'

# List all servers
curl http://localhost:3002/api/v1/servers
```

**Expected Result:** ✅ Three servers added successfully

### Test 7.2: Verify Cross-Server Data Aggregation

```bash
# Wait for sync (30 seconds)
timeout /t 30 /nobreak

# Get aggregated agents from all servers
curl http://localhost:3002/api/v1/agents

# Count agents by server
curl http://localhost:3002/api/v1/agents | jq '.data | group_by(.server_name) | map({server: .[0].server_name, count: length})'

# Expected output:
# [
#   {"server": "DC-East", "count": 10},
#   {"server": "DC-West", "count": 15},
#   {"server": "DC-Europe", "count": 8}
# ]
```

**Expected Result:** ✅ Agents from all servers are aggregated

### Test 7.3: Test Dashboard Stats

```bash
curl http://localhost:3002/api/v1/dashboard/stats

# Expected output:
# {
#   "success": true,
#   "data": {
#     "servers": 3,
#     "agents": {
#       "total": 33,
#       "online": 30,
#       "offline": 3
#     },
#     "activeAlerts": 7
#   }
# }
```

**Expected Result:** ✅ Dashboard stats reflect data from all 3 servers

### Test 7.4: Disable One Server

```bash
# Disable DC-West
curl -X POST http://localhost:3002/api/v1/servers/<dc-west-id>/toggle \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Wait for sync cycle (30 seconds)
timeout /t 30 /nobreak

# Check agent count (should only include DC-East and DC-Europe now)
curl http://localhost:3002/api/v1/agents | jq '.count'

# Expected: Count should be reduced (only agents from enabled servers)
```

**Expected Result:** ✅ Disabled server's agents are not synced

## Phase 8: Performance Tests

### Test 8.1: Large Dataset Test

```bash
# Add server with 100+ agents
# Wait for initial sync

# Measure query performance
time curl "http://localhost:3002/api/v1/agents"

# Expected: Response time < 500ms for 100 agents
```

**Expected Result:** ✅ Query performs well with large dataset

### Test 8.2: TimescaleDB Compression Test

```sql
-- Wait 7+ days or manually trigger compression
SELECT compress_chunk(i) FROM show_chunks('metrics') i;

-- Check compression stats
SELECT
  hypertable_name,
  before_compression_total_bytes,
  after_compression_total_bytes,
  pg_size_pretty(before_compression_total_bytes - after_compression_total_bytes) AS saved
FROM timescaledb_information.compression_settings;
```

**Expected Result:** ✅ Compression reduces data size significantly (4x-20x)

### Test 8.3: Continuous Aggregate Performance

```bash
# Query using continuous aggregate (should be fast)
time curl "http://localhost:3002/api/v1/metrics/aggregated?interval=1%20hour&time_range=30%20days"

# Expected: Response time < 1s even for 30 days of data
```

**Expected Result:** ✅ Continuous aggregates provide fast query performance

## Phase 9: Failure Recovery Tests

### Test 9.1: PostgreSQL Restart

```bash
# Restart PostgreSQL
docker-compose restart postgres

# Wait for restart
timeout /t 10 /nobreak

# Check if aggregator reconnects
curl http://localhost:3002/health

# Check logs
docker-compose logs aggregator | findstr "PostgreSQL"
```

**Expected Result:** ✅ Aggregator automatically reconnects to PostgreSQL

### Test 9.2: Redis Restart

```bash
# Restart Redis
docker-compose restart redis

# Wait for restart
timeout /t 5 /nobreak

# Check if aggregator reconnects
curl http://localhost:3002/health

# Make cached request
curl "http://localhost:3002/api/v1/metrics?time_range=1h"
```

**Expected Result:** ✅ Aggregator reconnects, cache rebuilds

### Test 9.3: Aggregator Restart

```bash
# Restart aggregator
docker-compose restart aggregator

# Wait for startup
timeout /t 10 /nobreak

# Verify workers restart
docker-compose logs aggregator | findstr "worker started"

# Verify API is accessible
curl http://localhost:3002/health
```

**Expected Result:** ✅ All workers restart automatically

## Test Results Summary

| Phase | Test | Status | Notes |
|-------|------|--------|-------|
| 1 | Infrastructure Setup | ⬜ | |
| 2 | Server Management | ⬜ | |
| 3 | Data Sync | ⬜ | |
| 4 | Caching | ⬜ | |
| 5 | Workers | ⬜ | |
| 6 | Frontend Integration | ⬜ | |
| 7 | Multi-Server | ⬜ | |
| 8 | Performance | ⬜ | |
| 9 | Failure Recovery | ⬜ | |

## Automated Test Script

```bash
# test_all.bat
@echo off
echo Running automated tests...

echo Test 1: Health Check
curl -s http://localhost:3002/health | findstr "healthy"
if errorlevel 1 (echo FAIL) else (echo PASS)

echo Test 2: PostgreSQL Connection
docker exec dcim-postgres pg_isready -U dcim
if errorlevel 1 (echo FAIL) else (echo PASS)

echo Test 3: Redis Connection
docker exec dcim-redis redis-cli ping | findstr "PONG"
if errorlevel 1 (echo FAIL) else (echo PASS)

echo Test 4: List Servers
curl -s http://localhost:3002/api/v1/servers | findstr "success"
if errorlevel 1 (echo FAIL) else (echo PASS)

echo All tests complete!
pause
```

## Reporting Issues

When reporting issues, include:
1. Test phase and number
2. Expected vs actual result
3. Error messages from logs: `docker-compose logs aggregator`
4. Database state: `SELECT COUNT(*) FROM servers;`
5. Environment details: OS, Docker version, Node version

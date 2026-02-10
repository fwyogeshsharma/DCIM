# DCIM Aggregator Quick Reference

## Quick Start Commands

```bash
# Start everything
cd E:\Projects\DCIM\DCIM_Aggregator
start.bat

# Or manually:
docker-compose up -d
npm run dev

# Stop everything
stop.bat
# Or: docker-compose down
```

## Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Aggregator API | http://localhost:3002 | REST API endpoints |
| Health Check | http://localhost:3002/health | Service status |
| Frontend | http://localhost:3000 | DCIM UI |
| PostgreSQL | localhost:5432 | Database |
| Redis | localhost:6379 | Cache |

## Essential API Endpoints

### Server Management
```bash
# List servers
GET http://localhost:3002/api/v1/servers

# Add server
POST http://localhost:3002/api/v1/servers
{
  "name": "DC-East",
  "url": "http://192.168.1.100:8080/api/v1",
  "enabled": true,
  "metadata": {
    "location": "New York",
    "environment": "production"
  }
}

# Test connection
GET http://localhost:3002/api/v1/servers/:id/health

# Enable/disable
POST http://localhost:3002/api/v1/servers/:id/toggle
{"enabled": false}
```

### Data Queries
```bash
# Get all agents
GET http://localhost:3002/api/v1/agents

# Get metrics
GET http://localhost:3002/api/v1/metrics?time_range=1h&limit=100

# Get alerts
GET http://localhost:3002/api/v1/alerts?severity=critical

# Dashboard stats
GET http://localhost:3002/api/v1/dashboard/stats
```

## Database Quick Access

```bash
# Connect to PostgreSQL
docker exec -it dcim-postgres psql -U dcim -d dcim_aggregator

# Common queries:
SELECT * FROM servers;
SELECT COUNT(*) FROM agents;
SELECT COUNT(*) FROM metrics WHERE timestamp >= NOW() - INTERVAL '1 hour';
SELECT * FROM timescaledb_information.hypertables;
\q  # Exit
```

## Redis Quick Access

```bash
# Connect to Redis
docker exec -it dcim-redis redis-cli

# Common commands:
KEYS *                          # List all keys
GET server:health:<server-id>   # Get server health
FLUSHALL                        # Clear all cache
quit                            # Exit
```

## Docker Commands

```bash
# View all services
docker-compose ps

# View logs
docker-compose logs -f              # All services
docker-compose logs -f aggregator   # Aggregator only
docker-compose logs -f postgres     # PostgreSQL only

# Restart service
docker-compose restart aggregator

# Stop all services
docker-compose down

# Rebuild and start
docker-compose up -d --build
```

## Troubleshooting Quick Fixes

### Service Won't Start
```bash
# Check if ports are in use
netstat -an | findstr "3002 5432 6379"

# Kill processes on ports (if needed)
# Use Task Manager or:
taskkill /F /PID <pid>

# Restart Docker
# Docker Desktop → Restart
```

### Database Connection Failed
```bash
# Restart PostgreSQL
docker-compose restart postgres

# Check if running
docker-compose ps postgres

# Verify connectivity
docker exec dcim-postgres pg_isready -U dcim
```

### No Data Syncing
```bash
# Check if server is enabled
psql -h localhost -U dcim -d dcim_aggregator -c "SELECT id, name, enabled FROM servers;"

# Check aggregator logs
docker-compose logs aggregator | findstr "Synced"

# Restart aggregator
docker-compose restart aggregator
```

### Frontend Not Connecting
```bash
# Verify aggregator URL in DCIM_UI/.env
# Should be: VITE_AGGREGATOR_URL=http://localhost:3002/api/v1

# Restart frontend
cd E:\Projects\DCIM\DCIM_UI
npm run dev
```

## Configuration Files

### `.env` (Aggregator)
```env
NODE_ENV=development
PORT=3002
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=dcim_aggregator
POSTGRES_USER=dcim
POSTGRES_PASSWORD=dcim_password
REDIS_URL=redis://localhost:6379
LOG_LEVEL=info
```

### `.env` (Frontend)
```env
VITE_AGGREGATOR_URL=http://localhost:3002/api/v1
VITE_API_URL=http://localhost:3001/api/v1  # Fallback
```

## Worker Intervals

| Worker | Interval | Purpose |
|--------|----------|---------|
| Metrics Sync | 10 seconds | Sync latest metrics |
| Agents Sync | 30 seconds | Update agent status |
| Alerts Sync | 15 seconds | Real-time alerts |
| Health Monitor | 30 seconds | Check server health |

To change intervals, edit files in `src/workers/`

## Database Schema Quick Ref

```sql
-- Servers: DCIM backend server configurations
servers (id, name, url, enabled, metadata)

-- Agents: Combined agents from all servers
agents (server_id, agent_id, hostname, ip_address, status)

-- Metrics: Time-series data (hypertable)
metrics (server_id, agent_id, metric_type, value, timestamp)

-- Alerts: Combined alerts from all servers
alerts (server_id, agent_id, severity, message, resolved)

-- SNMP Metrics: SNMP device data (hypertable)
snmp_metrics (server_id, agent_id, device_name, metric_name, value)
```

## Performance Tips

### Query Optimization
```sql
-- Use aggregated view for historical data
SELECT * FROM metrics_hourly WHERE bucket >= NOW() - INTERVAL '30 days';

-- Filter by server_id for single-server queries
SELECT * FROM metrics WHERE server_id = '<id>' AND timestamp >= NOW() - INTERVAL '1 hour';

-- Use LIMIT to prevent large result sets
SELECT * FROM metrics ORDER BY timestamp DESC LIMIT 1000;
```

### Cache Warming
```bash
# Warm up cache for frequently accessed data
curl "http://localhost:3002/api/v1/agents"
curl "http://localhost:3002/api/v1/metrics?time_range=1h"
curl "http://localhost:3002/api/v1/dashboard/stats"
```

## Backup & Restore

### Backup
```bash
# Backup all data
docker exec dcim-postgres pg_dump -U dcim dcim_aggregator > backup_$(date +%Y%m%d).sql

# Backup schema only
docker exec dcim-postgres pg_dump -U dcim -s dcim_aggregator > schema.sql
```

### Restore
```bash
# Restore from backup
docker exec -i dcim-postgres psql -U dcim dcim_aggregator < backup.sql
```

## Health Check Script

Create `health_check.bat`:
```batch
@echo off
echo Checking DCIM Aggregator Health...
echo.

curl -s http://localhost:3002/health | findstr "healthy"
if errorlevel 1 (echo [FAIL] Aggregator) else (echo [PASS] Aggregator)

docker exec dcim-postgres pg_isready -U dcim >nul 2>&1
if errorlevel 1 (echo [FAIL] PostgreSQL) else (echo [PASS] PostgreSQL)

docker exec dcim-redis redis-cli ping | findstr "PONG" >nul 2>&1
if errorlevel 1 (echo [FAIL] Redis) else (echo [PASS] Redis)

pause
```

## Common SQL Queries

```sql
-- Check database size
SELECT pg_size_pretty(pg_database_size('dcim_aggregator'));

-- Count data by server
SELECT s.name, COUNT(*) as agent_count
FROM agents a
JOIN servers s ON a.server_id = s.id
GROUP BY s.name;

-- Recent metrics by type
SELECT metric_type, COUNT(*) as count
FROM metrics
WHERE timestamp >= NOW() - INTERVAL '1 hour'
GROUP BY metric_type;

-- Active alerts by severity
SELECT severity, COUNT(*) as count
FROM alerts
WHERE resolved = false
GROUP BY severity;

-- Check compression stats
SELECT
  hypertable_name,
  pg_size_pretty(before_compression_total_bytes) as before,
  pg_size_pretty(after_compression_total_bytes) as after
FROM timescaledb_information.compression_settings;
```

## Monitoring Commands

```bash
# Watch logs in real-time
docker-compose logs -f aggregator | findstr "Synced"

# Check sync worker activity
docker-compose logs aggregator | findstr "worker"

# Monitor database connections
docker exec dcim-postgres psql -U dcim -d dcim_aggregator -c "SELECT count(*) FROM pg_stat_activity;"

# Check Redis memory usage
docker exec dcim-redis redis-cli INFO memory | findstr "used_memory_human"
```

## Emergency Procedures

### Complete Reset
```bash
# WARNING: This deletes ALL data
docker-compose down -v
docker-compose up -d postgres redis
timeout /t 10 /nobreak
npm run migrate
docker-compose up -d aggregator
```

### Clear Cache Only
```bash
docker exec dcim-redis redis-cli FLUSHALL
```

### Reset Database Only
```bash
docker exec dcim-postgres psql -U dcim -d dcim_aggregator -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
npm run migrate
docker-compose restart aggregator
```

## Getting Help

1. **Check logs first:**
   ```bash
   docker-compose logs -f aggregator
   ```

2. **Review documentation:**
   - `README.md` - Overview
   - `SETUP_GUIDE.md` - Setup steps
   - `TESTING_GUIDE.md` - Testing procedures
   - `IMPLEMENTATION_SUMMARY.md` - Complete details

3. **Verify configuration:**
   - Check `.env` files
   - Verify service URLs
   - Test connections

4. **Common issues:**
   - Port conflicts → Check `netstat`
   - Connection refused → Restart services
   - No data syncing → Check server enabled status
   - Cache issues → Clear Redis cache

## Useful Aliases (Optional)

Create `aliases.bat`:
```batch
@echo off
doskey dcup=docker-compose up -d
doskey dcdown=docker-compose down
doskey dclogs=docker-compose logs -f aggregator
doskey dcps=docker-compose ps
doskey dcrestart=docker-compose restart aggregator
doskey pgcli=docker exec -it dcim-postgres psql -U dcim -d dcim_aggregator
doskey rediscli=docker exec -it dcim-redis redis-cli
```

Run `aliases.bat` to enable shortcuts in current terminal.

## Version Info

- Node.js: 20+
- PostgreSQL: 15+
- TimescaleDB: 2.13+
- Redis: 7+
- TypeScript: 5.3+

---

**Quick Links:**
- [Full Setup Guide](SETUP_GUIDE.md)
- [Testing Guide](TESTING_GUIDE.md)
- [Implementation Summary](../IMPLEMENTATION_SUMMARY.md)

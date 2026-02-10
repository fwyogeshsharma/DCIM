# Local Setup Guide (Without Docker)

This guide helps you set up the DCIM Aggregator on Windows without Docker.

## Prerequisites

You need to install these services locally:

1. **Node.js 20+**
2. **PostgreSQL 15+** with TimescaleDB
3. **Redis 7+**

---

## Step 1: Install Node.js

1. Download Node.js 20 LTS from: https://nodejs.org/
2. Run the installer
3. Verify installation:
   ```bash
   node --version
   npm --version
   ```

---

## Step 2: Install PostgreSQL with TimescaleDB

### Option A: PostgreSQL + TimescaleDB (Recommended)

1. **Download PostgreSQL 15**:
   - Go to: https://www.postgresql.org/download/windows/
   - Download the installer (postgresql-15.x-windows-x64.exe)
   - Run installer with default settings
   - **Remember the password you set for 'postgres' user**

2. **Install TimescaleDB Extension**:
   - Download TimescaleDB from: https://www.timescale.com/download
   - Run the TimescaleDB installer
   - Select your PostgreSQL installation
   - Complete installation

3. **Verify PostgreSQL is running**:
   ```bash
   # Open Command Prompt
   pg_isready -h localhost -p 5432

   # Should output: localhost:5432 - accepting connections
   ```

4. **Create Database and User**:
   ```bash
   # Open psql command prompt
   psql -U postgres

   # Inside psql, run these commands:
   CREATE DATABASE dcim_aggregator;
   CREATE USER dcim WITH PASSWORD 'dcim_password';
   GRANT ALL PRIVILEGES ON DATABASE dcim_aggregator TO dcim;
   ALTER DATABASE dcim_aggregator OWNER TO dcim;
   \q
   ```

5. **Enable TimescaleDB Extension**:
   ```bash
   psql -U dcim -d dcim_aggregator

   # Inside psql:
   CREATE EXTENSION IF NOT EXISTS timescaledb;
   \q
   ```

### Option B: PostgreSQL Only (Without TimescaleDB)

If you can't install TimescaleDB, you can use regular PostgreSQL:

1. Install PostgreSQL 15 as above
2. Create database and user as above
3. Skip TimescaleDB extension
4. **Modify migration files** to remove TimescaleDB-specific features (see below)

---

## Step 3: Install Redis

### Option A: Memurai (Redis for Windows - Recommended)

1. Download Memurai from: https://www.memurai.com/get-memurai
2. Run the installer
3. Memurai will start automatically as a Windows service
4. Verify installation:
   ```bash
   redis-cli ping
   # Should output: PONG
   ```

### Option B: Redis on WSL2

1. Install WSL2:
   ```bash
   wsl --install
   ```
2. Restart computer
3. Open WSL2 (Ubuntu) and install Redis:
   ```bash
   sudo apt update
   sudo apt install redis-server
   sudo service redis-server start
   ```
4. Verify:
   ```bash
   redis-cli ping
   # Should output: PONG
   ```

### Option C: Redis for Windows (Community Build)

1. Download from: https://github.com/tporadowski/redis/releases
2. Extract to `C:\Program Files\Redis`
3. Run `redis-server.exe`
4. In another terminal, test with `redis-cli.exe ping`

---

## Step 4: Configure Environment

1. **Navigate to aggregator directory**:
   ```bash
   cd E:\Projects\DCIM\DCIM_Aggregator
   ```

2. **Verify `.env` file** (already created):
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

3. **If you changed the PostgreSQL password**, update it in `.env`

---

## Step 5: Install Dependencies

```bash
cd E:\Projects\DCIM\DCIM_Aggregator
npm install
```

---

## Step 6: Run Migrations

```bash
npm run migrate
```

You should see:
```
Starting database migrations...
Running migration: 001_initial_schema.sql
✓ Migration completed: 001_initial_schema.sql
Running migration: 002_timescale_setup.sql
✓ Migration completed: 002_timescale_setup.sql
All migrations completed successfully
```

**If TimescaleDB migration fails:**
1. You're probably using PostgreSQL without TimescaleDB
2. See "Running Without TimescaleDB" section below

---

## Step 7: Start the Aggregator

### Using the start script:
```bash
start-local.bat
```

### Or manually:
```bash
npm run dev
```

You should see:
```
PostgreSQL connected
Redis connected
Metrics sync worker started (every 10s)
Agents sync worker started (every 30s)
Alerts sync worker started (every 15s)
Health monitor worker started (every 30s)
DCIM Aggregator listening on port 3002
```

---

## Step 8: Verify Everything Works

### Test health endpoint:
```bash
curl http://localhost:3002/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "DCIM Aggregator",
  "timestamp": "2026-02-10T12:00:00.000Z"
}
```

### Test database connection:
```bash
psql -h localhost -U dcim -d dcim_aggregator

# Inside psql:
\dt
# Should show: servers, agents, metrics, alerts, etc.
\q
```

### Test Redis connection:
```bash
redis-cli ping
# Should output: PONG
```

---

## Running Without TimescaleDB

If you can't install TimescaleDB, modify the migration files:

### 1. Keep using `001_initial_schema.sql` as-is

### 2. Create a simplified `002_timescale_setup.sql`:

```sql
-- Skip TimescaleDB extension (comment out or remove)
-- CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Add a primary key to metrics (needed without hypertable)
ALTER TABLE metrics ADD PRIMARY KEY (id);
ALTER TABLE snmp_metrics ADD PRIMARY KEY (id);

-- Skip hypertable conversion
-- SELECT create_hypertable('metrics', 'timestamp', ...);

-- Skip compression
-- ALTER TABLE metrics SET (timescaledb.compress, ...);

-- Skip continuous aggregates
-- CREATE MATERIALIZED VIEW metrics_hourly ...

-- Create a regular view instead (slower but works)
CREATE OR REPLACE VIEW metrics_hourly AS
SELECT
    DATE_TRUNC('hour', timestamp) AS bucket,
    server_id,
    agent_id,
    metric_type,
    AVG(value) as avg_value,
    MAX(value) as max_value,
    MIN(value) as min_value,
    COUNT(*) as sample_count
FROM metrics
GROUP BY bucket, server_id, agent_id, metric_type;

-- Manual cleanup instead of retention policy
-- Set up a scheduled task to delete old data:
-- DELETE FROM metrics WHERE timestamp < NOW() - INTERVAL '90 days';
```

### 3. Re-run migrations:
```bash
npm run migrate
```

**Note:** Without TimescaleDB:
- ✅ Everything still works
- ❌ Slower queries on large datasets
- ❌ No automatic compression
- ❌ No automatic data retention
- ❌ Manual cleanup needed for old data

---

## Troubleshooting

### PostgreSQL not starting

**Check Windows Services:**
1. Press `Win + R`, type `services.msc`
2. Find "postgresql-x64-15"
3. Right-click → Start

**Or via command line:**
```bash
# Start PostgreSQL service
net start postgresql-x64-15
```

### Redis not starting

**If using Memurai:**
1. Press `Win + R`, type `services.msc`
2. Find "Memurai"
3. Right-click → Start

**If using WSL2:**
```bash
wsl
sudo service redis-server start
```

### Connection refused errors

**PostgreSQL:**
```bash
# Check if running on port 5432
netstat -an | findstr 5432

# If different port, update .env:
POSTGRES_PORT=5433
```

**Redis:**
```bash
# Check if running on port 6379
netstat -an | findstr 6379

# If different port, update .env:
REDIS_URL=redis://localhost:6380
```

### Migration errors

**"relation already exists":**
```bash
# Drop all tables and re-run
psql -U dcim -d dcim_aggregator

DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO dcim;
\q

# Re-run migrations
npm run migrate
```

**"extension timescaledb does not exist":**
- See "Running Without TimescaleDB" section above

### Port conflicts

**Port 3002 already in use:**
```bash
# Update .env:
PORT=3003

# Restart aggregator
```

---

## Managing Services

### Start PostgreSQL:
```bash
net start postgresql-x64-15
```

### Stop PostgreSQL:
```bash
net stop postgresql-x64-15
```

### Start Redis (Memurai):
```bash
net start Memurai
```

### Stop Redis (Memurai):
```bash
net stop Memurai
```

### Start Aggregator:
```bash
cd E:\Projects\DCIM\DCIM_Aggregator
npm run dev
```

---

## Performance Tips (Local Setup)

### PostgreSQL Configuration

Edit `C:\Program Files\PostgreSQL\15\data\postgresql.conf`:

```ini
# Memory settings (adjust based on your RAM)
shared_buffers = 256MB          # 25% of RAM
effective_cache_size = 1GB      # 50% of RAM
work_mem = 16MB

# Connection settings
max_connections = 100

# Logging
log_statement = 'all'           # For debugging (remove in production)
```

Restart PostgreSQL after changes:
```bash
net stop postgresql-x64-15
net start postgresql-x64-15
```

### Redis Configuration

For Memurai, edit `C:\Program Files\Memurai\memurai.conf`:

```ini
maxmemory 512mb
maxmemory-policy allkeys-lru
appendonly yes
```

Restart Memurai:
```bash
net stop Memurai
net start Memurai
```

---

## Automated Startup (Optional)

### Create startup script `auto-start.bat`:

```batch
@echo off
echo Starting DCIM Aggregator services...

REM Start PostgreSQL
net start postgresql-x64-15

REM Start Redis/Memurai
net start Memurai

REM Wait for services to be ready
timeout /t 5 /nobreak

REM Start Aggregator
cd E:\Projects\DCIM\DCIM_Aggregator
start "DCIM Aggregator" cmd /k npm run dev

echo All services started!
```

### Create shutdown script `auto-stop.bat`:

```batch
@echo off
echo Stopping DCIM Aggregator services...

REM Stop PostgreSQL
net stop postgresql-x64-15

REM Stop Redis/Memurai
net stop Memurai

echo All services stopped!
pause
```

**Note:** These require Administrator privileges. Right-click → "Run as Administrator"

---

## Next Steps

1. **Test the setup:**
   ```bash
   curl http://localhost:3002/health
   ```

2. **Start the frontend:**
   ```bash
   cd E:\Projects\DCIM\DCIM_UI
   npm run dev
   ```

3. **Add your first server:**
   - Open http://localhost:5173
   - Click "Servers" in sidebar
   - Add a DCIM server

4. **Monitor logs:**
   - Check `combined.log` and `error.log` in the aggregator directory
   - Watch console output for sync activity

---

## Backup & Maintenance

### Backup PostgreSQL:
```bash
pg_dump -U dcim -h localhost dcim_aggregator > backup_%date:~-4,4%%date:~-7,2%%date:~-10,2%.sql
```

### Restore PostgreSQL:
```bash
psql -U dcim -h localhost dcim_aggregator < backup_20260210.sql
```

### Clean old data (if not using TimescaleDB):
```sql
psql -U dcim -d dcim_aggregator

-- Delete metrics older than 90 days
DELETE FROM metrics WHERE timestamp < NOW() - INTERVAL '90 days';

-- Delete SNMP metrics older than 90 days
DELETE FROM snmp_metrics WHERE timestamp < NOW() - INTERVAL '90 days';

-- Vacuum to reclaim space
VACUUM ANALYZE metrics;
VACUUM ANALYZE snmp_metrics;
```

---

## Support

If you encounter issues:
1. Check service status: PostgreSQL, Redis, Node.js
2. Review logs: `combined.log`, `error.log`
3. Test connections individually
4. Verify `.env` configuration
5. Check Windows Firewall settings

For TimescaleDB-specific issues, refer to: https://docs.timescale.com/

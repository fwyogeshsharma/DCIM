# DCIM Aggregator Setup Guide

Complete guide to set up the multi-server DCIM architecture with PostgreSQL aggregation layer.

## Architecture Overview

```
┌─────────────────┐
│   DCIM_UI       │ (React - Port 3000)
│   (Frontend)    │
└────────┬────────┘
         │ HTTP
         ↓
┌─────────────────┐
│ DCIM_Aggregator │ (Node.js - Port 3002)
│  - PostgreSQL   │ (Port 5432)
│  - Redis        │ (Port 6379)
└────────┬────────┘
         │ HTTP
    ┌────┴────┬────────┐
    ↓         ↓        ↓
┌────────┐ ┌────────┐ ┌────────┐
│Server 1│ │Server 2│ │Server 3│
│DC-East │ │DC-West │ │DC-EU   │
└────────┘ └────────┘ └────────┘
```

## Prerequisites

- Node.js 20+
- Docker & Docker Compose
- PostgreSQL 15+ (or use Docker)
- Redis 7+ (or use Docker)

## Step 1: Install Dependencies

```bash
cd E:\Projects\DCIM\DCIM_Aggregator
npm install
```

## Step 2: Start Database Services

### Option A: Using Docker Compose (Recommended)

```bash
cd E:\Projects\DCIM\DCIM_Aggregator
docker-compose up -d postgres redis
```

This starts:
- PostgreSQL with TimescaleDB on port 5432
- Redis on port 6379

Verify services are running:
```bash
docker-compose ps
```

### Option B: Local Installation

If you prefer local PostgreSQL:

1. Install PostgreSQL 15+
2. Install TimescaleDB extension
3. Create database:
```sql
CREATE DATABASE dcim_aggregator;
CREATE USER dcim WITH PASSWORD 'dcim_password';
GRANT ALL PRIVILEGES ON DATABASE dcim_aggregator TO dcim;
```

4. Install Redis:
```bash
# Windows (using Chocolatey)
choco install redis-64

# Or download from https://github.com/microsoftarchive/redis/releases
```

## Step 3: Configure Environment

Edit `.env` file:

```env
NODE_ENV=development
PORT=3002

# PostgreSQL Configuration
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=dcim_aggregator
POSTGRES_USER=dcim
POSTGRES_PASSWORD=dcim_password

# Redis Configuration
REDIS_URL=redis://localhost:6379

# Logging
LOG_LEVEL=info
```

## Step 4: Run Database Migrations

```bash
npm run migrate
```

This will:
1. Create all tables (servers, agents, metrics, alerts, etc.)
2. Set up TimescaleDB hypertables for time-series data
3. Configure compression and retention policies
4. Create continuous aggregates for performance

Verify migration success:
```bash
# Connect to PostgreSQL
psql -h localhost -U dcim -d dcim_aggregator

# Check tables
\dt

# Check TimescaleDB hypertables
SELECT * FROM timescaledb_information.hypertables;

# Exit
\q
```

## Step 5: Start Aggregator Service

### Development Mode

```bash
npm run dev
```

### Production Mode

```bash
npm run build
npm start
```

### Using Docker

```bash
docker-compose up -d
```

Verify service is running:
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

## Step 6: Configure Frontend (DCIM_UI)

### Update Environment Variables

Edit `E:\Projects\DCIM\DCIM_UI\.env`:

```env
# Point to aggregator service
VITE_AGGREGATOR_URL=http://localhost:3002/api/v1
```

### Restart Frontend

```bash
cd E:\Projects\DCIM\DCIM_UI
npm run dev
```

## Step 7: Add DCIM Servers

### Via API

```bash
curl -X POST http://localhost:3002/api/v1/servers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DC-East",
    "url": "http://192.168.1.100:8080/api/v1",
    "enabled": true,
    "metadata": {
      "location": "New York",
      "environment": "production",
      "color": "#3b82f6"
    }
  }'
```

### Via UI

1. Open browser: `http://localhost:3000`
2. Navigate to **Server Management** (Servers icon in sidebar)
3. Click **Add Server** button
4. Fill in server details:
   - **Name**: DC-East
   - **URL**: http://192.168.1.100:8080/api/v1
   - **Location**: New York
   - **Environment**: Production
5. Click **Test** to verify connection
6. Click **Add Server**

## Step 8: Verify Data Sync

### Check Logs

```bash
# View aggregator logs
docker-compose logs -f aggregator

# Or if running locally
tail -f combined.log
```

You should see:
```
Metrics sync worker started (every 10s)
Agents sync worker started (every 30s)
Alerts sync worker started (every 15s)
Health monitor worker started (every 30s)
```

### Check Database

```bash
psql -h localhost -U dcim -d dcim_aggregator

# Check servers
SELECT id, name, url, enabled FROM servers;

# Check synced agents
SELECT COUNT(*) FROM agents;

# Check synced metrics
SELECT COUNT(*) FROM metrics WHERE timestamp >= NOW() - INTERVAL '1 hour';

# Check server health
SELECT * FROM agents LIMIT 10;
```

### Check Redis Cache

```bash
redis-cli

# Check cached keys
KEYS server:health:*

# View server health
GET server:health:<server-id>
```

## Step 9: Test API Endpoints

### Get All Servers

```bash
curl http://localhost:3002/api/v1/servers
```

### Get Aggregated Agents

```bash
curl http://localhost:3002/api/v1/agents
```

### Get Metrics

```bash
curl "http://localhost:3002/api/v1/metrics?time_range=1h&limit=100"
```

### Get Dashboard Stats

```bash
curl http://localhost:3002/api/v1/dashboard/stats
```

Expected response:
```json
{
  "success": true,
  "data": {
    "servers": 3,
    "agents": {
      "total": 150,
      "online": 145,
      "offline": 5
    },
    "activeAlerts": 12
  }
}
```

## Troubleshooting

### PostgreSQL Connection Error

```
Error: connect ECONNREFUSED 127.0.0.1:5432
```

**Solution:**
```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# Restart PostgreSQL
docker-compose restart postgres

# Check logs
docker-compose logs postgres
```

### Redis Connection Error

```
Error: connect ECONNREFUSED 127.0.0.1:6379
```

**Solution:**
```bash
# Check if Redis is running
docker-compose ps redis

# Restart Redis
docker-compose restart redis
```

### Migration Failed

```
Error: relation "servers" does not exist
```

**Solution:**
```bash
# Drop all tables and re-run migrations
psql -h localhost -U dcim -d dcim_aggregator -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Run migrations again
npm run migrate
```

### Workers Not Syncing Data

**Check:**
1. Server is enabled: `SELECT enabled FROM servers WHERE id = '<server-id>';`
2. Server URL is accessible: Test connection via UI
3. Check worker logs for errors

**Solution:**
```bash
# Restart aggregator
docker-compose restart aggregator

# Or if running locally
npm run dev
```

### TimescaleDB Extension Not Found

```
ERROR: extension "timescaledb" does not exist
```

**Solution:**
Make sure you're using the TimescaleDB Docker image:
```yaml
# In docker-compose.yml
postgres:
  image: timescale/timescaledb:latest-pg15
```

## Performance Tuning

### PostgreSQL

For production, tune PostgreSQL settings:

```sql
-- Increase shared buffers (25% of RAM)
ALTER SYSTEM SET shared_buffers = '4GB';

-- Increase work memory for complex queries
ALTER SYSTEM SET work_mem = '64MB';

-- Increase max connections
ALTER SYSTEM SET max_connections = 200;

-- Apply changes
SELECT pg_reload_conf();
```

### Redis

Configure Redis persistence:

```bash
# In docker-compose.yml, add:
redis:
  command: redis-server --appendonly yes --maxmemory 2gb --maxmemory-policy allkeys-lru
```

### Aggregator Workers

Adjust sync intervals in worker files:

```typescript
// metricsSync.ts
cron.schedule('*/10 * * * * *', ...) // Every 10 seconds

// For less frequent updates:
cron.schedule('*/30 * * * * *', ...) // Every 30 seconds
```

## Monitoring

### Application Logs

```bash
# View all logs
docker-compose logs -f

# View aggregator logs only
docker-compose logs -f aggregator

# View PostgreSQL logs
docker-compose logs -f postgres
```

### Database Metrics

```sql
-- Check table sizes
SELECT
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check hypertable chunks
SELECT * FROM timescaledb_information.chunks;

-- Check compression stats
SELECT * FROM timescaledb_information.compression_settings;
```

### Health Monitoring

Create a monitoring script:

```bash
#!/bin/bash
# health_check.sh

# Check aggregator
curl -f http://localhost:3002/health || echo "Aggregator down"

# Check PostgreSQL
pg_isready -h localhost -p 5432 || echo "PostgreSQL down"

# Check Redis
redis-cli ping || echo "Redis down"
```

## Backup & Recovery

### Backup PostgreSQL

```bash
# Full backup
docker exec dcim-postgres pg_dump -U dcim dcim_aggregator > backup.sql

# Backup schema only
docker exec dcim-postgres pg_dump -U dcim -s dcim_aggregator > schema.sql

# Backup data only
docker exec dcim-postgres pg_dump -U dcim -a dcim_aggregator > data.sql
```

### Restore PostgreSQL

```bash
# Restore full backup
docker exec -i dcim-postgres psql -U dcim dcim_aggregator < backup.sql
```

## Scaling

### Horizontal Scaling

Run multiple aggregator instances behind a load balancer:

```yaml
# docker-compose.yml
aggregator-1:
  build: .
  environment:
    - PORT=3002

aggregator-2:
  build: .
  environment:
    - PORT=3003

nginx:
  image: nginx:alpine
  ports:
    - "3002:80"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf
```

### Database Replication

For high availability, set up PostgreSQL replication:

```yaml
postgres-primary:
  image: timescale/timescaledb:latest-pg15

postgres-replica:
  image: timescale/timescaledb:latest-pg15
  environment:
    - POSTGRES_PRIMARY_HOST=postgres-primary
```

## Security

### Production Checklist

- [ ] Change default PostgreSQL password
- [ ] Enable SSL for PostgreSQL connections
- [ ] Set up firewall rules
- [ ] Enable Redis authentication
- [ ] Use environment variables for secrets
- [ ] Enable HTTPS for API endpoints
- [ ] Implement API authentication
- [ ] Set up network isolation

### SSL Configuration

Update `.env`:
```env
POSTGRES_SSL=true
POSTGRES_CA_CERT=/path/to/ca-cert.pem
```

## Next Steps

1. **Add More Servers**: Use the Server Management UI to add all your DCIM servers
2. **Configure Alerts**: Set up alert forwarding from aggregator
3. **Set Up Backups**: Create automated backup scripts
4. **Monitor Performance**: Use the dashboard to track system health
5. **Optimize Queries**: Review slow queries and add indexes as needed

## Support

For issues or questions:
- Check logs: `docker-compose logs -f`
- Review GitHub issues
- Check TimescaleDB documentation: https://docs.timescale.com/

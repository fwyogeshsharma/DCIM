# DCIM Aggregator Service

Multi-server DCIM data aggregation service with PostgreSQL + TimescaleDB and Redis caching.

## Features

- **Multi-Server Monitoring**: Aggregate data from multiple DCIM backend servers
- **TimescaleDB**: Optimized time-series storage with automatic compression and retention
- **Redis Caching**: Fast data access with intelligent caching layer
- **Background Workers**: Automatic sync of agents, metrics, alerts, and SNMP data
- **REST API**: Complete API for managing servers and querying aggregated data
- **Health Monitoring**: Real-time server health checks and status tracking

## Architecture

```
DCIM_UI (React) → DCIM_Aggregator (Node.js) → Multiple DCIM_Server instances
                        ↓
                  PostgreSQL + TimescaleDB
                        ↓
                    Redis Cache
```

## Quick Start

### 1. Install Dependencies

```bash
npm install
```

### 2. Configure Environment

Edit `.env` file with your database credentials:

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=dcim_aggregator
POSTGRES_USER=dcim
POSTGRES_PASSWORD=dcim_password
REDIS_URL=redis://localhost:6379
PORT=3002
```

### 3. Start with Docker Compose

```bash
docker-compose up -d
```

This will start:
- PostgreSQL with TimescaleDB extension (port 5432)
- Redis (port 6379)
- DCIM Aggregator API (port 3002)

### 4. Run Migrations

```bash
npm run migrate
```

### 5. Start Development Server

```bash
npm run dev
```

## API Endpoints

### Server Management

- `GET /api/v1/servers` - List all servers
- `POST /api/v1/servers` - Add new server
- `PUT /api/v1/servers/:id` - Update server
- `DELETE /api/v1/servers/:id` - Remove server
- `GET /api/v1/servers/:id/health` - Test server connection

### Agents

- `GET /api/v1/agents` - Get all agents (aggregated)
- `GET /api/v1/agents/:serverId/:agentId` - Get specific agent
- `GET /api/v1/agents/stats/summary` - Get agent statistics

### Metrics

- `GET /api/v1/metrics` - Query metrics with filters
- `GET /api/v1/metrics/aggregated` - Get pre-aggregated hourly metrics
- `GET /api/v1/metrics/latest/:agentId` - Get latest metrics for agent
- `GET /api/v1/metrics/snmp` - Query SNMP metrics

### Alerts

- `GET /api/v1/alerts` - Get all alerts
- `GET /api/v1/alerts/stats` - Get alert statistics
- `GET /api/v1/alerts/by-server/:serverId` - Get alerts by server

### Dashboard

- `GET /api/v1/dashboard/stats` - Get overall dashboard statistics

## Background Workers

The aggregator runs several background workers:

1. **Metrics Sync** (every 10s): Syncs metrics from all enabled servers
2. **Agents Sync** (every 30s): Syncs agent status and information
3. **Alerts Sync** (every 15s): Syncs alerts and notifications
4. **Health Monitor** (every 30s): Checks server connectivity and response times

## Database Schema

### Tables

- `servers` - Server configurations
- `agents` - Agent information from all servers
- `metrics` - Time-series metrics (hypertable)
- `snmp_metrics` - SNMP device metrics (hypertable)
- `alerts` - Alert history
- `user_preferences` - User preferences per server

### TimescaleDB Features

- **Hypertables**: Automatic time-based partitioning
- **Compression**: Data older than 7 days is compressed
- **Continuous Aggregates**: Pre-computed hourly statistics
- **Retention Policies**: Auto-delete data older than 90 days

## Adding a DCIM Server

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
      "environment": "production"
    }
  }'
```

### Via UI

Use the Server Management page in DCIM_UI to add servers with a visual interface.

## Development

### Build TypeScript

```bash
npm run build
```

### Run Tests

```bash
npm test
```

### View Logs

```bash
docker-compose logs -f aggregator
```

## Production Deployment

1. Update `.env` with production credentials
2. Set `NODE_ENV=production`
3. Use stronger PostgreSQL passwords
4. Configure Redis persistence
5. Set up SSL/TLS for API endpoints
6. Configure firewall rules

## Performance Tuning

### PostgreSQL

- Increase `shared_buffers` for better caching
- Tune `work_mem` for complex queries
- Adjust `max_connections` based on load

### Redis

- Enable persistence (AOF or RDB)
- Configure `maxmemory` and eviction policy
- Use Redis Sentinel for high availability

### Application

- Adjust worker intervals based on data freshness needs
- Tune cache TTLs for your use case
- Scale horizontally with multiple aggregator instances

## Monitoring

Check service health:

```bash
curl http://localhost:3002/health
```

View server health:

```bash
curl http://localhost:3002/api/v1/servers
```

## Troubleshooting

### Connection Issues

1. Check PostgreSQL is running: `docker-compose ps`
2. Test database connection: `psql -h localhost -U dcim -d dcim_aggregator`
3. Verify Redis: `redis-cli ping`

### Migration Errors

Run migrations manually:

```bash
npm run migrate
```

### Worker Issues

Check logs for specific worker errors:

```bash
docker-compose logs aggregator | grep "worker error"
```

## License

MIT

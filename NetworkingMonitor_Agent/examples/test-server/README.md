# Test Server for Network Monitor Agent

This is a simple HTTP server for testing the Network Monitor Agent. It implements the minimum required API endpoints to receive metrics and alerts from the agent.

## Features

- Receives and logs metrics from agents
- Receives and logs alerts with visual indicators
- Simple console output for easy debugging
- No database required
- Lightweight and easy to run

## Running the Test Server

### Option 1: Using Go Run

```bash
cd examples/test-server
go run main.go
```

### Option 2: Build and Run

```bash
cd examples/test-server
go build -o test-server .
./test-server
```

The server will start on `http://localhost:8080`

## Configure the Agent

Update your agent's `config.yaml`:

```yaml
server:
  url: "http://localhost:8080/api/v1"
  timeout: 30s
  retry_attempts: 3
```

## Endpoints

- `POST /api/v1/metrics` - Receive metrics
- `POST /api/v1/alerts` - Receive alerts
- `GET /health` - Health check

## Example Output

```
2024/01/20 10:30:00 Test monitoring server starting on :8080
2024/01/20 10:30:00 Endpoints:
2024/01/20 10:30:00   POST http://localhost:8080/api/v1/metrics
2024/01/20 10:30:00   POST http://localhost:8080/api/v1/alerts
2024/01/20 10:30:00   GET  http://localhost:8080/health

➡️  POST /api/v1/metrics (Agent: hostname-1234567890)
📊 Received 5 metrics from agent hostname-1234567890
   - cpu.usage: 45.20 percent (at 2024-01-20T10:29:30Z)
   - memory.usage: 62.50 percent (at 2024-01-20T10:29:30Z)
     Metadata: {"available":6000000000,"total":16000000000,"used":10000000000}
   - disk.usage: 75.30 percent (at 2024-01-20T10:29:30Z)
     Metadata: {"device":"/dev/sda1","free":123500000000,"mountpoint":"/","total":500000000000,"used":376500000000}
⬅️  POST /api/v1/metrics completed in 1.234ms

➡️  POST /api/v1/alerts (Agent: hostname-1234567890)
🚨 Received 1 alerts from agent hostname-1234567890
   🔥 [CRITICAL] cpu CRITICAL: 96.50% (threshold: 95.00%)
     Metric: cpu = 96.50 (threshold: 95.00)
     Time: 2024-01-20T10:30:00Z, Retries: 0
⬅️  POST /api/v1/alerts completed in 0.523ms
```

## Testing with cURL

### Send Test Metrics

```bash
curl -X POST http://localhost:8080/api/v1/metrics \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: test-agent" \
  -d '{
    "agent_id": "test-agent",
    "timestamp": "2024-01-20T10:30:00Z",
    "metrics": [
      {
        "timestamp": "2024-01-20T10:29:30Z",
        "metric_type": "cpu.usage",
        "value": 45.2,
        "unit": "percent"
      }
    ]
  }'
```

### Send Test Alert

```bash
curl -X POST http://localhost:8080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -H "X-Agent-ID: test-agent" \
  -d '{
    "agent_id": "test-agent",
    "timestamp": "2024-01-20T10:30:00Z",
    "alerts": [
      {
        "timestamp": "2024-01-20T10:30:00Z",
        "severity": "CRITICAL",
        "metric_type": "cpu",
        "value": 96.5,
        "threshold": 95.0,
        "message": "cpu CRITICAL: 96.50%"
      }
    ]
  }'
```

## Production Server Implementation

This test server is NOT suitable for production. For production use, you should implement:

1. **Database Storage**: Store metrics and alerts in a time-series database (InfluxDB, TimescaleDB, etc.)
2. **Authentication**: Require API keys or certificates
3. **Rate Limiting**: Prevent abuse
4. **HTTPS**: Use TLS for security
5. **Monitoring**: Track server health and performance
6. **Alerting**: Forward alerts to notification systems (Slack, PagerDuty, etc.)
7. **Data Retention**: Implement data lifecycle policies
8. **Horizontal Scaling**: Load balancer + multiple server instances

See `API_SPECIFICATION.md` for full API details.

# DCIM Server Documentation Index

**Version:** 2.0
**Last Updated:** 2026-02-13

---

## 📚 Documentation Overview

This directory contains comprehensive documentation for the DCIM Server project.

### Quick Navigation

| Document | Purpose | Audience |
|----------|---------|----------|
| **[API_DOCUMENTATION.md](API_DOCUMENTATION.md)** | Complete API reference | Developers, Integrators |
| **[API_QUICK_REFERENCE.md](API_QUICK_REFERENCE.md)** | Quick API lookup | Daily use |
| **[MIGRATIONS.md](MIGRATIONS.md)** | Database migrations guide | DevOps, Developers |
| **[AUTOMATIC_MIGRATIONS_COMPLETE.md](AUTOMATIC_MIGRATIONS_COMPLETE.md)** | Migration implementation details | Developers |
| **[ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)** | Alert resolution feature guide | Operators, Developers |
| **[IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)** | Server tracking & deduplication | Developers |

---

## 🚀 Getting Started

### 1. For API Users

**Start here:** [API_QUICK_REFERENCE.md](API_QUICK_REFERENCE.md)

Quick examples of common API calls:
- Submit metrics
- Get alerts
- Resolve alerts
- Health checks

**Then read:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md)

Complete reference with:
- All endpoints
- Request/response formats
- Authentication
- Error handling
- Code examples (Python, Go, PowerShell, cURL)

---

### 2. For Operators

**Start here:** [ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)

Learn how to:
- View alert details
- Mark alerts as resolved
- Track who fixed what
- Query resolution history

**Key APIs:**
```bash
# Get alert details
GET /api/v1/alerts/{id}

# Resolve alert
PUT /api/v1/alerts/{id}/resolve
```

---

### 3. For Developers/DevOps

**Start here:** [MIGRATIONS.md](MIGRATIONS.md)

Understand how database migrations work:
- Automatic migrations on server startup
- Migration file structure
- Adding new migrations
- Testing and deployment

**Then read:** [AUTOMATIC_MIGRATIONS_COMPLETE.md](AUTOMATIC_MIGRATIONS_COMPLETE.md)

Implementation details:
- Migration system architecture
- How tracking works
- Troubleshooting
- Production deployment

---

## 📖 Documentation by Topic

### API Development

**Primary:** [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- Complete endpoint reference
- Authentication with mTLS
- Request/response schemas
- Error handling
- Rate limiting
- Code examples

**Quick Lookup:** [API_QUICK_REFERENCE.md](API_QUICK_REFERENCE.md)
- Common commands
- Quick examples
- HTTP status codes
- Query parameters

---

### Alert Management

**Primary:** [ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)
- Alert lifecycle
- Resolution tracking (WHO, WHAT, WHEN)
- Resolution workflows
- Database queries
- Python test scripts

**Features:**
- ✅ Get single alert: `GET /api/v1/alerts/{id}`
- ✅ Resolve alert: `PUT /api/v1/alerts/{id}/resolve`
- ✅ Track resolver and fix details
- ✅ Resolution time analytics

---

### Database & Deployment

**Primary:** [MIGRATIONS.md](MIGRATIONS.md)
- Automatic migrations on startup
- Migration file format
- Creating new migrations
- Testing migrations
- Production deployment

**Implementation:** [AUTOMATIC_MIGRATIONS_COMPLETE.md](AUTOMATIC_MIGRATIONS_COMPLETE.md)
- Migration system internals
- How tracking works (schema_migrations table)
- Troubleshooting guide
- Build system integration

**Features:**
- ✅ Migrations run automatically on server start
- ✅ Tracked in database (schema_migrations table)
- ✅ Safe, transactional, idempotent
- ✅ Included in build package

---

### Server Features

**Primary:** [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)
- Server identification tracking
- Alert deduplication
- Multi-server support
- Database schema changes

**Features:**
- ✅ Server tracking (`server_id` in all tables)
- ✅ Alert deduplication (`occurrence_count`)
- ✅ Persistence tracking (`first_seen`, `last_seen`)
- ✅ Auto-generated server ID

---

## 🎯 Common Tasks

### I want to...

#### ...integrate with the API
1. Read [API_QUICK_REFERENCE.md](API_QUICK_REFERENCE.md) for examples
2. Generate certificates (see below)
3. Start coding using examples

#### ...understand all API endpoints
1. Read [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
2. Check code examples section
3. Test with curl/Python

#### ...deploy the server
1. Read [MIGRATIONS.md](MIGRATIONS.md) - Automatic migrations section
2. Build: `.\build.ps1 -Platform windows`
3. Run: `.\dcim-server.exe -config config.yaml`
4. Migrations run automatically!

#### ...add a new database feature
1. Read [MIGRATIONS.md](MIGRATIONS.md) - Adding new migrations
2. Create migration file in `migrations/` folder
3. Rebuild and test
4. Migration runs automatically on startup

#### ...resolve alerts
1. Read [ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)
2. Use API: `PUT /api/v1/alerts/{id}/resolve`
3. Provide: `resolved_by`, `resolution_action`, `resolution_notes`

#### ...track server metrics
1. All metrics automatically tagged with `server_id`
2. Query: `SELECT DISTINCT server_id FROM metrics`
3. See [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)

---

## 🔐 Certificate Management

**Generate Certificates:**
```powershell
cd DCIM_Server/scripts
.\generate-certs.ps1
```

**Output:**
- `certs/ca.crt` - CA certificate
- `certs/server.crt` - Server certificate
- `certs/server.key` - Server private key
- `certs/agents/{agent-name}/client.crt` - Client certificate
- `certs/agents/{agent-name}/client.key` - Client private key

**Usage in API calls:**
```bash
curl https://localhost:8443/api/v1/alerts \
  --cacert certs/ca.crt \
  --cert certs/agents/agent-001/client.crt \
  --key certs/agents/agent-001/client.key
```

---

## 🔄 What's New in v2.0

### Alert Resolution API ⭐
- `GET /api/v1/alerts/{id}` - Get alert details
- `PUT /api/v1/alerts/{id}/resolve` - Resolve with audit trail
- Track WHO, WHAT, WHEN for each resolution
- See [ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)

### Server Tracking ⭐
- All data tagged with `server_id`
- Multi-server deployment support
- Auto-generated persistent server ID
- See [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)

### Alert Deduplication ⭐
- `occurrence_count` - How many times alert occurred
- `first_seen` - When alert first appeared
- `last_seen` - When alert last occurred
- `updated_at` - Last update timestamp
- See [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)

### Automatic Migrations ⭐
- Migrations run on server startup
- No manual SQL scripts needed
- Tracked in `schema_migrations` table
- Included in build package
- See [MIGRATIONS.md](MIGRATIONS.md)

---

## 📊 Feature Overview

### Monitoring
- ✅ System metrics (CPU, memory, disk, network)
- ✅ SNMP device monitoring
- ✅ Cooling system monitoring
- ✅ Custom metrics support

### Alerting
- ✅ Threshold-based alerts
- ✅ Alert deduplication
- ✅ Resolution tracking (WHO, WHAT, WHEN)
- ✅ Severity levels (CRITICAL, WARNING, INFO)
- ✅ Server-side alert calculation

### Management
- ✅ Agent registration and tracking
- ✅ Multi-server deployment
- ✅ Status history tracking
- ✅ Event timeline

### Security
- ✅ mTLS authentication
- ✅ TLS 1.2+ encryption
- ✅ Client certificate validation
- ✅ Rate limiting

### Operations
- ✅ Automatic database migrations
- ✅ Health check endpoint
- ✅ License management
- ✅ Configurable retention policies

---

## 🛠️ Configuration Files

| File | Purpose |
|------|---------|
| `config.yaml` | Main server configuration |
| `cooling_config.yaml` | Cooling system thresholds |
| `license.json` | License key (generated) |

---

## 📝 Quick Commands

### Build Server
```powershell
.\build.ps1 -Platform windows
```

### Run Server
```powershell
.\dcim-server.exe -config config.yaml
```

### Generate Certificates
```powershell
.\scripts\generate-certs.ps1
```

### Test API
```bash
# Health check
curl https://localhost:8443/health

# Get alerts
curl -X GET https://localhost:8443/api/v1/alerts \
  --cacert certs/ca.crt \
  --cert certs/agents/agent-001/client.crt \
  --key certs/agents/agent-001/client.key
```

---

## 🆘 Getting Help

### Documentation Issues
If documentation is unclear or incomplete:
- Open issue on GitHub
- Email: support@faberlabs.com

### API Questions
- See [API_DOCUMENTATION.md](API_DOCUMENTATION.md)
- Check code examples section
- Review [API_QUICK_REFERENCE.md](API_QUICK_REFERENCE.md)

### Deployment Issues
- See [MIGRATIONS.md](MIGRATIONS.md)
- Check [AUTOMATIC_MIGRATIONS_COMPLETE.md](AUTOMATIC_MIGRATIONS_COMPLETE.md)
- Review server logs

### Alert Management
- See [ALERT_RESOLUTION_API.md](ALERT_RESOLUTION_API.md)
- Check Python test scripts
- Review database query examples

---

## 📅 Version History

### Version 2.0 (2026-02-13)
- ✅ Alert resolution API
- ✅ Server tracking
- ✅ Alert deduplication
- ✅ Automatic migrations

### Version 1.0 (2026-02-01)
- Initial release
- Basic metrics and alerts
- SNMP monitoring
- Agent management

---

## 📄 License

See `LICENSE_MANAGEMENT.md` for license information.

---

**Last Updated:** 2026-02-13
**Documentation Version:** 2.0

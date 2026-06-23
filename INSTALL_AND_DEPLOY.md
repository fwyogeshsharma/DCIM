# DCIM — Install & Deploy Guide

Covers **(A) running the Aggregator + UI locally** for development, and **(B) deploying the full stack to a VM** with Docker + HTTPS.

> Components: **DCIM Aggregator** (Node.js/Express API, port `3002`) and **DCIM UI** (React/Vite SPA on `8000` + Express proxy on `3001`). The Aggregator needs **PostgreSQL/TimescaleDB** and **Redis**.

---

## A. Local Installation (Development)

### Prerequisites
| Tool | Version | Notes |
|---|---|---|
| Node.js | **20.x** | both apps target Node 20 |
| npm | 10+ | ships with Node 20 |
| Docker Desktop | latest | easiest way to get Postgres + Redis |
| Git | any | |

Two databases are required by the Aggregator. The simplest local path is to run **only** TimescaleDB + Redis in Docker, and run the Node apps natively for hot reload.

---

### Step 1 — Start the databases (TimescaleDB + Redis)

From the repo root (`C:\Users\Faber\AMAN\DCIM`):

```bash
docker compose up -d timescaledb redis
```

This starts:
- **TimescaleDB** (PostgreSQL 15) on host port **5435** — db `dcim_aggregator`, user `dcim`, pass `dcim_pas`
- **Redis 7** on **6379**

Verify:
```bash
docker compose ps
```

> Optional: `docker compose up -d pgadmin` gives you a DB UI at http://localhost:5050 (admin@dcim.com / admin).

---

### Step 2 — Run the Aggregator (API :3002)

```bash
cd DCIM_Aggregator
cp .env.example .env        # then edit (see below)
npm install
npm run dev                 # nodemon + ts-node, hot reload
```

Edit `DCIM_Aggregator/.env` so it points at the Docker databases (the example file ships with port 5432; the Docker DB is published on **5435**):

```ini
POSTGRES_HOST=localhost
POSTGRES_PORT=5435
POSTGRES_DB=dcim_aggregator
POSTGRES_USER=dcim
POSTGRES_PASSWORD=dcim_pas

REDIS_URL=redis://localhost:6379
PORT=3002
NODE_ENV=development

# Required only if you POST to /api/v1/ingest
INGEST_API_KEY=dev-local-key

# Optional: remote DCIM servers the workers poll (comma-separated). Leave blank locally.
DCIM_SERVER_URLS=
```

**Database migrations run automatically on startup** (the `src/database/migrations/*.sql` files are applied in order, idempotently). No manual migrate step is needed for `npm run dev`.

Confirm it's up:
```bash
curl http://localhost:3002/health
```

> **Production-style run** (compiled JS): `npm run build && npm start`. After a build you can also run migrations explicitly with `npm run migrate`.

---

### Step 3 — Run the UI (SPA :5173 + proxy :3001)

In a second terminal:

```bash
cd DCIM_UI
cp .env.example .env
npm install
npm run dev:full            # runs proxy (3001) + Vite dev server (5173) together
```

Edit `DCIM_UI/.env`:

```ini
# UI talks to the Aggregator through the local proxy
VITE_API_URL=http://localhost:3001/api/v1

# Point the proxy at the local Aggregator
AGGREGATOR_URL=http://localhost:3002

# Optional AI / NLP features
VITE_AI_API_URL=http://localhost:5000/api
# VITE_OPENAI_API_KEY=sk-...
```

Open the app: **http://localhost:5173**

> `dev:full` uses `concurrently` to run both `npm run proxy` and `npm run dev`. To run them separately, use two terminals: `npm run proxy` and `npm run dev`.

---

### Local data flow
```
Browser :5173  →  Proxy :3001  →  Aggregator :3002  →  TimescaleDB :5435 / Redis :6379
```
The UI never calls `:3002` directly — everything goes through the proxy (adds mTLS, streams SSE).

### Quick local smoke test
```bash
curl http://localhost:3002/health                 # aggregator alive
curl http://localhost:3001/api/v1/dashboard/stats  # through the proxy
```

### Common local issues
| Symptom | Fix |
|---|---|
| Aggregator exits on boot | DB not ready — `docker compose ps`, confirm port **5435**, check `.env` password `dcim_pas` |
| UI loads but no data | Proxy not running or `AGGREGATOR_URL` wrong; run `npm run dev:full` |
| `ECONNREFUSED 6379` | Redis container not up — `docker compose up -d redis` |
| Port already in use | Change `PORT` (agg) / Vite `--port` / `PROXY_PORT` |

---

## B. Deploy to a VM (Docker, Production)

The whole stack is containerized in `docker-compose.yml`. On a VM you bring it all up together: TimescaleDB, Redis, Aggregator, UI, Nginx (TLS), Certbot, pgAdmin.

### B.1 — VM requirements
| Item | Recommended |
|---|---|
| OS | Ubuntu 22.04 LTS (or any Docker-capable Linux) |
| vCPU / RAM | 2 vCPU / 4 GB minimum (4 vCPU / 8 GB comfortable) |
| Disk | 30 GB+ (time-series data grows) |
| Software | Docker Engine + Docker Compose v2 |
| Network | Inbound **80** and **443** open; **22** for SSH |
| DNS | `fwdcim.faberwork.com` → VM's public IP (A record) |

Install Docker:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # re-login after this
docker compose version
```

---

### B.2 — Get the code onto the VM
```bash
git clone <your-repo-url> dcim
cd dcim
git checkout Docker-prod
```

---

### B.3 — Set production secrets

Before bringing the stack up, change the defaults baked into `docker-compose.yml`:

1. **DB password** — `POSTGRES_PASSWORD` (currently `dcim_pas`) on both the `timescaledb` and `aggregator` services. Keep them identical.
2. **Ingest API key** — `INGEST_API_KEY` on the `aggregator` service. Generate a fresh one:
   ```bash
   openssl rand -hex 32
   ```
3. **pgAdmin password** — `PGADMIN_DEFAULT_PASSWORD` (or remove the `pgadmin` service in production).

> Inside the Docker network the services find each other by name: the Aggregator uses `POSTGRES_HOST=timescaledb` and `REDIS_URL=redis://redis:6379`; the UI proxy uses `AGGREGATOR_URL=http://aggregator:3002`. Don't change those hostnames.

---

### B.4 — Build and start the application tier

Bring up everything **except** Nginx first (Nginx needs a cert to start, handled next):

```bash
docker compose up -d --build timescaledb redis aggregator dcim-ui pgadmin
docker compose ps
```

Check health:
```bash
docker compose logs -f aggregator        # watch migrations run, "listening on 3002"
curl http://localhost:3002/health
curl http://localhost:8000                # UI preview
```

---

### B.5 — Issue the HTTPS certificate (first time only)

Nginx and Certbot have a chicken-and-egg problem (Nginx won't start without a cert; Certbot needs Nginx on port 80). The included script bootstraps it:

```bash
chmod +x init-letsencrypt.sh
./init-letsencrypt.sh
```

It (1) writes a temporary self-signed cert, (2) starts Nginx, (3) requests the real Let's Encrypt cert for `fwdcim.faberwork.com` via the HTTP-01 challenge, (4) reloads Nginx.

**Preconditions or the challenge fails:** `fwdcim.faberwork.com` must resolve to this VM, and inbound TCP **80** must be open.

> Testing? Set `STAGING=1` inside `init-letsencrypt.sh` to avoid Let's Encrypt rate limits, then re-run for real once it works.

To change the domain/email, edit `DOMAIN` and `EMAIL` at the top of `init-letsencrypt.sh` **and** the `server_name` / `ssl_certificate` paths in `simulator/nginx/nginx.conf`.

---

### B.6 — Bring up Nginx (full stack live)
```bash
docker compose up -d nginx
docker compose ps
```

Verify from your laptop:
```bash
curl -I https://fwdcim.faberwork.com         # 200 / serves the UI
```

The app is now live at **https://fwdcim.faberwork.com**. Certbot auto-renews every 12h.

---

### B.7 — Production request flow
```
Internet
  │  HTTPS :443
  ▼
Nginx (TLS termination, Let's Encrypt)
  ├── /                 → dcim-ui:8000      (SPA + proxy, SSE passthrough)
  └── /api/v1/ingest    → aggregator:3002   (device data ingest, X-Ingest-Key)
        │
   dcim-ui proxy :3001 → aggregator:3002 → timescaledb:5432 / redis:6379
```

---

### B.8 — Operations cheat-sheet
| Task | Command |
|---|---|
| View all containers | `docker compose ps` |
| Tail logs | `docker compose logs -f aggregator` (or `dcim-ui`, `nginx`) |
| Restart one service | `docker compose restart aggregator` |
| Redeploy after `git pull` | `git pull && docker compose up -d --build aggregator dcim-ui` |
| Stop everything | `docker compose down` |
| Stop **and wipe data** ⚠️ | `docker compose down -v` (deletes DB volumes) |
| Backup database | `docker compose exec timescaledb pg_dump -U dcim dcim_aggregator > backup.sql` |
| Restore database | `cat backup.sql \| docker compose exec -T timescaledb psql -U dcim dcim_aggregator` |
| Manual cert renew | `docker compose run --rm certbot renew && docker compose exec nginx nginx -s reload` |

### Ports reference
| Service | Container port | Published (VM) | Exposure |
|---|---|---|---|
| Nginx | 80 / 443 | 80 / 443 | **public** |
| DCIM UI (Vite preview) | 8000 | 8000 | behind Nginx |
| UI proxy | 3001 | 3001 | internal |
| Aggregator | 3002 | 3002 | behind Nginx (only `/api/v1/ingest` is public) |
| TimescaleDB | 5432 | 5435 | internal — **firewall off publicly** |
| Redis | 6379 | 6379 | internal — **firewall off publicly** |
| pgAdmin | 80 | 5050 | restrict or remove in prod |

> **Security note:** In production, lock down the published DB/Redis/pgAdmin ports (5435, 6379, 5050) with the VM firewall/security group so only 80, 443, and 22 are reachable from the internet. Also rotate the `INGEST_API_KEY` and `POSTGRES_PASSWORD` away from the committed defaults.

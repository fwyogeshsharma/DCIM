# DCIM_ML — AI Capacity Planning & Rack Forecasting

A Python (FastAPI) microservice that forecasts data-center resource utilization
from historical metrics and produces capacity-planning recommendations: racks
needed, rack/storage exhaustion dates, power headroom, and server-growth
projections.

It reads the shared **TimescaleDB** (`dcim_aggregator`) directly (SELECT-only on
the aggregator's source tables) and writes its results into its own `ml_*`
tables, so the aggregator/UI can consume them later.

## Architecture

```
TimescaleDB (metrics_rollup_1d/1h, energy_rollup_*, devices, dc_inventory_devices)
      │ read-only
      ▼
  source_queries → features → forecasters (select champion) → capacity engine → recommendations
      │
      └─ writes ml_forecasts / ml_capacity_summary / ml_recommendations / ml_model_runs
      ▲
  FastAPI /api/v1/*  (reads the ml_* tables back; POST /api/v1/train to retrain)
```

### Forecast targets (spec §8)
`server_count`, `cpu`, `memory`, `power` are forecast directly from observed
series. `rack_units` and `storage` (GB) are **derived** from the server-growth
forecast via per-device averages (no real rack/storage history exists yet).

### Models & graceful degradation
Per series the service backtests candidate models (XGBoost lag → Holt-Winters →
linear Trend) and picks the lowest-MAE champion. On shallow/simulated data the
heavier models aren't viable and it falls back to the **Trend** model, which
needs only two points. Backtest MAE/MAPE are stored in `ml_model_runs` so model
quality is always visible.

## API

| Method | Path | Description |
|---|---|---|
| GET  | `/health` | liveness + DB reachability + last run time |
| GET  | `/api/v1/forecasts` | forecast points (`scope`, `scope_id`, `target`, `horizon`) |
| GET  | `/api/v1/capacity/summary` | derived capacity numbers per scope |
| GET  | `/api/v1/recommendations` | recommendations (`severity`, `status`) |
| GET  | `/api/v1/models` | recent training runs + accuracy |
| POST | `/api/v1/train` | trigger a retrain pass now |

Scopes: `global`/`all` plus one `datacenter`/`<name>` per monitored datacenter.

## Configuration

Environment variables (see `.env.example`): `POSTGRES_*`, `MODEL_PATH`,
`RACK_U_DEFAULT`, `RACK_POWER_CAPACITY_W`, `FORECAST_HORIZONS`,
`CPU_THRESHOLD_PCT`, `MEM_THRESHOLD_PCT`, `RETRAIN_CRON`, `RUN_ON_STARTUP`,
`STALE_RUN_HOURS`.

## Run

In the stack (recommended) — already wired into the root `docker-compose.yml`:

```bash
docker compose build dcim-ml
docker compose up -d dcim-ml
curl http://localhost:5002/health
curl -X POST http://localhost:5002/api/v1/train
curl 'http://localhost:5002/api/v1/capacity/summary'
```

Locally:

```bash
cd DCIM_ML
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # point POSTGRES_HOST/PORT at the DB (localhost:5435)
uvicorn app.main:app --reload --port 8000
pytest                      # unit tests (no DB required)
```

## Notes / limitations (v1)
- **Shallow history**: forecasts lean on the Trend model until real history
  accumulates; treat early numbers as directional. Accuracy is tracked in
  `ml_model_runs`.
- **No dedicated rack table**: rack capacity is inferred by grouping
  `dc_inventory_devices` on `(datacenter_id, rack)`; rack height/power come from
  `RACK_U_DEFAULT` / `RACK_POWER_CAPACITY_W`.
- **Read-only discipline**: v1 reuses the `dcim` DB credentials but only SELECTs
  source tables and writes `ml_*`. For least privilege, create a dedicated
  `dcim_ml` role with `SELECT` on source tables + full rights on `ml_*`.
- Supersedes the throwaway `prediction_service/` (left untouched).

"""
FastAPI application — Datacenter Network Simulator REST API.

Start alongside Qt UI via app/main.py, or standalone:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import topology, binding, snmp, gnmi, rules, traps, devices

app = FastAPI(
    title="Datacenter Network Simulator API",
    description="REST API for all simulator actions — SNMP, gNMI, topology, IP binding, rules, traps.",
    version="2.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(topology.router, prefix="/api")
app.include_router(binding.router, prefix="/api")
app.include_router(snmp.router, prefix="/api")
app.include_router(gnmi.router, prefix="/api")
app.include_router(rules.router, prefix="/api")
app.include_router(traps.router, prefix="/api")
app.include_router(devices.router, prefix="/api")


@app.get("/")
def root():
    return {
        "name": "Datacenter Network Simulator API",
        "version": "2.2.0",
        "docs": "/docs",
        "endpoints": [
            "GET  /api/topology",
            "POST /api/topology/upload",
            "GET  /api/topology/links",
            "POST /api/topology/links/break",
            "POST /api/topology/links/restore",
            "GET  /api/binding/adapters",
            "POST /api/binding/adapter",
            "POST /api/binding/subnet-mask",
            "POST /api/binding/bind",
            "POST /api/binding/unbind",
            "GET  /api/binding/count",
            "GET  /api/binding/status",
            "POST /api/snmp/datasets/generate",
            "POST /api/snmp/start",
            "POST /api/snmp/stop",
            "POST /api/snmp/clear",
            "GET  /api/snmp/status",
            "POST /api/snmp/trap-receiver",
            "POST /api/gnmi/datasets/generate",
            "POST /api/gnmi/start",
            "POST /api/gnmi/stop",
            "POST /api/gnmi/clear",
            "GET  /api/gnmi/status",
            "POST /api/gnmi/proxy/start",
            "POST /api/gnmi/proxy/stop",
            "GET  /api/rules",
            "POST /api/rules/enable",
            "POST /api/rules/disable",
            "POST /api/rules/reset-counts",
            "GET  /api/rules/{name}",
            "POST /api/rules/{name}/enable",
            "POST /api/rules/{name}/disable",
            "GET  /api/traps",
            "POST /api/traps/send",
            "DELETE /api/traps",
            "GET  /api/devices",
            "POST /api/devices",
            "GET  /api/devices/{id}",
            "PUT  /api/devices/{id}",
            "DELETE /api/devices/{id}",
        ],
    }


@app.get("/health")
def health():
    """Health check — returns initialized state of core objects."""
    from api.state import AppState
    s = AppState.get()
    return {
        "status": "ok",
        "core_initialized": s.device_manager is not None,
        "topology_loaded": s.topology is not None and (s.topology.node_count() > 0),
        "snmp_running": s.snmpsim.is_running() if s.snmpsim else False,
        "gnmi_running": s.gnmi.is_running() if s.gnmi else False,
        "rule_engine_enabled": s.rule_engine_enabled,
        "bound_ips": len(s.bound_ips),
        "gnmi_bound_ips": len(s.gnmi_bound_ips),
    }


def start_api_server(host: str = "0.0.0.0", port: int = 8000):
    """Start uvicorn in the current thread (blocking). Call from a daemon thread."""
    import asyncio
    import logging
    import uvicorn

    log = logging.getLogger("api.server")
    try:
        # On Windows (frozen or not), create a fresh SelectorEventLoop for this
        # thread — ProactorEventLoop is the default but doesn't work reliably in
        # non-main threads inside a PyInstaller bundle.
        if hasattr(asyncio, "SelectorEventLoop"):
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            loop="none",      # we set the loop ourselves above
            log_config=None,  # disable uvicorn's log setup — sys.stdout is None in windowed exe
        )
        server = uvicorn.Server(config)
        asyncio.get_event_loop().run_until_complete(server.serve())
    except Exception as exc:
        log.exception("REST API server failed to start: %s", exc)

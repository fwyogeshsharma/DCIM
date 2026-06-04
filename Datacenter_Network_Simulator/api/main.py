"""
FastAPI application — Datacenter Network Simulator REST API.

Start alongside Qt UI via app/main.py, or standalone:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import topology, binding, snmp, gnmi, rules, traps, devices, sflow, bacnet
from api.routers import events, jobs, tick
from api.routers import graph as graph_router

app = FastAPI(
    title="Datacenter Network Simulator API",
    description="REST API for all simulator actions — SNMP, gNMI, topology, IP binding, rules, traps.",
    version="3.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _force_connection_close(request, call_next):
    """
    Windows AddIPAddress churn breaks idle loopback keep-alive sockets.
    Reusing a broken socket triggers a ~2 min TCP retransmission timeout
    on the browser side. Forcing a fresh TCP connection per request avoids
    this — every response closes the socket immediately.
    """
    response = await call_next(request)
    response.headers["Connection"] = "close"
    return response

app.include_router(topology.router, prefix="/api")
app.include_router(graph_router.router, prefix="/api")
app.include_router(binding.router, prefix="/api")
app.include_router(snmp.router, prefix="/api")
app.include_router(gnmi.router, prefix="/api")
app.include_router(rules.router, prefix="/api")
app.include_router(traps.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(sflow.router,   prefix="/api")
app.include_router(bacnet.router,  prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(tick.router, prefix="/api")


import os as _os
_root = _os.path.dirname(_os.path.dirname(__file__))
_webui_dist = _os.path.join(_root, "webui", "dist")
_assets_dir  = _os.path.join(_root, "assets")
if _os.path.isdir(_assets_dir):
    from fastapi.staticfiles import StaticFiles as _SF
    app.mount("/assets", _SF(directory=_assets_dir), name="assets")
if _os.path.isdir(_webui_dist):
    from fastapi.staticfiles import StaticFiles
    app.mount("/web", StaticFiles(directory=_webui_dist, html=True), name="webui")


@app.get("/")
async def root():
    return {
        "name": "Datacenter Network Simulator API",
        "version": "3.1.0",
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


@app.get("/api/health")
async def health():
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
        # On Windows, ProactorEventLoop (IOCP) is immune to network-stack
        # disruption caused by mass AddIPAddress calls — SelectorEventLoop's
        # WSASelect stalls when 750+ IPs are added in parallel.
        # PyInstaller frozen bundles have issues with ProactorEventLoop in
        # non-main threads, so fall back to SelectorEventLoop only there.
        import sys as _sys
        if hasattr(asyncio, "ProactorEventLoop") and not getattr(_sys, "frozen", False):
            loop = asyncio.ProactorEventLoop()
        elif hasattr(asyncio, "SelectorEventLoop"):
            loop = asyncio.SelectorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def _suppress_win_connection_reset(loop, context):
            exc = context.get("exception")
            # WinError 10054: remote host forcibly closed connection during
            # ProactorEventLoop cleanup — spurious Windows noise, not a real error.
            if isinstance(exc, ConnectionResetError):
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(_suppress_win_connection_reset)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            loop="none",      # we set the loop ourselves above
            log_config=None,  # disable uvicorn's log setup — sys.stdout is None in windowed exe
            timeout_keep_alive=0,  # disable HTTP keep-alive — close socket after each response
        )
        server = uvicorn.Server(config)
        asyncio.get_event_loop().run_until_complete(server.serve())
    except Exception as exc:
        log.exception("REST API server failed to start: %s", exc)

"""
Simulator Orchestrator — central control plane.

Routes:
  GET  /health
  GET  /status                   Overview of all sim containers + DCIM services
  GET  /containers               Docker container list
  POST /containers/{name}/restart
  POST /containers/{name}/stop
  POST /containers/{name}/start
  GET  /containers/{name}/logs
  POST /fault                    Inject fault into one or all sim containers
  DELETE /fault                  Clear faults on one or all sim containers
  POST /profile                  Switch traffic profile
  POST /speed                    Change tick interval
  GET  /scenarios                List available fault scenarios
  POST /scenarios/{name}/run     Run a named fault scenario
  GET  /devices                  List all devices across all sim containers
  POST /trap                     Fire a test trap
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from docker_manager import DockerManager
from fault_scenarios import run_scenario, list_scenarios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("orchestrator")

app = FastAPI(title="DCIM Simulator Orchestrator", version="1.0.0")
docker_mgr = DockerManager()

# Container API base URLs — resolved from env or defaults
_SIM_CONTAINERS = {
    k: v for k, v in {
        "sim-network-a": os.environ.get("SIM_A_URL", "http://sim-network-a:8090"),
        "sim-network-b": os.environ.get("SIM_B_URL", "http://sim-network-b:8090"),
        "sim-network-c": os.environ.get("SIM_C_URL", "http://sim-network-c:8090"),
    }.items()
    if v  # drop empties
}

_TIMEOUT = 10.0


# ─────────────────────────────────────────────────────────────────────────────
#  Request models
# ─────────────────────────────────────────────────────────────────────────────

class FaultRequest(BaseModel):
    fault_type: str
    container: Optional[str] = None     # None → all sim containers
    device_name: Optional[str] = None
    iface_index: Optional[int] = None
    value: Optional[float] = None

class ProfileRequest(BaseModel):
    profile: str
    container: Optional[str] = None

class SpeedRequest(BaseModel):
    tick_interval: float
    container: Optional[str] = None

class TrapRequest(BaseModel):
    container: str
    device_name: str
    trap_type: str = "CPU_HIGH"
    severity: str = "major"
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_containers(name: Optional[str]) -> Dict[str, str]:
    if name and name in _SIM_CONTAINERS:
        return {name: _SIM_CONTAINERS[name]}
    if name:
        raise HTTPException(404, f"Unknown container: {name}")
    return _SIM_CONTAINERS


async def _post_all(
    containers: Dict[str, str],
    endpoint: str,
    body: dict,
    method: str = "POST",
) -> List[dict]:
    results = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = []
        for cname, base_url in containers.items():
            url = base_url + endpoint
            if method == "DELETE":
                tasks.append((cname, client.delete(url)))
            else:
                tasks.append((cname, client.post(url, json=body)))

        for cname, coro in tasks:
            try:
                r = await coro
                results.append({"container": cname, "status": r.status_code, "response": r.json()})
            except Exception as e:
                results.append({"container": cname, "error": str(e)})
    return results


async def _fetch_all(containers: Dict[str, str], endpoint: str) -> List[dict]:
    results = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for cname, base_url in containers.items():
            try:
                r = await client.get(base_url + endpoint)
                results.append({"container": cname, "data": r.json()})
            except Exception as e:
                results.append({"container": cname, "error": str(e)})
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "orchestrator"}


@app.get("/status")
async def status():
    sim_statuses = await _fetch_all(_SIM_CONTAINERS, "/status")
    docker_status = docker_mgr.all_containers_status()
    return {
        "sim_containers": sim_statuses,
        "docker": docker_status,
    }


@app.get("/containers")
def list_containers():
    return docker_mgr.all_containers_status()


@app.post("/containers/{name}/restart")
def restart_container(name: str = Path(...)):
    ok = docker_mgr.restart_container(name)
    if not ok:
        raise HTTPException(404, f"Container not found or restart failed: {name}")
    return {"ok": True, "container": name}


@app.post("/containers/{name}/stop")
def stop_container(name: str = Path(...)):
    ok = docker_mgr.stop_container(name)
    return {"ok": ok, "container": name}


@app.post("/containers/{name}/start")
def start_container(name: str = Path(...)):
    ok = docker_mgr.start_container(name)
    return {"ok": ok, "container": name}


@app.get("/containers/{name}/logs")
def container_logs(name: str = Path(...), tail: int = 200):
    logs = docker_mgr.get_logs(name, tail=tail)
    if not logs and docker_mgr.container_status(name) == "not_found":
        raise HTTPException(404, f"Container not found: {name}")
    return {"container": name, "logs": logs}


@app.get("/containers/{name}/stats")
def container_stats(name: str = Path(...)):
    stats = docker_mgr.get_stats(name)
    if stats is None:
        raise HTTPException(404, f"Container not found: {name}")
    return {"container": name, "stats": stats}


@app.post("/fault")
async def inject_fault(req: FaultRequest):
    containers = _resolve_containers(req.container)
    body = {
        "fault_type":  req.fault_type,
        "device_name": req.device_name,
        "iface_index": req.iface_index,
        "value":       req.value,
    }
    results = await _post_all(containers, "/fault", body)
    return {"ok": True, "results": results}


@app.delete("/fault")
async def clear_faults(container: Optional[str] = None):
    containers = _resolve_containers(container)
    results = await _post_all(containers, "/fault", {}, method="DELETE")
    return {"ok": True, "results": results}


@app.post("/profile")
async def set_profile(req: ProfileRequest):
    containers = _resolve_containers(req.container)
    results = await _post_all(containers, "/profile", {"profile": req.profile})
    return {"ok": True, "results": results}


@app.post("/speed")
async def set_speed(req: SpeedRequest):
    containers = _resolve_containers(req.container)
    results = await _post_all(containers, "/speed", {"tick_interval": req.tick_interval})
    return {"ok": True, "results": results}


@app.get("/scenarios")
def get_scenarios():
    return {"scenarios": list_scenarios()}


@app.post("/scenarios/{name}/run")
async def run_fault_scenario(name: str = Path(...)):
    result = await run_scenario(name, _SIM_CONTAINERS)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Scenario failed"))
    return result


@app.get("/devices")
async def list_all_devices():
    return await _fetch_all(_SIM_CONTAINERS, "/devices")


@app.get("/topology")
async def get_full_topology(container: Optional[str] = None):
    """Return merged topology (nodes + edges with IPs) from all sim containers."""
    containers = _resolve_containers(container)
    all_nodes: list = []
    all_edges: list = []
    seen_ips: set = set()
    seen_edges: set = set()

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for cname, base_url in containers.items():
            try:
                r = await client.get(base_url + "/topology")
                data = r.json()
                for n in data.get("nodes", []):
                    ip = n.get("ip_address", "")
                    if ip and ip not in seen_ips:
                        seen_ips.add(ip)
                        all_nodes.append({**n, "container": cname})
                for e in data.get("edges", []):
                    key = (e["src_ip"], e["dst_ip"])
                    rkey = (e["dst_ip"], e["src_ip"])
                    if key not in seen_edges and rkey not in seen_edges:
                        seen_edges.add(key)
                        all_edges.append({**e, "container": cname})
            except Exception as exc:
                pass  # container offline — skip silently

    return {"nodes": all_nodes, "edges": all_edges}


@app.get("/telemetry")
async def get_telemetry(
    container: Optional[str] = None,
    protocol: Optional[str] = None,
    device: Optional[str] = None,
    limit: int = 100,
):
    """Aggregate gNMI + sFlow telemetry events from one or all sim containers."""
    containers = _resolve_containers(container)
    params: dict = {"limit": limit}
    if protocol:
        params["protocol"] = protocol
    if device:
        params["device"] = device

    results = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for cname, base_url in containers.items():
            try:
                r = await client.get(base_url + "/telemetry", params=params)
                results.append({"container": cname, "data": r.json()})
            except Exception as e:
                results.append({"container": cname, "error": str(e)})
    return results


@app.post("/trap")
async def fire_trap(req: TrapRequest):
    containers = _resolve_containers(req.container)
    body = {
        "device_name": req.device_name,
        "trap_type":   req.trap_type,
        "severity":    req.severity,
        "message":     req.message,
    }
    results = await _post_all(containers, "/trap", body)
    return {"ok": True, "results": results}


# ─────────────────────────────────────────────────────────────────────────────
#  Entry
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("ORCHESTRATOR_PORT", 8099))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

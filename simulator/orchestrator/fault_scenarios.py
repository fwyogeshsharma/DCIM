"""
Pre-built fault scenario recipes.
Each scenario is a list of steps; each step targets a container's fault API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Dict, Any

import httpx

log = logging.getLogger("fault_scenarios")

# Registry: scenario_name → list of steps
SCENARIOS: Dict[str, List[Dict[str, Any]]] = {
    # ── Single-device faults ─────────────────────────────────────────────────
    "cpu_spike_all": [
        {"container": "*", "endpoint": "/fault",
         "body": {"fault_type": "cpu_spike", "value": 95.0}},
    ],
    "memory_pressure": [
        {"container": "*", "endpoint": "/fault",
         "body": {"fault_type": "memory_spike", "value": 88.0}},
    ],
    "clear_all": [
        {"container": "*", "endpoint": "/fault",
         "body": {"fault_type": "clear"}, "method": "DELETE"},
    ],

    # ── Traffic profile changes ───────────────────────────────────────────────
    "peak_load": [
        {"container": "*", "endpoint": "/profile",
         "body": {"profile": "peak"}},
    ],
    "idle_load": [
        {"container": "*", "endpoint": "/profile",
         "body": {"profile": "idle"}},
    ],
    "stress_test": [
        {"container": "*", "endpoint": "/profile",
         "body": {"profile": "stress"}},
        {"container": "*", "endpoint": "/fault",
         "body": {"fault_type": "cpu_spike", "value": 98.0}},
    ],

    # ── Network-A specific cascading failure ─────────────────────────────────
    "cascade_failure_network_a": [
        # Step 1: core switch link down
        {"container": "sim-network-a", "endpoint": "/fault",
         "body": {"fault_type": "link_down", "device_name": "core-sw-01", "iface_index": 0}},
        {"delay_sec": 5},
        # Step 2: spike CPU on servers due to rerouting
        {"container": "sim-network-a", "endpoint": "/fault",
         "body": {"fault_type": "cpu_spike", "value": 85.0}},
        {"delay_sec": 10},
        # Step 3: restore
        {"container": "sim-network-a", "endpoint": "/fault",
         "body": {"fault_type": "link_up", "device_name": "core-sw-01", "iface_index": 0}},
        {"container": "sim-network-a", "endpoint": "/fault",
         "body": {"fault_type": "clear"}, "method": "DELETE"},
    ],

    # ── Spine-leaf partial failure ────────────────────────────────────────────
    "spine_failure_network_b": [
        {"container": "sim-network-b", "endpoint": "/fault",
         "body": {"fault_type": "device_down", "device_name": "spine-01"}},
        {"delay_sec": 30},
        {"container": "sim-network-b", "endpoint": "/fault",
         "body": {"fault_type": "device_up", "device_name": "spine-01"}},
    ],

    # ── WAN degradation ──────────────────────────────────────────────────────
    "wan_degradation_network_c": [
        {"container": "sim-network-c", "endpoint": "/profile",
         "body": {"profile": "peak"}},
        {"container": "sim-network-c", "endpoint": "/speed",
         "body": {"tick_interval": 10}},
        {"delay_sec": 60},
        {"container": "sim-network-c", "endpoint": "/profile",
         "body": {"profile": "normal"}},
        {"container": "sim-network-c", "endpoint": "/speed",
         "body": {"tick_interval": 60}},
    ],
}


async def run_scenario(
    scenario_name: str,
    container_urls: Dict[str, str],
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """
    Execute a named scenario.

    container_urls: {"sim-network-a": "http://...:8090", ...}
    """
    steps = SCENARIOS.get(scenario_name)
    if steps is None:
        return {"ok": False, "error": f"Unknown scenario: {scenario_name}"}

    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for step in steps:
            # Pure delay step
            if "delay_sec" in step:
                await asyncio.sleep(step["delay_sec"])
                results.append({"delay": step["delay_sec"]})
                continue

            target   = step["container"]
            endpoint = step["endpoint"]
            body     = step.get("body", {})
            method   = step.get("method", "POST").upper()

            # Resolve target containers ("*" = all)
            if target == "*":
                targets = list(container_urls.keys())
            elif target in container_urls:
                targets = [target]
            else:
                results.append({"container": target, "error": "not found in urls"})
                continue

            for cname in targets:
                url = container_urls[cname] + endpoint
                try:
                    if method == "DELETE":
                        r = await client.delete(url)
                    else:
                        r = await client.post(url, json=body)
                    results.append({
                        "container": cname,
                        "endpoint":  endpoint,
                        "status":    r.status_code,
                        "response":  r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text,
                    })
                except Exception as e:
                    results.append({"container": cname, "endpoint": endpoint, "error": str(e)})

    return {"ok": True, "scenario": scenario_name, "steps": results}


def list_scenarios() -> List[str]:
    return list(SCENARIOS.keys())

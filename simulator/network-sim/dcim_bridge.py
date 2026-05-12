"""
DCIM Bridge — translates DeviceStateStore metrics into DCIM Server HTTP API calls.

Runs as a background asyncio task inside the network simulator container.
Responsibilities:
  1. Register all server devices as DCIM agents on startup
  2. POST /api/v1/metrics every tick (all device types → cpu, memory, temp…)
  3. POST /api/v1/snmp-metrics for network devices (interface counters)
  4. POST /api/v1/alerts when RuleEngine fires a TrapAction
  5. Maintain an in-memory telemetry log (gNMI-style events + sFlow interface events)
     accessible via GET /telemetry on the http_api.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Deque, Dict, List, Optional

import httpx

if TYPE_CHECKING:
    from core.device_manager import Device, DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.device_manager import DeviceType

log = logging.getLogger(__name__)

# Severity map: simulator levels → DCIM server levels (must be uppercase)
_SEVERITY_MAP = {
    "critical":      "CRITICAL",
    "major":         "WARNING",
    "minor":         "INFO",
    "informational": "INFO",
    "warning":       "WARNING",
    "info":          "INFO",
}

# Maximum telemetry log entries kept in memory per protocol
_MAX_LOG = 500


class TelemetryLog:
    """Thread-safe in-memory ring buffer of telemetry events."""

    def __init__(self, maxlen: int = _MAX_LOG):
        self._gnmi:  Deque[dict] = deque(maxlen=maxlen)
        self._sflow: Deque[dict] = deque(maxlen=maxlen)

    def append_gnmi(self, entry: dict):
        self._gnmi.append(entry)

    def append_sflow(self, entry: dict):
        self._sflow.append(entry)

    def get_gnmi(self, device: Optional[str] = None, limit: int = 100) -> List[dict]:
        entries = list(self._gnmi)
        if device:
            entries = [e for e in entries if e.get("device") == device]
        return list(reversed(entries))[:limit]

    def get_sflow(self, device: Optional[str] = None, limit: int = 100) -> List[dict]:
        entries = list(self._sflow)
        if device:
            entries = [e for e in entries if e.get("device") == device]
        return list(reversed(entries))[:limit]


class DCIMBridge:
    def __init__(
        self,
        dcim_url: str,
        network_id: str,
        device_manager: "DeviceManager",
        state_store: "DeviceStateStore",
        tick_interval: float = 30.0,
    ):
        self._url        = dcim_url.rstrip("/")
        self._net        = network_id
        self._dm         = device_manager
        self._store      = state_store
        self._interval   = tick_interval
        self._client:    Optional[httpx.AsyncClient] = None
        self._running    = False
        self._task:      Optional[asyncio.Task] = None

        # Alert queue (filled by rule engine callback, drained by sync loop)
        self._alert_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # In-memory telemetry log (gNMI events + sFlow events)
        self.telemetry = TelemetryLog()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self):
        self._client  = httpx.AsyncClient(timeout=10.0)
        self._running = True
        self._loop    = asyncio.get_running_loop()
        await self._register_all_agents()
        self._task = asyncio.create_task(self._sync_loop())
        log.info("[DCIMBridge] Started → %s (network=%s)", self._url, self._net)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    # ── Alert callback (called from rule engine thread) ───────────────────

    def on_alert(self, device: "Device", trap_type, severity: str,
                 details: str = "", rule_name: str = "",
                 iface_index: Optional[int] = None):
        """Thread-safe: put an alert into the queue for the async loop."""
        if self._loop and self._loop.is_running():
            ts = datetime.now(timezone.utc).isoformat()
            metric_type = rule_name or str(trap_type).split(".")[-1]
            alert = {
                # Per-Alert fields expected by DCIM Server models.Alert struct
                "metric_type": metric_type,
                "severity":    _SEVERITY_MAP.get(severity.lower(), "INFO"),
                "message":     details or f"{trap_type} on {device.name}",
                "value":       float(iface_index) if iface_index is not None else 0.0,
                "threshold":   0.0,
                "timestamp":   ts,
                # Kept for internal routing only (stripped before POST)
                "_agent_id":   self._agent_id(device),
            }
            asyncio.run_coroutine_threadsafe(
                self._alert_queue.put(alert), self._loop
            )

    # ── Internal ──────────────────────────────────────────────────────────

    def _agent_id(self, device: "Device") -> str:
        return f"{self._net}-{device.name}"

    async def _register_all_agents(self):
        devices = self._dm.get_all_devices()
        registered = 0
        for device in devices:
            payload = {
                "agent_id":   self._agent_id(device),
                "hostname":   device.name,
                "ip_address": device.ip_address or "0.0.0.0",
            }
            try:
                r = await self._client.post(
                    f"{self._url}/register",
                    json=payload,
                    headers={"X-Agent-ID": payload["agent_id"]},
                )
                if r.status_code in (200, 201):
                    registered += 1
            except Exception as e:
                log.debug("Register %s failed: %s", device.name, e)
        log.info("[DCIMBridge] Registered %d/%d agents", registered, len(devices))

    async def _sync_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self._send_all_metrics()
                await self._flush_alerts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("[DCIMBridge] sync error: %s", e)
                await asyncio.sleep(5)

    async def _send_all_metrics(self):
        devices = self._dm.get_all_devices()
        ts = datetime.now(timezone.utc).isoformat()
        sent = 0

        for device in devices:
            metrics = self._store.get_metrics(device.ip_address)
            if not metrics:
                continue

            agent_id = self._agent_id(device)

            # ── System metrics (all device types) ─────────────────────────
            metric_list = []

            cpu = metrics.get("cpu_usage")
            if cpu is not None:
                metric_list.append({"metric_type": "cpu_usage", "value": round(float(cpu), 2), "unit": "%", "timestamp": ts})

            mem_pct = metrics.get("memory_pct")
            if mem_pct is None:
                total = metrics.get("memory_total", 1) or 1
                used  = metrics.get("memory_used", 0) or 0
                mem_pct = (used / total) * 100
            metric_list.append({"metric_type": "memory_usage", "value": round(float(mem_pct), 2), "unit": "%", "timestamp": ts})

            disk_pct = metrics.get("disk_pct")
            if disk_pct is None:
                total = metrics.get("disk_total", 1) or 1
                used  = metrics.get("disk_used", 0) or 0
                disk_pct = (used / total) * 100
            metric_list.append({"metric_type": "disk_usage", "value": round(float(disk_pct), 2), "unit": "%", "timestamp": ts})

            temp = metrics.get("cpu_temp")
            if temp is not None:
                metric_list.append({"metric_type": "temperature", "value": round(float(temp), 1), "unit": "C", "timestamp": ts})

            inlet = metrics.get("inlet_temp")
            if inlet is not None:
                metric_list.append({"metric_type": "inlet_temperature", "value": round(float(inlet), 1), "unit": "C", "timestamp": ts})

            power = metrics.get("power_draw_w") or getattr(device, "power_draw_w", None)
            if power:
                metric_list.append({"metric_type": "power_consumption", "value": round(float(power), 1), "unit": "W", "timestamp": ts})

            uptime = metrics.get("sys_uptime")
            if uptime is not None:
                metric_list.append({"metric_type": "uptime", "value": int(uptime) // 100, "unit": "s", "timestamp": ts})

            # Interface bandwidth (sum all interfaces)
            ifaces_dict = metrics.get("interfaces", {})
            iface_values = list(ifaces_dict.values()) if isinstance(ifaces_dict, dict) else []
            if iface_values:
                rx = sum(i.get("in_octets",  0) for i in iface_values) * 8 / self._interval / 1e6
                tx = sum(i.get("out_octets", 0) for i in iface_values) * 8 / self._interval / 1e6
                metric_list.append({"metric_type": "network_rx", "value": round(rx, 3), "unit": "Mbps", "timestamp": ts})
                metric_list.append({"metric_type": "network_tx", "value": round(tx, 3), "unit": "Mbps", "timestamp": ts})

            # Environmental (sensors)
            humidity = metrics.get("humidity")
            if humidity is not None:
                metric_list.append({"metric_type": "humidity", "value": round(float(humidity), 1), "unit": "%", "timestamp": ts})

            dewpoint = metrics.get("dewpoint")
            if dewpoint is not None:
                metric_list.append({"metric_type": "dewpoint", "value": round(float(dewpoint), 1), "unit": "C", "timestamp": ts})

            airflow = metrics.get("airflow")
            if airflow is not None:
                metric_list.append({"metric_type": "airflow", "value": round(float(airflow), 2), "unit": "m/s", "timestamp": ts})

            if not metric_list:
                continue

            payload = {
                "agent_id":  agent_id,
                "timestamp": ts,
                "metrics":   metric_list,
            }
            try:
                r = await self._client.post(
                    f"{self._url}/metrics",
                    json=payload,
                    headers={"X-Agent-ID": agent_id},
                )
                if r.status_code < 300:
                    sent += 1
                    # Log as gNMI telemetry event
                    self.telemetry.append_gnmi({
                        "timestamp":     ts,
                        "device":        device.name,
                        "agent_id":      agent_id,
                        "protocol":      "gNMI",
                        "event":         "telemetry_update",
                        "path":          "/system/state",
                        "metrics_count": len(metric_list),
                        "values": {m["metric_type"]: {"value": m["value"], "unit": m["unit"]} for m in metric_list},
                    })
            except Exception as e:
                log.debug("metrics POST failed for %s: %s", device.name, e)

            # ── SNMP interface metrics (network devices only) ──────────────
            await self._send_snmp_metrics(device, agent_id, ts, ifaces_dict, iface_values)

        log.debug("[DCIMBridge] Sent metrics for %d devices", sent)

    async def _send_snmp_metrics(self, device, agent_id, ts, ifaces_dict, iface_values):
        if not iface_values:
            return
        snmp_metrics = []
        iface_names = list(ifaces_dict.keys()) if isinstance(ifaces_dict, dict) else []

        for i, iface_data in enumerate(iface_values):
            iface_name = iface_names[i] if i < len(iface_names) else f"eth{i}"
            snmp_metrics.append({
                "device_name":  device.name,
                "device_ip":    device.ip_address or "",
                "metric_name":  f"ifInOctets.{i+1}",
                "metric_value": iface_data.get("in_octets", 0),
                "metric_type":  "counter64",
            })
            snmp_metrics.append({
                "device_name":  device.name,
                "device_ip":    device.ip_address or "",
                "metric_name":  f"ifOutOctets.{i+1}",
                "metric_value": iface_data.get("out_octets", 0),
                "metric_type":  "counter64",
            })
            snmp_metrics.append({
                "device_name":  device.name,
                "device_ip":    device.ip_address or "",
                "metric_name":  f"ifOperStatus.{i+1}",
                "metric_value": iface_data.get("oper_status", 1),
                "metric_type":  "integer",
            })

            # Log as sFlow event for each interface with traffic
            in_oct  = iface_data.get("in_octets", 0)
            out_oct = iface_data.get("out_octets", 0)
            if in_oct > 0 or out_oct > 0:
                self.telemetry.append_sflow({
                    "timestamp":   ts,
                    "device":      device.name,
                    "agent_id":    agent_id,
                    "protocol":    "sFlow",
                    "event":       "flow_sample",
                    "interface":   iface_name,
                    "if_index":    i + 1,
                    "in_octets":   in_oct,
                    "out_octets":  out_oct,
                    "oper_status": iface_data.get("oper_status", 1),
                    "in_mbps":     round(in_oct * 8 / self._interval / 1e6, 3),
                    "out_mbps":    round(out_oct * 8 / self._interval / 1e6, 3),
                })

        if not snmp_metrics:
            return
        payload = {
            "agent_id":    agent_id,
            "timestamp":   ts,
            "snmp_metrics": snmp_metrics,
        }
        try:
            await self._client.post(
                f"{self._url}/snmp-metrics",
                json=payload,
                headers={"X-Agent-ID": agent_id},
            )
        except Exception as e:
            log.debug("snmp-metrics POST failed for %s: %s", device.name, e)

    async def _flush_alerts(self):
        alerts = []
        while not self._alert_queue.empty():
            try:
                alerts.append(self._alert_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not alerts:
            return

        # Group by agent_id (stored as _agent_id, strip before sending)
        by_agent: dict = {}
        for a in alerts:
            aid = a.pop("_agent_id", None) or "unknown"
            by_agent.setdefault(aid, []).append(a)

        for agent_id, agent_alerts in by_agent.items():
            payload = {
                "agent_id":  agent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "alerts":    agent_alerts,
            }
            try:
                await self._client.post(
                    f"{self._url}/alerts",
                    json=payload,
                    headers={"X-Agent-ID": agent_id},
                )
                log.info("[DCIMBridge] Sent %d alert(s) for %s",
                         len(agent_alerts), agent_id)
            except Exception as e:
                log.warning("[DCIMBridge] alerts POST failed: %s", e)

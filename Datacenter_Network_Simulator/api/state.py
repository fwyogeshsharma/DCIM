"""
Shared application state between Qt UI and REST API.
MainWindow registers core objects here after initialization.
"""
from __future__ import annotations

import queue as _queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.device_manager import DeviceManager
    from core.topology_engine import TopologyEngine
    from core.ip_manager import IPManager
    from core.device_state_store import DeviceStateStore
    from simulator.snmpsim_controller import SNMPSimController
    from simulator.gnmi_controller import GNMIController
    from core.rule_engine import RuleEngine
    from core.trap_engine import TrapEngine


@dataclass
class JobStatus:
    job_id: str
    operation: str
    status: str = "pending"        # pending | running | completed | failed
    progress_done: int = 0
    progress_total: int = 0
    message: str = ""
    error: str = ""
    result: Any = None
    started_at: str = ""
    finished_at: str = ""


class AppState:
    _instance: Optional["AppState"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self):
        # Core objects — set by MainWindow.register()
        self.device_manager: Optional["DeviceManager"] = None
        self.topology: Optional["TopologyEngine"] = None
        self.ip_manager: Optional["IPManager"] = None
        self.snmpsim: Optional["SNMPSimController"] = None
        self.gnmi: Optional["GNMIController"] = None
        self.sflow:   Optional[Any] = None  # SFlowController (set by MainWindow)
        self.bacnet:  Optional[Any] = None  # BACnetController (set by MainWindow)
        self.state_store: Optional["DeviceStateStore"] = None
        self.rule_engine: Optional["RuleEngine"] = None
        self.trap_engine: Optional["TrapEngine"] = None
        self.snmp_set_agent: Optional[Any] = None

        # Binding state (mirrors MainWindow's _bound_ips etc.)
        self.selected_adapter: str = ""
        self.subnet_mask: str = "255.255.255.0"
        self.bound_ips: List[str] = []
        self.nte_contexts: Dict[str, Any] = {}
        self.gnmi_bound_ips: List[str] = []
        self.gnmi_nte_contexts: Dict[str, Any] = {}

        # Dataset generation state
        self.generated_snmp_files: List[str] = []
        self.generated_gnmi_files: List[str] = []
        self.snmp_datasets_dir: str = "datasets/snmp"
        self.gnmi_datasets_dir: str = "datasets/gnmi"

        # Trap receiver config
        self.trap_receiver_ip: str = "127.0.0.1"
        self.trap_receiver_port: int = 162
        self.rule_engine_enabled: bool = False

        # Trap history (ring buffer — last 1000)
        self.trap_history: List[dict] = []
        self._trap_history_limit: int = 1000

        # Current topology file path
        self.current_topology_path: str = ""

        # Registered by MainWindow — the same queue drained every 150 ms on the main thread.
        self._ui_queue: Optional[Any] = None  # queue.Queue

        # SSE clients: list of queue.Queue, one per connected web browser tab
        self._sse_clients: List[_queue.Queue] = []

        # Background jobs
        self.jobs: Dict[str, JobStatus] = {}
        self._state_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-worker")

    @classmethod
    def get(cls) -> "AppState":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    def register(
        self,
        device_manager=None,
        topology=None,
        ip_manager=None,
        snmpsim=None,
        gnmi=None,
        sflow=None,
        bacnet=None,
        state_store=None,
        rule_engine=None,
        trap_engine=None,
        snmp_datasets_dir: str = "datasets/snmp",
        gnmi_datasets_dir: str = "datasets/gnmi",
    ):
        self.device_manager = device_manager
        self.topology = topology
        self.ip_manager = ip_manager
        self.snmpsim = snmpsim
        self.gnmi = gnmi
        self.sflow = sflow
        self.bacnet = bacnet
        self.state_store = state_store
        self.rule_engine = rule_engine
        self.trap_engine = trap_engine
        self.snmp_datasets_dir = snmp_datasets_dir
        self.gnmi_datasets_dir = gnmi_datasets_dir

    def create_job(self, operation: str) -> str:
        job_id = str(uuid.uuid4())
        job = JobStatus(
            job_id=job_id,
            operation=operation,
            status="running",
            started_at=datetime.utcnow().isoformat(),
        )
        with self._state_lock:
            self.jobs[job_id] = job
        return job_id

    def update_job(self, job_id: str, **kwargs):
        with self._state_lock:
            job = self.jobs.get(job_id)
            if job:
                for k, v in kwargs.items():
                    setattr(job, k, v)

    def get_job(self, job_id: str) -> Optional[JobStatus]:
        return self.jobs.get(job_id)

    def record_trap(self, event):
        """Called when trap_engine emits trap_sent signal."""
        defn = getattr(event, "defn", None)
        record = {
            "timestamp": event.timestamp.isoformat() if hasattr(event.timestamp, "isoformat") else str(event.timestamp),
            "device_id": event.device.id if event.device else None,
            "device_name": event.device.name if event.device else None,
            "device_ip": (getattr(event.device, "mgmt_ip", None) or None) if event.device else None,
            "trap_type": event.trap_type.name if event.trap_type else None,
            "display_name": defn.display_name if defn else None,
            "severity": defn.severity if defn else None,
            "details": event.details,
            "rule_name": event.rule_name or "",
            "iface_index": event.iface_index,
        }
        with self._state_lock:
            self.trap_history.append(record)
            if len(self.trap_history) > self._trap_history_limit:
                self.trap_history = self.trap_history[-self._trap_history_limit:]
        self.notify_ui("sync_traps")

    def require_core(self):
        """Raise RuntimeError if core objects not yet registered."""
        if self.device_manager is None:
            raise RuntimeError("App state not initialized — start the simulator first")

    def start_ticker_if_needed(self):
        """Start the metrics ticker when any simulator becomes active."""
        if self.state_store and not self.state_store.is_running():
            self.state_store.start()

    def stop_ticker_if_idle(self):
        """Stop the metrics ticker only when ALL simulators are stopped."""
        if self.state_store is None or not self.state_store.is_running():
            return
        snmp_on   = bool(self.snmpsim and self.snmpsim.is_running())
        gnmi_on   = bool(self.gnmi    and self.gnmi.is_running())
        sflow_on  = bool(self.sflow   and self.sflow.is_running())
        bacnet_on = bool(self.bacnet  and self.bacnet.is_running())
        if not snmp_on and not gnmi_on and not sflow_on and not bacnet_on:
            self.state_store.stop()

    def get_all_bind_ips(self) -> List[str]:
        """Return all IPs that should be bound (production + mgmt, deduplicated)."""
        if not self.device_manager:
            return []
        devs = self.device_manager.get_all_devices()
        has_mgmt = any(d.mgmt_ip for d in devs if hasattr(d, "mgmt_ip"))
        seen: set = set()
        ips: List[str] = []
        for d in devs:
            candidates = [d.ip_address]
            if has_mgmt and hasattr(d, "mgmt_ip") and d.mgmt_ip:
                candidates.append(d.mgmt_ip)
            for ip in candidates:
                if ip and ip not in seen:
                    ips.append(ip)
                    seen.add(ip)
        return ips

    def add_sse_client(self, q: _queue.Queue):
        with self._state_lock:
            self._sse_clients.append(q)

    def remove_sse_client(self, q: _queue.Queue):
        with self._state_lock:
            try:
                self._sse_clients.remove(q)
            except ValueError:
                pass

    def _broadcast_sse(self, payload: dict):
        """Broadcast an event dict to all connected SSE clients. Thread-safe."""
        with self._state_lock:
            clients = list(self._sse_clients)
        for q in clients:
            try:
                q.put_nowait(payload)
            except _queue.Full:
                pass

    def notify_ui(self, event: str, *args):
        """Post a UI-sync event to the main-thread drain queue. Thread-safe."""
        if self._ui_queue is not None:
            try:
                self._ui_queue.put_nowait((event, *args))
            except Exception:
                pass
        # Also broadcast to SSE clients so the web UI reacts in real time
        self._broadcast_sse(self._sse_event(event, *args))

    @staticmethod
    def _sse_event(event: str, *args) -> dict:
        if event in ("log", "log_sflow", "log_gnmi", "log_bacnet", "console_log") and len(args) >= 1:
            msg = args[0]
            level = args[1] if len(args) > 1 else "info"
            if event == "log_sflow":
                tab = "sflow"
            elif event == "log_gnmi":
                tab = "gnmi"
            elif event == "log_bacnet":
                tab = "bacnet"
            else:
                tab = "snmp"
                if isinstance(msg, str) and "[gNMI]" in msg:
                    tab = "gnmi"
                elif isinstance(msg, str) and "[sFlow]" in msg:
                    tab = "sflow"
                elif isinstance(msg, str) and "[BACnet]" in msg:
                    tab = "bacnet"
            return {"type": "log", "tab": tab, "msg": str(msg), "level": str(level)}
        if event in ("snmp_progress", "gnmi_progress", "binding_progress"):
            done = args[0] if len(args) > 0 else 0
            total = args[1] if len(args) > 1 else 0
            op = "snmp" if "snmp" in event else ("gnmi" if "gnmi" in event else "binding")
            return {"type": "progress", "operation": op, "done": done, "total": total}
        if event == "status" and args:
            return {"type": "status", "msg": str(args[0])}
        if event.startswith("sync_"):
            target = event[5:]
            return {"type": "sync", "target": target}
        if event == "rebuild_topology_scene":
            return {"type": "sync", "target": "topology"}
        if event == "link_changed" and len(args) >= 3:
            return {"type": "link_changed", "src": args[0], "dst": args[1], "broken": args[2]}
        return {"type": event, "args": [str(a) for a in args]}
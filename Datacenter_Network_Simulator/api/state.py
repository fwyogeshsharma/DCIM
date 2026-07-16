"""
Shared application state between Qt UI and REST API.
MainWindow registers core objects here after initialization.
"""
from __future__ import annotations

import queue as _queue
import threading
import uuid
from pathlib import Path as _Path
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
        self.redfish: Optional[Any] = None  # RedfishController (set by MainWindow)
        self.state_store: Optional["DeviceStateStore"] = None
        self.rule_engine: Optional["RuleEngine"] = None
        self.trap_engine: Optional["TrapEngine"] = None
        self.snmp_set_agent: Optional[Any] = None
        self.fleet_engine: Optional[Any] = None  # FleetLifecycleEngine (lazy)

        # Binding state (mirrors MainWindow's _bound_ips etc.)
        self.selected_adapter: str = ""
        # /23 covers the OOB mgmt range 192.168.0.0/23 (PDUs span .0.x and .1.x);
        # a single mask is applied to every bound alias (see binding.bind_ips).
        self.subnet_mask: str = "255.255.254.0"
        self.bound_ips: List[str] = []
        self.nte_contexts: Dict[str, Any] = {}
        self.gnmi_bound_ips: List[str] = []
        self.gnmi_nte_contexts: Dict[str, Any] = {}

        # Dataset generation state
        self.generated_snmp_files: List[str] = []
        # How many datasets this topology expects but disk does not have. Set by
        # reconcile_generated_datasets so the caller can say WHY it adopted none.
        self._missing_datasets: int = 0
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
        # Uploaded floor-plan doc that overrides the live build (None = live).
        self.uploaded_floorplan: Optional[dict] = None

        # Registered by MainWindow — the same queue drained every 150 ms on the main thread.
        self._ui_queue: Optional[Any] = None  # queue.Queue

        # SSE clients: list of queue.Queue, one per connected web browser tab
        self._sse_clients: List[_queue.Queue] = []

        # Background jobs
        self.jobs: Dict[str, JobStatus] = {}
        self._state_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-worker")
        # Guards SNMP-sim hot-reloads so concurrent triggers (button + fleet
        # day) coalesce into one bounce instead of stacking restarts.
        self._snmp_reload_lock = threading.Lock()
        self._snmp_reloading = False

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
        redfish=None,
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
        self.redfish = redfish
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
            # IP of the agent that fired the trap: server OS traps → prod IP,
            # BMC platform events → mgmt IP, NOS/UPS/PDU/sensor → mgmt IP.
            "device_ip": getattr(event, "source_ip", None)
                         or ((getattr(event.device, "mgmt_ip", None)
                              or getattr(event.device, "ip_address", None))
                             if event.device else None),
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
        snmp_on    = bool(self.snmpsim and self.snmpsim.is_running())
        gnmi_on    = bool(self.gnmi    and self.gnmi.is_running())
        sflow_on   = bool(self.sflow   and self.sflow.is_running())
        bacnet_on  = bool(self.bacnet  and self.bacnet.is_running())
        redfish_on = bool(self.redfish and self.redfish.is_running())
        if not (snmp_on or gnmi_on or sflow_on or bacnet_on or redfish_on):
            self.state_store.stop()

    def reload_snmp(self, log_cb=None) -> bool:
        """Hot-reload the SNMP simulator so devices added after it started (e.g.
        Fleet Lifecycle churn) become pollable. snmpsim indexes its data-dir at
        process start, so it only serves the new .snmprec files after a bounce.
        Host-binds any not-yet-bound device IPs, then restarts snmpsim with the
        current full device-IP list. Coalesced via _snmp_reload_lock so the Reload
        button and the per-day fleet trigger never stack restarts. Returns True on
        a successful (re)start, False if skipped/failed."""
        log = log_cb or (lambda *_a, **_k: None)
        if not (self.snmpsim and self.snmpsim.is_running()):
            return False
        with self._snmp_reload_lock:
            if self._snmp_reloading:
                log("SNMP reload already in progress — skipped")
                return False
            self._snmp_reloading = True
        try:
            from core.snmprec_generator import SNMPRecGenerator as _Gen
            seen: set = set()
            device_ips: List[str] = []
            for d in self.topology.get_all_devices():
                for ip in _Gen.snmp_bind_ips(d):
                    if ip not in seen:
                        seen.add(ip)
                        device_ips.append(ip)
            # Host-bind any IPs added since the last bind (new churned devices).
            bound_set = set(self.bound_ips or [])
            missing = [ip for ip in device_ips if ip not in bound_set]
            if missing and self.selected_adapter:
                from core.ip_binder import add_ips_fast, is_admin
                if is_admin():
                    log(f"Binding {len(missing)} new IP(s)...")
                    bound, _ctx = add_ips_fast(
                        self.selected_adapter, missing, self.subnet_mask,
                        log_cb=lambda m, _l=None: log(m))
                    self.bound_ips = list(bound_set | set(bound))
                else:
                    log("not admin — skipping host IP bind (new SNMP IPs unreachable)")
            # Reuse the port snmpsim is currently serving on. Ask the controller
            # rather than string-splitting an endpoint label: that list is empty
            # whenever the sim is not ready, and the 161 fallback would silently
            # move a running sim off the port the operator chose.
            port = self.snmpsim.get_port() or 161
            if self.state_store:
                self.state_store.disable_snmp_sync()
            log("Restarting SNMP simulator...")
            self.snmpsim.stop()
            ok = self.snmpsim.start(device_ips, port=port)
            if ok and self.state_store:
                self.state_store.enable_snmp_sync(self.snmpsim)
            self.notify_ui("sync_snmp")
            log(f"SNMP reloaded — {len(device_ips)} agent(s)" if ok
                else "SNMP reload failed — snmpsim.start() returned False")
            return ok
        finally:
            self._snmp_reloading = False

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

    def reconcile_bound_ips(self) -> int:
        """Adopt device IPs the OS already has aliased, and return how many.

        Binding puts IP aliases on a real interface; those aliases OUTLIVE this
        process. bound_ips does not — it is rebuilt empty on every start. So after
        a restart the OS still has every alias while the app believes nothing is
        bound, and /redfish/start refuses with "none have a bound IP" at a user who
        did bind, correctly, before the restart. The only fix is to ask the OS
        instead of trusting our own memory.

        Only IPs this topology would itself bind are adopted (get_all_bind_ips) —
        never every address on the box, or we would claim the host's own IP and
        hand it to unbind. Cheap on the surface (one `ip addr` per interface) but
        it shells out, so this is called on topology load, not per status poll.

        nte_contexts stays empty: it exists only to speed up unbind, Linux ignores
        it outright, and the Windows path already falls back to netsh without it.
        """
        want = set(self.get_all_bind_ips())
        if not want:
            return 0
        try:
            from core.ip_binder import get_interfaces, get_interface_ips
        except Exception:
            return 0
        found: Dict[str, List[str]] = {}
        for name, _label in get_interfaces():
            try:
                hit = [ip for ip in get_interface_ips(name) if ip in want]
            except Exception:
                continue
            if hit:
                found[name] = hit
        if not found:
            return 0
        adopted = sorted({ip for ips in found.values() for ip in ips})
        self.bound_ips = adopted
        # Point the adapter at wherever the aliases actually live, so a later
        # unbind removes them from the right interface. A choice the user already
        # made and the OS still reflects — not a new one being made for them.
        if not self.selected_adapter:
            self.selected_adapter = max(found, key=lambda n: len(found[n]))
        return len(adopted)

    def reconcile_generated_datasets(self) -> int:
        """Adopt .snmprec datasets already on disk for THIS topology, or none.

        generated_snmp_files is rebuilt empty on every start, but the datasets it
        tracks live on disk and outlive the process — so after a restart the app
        demands a full regeneration of hundreds of files it already has, and
        /snmp/start refuses with "No datasets generated" at an operator looking at
        a directory full of them. Same shape as reconcile_bound_ips().

        ALL-OR-NOTHING, deliberately. A partial adoption would let snmpsim start
        with some devices served from disk and the rest silent — worse than asking
        for a regeneration, because it looks healthy. Every dataset this topology
        expects (OS agent + BMC agent, per snmp_bind_ips) must be present or we
        adopt nothing.

        WHAT THIS CANNOT SEE: a file matching by NAME is not proof its CONTENT is
        current. Re-seat a switch's ports or resize a device and the filenames are
        unchanged while the contents are stale. The caller logs loudly for exactly
        this reason — adoption saves a rebuild, it does not certify freshness.
        """
        if self.topology is None or self.device_manager is None:
            return 0
        try:
            from core.snmprec_generator import SNMPRecGenerator
        except Exception:
            return 0
        ddir = _Path(self.snmp_datasets_dir)
        if not ddir.is_dir():
            return 0
        expected: set = set()
        owners: List[str] = []          # the per-device file generate_device returns
        for dev in self.topology.get_all_devices():
            ips = SNMPRecGenerator.snmp_bind_ips(dev)
            if not ips:
                continue                # no SNMP agent (BACnet/Modbus plant gear)
            for ip in ips:
                expected.add(f"{ip}.snmprec")
            addr = SNMPRecGenerator.snmp_address(dev)
            if addr:
                owners.append(str(ddir / f"{addr}.snmprec"))
        if not expected:
            return 0
        have = {p.name for p in ddir.glob("*.snmprec")}
        missing = expected - have
        if missing:
            self._missing_datasets = len(missing)
            return 0
        self._missing_datasets = 0
        self.generated_snmp_files = owners
        return len(owners)

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
        if event in ("log", "log_sflow", "log_gnmi", "log_bacnet", "log_redfish", "console_log") and len(args) >= 1:
            msg = args[0]
            level = args[1] if len(args) > 1 else "info"
            if event == "log_sflow":
                tab = "sflow"
            elif event == "log_gnmi":
                tab = "gnmi"
            elif event == "log_bacnet":
                tab = "bacnet"
            elif event == "log_redfish":
                tab = "redfish"
            else:
                tab = "snmp"
                if isinstance(msg, str) and "[gNMI]" in msg:
                    tab = "gnmi"
                elif isinstance(msg, str) and "[sFlow]" in msg:
                    tab = "sflow"
                elif isinstance(msg, str) and "[BACnet]" in msg:
                    tab = "bacnet"
                elif isinstance(msg, str) and "[Redfish]" in msg:
                    tab = "redfish"
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
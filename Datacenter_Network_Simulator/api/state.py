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

        # Durable session store (SQLite). Snapshots state on change so a host
        # restart (the GCP VM reboots daily at 04:30) doesn't wipe everything.
        # Never fatal: if the store can't open, the app runs stateless.
        try:
            from core.persistence import SessionStore
            self.session_store = SessionStore()
        except Exception:  # pragma: no cover — defensive, store self-guards too
            self.session_store = None
        # Set once startup re-binding has finished (or was skipped). Phase 3
        # simulator restore waits on this so sims that need bound IPs (SNMP,
        # gNMI) don't start before the addresses exist on the adapter.
        self._binding_restored = threading.Event()
        # True when Redfish was running before a restart but couldn't be
        # auto-started because its password is deliberately NOT persisted — the
        # UI surfaces this to prompt the operator to re-enter it. Surfaced via
        # GET /redfish/status.
        self.redfish_needs_password: bool = False
        # In-memory ring of recent console log lines. Fed by notify_ui (cheap,
        # in-process), snapshotted to disk by the periodic flush, restored on
        # boot and replayed to SSE clients on connect. A ring + blob snapshot —
        # not a per-line table — because logs are high-volume and ephemeral;
        # a synchronous DB write per log line would stall the hot path.
        import collections as _collections
        self._log_ring = _collections.deque(maxlen=2000)

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
        if self.session_store:
            self.session_store.append_trap(record, keep=self._trap_history_limit)
        self.notify_ui("sync_traps")

    def require_core(self):
        """Raise RuntimeError if core objects not yet registered."""
        if self.device_manager is None:
            raise RuntimeError("App state not initialized — start the simulator first")

    # ── persistence: topology (Phase 1) ──────────────────────────────────────
    # Topology is not only set by upload — device add/edit/delete, link
    # break/restore/create and fleet churn all mutate the live graph. So we
    # persist the SERIALIZED live topology (to_dict), not the uploaded file, and
    # call this after any structural change. Restore replays it via from_dict.
    def persist_topology(self):
        """Snapshot the current live topology to the session store. No-op if
        persistence is unavailable or no topology is loaded."""
        if not self.session_store or self.topology is None:
            return
        try:
            self.session_store.set_blob("topology", {
                "path": self.current_topology_path,
                "data": self.topology.to_dict(),
            })
        except Exception:
            # Persistence failures must never break the mutating request.
            import logging
            logging.getLogger("persistence").exception("persist_topology failed")

    def clear_persisted_topology(self):
        """Drop the saved topology (called when the topology is cleared)."""
        if self.session_store:
            self.session_store.delete_blob("topology")

    def restore(self):
        """Replay persisted state into the freshly-started process. Called once
        at startup AFTER core objects are registered. Each slice is best-effort
        and isolated — a failure in one never blocks the others or startup."""
        if not self.session_store:
            return
        import logging
        _log = logging.getLogger("persistence")
        try:
            self._restore_topology(_log)
        except Exception:
            _log.exception("topology restore failed")
        try:
            self._restore_bindings(_log)
        except Exception:
            _log.exception("binding restore failed")
            self._binding_restored.set()  # never leave Phase-3 waiters hung
        # Runtime meters + rules + tick must be restored BEFORE simulators — the
        # sim restore starts the metric ticker, and energy re-seed has to be in
        # place before the first tick runs.
        try:
            self._restore_runtime(_log)
        except Exception:
            _log.exception("runtime restore failed")
        try:
            self._restore_rules(_log)
        except Exception:
            _log.exception("rules restore failed")
        try:
            self._restore_tick(_log)
        except Exception:
            _log.exception("tick restore failed")
        try:
            self._restore_fleet(_log)
        except Exception:
            _log.exception("fleet restore failed")
        try:
            self._restore_traps(_log)
        except Exception:
            _log.exception("traps restore failed")
        try:
            self._restore_console(_log)
        except Exception:
            _log.exception("console restore failed")
        try:
            self._restore_simulators(_log)
        except Exception:
            _log.exception("simulator restore failed")
        # Periodic snapshot of fast-changing runtime state (energy meters).
        self._start_runtime_flusher()

    def _restore_topology(self, _log):
        """Rebuild the in-memory topology from the saved snapshot. Mirrors the
        /topology/upload code path. Skips if core isn't ready or a topology is
        already loaded (never clobber live state)."""
        if self.topology is None or self.device_manager is None:
            return
        if self.topology.node_count() > 0:
            return  # something already loaded a topology — leave it
        blob = self.session_store.get_blob("topology")
        if not blob:
            return
        data = blob.get("data")
        if not data:
            return
        self.device_manager.clear()
        if self.ip_manager:
            self.ip_manager.reset()
        self.topology.from_dict(data)
        for device in self.topology.get_all_devices():
            self.device_manager.add_device(device)
            if self.ip_manager:
                self.ip_manager.reserve(device.ip_address)
        self.current_topology_path = blob.get("path", "") or ""
        self.notify_ui("rebuild_topology_scene")
        _log.info("restored topology — %d device(s), %d link(s)",
                  self.topology.node_count(), self.topology.edge_count())

    # ── persistence: bindings (Phase 2) ──────────────────────────────────────
    # Host IP aliases (ip addr add / AddIPAddress) are kernel state — a reboot
    # wipes them. We persist the adapter, mask and the exact bound-IP set, then
    # re-apply them on startup (the boot-time network-config pattern). nte
    # contexts are NOT persisted: they're handles into the running kernel that
    # a fresh add_ips_fast() regenerates.
    def persist_binding(self):
        """Snapshot binding config + current bound-IP set."""
        if not self.session_store:
            return
        try:
            self.session_store.set_blob("binding", {
                "adapter": self.selected_adapter,
                "mask": self.subnet_mask,
                "bound_ips": list(self.bound_ips),
                "gnmi_bound_ips": list(self.gnmi_bound_ips),
                "was_bound": bool(self.bound_ips or self.gnmi_bound_ips),
            })
        except Exception:
            import logging
            logging.getLogger("persistence").exception("persist_binding failed")

    def _restore_bindings(self, _log):
        """Restore adapter/mask always; re-bind the saved IP set in the
        background (needs root/CAP_NET_ADMIN). Sets self._binding_restored when
        binding is done or determined unnecessary/impossible."""
        blob = self.session_store.get_blob("binding")
        if not blob:
            self._binding_restored.set()
            return
        # Config first — even if we can't re-bind, a later manual bind reuses it.
        self.selected_adapter = blob.get("adapter", "") or ""
        self.subnet_mask = blob.get("mask", self.subnet_mask) or self.subnet_mask

        ips = blob.get("bound_ips") or []
        if not (blob.get("was_bound") and ips and self.selected_adapter):
            self._binding_restored.set()
            return

        from core.ip_binder import is_admin
        if not is_admin():
            _log.warning(
                "saved bindings present (%d IP(s)) but process lacks root/"
                "CAP_NET_ADMIN — skipping auto re-bind; bind manually from the UI",
                len(ips))
            self._binding_restored.set()
            return

        adapter, mask = self.selected_adapter, self.subnet_mask

        def _rebind_worker():
            try:
                from core.ip_binder import add_ips_fast
                _log.info("re-binding %d saved IP(s) to %s …", len(ips), adapter)
                bound, contexts = add_ips_fast(
                    adapter, ips, mask,
                    log_cb=lambda m, l="info": self.notify_ui("log", m, l),
                )
                self.bound_ips = bound
                self.nte_contexts = contexts
                self.notify_ui("sync_binding")
                _log.info("re-bound %d/%d IP(s)", len(bound), len(ips))
            except Exception:
                _log.exception("auto re-bind failed")
            finally:
                # Unblock Phase-3 sim restore regardless of outcome.
                self._binding_restored.set()

        # Background it: a large bind must not stall API/UI startup. Phase 3
        # waits on self._binding_restored before starting IP-dependent sims.
        threading.Thread(target=_rebind_worker, daemon=True,
                         name="binding-restore").start()

    # ── persistence: simulators (Phase 3) ────────────────────────────────────
    # Each protocol simulator persists its on/off flag + the config it was
    # started with. On restart we replay those starts (after bindings land) by
    # calling the same router start functions a user's click would, so behaviour
    # is identical. Redfish is the exception: its password is deliberately NOT
    # stored, so a running Redfish is flagged for password re-entry, not
    # auto-started.
    def persist_simulator(self, name: str, running: bool, cfg: Optional[dict] = None):
        """Update one simulator's persisted on/off + config (read-modify-write).
        Also snapshots the generated dataset file lists, which the SNMP/gNMI
        restart paths need and which are otherwise lost on restart."""
        if not self.session_store:
            return
        try:
            blob = self.session_store.get_blob("simulators") or {}
            entry = blob.get(name) or {}
            entry["running"] = running
            if cfg:
                entry.update(cfg)
            blob[name] = entry
            blob["generated_snmp_files"] = list(self.generated_snmp_files)
            blob["generated_gnmi_files"] = list(self.generated_gnmi_files)
            self.session_store.set_blob("simulators", blob)
        except Exception:
            import logging
            logging.getLogger("persistence").exception("persist_simulator(%s) failed", name)

    def _restore_simulators(self, _log):
        """Replay simulator state. Backgrounded and gated on binding restore so
        IP-dependent sims start only once their addresses exist."""
        import os
        blob = self.session_store.get_blob("simulators")
        if not blob:
            return
        # Restore generated dataset lists (files persist on disk across reboot);
        # drop any whose file is missing so the start guards behave correctly.
        self.generated_snmp_files = [f for f in blob.get("generated_snmp_files", []) if os.path.exists(f)]
        self.generated_gnmi_files = [f for f in blob.get("generated_gnmi_files", []) if os.path.exists(f)]

        want = {n: (blob.get(n) or {}) for n in ("snmp", "gnmi", "sflow", "bacnet", "redfish")}
        if not any(want[n].get("running") for n in want):
            return  # nothing was running — nothing to restore

        def _worker():
            # SNMP/gNMI/Redfish need bound IPs; wait for the re-bind to finish.
            self._binding_restored.wait(timeout=180)
            self._restore_sims_ordered(want, _log)

        threading.Thread(target=_worker, daemon=True, name="sim-restore").start()

    def _restore_sims_ordered(self, want: dict, _log):
        """Start previously-running sims in dependency order. Each is isolated —
        one failure never blocks the rest."""
        try:
            from api.routers import snmp as snmp_r, gnmi as gnmi_r, sflow as sflow_r, bacnet as bacnet_r
            from api.models.schemas import SnmpStartRequest, GnmiStartRequest
            from api.routers.sflow import SFlowConfig
            from api.routers.bacnet import BACnetConfig
        except Exception:
            _log.exception("simulator restore: router import failed")
            return

        c = want["snmp"]
        if c.get("running"):
            try:
                _log.info("restoring SNMP simulator …")
                r = snmp_r.start_snmp_simulator(
                    SnmpStartRequest(port=c.get("port", 161), mgmt_port=c.get("mgmt_port", 1161)))
                self._wait_job(getattr(r, "job_id", None), 120, _log)
            except Exception as e:
                _log.warning("SNMP restore failed: %s", e)

        c = want["gnmi"]
        if c.get("running"):
            try:
                _log.info("restoring gNMI simulator …")
                r = gnmi_r.start_gnmi_simulator(GnmiStartRequest(port=c.get("port", 50051)))
                self._wait_job(getattr(r, "job_id", None), 120, _log)
                if c.get("proxy_running"):
                    gnmi_r.start_gnmi_proxy(GnmiStartRequest(port=c.get("proxy_port", c.get("port", 50051))))
            except Exception as e:
                _log.warning("gNMI restore failed: %s", e)

        c = want["sflow"]
        if c.get("running"):
            try:
                _log.info("restoring sFlow …")
                sflow_r.sflow_start(SFlowConfig(
                    collector_ip=c.get("collector_ip", "127.0.0.1"),
                    collector_port=c.get("collector_port", 6343),
                    interval=c.get("interval", 30),
                    sample_rate=c.get("sample_rate", 1000)))
            except Exception as e:
                _log.warning("sFlow restore failed: %s", e)

        c = want["bacnet"]
        if c.get("running"):
            try:
                _log.info("restoring BACnet …")
                bacnet_r.bacnet_start(BACnetConfig(
                    base_instance=c.get("base_instance", 40001),
                    frequency_hz=c.get("frequency_hz", 50.0),
                    port=c.get("port", 47808)))
            except Exception as e:
                _log.warning("BACnet restore failed: %s", e)

        # Redfish: password not persisted — flag for re-entry, do not auto-start.
        if want["redfish"].get("running"):
            self.redfish_needs_password = True
            self.notify_ui("log",
                           "Redfish was running before restart — re-enter the password to start it.",
                           "warning")
            self.notify_ui("sync_redfish")
            _log.info("Redfish was running — awaiting password re-entry (not auto-started)")

    def _wait_job(self, job_id, timeout: float, _log):
        """Block until a background job finishes or times out. Used to serialize
        dependent restore steps (e.g. gNMI proxy after the gNMI sim)."""
        import time
        if not job_id or job_id == "none":
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.get_job(job_id)
            if j and j.status in ("completed", "failed"):
                if j.status == "failed":
                    _log.warning("restore job %s failed: %s", j.operation, j.error)
                return
            time.sleep(0.5)
        _log.warning("restore job %s did not finish within %ss", job_id, timeout)

    # ── persistence: rules (Phase 4) ─────────────────────────────────────────
    # Persists the rule-engine control state: master enable, autonomous-fault
    # toggle, and which rules are disabled. Fire counts are NOT persisted — the
    # engine exposes no count setter and they're session statistics, not config.
    def persist_rules(self):
        if not self.session_store:
            return
        try:
            disabled = []
            if self.rule_engine:
                for r in self.rule_engine.get_rules():
                    if not getattr(r, "enabled", True):
                        disabled.append(r.rule_name)
            self.session_store.set_blob("rules", {
                "engine_enabled": self.rule_engine_enabled,
                "autonomous_faults": bool(self.state_store.autonomous_faults) if self.state_store else False,
                "disabled_rules": disabled,
            })
        except Exception:
            import logging
            logging.getLogger("persistence").exception("persist_rules failed")

    def _restore_rules(self, _log):
        blob = self.session_store.get_blob("rules")
        if not blob:
            return
        self.rule_engine_enabled = bool(blob.get("engine_enabled", self.rule_engine_enabled))
        if self.state_store is not None:
            self.state_store.autonomous_faults = bool(blob.get("autonomous_faults", False))
        if self.rule_engine is not None:
            for name in blob.get("disabled_rules", []):
                try:
                    if self.rule_engine.get_rule(name) is not None:
                        self.rule_engine.enable_rule(name, False)
                except Exception:
                    pass
        _log.info("restored rules — autonomous_faults=%s, %d disabled",
                  self.state_store.autonomous_faults if self.state_store else "?",
                  len(blob.get("disabled_rules", [])))

    # ── persistence: tick settings (Phase 4) ─────────────────────────────────
    def persist_tick(self):
        if not self.session_store or self.state_store is None:
            return
        try:
            st = self.state_store
            self.session_store.set_blob("tick", {
                "interval": int(st._tick_interval),
                "metric_flags": dict(st.metric_flags),
                "metric_limits": {k: dict(v) for k, v in st.metric_limits.items()},
            })
        except Exception:
            import logging
            logging.getLogger("persistence").exception("persist_tick failed")

    def _restore_tick(self, _log):
        blob = self.session_store.get_blob("tick")
        if not blob or self.state_store is None:
            return
        st = self.state_store
        if blob.get("interval"):
            st.set_tick_interval(max(1, int(blob["interval"])))
        # Overlay only known keys — never replace the structure the store built.
        for k, v in (blob.get("metric_flags") or {}).items():
            if k in st.metric_flags:
                st.metric_flags[k] = bool(v)
        for k, v in (blob.get("metric_limits") or {}).items():
            if k not in st.metric_limits:
                continue
            lim = st.metric_limits[k]
            if "enabled" in v:
                lim["enabled"] = bool(v["enabled"])
            if v.get("min") is not None:
                lim["min"] = float(v["min"])
            if v.get("max") is not None:
                lim["max"] = float(v["max"])
            if v.get("lock") is not None:
                lim["lock"] = str(v["lock"])
        _log.info("restored tick settings — interval=%ss", int(st._tick_interval))

    # ── persistence: runtime meters (Phase 4) ────────────────────────────────
    # Fast-changing cumulative counters (energy kWh, run-hours) — snapshotted
    # periodically and on shutdown, re-seeded on startup so meters stay
    # monotonic across a restart.
    def flush_runtime(self):
        if not self.session_store:
            return
        try:
            if self.state_store is not None:
                self.session_store.set_blob("runtime", {
                    "energy": self.state_store.export_energy(),
                })
            # Snapshot the console log ring (Phase 6) — high-volume, so it rides
            # the periodic flush rather than a write-per-line.
            self.session_store.set_blob("console", {"logs": list(self._log_ring)})
        except Exception:
            import logging
            logging.getLogger("persistence").exception("flush_runtime failed")

    def _restore_runtime(self, _log):
        blob = self.session_store.get_blob("runtime")
        if not blob or self.state_store is None:
            return
        energy = blob.get("energy") or {}
        if energy:
            self.state_store.seed_energy(energy)
            _log.info("restored energy meters for %d device(s)", len(energy))

    # ── persistence: traps + console logs (Phase 6) ──────────────────────────
    def _restore_traps(self, _log):
        traps = self.session_store.recent_traps(self._trap_history_limit)
        if traps:
            with self._state_lock:
                self.trap_history = traps
            _log.info("restored %d trap(s)", len(traps))

    def _restore_console(self, _log):
        blob = self.session_store.get_blob("console")
        if not blob:
            return
        logs = blob.get("logs") or []
        if logs:
            self._log_ring.extend(logs)
            _log.info("restored %d console log line(s)", len(logs))

    def recent_logs(self) -> list:
        """Snapshot of the console log ring for SSE replay on connect."""
        return list(self._log_ring)

    def _start_runtime_flusher(self):
        """Daemon thread that snapshots runtime meters every 30s. Idempotent."""
        if getattr(self, "_flusher_started", False):
            return
        self._flusher_started = True

        def _loop():
            import time
            while True:
                time.sleep(30)
                self.flush_runtime()

        threading.Thread(target=_loop, daemon=True, name="runtime-flush").start()

    # ── persistence: fleet lifecycle (Phase 5) ───────────────────────────────
    def persist_fleet(self):
        eng = getattr(self, "fleet_engine", None)
        if not self.session_store or eng is None:
            return
        try:
            self.session_store.set_blob("fleet", eng.export_state())
        except Exception:
            import logging
            logging.getLogger("persistence").exception("persist_fleet failed")

    def _restore_fleet(self, _log):
        blob = self.session_store.get_blob("fleet")
        if not blob:
            return
        import logging
        from core.fleet_lifecycle import FleetLifecycleEngine
        eng = getattr(self, "fleet_engine", None)
        if eng is None:
            eng = FleetLifecycleEngine(self, log_cb=logging.getLogger("fleet").info)
            self.fleet_engine = eng
        eng.import_state(blob)
        _log.info("restored fleet — day=%s", eng.day)
        # Resume the day scheduler only if it was running (needs a topology,
        # which was restored first). start() launches the background thread.
        if blob.get("enabled"):
            try:
                eng.start()
                _log.info("resumed fleet lifecycle scheduler")
            except Exception as e:
                _log.warning("fleet scheduler resume failed: %s", e)

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
            # Reuse the port snmpsim is currently serving on.
            port = 161
            eps = self.snmpsim.get_active_endpoints()
            if eps:
                try:
                    port = int(eps[0].rsplit(":", 1)[1])
                except (ValueError, IndexError):
                    pass
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
        payload = self._sse_event(event, *args)
        # Capture console log lines into the ring for persistence + replay.
        if payload.get("type") == "log":
            try:
                self._log_ring.append({
                    "tab": payload.get("tab", "snmp"),
                    "msg": payload.get("msg", ""),
                    "level": payload.get("level", "info"),
                })
            except Exception:
                pass
        self._broadcast_sse(payload)

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
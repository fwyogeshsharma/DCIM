"""
Main Application Window.
"""
from __future__ import annotations
import json
import os
import queue
import random
import shutil
import threading
from pathlib import Path
from typing import List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QDockWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QStatusBar, QLabel, QMenuBar,
    QMenu, QFileDialog, QMessageBox, QInputDialog,
    QAbstractItemView, QFrame, QPushButton, QDialog,
    QSpinBox, QComboBox, QFormLayout, QDialogButtonBox,
    QGroupBox, QToolBar, QLineEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSize, QRectF
from PySide6.QtGui import (QAction, QIcon, QImage, QPixmap, QFont, QColor,
                           QKeySequence, QPainter, QPainterPath, QBrush)

from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.device_models import DEVICE_MODELS
from core.topology_engine import TopologyEngine
from core.snmprec_generator import SNMPRecGenerator
from core.gnmi_data_generator import GNMIDataGenerator
from core.device_state_store import DeviceStateStore
from core.ip_manager import IPManager
from core.ip_binder import (
    add_ips_batch, remove_ips_batch, is_admin,
)
from simulator.snmpsim_controller import SNMPSimController
from simulator.gnmi_controller import GNMIController
from simulator.sflow_controller import SFlowController
from simulator.bacnet_controller import BACnetController
from simulator.redfish_controller import RedfishController
from core.trap_definitions import TrapType, TRAP_DEFINITIONS, get_applicable_traps
from core.trap_engine import TrapEngine
from core.rule_engine import RuleEngine
from core.trap_rules import DEFAULT_RULES, save_rules
from core.snmp_set_agent import SnmpSetAgent
from ui.device_dialog import DeviceDialog
from ui.rules_panel import RulesPanel
from ui.topology_view import TopologyView
from ui.snmp_panel import SNMPPanel
from ui.trap_panel import TrapPanel
from ui.gnmi_panel import GNMIPanel
from ui.sflow_panel import SFlowPanel
from ui.bacnet_panel import BACnetPanel
from ui.redfish_panel import RedfishPanel
from ui.console_panel import ConsolePanel
from ui.binding_panel import BindingPanel
from ui.discovery_dialog import DiscoveryDialog
from ui.tick_panel import TickPanel as TickSidePanel


DATASETS_DIR        = "datasets"
SNMP_DATASETS_DIR   = os.path.join(DATASETS_DIR, "snmp")
GNMI_DATASETS_DIR   = os.path.join(DATASETS_DIR, "gnmi")
BACNET_DATASETS_DIR = os.path.join(DATASETS_DIR, "bacnet")
TOPOLOGIES_DIR = "topologies"


def _default_model_name(device) -> str:
    """Return the first known model name for this device's vendor+type, or '—'."""
    models = DEVICE_MODELS.get((device.device_type, device.vendor), [])
    return models[0].name if models else "—"


def _all_bind_ips(devices) -> list:
    """Collect every IP that should be bound to the network adapter.

    For topologies with a management layer each device contributes both its
    production ip_address (10.x.x.x — ICMP ping) and its mgmt_ip (192.168.x.x
    — SNMP).  OOB switches and sensors live entirely on the mgmt network so
    their ip_address and mgmt_ip are identical; deduplication handles this.
    For legacy topologies without a management layer only ip_address is used.
    """
    devs = list(devices)
    has_mgmt = any(d.mgmt_ip for d in devs)
    seen: set = set()
    ips: list = []
    for d in devs:
        for ip in ([d.ip_address] + ([d.mgmt_ip] if has_mgmt and d.mgmt_ip else [])):
            if ip and ip not in seen:
                ips.append(ip)
                seen.add(ip)
    return ips


# ------------------------------------------------------------------ #
#  Background worker for dataset generation                           #
# ------------------------------------------------------------------ #

class GeneratorWorker(QObject):
    progress = Signal(int, int)
    log      = Signal(str, str)
    finished = Signal()   # no args – result stored in self.result / self.gnmi_result
    error    = Signal(str)

    def __init__(self, topology: TopologyEngine, output_dir: str):
        super().__init__()
        self.topology = topology
        self.output_dir = output_dir

    def run(self):
        try:
            snmp_gen = SNMPRecGenerator(self.output_dir)
            devices  = self.topology.get_all_devices()
            total    = len(devices)
            snmp_files  = []
            # Emit at most ~100 progress updates regardless of topology size.
            step = max(1, total // 100)
            for i, device in enumerate(devices):
                fp = snmp_gen.generate_device(device, self.topology)
                snmp_files.append(fp)
                self.log.emit(
                    f"[SNMP] {device.ip_address}  {device.device_type.value}  ({device.interface_count} ifaces)",
                    "info"
                )

                if (i + 1) % step == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)

            self.result = snmp_files
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Background worker for standalone gNMI dataset generation           #
# ------------------------------------------------------------------ #

class _GNMIGenWorker(QObject):
    progress = Signal(int, int)
    finished = Signal()
    error    = Signal(str)

    def __init__(self, devices, topology: TopologyEngine, output_dir: str):
        super().__init__()
        self.devices    = devices
        self.topology   = topology
        self.output_dir = output_dir
        self.result: list = []

    def run(self):
        try:
            gnmi_gen = GNMIDataGenerator(self.output_dir)
            files = []
            total = len(self.devices)
            step  = max(1, total // 100)
            for i, device in enumerate(self.devices):
                fp = gnmi_gen.generate_device(device, self.topology)
                if fp:
                    files.append(fp)
                if (i + 1) % step == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)
            self.result = files
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Background worker for snmpsim index pre-building                   #
# ------------------------------------------------------------------ #

class IndexWorker(QObject):
    """Pre-builds snmpsim .dbm indexes in parallel after dataset generation."""
    progress = Signal(int, int)   # (completed, total)
    finished = Signal(int)        # total files indexed
    error    = Signal(str)

    def __init__(self, snmpsim_controller: "SNMPSimController", data_dir: str):
        super().__init__()
        self._ctrl = snmpsim_controller
        self._data_dir = data_dir

    def run(self):
        try:
            count = self._ctrl.preindex_datasets(
                self._data_dir,
                progress_cb=lambda c, t: self.progress.emit(c, t),
            )
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Background worker for live SNMP topology discovery                  #
# ------------------------------------------------------------------ #

class LiveDiscoveryWorker(QObject):
    """
    Runs a full SNMP topology discovery scan in a background thread.

    Confirmed links are pushed into self.link_queue (a thread-safe Queue) instead
    of being emitted as Qt signals.  A QTimer on the main thread drains the queue
    every 50 ms so the graph updates progressively in sync with the actual SNMP
    polling, rather than in a single burst when the event queue is finally flushed.
    """
    finished = Signal(object)   # DiscoveryResult
    error    = Signal(str)

    def __init__(self, topology, host: str = "127.0.0.1", port: int = 161):
        super().__init__()
        self._topology = topology
        self._host = host
        self._port = port
        import queue as _q
        self.link_queue = _q.Queue()   # (src_id, dst_id) tuples, thread-safe

    def run(self):
        try:
            from core.discovery_engine import DiscoveryEngine
            engine = DiscoveryEngine(self._host, self._port)
            result = engine.discover(
                self._topology,
                device_scanned_cb=lambda dev_id: self.link_queue.put(dev_id),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ------------------------------------------------------------------ #
#  Background worker for dataset file deletion                         #
# ------------------------------------------------------------------ #

class ClearDatasetsWorker(QObject):
    """Deletes SNMP dataset files in the background so the main thread is
    never blocked by shutil.rmtree on large topologies.
    Only the snmp_datasets_dir is touched — gNMI datasets are left intact."""
    finished = Signal()

    def __init__(self, snmp_datasets_dir: str):
        super().__init__()
        self.snmp_datasets_dir = snmp_datasets_dir

    def run(self):
        ds_path = Path(self.snmp_datasets_dir)
        try:
            for child in ds_path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                elif child.suffix == ".snmprec":
                    child.unlink(missing_ok=True)
        except Exception:
            pass
        self.finished.emit()


# ------------------------------------------------------------------ #
#  Background workers for IP binding / unbinding                       #
# ------------------------------------------------------------------ #

class IPBindWorker(QObject):
    """Adds a list of IPs to a Windows network interface via netsh."""
    progress = Signal(int, int)         # (current, total)
    log      = Signal(str, str)         # (message, level)
    finished = Signal()                 # result stored in self.result
    error    = Signal(str)

    def __init__(self, interface: str, ips: List[str], mask: str):
        super().__init__()
        self.interface = interface
        self.ips = ips
        self.mask = mask
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            from core.ip_binder import add_ips_fast
            bound, contexts = add_ips_fast(
                self.interface, self.ips, self.mask,
                log_cb=lambda msg, lvl: self.log.emit(msg, lvl),
                progress_cb=lambda c, t: self.progress.emit(c, t),
                cancelled_fn=lambda: self.cancelled,
            )
            self.result       = bound
            self.nte_contexts = contexts   # {ip: nte_context} for fast removal
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class IPUnbindWorker(QObject):
    """Removes a list of IPs from a Windows network interface."""
    progress = Signal(int, int)   # (current, total)
    log      = Signal(str, str)
    finished = Signal()

    def __init__(self, interface: str, ips: List[str],
                 nte_contexts: Optional[dict] = None):
        super().__init__()
        self.interface    = interface
        self.ips          = ips
        self.nte_contexts = nte_contexts or {}

    def run(self):
        try:
            from core.ip_binder import remove_ips_fast
            remove_ips_fast(
                self.interface, self.ips, self.nte_contexts,
                log_cb=lambda msg, lvl: self.log.emit(msg, lvl),
                progress_cb=lambda c, t: self.progress.emit(c, t),
            )
        except Exception as e:
            self.log.emit(f"Unbind error: {e}", "error")
        finally:
            self.finished.emit()


# ------------------------------------------------------------------ #
#  Force-Directed Layout Worker                                        #
# ------------------------------------------------------------------ #

class ForceLayoutWorker(QObject):
    """Computes a NetworkX layout in a background thread."""
    finished = Signal(dict)   # {device_id: (x, y)}
    error    = Signal(str)

    def __init__(self, node_ids: list, edge_pairs: list,
                 layout_name: str = "spring",
                 device_types: dict = None):
        super().__init__()
        self._node_ids   = node_ids     # [device_id, ...]
        self._edge_pairs = edge_pairs   # [(src_id, dst_id), ...]
        self._layout     = layout_name  # spring | shell | multipartite | kamada_kawai
        self._dev_types  = device_types or {}  # {device_id: DeviceType}

    def run(self):
        try:
            import networkx as nx

            G = nx.Graph()
            G.add_nodes_from(self._node_ids)
            G.add_edges_from(self._edge_pairs)

            # scale=4000 → positions span roughly ±4 000 scene units
            if self._layout == "spring":
                pos = nx.spring_layout(G, seed=42, iterations=50, scale=4000)

            elif self._layout == "shell":
                # Arrange nodes in concentric shells by device type priority
                type_order = [
                    "FIREWALL", "ROUTER", "SWITCH", "WIRELESS_AP",
                    "SERVER", "WORKSTATION", "PRINTER", "GENERIC"
                ]
                shells_dict: dict[str, list] = {t: [] for t in type_order}
                for nid in self._node_ids:
                    dt = str(self._dev_types.get(nid, "GENERIC")).split(".")[-1].upper()
                    bucket = dt if dt in shells_dict else "GENERIC"
                    shells_dict[bucket].append(nid)
                shells = [s for s in (shells_dict[t] for t in type_order) if s]
                if not shells:
                    shells = [self._node_ids]
                pos = nx.shell_layout(G, nlist=shells, scale=4000)

            elif self._layout == "kamada_kawai":
                # Hard cap: fall back to spring for very large graphs
                if len(self._node_ids) > 500:
                    raise ValueError(
                        f"Kamada-Kawai is too slow for {len(self._node_ids)} nodes "
                        f"(limit: 500). Use Spring Layout instead."
                    )
                # Warm-start with spring positions to reduce iterations
                init_pos = nx.spring_layout(G, seed=42, scale=4000)
                pos = nx.kamada_kawai_layout(G, pos=init_pos, scale=4000)

            else:
                pos = nx.spring_layout(G, seed=42, iterations=50, scale=4000)

            result = {nid: (float(xy[0]), float(xy[1]))
                      for nid, xy in pos.items()}
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ------------------------------------------------------------------ #
#  Bulk Device Dialog                                                  #
# ------------------------------------------------------------------ #

class BulkAddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk Add Devices")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        for dt in DeviceType:
            self.type_combo.addItem(dt.value.capitalize(), dt)
        form.addRow("Device Type:", self.type_combo)

        self.vendor_combo = QComboBox()
        for v in Vendor:
            self.vendor_combo.addItem(v.value, v)
        form.addRow("Vendor:", self.vendor_combo)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 500)
        self.count_spin.setValue(10)
        form.addRow("Count:", self.count_spin)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self):
        return {
            "device_type": self.type_combo.currentData(),
            "vendor": self.vendor_combo.currentData(),
            "count": self.count_spin.value(),
        }


# ------------------------------------------------------------------ #
#  Main Window                                                         #
# ------------------------------------------------------------------ #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Datacenter Network Simulator v4.0")
        self.setMinimumSize(1200, 750)
        self._datasets_dir        = DATASETS_DIR
        self._snmp_datasets_dir   = SNMP_DATASETS_DIR
        self._gnmi_datasets_dir   = GNMI_DATASETS_DIR
        self._bacnet_datasets_dir = BACNET_DATASETS_DIR
        self._topologies_dir      = TOPOLOGIES_DIR
        os.makedirs(self._snmp_datasets_dir,   exist_ok=True)
        os.makedirs(self._gnmi_datasets_dir,   exist_ok=True)
        os.makedirs(self._bacnet_datasets_dir, exist_ok=True)
        os.makedirs(self._topologies_dir,      exist_ok=True)

        self.device_manager = DeviceManager()
        self.topology = TopologyEngine()
        self.ip_manager = IPManager()
        self.snmpsim = SNMPSimController(self._snmp_datasets_dir)
        self.gnmi    = GNMIController(self._gnmi_datasets_dir)
        self.sflow   = SFlowController()
        self.bacnet  = BACnetController(self._bacnet_datasets_dir)
        self.redfish = RedfishController()
        self.state_store = DeviceStateStore(
            self.device_manager, self.topology, self._snmp_datasets_dir,
            tick_interval=30.0, snmp_sync_every=1,
        )
        self.gnmi.set_state_store(self.state_store)
        self.sflow.set_state_store(self.state_store)
        self.sflow.set_topology(self.topology)
        self.sflow.set_device_manager(self.device_manager)
        # BACnet callbacks (log forwarded to console BACnet tab)
        self.bacnet.set_log_callback(self._on_bacnet_log)
        self.bacnet.set_ready_callback(self._on_bacnet_ready)
        # Redfish callbacks (log forwarded to console)
        self.redfish.set_log_callback(self._on_redfish_log)
        self.redfish.set_ready_callback(self._on_redfish_ready)
        self._trap_engine = TrapEngine(self)

        # Rule engine — loaded with default rules; disabled until user enables it
        self._rule_engine = RuleEngine()
        for rule in DEFAULT_RULES:
            self._rule_engine.add_rule(rule)
        self._trap_engine.set_rule_engine(self._rule_engine, self.device_manager)
        self.state_store.set_rule_engine_callback(self._rule_engine.evaluate_fact)

        # BMC platform-event traps: chassis power transitions → SNMP trap.
        from core.trap_definitions import TrapType as _TT
        self.redfish.set_trap_callback(
            lambda dev, is_on, rt: self._trap_engine.send_trap(
                dev, _TT.SERVER_POWER_ON if is_on else _TT.SERVER_POWER_OFF,
                reset_type=rt))

        # SNMP SET agent — listens on port 1161 for threshold configuration SETs
        self._snmp_set_agent = SnmpSetAgent(
            self._rule_engine,
            port=1161,
            on_change_cb=self._on_snmp_threshold_changed,
            device_lookup=self._lookup_device_by_ip,
            on_device_updated=self._on_snmp_device_updated,
        )
        self._trap_rules_path = Path("trap_rules.json")
        self._device_thresholds_path = Path("device_thresholds.json")
        if self._device_thresholds_path.exists():
            try:
                import json as _json
                with open(self._device_thresholds_path) as _f:
                    self._rule_engine.load_device_overrides(_json.load(_f))
            except Exception:
                pass

        self._generated_files: list = []
        self._gnmi_files: list = []
        self._default_positions: dict = {}         # {device_id: (x, y)} — snapshot at load/template time
        self._current_layout_positions: dict = {}  # snapshot after each layout application
        self._algo_layout_active: bool = False  # True after an algo layout; drags no longer update default
        self._worker_thread: QThread = None
        self._worker: GeneratorWorker = None
        self._index_thread: QThread = None
        self._index_worker: IndexWorker = None
        self._link_mode = False

        # SNMP IP binding state
        self._bound_ips: List[str] = []
        self._bound_interface: str = ""
        self._nte_contexts: dict = {}   # {ip: nte_context} for fast DeleteIPAddress
        self._bind_thread: QThread = None
        self._bind_worker = None
        self._unbind_thread: QThread = None
        self._unbind_worker = None

        # gNMI IP binding state (independent from SNMP)
        self._gnmi_bound_ips: List[str] = []
        self._gnmi_bound_interface: str = ""
        self._gnmi_nte_contexts: dict = {}
        self._gnmi_bind_thread: QThread = None
        self._gnmi_bind_worker = None
        self._gnmi_unbind_thread: QThread = None
        self._gnmi_unbind_worker = None
        # Binding-panel manual bind/unbind workers
        self._panel_bind_thread: QThread = None
        self._panel_bind_worker = None
        self._panel_unbind_thread: QThread = None
        self._panel_unbind_worker = None
        # Set when a clear operation needs to chain unbind of the other simulator's IPs
        self._pending_clear_finish: bool = False
        self._clear_thread: QThread = None
        self._clear_worker: ClearDatasetsWorker = None

        # Live topology discovery state
        self._live_discovery_thread: QThread = None
        self._live_discovery_worker: LiveDiscoveryWorker = None
        self._live_discovery_running: bool = False
        self._link_drain_timer: QTimer = None
        self._discovered_devices: set = set()   # device IDs already polled
        self._device_adjacency: dict = {}       # device_id → {neighbor_id}

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._apply_theme()

        # Thread-safe log queue — monitor thread puts, main thread drains
        self._log_queue: queue.Queue = queue.Queue()
        self._log_drain_timer = QTimer(self)
        self._log_drain_timer.setInterval(150)   # 150 ms — log display doesn't need 50 ms refresh
        self._log_drain_timer.timeout.connect(self._drain_log_queue)
        self._log_drain_timer.start()

        # Periodic status refresh
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(2000)

        # Register all core objects with the REST API shared state
        try:
            from api.state import AppState
            api_state = AppState.get()
            api_state.register(
                device_manager=self.device_manager,
                topology=self.topology,
                ip_manager=self.ip_manager,
                snmpsim=self.snmpsim,
                gnmi=self.gnmi,
                sflow=self.sflow,
                bacnet=self.bacnet,
                redfish=self.redfish,
                state_store=self.state_store,
                rule_engine=self._rule_engine,
                trap_engine=self._trap_engine,
                snmp_datasets_dir=self._snmp_datasets_dir,
                gnmi_datasets_dir=self._gnmi_datasets_dir,
            )
            api_state._ui_queue = self._log_queue
            self._trap_engine.trap_sent.connect(api_state.record_trap)
            self.state_store.set_tick_callback(lambda: api_state.notify_ui("sync_devices"))
            self.state_store.set_link_callback(
                lambda src, dst, broken: api_state.notify_ui("link_changed", src, dst, broken))
        except Exception:
            pass  # API integration is non-critical — UI must not fail if it errors

    # ------------------------------------------------------------------ #
    #  UI Construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Topology canvas fills the entire central widget
        self._topology_view = TopologyView()
        main_layout.addWidget(self._topology_view)

        # Dockable device list (left)
        self._build_device_dock()

        # Both right-side panels in one outer dock with an inner splitter
        self._build_right_panels()

        # Right-side panel toggle toolbar (built last so dock refs are available)
        self._build_right_toolbar()

        # Status bar
        self._status_bar = self.statusBar()
        self._status_label = QLabel("Ready")
        self._status_label.setFont(QFont("Arial", 9))
        self._status_bar.addPermanentWidget(self._status_label)

    def _build_device_dock(self):
        dock = QDockWidget("Device List", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.BottomDockWidgetArea)

        # ── Search bar ───────────────────────────────────────────────────
        self._device_search = QLineEdit()
        self._device_search.setPlaceholderText("Search devices…")
        self._device_search.setClearButtonEnabled(True)
        self._device_search.setStyleSheet("""
            QLineEdit {
                background: #21262d;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #1f6feb; }
        """)
        self._device_search.textChanged.connect(self._on_device_search)

        # ── Table ────────────────────────────────────────────────────────
        self._device_table = QTableWidget()
        self._device_table.setColumnCount(8)
        self._device_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Vendor", "Mgmt IP", "Prod IP", "Interfaces", "SNMP Port", "Location"]
        )

        hdr = self._device_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setDefaultSectionSize(90)
        hdr.setMinimumSectionSize(50)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)
        hdr.setStretchLastSection(True)

        self._device_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._device_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._device_table.setAlternatingRowColors(True)
        self._device_table.setStyleSheet("""
            QTableWidget {
                background: #161b22;
                color: #e6edf3;
                border: none;
                alternate-background-color: #0d1117;
                gridline-color: #30363d;
            }
            QHeaderView::section {
                background: #21262d;
                color: #8b949e;
                padding: 4px;
                border: none;
                border-bottom: 1px solid #30363d;
            }
            QHeaderView::section:hover { background: #2d333b; }
            QTableWidget::item:selected { background: #1f6feb; }
        """)
        self._device_table.doubleClicked.connect(self._on_device_table_double_click)
        self._device_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._device_table.customContextMenuRequested.connect(self._on_device_table_right_click)

        # ── Container ────────────────────────────────────────────────────
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(self._device_search)
        layout.addWidget(self._device_table)

        dock.setWidget(container)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self._device_dock = dock

    def _build_right_panels(self):
        """Single outer dock holding panels in a horizontal QSplitter:
        1. Network Interface Binding
        2. SNMP Simulator (controls only)
        3. SNMP Traps (receiver config, Rule Engine, trap log)
        4. gNMI Simulator
        5. sFlow Simulator
        6. Console
        7. Rule Engine
        """
        self._right_splitter = QSplitter(Qt.Horizontal)
        self._right_splitter.setChildrenCollapsible(True)
        self._right_splitter.setHandleWidth(3)
        self._right_splitter.setStyleSheet(
            "QSplitter::handle { background: #30363d; }"
            "QSplitter::handle:hover { background: #58a6ff; }"
        )

        # Panel 1 — Network Interface Binding
        self._binding_panel = BindingPanel()
        self._binding_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._binding_panel)

        # Panel 2 — SNMP Simulator (controls only)
        self._sim_panel = SNMPPanel()
        self._sim_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._sim_panel)

        # Panel 3 — SNMP Traps
        self._trap_panel = TrapPanel()
        self._trap_panel.setMinimumWidth(300)
        self._right_splitter.addWidget(self._trap_panel)

        # Panel 4 — gNMI Simulator
        self._gnmi_panel = GNMIPanel()
        self._gnmi_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._gnmi_panel)

        # Panel 5 — sFlow Simulator
        self._sflow_panel = SFlowPanel()
        self._sflow_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._sflow_panel)

        # Panel 6 — BACnet/IP Simulator
        self._bacnet_panel = BACnetPanel()
        self._bacnet_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._bacnet_panel)

        # Panel 7 — Redfish Simulator
        self._redfish_panel = RedfishPanel()
        self._redfish_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._redfish_panel)

        # Panel 8 — Console
        self._console_panel = ConsolePanel()
        self._console_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._console_panel)

        # Panel 9 — Rule Engine
        self._rules_panel = RulesPanel()
        self._rules_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._rules_panel)

        # Panel 10 — Metrics Tick
        self._tick_panel = TickSidePanel()
        self._tick_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._tick_panel)

        for i in range(10):
            self._right_splitter.setStretchFactor(i, 1)
        self._right_splitter.setSizes([250, 250, 300, 250, 250, 260, 260, 250, 250, 300])

        # Only the IP Binder panel visible on startup
        self._sim_panel.setVisible(False)
        self._trap_panel.setVisible(False)
        self._gnmi_panel.setVisible(False)
        self._sflow_panel.setVisible(False)
        self._bacnet_panel.setVisible(False)
        self._redfish_panel.setVisible(False)
        self._console_panel.setVisible(False)
        self._rules_panel.setVisible(False)
        self._tick_panel.setVisible(False)

        self._right_dock = QDockWidget(self)
        self._right_dock.setObjectName("right_panels_dock")
        self._right_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self._right_dock.setTitleBarWidget(QWidget())
        self._right_dock.setWidget(self._right_splitter)
        self.addDockWidget(Qt.RightDockWidgetArea, self._right_dock)
        self.resizeDocks([self._right_dock], [250], Qt.Horizontal)

    def _build_right_toolbar(self):
        _TB_STYLE = """
            QToolBar {
                background: #161b22;
                border-left: 1px solid #30363d;
                padding: 6px 1px;
                spacing: 2px;
            }
            QToolButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: #8b949e;
                font-family: 'Segoe UI Emoji', 'Noto Color Emoji', 'Apple Color Emoji', 'Twemoji Mozilla', sans-serif;
                font-size: 17px;
                padding: 4px;
                min-width: 33px;
                min-height: 33px;
                max-width: 33px;
                max-height: 33px;
            }
            QToolButton:hover {
                background: #21262d;
                color: #e6edf3;
            }
            QToolButton:checked {
                background: rgba(31,111,235,0.15);
                color: #58a6ff;
                border-color: rgba(31,111,235,0.45);
            }
            QToolBarSeparator {
                background: #30363d;
                width: 1px;
                margin: 4px 5px;
            }
        """
        tb = QToolBar("Panels", self)
        tb.setObjectName("right_panel_tb")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setOrientation(Qt.Vertical)
        tb.setIconSize(QSize(24, 24))
        tb.setStyleSheet(_TB_STYLE)

        # ── Top group: simulators ──────────────────────────────────────────

        # IP Binder
        self._act_panel_binding = QAction("🔗", self)
        self._act_panel_binding.setCheckable(True)
        self._act_panel_binding.setChecked(True)
        self._act_panel_binding.setToolTip("Network Interface Bindings")
        self._act_panel_binding.toggled.connect(self._on_toggle_binding_panel)
        tb.addAction(self._act_panel_binding)

        tb.addSeparator()

        # Helper: load image via QPixmap, scale at HiDPI resolution, boost brightness
        from PySide6.QtWidgets import QApplication as _QApp2
        _dpr2 = _QApp2.instance().devicePixelRatio()

        def _bright_icon(path: str, size: int, factor: float = 1.6) -> QIcon:
            _phys = int(size * _dpr2)
            _img = QPixmap(path).scaled(
                _phys, _phys, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ).toImage().convertToFormat(QImage.Format_ARGB32)
            for _iy in range(_img.height()):
                for _ix in range(_img.width()):
                    _c = _img.pixelColor(_ix, _iy)
                    if _c.alpha() > 10:
                        _c.setRed(  min(255, int(_c.red()   * factor)))
                        _c.setGreen(min(255, int(_c.green() * factor)))
                        _c.setBlue( min(255, int(_c.blue()  * factor)))
                        _img.setPixelColor(_ix, _iy, _c)
            _pix = QPixmap.fromImage(_img)
            _pix.setDevicePixelRatio(_dpr2)
            return QIcon(_pix)

        # SNMP Simulator — strip white bg, boost brightness
        _snmp_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "snmp.png"
        )
        from PySide6.QtWidgets import QApplication as _QApp
        _dpr      = _QApp.instance().devicePixelRatio()
        _log_size = 24                          # logical px
        _phy_size = int(_log_size * _dpr)       # physical px — crisp on HiDPI

        _snmp_img = QPixmap(_snmp_icon_path).scaled(
            _phy_size, _phy_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).toImage().convertToFormat(QImage.Format_ARGB32)
        for _iy in range(_snmp_img.height()):
            for _ix in range(_snmp_img.width()):
                _c = _snmp_img.pixelColor(_ix, _iy)
                if _c.red() > 220 and _c.green() > 220 and _c.blue() > 220:
                    _c.setAlpha(0)
                else:
                    _c.setRed(  min(255, int(_c.red()   * 1.6)))
                    _c.setGreen(min(255, int(_c.green() * 1.6)))
                    _c.setBlue( min(255, int(_c.blue()  * 1.6)))
                _snmp_img.setPixelColor(_ix, _iy, _c)
        _snmp_result = QPixmap.fromImage(_snmp_img)
        _snmp_result.setDevicePixelRatio(_dpr)  # tell Qt: this is already HiDPI
        _snmp_icon = QIcon(_snmp_result)
        self._act_panel_sim = QAction(_snmp_icon, "", self)
        self._act_panel_sim.setCheckable(True)
        self._act_panel_sim.setChecked(False)
        self._act_panel_sim.setToolTip("SNMP Simulator")
        self._act_panel_sim.toggled.connect(self._on_toggle_sim_panel)
        tb.addAction(self._act_panel_sim)
        _snmp_btn = tb.widgetForAction(self._act_panel_sim)
        if _snmp_btn:
            _snmp_btn.setIconSize(QSize(24, 24))

        tb.addSeparator()

        # gNMI Simulator
        _gnmi_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "gnmi.svg"
        )
        _gnmi_icon = _bright_icon(_gnmi_icon_path, 24, factor=1.7)
        self._act_panel_gnmi = QAction(_gnmi_icon, "", self)
        self._act_panel_gnmi.setCheckable(True)
        self._act_panel_gnmi.setChecked(False)
        self._act_panel_gnmi.setToolTip("gNMI Simulator")
        self._act_panel_gnmi.toggled.connect(self._on_toggle_gnmi_panel)
        tb.addAction(self._act_panel_gnmi)

        tb.addSeparator()

        # sFlow Simulator — invert RGB then boost brightness
        _sflow_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "sflow.png"
        )
        _sf_phy = int(24 * _dpr2)
        _sflow_img = QPixmap(_sflow_icon_path).scaled(
            _sf_phy, _sf_phy, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).toImage().convertToFormat(QImage.Format_ARGB32)
        _sflow_img.invertPixels(QImage.InvertRgb)
        for _iy in range(_sflow_img.height()):
            for _ix in range(_sflow_img.width()):
                _c = _sflow_img.pixelColor(_ix, _iy)
                if _c.alpha() > 10:
                    _c.setRed(  min(255, int(_c.red()   * 1.4)))
                    _c.setGreen(min(255, int(_c.green() * 1.4)))
                    _c.setBlue( min(255, int(_c.blue()  * 1.4)))
                    _sflow_img.setPixelColor(_ix, _iy, _c)
        _sf_pix = QPixmap.fromImage(_sflow_img)
        _sf_pix.setDevicePixelRatio(_dpr2)
        _sflow_icon = QIcon(_sf_pix)
        self._act_panel_sflow = QAction(_sflow_icon, "", self)
        self._act_panel_sflow.setCheckable(True)
        self._act_panel_sflow.setChecked(False)
        self._act_panel_sflow.setToolTip("sFlow Simulator")
        self._act_panel_sflow.toggled.connect(self._on_toggle_sflow_panel)
        tb.addAction(self._act_panel_sflow)

        tb.addSeparator()

        # BACnet/IP Simulator
        _bacnet_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "bacnet.png"
        )
        _bacnet_phy = int(24 * _dpr2)
        _bacnet_pix = QPixmap(_bacnet_icon_path).scaled(
            _bacnet_phy, _bacnet_phy, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        _bacnet_pix.setDevicePixelRatio(_dpr2)
        _bacnet_icon = QIcon(_bacnet_pix)
        self._act_panel_bacnet = QAction(_bacnet_icon, "", self)
        self._act_panel_bacnet.setCheckable(True)
        self._act_panel_bacnet.setChecked(False)
        self._act_panel_bacnet.setToolTip("BACnet/IP Simulator (Verdigris EV2)")
        self._act_panel_bacnet.toggled.connect(self._on_toggle_bacnet_panel)
        tb.addAction(self._act_panel_bacnet)

        tb.addSeparator()

        # Redfish Simulator (server BMCs) — DMTF Redfish logo
        _redfish_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "redfish.png"
        )
        _redfish_phy = int(24 * _dpr2)
        _redfish_pix = QPixmap(_redfish_icon_path).scaled(
            _redfish_phy, _redfish_phy, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        _redfish_pix.setDevicePixelRatio(_dpr2)
        _redfish_icon = QIcon(_redfish_pix)
        self._act_panel_redfish = QAction(_redfish_icon, "", self)
        self._act_panel_redfish.setCheckable(True)
        self._act_panel_redfish.setChecked(False)
        self._act_panel_redfish.setToolTip("Redfish Simulator (server BMCs)")
        self._act_panel_redfish.toggled.connect(self._on_toggle_redfish_panel)
        tb.addAction(self._act_panel_redfish)

        # ── Spacer — pushes bottom group to the foot of the toolbar ───────
        _spacer = QWidget()
        _spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        tb.addWidget(_spacer)

        # ── Bottom group: logs / analysis ──────────────────────────────────

        tb.addSeparator()

        # SNMP Traps
        self._act_panel_traps = QAction("⚡", self)
        self._act_panel_traps.setCheckable(True)
        self._act_panel_traps.setChecked(False)
        self._act_panel_traps.setToolTip("SNMP Traps")
        self._act_panel_traps.toggled.connect(self._on_toggle_trap_panel)
        tb.addAction(self._act_panel_traps)

        tb.addSeparator()

        # Rule Engine
        _rules_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "rules.png"
        )
        _rules_phy = int(20 * _dpr2)
        _rules_img = QPixmap(_rules_icon_path).scaled(
            _rules_phy, _rules_phy, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).toImage().convertToFormat(QImage.Format_ARGB32)
        _rules_img.invertPixels(QImage.InvertRgb)
        for _iy in range(_rules_img.height()):
            for _ix in range(_rules_img.width()):
                _c = _rules_img.pixelColor(_ix, _iy)
                if _c.alpha() > 10:
                    _c.setRed(  min(255, int(_c.red()   * 1.4)))
                    _c.setGreen(min(255, int(_c.green() * 1.4)))
                    _c.setBlue( min(255, int(_c.blue()  * 1.4)))
                    _rules_img.setPixelColor(_ix, _iy, _c)
        _rules_pix = QPixmap.fromImage(_rules_img)
        _rules_pix.setDevicePixelRatio(_dpr2)
        _rules_icon = QIcon(_rules_pix)
        self._act_panel_rules = QAction(_rules_icon, "", self)
        self._act_panel_rules.setCheckable(True)
        self._act_panel_rules.setChecked(False)
        self._act_panel_rules.setToolTip("Rule Engine")
        self._act_panel_rules.toggled.connect(self._on_toggle_rules_panel)
        tb.addAction(self._act_panel_rules)
        _rules_btn = tb.widgetForAction(self._act_panel_rules)
        if _rules_btn:
            _rules_btn.setIconSize(QSize(20, 20))

        tb.addSeparator()

        # Metrics Tick
        _tick_icon_path = str(
            Path(__file__).parent.parent / "assets" / "icons" / "tick.png"
        )
        _tick_phy  = int(20 * _dpr2)
        _tick_img = QPixmap(_tick_icon_path).scaled(
            _tick_phy, _tick_phy, Qt.KeepAspectRatio, Qt.SmoothTransformation
        ).toImage().convertToFormat(QImage.Format_ARGB32)
        _tick_img.invertPixels(QImage.InvertRgb)
        for _iy in range(_tick_img.height()):
            for _ix in range(_tick_img.width()):
                _c = _tick_img.pixelColor(_ix, _iy)
                if _c.alpha() > 0:
                    _c.setRed(  min(255, int(_c.red()   * 1.4)))
                    _c.setGreen(min(255, int(_c.green() * 1.4)))
                    _c.setBlue( min(255, int(_c.blue()  * 1.4)))
                    _tick_img.setPixelColor(_ix, _iy, _c)
        _tick_pix = QPixmap.fromImage(_tick_img)
        _tick_pix.setDevicePixelRatio(_dpr2)
        _tick_icon = QIcon(_tick_pix)
        self._act_panel_tick = QAction(_tick_icon, "", self)
        self._act_panel_tick.setCheckable(True)
        self._act_panel_tick.setChecked(False)
        self._act_panel_tick.setToolTip("Metrics Tick")
        self._act_panel_tick.toggled.connect(self._on_toggle_tick_panel)
        tb.addAction(self._act_panel_tick)
        _tick_btn = tb.widgetForAction(self._act_panel_tick)
        if _tick_btn:
            _tick_btn.setIconSize(QSize(20, 20))

        tb.addSeparator()

        # Console — pinned to the very bottom
        self._act_panel_console = QAction(">_", self)
        self._act_panel_console.setCheckable(True)
        self._act_panel_console.setChecked(False)
        self._act_panel_console.setToolTip("Console")
        self._act_panel_console.toggled.connect(self._on_toggle_console_panel)
        tb.addAction(self._act_panel_console)
        _btn = tb.widgetForAction(self._act_panel_console)
        if _btn:
            _f = _btn.font()
            _f.setBold(True)
            _btn.setFont(_f)
            _btn.setStyleSheet(
                "QToolButton { color: #c9d1d9; }"
                "QToolButton:hover { color: #e6edf3; background: #21262d; }"
                "QToolButton:checked { color: #58a6ff; background: rgba(31,111,235,0.15); border-color: rgba(31,111,235,0.45); }"
            )

        self._right_dock.visibilityChanged.connect(self._on_right_dock_visibility)
        self.addToolBar(Qt.RightToolBarArea, tb)

    # ── Panel toggle slots ─────────────────────────────────────────────────────

    def _visible_panel_count(self) -> int:
        return sum([
            self._act_panel_binding.isChecked(),
            self._act_panel_sim.isChecked(),
            self._act_panel_traps.isChecked(),
            self._act_panel_gnmi.isChecked(),
            self._act_panel_sflow.isChecked(),
            self._act_panel_bacnet.isChecked(),
            self._act_panel_redfish.isChecked(),
            self._act_panel_console.isChecked(),
            self._act_panel_rules.isChecked(),
            self._act_panel_tick.isChecked(),
        ])

    def _resize_right_dock(self):
        n = self._visible_panel_count()
        target = max(250, n * 250)
        QTimer.singleShot(0, lambda: self.resizeDocks(
            [self._right_dock], [target], Qt.Horizontal
        ))

    def _on_toggle_binding_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._binding_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_sim_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._sim_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_trap_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._trap_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_gnmi_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._gnmi_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_sflow_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._sflow_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_bacnet_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._bacnet_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_redfish_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._redfish_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_console_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._console_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_rules_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
            self._rules_panel.set_rule_engine(self._rule_engine)
        self._rules_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_toggle_tick_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
            self._tick_panel.set_state_store(self.state_store)
        self._tick_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_right_dock_visibility(self, visible: bool):
        """Outer dock hidden externally — uncheck all toolbar buttons.
        Skips minimise events: the dock goes invisible when the OS minimises the
        window, but that is not a genuine user-close action."""
        if not visible and not self.isMinimized():
            for btn in (self._act_panel_binding, self._act_panel_sim,
                        self._act_panel_traps, self._act_panel_gnmi,
                        self._act_panel_sflow, self._act_panel_bacnet,
                        self._act_panel_redfish,
                        self._act_panel_console, self._act_panel_rules,
                        self._act_panel_tick):
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)

    def _build_menus(self):
        menubar = self.menuBar()

        # File
        file_menu = menubar.addMenu("&File")
        self._act_new = QAction("&New Topology", self, shortcut="Ctrl+N")
        self._act_open = QAction("&Open Topology...", self, shortcut="Ctrl+O")
        self._act_save = QAction("&Save Topology...", self, shortcut="Ctrl+S")
        self._act_close_topo = QAction("&Close Topology", self, shortcut="Ctrl+W")
        self._act_export_json = QAction("Export as &JSON...", self)
        file_menu.addAction(self._act_new)
        file_menu.addSeparator()
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_save)
        file_menu.addAction(self._act_close_topo)
        file_menu.addSeparator()
        file_menu.addAction(self._act_export_json)
        file_menu.addSeparator()
        file_menu.addAction(QAction("E&xit", self, shortcut="Ctrl+Q",
                                    triggered=self.close))

        # Device
        dev_menu = menubar.addMenu("&Devices")
        self._act_add_device = QAction("&Add Device...", self, shortcut="Ctrl+D")
        self._act_bulk_add      = QAction("Bulk Add Devices...", self)
        self._act_remove_selected = QAction("&Remove Selected", self, shortcut="Del")
        dev_menu.addAction(self._act_add_device)
        dev_menu.addAction(self._act_bulk_add)
        dev_menu.addSeparator()
        dev_menu.addAction(self._act_remove_selected)

        # Topology
        topo_menu = menubar.addMenu("&Topology")
        self._act_link_mode   = QAction("&Link Mode", self, checkable=True, shortcut="Ctrl+L")
        self._act_fit_view    = QAction("&Fit View", self, shortcut="Ctrl+Shift+F")
        topo_menu.addAction(self._act_link_mode)
        topo_menu.addSeparator()
        topo_menu.addAction(self._act_fit_view)
        topo_menu.addSeparator()

        # Layouts submenu
        layouts_menu = topo_menu.addMenu("&Layouts")
        self._act_layout_default     = QAction("&Default Layout",       self, shortcut="Ctrl+Shift+D")
        self._act_layout_spring      = QAction("&Spring Layout",        self, shortcut="Ctrl+F")
        self._act_layout_shell       = QAction("S&hell Layout",         self)
        self._act_layout_kamada      = QAction("&Kamada-Kawai Layout",  self)
        self._act_layout_default.setToolTip(
            "Restore the original saved positions from the loaded topology."
        )
        self._act_layout_spring.setToolTip(
            "Re-arrange nodes using the Fruchterman-Reingold spring algorithm."
        )
        self._act_layout_shell.setToolTip(
            "Arrange nodes in concentric shells grouped by device type."
        )
        self._act_layout_kamada.setToolTip(
            "Re-arrange nodes using the Kamada-Kawai energy minimisation algorithm."
        )
        layouts_menu.addAction(self._act_layout_default)
        layouts_menu.addSeparator()
        layouts_menu.addAction(self._act_layout_spring)
        layouts_menu.addAction(self._act_layout_shell)
        layouts_menu.addAction(self._act_layout_kamada)

        # Simulation
        sim_menu = menubar.addMenu("&Simulation")
        self._act_generate = QAction("&Generate Datasets",      self, shortcut="F5")
        self._act_start    = QAction("&Start SNMP Simulator",   self, shortcut="F6")
        self._act_stop     = QAction("S&top SNMP Simulator",    self, shortcut="F7")
        self._act_clear    = QAction("&Clear Simulation",       self)
        self._act_discover = QAction("&Discover Topology via SNMP...", self, shortcut="F8")
        self._act_gnmi_start  = QAction("Start &gNMI Server",    self, shortcut="F9")
        self._act_gnmi_stop   = QAction("Stop g&NMI Server",     self, shortcut="F10")
        self._act_sflow_start = QAction("Start s&Flow Agent",    self, shortcut="F11")
        self._act_sflow_stop  = QAction("Sto&p sFlow Agent",     self, shortcut="F12")
        sim_menu.addAction(self._act_generate)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_start)
        sim_menu.addAction(self._act_stop)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_gnmi_start)
        sim_menu.addAction(self._act_gnmi_stop)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_sflow_start)
        sim_menu.addAction(self._act_sflow_stop)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_clear)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_discover)

        # Help
        help_menu = menubar.addMenu("&Help")
        help_menu.addAction(QAction("&About", self, triggered=self._show_about))
        help_menu.addAction(QAction("SNMP Walk &Command", self, triggered=self._show_snmpwalk))

    def _connect_signals(self):
        # Menu actions
        self._act_new.triggered.connect(self._new_topology)
        self._act_open.triggered.connect(self._open_topology)
        self._act_save.triggered.connect(self._save_topology)
        self._act_close_topo.triggered.connect(self._close_topology)
        self._act_export_json.triggered.connect(self._export_json)
        self._act_add_device.triggered.connect(self._add_device)
        self._act_bulk_add.triggered.connect(self._bulk_add)
        self._act_remove_selected.triggered.connect(self._remove_selected)
        self._act_link_mode.toggled.connect(self._toggle_link_mode)
        self._act_fit_view.triggered.connect(self._topology_view.fit_view)
        self._act_layout_default.triggered.connect(self._apply_default_layout)
        self._topology_view.reset_current_layout_requested.connect(self._reset_current_layout)
        self._act_layout_spring.triggered.connect(lambda: self._apply_algo_layout("spring"))
        self._act_layout_shell.triggered.connect(lambda: self._apply_algo_layout("shell"))
        self._act_layout_kamada.triggered.connect(lambda: self._apply_algo_layout("kamada_kawai"))
        self._act_generate.triggered.connect(self._generate_datasets)
        self._act_start.triggered.connect(self._start_simulator)
        self._act_stop.triggered.connect(self._stop_simulator)
        self._act_clear.triggered.connect(self._clear_simulation)
        self._act_discover.triggered.connect(self._discover_topology)

        # Binding panel
        self._binding_panel.sig_bind.connect(self._on_panel_bind_ips)
        self._binding_panel.sig_unbind.connect(self._on_panel_unbind_ips)

        # Simulation panel
        self._sim_panel.sig_generate.connect(self._generate_datasets)
        self._sim_panel.sig_start.connect(self._start_simulator)
        self._sim_panel.sig_stop.connect(self._stop_simulator)
        self._sim_panel.sig_cancel.connect(self._cancel_binding)
        self._sim_panel.sig_clear.connect(self._clear_simulation)
        self._gnmi_panel.sig_generate.connect(self._generate_gnmi_datasets)
        self._gnmi_panel.sig_gnmi_start.connect(self._start_gnmi_server)
        self._gnmi_panel.sig_gnmi_stop.connect(self._stop_gnmi_server)
        self._gnmi_panel.sig_clear.connect(self._clear_gnmi_data)
        self._gnmi_panel.sig_proxy_toggle.connect(self._on_gnmi_proxy_toggle)

        # gNMI menu actions
        self._act_gnmi_start.triggered.connect(self._start_gnmi_server)
        self._act_gnmi_stop.triggered.connect(self._stop_gnmi_server)

        # sFlow panel signals
        self._sflow_panel.sig_start.connect(self._start_sflow)
        self._sflow_panel.sig_stop.connect(self._stop_sflow)

        # sFlow menu actions
        self._act_sflow_start.triggered.connect(self._start_sflow)
        self._act_sflow_stop.triggered.connect(self._stop_sflow)

        # BACnet panel signals
        self._bacnet_panel.sig_start.connect(self._start_bacnet)
        self._bacnet_panel.sig_stop.connect(self._stop_bacnet)

        # Redfish panel signals
        self._redfish_panel.sig_start.connect(self._start_redfish)
        self._redfish_panel.sig_stop.connect(self._stop_redfish)
        self._redfish_panel.sig_action.connect(self._redfish_action)
        self._redfish_panel.sig_view_log.connect(self._redfish_view_log)
        self._redfish_panel.sig_test_event.connect(self._redfish_test_event)
        self._redfish_panel.sig_subscribe.connect(self._redfish_subscribe)
        self._redfish_panel.sig_unsubscribe.connect(self._redfish_unsubscribe)
        self._redfish_panel.sig_request_subs.connect(self._redfish_refresh_subs)

        # sFlow controller callbacks
        self.sflow.set_log_callback(self._on_sflow_log)
        self.sflow.set_status_callback(
            lambda s: self._log_queue.put(("sflow_status", s))
        )
        self.sflow.set_ready_callback(
            lambda: self._log_queue.put(("sflow_ready",))
        )

        # gNMI controller callbacks
        self.gnmi.set_log_callback(
            lambda msg: self._log_queue.put(("log_gnmi", msg, "info"))
        )
        self.gnmi.set_status_callback(
            lambda s: self._log_queue.put(("gnmi_status", s))
        )
        self.gnmi.set_ready_callback(
            lambda: self._log_queue.put(("gnmi_ready",))
        )

        # SNMPSim callbacks — push into a queue; main-thread timer drains it.
        # Using a queue instead of direct signal emission prevents crashes when
        # the daemon thread emits while Qt is mid-repaint (e.g. on maximize).
        self.snmpsim.set_log_callback(
            lambda msg: self._log_queue.put(("log", msg, "info"))
        )
        self.snmpsim.set_status_callback(
            lambda s: self._log_queue.put(("status", s))
        )
        self.snmpsim.set_ready_callback(
            lambda: self._log_queue.put(("snmpsim_ready",))
        )

        # Trap panel ↔ trap engine
        self._trap_panel.sig_trap_apply.connect(self._trap_engine.configure)
        self._trap_engine.trap_sent.connect(self._trap_panel.add_trap_event)
        self._trap_engine.trap_error.connect(self._trap_panel.add_trap_error)
        # Rule engine toggle from Trap panel
        self._trap_panel.sig_rule_engine.connect(self._on_rule_engine_toggled)
        # Rules panel signals
        self._rules_panel.sig_rule_engine_toggled.connect(self._on_rule_engine_toggled)
        self._rules_panel.sig_rule_toggled.connect(
            lambda name, enabled: self._rule_engine.enable_rule(name, enabled)
        )
        self._rules_panel.sig_rules_imported.connect(self._on_rules_imported)
        # Propagate trap_sent to rules panel stats
        self._trap_engine.trap_sent.connect(self._on_rule_trap_sent)
        # Update topology graph immediately when a link rule fires (before SNMP delivery)
        self._trap_engine.link_state_changed.connect(self._on_link_state_changed)

        # Topology scene signals
        scene = self._topology_view.topology_scene
        scene.link_created.connect(self._on_link_created)
        scene.device_moved.connect(self._on_device_moved)
        scene.node_right_clicked.connect(self._on_node_right_click)
        scene.edge_right_clicked.connect(self._on_edge_right_click)

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow { background: #0d1117; }
            QMenuBar {
                background: #161b22;
                color: #e6edf3;
                border-bottom: 1px solid #30363d;
            }
            QMenuBar::item:selected { background: #21262d; }
            QMenu {
                background: #161b22;
                color: #e6edf3;
                border: 1px solid #30363d;
            }
            QMenu::item:selected { background: #1f6feb; }
            QMenu::item:disabled { color: #484f58; }
            QDockWidget {
                color: #e6edf3;
                background: #161b22;
            }
            QDockWidget::title {
                background: #21262d;
                padding: 4px;
                border-bottom: 1px solid #30363d;
            }
            QStatusBar { background: #161b22; color: #8b949e; }
            SNMPPanel { background: #161b22; }
            TrapPanel { background: #161b22; }
            QGroupBox {
                color: #8b949e;
                border: 1px solid #30363d;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 4px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }
            QLabel { color: #e6edf3; }
            QScrollBar:vertical {
                background: #0d1117; width: 10px; margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #30363d; border-radius: 5px; min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #58a6ff; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal {
                background: #0d1117; height: 10px; margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #30363d; border-radius: 5px; min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover { background: #58a6ff; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollArea { background: #161b22; border: none; }
            QDialog { background: #161b22; color: #e6edf3; }
            QDialogButtonBox QPushButton {
                background: #21262d; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 4px; padding: 4px 12px;
            }
            QDialogButtonBox QPushButton:hover { background: #30363d; }
            QDialogButtonBox QPushButton:pressed { background: #0d1117; }
            QSpinBox, QDoubleSpinBox {
                background: #21262d; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 4px; padding: 2px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                background: #30363d; border: none; width: 16px;
            }
        """)

    # ------------------------------------------------------------------ #
    #  Device operations                                                   #
    # ------------------------------------------------------------------ #

    def _add_device(self):
        dlg = DeviceDialog(self, ip_manager=self.ip_manager)
        if dlg.exec() == DeviceDialog.Accepted:
            values = dlg.get_values()
            device = Device(**values)
            # Reserve IP
            self.ip_manager.reserve(device.ip_address)
            self.device_manager.add_device(device)
            # Place at center of current view
            view_center = self._topology_view.mapToScene(
                self._topology_view.viewport().rect().center()
            )
            x = view_center.x() + random.randint(-100, 100)
            y = view_center.y() + random.randint(-100, 100)
            self.topology.add_device(device, x, y)
            self._topology_view.topology_scene.add_device_node(device, x, y)
            self._refresh_device_table()
            self._refresh_stats()

            self._console_panel.log(f"Added device: {device.name} ({device.ip_address})", "success")

    def _edit_device(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        dlg = DeviceDialog(self, device=device, ip_manager=self.ip_manager)
        if dlg.exec() == DeviceDialog.Accepted:
            values = dlg.get_values()
            for key, val in values.items():
                setattr(device, key, val)
            device.__post_init__()
            self._refresh_device_table()
            # Refresh node visual
            node = self._topology_view.topology_scene.get_node(device_id)
            if node:
                node.device = device
                node.update()
            self._console_panel.log(f"Updated device: {device.name}", "info")
            # Patch snmprec so sysLocation/sysName changes reflect immediately
            if self._sim_panel._running:
                self._regenerate_device_live(device_id)

    def _remove_device(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        reply = QMessageBox.question(
            self, "Remove Device",
            f"Remove '{device.name}' and all its connections?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.ip_manager.release(device.ip_address)
            # Remove from topology graph (removes edges too)
            for neighbor in list(self.topology.get_neighbors(device_id)):
                self.topology.remove_link(device_id, neighbor.id)
                self._topology_view.topology_scene.remove_link_edge(device_id, neighbor.id)
            self.topology.remove_device(device_id)
            self.device_manager.remove_device(device_id)
            self._topology_view.topology_scene.remove_device_node(device_id)
            self._refresh_device_table()
            self._refresh_stats()

            self._console_panel.log(f"Removed device: {device.name}", "warning")

    def _remove_selected(self):
        selected_rows = set(idx.row() for idx in self._device_table.selectedIndexes())
        device_ids = []
        for row in selected_rows:
            id_item = self._device_table.item(row, 0)
            if id_item:
                dev_id = id_item.data(Qt.UserRole)
                if dev_id:
                    device_ids.append(dev_id)
        for dev_id in device_ids:
            self._remove_device(dev_id)

    def _bulk_add(self):
        dlg = BulkAddDialog(self)
        if dlg.exec() == QDialog.Accepted:
            values = dlg.get_values()
            devices = self.device_manager.bulk_add(
                values["device_type"], values["vendor"],
                values["count"], self.ip_manager
            )
            # Place devices in grid layout on canvas
            cols = max(1, int(values["count"] ** 0.5))
            view_center = self._topology_view.mapToScene(
                self._topology_view.viewport().rect().center()
            )
            spacing = 150
            for i, device in enumerate(devices):
                col = i % cols
                row = i // cols
                x = view_center.x() + (col - cols / 2) * spacing
                y = view_center.y() + row * spacing
                self.topology.add_device(device, x, y)
                self._topology_view.topology_scene.add_device_node(device, x, y)
            self._refresh_device_table()
            self._refresh_stats()

            self._console_panel.log(
                f"Added {len(devices)} devices ({values['device_type'].value}s)",
                "success"
            )


    # ------------------------------------------------------------------ #
    #  Topology operations                                                 #
    # ------------------------------------------------------------------ #

    # -- layout helpers -------------------------------------------------- #

    def _snapshot_default_positions(self):
        """Capture current topology positions as the Default Layout baseline."""
        positions = {
            dev.id: self.topology.get_position(dev.id)
            for dev in self.topology.get_all_devices()
        }
        self._default_positions = positions
        self._current_layout_positions = dict(positions)
        self._algo_layout_active = False

    _LAYOUT_LABELS = {
        "spring":       "Spring",
        "shell":        "Shell",
        "kamada_kawai": "Kamada-Kawai",
    }

    def _layout_actions(self):
        return [
            self._act_layout_default,
            self._act_layout_spring,
            self._act_layout_shell,
            self._act_layout_kamada,
        ]

    def _reset_current_layout(self):
        """Restore node positions to the last applied layout, undoing any drags."""
        scene = self._topology_view.topology_scene
        if not scene._nodes or not self._current_layout_positions:
            return
        positions = {
            nid: self._current_layout_positions[nid]
            for nid in scene._nodes
            if nid in self._current_layout_positions
        }
        if not positions:
            return
        self._topology_view.apply_force_layout_positions(positions)
        for dev_id, (x, y) in positions.items():
            self.topology.set_position(dev_id, x, y)
        self._status_label.setText("Layout reset — node positions restored.")

    def _apply_default_layout(self):
        """Restore every node to the baseline position captured at load/template time."""
        scene = self._topology_view.topology_scene
        if not scene._nodes:
            return
        positions = {
            nid: self._default_positions[nid]
            for nid in scene._nodes
            if nid in self._default_positions
        }
        if not positions:
            return
        self._topology_view.apply_force_layout_positions(positions)
        self._algo_layout_active = False
        self._current_layout_positions = dict(positions)
        # Persist restored positions back into the topology model so Save works
        for dev_id, (x, y) in positions.items():
            self.topology.set_position(dev_id, x, y)
        self._status_label.setText("Default layout restored.")

    def _apply_algo_layout(self, layout_name: str):
        """Run a NetworkX layout algorithm in a background thread."""
        scene = self._topology_view.topology_scene
        if not scene._nodes:
            return

        node_ids   = list(scene._nodes.keys())
        edge_pairs = list(scene._edges.keys())

        # Kamada-Kawai is O(n³) — warn and offer a faster alternative for large graphs
        _KK_WARN_THRESHOLD = 200
        if layout_name == "kamada_kawai" and len(node_ids) > _KK_WARN_THRESHOLD:
            msg = (
                f"Kamada-Kawai layout on <b>{len(node_ids)} nodes</b> is very slow "
                f"(O(n³) complexity) and may take several minutes.<br><br>"
                f"Use <b>Spring Layout</b> instead? It produces similar results "
                f"and runs in seconds."
            )
            box = QMessageBox(self)
            box.setWindowTitle("Large Topology Warning")
            box.setIcon(QMessageBox.Warning)
            box.setText(msg)
            box.setTextFormat(Qt.RichText)
            btn_spring = box.addButton("Use Spring Layout", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() == btn_spring:
                layout_name = "spring"
            else:
                return

        # Collect device types so shell layout can group by tier
        device_types = {}
        for nid in node_ids:
            dev = self.device_manager.get_device(nid)
            if dev:
                device_types[nid] = dev.device_type

        label = self._LAYOUT_LABELS.get(layout_name, layout_name.capitalize())
        for act in self._layout_actions():
            act.setEnabled(False)
        self._status_label.setText(
            f"Computing {label} layout for {len(node_ids)} nodes…"
        )
        self._topology_view.show_spinner(f"Computing {label} layout…")
        self._current_layout_name = layout_name

        self._force_worker = ForceLayoutWorker(node_ids, edge_pairs,
                                               layout_name, device_types)
        self._force_thread = QThread(self)
        self._force_worker.moveToThread(self._force_thread)

        self._force_thread.started.connect(self._force_worker.run)
        self._force_worker.finished.connect(self._on_layout_done)
        self._force_worker.error.connect(self._on_layout_error)
        self._force_worker.finished.connect(self._force_thread.quit)
        self._force_worker.error.connect(self._force_thread.quit)
        self._force_thread.finished.connect(self._force_worker.deleteLater)

        self._force_thread.start()

    def _on_layout_done(self, positions: dict):
        self._topology_view.hide_spinner()
        self._topology_view.apply_force_layout_positions(positions)
        self._algo_layout_active = True
        self._current_layout_positions = dict(positions)
        for dev_id, (x, y) in positions.items():
            self.topology.set_position(dev_id, x, y)
        for act in self._layout_actions():
            act.setEnabled(True)
        label = self._LAYOUT_LABELS.get(
            getattr(self, "_current_layout_name", ""), "")
        self._status_label.setText(
            f"{label} layout applied to {len(positions)} nodes."
        )

    def _on_layout_error(self, msg: str):
        self._topology_view.hide_spinner()
        for act in self._layout_actions():
            act.setEnabled(True)
        label = self._LAYOUT_LABELS.get(
            getattr(self, "_current_layout_name", ""), "Layout")
        self._status_label.setText(f"{label} layout error: {msg}")

    def _toggle_link_mode(self, enabled: bool):
        self._link_mode = enabled
        self._topology_view.topology_scene.set_link_mode(enabled)
        self._act_link_mode.setChecked(enabled)
        if enabled:
            self._topology_view.setDragMode(self._topology_view.NoDrag)
            self._status_label.setText("Link Mode: click source then destination")
        else:
            self._topology_view.setDragMode(self._topology_view.RubberBandDrag)
            self._status_label.setText("Ready")

    def _on_link_created(self, src_id: str, dst_id: str):
        ok = self.topology.add_link(src_id, dst_id)
        if ok:
            self._topology_view.topology_scene.add_link_edge(src_id, dst_id)
            src = self.device_manager.get_device(src_id)
            dst = self.device_manager.get_device(dst_id)
            if src and dst:
                self._console_panel.log(f"Linked: {src.name} ↔ {dst.name}", "success")
            self._refresh_stats()

    def _on_device_moved(self, device_id: str, x: float, y: float):
        self.topology.set_position(device_id, x, y)
        if not self._algo_layout_active:
            self._default_positions[device_id] = (x, y)

    def _on_node_right_click(self, device_id: str, screen_pos):
        # Use popup() instead of exec() to avoid a nested event loop.
        # exec() blocks by running its own QEventLoop; during that loop,
        # cross-thread signals (IndexWorker progress, link-drain timer, etc.)
        # are dispatched and trigger Qt UI updates that share the main window's
        # QBackingStore with the QGraphicsView.  On Windows this causes
        # "QBackingStore::endPaint() called with active painter" → crash.
        # popup() shows the menu inside the normal top-level event loop where
        # repaints cannot be re-entered.
        device = self.device_manager.get_device(device_id)
        _menu_style = """
            QMenu { background: #161b22; color: #e6edf3; border: 1px solid #30363d; }
            QMenu::item:selected { background: #1f6feb; }
        """
        menu = QMenu(self)
        menu.setStyleSheet(_menu_style)
        edit_act     = menu.addAction("Edit Device...")
        sim_active   = self.snmpsim.is_running() or self.gnmi.is_running()
        remove_act   = None if sim_active else menu.addAction("Remove Device")
        menu.addSeparator()
        locate_act   = menu.addAction("Locate on Graph")
        menu.addSeparator()
        info_act     = menu.addAction("Show Info")

        trap_actions: dict = {}
        if device and self._sim_panel._running:
            menu.addSeparator()
            trap_menu = menu.addMenu("Send Trap \u25b6")
            trap_menu.setStyleSheet(_menu_style)
            _LINK_TRAPS = {TrapType.LINK_DOWN, TrapType.LINK_UP}
            applicable = [
                t for t in get_applicable_traps(
                    device.device_type.value, device.vendor.value, device.model_name
                )
                if t not in _LINK_TRAPS
            ]
            for tt in applicable:
                act = trap_menu.addAction(TRAP_DEFINITIONS[tt].display_name)
                trap_actions[act] = tt

        def _dispatch(action):
            if action == edit_act:
                self._edit_device(device_id)
            elif remove_act and action == remove_act:
                self._remove_device(device_id)
            elif action == locate_act:
                self._locate_device_on_graph(device_id)
            elif action == info_act:
                self._show_device_info(device_id)
            elif action in trap_actions and device:
                self._send_trap(device, trap_actions[action])

        menu.triggered.connect(_dispatch)
        menu.popup(screen_pos)

    def _on_edge_right_click(self, src_id: str, dst_id: str, screen_pos):
        src = self.device_manager.get_device(src_id)
        dst = self.device_manager.get_device(dst_id)
        if not src or not dst:
            return
        broken = self.topology.is_link_broken(src_id, dst_id)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #161b22; color: #e6edf3; border: 1px solid #30363d; }
            QMenu::item:selected { background: #1f6feb; }
        """)
        menu.addSection(f"{src.name} — {dst.name}")
        if broken:
            toggle_act = menu.addAction("Restore Link")
        else:
            toggle_act = menu.addAction("Break Link")

        def _dispatch(action):
            if action != toggle_act:
                return
            if broken:
                self.topology.restore_link(src_id, dst_id)
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, False)
                self._console_panel.log(f"Link restored: {src.name} <-> {dst.name}", "success")
                trap_type = TrapType.LINK_UP
            else:
                self.topology.break_link(src_id, dst_id)
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, True)
                self._console_panel.log(f"Link broken: {src.name} <-> {dst.name}", "error")
                trap_type = TrapType.LINK_DOWN
            # Resync the rule engine's iface snapshot BEFORE the explicit trap so
            # the ticker doesn't re-fire a duplicate LinkDown/LinkUp on the
            # up<->down transition break_link/restore_link just caused.
            for dev in (src, dst):
                self._rule_engine.sync_iface_history(dev)
            if self._sim_panel._running:
                for dev, peer_id in ((src, dst_id), (dst, src_id)):
                    iface = next(
                        (i for i in dev.interfaces if i.connected_to_device == peer_id),
                        dev.interfaces[0] if dev.interfaces else None,
                    )
                    kwargs = {"iface_index": iface.index} if iface else {}
                    self._trap_engine.send_trap(dev, trap_type, **kwargs)
            # Regenerate snmprec for both devices live if simulator is running
            self._regenerate_device_live(src_id)
            self._regenerate_device_live(dst_id)

        menu.triggered.connect(_dispatch)
        menu.popup(screen_pos)

    # ── Trap helpers ──────────────────────────────────────────────────────────

    def _send_trap(self, device: Device, trap_type: TrapType):
        self._trap_engine.send_trap(device, trap_type)

    def _on_snmp_threshold_changed(self, device_ip: str, rule_name: str):
        """Called from the SnmpSetAgent thread when a threshold is SET via SNMP."""
        try:
            import json as _json
            overrides = self._rule_engine.get_all_device_overrides()
            with open(self._device_thresholds_path, "w", encoding="utf-8") as _f:
                _json.dump(overrides, _f, indent=2)
        except Exception:
            pass
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(self._rules_panel, "refresh",
                                 Qt.ConnectionType.QueuedConnection)

    def _lookup_device_by_ip(self, ip: str):
        """Called from SnmpSetAgent thread — device_manager reads are thread-safe."""
        for d in self.device_manager.get_all_devices():
            if d.ip_address == ip or d.mgmt_ip == ip:
                return d
        return None

    def _on_snmp_device_updated(self, device):
        """Called from SnmpSetAgent thread after SNMP SET changes a device attribute."""
        try:
            from core.snmprec_generator import SNMPRecGenerator
            SNMPRecGenerator(self._snmp_datasets_dir).generate_device(device, self.topology)
        except Exception as exc:
            log.warning("[MainWindow] snmprec regen after SNMP SET: %s", exc)
        # Post to the thread-safe ui_queue that the main-thread drain loop handles.
        # Avoids crossing into Qt from a non-Qt thread via QMetaObject.invokeMethod.
        try:
            from api.state import AppState
            AppState.get().notify_ui("sync_devices")
        except Exception:
            pass

    def _on_rule_engine_toggled(self, enabled: bool):
        self._trap_engine.set_rule_engine_enabled(enabled)
        self._trap_panel.set_rule_engine_active(enabled)
        self._rules_panel.set_engine_active(enabled)   # visual-only, no signal re-emission
        status = "enabled" if enabled else "disabled"
        if hasattr(self, '_console_panel'):
            self._console_panel.log(f"[RuleEngine] Rule-driven trap generation {status}.", "info")
        if enabled:
            self._send_rule_engine_test_traps()

    def _send_rule_engine_test_traps(self):
        """Send one trap of every type so the receiver can verify end-to-end connectivity."""
        devices = self.device_manager.get_all_devices()
        if not devices:
            return

        by_type = {}
        for d in devices:
            by_type.setdefault(d.device_type.value, d)

        first  = devices[0]
        router = by_type.get("router",  first)
        server = by_type.get("server",  first)

        test_traps = [
            (first,  TrapType.COLD_START),
            (first,  TrapType.WARM_START),
            (first,  TrapType.LINK_DOWN),
            (first,  TrapType.LINK_UP),
            (first,  TrapType.AUTH_FAILURE),
            (router, TrapType.BGP_DOWN),
            (server, TrapType.UPS_ON_BATTERY),
            (server, TrapType.UPS_LOW_BATTERY),
            (first,  TrapType.CPU_HIGH),
            (first,  TrapType.MEMORY_HIGH),
            (first,  TrapType.TEMPERATURE_ALERT),
            (first,  TrapType.LINK_FLAP),
            (first,  TrapType.RACK_FAILURE),
        ]

        _trap_rule = {
            TrapType.LINK_DOWN:         "LinkDown",
            TrapType.LINK_UP:           "LinkUp",
            TrapType.BGP_DOWN:          "BGPSessionDown",
            TrapType.UPS_ON_BATTERY:    "UPSOnBattery",
            TrapType.UPS_LOW_BATTERY:   "UPSLowBattery",
            TrapType.CPU_HIGH:          "HighCPU",
            TrapType.MEMORY_HIGH:       "HighMemory",
            TrapType.TEMPERATURE_ALERT: "HighTemperature",
            TrapType.LINK_FLAP:         "LinkFlap",
            TrapType.RACK_FAILURE:      "RackFailure",
        }

        from core.trap_engine import TrapEvent
        from datetime import datetime
        now_ts = datetime.now().strftime("%H:%M:%S")
        for device, trap_type in test_traps:
            # Add to the in-app table immediately (guaranteed, no async path)
            event = TrapEvent(device, trap_type, "Rule engine test — receiver connectivity check")
            self._trap_panel.add_trap_event(event)
            # Fire UDP to the external receiver; no_table=True avoids a duplicate
            # entry if the async send also succeeds
            self._trap_engine.send_trap(device, trap_type, no_table=True)
            # Reflect the initial test fire in the rules panel Fired column
            rule_name = _trap_rule.get(trap_type)
            if rule_name:
                self._rule_engine.record_manual_fire(rule_name)
                self._rules_panel.update_rule_stats(
                    rule_name,
                    fired=self._rule_engine.get_total_fired_count(rule_name),
                    last_ts=now_ts,
                )

        self._rules_panel.update_stats(self._rule_engine.get_grand_total_fired())

        if hasattr(self, '_console_panel'):
            self._console_panel.log(
                f"[RuleEngine] Sent {len(test_traps)} test traps "
                f"({len({t for _, t in test_traps})} types) to verify receiver connectivity.",
                "info",
            )

    def _on_rules_imported(self, rules: list):
        for rule in rules:
            self._rule_engine.add_rule(rule)
        self._rules_panel.refresh()

    def _on_rule_trap_sent(self, event):
        if event.rule_name:
            self._rules_panel.update_rule_stats(
                event.rule_name,
                fired=self._rule_engine.get_total_fired_count(event.rule_name),
                last_ts=event.timestamp.strftime("%H:%M:%S"),
            )
            self._rules_panel.update_stats(self._rule_engine.get_grand_total_fired())

    def _on_link_state_changed(self, device, iface_index: int, is_up: bool):
        """Update the topology graph when the rule engine fires LinkDown or LinkUp."""
        iface = next((i for i in device.interfaces if i.index == iface_index), None)
        if iface is None:
            return
        peer_id = iface.connected_to_device
        if not peer_id:
            return
        self._topology_view.topology_scene.set_edge_broken(device.id, peer_id, not is_up)

    def _regenerate_device_live(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return

        datasets_dir  = self._snmp_datasets_dir
        gnmi_dir      = self._gnmi_datasets_dir
        topology      = self.topology
        gnmi_ctrl     = self.gnmi
        is_gnmi       = device.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)

        def _work():
            # Use patch_metrics + patch_lldp instead of generate_device.
            #
            # generate_device() rewrites the entire .snmprec file (thousands of
            # static OIDs) on the main thread.  snmpsim detects the mtime change
            # and triggers a full blocking index rebuild inside its asyncio event
            # loop — making every device unreachable for up to a minute.
            #
            # patch_metrics rewrites only the dynamic counter OIDs (including
            # ifOperStatus); patch_lldp rewrites only the LLDP/CDP neighbor
            # section.  Both pre-build the dbm index so snmpsim finds it
            # immediately and skips its own rebuild.  Running off the main thread
            # keeps the Qt event loop live throughout.
            gen = SNMPRecGenerator(output_dir=datasets_dir)
            try:
                gen.patch_metrics(device)
                gen.patch_lldp(device, topology)
            except Exception:
                pass
            if is_gnmi:
                try:
                    GNMIDataGenerator(gnmi_dir).regenerate(device, topology)
                    gnmi_ctrl.reload_device(device.ip_address)
                except Exception:
                    pass

        threading.Thread(target=_work, daemon=True, name="snmp-link-patch").start()

    def _show_device_info(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        neighbors = self.topology.get_neighbors(device_id)

        neighbor_lines = []
        for neighbor in neighbors:
            # MultiGraph: graph[u][v] returns {edge_key: attr_dict}
            for _key, edge in self.topology.graph[device_id][neighbor.id].items():
                if edge.get("src_node") == device_id:
                    local_idx  = edge.get("src_iface", 0)
                    remote_idx = edge.get("dst_iface", 0)
                else:
                    local_idx  = edge.get("dst_iface", 0)
                    remote_idx = edge.get("src_iface", 0)
                layer = edge.get("layer", "production")
                if layer == "power":
                    neighbor_lines.append(f"  {neighbor.name}  (power)")
                else:
                    local_port  = device.interfaces[local_idx].name  if local_idx  < len(device.interfaces)   else f"port{local_idx}"
                    remote_port = neighbor.interfaces[remote_idx].name if remote_idx < len(neighbor.interfaces) else f"port{remote_idx}"
                    neighbor_lines.append(f"  {neighbor.name}  [{local_port} <-> {remote_port}] ({layer})")

        neighbor_text = ("\n" + "\n".join(neighbor_lines)) if neighbor_lines else "  None"

        info = (
            f"Name:        {device.name}\n"
            f"Type:        {device.device_type.value}\n"
            f"Vendor:      {device.vendor.value}\n"
            f"Model:       {device.model_name or _default_model_name(device)}\n"
            f"OS:          {device.os_name}\n"
            f"OS Version:  {device.os_version}\n"
            + (f"Prod IP:     {device.ip_address}\n" if device.ip_address else "")
            + (f"Mgmt IP:     {device.mgmt_ip}\n"     if device.mgmt_ip     else "")
            + f"SNMP Port:   {device.snmp_port}\n"
            f"gNMI Port:   {device.gnmi_port}\n"
            f"Community:   {device.snmp_community}\n"
            f"Interfaces:  {device.interface_count}\n"
            f"Location:    {device.sys_location}\n"
            f"CPU:         {device.cpu_usage}%\n"
            f"Memory:      {device.memory_used // (1024**2)} / {device.memory_total // (1024**2)} MB\n"
            f"Neighbors:{neighbor_text}\n"
        )
        QMessageBox.information(self, f"Device: {device.name}", info)

    def _on_device_table_double_click(self, index):
        row = index.row()
        id_item = self._device_table.item(row, 0)
        if id_item:
            dev_id = id_item.data(Qt.UserRole)
            if dev_id:
                self._edit_device(dev_id)

    def _locate_device_on_graph(self, device_id: str):
        """Select the node and zoom the canvas to it."""
        scene = self._topology_view.topology_scene
        node = scene.get_node(device_id)
        if not node:
            return
        scene.clearSelection()
        node.setSelected(True)
        bounds = node.sceneBoundingRect()
        self._topology_view.fitInView(
            bounds.adjusted(-150, -150, 150, 150),
            Qt.KeepAspectRatio,
        )
        self._topology_view._sync_zoom_after_fit()

    def _on_device_table_right_click(self, pos):
        item = self._device_table.itemAt(pos)
        if not item:
            return
        id_item = self._device_table.item(item.row(), 0)
        if not id_item:
            return
        device_id = id_item.data(Qt.UserRole)
        if device_id:
            screen_pos = self._device_table.viewport().mapToGlobal(pos)
            self._on_node_right_click(device_id, screen_pos)

    # ------------------------------------------------------------------ #
    #  Simulation operations                                               #
    # ------------------------------------------------------------------ #

    def _generate_datasets(self):
        if self.topology.node_count() == 0:
            QMessageBox.warning(self, "No Devices", "Add devices to the topology first.")
            return
        if self.snmpsim.is_running():
            QMessageBox.warning(self, "Simulator Running",
                                "Stop the simulator before regenerating datasets.")
            return

        self._console_panel.log("Starting dataset generation...", "info")
        self._sim_panel.set_status("Generating...")
        self._sim_panel.show_progress(0, self.topology.node_count())

        # Disable mouse interaction on the scene for the entire
        # generation+indexing pipeline.  This blocks hover events (which show the
        # tooltip) and drag events while background threads are running.  Hover
        # events were the proximate crash trigger: showing the tooltip caused a DWM
        # compositing repaint to race with the scene's own QPainter, producing
        # QBackingStore::endPaint() / STATUS_ACCESS_VIOLATION on Windows.
        # setInteractive(False) keeps the canvas visible and repainting normally
        # (nodes stay where they are, progress is shown), but silently drops all
        # mouse input until the workers finish.
        self._topology_view.setInteractive(False)

        self._worker = GeneratorWorker(self.topology, self._snmp_datasets_dir)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_gen_progress)
        self._worker.log.connect(self._console_panel.log)
        self._worker.finished.connect(self._on_gen_finished)
        self._worker.error.connect(self._on_gen_error)
        self._worker_thread.start()

    def _update_sim_panel_counts(self):
        """Push current device and link counts into the SNMP simulator panel."""
        dm = self.device_manager
        self._sim_panel.set_device_counts(
            len(dm.get_devices_by_type(DeviceType.SWITCH)),
            len(dm.get_devices_by_type(DeviceType.ROUTER)),
            len(dm.get_devices_by_type(DeviceType.SERVER)),
            len(dm.get_devices_by_type(DeviceType.FIREWALL)),
            len(dm.get_devices_by_type(DeviceType.LOAD_BALANCER)),
            len(dm.get_devices_by_type(DeviceType.UPS)),
            len(dm.get_devices_by_type(DeviceType.PDU)),
            len(dm.get_devices_by_type(DeviceType.FLOOR_PDU)),
            oob_switches=len(dm.get_devices_by_type(DeviceType.OOB_SWITCH)),
            sensors=len(dm.get_devices_by_type(DeviceType.SENSOR)),
            generators=len(dm.get_devices_by_type(DeviceType.GENERATOR)),
            crahs=len(dm.get_devices_by_type(DeviceType.CRAH)),
            cdus=len(dm.get_devices_by_type(DeviceType.CDU)),
        )
        self._sim_panel.set_link_counts(
            prod=len(self.topology.get_edges_by_layer("production")),
            mgmt=len(self.topology.get_edges_by_layer("management")),
            power=len(self.topology.get_edges_by_layer("power")),
        )

    def _on_gen_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _on_index_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _on_gen_finished(self):
        self._worker_thread.quit()
        self._worker_thread.wait()
        files = self._worker.result
        self._generated_files = files
        self._console_panel.log(
            f"[SNMP] {len(files)} .snmprec files generated",
            "success"
        )
        self._refresh_stats()
        # Pre-build snmpsim indexes now so simulator starts instantly
        self._sim_panel.set_status("Building indexes…")
        self._console_panel.log("Pre-building SNMP indexes (parallel)…", "info")
        self._sim_panel.show_progress(0, len(files))

        self._index_worker = IndexWorker(self.snmpsim, self.snmpsim.datasets_dir)
        self._index_thread = QThread()
        self._index_worker.moveToThread(self._index_thread)
        self._index_thread.started.connect(self._index_worker.run)
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.finished.connect(self._on_index_finished)
        self._index_worker.error.connect(self._on_index_error)
        # LowPriority: index building is a background task — UI must stay responsive
        self._index_thread.start(QThread.LowPriority)

    def _on_index_finished(self, count: int):
        self._index_thread.quit()
        self._index_thread.wait()
        self._console_panel.log(f"Indexes built for {count} datasets — simulator will start instantly.", "success")
        self._sim_panel.set_status("Datasets ready")
        self._sim_panel.set_datasets_ready(True)
        self._gnmi_panel.set_datasets_ready(bool(self._gnmi_files))
        self._topology_view.setInteractive(True)

    def _on_index_error(self, error: str):
        self._index_thread.quit()
        self._index_thread.wait()
        self._console_panel.log(f"Index pre-build warning: {error}", "warning")
        # Still mark datasets ready — snmpsim will build indexes itself on start
        self._sim_panel.set_status("Datasets ready")
        self._sim_panel.set_datasets_ready(True)
        self._gnmi_panel.set_datasets_ready(bool(self._gnmi_files))
        self._topology_view.setInteractive(True)

    def _on_gen_error(self, error: str):
        self._worker_thread.quit()
        self._worker_thread.wait()
        self._console_panel.log(f"Generation error: {error}", "error")
        self._sim_panel.set_status("Error")
        self._topology_view.setInteractive(True)

    def _start_simulator(self):
        if self.topology.node_count() == 0:
            QMessageBox.warning(self, "No Devices",
                                "Generate datasets first (no devices in topology).")
            return
        if not self._generated_files:
            reply = QMessageBox.question(
                self, "No Datasets",
                "No datasets generated yet. Generate now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._generate_datasets()
            return

        interface = self._binding_panel.selected_interface
        if not interface:
            QMessageBox.warning(
                self, "No Interface Selected",
                "Select a network interface in the 'Network Interface Binding' panel before starting.\n"
                "Device IPs must be bound to an adapter for SNMP polling to work."
            )
            return

        if not is_admin():
            reply = QMessageBox.warning(
                self, "Administrator Required",
                "Binding IPs via netsh requires Administrator privileges.\n\n"
                "The application does not appear to be running as Administrator.\n"
                "IP binding may fail — continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        # If IPs are already bound (e.g. via the binding panel), skip rebinding
        if self._bound_ips:
            self._console_panel.log(
                f"IPs already bound ({len(self._bound_ips)}). Launching SNMPSim...", "info"
            )
            self._launch_snmpsim(self._bound_ips)
            return

        device_ips = _all_bind_ips(self.device_manager.get_all_devices())
        mask = self._binding_panel.subnet_mask

        self._console_panel.log(
            f"Binding {len(device_ips)} IPs to interface '{interface}'...", "info"
        )
        self._sim_panel.set_status("Binding IPs...")
        self._sim_panel.show_progress(0, len(device_ips))
        self._sim_panel.set_binding(True)
        self._binding_panel.set_snmp_locked(True)

        self._bound_interface = interface
        self._bind_worker = IPBindWorker(interface, device_ips, mask)
        self._bind_thread = QThread()
        self._bind_worker.moveToThread(self._bind_thread)
        self._bind_thread.started.connect(self._bind_worker.run)
        self._bind_worker.progress.connect(self._on_bind_progress)
        self._bind_worker.log.connect(self._console_panel.log)
        self._bind_worker.finished.connect(self._on_bind_finished)
        self._bind_worker.error.connect(self._on_bind_error)
        self._bind_thread.start()

    def _on_bind_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _cancel_binding(self):
        if self._bind_worker:
            self._bind_worker.cancel()
            # Reset the progress bar immediately so it doesn't freeze on the
            # last bind value while we wait for the worker to notice the flag.
            self._sim_panel.show_progress(0, 1)

    def _on_cancel_unbind_finished(self):
        self._unbind_thread.quit()
        self._unbind_thread.wait()
        self._binding_panel.set_snmp_locked(False)
        self._console_panel.log("Partial IPs removed.", "success")
        self._sim_panel.set_status("Cancelled")
        self._sim_panel.set_datasets_ready(True)

    def _on_bind_finished(self):
        self._bind_thread.quit()
        self._bind_thread.wait()
        self._sim_panel.set_binding(False)

        if self._bind_worker.cancelled:
            partial_ips = self._bind_worker.result  # IPs bound before cancellation
            if partial_ips:
                self._bound_ips    = partial_ips
                self._nte_contexts = getattr(self._bind_worker, "nte_contexts", {})
                self._binding_panel.set_bound_count(
                    len(set(self._bound_ips) | set(self._gnmi_bound_ips))
                )
                self._console_panel.log(
                    f"Binding cancelled — {len(partial_ips)} IP(s) remain bound.",
                    "warning",
                )
            else:
                self._console_panel.log("IP binding cancelled — no IPs were bound.", "warning")
            self._sim_panel.set_status("Cancelled")
            self._sim_panel.set_datasets_ready(True)
            self._binding_panel.set_snmp_locked(False)
            return

        bound_ips = self._bind_worker.result
        self._bound_ips    = bound_ips
        self._nte_contexts = getattr(self._bind_worker, "nte_contexts", {})
        self._binding_panel.set_bound_count(len(set(self._bound_ips) | set(self._gnmi_bound_ips)))

        if not bound_ips:
            self._console_panel.log("No IPs were bound — aborting simulator start.", "error")
            self._sim_panel.set_status("Error: no IPs bound")
            self._sim_panel.set_datasets_ready(True)
            return

        self._launch_snmpsim(bound_ips)

    def _update_topology_edit_actions(self):
        """Enable/disable topology-editing actions based on simulator state."""
        sim_active = self.snmpsim.is_running() or self.gnmi.is_running()
        self._act_add_device.setEnabled(not sim_active)
        self._act_bulk_add.setEnabled(not sim_active)
        self._act_remove_selected.setEnabled(not sim_active)

    def _launch_snmpsim(self, bound_ips: list):
        """Start SNMPSim using the given list of already-bound IPs."""
        failed = len(self.device_manager.get_all_devices()) - len(bound_ips)
        if failed:
            self._console_panel.log(
                f"Warning: {failed} IP(s) could not be bound.", "warning"
            )

        snmp_port = self._sim_panel.get_snmp_port()
        self._console_panel.log(
            f"Launching SNMPSim on port {snmp_port} with {len(bound_ips)} device(s)...", "success"
        )
        ok = self.snmpsim.start(device_ips=bound_ips, port=snmp_port)
        if ok:
            self.state_store.set_log_callback(self._console_panel.log)
            self.state_store.start()
            self.state_store.enable_snmp_sync(self.snmpsim)
            set_port = self._sim_panel.get_set_port()
            if set_port != self._snmp_set_agent.port:
                self._snmp_set_agent = SnmpSetAgent(
                    self._rule_engine,
                    port=set_port,
                    on_change_cb=self._on_snmp_threshold_changed,
                    device_lookup=self._lookup_device_by_ip,
                    on_device_updated=self._on_snmp_device_updated,
                )
            if not self._snmp_set_agent.is_running():
                if self._snmp_set_agent.start():
                    self._console_panel.log(
                        f"SNMP management agent on port {self._snmp_set_agent.port}"
                        f"  (community: <device-ip>)", "info"
                    )
                    self._rules_panel.set_management_endpoint(
                        "0.0.0.0", self._snmp_set_agent.port,
                    )
                else:
                    self._console_panel.log(
                        "Warning: SNMP management agent failed to start on port "
                        f"{self._snmp_set_agent.port} — threshold SET will not work.",
                        "warning",
                    )
            # Process launched — show stop button but keep traps disabled until
            # snmpsim logs "Listening at UDP/IPv4 endpoint" (ready callback).
            self._sim_panel.set_simulator_running(True)
            self._update_topology_edit_actions()
            self._binding_panel.set_snmp_locked(True)
            self._update_sim_panel_counts()
            self._status_label.setText(
                f"SNMPSim starting — loading datasets… ({len(bound_ips)} devices)"
            )
            self._console_panel.log(
                "SNMPSim is loading pre-built datasets — devices will respond once 'Running' is shown.",
                "info"
            )
        else:
            self._sim_panel.set_simulator_running(False)
            self._update_topology_edit_actions()
            self._binding_panel.set_snmp_locked(False)
            self._sim_panel.set_datasets_ready(True)

    def _on_bind_error(self, error: str):
        self._bind_thread.quit()
        self._bind_thread.wait()
        self._sim_panel.set_binding(False)
        self._binding_panel.set_snmp_locked(False)
        self._console_panel.log(f"IP bind error: {error}", "error")
        self._sim_panel.set_status("Error")
        self._sim_panel.set_datasets_ready(True)

    # ------------------------------------------------------------------ #
    #  Binding panel — manual Bind / Remove Binding                        #
    # ------------------------------------------------------------------ #

    def _on_panel_bind_ips(self):
        """Bind all device IPs to the selected adapter without starting a simulator."""
        interface = self._binding_panel.selected_interface
        if not interface:
            QMessageBox.warning(
                self, "No Interface Selected",
                "Select a network interface in the 'Network Interface Binding' panel first."
            )
            return
        devices = self.device_manager.get_all_devices()
        if not devices:
            QMessageBox.warning(self, "No Devices", "Build a topology with devices first.")
            return
        if not is_admin():
            reply = QMessageBox.warning(
                self, "Administrator Required",
                "Binding IPs via netsh requires Administrator privileges.\n"
                "IP binding may fail — continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        device_ips = _all_bind_ips(devices)
        mask = self._binding_panel.subnet_mask
        self._console_panel.log(
            f"Binding {len(device_ips)} IPs to '{interface}'…", "info"
        )
        self._binding_panel.set_snmp_locked(True)
        self._bound_interface = interface

        self._binding_panel.show_progress(0, len(device_ips))
        self._panel_bind_worker = IPBindWorker(interface, device_ips, mask)
        self._panel_bind_thread = QThread()
        self._panel_bind_worker.moveToThread(self._panel_bind_thread)
        self._panel_bind_thread.started.connect(self._panel_bind_worker.run)
        self._panel_bind_worker.progress.connect(self._binding_panel.show_progress)
        self._panel_bind_worker.log.connect(self._console_panel.log)
        self._panel_bind_worker.finished.connect(self._on_panel_bind_ips_finished)
        self._panel_bind_worker.error.connect(self._on_panel_bind_ips_error)

        # Mirror progress + completion into AppState so the web UI sees live updates.
        try:
            from api.state import AppState as _AS
            _s = _AS.get()
            _s.selected_adapter = interface
            _s.subnet_mask = mask
            self._panel_bind_job_id = _s.create_job("bind_ips")
            _s.notify_ui("binding_started")
            _jid = self._panel_bind_job_id

            def _fwd_progress(done, total, s=_s, jid=_jid):
                s.update_job(jid, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            self._panel_bind_worker.progress.connect(_fwd_progress)
        except Exception:
            self._panel_bind_job_id = None

        self._panel_bind_thread.start()

    def _on_panel_bind_ips_finished(self):
        self._panel_bind_thread.quit()
        self._panel_bind_thread.wait()
        self._panel_bind_thread = None
        bound_ips = self._panel_bind_worker.result
        self._bound_ips    = bound_ips
        self._nte_contexts = getattr(self._panel_bind_worker, "nte_contexts", {})
        self._panel_bind_worker = None
        self._binding_panel.set_bound_count(len(set(self._bound_ips) | set(self._gnmi_bound_ips)))
        self._binding_panel.set_snmp_locked(False)
        if bound_ips:
            self._console_panel.log(f"{len(bound_ips)} IPs bound to adapter.", "success")
        else:
            self._console_panel.log("No IPs were bound.", "error")
        try:
            from api.state import AppState as _AS
            _s = _AS.get()
            _s.bound_ips    = list(bound_ips)
            _s.nte_contexts = dict(self._nte_contexts)
            if self._panel_bind_job_id:
                _s.update_job(self._panel_bind_job_id, status="completed",
                              progress_done=len(bound_ips), progress_total=len(bound_ips))
            _s.notify_ui("sync_binding")
        except Exception:
            pass
        self._panel_bind_job_id = None

    def _on_panel_bind_ips_error(self, error: str):
        self._panel_bind_thread.quit()
        self._panel_bind_thread.wait()
        self._panel_bind_thread = None
        self._panel_bind_worker = None
        self._binding_panel.set_snmp_locked(False)
        self._console_panel.log(f"Bind error: {error}", "error")
        try:
            from api.state import AppState as _AS
            _s = _AS.get()
            if self._panel_bind_job_id:
                _s.update_job(self._panel_bind_job_id, status="failed")
            _s.notify_ui("sync_binding")
        except Exception:
            pass
        self._panel_bind_job_id = None

    def _on_panel_unbind_ips(self):
        """Remove all bound IPs (SNMP and gNMI) from the adapter."""
        from api.state import AppState
        _s = AppState.get()
        all_ips = list(set(self._bound_ips) | set(self._gnmi_bound_ips) |
                       set(_s.bound_ips) | set(_s.gnmi_bound_ips))
        iface   = self._bound_interface or self._gnmi_bound_interface or _s.selected_adapter
        if not all_ips or not iface:
            return
        self._console_panel.log(f"Removing {len(all_ips)} bound IPs…", "info")
        self._binding_panel.set_snmp_locked(True)
        self._binding_panel.set_gnmi_locked(True)
        all_contexts = {**_s.nte_contexts, **_s.gnmi_nte_contexts, **self._nte_contexts}

        self._binding_panel.show_progress(0, len(all_ips))
        self._panel_unbind_worker = IPUnbindWorker(iface, all_ips, all_contexts)
        self._panel_unbind_thread = QThread()
        self._panel_unbind_worker.moveToThread(self._panel_unbind_thread)
        self._panel_unbind_thread.started.connect(self._panel_unbind_worker.run)
        self._panel_unbind_worker.progress.connect(self._binding_panel.show_progress)
        self._panel_unbind_worker.log.connect(self._console_panel.log)
        self._panel_unbind_worker.finished.connect(self._on_panel_unbind_ips_finished)

        try:
            self._panel_unbind_job_id = _s.create_job("unbind_ips")
            _s.notify_ui("binding_started")
            _jid = self._panel_unbind_job_id

            def _fwd_unbind_progress(done, total, s=_s, jid=_jid):
                s.update_job(jid, progress_done=done, progress_total=total)
                s.notify_ui("binding_progress", done, total)

            self._panel_unbind_worker.progress.connect(_fwd_unbind_progress)
        except Exception:
            self._panel_unbind_job_id = None

        self._panel_unbind_thread.start()

    def _on_panel_unbind_ips_finished(self):
        self._panel_unbind_thread.quit()
        self._panel_unbind_thread.wait()
        self._panel_unbind_thread = None
        self._panel_unbind_worker = None
        self._bound_ips           = []
        self._bound_interface     = ""
        self._nte_contexts        = {}
        self._gnmi_bound_ips      = []
        self._gnmi_bound_interface = ""
        self._binding_panel.set_bound_count(0)
        self._binding_panel.set_snmp_locked(False)
        self._binding_panel.set_gnmi_locked(False)
        self._console_panel.log("All IPs removed from adapter.", "warning")
        try:
            from api.state import AppState
            _s = AppState.get()
            _s.bound_ips = []
            _s.nte_contexts = {}
            _s.gnmi_bound_ips = []
            _s.gnmi_nte_contexts = {}
            if getattr(self, "_panel_unbind_job_id", None):
                _s.update_job(self._panel_unbind_job_id, status="completed",
                              progress_done=1, progress_total=1)
            _s.notify_ui("sync_binding")
        except Exception:
            pass
        self._panel_unbind_job_id = None

    def _on_snmpsim_ready(self):
        """Called (via queue) when SNMPSim logs its 'Listening at UDP/IPv4 endpoint' line."""
        n_bound = len(self._bound_ips)
        self._status_label.setText(
            f"SNMPSim running — {n_bound} devices — PID {self.snmpsim.get_pid()}"
        )
        self._console_panel.log("SNMPSim is ready — devices are now responding to SNMP polls.", "success")

        # Unfade the entire topology so all layers are visible from the start.
        self._topology_view.topology_scene.set_all_faded(False)

        # Unlock the Rule Engine button now that SNMP is running.
        self._trap_panel.set_rule_engine_available(True)
        self._rules_panel.set_rule_engine_available(True)
        self._tick_panel.set_available(True)


    def _on_gnmi_ready(self):
        """Called (via queue) when gNMI controller signals ready."""
        counts = self.gnmi.target_counts()
        self._gnmi_panel.set_gnmi_running(True)
        self._update_topology_edit_actions()
        self._gnmi_panel.set_gnmi_status("Running")
        self._gnmi_panel.set_gnmi_targets(counts)
        n_direct = self.gnmi.get_per_device_count()
        self._console_panel.log_gnmi(
            f"[gNMI] Simulation ready — "
            f"{counts.get('switch', 0)} switches, {counts.get('router', 0)} routers"
            + (f" | {n_direct} direct server(s)" if n_direct else ""),
            "success"
        )

    def _start_live_discovery(self):
        """Launch a background SNMP discovery scan if one is not already running."""
        if self._live_discovery_running or not self.snmpsim.is_ready():
            return
        if self.topology.node_count() == 0:
            return
        # A previous thread may still be winding down after a stop() timeout.
        # Replacing self._live_discovery_thread while it's running would drop the
        # last Python reference to the QThread, triggering a GC-while-running crash.
        if self._live_discovery_thread and self._live_discovery_thread.isRunning():
            return
        self._live_discovery_running = True
        self._topology_view.topology_scene.set_discovery_running(True)

        # Pre-build adjacency so the drain loop can un-fade edges instantly when
        # both endpoints have been polled, without iterating all edges each tick.
        self._discovered_devices = set()
        self._device_adjacency = {}
        for src_id, dst_id, _ in self.topology.get_links():
            self._device_adjacency.setdefault(src_id, set()).add(dst_id)
            self._device_adjacency.setdefault(dst_id, set()).add(src_id)

        self._live_discovery_worker = LiveDiscoveryWorker(self.topology)
        self._live_discovery_thread = QThread()
        self._live_discovery_worker.moveToThread(self._live_discovery_thread)
        self._live_discovery_thread.started.connect(self._live_discovery_worker.run)
        self._live_discovery_worker.finished.connect(self._on_live_discovery_done)
        self._live_discovery_worker.error.connect(self._on_live_discovery_error)

        # Drain the scan queue every 50 ms on the main thread.
        # A node un-fades when its device is polled; an edge un-fades when BOTH
        # its endpoints have been polled — this ties animation to per-device scan
        # progress, not to when a neighbor happens to report a link first.
        self._link_drain_timer = QTimer(self)
        self._link_drain_timer.setInterval(50)
        self._link_drain_timer.timeout.connect(self._drain_link_queue)
        self._link_drain_timer.start()

        self._live_discovery_thread.start()

    def _drain_link_queue(self):
        """Process all device IDs polled since the last timer tick."""
        if not self._live_discovery_worker:
            return
        scene = self._topology_view.topology_scene
        q = self._live_discovery_worker.link_queue
        changed = False
        while not q.empty():
            try:
                device_id = q.get_nowait()
            except Exception:
                break
            self._discovered_devices.add(device_id)
            # Set flags without per-item repaints; one scene.update() at the end.
            scene.set_node_faded(device_id, False, repaint=False)
            for neighbor_id in self._device_adjacency.get(device_id, ()):
                if neighbor_id in self._discovered_devices:
                    scene.set_edge_faded(device_id, neighbor_id, False, repaint=False)
            changed = True
        if changed:
            scene.update()  # single repaint covering all changes this tick

    def _on_live_discovery_done(self, result):
        """Finalize the graph after all devices have been scanned."""
        # Stop drain timer and do one final drain to flush any last links
        if self._link_drain_timer:
            self._link_drain_timer.stop()
            self._link_drain_timer = None
        self._drain_link_queue()

        self._live_discovery_running = False
        self._live_discovery_thread.quit()
        self._live_discovery_thread.wait()

        scene = self._topology_view.topology_scene
        scene.set_discovery_running(False)
        # Batch all flag changes, then a single repaint at the end.
        for src_id, dst_id in result.matched:
            scene.set_edge_faded(src_id, dst_id, False, repaint=False)
            scene.set_node_faded(src_id, False, repaint=False)
            scene.set_node_faded(dst_id, False, repaint=False)
        for src_id, dst_id in result.missing:
            scene.set_edge_faded(src_id, dst_id, False, repaint=False)
            scene.set_edge_broken(src_id, dst_id, True)   # set_edge_broken has its own update()
        scene.update()

        self._update_sim_panel_counts()

        matched = len(result.matched)
        missing = len(result.missing)
        n_bound = len(self._bound_ips)
        if missing:
            self._status_label.setText(
                f"SNMPSim running — {n_bound} devices — "
                f"SNMP: {matched} links OK, {missing} missing"
            )
            self._console_panel.log(
                f"Live discovery: {matched} matched, {missing} missing links.", "warn"
            )
        else:
            self._status_label.setText(
                f"SNMPSim running — {n_bound} devices — SNMP: all {matched} links OK"
            )
            self._console_panel.log(
                f"Live discovery: all {matched} links confirmed via SNMP.", "success"
            )

    def _on_live_discovery_error(self, error: str):
        """Handle a live discovery failure — un-fade everything so graph remains usable."""
        if self._link_drain_timer:
            self._link_drain_timer.stop()
            self._link_drain_timer = None
        self._live_discovery_running = False
        self._topology_view.topology_scene.set_discovery_running(False)
        if self._live_discovery_thread:
            self._live_discovery_thread.quit()
            self._live_discovery_thread.wait()
        self._topology_view.topology_scene.set_all_faded(False)
        self._console_panel.log(f"Live discovery error: {error}", "error")
        # Simulator is running even though discovery failed — show device counts
        self._update_sim_panel_counts()

    def _stop_simulator(self):
        # Stop any in-flight live discovery scan
        if self._link_drain_timer:
            self._link_drain_timer.stop()
            self._link_drain_timer = None
        if self._live_discovery_thread and self._live_discovery_thread.isRunning():
            self._live_discovery_thread.quit()
            self._live_discovery_thread.wait(2000)
            # Only clear the flag if the thread actually stopped.
            # If it timed out the thread is still alive; _start_live_discovery
            # will refuse to replace its reference, preventing the GC-crash.
            if not self._live_discovery_thread.isRunning():
                self._live_discovery_running = False
        else:
            self._live_discovery_running = False
        # Reset graph: clear broken state and fade everything back
        scene = self._topology_view.topology_scene
        scene.set_discovery_running(False)
        for u, v, _ in self.topology.get_links():
            scene.set_edge_broken(u, v, False)
        scene.set_all_faded(True)

        self._on_rule_engine_toggled(False)
        self._trap_panel.set_rule_engine_available(False)
        self._rules_panel.set_rule_engine_available(False)
        self._tick_panel.set_available(False)
        self._snmp_set_agent.stop()
        self._rules_panel.set_management_endpoint(None, None)
        self.snmpsim.stop()
        self.state_store.disable_snmp_sync()
        self._sim_panel.set_device_counts(0, 0, 0)
        self._sim_panel.set_simulator_running(False)
        self._update_topology_edit_actions()
        self._binding_panel.set_snmp_locked(False)
        # IP bindings are intentionally kept so the user can restart quickly
        # without waiting for rebind.  Use Clear Simulation to release IPs.
        self._sim_panel.set_status("Stopped")
        self._status_label.setText("Stopped")

    # ------------------------------------------------------------------ #
    #  gNMI Dataset generation                                            #
    # ------------------------------------------------------------------ #

    def _generate_gnmi_datasets(self):
        if self.topology.node_count() == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Devices",
                                "Add devices to the topology first.")
            return

        devices = [
            d for d in self.device_manager.get_all_devices()
            if d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)
        ]
        if not devices:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "No Targets",
                                "No switches or routers in topology — nothing to generate.")
            return

        self._gnmi_panel.set_generating(True)
        self._gnmi_panel.show_progress(0, len(devices))
        self._console_panel.log_gnmi(
            f"Generating gNMI datasets for {len(devices)} devices…", "info"
        )

        # Run in a background thread so the UI stays responsive.
        self._gnmi_gen_worker = _GNMIGenWorker(
            devices, self.topology, self._gnmi_datasets_dir
        )
        self._gnmi_gen_thread = QThread(self)
        self._gnmi_gen_worker.moveToThread(self._gnmi_gen_thread)
        self._gnmi_gen_thread.started.connect(self._gnmi_gen_worker.run)
        self._gnmi_gen_worker.progress.connect(self._on_gnmi_gen_progress)
        self._gnmi_gen_worker.finished.connect(self._on_gnmi_gen_finished)
        self._gnmi_gen_worker.error.connect(self._on_gnmi_gen_error)
        self._gnmi_gen_thread.start()

    def _on_gnmi_gen_progress(self, current: int, total: int):
        self._gnmi_panel.show_progress(current, total)

    def _on_gnmi_gen_finished(self):
        self._gnmi_gen_thread.quit()
        self._gnmi_gen_thread.wait()
        files = self._gnmi_gen_worker.result
        self._gnmi_files = files
        self._gnmi_panel.set_generating(False)
        self._gnmi_panel.set_datasets_ready(bool(files))
        self._console_panel.log_gnmi(
            f"[gNMI] {len(files)} datasets generated.", "success"
        )

    def _on_gnmi_gen_error(self, error: str):
        self._gnmi_gen_thread.quit()
        self._gnmi_gen_thread.wait()
        self._gnmi_panel.set_generating(False)
        self._console_panel.log_gnmi(f"gNMI generation error: {error}", "error")

    # ------------------------------------------------------------------ #
    #  gNMI Server start / stop                                            #
    # ------------------------------------------------------------------ #

    def _start_gnmi_server(self):
        if self.topology.node_count() == 0:
            QMessageBox.warning(self, "No Devices",
                                "Generate datasets first — no devices in topology.")
            return
        if not self._gnmi_files and not self._generated_files:
            reply = QMessageBox.question(
                self, "No Datasets",
                "No datasets generated yet. Generate now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._generate_datasets()
            return

        if self.gnmi.is_running():
            self._console_panel.log_gnmi("[gNMI] Server is already running.", "info")
            return

        port      = self._gnmi_panel.server_port
        interface = self._binding_panel.selected_interface

        if not interface:
            QMessageBox.warning(
                self, "No Interface Selected",
                "Select a network interface in the 'Network Interface Binding' "
                "panel before starting.\n"
                "Device IPs must be bound to an adapter for gNMI polling to work."
            )
            return

        self._gnmi_panel.set_gnmi_status("Starting…")
        self._gnmi_panel.set_gnmi_running(False)
        self._update_topology_edit_actions()
        self._binding_panel.set_gnmi_locked(True)

        # If an interface is selected, always bind gNMI's own IPs to it —
        # even if SNMP has already bound the same IPs to its adapter.
        # gRPC needs the bind to go through gNMI's selected adapter to work
        # reliably on Windows; relying on SNMP's netsh pass is not sufficient.
        all_device_ips = [
            d.mgmt_ip if d.mgmt_ip else d.ip_address
            for d in self.device_manager.get_all_devices()
            if d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)
        ]
        # Only skip IPs that gNMI itself has already bound (not SNMP-bound ones).
        gnmi_bound = set(self._gnmi_bound_ips)
        ips_to_bind = [ip for ip in all_device_ips if ip not in gnmi_bound]
        needs_bind = bool(interface and ips_to_bind and is_admin())
        already_bound = set(self._bound_ips) | gnmi_bound
        if needs_bind:
            mask = self._binding_panel.subnet_mask
            self._gnmi_bound_interface = interface
            self._console_panel.log_gnmi(
                f"[gNMI] Binding {len(ips_to_bind)} IPs to '{interface}'…", "info")
            self._gnmi_bind_worker = IPBindWorker(interface, ips_to_bind, mask)
            self._gnmi_bind_thread = QThread()
            self._gnmi_bind_worker.moveToThread(self._gnmi_bind_thread)
            self._gnmi_bind_thread.started.connect(self._gnmi_bind_worker.run)
            self._gnmi_bind_worker.log.connect(self._console_panel.log_gnmi)
            self._gnmi_bind_worker.progress.connect(self._gnmi_panel.show_progress)
            self._gnmi_bind_worker.finished.connect(self._on_gnmi_bind_finished)
            self._gnmi_bind_worker.error.connect(
                lambda e: self._console_panel.log_gnmi(f"[gNMI] Bind error: {e}", "error"))
            self._gnmi_bind_thread.start()
        else:
            # No interface selected or IPs already gNMI-bound — start directly.
            # Still apply a short delay so gRPC sockets have time to activate.
            self._console_panel.log_gnmi("[gNMI] Waiting for IPs to activate…", "info")
            QTimer.singleShot(2000, lambda: self._do_start_gnmi_server(list(already_bound), port))

    def _on_gnmi_bind_finished(self):
        self._gnmi_bind_thread.quit()
        self._gnmi_bind_thread.wait()
        self._gnmi_bound_ips = self._gnmi_bind_worker.result
        self._binding_panel.set_bound_count(len(set(self._bound_ips) | set(self._gnmi_bound_ips)))
        if self._gnmi_bound_ips:
            self._console_panel.log_gnmi(
                f"[gNMI] {len(self._gnmi_bound_ips)} IPs bound.", "success")
        port      = self._gnmi_panel.server_port
        all_bound = list(set(self._bound_ips) | set(self._gnmi_bound_ips))
        # Allow the OS 2 s to fully activate the newly bound IPs before gRPC
        # tries to open TCP sockets on them (critical for loopback adapter IPs).
        self._console_panel.log_gnmi("[gNMI] Waiting for IPs to activate…", "info")
        QTimer.singleShot(2000, lambda: self._do_start_gnmi_server(all_bound, port))

    def _do_start_gnmi_server(self, bound_ips: list, port: int):
        """Actually start the gNMI server — called directly or after IP binding."""
        all_devices = self.device_manager.get_all_devices()
        switch_ips  = [d.ip_address for d in all_devices
                       if d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)]

        # Build {bind_ip: gnmi_port} preferring mgmt_ip as the gRPC bind address.
        # gNMI is a management-plane protocol; when a device has a separate OOB
        # management IP it should be reachable on that address, mirroring how real
        # devices expose gNMI on the management interface rather than the data plane.
        # Dataset files are now named by mgmt_ip (or ip_address when no mgmt_ip),
        # so bind_ip == dataset key — no cross-mapping needed.
        bound_set = set(bound_ips)
        bound_ip_ports: dict = {}
        for d in all_devices:
            if d.device_type not in (DeviceType.SWITCH, DeviceType.ROUTER):
                continue
            bind_ip = (d.mgmt_ip
                       if d.mgmt_ip and d.mgmt_ip in bound_set
                       else d.ip_address)
            if bind_ip in bound_set:
                # Use the panel port (user-configured) as the gRPC bind port for
                # all per-device servers so the "gNMI Port" spinner is the single
                # source of truth.  d.gnmi_port is only a per-device override when
                # it differs from the panel default.
                bound_ip_ports[bind_ip] = port if port else d.gnmi_port

        # Register auto-proxy callback — called from background thread when
        # per-device binding completely fails; dispatched to main thread via QTimer.
        def _on_auto_proxy():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._gnmi_panel.set_proxy_running(True))

        self.gnmi.set_auto_proxy_callback(_on_auto_proxy)

        self._console_panel.log_gnmi("[gNMI] Starting device simulation…", "info")
        ok = self.gnmi.start(
            device_ips=switch_ips,
            port=port,
            bound_ip_ports=bound_ip_ports if bound_ip_ports else None,
        )
        if ok:
            self.state_store.set_log_callback(self._console_panel.log)
            self.state_store.start()
            self._gnmi_panel.set_gnmi_running(True)
            self._update_topology_edit_actions()
            counts   = self.gnmi.target_counts()
            n_direct = self.gnmi.get_per_device_count()
            self._gnmi_panel.set_gnmi_targets(counts)
            self._gnmi_panel.set_direct_servers(n_direct)
            self._status_label.setText(
                f"gNMI running — {n_direct} direct server(s), "
                f"{counts.get('switch', 0)} switches, {counts.get('router', 0)} routers"
            )
        else:
            self._gnmi_panel.set_gnmi_status("Error")
            self._binding_panel.set_gnmi_locked(False)

    def _stop_gnmi_server(self):
        if not self.gnmi.is_running():
            return
        self.gnmi.stop()
        self._gnmi_panel.set_gnmi_running(False)
        self._update_topology_edit_actions()
        self._gnmi_panel.set_gnmi_status("Stopped")
        self._gnmi_panel.set_gnmi_targets({})
        self._gnmi_panel.set_direct_servers(0)
        self._gnmi_panel.set_clients([])
        self._binding_panel.set_gnmi_locked(False)
        self._status_label.setText("gNMI stopped.")

    # ------------------------------------------------------------------ #
    #  sFlow lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def _start_sflow(self):
        if self.sflow.is_running():
            return
        devices = self.device_manager.get_all_devices()
        if not devices:
            QMessageBox.warning(self, "sFlow", "Add devices to the topology first.")
            return
        if not self.state_store.is_running():
            self.state_store.start()
        cfg = self._sflow_panel.get_config()
        device_ips = [d.ip_address for d in devices]
        self.sflow.start(
            device_ips    = device_ips,
            collector_ip  = cfg["collector_ip"],
            collector_port= cfg["collector_port"],
            interval      = cfg["interval"],
            sample_rate   = cfg["sample_rate"],
        )

    def _stop_sflow(self):
        if not self.sflow.is_running():
            return
        self.sflow.stop()
        self._sflow_panel.set_running(False)
        self._sflow_panel.set_status("Stopped")
        self._status_label.setText("sFlow stopped.")

    def _on_sflow_ready(self):
        counts: dict = {}
        for d in self.device_manager.get_all_devices():
            key = d.device_type.value
            counts[key] = counts.get(key, 0) + 1
        self._sflow_panel.set_device_counts(counts)
        self._sflow_panel.set_running(True)
        self._sflow_panel.set_collector_info(
            self.sflow.get_collector(), self.sflow.get_interval()
        )
        self._status_label.setText(
            f"sFlow running — {self.sflow.get_device_count()} agent(s) → "
            f"{self.sflow.get_collector()}"
        )

    def _on_gnmi_proxy_toggle(self, enable: bool):
        """Enable or disable the gNMI proxy server independently of device simulation."""
        if enable:
            port = self._gnmi_panel.gnmi_port
            self._console_panel.log_gnmi(f"[gNMI] Starting proxy on port {port}…", "info")
            ok = self.gnmi.start_proxy(port)
            if ok:
                self._console_panel.log_gnmi(f"[gNMI] Proxy running on port {port}.", "success")
                self._gnmi_panel.set_proxy_running(True)
                counts   = self.gnmi.target_counts()
                n_direct = self.gnmi.get_per_device_count()
                self._status_label.setText(
                    f"gNMI running — proxy:{port}, {n_direct} direct server(s), "
                    f"{counts.get('switch', 0)} switches, {counts.get('router', 0)} routers"
                )
            else:
                self._console_panel.log_gnmi("[gNMI] Proxy failed to start.", "error")
                self._gnmi_panel.set_proxy_running(False)
        else:
            self.gnmi.stop_proxy()
            self._console_panel.log_gnmi("[gNMI] Proxy stopped.", "info")
            self._gnmi_panel.set_proxy_running(False)

    # ------------------------------------------------------------------ #
    #  gNMI IP unbinding (called by clear, not by stop)                  #
    # ------------------------------------------------------------------ #

    def _complete_pending_clear(self):
        """Chain the SNMP IP unbind (or finish immediately) after a gNMI unbind
        that was triggered as the second half of a cross-clear operation."""
        if self._bound_ips and self._bound_interface:
            ips = list(self._bound_ips)
            iface = self._bound_interface
            self._console_panel.log_gnmi(f"[gNMI] Removing {len(ips)} bound SNMP IPs…", "info")
            self._unbind_worker = IPUnbindWorker(iface, ips, self._nte_contexts)
            self._unbind_thread = QThread()
            self._unbind_worker.moveToThread(self._unbind_thread)
            self._unbind_thread.started.connect(self._unbind_worker.run)
            self._unbind_worker.log.connect(self._console_panel.log_gnmi)
            self._unbind_worker.progress.connect(self._gnmi_panel.show_progress)
            self._unbind_worker.finished.connect(self._on_clear_unbind_finished)
            self._unbind_thread.start()
        else:
            self._finish_clear()

    def _on_gnmi_unbind(self):
        if not self._gnmi_bound_ips:
            if self._pending_clear_finish:
                self._pending_clear_finish = False
                self._complete_pending_clear()
            return
        # Only remove IPs that are not also held by the SNMP binding
        snmp_set = set(self._bound_ips)
        to_remove = [ip for ip in self._gnmi_bound_ips if ip not in snmp_set]
        if not to_remove:
            # All IPs overlap with SNMP binding — just clear the gNMI tracking
            self._gnmi_bound_ips = []
            self._binding_panel.set_bound_count(len(self._bound_ips))
            if self._pending_clear_finish:
                self._pending_clear_finish = False
                self._complete_pending_clear()
            return

        self._console_panel.log_gnmi(
            f"[gNMI] Unbinding {len(to_remove)} IPs from '{self._gnmi_bound_interface}'…",
            "warning")
        self._gnmi_unbind_worker = IPUnbindWorker(self._gnmi_bound_interface, to_remove)
        self._gnmi_unbind_thread = QThread()
        self._gnmi_unbind_worker.moveToThread(self._gnmi_unbind_thread)
        self._gnmi_unbind_thread.started.connect(self._gnmi_unbind_worker.run)
        self._gnmi_unbind_worker.log.connect(self._console_panel.log_gnmi)
        self._gnmi_unbind_worker.progress.connect(self._gnmi_panel.show_progress)
        self._gnmi_unbind_worker.finished.connect(self._on_gnmi_unbind_finished)
        self._gnmi_unbind_thread.start()

    def _on_gnmi_unbind_finished(self):
        self._gnmi_unbind_thread.quit()
        self._gnmi_unbind_thread.wait()
        self._gnmi_bound_ips = []
        self._binding_panel.set_bound_count(len(self._bound_ips))
        self._binding_panel.set_gnmi_locked(False)
        if self._pending_clear_finish:
            self._pending_clear_finish = False
            self._complete_pending_clear()
        self._gnmi_panel.set_gnmi_status("Idle")
        self._console_panel.log_gnmi("[gNMI] IPs unbound.", "warning")

    def _clear_simulation(self):
        reply = QMessageBox.question(
            self, "Clear Simulation",
            "Stop SNMP simulator and clear all datasets?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if self.snmpsim.is_running():
            self._on_rule_engine_toggled(False)
            self._trap_panel.set_rule_engine_available(False)
            self._rules_panel.set_rule_engine_available(False)
            self._tick_panel.set_available(False)
            self.snmpsim.stop()
        if self.sflow.is_running():
            self.sflow.stop()
            self._sflow_panel.set_running(False)
        self.state_store.stop()
        self._finish_clear()

    def _finish_clear(self):
        """Kick off background deletion of dataset files, then reset UI when done."""
        self._sim_panel.set_status("Clearing…")
        self._act_clear.setEnabled(False)

        self._clear_worker = ClearDatasetsWorker(self._snmp_datasets_dir)
        self._clear_thread = QThread()
        self._clear_worker.moveToThread(self._clear_thread)
        self._clear_thread.started.connect(self._clear_worker.run)
        self._clear_worker.finished.connect(self._on_clear_datasets_finished)
        self._clear_thread.start()

    def _on_clear_datasets_finished(self):
        """Called on main thread once background file deletion is complete."""
        self._clear_thread.quit()
        self._clear_thread.wait()
        self._clear_thread = None
        self._clear_worker = None

        self._generated_files = []
        self._rule_engine.reset_fired_counts()
        self._rules_panel.reset_stats()
        self._sim_panel.set_device_counts(0, 0, 0)
        self._sim_panel.set_simulator_running(False)
        self._update_topology_edit_actions()
        self._binding_panel.set_snmp_locked(False)
        self._sim_panel.set_datasets_ready(False)
        self._binding_panel.set_bound_count(len(set(self._bound_ips) | set(self._gnmi_bound_ips)))
        self._act_clear.setEnabled(True)
        self._sim_panel.set_status("Idle")
        self._console_panel.log("SNMP datasets cleared — IPs kept on adapter.", "warning")

    def _randomize_metrics(self):
        self.device_manager.randomize_all_metrics()
        self._console_panel.log("Metrics randomized.", "info")

        # Regenerate gNMI data files and hot-reload the running server
        if self._gnmi_files or self.gnmi.is_running():
            gnmi_gen = GNMIDataGenerator(self._gnmi_datasets_dir)
            reloaded = 0
            for device in self.device_manager.get_all_devices():
                if device.device_type in (DeviceType.SWITCH, DeviceType.ROUTER):
                    gnmi_gen.regenerate(device, self.topology)
                    self.gnmi.reload_device(device.ip_address)
                    reloaded += 1
            if reloaded:
                self._console_panel.log_gnmi(
                    f"[gNMI] Hot-reloaded metrics for {reloaded} device(s).", "success"
                )

        # Regenerate SNMP .snmprec files
        if self._generated_files:
            snmp_gen = SNMPRecGenerator(self._snmp_datasets_dir)
            for device in self.device_manager.get_all_devices():
                snmp_gen.generate_device(device, self.topology)
            self._console_panel.log("SNMP datasets regenerated with new metrics.", "success")

    def _randomize_gnmi_metrics(self):
        """Randomize device metrics and hot-reload gNMI datasets only."""
        self.device_manager.randomize_all_metrics()
        gnmi_gen = GNMIDataGenerator(self._gnmi_datasets_dir)
        reloaded = 0
        for device in self.device_manager.get_all_devices():
            if device.device_type in (DeviceType.SWITCH, DeviceType.ROUTER):
                gnmi_gen.regenerate(device, self.topology)
                self.gnmi.reload_device(device.ip_address)
                reloaded += 1
        self._console_panel.log_gnmi(
            f"[gNMI] Metrics randomized — {reloaded} device(s) reloaded.", "success"
        )

    def _clear_gnmi_data(self):
        """Delete all gNMI dataset files and stop the gNMI server if running."""
        reply = QMessageBox.question(
            self, "Clear gNMI Simulation",
            "Stop gNMI simulator and clear all gNMI datasets?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if self.gnmi.is_running():
            self.gnmi.stop()
            self._gnmi_panel.set_gnmi_running(False)
            self._update_topology_edit_actions()
            self._gnmi_panel.set_gnmi_status("Stopped")
            self._gnmi_panel.set_gnmi_targets({})
            self._gnmi_panel.set_direct_servers(0)
            self._gnmi_panel.set_clients([])
            self._gnmi_panel.set_gnmi_status("Idle")

        import pathlib
        removed = 0
        for f in pathlib.Path(self._gnmi_datasets_dir).glob("*.gnmi.json"):
            f.unlink(missing_ok=True)
            removed += 1
        self._gnmi_files = []
        self._gnmi_panel.set_datasets_ready(False)
        self._binding_panel.set_bound_count(len(set(self._bound_ips) | set(self._gnmi_bound_ips)))
        self._console_panel.log_gnmi(
            f"[gNMI] Cleared {removed} dataset file(s) — IPs kept on adapter.", "warning"
        )

    # ------------------------------------------------------------------ #
    #  File I/O                                                            #
    # ------------------------------------------------------------------ #

    def _close_topology(self):
        if self.topology.node_count() == 0:
            return
        reply = QMessageBox.question(
            self, "Close Topology",
            "Close the current topology? Running simulators will be stopped and all devices removed.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self.snmpsim.is_running():
            self._stop_simulator()
        if self.gnmi.is_running():
            self.gnmi.stop()
            self._gnmi_panel.set_gnmi_running(False)
            self._update_topology_edit_actions()
            self._gnmi_panel.set_gnmi_status("Idle")
            self._gnmi_panel.set_gnmi_targets({})
            self._gnmi_panel.set_clients([])
        self._new_topology(confirm=False)

    def _new_topology(self, confirm: bool = True):
        if confirm and self.topology.node_count() > 0:
            reply = QMessageBox.question(
                self, "New Topology",
                "Clear current topology?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.topology.clear()
        self.device_manager.clear()
        self.ip_manager.reset()
        self._topology_view.topology_scene.clear_all()
        self._default_positions.clear()
        self._refresh_device_table()
        self._refresh_stats()
        self._console_panel.log("New topology created.", "info")

    def _save_topology(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Topology", self._topologies_dir,
            "JSON Files (*.json);;All Files (*)"
        )
        if path:
            data = self.topology.to_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._console_panel.log(f"Topology saved: {path}", "success")

    def _open_topology(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Topology", self._topologies_dir,
            "JSON Files (*.json);;All Files (*)"
        )
        if path:
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                self._load_topology_data(data)
                self._console_panel.log(f"Topology loaded: {path}", "success")
            except Exception as e:
                QMessageBox.critical(self, "Load Error", str(e))

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Topology JSON", self._topologies_dir,
            "JSON Files (*.json)"
        )
        if path:
            data = self.topology.to_dict()
            # Add extra metadata
            data["meta"] = {
                "device_count": self.topology.node_count(),
                "link_count":   self.topology.edge_count(),
                "generated_files": self._generated_files,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._console_panel.log(f"Exported: {path}", "success")

    def _load_topology_data(self, data: dict):
        self._new_topology(confirm=False)
        self.topology.from_dict(data)
        # Sync device manager
        for device in self.topology.get_all_devices():
            self.device_manager.add_device(device)
            self.ip_manager.reserve(device.ip_address)
        # Rebuild scene
        for device in self.topology.get_all_devices():
            x, y = self.topology.get_position(device.id)
            self._topology_view.topology_scene.add_device_node(device, x, y)
        for src_id, dst_id, edge_data in self.topology.get_links():
            # Pass the stored flow direction (src_node→dst_node), not the
            # NetworkX (u,v) order, so cooling supply and return render as two
            # distinct directional edges instead of collapsing into one.
            self._topology_view.topology_scene.add_link_edge(
                edge_data.get("src_node", src_id),
                edge_data.get("dst_node", dst_id),
                layer=edge_data.get("layer", "production"),
            )
            if self.topology.is_link_broken(src_id, dst_id):
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, True)
        self._refresh_device_table()
        self._refresh_stats()
        self._topology_view.fit_view()
        self._snapshot_default_positions()
        # Fade the graph until SNMP confirms devices are live
        self._topology_view.topology_scene.set_all_faded(True)


    # ------------------------------------------------------------------ #
    #  API → UI sync helpers (called on main thread via _drain_log_queue) #
    # ------------------------------------------------------------------ #

    def _sync_binding_ui(self):
        try:
            self._binding_panel.set_binding_in_progress(False)
            self._binding_panel.progress.hide()
            from api.state import AppState
            s = AppState.get()
            if s.selected_adapter:
                self._binding_panel.set_selected_adapter(s.selected_adapter)
                if not self._bound_interface and not self._gnmi_bound_interface:
                    self._bound_interface = s.selected_adapter
                    self._gnmi_bound_interface = s.selected_adapter
            if s.subnet_mask:
                self._binding_panel.set_subnet_mask(s.subnet_mask)
            # Keep MainWindow local state in sync so unbind/clear paths see real values.
            if s.bound_ips:
                self._bound_ips = list(s.bound_ips)
                self._nte_contexts = dict(s.nte_contexts)
            if s.gnmi_bound_ips:
                self._gnmi_bound_ips = list(s.gnmi_bound_ips)
                self._gnmi_nte_contexts = dict(s.gnmi_nte_contexts)
            # If AppState cleared IPs (after unbind), mirror that too.
            if not s.bound_ips and not s.gnmi_bound_ips:
                self._bound_ips = []
                self._nte_contexts = {}
                self._gnmi_bound_ips = []
                self._gnmi_nte_contexts = {}
            total = len(set(s.bound_ips + s.gnmi_bound_ips))
            self._binding_panel.set_bound_count(total)
            snmp_running = s.snmpsim.is_running() if s.snmpsim else False
            gnmi_running = s.gnmi.is_running() if s.gnmi else False
            self._binding_panel.set_snmp_locked(snmp_running)
            self._binding_panel.set_gnmi_locked(gnmi_running)
        except Exception as e:
            self._console_panel.log(f"Binding UI sync error: {e}", "error")

    def _sync_snmp_ui(self):
        try:
            self._sim_panel.progress.hide()
            from api.state import AppState
            s = AppState.get()
            running = self.snmpsim.is_running()
            ready = self.snmpsim.is_ready() if running else False
            datasets_ready = bool(s.generated_snmp_files)
            self._sim_panel.set_simulator_running(running)
            self._sim_panel.set_datasets_ready(datasets_ready)
            self._sim_panel.set_status("Ready" if ready else ("Starting…" if running else "Idle"))
            self._binding_panel.set_snmp_locked(running)
            self._trap_panel.set_rule_engine_available(running)
            self._rules_panel.set_rule_engine_available(running)
            self._tick_panel.set_available(running)
            self._update_topology_edit_actions()
            if running:
                self._update_sim_panel_counts()
            n = len(s.bound_ips) or len(self._bound_ips)
            if running:
                self._status_label.setText(
                    f"SNMPSim {'running' if ready else 'starting'} — {n} devices"
                )
        except Exception as e:
            self._console_panel.log(f"SNMP UI sync error: {e}", "error")

    def _sync_gnmi_ui(self):
        try:
            from api.state import AppState
            s = AppState.get()
            running = self.gnmi.is_running()
            datasets_ready = bool(s.generated_gnmi_files)
            if s.generated_gnmi_files and not self._gnmi_files:
                self._gnmi_files = list(s.generated_gnmi_files)
            self._gnmi_panel.set_generating(False)
            self._gnmi_panel.set_gnmi_running(running)
            self._gnmi_panel.set_datasets_ready(datasets_ready)
            if running:
                self._gnmi_panel.set_gnmi_status("Running")
                self._gnmi_panel.set_gnmi_targets(self.gnmi.get_active_targets())
                self._gnmi_panel.set_clients(self.gnmi.get_clients())
            else:
                self._gnmi_panel.set_gnmi_status("Idle")
            self._gnmi_panel.set_proxy_running(self.gnmi.is_proxy_running())
            self._binding_panel.set_gnmi_locked(running)
        except Exception as e:
            self._console_panel.log(f"gNMI UI sync error: {e}", "error")

    def _on_sflow_log(self, msg: str):
        try:
            from api.state import AppState
            AppState.get().notify_ui("log_sflow", msg, "info")
        except Exception:
            self._log_queue.put(("log_sflow", msg, "info"))

    def _sync_bacnet_ui(self):
        """Sync BACnet panel — always runs on Qt main thread via drain loop."""
        try:
            running = self.bacnet.is_running()
            self._binding_panel.set_bacnet_locked(running)
            self._bacnet_panel.set_running(running)
            if running:
                summary = self.bacnet.get_device_summary()
                self._bacnet_panel.refresh_device_table(summary)
                self._status_label.setText(
                    f"BACnet/IP running — {len(summary)} EV2 device(s) on UDP :47808"
                )
                if not hasattr(self, "_bacnet_refresh_timer"):
                    self._bacnet_refresh_timer = QTimer(self)
                    self._bacnet_refresh_timer.timeout.connect(self._refresh_bacnet_panel)
                self._bacnet_refresh_timer.start(2000)
            else:
                if hasattr(self, "_bacnet_refresh_timer"):
                    self._bacnet_refresh_timer.stop()
                self._bacnet_panel.refresh_device_table([])
                self._status_label.setText("BACnet/IP stopped.")
        except Exception as e:
            self._console_panel.log(f"BACnet UI sync error: {e}", "error")

    # ------------------------------------------------------------------ #
    #  BACnet/IP Simulator                                                 #
    # ------------------------------------------------------------------ #

    def _start_bacnet(self):
        if self.bacnet.is_running():
            return
        from core.device_manager import DeviceType
        import re as _re

        devices = [
            d for d in self.device_manager.get_all_devices()
            if d.device_type == DeviceType.ENERGY_MONITOR
        ]
        if not devices:
            QMessageBox.warning(
                self, "BACnet/IP",
                "No Energy Monitor devices in topology.\n\n"
                "Add Verdigris EV2 devices (Device Type → energy_monitor, "
                "Vendor → Verdigris Technologies) and bind IPs first."
            )
            return

        # Only start on IPs that are actually bound to a network interface.
        # Unbound IPs have no OS route — their send sockets would bind to the
        # wrong source IP and BACnet tools would never find the device.
        # EV2 devices are mgmt_only so ip_address is "" and the real IP is mgmt_ip.
        bound_set = set(self._bound_ips) | set(self._gnmi_bound_ips)
        bound_devices = [
            d for d in devices
            if (d.ip_address and d.ip_address in bound_set)
            or (d.mgmt_ip    and d.mgmt_ip    in bound_set)
        ]

        if not bound_devices:
            QMessageBox.warning(
                self, "BACnet/IP — IPs Not Bound",
                f"Found {len(devices)} EV2 device(s) in topology but none of their "
                f"IPs are bound to a network interface.\n\n"
                f"Bind IPs first (Binding panel → Bind IPs), then start BACnet."
            )
            return

        unbound = len(devices) - len(bound_devices)
        if unbound:
            self._console_panel.log_bacnet(
                f"[BACnet] Warning: {unbound} EV2 device(s) skipped — IPs not bound.",
                "warning"
            )

        if not self.state_store.is_running():
            self.state_store.start()

        cfg = self._bacnet_panel.get_config()

        # Per-device (total_circuits, active_circuits) map.
        # Walk the power graph to count downstream breaker circuits so that
        # only breakers with real loads generate telemetry; spare slots stay at 0.
        _power_edges = self.topology.get_edges_by_layer("power")
        _id_to_type: dict = {
            dev.id: dev.device_type
            for dev in self.device_manager.get_all_devices()
        }
        _upstream_types = {DeviceType.UPS, DeviceType.GENERATOR}

        circuits_map: dict = {}
        device_ips: list = []
        for d in bound_devices:
            ip = d.ip_address or d.mgmt_ip
            device_ips.append(ip)
            m = _re.search(r"EV2-(\d+)", d.model_name or "")
            capacity = int(m.group(1)) if m else 42

            active = 0
            if _power_edges:
                pdu_id = next(
                    (v if u == d.id else u)
                    for u, v, _ in _power_edges
                    if d.id in (u, v)
                ) if any(d.id in (u, v) for u, v, _ in _power_edges) else None
                if pdu_id:
                    downstream = [
                        (v if u == pdu_id else u)
                        for u, v, _ in _power_edges
                        if pdu_id in (u, v) and d.id not in (u, v)
                        and _id_to_type.get(v if u == pdu_id else u) not in _upstream_types
                    ]
                    active = len(downstream)

            circuits_map[ip] = (max(capacity, active), active) if active > 0 else (capacity, capacity)

        self.bacnet.start(
            device_ips    = device_ips,
            base_instance = cfg["base_instance"],
            circuits_map  = circuits_map,
            frequency_hz  = cfg["frequency_hz"],
            port          = cfg.get("port", 47808),
        )
        self.state_store.enable_bacnet(self.bacnet)

    def _stop_bacnet(self):
        self._binding_panel.set_bacnet_locked(False)
        self.state_store.disable_bacnet()
        self.bacnet.stop()
        if hasattr(self, "_bacnet_refresh_timer"):
            self._bacnet_refresh_timer.stop()
        self._bacnet_panel.set_running(False)
        self._bacnet_panel.refresh_device_table([])
        self._status_label.setText("BACnet/IP stopped.")

    def _on_bacnet_log(self, msg: str, level: str = "info"):
        self._log_queue.put(("log_bacnet", msg, level))

    def _on_bacnet_ready(self):
        # Called from BACnet background thread — no Qt here, just post to queue.
        try:
            from api.state import AppState
            AppState.get().notify_ui("sync_bacnet")
        except Exception:
            pass

    def _refresh_bacnet_panel(self):
        try:
            if not self.bacnet.is_running():
                self._bacnet_refresh_timer.stop()
                return
            self._bacnet_panel.refresh_device_table(self.bacnet.get_device_summary())
        except Exception as e:
            self._console_panel.log(f"BACnet panel refresh error: {e}", "error")

    # ------------------------------------------------------------------ #
    #  Redfish Simulator                                                   #
    # ------------------------------------------------------------------ #

    def _start_redfish(self):
        if self.redfish.is_running():
            return
        from core.device_manager import DeviceType

        servers = [
            d for d in self.device_manager.get_all_devices()
            if d.device_type == DeviceType.SERVER
        ]
        if not servers:
            QMessageBox.warning(
                self, "Redfish",
                "No server devices in topology.\n\n"
                "Add servers (Device Type → server) and bind their IPs first."
            )
            return

        # BMC lives on the OOB mgmt net when present, else the production IP.
        # Only start on IPs actually bound to a network interface.
        bound_set = set(self._bound_ips) | set(self._gnmi_bound_ips)
        def _bmc_ip(d):
            return (d.mgmt_ip or d.ip_address)
        bound_servers = [d for d in servers if _bmc_ip(d) in bound_set]
        if not bound_servers:
            QMessageBox.warning(
                self, "Redfish — IPs Not Bound",
                f"Found {len(servers)} server(s) but none of their IPs are bound "
                f"to a network interface.\n\n"
                f"Bind IPs first (Binding panel → Bind IPs), then start Redfish."
            )
            return

        unbound = len(servers) - len(bound_servers)
        if unbound:
            self._console_panel.log(
                f"[Redfish] Warning: {unbound} server(s) skipped — IPs not bound.",
                "warning"
            )

        # Live telemetry comes from the ticker mutating Device fields.
        if not self.state_store.is_running():
            self.state_store.start()

        cfg = self._redfish_panel.get_config()
        ok = self.redfish.start(
            devices=bound_servers,
            port=cfg["port"],
            username=cfg["username"],
            password=cfg["password"],
            ip_for=_bmc_ip,
        )
        if ok:
            self._redfish_panel.set_running(True)
            self._redfish_panel.set_status("Running")
            self._redfish_panel.refresh_device_table(self.redfish.get_device_summary())
            self._status_label.setText(
                f"Redfish running — {self.redfish.device_count()} BMC(s) on "
                f"port {cfg['port']}"
            )

    def _stop_redfish(self):
        if not self.redfish.is_running():
            return
        self.redfish.stop()
        self._redfish_panel.set_running(False)
        self._redfish_panel.set_status("Stopped")
        self._redfish_panel.refresh_device_table([])
        self._status_label.setText("Redfish stopped.")

    def _redfish_action(self, ip: str, action: str):
        """Run a Server Operation on one BMC and refresh the panel."""
        if not self.redfish.is_running():
            return
        res = self.redfish.perform_action(ip, action)
        if res:
            lvl = "success" if res.get("ok") else "warning"
            self._console_panel.log(
                f"[Redfish] {res['device']} ← {action}: {res['message']}", lvl)
            self._redfish_panel.refresh_device_table(self.redfish.get_device_summary())
            self._redfish_refresh_subs(ip)   # reboot_bmc drops subs → resync list
            self._topology_view.topology_scene.sync_power_states()
        else:
            self._console_panel.log(f"[Redfish] No BMC at {ip}", "error")

    def _redfish_refresh_subs(self, ip: str):
        """Refill the panel's subscription list for the selected BMC."""
        if not self.redfish.is_running():
            self._redfish_panel.set_subscriptions([])
            return
        subs = [s for s in self.redfish.get_subscriptions() if s.get("ip") == ip]
        self._redfish_panel.set_subscriptions(subs)

    def _redfish_subscribe(self, ip: str, destination: str):
        """Register a push subscriber on one BMC."""
        if not self.redfish.is_running():
            return
        res = self.redfish.add_subscription(ip, destination)
        if res is None:
            self._console_panel.log(f"[Redfish] No BMC at {ip}", "error")
        elif res.get("ok"):
            self._console_panel.log(
                f"[Redfish] subscription added → {destination}", "success")
        else:
            self._console_panel.log(
                f"[Redfish] subscribe failed: {res.get('error')}", "warning")
        self._redfish_refresh_subs(ip)
        self._redfish_panel.refresh_device_table(self.redfish.get_device_summary())

    def _redfish_unsubscribe(self, ip: str, sub_id: str):
        """Delete a push subscription from one BMC."""
        if not self.redfish.is_running():
            return
        self.redfish.remove_subscription(ip, sub_id)
        self._console_panel.log("[Redfish] subscription removed", "info")
        self._redfish_refresh_subs(ip)
        self._redfish_panel.refresh_device_table(self.redfish.get_device_summary())

    def _redfish_test_event(self, ip: str):
        """Fire a Redfish push event from one BMC to its subscribers."""
        if not self.redfish.is_running():
            return
        res = self.redfish.submit_test_event(ip, message="Manual test event")
        if res:
            self._console_panel.log(
                f"[Redfish] {res['device']} → test event to "
                f"{res['subscribers']} subscriber(s)", "info")
        else:
            self._console_panel.log(f"[Redfish] No BMC at {ip}", "error")

    def _redfish_view_log(self, ip: str):
        sel = self.redfish.get_sel(ip) or []
        if sel:
            text = "\n".join(
                f"{e.get('Created','')}  {e.get('Severity',''):8}  {e.get('Message','')}"
                for e in sel)
        else:
            text = "(event log empty)"
        QMessageBox.information(self, f"Event Log (SEL) — {ip}", text)

    def _on_redfish_log(self, msg: str, level: str = "info"):
        self._log_queue.put(("console_log", msg, level))

    def _on_redfish_ready(self):
        # Called from a Redfish background thread — no Qt here, just post to queue.
        try:
            from api.state import AppState
            AppState.get().notify_ui("sync_redfish")
        except Exception:
            pass

    def _sync_redfish_ui(self):
        try:
            running = self.redfish.is_running()
            self._redfish_panel.set_running(running)
            self._redfish_panel.set_status("Running" if running else "Stopped")
            self._redfish_panel.refresh_device_table(
                self.redfish.get_device_summary() if running else [])
            self._topology_view.topology_scene.sync_power_states()
        except Exception as e:
            self._console_panel.log(f"Redfish UI sync error: {e}", "error")

    def _sync_sflow_ui(self):
        try:
            running = self.sflow.is_running()
            self._sflow_panel.set_running(running)
            self._sflow_panel.set_status("Running" if running else "Stopped")
            if running:
                self._on_sflow_ready()
        except Exception as e:
            self._console_panel.log(f"sFlow UI sync error: {e}", "error")

    def _sync_rules_ui(self):
        try:
            from api.state import AppState
            s = AppState.get()
            enabled = s.rule_engine_enabled
            self._rules_panel.set_engine_active(enabled)
            self._trap_panel.set_rule_engine_active(enabled)
            self._rules_panel.refresh()
        except Exception as e:
            self._console_panel.log(f"Rules UI sync error: {e}", "error")

    def _sync_devices_ui(self):
        try:
            self._refresh_device_table()
            self._update_sim_panel_counts()
        except Exception as e:
            self._console_panel.log(f"Devices UI sync error: {e}", "error")

    def _rebuild_scene_from_topology(self):
        """Rebuild the Qt scene from the current topology state (already loaded into
        self.topology / self.device_manager by the API). No data-model changes here."""
        try:
            self._topology_view.topology_scene.clear_all()
            self._default_positions.clear()
            for device in self.topology.get_all_devices():
                x, y = self.topology.get_position(device.id)
                self._topology_view.topology_scene.add_device_node(device, x, y)
            for src_id, dst_id, edge_data in self.topology.get_links():
                # Pass the stored flow direction (src_node→dst_node) so cooling
                # supply and return render as two distinct directional edges.
                self._topology_view.topology_scene.add_link_edge(
                    edge_data.get("src_node", src_id),
                    edge_data.get("dst_node", dst_id),
                    layer=edge_data.get("layer", "production"),
                )
                if self.topology.is_link_broken(src_id, dst_id):
                    self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, True)
            self._refresh_device_table()
            self._refresh_stats()
            self._topology_view.fit_view()
            self._snapshot_default_positions()
            self._topology_view.topology_scene.set_all_faded(True)
            self._console_panel.log(
                f"Topology loaded via API: {self.topology.node_count()} devices, "
                f"{self.topology.edge_count()} links",
                "success",
            )
        except Exception as e:
            self._console_panel.log(f"API topology render error: {e}", "error")

    # ------------------------------------------------------------------ #
    #  UI Refresh                                                          #
    # ------------------------------------------------------------------ #

    def _refresh_device_table(self):
        devices = self.device_manager.get_all_devices()
        t = self._device_table
        # Suppress per-cell repaints and signals for the entire rebuild.
        # Without this, inserting 1 300+ rows triggers one repaint per cell
        # which locks the main thread for several seconds.
        t.setUpdatesEnabled(False)
        t.blockSignals(True)
        try:
            t.setRowCount(len(devices))
            type_colors = {
                DeviceType.ROUTER:        QColor("#1f6feb"),
                DeviceType.SWITCH:        QColor("#238636"),
                DeviceType.SERVER:        QColor("#8957e5"),
                DeviceType.FIREWALL:      QColor("#e67e22"),
                DeviceType.LOAD_BALANCER: QColor("#16a085"),
                DeviceType.UPS:           QColor("#c9a227"),
                DeviceType.PDU:           QColor("#d44f00"),
                DeviceType.FLOOR_PDU:     QColor("#b03060"),
                DeviceType.ENERGY_MONITOR: QColor("#16a34a"),
            }
            _consolas = QFont("Consolas", 9)
            for row, device in enumerate(devices):
                name_item = QTableWidgetItem(device.name)
                name_item.setData(Qt.UserRole, device.id)
                type_item = QTableWidgetItem(device.device_type.value.capitalize())
                type_item.setForeground(type_colors.get(device.device_type, QColor("white")))
                vendor_item = QTableWidgetItem(device.vendor.value)
                mgmt_ip_item = QTableWidgetItem(device.mgmt_ip or "—")
                mgmt_ip_item.setFont(_consolas)
                ip_item = QTableWidgetItem(device.ip_address)
                ip_item.setFont(_consolas)
                iface_item = QTableWidgetItem(str(device.interface_count))
                iface_item.setTextAlignment(Qt.AlignCenter)
                port_item  = QTableWidgetItem(str(device.snmp_port))
                port_item.setTextAlignment(Qt.AlignCenter)
                loc_item   = QTableWidgetItem(device.sys_location)
                t.setItem(row, 0, name_item)
                t.setItem(row, 1, type_item)
                t.setItem(row, 2, vendor_item)
                t.setItem(row, 3, mgmt_ip_item)
                t.setItem(row, 4, ip_item)
                t.setItem(row, 5, iface_item)
                t.setItem(row, 6, port_item)
                t.setItem(row, 7, loc_item)
        finally:
            t.blockSignals(False)
            t.setUpdatesEnabled(True)

        # Re-apply any active search filter
        self._on_device_search(self._device_search.text())

    def _on_device_search(self, query: str):
        """Show only rows whose name, type, vendor, or IP match the query."""
        query = query.strip().lower()
        for row in range(self._device_table.rowCount()):
            if not query:
                self._device_table.setRowHidden(row, False)
                continue
            match = False
            for col in range(self._device_table.columnCount()):
                item = self._device_table.item(row, col)
                if item and query in item.text().lower():
                    match = True
                    break
            self._device_table.setRowHidden(row, not match)

    def _refresh_stats(self):
        pass  # Active Devices counts are populated after discovery, not on topology changes

    def _drain_log_queue(self):
        """Drain log/status messages queued by SNMPSim and gNMI monitor threads.

        Collects up to 100 items per tick, batches all plain log lines into a
        single console.log_batch() call, and handles control messages immediately.
        This keeps the main-thread time per tick to a single HTML append rather
        than N individual append() calls.
        """
        _MAX_PER_TICK = 100
        snmp_lines:   list = []
        gnmi_lines:   list = []
        sflow_lines:  list = []
        bacnet_lines: list = []
        processed = 0
        try:
            while processed < _MAX_PER_TICK:
                item = self._log_queue.get_nowait()
                processed += 1
                if item[0] == "log":
                    snmp_lines.append((item[1], item[2]))
                elif item[0] == "log_gnmi":
                    gnmi_lines.append((item[1], item[2]))
                elif item[0] == "log_sflow":
                    sflow_lines.append((item[1], item[2]))
                elif item[0] == "log_bacnet":
                    bacnet_lines.append((item[1], item[2]))
                elif item[0] == "status":
                    self._sim_panel.set_status(item[1])
                elif item[0] == "snmpsim_ready":
                    self._on_snmpsim_ready()
                elif item[0] == "gnmi_status":
                    self._gnmi_panel.set_gnmi_status(item[1])
                elif item[0] == "gnmi_ready":
                    self._on_gnmi_ready()
                elif item[0] == "sflow_status":
                    self._sflow_panel.set_status(item[1])
                elif item[0] == "sflow_ready":
                    self._on_sflow_ready()
                elif item[0] == "sync_sflow":
                    self._sync_sflow_ui()
                elif item[0] == "rebuild_topology_scene":
                    self._rebuild_scene_from_topology()
                elif item[0] == "binding_started":
                    self._binding_panel.set_binding_in_progress(True)
                    self._binding_panel.progress.setRange(0, 0)
                    self._binding_panel.progress.show()
                elif item[0] == "binding_progress":
                    self._binding_panel.show_progress(item[1], item[2])
                elif item[0] == "sync_binding":
                    self._sync_binding_ui()
                elif item[0] == "snmp_gen_started":
                    self._sim_panel.show_progress(0, item[1])
                elif item[0] == "snmp_progress":
                    self._sim_panel.show_progress(item[1], item[2])
                elif item[0] == "gnmi_gen_started":
                    self._gnmi_panel.set_generating(True)
                    self._gnmi_panel.show_progress(0, item[1])
                elif item[0] == "gnmi_gen_progress":
                    self._gnmi_panel.show_progress(item[1], item[2])
                elif item[0] == "sync_snmp":
                    self._sync_snmp_ui()
                elif item[0] == "sync_gnmi":
                    self._sync_gnmi_ui()
                elif item[0] == "sync_bacnet":
                    self._sync_bacnet_ui()
                elif item[0] == "sync_redfish":
                    self._sync_redfish_ui()
                elif item[0] == "sync_rules":
                    self._sync_rules_ui()
                elif item[0] == "sync_devices":
                    self._sync_devices_ui()
                elif item[0] == "link_changed":
                    try:
                        self._topology_view.topology_scene.set_edge_broken(item[1], item[2], item[3])
                    except Exception:
                        pass
                elif item[0] == "console_log":
                    try:
                        self._console_panel.log(item[1], item[2] if len(item) > 2 else "info")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        if snmp_lines or gnmi_lines or sflow_lines or bacnet_lines:
            self._console_panel.log_batch(
                snmp_lines, gnmi_lines, sflow_lines, bacnet_lines
            )

    def _refresh_status(self):
        if self.snmpsim.is_running():
            n_bound = len(self._bound_ips)
            if self.snmpsim.is_ready():
                self._status_label.setText(
                    f"SNMPSim running — {n_bound} IPs on port 161 "
                    f"— PID {self.snmpsim.get_pid()}"
                )
                self._sim_panel.set_status("Running")
            else:
                self._status_label.setText(
                    f"SNMPSim starting — loading datasets… ({n_bound} devices)"
                )
                self._sim_panel.set_status("Starting…")

        if self.gnmi.is_running():
            self._gnmi_panel.set_clients(self.gnmi.get_clients())
            self._gnmi_panel.set_direct_servers(self.gnmi.get_per_device_count())


    # ------------------------------------------------------------------ #
    #  Dialogs                                                             #
    # ------------------------------------------------------------------ #

    def _show_about(self):
        QMessageBox.about(
            self, "About Datacenter Network Simulator",
            "<h3>Datacenter Network Simulator v4.0</h3>"
            "<p>Visually build network topologies and simulate SNMP, gNMI, sFlow, "
            "BACnet, and Redfish protocols for routers, switches, servers, and "
            "facility devices.</p>"
            "<br>"
            "<b>Tech Stack:</b> Python 3.11+, PySide6, NetworkX, SNMPSim, gRPC<br>"
            "<b>Supports:</b> Routers, Switches, Servers, Firewalls, PDUs, UPS, Sensors<br>"
            "<b>SNMP Versions:</b> v1, v2c<br>"
            "<b>gNMI:</b> OpenConfig — Interfaces, LLDP, BGP, OSPF, AFT, System<br>"
            "<b>Telemetry:</b> gNMI Subscribe STREAM / ONCE / POLL<br>"
            "<b>sFlow:</b> v5 UDP datagrams — counter + flow samples<br>"
            "<b>BACnet:</b> BACnet/IP — building automation objects<br>"
            "<b>Redfish:</b> REST API — server health, power, thermal, events<br>"
            "<b>Web UI:</b> React 18, TypeScript, Vite, Zustand, React Flow<br>"
        )

    def _show_snmpwalk(self):
        if self._bound_ips:
            example_ip = self._bound_ips[0]
            community = "public"
            # Try to find the actual community for this device
            for dev in self.device_manager.get_all_devices():
                if dev.ip_address == example_ip:
                    community = dev.snmp_community
                    break
        else:
            example_ip = "192.168.1.10"
            community = "public"
        snmp_port = self._sim_panel.get_snmp_port()
        cmd = self.snmpsim.get_snmp_walk_command(example_ip, port=snmp_port, community=community)
        QMessageBox.information(
            self, "SNMP Walk Command",
            f"Each device responds on its own IP at port {snmp_port}.\n\n"
            f"Example (first device):\n\n  {cmd}\n\n"
            f"Device IPs are bound to the selected network adapter via netsh.\n"
            f"Point your monitoring tool to any device IP on port 161."
        )

    def _discover_topology(self):
        if self.topology.node_count() == 0:
            QMessageBox.warning(self, "No Topology",
                                "Load or build a topology first.")
            return
        dlg = DiscoveryDialog(
            topology=self.topology,
            snmpsim_running=self.snmpsim.is_ready(),
            host="127.0.0.1",
            port=self._sim_panel.get_snmp_port(),
            parent=self,
        )
        dlg.exec()

    def closeEvent(self, event):
        if self.snmpsim.is_running():
            reply = QMessageBox.question(
                self, "Exit",
                "SNMPSim is still running. Stop it and remove bound IPs before exiting?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self._trap_engine.set_rule_engine_enabled(False)
            self.snmpsim.stop()
            # Best-effort synchronous IP removal on exit
            if self._bound_ips and self._bound_interface:
                remove_ips_batch(self._bound_interface, self._bound_ips)
        # Stop gNMI server if running
        if self.gnmi.is_running():
            self.gnmi.stop()
        # Stop BACnet server if running
        if self.bacnet.is_running():
            self.state_store.disable_bacnet()
            self.bacnet.stop()
        # Stop Redfish servers if running
        if self.redfish.is_running():
            self.redfish.stop()
        self._trap_engine.stop()
        event.accept()

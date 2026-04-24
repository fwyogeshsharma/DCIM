"""
Main Application Window.
"""
from __future__ import annotations
import json
import os
import queue
import random
import shutil
from pathlib import Path
from typing import List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QDockWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QStatusBar, QLabel, QMenuBar,
    QMenu, QFileDialog, QMessageBox, QInputDialog,
    QAbstractItemView, QFrame, QPushButton, QDialog,
    QSpinBox, QComboBox, QFormLayout, QDialogButtonBox,
    QGroupBox, QToolBar, QLineEdit,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QFont, QColor, QKeySequence

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
from simulator.snmp_agent import SNMPAgent
from simulator.gnmi_controller import GNMIController
from simulator.sflow_controller import SFlowController
from core.trap_definitions import TrapType, TRAP_DEFINITIONS, get_applicable_traps
from core.trap_engine import TrapEngine
from ui.device_dialog import DeviceDialog
from ui.topology_view import TopologyView
from ui.snmp_panel import SNMPPanel
from ui.gnmi_panel import GNMIPanel
from ui.sflow_panel import SFlowPanel
from ui.console_panel import ConsolePanel
from ui.binding_panel import BindingPanel
from ui.discovery_dialog import DiscoveryDialog


DATASETS_DIR      = "datasets"
SNMP_DATASETS_DIR = os.path.join(DATASETS_DIR, "snmp")
GNMI_DATASETS_DIR = os.path.join(DATASETS_DIR, "gnmi")
TOPOLOGIES_DIR = "topologies"


def _default_model_name(device) -> str:
    """Return the first known model name for this device's vendor+type, or '—'."""
    models = DEVICE_MODELS.get((device.device_type, device.vendor), [])
    return models[0].name if models else "—"


# ------------------------------------------------------------------ #
#  Background worker for dataset generation                           #
# ------------------------------------------------------------------ #

class GeneratorWorker(QObject):
    progress = Signal(int, int)
    log      = Signal(str, str)
    finished = Signal()   # result stored in self.result
    error    = Signal(str)

    def __init__(self, topology: TopologyEngine, agent=None):
        super().__init__()
        self.topology = topology
        self._agent   = agent

    def run(self):
        try:
            snmp_gen = SNMPRecGenerator()
            devices  = self.topology.get_all_devices()
            total    = len(devices)
            loaded   = []
            step = max(1, total // 100)
            for i, device in enumerate(devices):
                entries = snmp_gen.build_entries(device, self.topology)
                if self._agent:
                    self._agent.update_device(device.ip_address, entries)
                loaded.append(device.ip_address)
                self.log.emit(
                    f"[SNMP] {device.ip_address}  {device.device_type.value}  ({device.interface_count} ifaces)",
                    "info",
                )
                if (i + 1) % step == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)
            self.result = loaded
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
        self.setWindowTitle("Datacenter Network Simulator")
        self.setMinimumSize(1200, 750)
        self._datasets_dir      = DATASETS_DIR
        self._snmp_datasets_dir = SNMP_DATASETS_DIR
        self._gnmi_datasets_dir = GNMI_DATASETS_DIR
        self._topologies_dir    = TOPOLOGIES_DIR
        os.makedirs(self._snmp_datasets_dir, exist_ok=True)
        os.makedirs(self._gnmi_datasets_dir, exist_ok=True)
        os.makedirs(self._topologies_dir, exist_ok=True)

        self.device_manager = DeviceManager()
        self.topology = TopologyEngine()
        self.ip_manager = IPManager()
        self.snmpsim = SNMPAgent()
        self.gnmi  = GNMIController(self._gnmi_datasets_dir)
        self.sflow = SFlowController()
        self.state_store = DeviceStateStore(
            self.device_manager, self.topology, self._snmp_datasets_dir,
            tick_interval=60.0, snmp_sync_every=1,   # 60 s reduces I/O pressure for large topologies
        )
        self.gnmi.set_state_store(self.state_store)
        self.sflow.set_state_store(self.state_store)
        self.sflow.set_topology(self.topology)
        self.sflow.set_device_manager(self.device_manager)
        self._trap_engine = TrapEngine(self)
        self._generated_files: list = []
        self._gnmi_files: list = []
        self._default_positions: dict = {}         # {device_id: (x, y)} — snapshot at load/template time
        self._current_layout_positions: dict = {}  # snapshot after each layout application
        self._algo_layout_active: bool = False  # True after an algo layout; drags no longer update default
        self._worker_thread: QThread = None
        self._worker: GeneratorWorker = None
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
        self._device_table.setColumnCount(6)
        self._device_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Vendor", "IP Address", "Interfaces", "SNMP Port"]
        )

        hdr = self._device_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setDefaultSectionSize(90)
        hdr.setMinimumSectionSize(50)
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
        """Single outer dock holding four panels in a horizontal QSplitter:
        1. Network Interface Binding (shared adapter selector)
        2. SNMP Simulator (controls + embedded SNMP Traps)
        3. gNMI Simulator
        4. Console (shared log)
        """
        self._right_splitter = QSplitter(Qt.Horizontal)
        self._right_splitter.setChildrenCollapsible(True)
        self._right_splitter.setHandleWidth(3)
        self._right_splitter.setStyleSheet(
            "QSplitter::handle { background: #30363d; }"
            "QSplitter::handle:hover { background: #58a6ff; }"
        )

        # Panel 1 — Network Interface Binding (shared)
        self._binding_panel = BindingPanel()
        self._binding_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._binding_panel)

        # Panel 2 — SNMP Simulator + Traps
        self._sim_panel = SNMPPanel()
        self._sim_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._sim_panel)

        # Panel 3 — gNMI Simulator
        self._gnmi_panel = GNMIPanel()
        self._gnmi_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._gnmi_panel)

        # Panel 4 — sFlow Simulator
        self._sflow_panel = SFlowPanel()
        self._sflow_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._sflow_panel)

        # Panel 5 — Console
        self._console_panel = ConsolePanel()
        self._console_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._console_panel)

        self._right_splitter.setStretchFactor(0, 1)
        self._right_splitter.setStretchFactor(1, 1)
        self._right_splitter.setStretchFactor(2, 1)
        self._right_splitter.setStretchFactor(3, 1)
        self._right_splitter.setStretchFactor(4, 1)
        self._right_splitter.setSizes([250, 250, 250, 250, 250])

        # Only the IP Binder panel visible on startup
        self._sim_panel.setVisible(False)
        self._gnmi_panel.setVisible(False)
        self._sflow_panel.setVisible(False)
        self._console_panel.setVisible(False)

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
                font-size: 17px;
                padding: 8px 3px;
                min-width: 28px;
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
        tb.setStyleSheet(_TB_STYLE)

        # ── IP Binder ─────────────────────────────────────────────────────
        self._act_panel_binding = QAction("🔗", self)
        self._act_panel_binding.setCheckable(True)
        self._act_panel_binding.setChecked(True)
        self._act_panel_binding.setToolTip("Network Interface Bindings")
        self._act_panel_binding.toggled.connect(self._on_toggle_binding_panel)
        tb.addAction(self._act_panel_binding)

        tb.addSeparator()

        # ── SNMP Simulator ────────────────────────────────────────────────
        self._act_panel_sim = QAction("🖥️", self)
        self._act_panel_sim.setCheckable(True)
        self._act_panel_sim.setChecked(False)
        self._act_panel_sim.setToolTip("SNMP Simulator")
        self._act_panel_sim.toggled.connect(self._on_toggle_sim_panel)
        tb.addAction(self._act_panel_sim)

        tb.addSeparator()

        # ── gNMI Simulator ────────────────────────────────────────────────
        self._act_panel_gnmi = QAction("📡", self)
        self._act_panel_gnmi.setCheckable(True)
        self._act_panel_gnmi.setChecked(False)
        self._act_panel_gnmi.setToolTip("gNMI Simulator")
        self._act_panel_gnmi.toggled.connect(self._on_toggle_gnmi_panel)
        tb.addAction(self._act_panel_gnmi)

        tb.addSeparator()

        # ── sFlow Simulator ───────────────────────────────────────────────
        self._act_panel_sflow = QAction("📶", self)
        self._act_panel_sflow.setCheckable(True)
        self._act_panel_sflow.setChecked(False)
        self._act_panel_sflow.setToolTip("sFlow Simulator")
        self._act_panel_sflow.toggled.connect(self._on_toggle_sflow_panel)
        tb.addAction(self._act_panel_sflow)

        tb.addSeparator()

        # ── Console ───────────────────────────────────────────────────────
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

        self._right_dock.visibilityChanged.connect(self._on_right_dock_visibility)
        self.addToolBar(Qt.RightToolBarArea, tb)

    # ── Panel toggle slots ─────────────────────────────────────────────────────

    def _visible_panel_count(self) -> int:
        return sum([
            self._act_panel_binding.isChecked(),
            self._act_panel_sim.isChecked(),
            self._act_panel_gnmi.isChecked(),
            self._act_panel_sflow.isChecked(),
            self._act_panel_console.isChecked(),
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

    def _on_toggle_console_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._console_panel.setVisible(visible)
        if self._visible_panel_count() == 0:
            self._right_dock.hide()
        else:
            self._resize_right_dock()

    def _on_right_dock_visibility(self, visible: bool):
        """Outer dock hidden externally — uncheck all toolbar buttons."""
        if not visible:
            for btn in (self._act_panel_binding, self._act_panel_sim,
                        self._act_panel_gnmi, self._act_panel_console):
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
        self._act_bulk_add   = QAction("Bulk Add Devices...", self)
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

        # sFlow controller callbacks
        self.sflow.set_log_callback(
            lambda msg: self._log_queue.put(("log_sflow", msg, "info"))
        )
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
            lambda msg, level="info": self._log_queue.put(("log", msg, level))
        )
        self.snmpsim.set_status_callback(
            lambda s: self._log_queue.put(("status", s))
        )
        self.snmpsim.set_ready_callback(
            lambda: self._log_queue.put(("snmpsim_ready",))
        )

        # Trap section (embedded in SNMPPanel) ↔ trap engine
        self._sim_panel.sig_trap_apply.connect(self._trap_engine.configure)
        self._sim_panel.sig_trap_simulate.connect(self._on_trap_simulate)
        self._trap_engine.trap_sent.connect(self._sim_panel.add_trap_event)
        self._trap_engine.trap_error.connect(self._sim_panel.add_trap_error)

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
            self._sync_trap_devices()
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
            self._sync_trap_devices()
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
            self._sync_trap_devices()
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

    def _on_trap_simulate(self, active: bool):
        if active:
            devices = self.device_manager.get_all_devices()
            if not devices:
                QMessageBox.warning(
                    self, "No Devices",
                    "Add devices to the topology before simulating traps."
                )
                self._sim_panel.set_simulating(False)
                return
            self._trap_engine.start_simulation(devices)
        else:
            self._trap_engine.stop_simulation()

    def _sync_trap_devices(self):
        """Keep the trap engine's device list in sync with the topology."""
        self._trap_engine.update_sim_devices(self.device_manager.get_all_devices())

    def _regenerate_device_live(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        # Reload SNMP in-memory table for this device
        entries = SNMPRecGenerator().build_entries(device, self.topology)
        self.snmpsim.update_device(device.ip_address, entries)
        # gNMI
        if device.device_type in (DeviceType.SWITCH, DeviceType.ROUTER):
            GNMIDataGenerator(self._gnmi_datasets_dir).regenerate(device, self.topology)
            self.gnmi.reload_device(device.ip_address)

    def _show_device_info(self, device_id: str):
        device = self.device_manager.get_device(device_id)
        if not device:
            return
        neighbors = self.topology.get_neighbors(device_id)

        neighbor_lines = []
        for neighbor in neighbors:
            edge = self.topology.graph.edges[device_id, neighbor.id]
            if edge.get("src_node") == device_id:
                local_idx  = edge.get("src_iface", 0)
                remote_idx = edge.get("dst_iface", 0)
            else:
                local_idx  = edge.get("dst_iface", 0)
                remote_idx = edge.get("src_iface", 0)

            local_port  = device.interfaces[local_idx].name  if local_idx  < len(device.interfaces)   else f"port{local_idx}"
            remote_port = neighbor.interfaces[remote_idx].name if remote_idx < len(neighbor.interfaces) else f"port{remote_idx}"
            neighbor_lines.append(f"  {neighbor.name}  [{local_port} <-> {remote_port}]")

        neighbor_text = ("\n" + "\n".join(neighbor_lines)) if neighbor_lines else "  None"

        info = (
            f"Name:        {device.name}\n"
            f"Type:        {device.device_type.value}\n"
            f"Vendor:      {device.vendor.value}\n"
            f"Model:       {device.model_name or _default_model_name(device)}\n"
            f"OS:          {device.os_name}\n"
            f"OS Version:  {device.os_version}\n"
            f"IP:          {device.ip_address}\n"
            f"SNMP Port:   {device.snmp_port}\n"
            f"gNMI Port:   {device.gnmi_port}\n"
            f"Community:   {device.snmp_community}\n"
            f"Interfaces:  {device.interface_count}\n"
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

        self._worker = GeneratorWorker(self.topology, agent=self.snmpsim)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_gen_progress)
        self._worker.log.connect(self._console_panel.log)
        self._worker.finished.connect(self._on_gen_finished)
        self._worker.error.connect(self._on_gen_error)
        self._worker_thread.start()

    def _on_gen_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _on_gen_finished(self):
        self._worker_thread.quit()
        self._worker_thread.wait()
        loaded = self._worker.result
        self._generated_files = loaded
        self._console_panel.log(
            f"[SNMP] {len(loaded)} devices loaded into SNMP agent", "success"
        )
        self._refresh_stats()
        self._sim_panel.show_progress(len(loaded), len(loaded))
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

        device_ips = [d.ip_address for d in self.device_manager.get_all_devices()]
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
                    len(self._bound_ips) + len(self._gnmi_bound_ips)
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
        self._binding_panel.set_bound_count(len(self._bound_ips) + len(self._gnmi_bound_ips))

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

        self._console_panel.log(
            f"Starting SNMP agent on port 161 with {len(bound_ips)} device(s)...", "success"
        )
        ok = self.snmpsim.start(device_ips=bound_ips, port=161)
        if ok:
            self.state_store.set_log_callback(self._console_panel.log)
            self.state_store.start()
            self.state_store.enable_snmp_sync(self.snmpsim)
            self._sim_panel.set_simulator_running(True)
            self._update_topology_edit_actions()
            self._binding_panel.set_snmp_locked(True)
            self._sim_panel.set_device_counts(
                len(self.device_manager.get_devices_by_type(DeviceType.SWITCH)),
                len(self.device_manager.get_devices_by_type(DeviceType.ROUTER)),
                len(self.device_manager.get_devices_by_type(DeviceType.SERVER)),
                len(self.device_manager.get_devices_by_type(DeviceType.FIREWALL)),
                len(self.device_manager.get_devices_by_type(DeviceType.LOAD_BALANCER)),
            )
            self._status_label.setText(
                f"SNMP Agent starting… ({len(bound_ips)} devices)"
            )
            self._console_panel.log(
                "SNMP agent is starting — devices will respond once 'Running' is shown.",
                "info",
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

        device_ips = [d.ip_address for d in devices]
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
        self._panel_bind_thread.start()

    def _on_panel_bind_ips_finished(self):
        self._panel_bind_thread.quit()
        self._panel_bind_thread.wait()
        self._panel_bind_thread = None
        bound_ips = self._panel_bind_worker.result
        self._bound_ips    = bound_ips
        self._nte_contexts = getattr(self._panel_bind_worker, "nte_contexts", {})
        self._panel_bind_worker = None
        self._binding_panel.set_bound_count(len(self._bound_ips) + len(self._gnmi_bound_ips))
        self._binding_panel.set_snmp_locked(False)
        if bound_ips:
            self._console_panel.log(f"{len(bound_ips)} IPs bound to adapter.", "success")
        else:
            self._console_panel.log("No IPs were bound.", "error")

    def _on_panel_bind_ips_error(self, error: str):
        self._panel_bind_thread.quit()
        self._panel_bind_thread.wait()
        self._panel_bind_thread = None
        self._panel_bind_worker = None
        self._binding_panel.set_snmp_locked(False)
        self._console_panel.log(f"Bind error: {error}", "error")

    def _on_panel_unbind_ips(self):
        """Remove all bound IPs (SNMP and gNMI) from the adapter."""
        all_ips = list(set(self._bound_ips) | set(self._gnmi_bound_ips))
        iface   = self._bound_interface or self._gnmi_bound_interface
        if not all_ips or not iface:
            return
        self._console_panel.log(f"Removing {len(all_ips)} bound IPs…", "info")
        self._binding_panel.set_snmp_locked(True)
        self._binding_panel.set_gnmi_locked(True)
        all_contexts = dict(self._nte_contexts)

        self._binding_panel.show_progress(0, len(all_ips))
        self._panel_unbind_worker = IPUnbindWorker(iface, all_ips, all_contexts)
        self._panel_unbind_thread = QThread()
        self._panel_unbind_worker.moveToThread(self._panel_unbind_thread)
        self._panel_unbind_thread.started.connect(self._panel_unbind_worker.run)
        self._panel_unbind_worker.progress.connect(self._binding_panel.show_progress)
        self._panel_unbind_worker.log.connect(self._console_panel.log)
        self._panel_unbind_worker.finished.connect(self._on_panel_unbind_ips_finished)
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

    def _on_snmpsim_ready(self):
        """Called (via queue) when the SNMP agent signals ready."""
        n_bound = len(self._bound_ips)
        self._status_label.setText(
            f"SNMP Agent running — {n_bound} devices on port 161"
        )
        self._console_panel.log("SNMP Agent is ready — devices are now responding to SNMP polls.", "success")

        # Run one SNMP topology discovery scan as soon as the simulator is ready.
        self._start_live_discovery()

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

        switches       = len(self.device_manager.get_devices_by_type(DeviceType.SWITCH))
        routers        = len(self.device_manager.get_devices_by_type(DeviceType.ROUTER))
        servers        = len(self.device_manager.get_devices_by_type(DeviceType.SERVER))
        firewalls      = len(self.device_manager.get_devices_by_type(DeviceType.FIREWALL))
        load_balancers = len(self.device_manager.get_devices_by_type(DeviceType.LOAD_BALANCER))
        self._sim_panel.set_device_counts(switches, routers, servers, firewalls, load_balancers)

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
        switches       = len(self.device_manager.get_devices_by_type(DeviceType.SWITCH))
        routers        = len(self.device_manager.get_devices_by_type(DeviceType.ROUTER))
        servers        = len(self.device_manager.get_devices_by_type(DeviceType.SERVER))
        firewalls      = len(self.device_manager.get_devices_by_type(DeviceType.FIREWALL))
        load_balancers = len(self.device_manager.get_devices_by_type(DeviceType.LOAD_BALANCER))
        self._sim_panel.set_device_counts(switches, routers, servers, firewalls, load_balancers)

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

        self.snmpsim.stop()
        self.state_store.disable_snmp_sync()
        self._sim_panel.set_device_counts(0, 0, 0)
        self._sim_panel.set_simulator_running(False)
        self._update_topology_edit_actions()
        self._binding_panel.set_snmp_locked(False)
        self._trap_engine.stop_simulation()
        self._sim_panel.set_simulating(False)
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

        port      = self._gnmi_panel.gnmi_port
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
            d.ip_address for d in self.device_manager.get_all_devices()
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
        self._binding_panel.set_bound_count(len(self._bound_ips) + len(self._gnmi_bound_ips))
        if self._gnmi_bound_ips:
            self._console_panel.log_gnmi(
                f"[gNMI] {len(self._gnmi_bound_ips)} IPs bound.", "success")
        port      = self._gnmi_panel.gnmi_port
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

        # Build {ip: gnmi_port} from device topology data for bound IPs only
        bound_set = set(bound_ips)
        bound_ip_ports = {
            d.ip_address: d.gnmi_port
            for d in all_devices
            if d.ip_address in bound_set
               and d.device_type in (DeviceType.SWITCH, DeviceType.ROUTER)
        }

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
        self._sflow_panel.set_collector_info(self.sflow.get_collector())
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

        self._trap_engine.stop_simulation()
        self._sim_panel.set_simulating(False)
        self._generated_files = []
        self._sim_panel.set_device_counts(0, 0, 0)
        self._sim_panel.set_simulator_running(False)
        self._update_topology_edit_actions()
        self._binding_panel.set_snmp_locked(False)
        self._sim_panel.set_datasets_ready(False)
        self._binding_panel.set_bound_count(len(self._bound_ips) + len(self._gnmi_bound_ips))
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

        # Push fresh OID tables to the SNMP agent
        if self._generated_files:
            snmp_gen = SNMPRecGenerator()
            for device in self.device_manager.get_all_devices():
                entries = snmp_gen.build_entries(device, self.topology)
                self.snmpsim.update_device(device.ip_address, entries)
            self._console_panel.log("SNMP agent updated with new metrics.", "success")

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
        self._binding_panel.set_bound_count(len(self._bound_ips) + len(self._gnmi_bound_ips))
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
        self._trap_engine.stop_simulation()
        self._sim_panel.set_simulating(False)
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
                with open(path, "r", encoding="utf-8") as f:
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
        for src_id, dst_id, _ in self.topology.get_links():
            self._topology_view.topology_scene.add_link_edge(src_id, dst_id)
            if self.topology.is_link_broken(src_id, dst_id):
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, True)
        self._refresh_device_table()
        self._refresh_stats()
        self._sync_trap_devices()
        self._topology_view.fit_view()
        self._snapshot_default_positions()
        # Fade the graph until SNMP confirms devices are live
        self._topology_view.topology_scene.set_all_faded(True)

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
            }
            _consolas = QFont("Consolas", 9)
            for row, device in enumerate(devices):
                name_item = QTableWidgetItem(device.name)
                name_item.setData(Qt.UserRole, device.id)
                type_item = QTableWidgetItem(device.device_type.value.capitalize())
                type_item.setForeground(type_colors.get(device.device_type, QColor("white")))
                vendor_item = QTableWidgetItem(device.vendor.value)
                ip_item = QTableWidgetItem(device.ip_address)
                ip_item.setFont(_consolas)
                iface_item = QTableWidgetItem(str(device.interface_count))
                iface_item.setTextAlignment(Qt.AlignCenter)
                port_item  = QTableWidgetItem(str(device.snmp_port))
                port_item.setTextAlignment(Qt.AlignCenter)
                t.setItem(row, 0, name_item)
                t.setItem(row, 1, type_item)
                t.setItem(row, 2, vendor_item)
                t.setItem(row, 3, ip_item)
                t.setItem(row, 4, iface_item)
                t.setItem(row, 5, port_item)
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
        snmp_lines:  list = []
        gnmi_lines:  list = []
        sflow_lines: list = []
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
        except queue.Empty:
            pass
        if snmp_lines or gnmi_lines or sflow_lines:
            self._console_panel.log_batch(snmp_lines, gnmi_lines, sflow_lines)

    def _refresh_status(self):
        if self.snmpsim.is_running():
            n_bound = len(self._bound_ips)
            if self.snmpsim.is_ready():
                self._status_label.setText(
                    f"SNMP Agent running — {n_bound} IPs on port 161"
                )
                self._sim_panel.set_status("Running")
            else:
                self._status_label.setText(
                    f"SNMP Agent starting… ({n_bound} devices)"
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
            "<h3>Datacenter Network Simulator v2.0</h3>"
            "<p>Visually build network topologies and simulate both SNMP and gNMI "
            "protocols for routers, switches, and servers.</p>"
            "<br>"
            "<b>Tech Stack:</b> Python 3.11+, PySide6, NetworkX, pysnmp, gRPC<br>"
            "<b>Supports:</b> Routers, Switches, Servers<br>"
            "<b>SNMP Versions:</b> v1, v2c<br>"
            "<b>gNMI:</b> OpenConfig — Interfaces, LLDP, BGP, OSPF, AFT, System<br>"
            "<b>Telemetry:</b> gNMI Subscribe STREAM / ONCE / POLL<br>"
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
        cmd = self.snmpsim.get_snmp_walk_command(example_ip, port=161, community=community)
        QMessageBox.information(
            self, "SNMP Walk Command",
            f"Each device responds on its own IP at port 161.\n\n"
            f"Example (first device):\n\n  {cmd}\n\n"
            f"Device IPs are bound to the selected network adapter.\n"
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
            port=161,
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
            self.snmpsim.stop()
            # Best-effort synchronous IP removal on exit
            if self._bound_ips and self._bound_interface:
                remove_ips_batch(self._bound_interface, self._bound_ips)
        # Stop gNMI server if running
        if self.gnmi.is_running():
            self.gnmi.stop()
        self._trap_engine.stop()
        event.accept()

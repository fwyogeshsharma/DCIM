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
    QGroupBox, QToolBar,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QFont, QColor, QKeySequence

from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.topology_engine import TopologyEngine
from core.snmprec_generator import SNMPRecGenerator
from core.ip_manager import IPManager
from core.ip_binder import (
    add_ips_batch, remove_ips_batch, is_admin,
)
from simulator.snmpsim_controller import SNMPSimController
from core.trap_definitions import TrapType, TRAP_DEFINITIONS, get_applicable_traps
from core.trap_engine import TrapEngine
from ui.device_dialog import DeviceDialog
from ui.topology_view import TopologyView
from ui.simulation_panel import SimulationPanel
from ui.trap_panel import TrapPanel
from ui.discovery_dialog import DiscoveryDialog


DATASETS_DIR = "datasets"
TOPOLOGIES_DIR = "topologies"


# ------------------------------------------------------------------ #
#  Background worker for dataset generation                           #
# ------------------------------------------------------------------ #

class GeneratorWorker(QObject):
    progress = Signal(int, int)
    log      = Signal(str, str)
    finished = Signal()   # no args – result stored in self.result
    error    = Signal(str)

    def __init__(self, topology: TopologyEngine, output_dir: str):
        super().__init__()
        self.topology = topology
        self.output_dir = output_dir

    def run(self):
        try:
            gen = SNMPRecGenerator(self.output_dir)
            devices = self.topology.get_all_devices()
            total = len(devices)
            generated = []
            for i, device in enumerate(devices):
                self.log.emit(f"Generating dataset for {device.name}...", "info")
                fp = gen.generate_device(device, self.topology)
                generated.append(fp)
                self.progress.emit(i + 1, total)
            self.result = generated
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


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

    def run(self):
        try:
            bound: List[str] = []
            total = len(self.ips)
            for i, ip in enumerate(self.ips):
                from core.ip_binder import add_ip
                ok, msg = add_ip(self.interface, ip, self.mask)
                level = "success" if ok else "error"
                self.log.emit(f"  {'OK' if ok else 'FAIL'} {ip}: {msg}", level)
                if ok:
                    bound.append(ip)
                self.progress.emit(i + 1, total)
            self.result = bound
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class IPUnbindWorker(QObject):
    """Removes a list of IPs from a Windows network interface via netsh."""
    log      = Signal(str, str)
    finished = Signal()

    def __init__(self, interface: str, ips: List[str]):
        super().__init__()
        self.interface = interface
        self.ips = ips

    def run(self):
        try:
            for ip in self.ips:
                from core.ip_binder import remove_ip
                ok, msg = remove_ip(self.interface, ip)
                self.log.emit(f"  {'OK' if ok else 'FAIL'} Removed {ip}: {msg}", "info")
        except Exception as e:
            self.log.emit(f"Unbind error: {e}", "error")
        finally:
            self.finished.emit()


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
        self.setWindowTitle("SNMP Network Topology Simulator")
        self.setMinimumSize(1200, 750)
        self._datasets_dir = DATASETS_DIR
        self._topologies_dir = TOPOLOGIES_DIR
        os.makedirs(self._datasets_dir, exist_ok=True)
        os.makedirs(self._topologies_dir, exist_ok=True)

        self.device_manager = DeviceManager()
        self.topology = TopologyEngine()
        self.ip_manager = IPManager()
        self.snmpsim = SNMPSimController(self._datasets_dir)
        self._trap_engine = TrapEngine(self)
        self._generated_files: list = []
        self._worker_thread: QThread = None
        self._worker: GeneratorWorker = None
        self._link_mode = False

        # IP binding state
        self._bound_ips: List[str] = []
        self._bound_interface: str = ""
        self._bind_thread: QThread = None
        self._bind_worker = None
        self._unbind_thread: QThread = None
        self._unbind_worker = None

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._apply_theme()

        # Thread-safe log queue — monitor thread puts, main thread drains
        self._log_queue: queue.Queue = queue.Queue()
        self._log_drain_timer = QTimer(self)
        self._log_drain_timer.setInterval(50)
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

        self._device_table = QTableWidget()
        self._device_table.setColumnCount(6)
        self._device_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Vendor", "IP Address", "Interfaces", "SNMP Port"]
        )
        self._device_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
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
            QTableWidget::item:selected {
                background: #1f6feb;
            }
        """)
        self._device_table.doubleClicked.connect(self._on_device_table_double_click)

        dock.setWidget(self._device_table)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self._device_dock = dock

    def _build_right_panels(self):
        """Single outer dock that holds both panels in an inner QSplitter.

        Using one outer dock means the topology canvas absorbs all horizontal
        resize — the inner splitter handle only redistributes between the two
        panels, so stretching Sim Control never forces SNMP Traps to shrink.
        """
        # ── Inner splitter ────────────────────────────────────────────────
        self._right_splitter = QSplitter(Qt.Horizontal)
        self._right_splitter.setChildrenCollapsible(True)
        self._right_splitter.setHandleWidth(3)
        self._right_splitter.setStyleSheet(
            "QSplitter::handle { background: #30363d; }"
            "QSplitter::handle:hover { background: #58a6ff; }"
        )

        self._sim_panel = SimulationPanel()
        self._sim_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._sim_panel)

        self._trap_panel = TrapPanel()
        self._trap_panel.setMinimumWidth(260)
        self._right_splitter.addWidget(self._trap_panel)

        # Sim Control gets all extra space when outer dock is resized;
        # SNMP Traps stays at its own preferred width.
        self._right_splitter.setStretchFactor(0, 1)
        self._right_splitter.setStretchFactor(1, 0)
        self._right_splitter.setSizes([300, 300])

        # ── Outer dock ────────────────────────────────────────────────────
        self._right_dock = QDockWidget(self)
        self._right_dock.setObjectName("right_panels_dock")
        self._right_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        # Suppress the dock title bar entirely — toolbar buttons handle show/hide
        self._right_dock.setTitleBarWidget(QWidget())
        self._right_dock.setWidget(self._right_splitter)
        self.addDockWidget(Qt.RightDockWidgetArea, self._right_dock)
        self.resizeDocks([self._right_dock], [600], Qt.Horizontal)

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

        # ── Simulation Control ────────────────────────────────────────────
        self._act_panel_sim = QAction("⚙", self)
        self._act_panel_sim.setCheckable(True)
        self._act_panel_sim.setChecked(True)
        self._act_panel_sim.setToolTip("Simulation Control")
        self._act_panel_sim.toggled.connect(self._on_toggle_sim_panel)
        tb.addAction(self._act_panel_sim)

        tb.addSeparator()

        # ── SNMP Traps ────────────────────────────────────────────────────
        self._act_panel_traps = QAction("⚡", self)
        self._act_panel_traps.setCheckable(True)
        self._act_panel_traps.setChecked(True)
        self._act_panel_traps.setToolTip("SNMP Traps")
        self._act_panel_traps.toggled.connect(self._on_toggle_traps_panel)
        tb.addAction(self._act_panel_traps)

        # If the outer dock is closed (via float/close), uncheck both buttons
        self._right_dock.visibilityChanged.connect(self._on_right_dock_visibility)

        self.addToolBar(Qt.RightToolBarArea, tb)

    # ── Panel toggle slots ─────────────────────────────────────────────────────

    def _on_toggle_sim_panel(self, visible: bool):
        if visible:
            self._right_dock.show()   # parent must be visible before child
        self._sim_panel.setVisible(visible)
        if not self._act_panel_sim.isChecked() and not self._act_panel_traps.isChecked():
            self._right_dock.hide()

    def _on_toggle_traps_panel(self, visible: bool):
        if visible:
            self._right_dock.show()
        self._trap_panel.setVisible(visible)
        if not self._act_panel_sim.isChecked() and not self._act_panel_traps.isChecked():
            self._right_dock.hide()

    def _on_right_dock_visibility(self, visible: bool):
        """Outer dock hidden externally — uncheck both toolbar buttons."""
        if not visible:
            for btn in (self._act_panel_sim, self._act_panel_traps):
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
        self._act_export_json = QAction("Export as &JSON...", self)
        file_menu.addAction(self._act_new)
        file_menu.addSeparator()
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_save)
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
        self._act_link_mode = QAction("&Link Mode", self, checkable=True, shortcut="Ctrl+L")
        self._act_fit_view  = QAction("&Fit View", self, shortcut="Ctrl+Shift+F")
        self._act_reset_zoom = QAction("Reset Zoom", self)
        topo_menu.addAction(self._act_link_mode)
        topo_menu.addSeparator()
        topo_menu.addAction(self._act_fit_view)
        topo_menu.addAction(self._act_reset_zoom)

        # Templates submenu
        templates_menu = topo_menu.addMenu("Apply &Template")
        templates_menu.addAction(QAction("Data Center",    self, triggered=lambda: self._apply_template("data_center")))
        templates_menu.addAction(QAction("Enterprise LAN", self, triggered=lambda: self._apply_template("enterprise_lan")))
        templates_menu.addAction(QAction("Campus Network", self, triggered=lambda: self._apply_template("campus_network")))

        # Simulation
        sim_menu = menubar.addMenu("&Simulation")
        self._act_generate = QAction("&Generate Datasets", self, shortcut="F5")
        self._act_start    = QAction("&Start Simulator",   self, shortcut="F6")
        self._act_stop     = QAction("S&top Simulator",    self, shortcut="F7")
        self._act_clear    = QAction("&Clear Simulation",  self)
        self._act_discover = QAction("&Discover Topology via SNMP...", self, shortcut="F8")
        sim_menu.addAction(self._act_generate)
        sim_menu.addSeparator()
        sim_menu.addAction(self._act_start)
        sim_menu.addAction(self._act_stop)
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
        self._act_export_json.triggered.connect(self._export_json)
        self._act_add_device.triggered.connect(self._add_device)
        self._act_bulk_add.triggered.connect(self._bulk_add)
        self._act_remove_selected.triggered.connect(self._remove_selected)
        self._act_link_mode.toggled.connect(self._toggle_link_mode)
        self._act_fit_view.triggered.connect(self._topology_view.fit_view)
        self._act_reset_zoom.triggered.connect(self._topology_view.reset_zoom)
        self._act_generate.triggered.connect(self._generate_datasets)
        self._act_start.triggered.connect(self._start_simulator)
        self._act_stop.triggered.connect(self._stop_simulator)
        self._act_clear.triggered.connect(self._clear_simulation)
        self._act_discover.triggered.connect(self._discover_topology)

        # Simulation panel
        self._sim_panel.sig_generate.connect(self._generate_datasets)
        self._sim_panel.sig_start.connect(self._start_simulator)
        self._sim_panel.sig_stop.connect(self._stop_simulator)
        self._sim_panel.sig_clear.connect(self._clear_simulation)
        self._sim_panel.sig_randomize.connect(self._randomize_metrics)

        # SNMPSim callbacks — push into a queue; main-thread timer drains it.
        # Using a queue instead of direct signal emission prevents crashes when
        # the daemon thread emits while Qt is mid-repaint (e.g. on maximize).
        self.snmpsim.set_log_callback(
            lambda msg: self._log_queue.put(("log", msg, "info"))
        )
        self.snmpsim.set_status_callback(
            lambda s: self._log_queue.put(("status", s))
        )

        # Trap panel ↔ trap engine
        self._trap_panel.sig_apply.connect(self._trap_engine.configure)
        self._trap_panel.sig_simulate.connect(self._on_trap_simulate)
        self._trap_engine.trap_sent.connect(self._trap_panel.add_event)
        self._trap_engine.trap_error.connect(self._trap_panel.add_error)

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
            SimulationPanel { background: #161b22; }
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
            self._sim_panel.log(f"Added device: {device.name} ({device.ip_address})", "success")

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
            self._sim_panel.log(f"Updated device: {device.name}", "info")

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
            self._sim_panel.log(f"Removed device: {device.name}", "warning")

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
            self._sim_panel.log(
                f"Added {len(devices)} devices ({values['device_type'].value}s)",
                "success"
            )

    # ------------------------------------------------------------------ #
    #  Topology operations                                                 #
    # ------------------------------------------------------------------ #

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
                self._sim_panel.log(f"Linked: {src.name} ↔ {dst.name}", "success")
            self._refresh_stats()

    def _on_device_moved(self, device_id: str, x: float, y: float):
        self.topology.set_position(device_id, x, y)

    def _on_node_right_click(self, device_id: str, screen_pos):
        device = self.device_manager.get_device(device_id)
        _menu_style = """
            QMenu { background: #161b22; color: #e6edf3; border: 1px solid #30363d; }
            QMenu::item:selected { background: #1f6feb; }
        """
        menu = QMenu(self)
        menu.setStyleSheet(_menu_style)
        edit_act   = menu.addAction("Edit Device...")
        remove_act = menu.addAction("Remove Device")
        menu.addSeparator()
        info_act   = menu.addAction("Show Info")

        trap_actions: dict = {}
        if device:
            menu.addSeparator()
            trap_menu = menu.addMenu("Send Trap \u25b6")
            trap_menu.setStyleSheet(_menu_style)
            _LINK_TRAPS = {TrapType.LINK_DOWN, TrapType.LINK_UP}
            applicable = [
                t for t in get_applicable_traps(
                    device.device_type.value, device.vendor.value
                )
                if t not in _LINK_TRAPS
            ]
            for tt in applicable:
                act = trap_menu.addAction(TRAP_DEFINITIONS[tt].display_name)
                trap_actions[act] = tt

        chosen = menu.exec(screen_pos)
        if chosen == edit_act:
            self._edit_device(device_id)
        elif chosen == remove_act:
            self._remove_device(device_id)
        elif chosen == info_act:
            self._show_device_info(device_id)
        elif chosen in trap_actions and device:
            self._send_trap(device, trap_actions[chosen])

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
        chosen = menu.exec(screen_pos)
        if chosen == toggle_act:
            if broken:
                self.topology.restore_link(src_id, dst_id)
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, False)
                self._sim_panel.log(f"Link restored: {src.name} <-> {dst.name}", "success")
                trap_type = TrapType.LINK_UP
            else:
                self.topology.break_link(src_id, dst_id)
                self._topology_view.topology_scene.set_edge_broken(src_id, dst_id, True)
                self._sim_panel.log(f"Link broken: {src.name} <-> {dst.name}", "error")
                trap_type = TrapType.LINK_DOWN
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
                self._trap_panel.set_simulating(False)
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
        gen = SNMPRecGenerator(output_dir="datasets")
        gen.generate_device(device, self.topology)

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
            f"IP:          {device.ip_address}\n"
            f"SNMP Port:   {device.snmp_port}\n"
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

    def _apply_template(self, template_name: str):
        if self.topology.node_count() > 0:
            reply = QMessageBox.question(
                self, "Apply Template",
                "This will clear the current topology. Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._new_topology(confirm=False)
        self.topology.apply_template(template_name, self.device_manager, self.ip_manager)
        # Sync scene with topology
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
        self._sim_panel.log(f"Applied template: {template_name.replace('_', ' ').title()}", "success")

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

        self._sim_panel.log("Starting dataset generation...", "info")
        self._sim_panel.set_status("Generating...")
        self._sim_panel.show_progress(0, self.topology.node_count())

        self._worker = GeneratorWorker(self.topology, self._datasets_dir)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_gen_progress)
        self._worker.log.connect(self._sim_panel.log)
        self._worker.finished.connect(self._on_gen_finished)
        self._worker.error.connect(self._on_gen_error)
        self._worker_thread.start()

    def _on_gen_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _on_gen_finished(self):
        self._worker_thread.quit()
        self._worker_thread.wait()
        files = self._worker.result
        self._generated_files = files
        self._sim_panel.log(f"Generated {len(files)} dataset files in '{self._datasets_dir}/'", "success")
        self._sim_panel.set_status("Datasets ready")
        self._sim_panel.set_datasets_ready(True)
        self._sim_panel.set_stats(
            self.topology.node_count(),
            self.topology.edge_count(),
            len(files),
        )

    def _on_gen_error(self, error: str):
        self._worker_thread.quit()
        self._worker_thread.wait()
        self._sim_panel.log(f"Generation error: {error}", "error")
        self._sim_panel.set_status("Error")

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

        interface = self._sim_panel.selected_interface
        if not interface:
            QMessageBox.warning(
                self, "No Interface Selected",
                "Select a network interface in the panel before starting.\n"
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

        device_ips = [d.ip_address for d in self.device_manager.get_all_devices()]
        mask = self._sim_panel.subnet_mask

        self._sim_panel.log(
            f"Binding {len(device_ips)} IPs to interface '{interface}'...", "info"
        )
        self._sim_panel.set_status("Binding IPs...")
        self._sim_panel.show_progress(0, len(device_ips))
        # Disable start/stop during binding
        self._sim_panel.btn_start.setEnabled(False)
        self._sim_panel.btn_stop.setEnabled(False)

        self._bound_interface = interface
        self._bind_worker = IPBindWorker(interface, device_ips, mask)
        self._bind_thread = QThread()
        self._bind_worker.moveToThread(self._bind_thread)
        self._bind_thread.started.connect(self._bind_worker.run)
        self._bind_worker.progress.connect(self._on_bind_progress)
        self._bind_worker.log.connect(self._sim_panel.log)
        self._bind_worker.finished.connect(self._on_bind_finished)
        self._bind_worker.error.connect(self._on_bind_error)
        self._bind_thread.start()

    def _on_bind_progress(self, current: int, total: int):
        self._sim_panel.show_progress(current, total)

    def _on_bind_finished(self):
        self._bind_thread.quit()
        self._bind_thread.wait()
        bound_ips = self._bind_worker.result
        self._bound_ips = bound_ips
        self._sim_panel.set_bound_count(len(bound_ips))

        if not bound_ips:
            self._sim_panel.log("No IPs were bound — aborting simulator start.", "error")
            self._sim_panel.set_status("Error: no IPs bound")
            self._sim_panel.set_datasets_ready(True)
            return

        failed = len(self.device_manager.get_all_devices()) - len(bound_ips)
        if failed:
            self._sim_panel.log(
                f"Warning: {failed} IP(s) could not be bound.", "warning"
            )

        self._sim_panel.log(
            f"Bound {len(bound_ips)} IPs. Launching SNMPSim on port 161...", "success"
        )
        ok = self.snmpsim.start(device_ips=bound_ips, port=161)
        if ok:
            self._sim_panel.set_simulator_running(True)
            self._status_label.setText(
                f"SNMPSim running — {len(bound_ips)} devices — PID {self.snmpsim.get_pid()}"
            )
        else:
            self._sim_panel.set_simulator_running(False)
            self._sim_panel.set_datasets_ready(True)

    def _on_bind_error(self, error: str):
        self._bind_thread.quit()
        self._bind_thread.wait()
        self._sim_panel.log(f"IP bind error: {error}", "error")
        self._sim_panel.set_status("Error")
        self._sim_panel.set_datasets_ready(True)

    def _stop_simulator(self):
        self.snmpsim.stop()
        self._sim_panel.set_simulator_running(False)
        self._status_label.setText("Removing bound IPs...")

        if self._bound_ips and self._bound_interface:
            self._sim_panel.log(
                f"Removing {len(self._bound_ips)} IPs from '{self._bound_interface}'...",
                "info",
            )
            self._sim_panel.set_status("Removing IPs...")
            self._unbind_worker = IPUnbindWorker(self._bound_interface, list(self._bound_ips))
            self._unbind_thread = QThread()
            self._unbind_worker.moveToThread(self._unbind_thread)
            self._unbind_thread.started.connect(self._unbind_worker.run)
            self._unbind_worker.log.connect(self._sim_panel.log)
            self._unbind_worker.finished.connect(self._on_unbind_finished)
            self._unbind_thread.start()
        else:
            self._status_label.setText("Ready")
            self._sim_panel.set_status("Stopped")

    def _on_unbind_finished(self):
        self._unbind_thread.quit()
        self._unbind_thread.wait()
        self._bound_ips = []
        self._bound_interface = ""
        self._sim_panel.set_bound_count(0)
        self._sim_panel.set_status("Stopped")
        self._status_label.setText("Ready")
        self._sim_panel.log("All IPs removed from interface.", "info")

    def _clear_simulation(self):
        reply = QMessageBox.question(
            self, "Clear Simulation",
            "Stop simulator, remove bound IPs, and clear all devices and datasets?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            if self.snmpsim.is_running():
                self.snmpsim.stop()
            # Synchronously remove bound IPs (user is waiting anyway)
            if self._bound_ips and self._bound_interface:
                self._sim_panel.log(
                    f"Removing {len(self._bound_ips)} bound IPs...", "info"
                )
                remove_ips_batch(
                    self._bound_interface,
                    self._bound_ips,
                    log_cb=self._sim_panel.log,
                )
                self._bound_ips = []
                self._bound_interface = ""
                self._sim_panel.set_bound_count(0)
            # Remove dataset directories (IP-based subdirs)
            ds_path = Path(self._datasets_dir)
            for child in ds_path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                elif child.suffix == ".snmprec":
                    child.unlink(missing_ok=True)
            self._new_topology(confirm=False)
            self._generated_files = []
            self._sim_panel.set_simulator_running(False)
            self._sim_panel.set_datasets_ready(False)
            self._sim_panel.set_status("Idle")
            self._sim_panel.log("Simulation cleared.", "warning")

    def _randomize_metrics(self):
        self.device_manager.randomize_all_metrics()
        self._sim_panel.log("Metrics randomized. Regenerate datasets to apply.", "info")

    # ------------------------------------------------------------------ #
    #  File I/O                                                            #
    # ------------------------------------------------------------------ #

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
        self._trap_panel.set_simulating(False)
        self.topology.clear()
        self.device_manager.clear()
        self.ip_manager.reset()
        self._topology_view.topology_scene.clear_all()
        self._refresh_device_table()
        self._refresh_stats()
        self._sim_panel.log("New topology created.", "info")

    def _save_topology(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Topology", self._topologies_dir,
            "JSON Files (*.json);;All Files (*)"
        )
        if path:
            data = self.topology.to_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._sim_panel.log(f"Topology saved: {path}", "success")

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
                self._sim_panel.log(f"Topology loaded: {path}", "success")
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
            self._sim_panel.log(f"Exported: {path}", "success")

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

    # ------------------------------------------------------------------ #
    #  UI Refresh                                                          #
    # ------------------------------------------------------------------ #

    def _refresh_device_table(self):
        devices = self.device_manager.get_all_devices()
        self._device_table.setRowCount(len(devices))
        type_colors = {
            DeviceType.ROUTER: QColor("#1f6feb"),
            DeviceType.SWITCH: QColor("#238636"),
            DeviceType.SERVER: QColor("#8957e5"),
        }
        for row, device in enumerate(devices):
            name_item = QTableWidgetItem(device.name)
            name_item.setData(Qt.UserRole, device.id)
            type_item = QTableWidgetItem(device.device_type.value.capitalize())
            type_item.setForeground(type_colors.get(device.device_type, QColor("white")))
            vendor_item = QTableWidgetItem(device.vendor.value)
            ip_item = QTableWidgetItem(device.ip_address)
            ip_item.setFont(QFont("Consolas", 9))
            iface_item  = QTableWidgetItem(str(device.interface_count))
            iface_item.setTextAlignment(Qt.AlignCenter)
            port_item   = QTableWidgetItem(str(device.snmp_port))
            port_item.setTextAlignment(Qt.AlignCenter)

            self._device_table.setItem(row, 0, name_item)
            self._device_table.setItem(row, 1, type_item)
            self._device_table.setItem(row, 2, vendor_item)
            self._device_table.setItem(row, 3, ip_item)
            self._device_table.setItem(row, 4, iface_item)
            self._device_table.setItem(row, 5, port_item)

    def _refresh_stats(self):
        self._sim_panel.set_stats(
            self.topology.node_count(),
            self.topology.edge_count(),
            len(self._generated_files),
        )

    def _drain_log_queue(self):
        """Drain log/status messages queued by the SNMPSim monitor thread."""
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item[0] == "log":
                    self._sim_panel.log(item[1], item[2])
                elif item[0] == "status":
                    self._sim_panel.set_status(item[1])
        except queue.Empty:
            pass

    def _refresh_status(self):
        if self.snmpsim.is_running():
            n_bound = len(self._bound_ips)
            self._status_label.setText(
                f"SNMPSim running — {n_bound} IPs on port 161 "
                f"— PID {self.snmpsim.get_pid()}"
            )
            self._sim_panel.set_status("Running")

    # ------------------------------------------------------------------ #
    #  Dialogs                                                             #
    # ------------------------------------------------------------------ #

    def _show_about(self):
        QMessageBox.about(
            self, "About SNMP Network Topology Simulator",
            "<h3>SNMP Network Topology Simulator</h3>"
            "<p>Visually build network topologies and generate SNMP simulation "
            "datasets compatible with SNMPSim.</p>"
            "<br>"
            "<b>Tech Stack:</b> Python 3.11+, PySide6, NetworkX, SNMPSim<br>"
            "<b>Supports:</b> Routers, Switches, Servers<br>"
            "<b>SNMP Versions:</b> v1, v2c<br>"
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
            snmpsim_running=self.snmpsim.is_running(),
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
        self._trap_engine.stop()
        event.accept()

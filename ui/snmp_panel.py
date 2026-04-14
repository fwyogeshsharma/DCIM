"""
SNMP Simulation Panel — network binding, dataset generation, SNMPSim controls,
and embedded SNMP Trap receiver / log.

The log console has been moved to ConsolePanel.
The gNMI controls have been moved to GNMIPanel.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QProgressBar,
    QComboBox, QLineEdit, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QObject
from PySide6.QtGui import QFont, QColor

from core.trap_definitions import TRAP_DEFINITIONS, SEVERITY_COLOR
from core.trap_engine import TrapEvent


# ------------------------------------------------------------------ #
#  Status badge                                                        #
# ------------------------------------------------------------------ #

class StatusBadge(QLabel):
    def __init__(self, parent=None):
        super().__init__("● Idle", parent)
        self.setFont(QFont("Arial", 9, QFont.Bold))
        self._set_idle()

    def _set_idle(self):
        self.setText("● Idle")
        self.setStyleSheet("color: #8b949e; padding: 2px 8px;")

    def set_status(self, status: str):
        s = status.lower()
        if "running" in s:
            self.setText(f"● {status}")
            self.setStyleSheet("color: #3fb950; padding: 2px 8px;")
        elif "error" in s or "stopped" in s:
            self.setText(f"● {status}")
            self.setStyleSheet("color: #f85149; padding: 2px 8px;")
        elif any(k in s for k in ("generating", "starting", "binding", "removing")):
            self.setText(f"● {status}")
            self.setStyleSheet("color: #d29922; padding: 2px 8px;")
        else:
            self.setText(f"● {status}")
            self.setStyleSheet("color: #8b949e; padding: 2px 8px;")


# ------------------------------------------------------------------ #
#  Interface loader (runs in background thread)                        #
# ------------------------------------------------------------------ #

class _InterfaceLoader(QObject):
    finished = Signal()

    def run(self):
        from core.ip_binder import get_interfaces
        self.result = get_interfaces()
        self.finished.emit()


# ------------------------------------------------------------------ #
#  SNMP Simulation Panel                                               #
# ------------------------------------------------------------------ #

class SNMPPanel(QWidget):
    # SNMP Simulator signals
    sig_generate  = Signal()
    sig_start     = Signal()
    sig_stop      = Signal()
    sig_cancel    = Signal()
    sig_clear     = Signal()
    sig_refresh_interfaces = Signal()

    # SNMP Trap signals (forwarded from embedded trap section)
    sig_trap_apply    = Signal(str, int)   # (ip, port)
    sig_trap_simulate = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._binding = False
        self._severity_counts: dict[str, int] = {s: 0 for s in SEVERITY_COLOR}
        self._build_ui()
        self._load_interfaces()

    # ------------------------------------------------------------------ #
    #  Build UI                                                            #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet(
            "background: #21262d; border-bottom: 1px solid #30363d;"
        )
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("SNMP Simulator")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        layout.addWidget(title_bar)

        # ── Scrollable content ────────────────────────────────────────────
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(8)
        layout.addWidget(content)
        layout = content_layout  # redirect remaining additions

        # ── Status ────────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Network binding group ──────────────────────────────────────────
        net_group = QGroupBox("Network Interface Binding")
        net_group.setStyleSheet(self._group_style())
        net_layout = QVBoxLayout(net_group)
        net_layout.setContentsMargins(6, 4, 6, 6)
        net_layout.setSpacing(4)

        hint = QLabel(
            "Device IPs will be added to the selected adapter"
        )
        hint.setFont(QFont("Arial", 8))
        hint.setStyleSheet("color: #8b949e;")
        hint.setWordWrap(True)
        net_layout.addWidget(hint)

        iface_row = QHBoxLayout()
        iface_row.setSpacing(4)
        self.iface_combo = QComboBox()
        self.iface_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.iface_combo.setMinimumWidth(100)
        self.iface_combo.setStyleSheet(self._combo_style())
        self.iface_combo.setPlaceholderText("Loading interfaces...")
        iface_row.addWidget(self.iface_combo, stretch=1)

        self.btn_refresh_ifaces = QPushButton("⟳")
        self.btn_refresh_ifaces.setFixedWidth(32)
        self.btn_refresh_ifaces.setFixedHeight(28)
        self.btn_refresh_ifaces.setStyleSheet(self._btn_secondary_style())
        self.btn_refresh_ifaces.setToolTip("Re-scan network adapters")
        self.btn_refresh_ifaces.clicked.connect(self._load_interfaces)
        iface_row.addWidget(self.btn_refresh_ifaces)
        net_layout.addLayout(iface_row)

        mask_row = QHBoxLayout()
        mask_label = QLabel("Subnet Mask:")
        mask_label.setFont(QFont("Arial", 9))
        mask_label.setStyleSheet("color: #8b949e;")
        mask_row.addWidget(mask_label)
        self.mask_edit = QLineEdit("255.255.255.0")
        self.mask_edit.setMaximumWidth(120)
        self.mask_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mask_edit.setFont(QFont("Consolas", 9))
        self.mask_edit.setStyleSheet(self._lineedit_style())
        mask_row.addWidget(self.mask_edit, stretch=1)
        net_layout.addLayout(mask_row)

        self.bound_label = QLabel("IPs bound: 0")
        self.bound_label.setFont(QFont("Consolas", 8))
        self.bound_label.setStyleSheet("color: #3fb950;")
        net_layout.addWidget(self.bound_label)
        layout.addWidget(net_group)

        # ── Active Devices ─────────────────────────────────────────────────
        stats_group = QGroupBox("Active Devices")
        stats_group.setStyleSheet(self._group_style())
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setContentsMargins(6, 4, 6, 6)
        stats_layout.setSpacing(2)
        self.lbl_switches      = QLabel("Switches:      0")
        self.lbl_routers       = QLabel("Routers:       0")
        self.lbl_firewalls     = QLabel("Firewalls:     0")
        self.lbl_load_balancers = QLabel("Load Balancers: 0")
        self.lbl_servers       = QLabel("Servers:       0")
        self.lbl_total         = QLabel("Total:         0")
        for lbl in (self.lbl_switches, self.lbl_routers, self.lbl_firewalls,
                    self.lbl_load_balancers, self.lbl_servers, self.lbl_total):
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            stats_layout.addWidget(lbl)
        layout.addWidget(stats_group)

        # ── Progress bar ───────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #30363d; border-radius: 4px;
                background: #0d1117; color: #e6edf3;
                height: 16px; text-align: center;
            }
            QProgressBar::chunk { background: #1f6feb; border-radius: 3px; }
        """)
        self.progress.hide()
        layout.addWidget(self.progress)

        # ── Action buttons ─────────────────────────────────────────────────
        self.btn_generate = QPushButton("Generate Datasets")
        self.btn_generate.setStyleSheet(self._btn_generate_style())
        self.btn_generate.clicked.connect(self.sig_generate.emit)
        layout.addWidget(self.btn_generate)

        ss_row = QHBoxLayout()
        self.btn_start = QPushButton("Start SNMP Simulator")
        self.btn_start.setStyleSheet(self._btn_start_style())
        self.btn_start.clicked.connect(self.sig_start.emit)
        self.btn_start.setEnabled(False)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet(self._btn_stop_style())
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.setEnabled(False)
        ss_row.addWidget(self.btn_start)
        ss_row.addWidget(self.btn_stop)
        layout.addLayout(ss_row)

        misc_row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Simulation")
        self.btn_clear.setStyleSheet(self._btn_secondary_style())
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self.sig_clear.emit)
        misc_row.addWidget(self.btn_clear)
        layout.addLayout(misc_row)

        # ── SNMP Traps section ─────────────────────────────────────────────
        traps_group = QGroupBox("SNMP Traps")
        traps_group.setStyleSheet(self._group_style())
        traps_layout = QVBoxLayout(traps_group)
        traps_layout.setContentsMargins(6, 4, 6, 6)
        traps_layout.setSpacing(4)

        # Receiver config row
        recv_row = QHBoxLayout()
        recv_row.setSpacing(4)
        recv_row.addWidget(QLabel("IP:"))
        self._trap_ip = QLineEdit("127.0.0.1")
        self._trap_ip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._trap_ip.setStyleSheet(self._lineedit_style())
        recv_row.addWidget(self._trap_ip, stretch=1)
        recv_row.addWidget(QLabel("Port:"))
        self._trap_port = QSpinBox()
        self._trap_port.setRange(1, 65535)
        self._trap_port.setValue(162)
        self._trap_port.setFixedWidth(60)
        self._trap_port.setStyleSheet(
            "QSpinBox { background:#21262d; color:#e6edf3; "
            "border:1px solid #30363d; border-radius:4px; padding:2px 4px; }"
        )
        recv_row.addWidget(self._trap_port)
        trap_apply = QPushButton("Apply")
        trap_apply.setFixedWidth(48)
        trap_apply.setStyleSheet(self._btn_secondary_style())
        trap_apply.clicked.connect(self._on_trap_apply)
        recv_row.addWidget(trap_apply)
        traps_layout.addLayout(recv_row)

        # Simulate button
        self._trap_sim_btn = QPushButton("▶  Simulate Traps")
        self._trap_sim_btn.setCheckable(True)
        self._trap_sim_btn.setStyleSheet(self._btn_secondary_style())
        self._trap_sim_btn.toggled.connect(self._on_trap_simulate_toggled)
        traps_layout.addWidget(self._trap_sim_btn)

        # Severity counter badges + clear
        sev_row = QHBoxLayout()
        sev_row.setSpacing(3)
        self._sev_labels: dict[str, QLabel] = {}
        for sev, color in SEVERITY_COLOR.items():
            badge = QLabel("0")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedWidth(32)
            badge.setStyleSheet(
                f"background:{color}; color:white; border-radius:3px;"
                f" padding:1px 3px; font-weight:bold; font-size:10px;"
            )
            badge.setToolTip(sev.capitalize())
            self._sev_labels[sev] = badge
            sev_row.addWidget(badge)
        sev_row.addStretch()
        trap_clr = QPushButton("Clear")
        trap_clr.setFixedWidth(46)
        trap_clr.setStyleSheet(self._btn_secondary_style())
        trap_clr.clicked.connect(self.clear_traps)
        sev_row.addWidget(trap_clr)
        traps_layout.addLayout(sev_row)

        # Trap log table
        self._trap_table = QTableWidget(0, 5)
        self._trap_table.setHorizontalHeaderLabels(
            ["Time", "Device", "IP", "Trap Type", "Details"]
        )
        hdr = self._trap_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        self._trap_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._trap_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._trap_table.setAlternatingRowColors(True)
        self._trap_table.verticalHeader().setVisible(False)
        self._trap_table.setFont(QFont("Consolas", 8))
        self._trap_table.setMinimumHeight(120)
        self._trap_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._trap_table.setStyleSheet("""
            QTableWidget {
                background: #161b22; color: #e6edf3;
                border: 1px solid #30363d;
                alternate-background-color: #0d1117;
                gridline-color: #30363d;
            }
            QHeaderView::section {
                background: #21262d; color: #8b949e;
                padding: 3px; border: none;
                border-bottom: 1px solid #30363d;
            }
            QTableWidget::item:selected { background: #1f6feb; }
        """)
        traps_layout.addWidget(self._trap_table)
        layout.addWidget(traps_group)
        layout.addStretch()

    # ------------------------------------------------------------------ #
    #  Interface loading                                                   #
    # ------------------------------------------------------------------ #

    def _load_interfaces(self):
        self.btn_refresh_ifaces.setEnabled(False)
        self.btn_refresh_ifaces.setText("...")
        self.iface_combo.setEnabled(False)

        self._iface_thread = QThread()
        self._iface_worker = _InterfaceLoader()
        self._iface_worker.moveToThread(self._iface_thread)
        self._iface_thread.started.connect(self._iface_worker.run)
        self._iface_worker.finished.connect(self._on_interfaces_loaded)
        self._iface_thread.start()

    def _on_interfaces_loaded(self):
        self._iface_thread.quit()
        self._iface_thread.wait()
        ifaces = self._iface_worker.result
        self.btn_refresh_ifaces.setEnabled(True)
        self.btn_refresh_ifaces.setText("⟳")
        self.iface_combo.setEnabled(True)

        prev = self.iface_combo.currentData()
        self.iface_combo.clear()

        if not ifaces:
            self.iface_combo.addItem("(no adapters found)", None)
            return
        for name, label in ifaces:
            self.iface_combo.addItem(label, name)
        if prev:
            idx = self.iface_combo.findData(prev)
            if idx >= 0:
                self.iface_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    #  Public API — SNMP simulator                                        #
    # ------------------------------------------------------------------ #

    def set_status(self, status: str):
        self.status_badge.set_status(status)

    def set_device_counts(self, switches: int, routers: int, servers: int,
                          firewalls: int = 0, load_balancers: int = 0):
        self.lbl_switches.setText(f"Switches:       {switches}")
        self.lbl_routers.setText(f"Routers:        {routers}")
        self.lbl_firewalls.setText(f"Firewalls:      {firewalls}")
        self.lbl_load_balancers.setText(f"Load Balancers: {load_balancers}")
        self.lbl_servers.setText(f"Servers:        {servers}")
        self.lbl_total.setText(f"Total:          {switches + routers + servers + firewalls + load_balancers}")

    def set_bound_count(self, count: int):
        if count > 0:
            self.bound_label.setText(f"IPs bound: {count}")
            self.bound_label.setStyleSheet("color: #3fb950;")
        else:
            self.bound_label.setText("IPs bound: 0")
            self.bound_label.setStyleSheet("color: #8b949e;")

    def show_progress(self, value: int, maximum: int = 100):
        self.progress.setMaximum(maximum)
        self.progress.setValue(value)
        self.progress.show()
        if value >= maximum:
            QTimer.singleShot(1500, self.progress.hide)

    def set_binding(self, binding: bool):
        self._binding = binding
        if binding:
            self.btn_start.setEnabled(False)
            self.btn_stop.setText("Cancel")
            self.btn_stop.setStyleSheet(self._btn_cancel_style())
            self.btn_stop.setEnabled(True)
        else:
            self.btn_stop.setText("Stop")
            self.btn_stop.setStyleSheet(self._btn_stop_style())
            self.btn_stop.setEnabled(False)

    def _on_stop_clicked(self):
        if self._binding:
            self.sig_cancel.emit()
        else:
            self.sig_stop.emit()

    def set_simulator_running(self, running: bool):
        self._running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_generate.setEnabled(not running)
        self.iface_combo.setEnabled(not running)
        self.mask_edit.setEnabled(not running)
        self.btn_refresh_ifaces.setEnabled(not running)

    def set_datasets_ready(self, ready: bool):
        self.btn_start.setEnabled(ready and not self._running)
        self.btn_clear.setEnabled(ready)

    @property
    def selected_interface(self) -> str:
        data = self.iface_combo.currentData()
        return data if data else ""

    @property
    def subnet_mask(self) -> str:
        return self.mask_edit.text().strip() or "255.255.255.0"

    # ------------------------------------------------------------------ #
    #  Public API — SNMP Traps                                            #
    # ------------------------------------------------------------------ #

    def add_trap_event(self, event: TrapEvent):
        defn = event.defn
        row  = self._trap_table.rowCount()
        self._trap_table.insertRow(row)
        bg = QColor(SEVERITY_COLOR.get(defn.severity, "#888"))
        bg.setAlpha(45)
        for col, text in enumerate([
            event.timestamp.strftime("%H:%M:%S"),
            event.device.name,
            event.device.ip_address,
            defn.display_name,
            event.details,
        ]):
            item = QTableWidgetItem(text)
            item.setBackground(bg)
            if col == 3:
                f = item.font(); f.setBold(True); item.setFont(f)
            self._trap_table.setItem(row, col, item)
        self._trap_table.scrollToBottom()
        self._severity_counts[defn.severity] = (
            self._severity_counts.get(defn.severity, 0) + 1
        )
        self._sev_labels[defn.severity].setText(
            str(self._severity_counts[defn.severity])
        )

    def add_trap_error(self, msg: str):
        row = self._trap_table.rowCount()
        self._trap_table.insertRow(row)
        item = QTableWidgetItem(msg)
        item.setForeground(QColor("#e74c3c"))
        self._trap_table.setItem(row, 4, item)
        self._trap_table.scrollToBottom()

    def clear_traps(self):
        self._trap_table.setRowCount(0)
        self._severity_counts = {s: 0 for s in SEVERITY_COLOR}
        for lbl in self._sev_labels.values():
            lbl.setText("0")

    def set_simulating(self, active: bool):
        self._trap_sim_btn.blockSignals(True)
        self._trap_sim_btn.setChecked(active)
        self._trap_sim_btn.setText("⏹  Stop Simulation" if active else "▶  Simulate Traps")
        self._trap_sim_btn.blockSignals(False)

    def _on_trap_apply(self):
        self.sig_trap_apply.emit(self._trap_ip.text().strip(), self._trap_port.value())

    def _on_trap_simulate_toggled(self, checked: bool):
        self._trap_sim_btn.setText("⏹  Stop Simulation" if checked else "▶  Simulate Traps")
        self.sig_trap_simulate.emit(checked)

    # ------------------------------------------------------------------ #
    #  Style helpers                                                       #
    # ------------------------------------------------------------------ #

    def _group_style(self) -> str:
        return (
            "QGroupBox { color: #8b949e; font-size: 7pt; "
            "border: 1px solid #30363d; border-radius: 4px; "
            "margin-top: 8px; padding-top: 4px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        )

    def _combo_style(self) -> str:
        return """
            QComboBox {
                background: #21262d; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 4px;
                padding: 4px 6px; font-size: 8pt;
            }
            QComboBox:hover { border-color: #58a6ff; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #161b22; color: #e6edf3;
                border: 1px solid #30363d;
                selection-background-color: #1f6feb;
            }
        """

    def _lineedit_style(self) -> str:
        return (
            "QLineEdit { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 3px 6px; }"
            "QLineEdit:focus { border-color: #58a6ff; }"
        )

    def _btn_generate_style(self) -> str:
        return (
            "QPushButton { background: #1f6feb; color: white; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #388bfd; } "
            "QPushButton:pressed { background: #1158c7; } "
            "QPushButton:disabled { background: #30363d; color: #6e7681; }"
        )

    def _btn_start_style(self) -> str:
        return (
            "QPushButton { background: #238636; color: white; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #2ea043; } "
            "QPushButton:pressed { background: #196127; } "
            "QPushButton:disabled { background: #30363d; color: #6e7681; }"
        )

    def _btn_cancel_style(self) -> str:
        return (
            "QPushButton { background: #9a6700; color: white; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #bb8009; } "
            "QPushButton:pressed { background: #7a5200; }"
        )

    def _btn_stop_style(self) -> str:
        return (
            "QPushButton { background: #b62324; color: white; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #d12b2b; } "
            "QPushButton:pressed { background: #8d1919; } "
            "QPushButton:disabled { background: #30363d; color: #6e7681; }"
        )

    def _btn_secondary_style(self) -> str:
        return (
            "QPushButton { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 8px; } "
            "QPushButton:hover { background: #30363d; } "
            "QPushButton:pressed { background: #0d1117; } "
            "QPushButton:disabled { color: #6e7681; }"
        )
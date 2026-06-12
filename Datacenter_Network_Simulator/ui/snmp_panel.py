"""
SNMP Simulation Panel — dataset generation and SNMPSim controls.

The log console has been moved to ConsolePanel.
The gNMI controls have been moved to GNMIPanel.
The SNMP Trap receiver and Rule Engine controls have been moved to TrapPanel.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QProgressBar, QFrame, QSpinBox,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont


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
#  SNMP Simulation Panel                                               #
# ------------------------------------------------------------------ #

class SNMPPanel(QWidget):
    sig_generate  = Signal()
    sig_start     = Signal()
    sig_stop      = Signal()
    sig_cancel    = Signal()
    sig_clear     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._binding = False
        self._build_ui()

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
        layout.addWidget(content, stretch=1)
        layout = content_layout  # redirect remaining additions

        # ── Status ────────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Active Devices ─────────────────────────────────────────────────
        self.stats_group = QGroupBox("Active Devices")
        self.stats_group.setStyleSheet(self._group_style())
        self.stats_group.hide()
        stats_layout = QVBoxLayout(self.stats_group)
        stats_layout.setContentsMargins(6, 4, 6, 6)
        stats_layout.setSpacing(2)

        # Network devices
        self.lbl_switches       = QLabel("Switches: 0")
        self.lbl_routers        = QLabel("Routers: 0")
        self.lbl_servers        = QLabel("Servers: 0")
        self.lbl_firewalls      = QLabel("Firewalls: 0")
        self.lbl_load_balancers = QLabel("Load Balancers: 0")
        # Infrastructure / mgmt devices
        self.lbl_oob_switches   = QLabel("OOB Switches: 0")
        self.lbl_sensors        = QLabel("Sensors: 0")
        self.lbl_ups            = QLabel("UPS: 0")
        self.lbl_pdus           = QLabel("Rack PDUs: 0")
        self.lbl_floor_pdus     = QLabel("Floor PDUs: 0")
        self.lbl_generators     = QLabel("Generators: 0")
        self.lbl_crahs          = QLabel("CRAHs: 0")
        self.lbl_cdus           = QLabel("CDUs: 0")
        self.lbl_total          = QLabel("Total: 0")

        _net_lbls  = (self.lbl_switches, self.lbl_routers, self.lbl_servers,
                      self.lbl_firewalls, self.lbl_load_balancers)
        _infra_lbls = (self.lbl_oob_switches, self.lbl_sensors, self.lbl_ups,
                       self.lbl_pdus, self.lbl_floor_pdus, self.lbl_generators,
                       self.lbl_crahs, self.lbl_cdus)

        for lbl in _net_lbls:
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            lbl.hide()
            stats_layout.addWidget(lbl)

        self._stats_sep = QFrame()
        self._stats_sep.setFrameShape(QFrame.HLine)
        self._stats_sep.setStyleSheet("color: #30363d;")
        self._stats_sep.hide()
        stats_layout.addWidget(self._stats_sep)

        for lbl in _infra_lbls:
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            lbl.hide()
            stats_layout.addWidget(lbl)

        self.lbl_total.setFont(QFont("Consolas", 9))
        self.lbl_total.setStyleSheet("color: #8b949e;")
        self.lbl_total.hide()
        stats_layout.addWidget(self.lbl_total)
        layout.addWidget(self.stats_group)

        # ── Network Links ──────────────────────────────────────────────────
        self.links_group = QGroupBox("Network Links")
        self.links_group.setStyleSheet(self._group_style())
        self.links_group.hide()
        links_layout = QVBoxLayout(self.links_group)
        links_layout.setContentsMargins(6, 4, 6, 6)
        links_layout.setSpacing(2)
        self.lbl_prod_links = QLabel("Prod Links: 0")
        self.lbl_mgmt_links = QLabel("Mgmt Links: 0")
        for lbl in (self.lbl_prod_links, self.lbl_mgmt_links):
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            links_layout.addWidget(lbl)
        layout.addWidget(self.links_group)

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

        # ── Port Configuration group ───────────────────────────────────────
        port_group = QGroupBox("Port Configuration")
        port_group.setStyleSheet(self._group_style())
        port_group_layout = QVBoxLayout(port_group)
        port_group_layout.setContentsMargins(8, 6, 8, 8)
        port_group_layout.setSpacing(4)

        # SNMP Port (snmpsim / read)
        port_row = QHBoxLayout()
        port_lbl = QLabel("SNMP Port:")
        port_lbl.setStyleSheet("color: #c9d1d9; font-size: 8pt;")
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(161)
        self.spin_port.setFixedWidth(70)
        self.spin_port.setToolTip(
            "UDP port snmpsim will listen on.\n"
            "Use 1162 or higher if Windows SNMP Service already occupies port 161."
        )
        self.spin_port.setStyleSheet(
            "QSpinBox { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 3px 6px; } "
            "QSpinBox:focus { border-color: #58a6ff; } "
            "QSpinBox:disabled { color: #6e7681; }"
        )
        port_row.addWidget(port_lbl)
        port_row.addStretch()
        port_row.addWidget(self.spin_port)
        port_group_layout.addLayout(port_row)
        snmp_port_hint = QLabel("Read  ·  GET  ·  WALK")
        snmp_port_hint.setStyleSheet("color: #484f58; font-size: 7pt; padding-left: 1px;")
        port_group_layout.addWidget(snmp_port_hint)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #21262d; margin-top: 2px; margin-bottom: 2px;")
        port_group_layout.addWidget(sep)

        # Mgmt Port (SET agent / write)
        set_port_row = QHBoxLayout()
        set_port_lbl = QLabel("Mgmt Port:")
        set_port_lbl.setStyleSheet("color: #c9d1d9; font-size: 8pt;")
        self.spin_set_port = QSpinBox()
        self.spin_set_port.setRange(1, 65535)
        self.spin_set_port.setValue(1161)
        self.spin_set_port.setFixedWidth(70)
        self.spin_set_port.setToolTip(
            "UDP port the SNMP management agent listens on for SNMP SET requests.\n"
            "DCIM systems use snmpset on this port to update sysName, sysLocation,\n"
            "rack position, model, and other asset fields.\n"
            "Community string = device IP address.\n"
            "Default: 1161"
        )
        self.spin_set_port.setStyleSheet(
            "QSpinBox { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 3px 6px; } "
            "QSpinBox:focus { border-color: #58a6ff; } "
            "QSpinBox:disabled { color: #6e7681; }"
        )
        set_port_row.addWidget(set_port_lbl)
        set_port_row.addStretch()
        set_port_row.addWidget(self.spin_set_port)
        port_group_layout.addLayout(set_port_row)
        mgmt_port_hint = QLabel("Write  ·  SET")
        mgmt_port_hint.setStyleSheet("color: #484f58; font-size: 7pt; padding-left: 1px;")
        port_group_layout.addWidget(mgmt_port_hint)

        layout.addWidget(port_group)

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

        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    #  Public API — SNMP simulator                                        #
    # ------------------------------------------------------------------ #

    def set_status(self, status: str):
        self.status_badge.set_status(status)

    def set_device_counts(self, switches: int, routers: int, servers: int,
                          firewalls: int = 0, load_balancers: int = 0,
                          ups: int = 0, pdus: int = 0, floor_pdus: int = 0,
                          oob_switches: int = 0, sensors: int = 0,
                          generators: int = 0, crahs: int = 0, cdus: int = 0):
        _net_entries = [
            (self.lbl_switches,       "Switches",       switches),
            (self.lbl_routers,        "Routers",        routers),
            (self.lbl_servers,        "Servers",        servers),
            (self.lbl_firewalls,      "Firewalls",      firewalls),
            (self.lbl_load_balancers, "Load Balancers", load_balancers),
        ]
        _infra_entries = [
            (self.lbl_oob_switches,   "OOB Switches",   oob_switches),
            (self.lbl_sensors,        "Sensors",        sensors),
            (self.lbl_ups,            "UPS",            ups),
            (self.lbl_pdus,           "Rack PDUs",      pdus),
            (self.lbl_floor_pdus,     "Floor PDUs",     floor_pdus),
            (self.lbl_generators,     "Generators",     generators),
            # CRAH/CDU are the only cooling devices with SNMP agents
            # (native comm cards) — chiller/pump/tower/valve are BACnet-only.
            (self.lbl_crahs,          "CRAHs",          crahs),
            (self.lbl_cdus,           "CDUs",           cdus),
        ]
        for lbl, name, n in _net_entries + _infra_entries:
            if n > 0:
                lbl.setText(f"{name}: {n}")
                lbl.show()
            else:
                lbl.hide()
        has_infra = any(n > 0 for _, _, n in _infra_entries)
        self._stats_sep.setVisible(has_infra)
        total = (switches + routers + servers + firewalls + load_balancers
                 + ups + pdus + floor_pdus + oob_switches + sensors
                 + generators + crahs + cdus)
        if total > 0:
            self.lbl_total.setText(f"Total: {total}")
            self.lbl_total.show()
        else:
            self.lbl_total.hide()

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

    def set_link_counts(self, prod: int = 0, mgmt: int = 0, power: int = 0):
        self.lbl_prod_links.setText(f"Prod Links: {prod}")
        self.lbl_mgmt_links.setText(f"Mgmt Links: {mgmt}")
        self.links_group.setVisible(prod > 0 or mgmt > 0)

    def get_snmp_port(self) -> int:
        return self.spin_port.value()

    def get_set_port(self) -> int:
        return self.spin_set_port.value()

    def set_simulator_running(self, running: bool):
        self._running = running
        self.stats_group.setVisible(running)
        if not running:
            self.links_group.hide()
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_generate.setEnabled(not running)
        self.spin_port.setEnabled(not running)
        self.spin_set_port.setEnabled(not running)

    def set_datasets_ready(self, ready: bool):
        self.btn_start.setEnabled(ready and not self._running)
        self.btn_clear.setEnabled(ready)

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
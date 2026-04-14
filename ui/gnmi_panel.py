"""
gNMI Simulator Panel — controls for the gRPC/gNMI server.

Serves OpenConfig telemetry for switches and routers.
Log output is forwarded to ConsolePanel.
"""
from __future__ import annotations
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QComboBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtGui import QFont

from ui.snmp_panel import StatusBadge   # reuse the same badge widget


class _IfaceLoader(QObject):
    """Load network interfaces in a background thread."""
    finished = Signal()

    def run(self):
        from core.ip_binder import get_interfaces
        self.result = get_interfaces()
        self.finished.emit()


class GNMIPanel(QWidget):
    sig_generate    = Signal()
    sig_gnmi_start  = Signal()
    sig_gnmi_stop   = Signal()
    sig_clear       = Signal()
    sig_proxy_toggle = Signal(bool)   # True = enable proxy, False = disable

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._proxy_running = False
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
        title_lbl = QLabel("gNMI Simulator")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        layout.addWidget(title_bar)

        # ── Content ────────────────────────────────────────────────────────
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(8)
        layout.addWidget(content)
        layout = cl  # redirect

        # ── Status row ─────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Network Interface Binding group ────────────────────────────────
        bind_group = QGroupBox("Network Interface Binding")
        bind_group.setStyleSheet(self._group_style())
        bind_layout = QVBoxLayout(bind_group)
        bind_layout.setContentsMargins(6, 4, 6, 6)
        bind_layout.setSpacing(4)

        bind_hint = QLabel(
            "Device IPs will be added to the selected adapter"
        )
        bind_hint.setFont(QFont("Arial", 8))
        bind_hint.setStyleSheet("color: #8b949e;")
        bind_hint.setWordWrap(True)
        bind_layout.addWidget(bind_hint)

        iface_row = QHBoxLayout()
        iface_row.setSpacing(4)
        self.iface_combo = QComboBox()
        self.iface_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.iface_combo.setStyleSheet(self._combo_style())
        self.iface_combo.setPlaceholderText("Loading interfaces…")
        iface_row.addWidget(self.iface_combo, stretch=1)
        self.btn_refresh_ifaces = QPushButton("⟳")
        self.btn_refresh_ifaces.setFixedWidth(32)
        self.btn_refresh_ifaces.setFixedHeight(28)
        self.btn_refresh_ifaces.setStyleSheet(self._btn_secondary_style())
        self.btn_refresh_ifaces.setToolTip("Re-scan network adapters")
        self.btn_refresh_ifaces.clicked.connect(self._load_interfaces)
        iface_row.addWidget(self.btn_refresh_ifaces)
        bind_layout.addLayout(iface_row)

        mask_row = QHBoxLayout()
        mask_lbl = QLabel("Subnet Mask:")
        mask_lbl.setFont(QFont("Arial", 9))
        mask_lbl.setStyleSheet("color: #8b949e;")
        mask_row.addWidget(mask_lbl)
        self.mask_edit = QLineEdit("255.255.255.0")
        self.mask_edit.setFont(QFont("Consolas", 9))
        self.mask_edit.setStyleSheet(self._lineedit_style())
        mask_row.addWidget(self.mask_edit, stretch=1)
        bind_layout.addLayout(mask_row)

        self.bound_label = QLabel("IPs bound: 0")
        self.bound_label.setFont(QFont("Consolas", 8))
        self.bound_label.setStyleSheet("color: #3fb950;")
        bind_layout.addWidget(self.bound_label)

        layout.addWidget(bind_group)

        # ── Targets group ──────────────────────────────────────────────────
        tgt_group = QGroupBox("Active Devices")
        tgt_group.setStyleSheet(self._group_style())
        tgt_layout = QVBoxLayout(tgt_group)
        tgt_layout.setContentsMargins(6, 4, 6, 6)
        tgt_layout.setSpacing(3)

        self.lbl_switches = QLabel("Switches:  0")
        self.lbl_routers  = QLabel("Routers:   0")
        self.lbl_total    = QLabel("Total:     0")
        for lbl in (self.lbl_switches, self.lbl_routers, self.lbl_total):
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            tgt_layout.addWidget(lbl)
        layout.addWidget(tgt_group)

        # ── Generate button + progress ────────────────────────────────────
        self.btn_generate = QPushButton("Generate Dataset")
        self.btn_generate.setStyleSheet(self._btn_generate_style())
        self.btn_generate.clicked.connect(self.sig_generate.emit)
        layout.addWidget(self.btn_generate)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setFont(QFont("Consolas", 8))
        self.lbl_progress.setStyleSheet("color: #8b949e;")
        self.lbl_progress.setAlignment(Qt.AlignCenter)
        self.lbl_progress.hide()
        layout.addWidget(self.lbl_progress)

        # ── Start / Stop buttons ───────────────────────────────────────────
        ss_row = QHBoxLayout()
        self.btn_start = QPushButton("Start gNMI Simulator")
        self.btn_start.setStyleSheet(self._btn_start_style())
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self.sig_gnmi_start.emit)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet(self._btn_stop_style())
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.sig_gnmi_stop.emit)
        ss_row.addWidget(self.btn_start)
        ss_row.addWidget(self.btn_stop)
        layout.addLayout(ss_row)

        # ── Clear button ──────────────────────────────────────────────────
        rc_row = QHBoxLayout()
        self.btn_clear = QPushButton("Clear Simulation")
        self.btn_clear.setStyleSheet(self._btn_secondary_style())
        self.btn_clear.setEnabled(False)
        self.btn_clear.clicked.connect(self.sig_clear.emit)
        rc_row.addWidget(self.btn_clear)
        layout.addLayout(rc_row)

        # ── Proxy Server Configuration group ───────────────────────────────
        cfg_group = QGroupBox("Proxy Server Configuration")
        cfg_group.setStyleSheet(self._group_style())
        cfg_layout = QVBoxLayout(cfg_group)
        cfg_layout.setContentsMargins(6, 4, 6, 6)
        cfg_layout.setSpacing(6)

        proxy_hint = QLabel(
            "Aggregates all device gRPC endpoints behind a single port.\n"
            "Start the gNMI simulator first, then enable the proxy."
        )
        proxy_hint.setFont(QFont("Arial", 8))
        proxy_hint.setStyleSheet("color: #8b949e;")
        proxy_hint.setWordWrap(True)
        cfg_layout.addWidget(proxy_hint)

        port_row = QHBoxLayout()
        port_row.setSpacing(4)
        port_lbl = QLabel("Proxy Port:")
        port_lbl.setFont(QFont("Arial", 9))
        port_lbl.setStyleSheet("color: #8b949e;")
        port_row.addWidget(port_lbl)
        self.port_edit = QLineEdit("50051")
        self.port_edit.setMaximumWidth(75)
        self.port_edit.setFont(QFont("Consolas", 9))
        self.port_edit.setStyleSheet(self._lineedit_style())
        self.port_edit.setToolTip(
            "Proxy gRPC port — clients connect here and use target= to address a device (default 50051)")
        port_row.addWidget(self.port_edit)
        port_row.addStretch()
        cfg_layout.addLayout(port_row)

        self.btn_proxy_toggle = QPushButton("Enable Proxy")
        self.btn_proxy_toggle.setCheckable(True)
        self.btn_proxy_toggle.setEnabled(False)
        self.btn_proxy_toggle.setStyleSheet(self._btn_proxy_off_style())
        self.btn_proxy_toggle.setToolTip(
            "Start or stop the aggregating proxy gRPC server (requires gNMI simulation to be running)")
        self.btn_proxy_toggle.toggled.connect(self._on_proxy_btn_toggled)
        cfg_layout.addWidget(self.btn_proxy_toggle)

        layout.addWidget(cfg_group)

        # ── Connected Clients group ────────────────────────────────────
        cli_group = QGroupBox("Connected Clients")
        cli_group.setStyleSheet(self._group_style())
        cli_layout = QVBoxLayout(cli_group)
        cli_layout.setContentsMargins(6, 4, 6, 6)
        cli_layout.setSpacing(4)

        _COLS = ["Peer", "Mode", "Target", "Paths", "Pushes", "Uptime"]
        self.clients_table = QTableWidget(0, len(_COLS))
        self.clients_table.setHorizontalHeaderLabels(_COLS)
        self.clients_table.setFont(QFont("Consolas", 8))
        self.clients_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.clients_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.clients_table.setAlternatingRowColors(True)
        self.clients_table.verticalHeader().setVisible(False)
        self.clients_table.setMinimumHeight(120)
        self.clients_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.clients_table.setStyleSheet("""
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
        hdr = self.clients_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Peer
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Mode
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Target
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Paths
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Pushes
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Uptime
        # Initial width = header text width from font metrics
        fm = hdr.fontMetrics()
        cli_layout.addWidget(self.clients_table)

        layout.addWidget(cli_group)

        layout.addStretch()

    # ------------------------------------------------------------------ #
    #  Interface loading                                                   #
    # ------------------------------------------------------------------ #

    def _load_interfaces(self):
        self.btn_refresh_ifaces.setEnabled(False)
        self.btn_refresh_ifaces.setText("…")
        self.iface_combo.setEnabled(False)
        self._iface_thread = QThread()
        self._iface_worker = _IfaceLoader()
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
        self.iface_combo.addItem("Select interface…", None)
        for name, label in ifaces:
            self.iface_combo.addItem(label, name)
        if prev:
            idx = self.iface_combo.findData(prev)
            if idx >= 0:
                self.iface_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def set_generating(self, active: bool, progress: str = ""):
        self.btn_generate.setEnabled(not active)
        if active:
            self.lbl_progress.setText(progress or "Generating…")
            self.lbl_progress.show()
        else:
            self.lbl_progress.hide()

    def set_gnmi_running(self, running: bool):
        self._running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_proxy_toggle.setEnabled(running)
        if not running:
            # Reset proxy button when simulation stops
            self.set_proxy_running(False)

    def set_proxy_running(self, running: bool):
        """Update the proxy toggle button state without emitting the signal."""
        self._proxy_running = running
        self.btn_proxy_toggle.blockSignals(True)
        self.btn_proxy_toggle.setChecked(running)
        self.btn_proxy_toggle.blockSignals(False)
        if running:
            self.btn_proxy_toggle.setText("Disable Proxy")
            self.btn_proxy_toggle.setStyleSheet(self._btn_proxy_on_style())
            self.port_edit.setEnabled(False)
        else:
            self.btn_proxy_toggle.setText("Enable Proxy")
            self.btn_proxy_toggle.setStyleSheet(self._btn_proxy_off_style())
            self.port_edit.setEnabled(True)

    def _on_proxy_btn_toggled(self, checked: bool):
        self.set_proxy_running(checked)
        self.sig_proxy_toggle.emit(checked)

    def set_gnmi_status(self, status: str):
        self.status_badge.set_status(status)

    def set_gnmi_targets(self, switches: int, routers: int):
        self.lbl_switches.setText(f"Switches:  {switches}")
        self.lbl_routers.setText(f"Routers:   {routers}")
        self.lbl_total.setText(f"Total:     {switches + routers}")

    def set_direct_servers(self, count: int):
        pass  # Direct server count no longer displayed

    def set_bound_count(self, count: int):
        if count > 0:
            self.bound_label.setText(f"IPs bound: {count}")
            self.bound_label.setStyleSheet("color: #3fb950;")
        else:
            self.bound_label.setText("IPs bound: 0")
            self.bound_label.setStyleSheet("color: #8b949e;")

    def set_interface_locked(self, locked: bool):
        """Lock interface controls while binding is in progress or server is running."""
        self.iface_combo.setEnabled(not locked)
        self.mask_edit.setEnabled(not locked)
        self.btn_refresh_ifaces.setEnabled(not locked)

    def set_datasets_ready(self, ready: bool):
        """Enable Start button once datasets have been generated."""
        self.btn_start.setEnabled(ready and not self._running)
        self.btn_clear.setEnabled(ready)

    def set_clients(self, clients: list):
        """Refresh the Connected Clients table from a snapshot of active subscribers."""
        self.clients_table.setRowCount(0)
        now = time.time()
        for c in clients:
            duration = int(now - c.get("connected_at", now))
            mins, secs = divmod(duration, 60)
            dur_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
            paths = ", ".join(c.get("paths", ["/"]))

            row = self.clients_table.rowCount()
            self.clients_table.insertRow(row)
            for col, val in enumerate([
                c.get("peer", "?"),
                c.get("mode", "?"),
                c.get("target", "?"),
                paths,
                str(c.get("push_count", 0)),
                dur_str,
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self.clients_table.setItem(row, col, item)
        self.clients_table.resizeRowsToContents()

    @property
    def gnmi_port(self) -> int:
        try:
            return int(self.port_edit.text().strip())
        except ValueError:
            return 50051

    @property
    def selected_interface(self) -> str:
        data = self.iface_combo.currentData()
        return data if data else ""

    @property
    def subnet_mask(self) -> str:
        return self.mask_edit.text().strip() or "255.255.255.0"

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

    def _btn_proxy_off_style(self) -> str:
        return (
            "QPushButton { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #30363d; border-color: #58a6ff; } "
            "QPushButton:pressed { background: #0d1117; } "
            "QPushButton:disabled { color: #6e7681; background: #161b22; border-color: #21262d; }"
        )

    def _btn_proxy_on_style(self) -> str:
        return (
            "QPushButton { background: #9a6700; color: white; "
            "border: none; border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #bb8009; } "
            "QPushButton:pressed { background: #7a5200; } "
            "QPushButton:disabled { background: #30363d; color: #6e7681; }"
        )
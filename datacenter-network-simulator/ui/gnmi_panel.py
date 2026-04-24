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
    QHeaderView, QAbstractItemView, QSizePolicy, QProgressBar,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from ui.snmp_panel import StatusBadge   # reuse the same badge widget

_DEVICE_TYPE_LABELS = [
    ("switch",        "Switches"),
    ("router",        "Routers"),
    ("server",        "Servers"),
    ("firewall",      "Firewalls"),
    ("load_balancer", "Load Balancers"),
]


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
        layout.addWidget(content, stretch=1)
        layout = cl  # redirect

        # ── Status row ─────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Targets group ──────────────────────────────────────────────────
        self.tgt_group = QGroupBox("Active Devices")
        self.tgt_group.setStyleSheet(self._group_style())
        self.tgt_group.hide()
        tgt_layout = QVBoxLayout(self.tgt_group)
        tgt_layout.setContentsMargins(6, 4, 6, 6)
        tgt_layout.setSpacing(3)

        self._device_labels = {}
        for key, name in _DEVICE_TYPE_LABELS:
            lbl = QLabel(f"{name}: 0")
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            lbl.hide()
            tgt_layout.addWidget(lbl)
            self._device_labels[key] = lbl
        self.lbl_total = QLabel("Total: 0")
        self.lbl_total.setFont(QFont("Consolas", 9))
        self.lbl_total.setStyleSheet("color: #8b949e;")
        self.lbl_total.hide()
        tgt_layout.addWidget(self.lbl_total)
        layout.addWidget(self.tgt_group)

        # ── Progress bar ──────────────────────────────────────────────────
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

        # ── Generate button ───────────────────────────────────────────────
        self.btn_generate = QPushButton("Generate Dataset")
        self.btn_generate.setStyleSheet(self._btn_generate_style())
        self.btn_generate.clicked.connect(self.sig_generate.emit)
        layout.addWidget(self.btn_generate)

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
        cli_layout.addWidget(self.clients_table, stretch=1)

        layout.addWidget(cli_group, stretch=1)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def set_generating(self, active: bool, progress: str = ""):
        self.btn_generate.setEnabled(not active and not self._running)
        if active:
            self.progress.setValue(0)
            self.progress.show()
        else:
            self.progress.hide()

    def show_progress(self, value: int, maximum: int = 100):
        self.progress.setMaximum(maximum)
        self.progress.setValue(value)
        self.progress.show()
        if value >= maximum:
            QTimer.singleShot(1500, self.progress.hide)

    def set_gnmi_running(self, running: bool):
        self._running = running
        self.tgt_group.setVisible(running)
        self.btn_generate.setEnabled(not running)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if running:
            self.btn_clear.setEnabled(True)
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

    def set_gnmi_targets(self, counts: dict):
        total = 0
        for key, name in _DEVICE_TYPE_LABELS:
            n = counts.get(key, 0)
            total += n
            lbl = self._device_labels[key]
            if n > 0:
                lbl.setText(f"{name}: {n}")
                lbl.show()
            else:
                lbl.hide()
        if total > 0:
            self.lbl_total.setText(f"Total: {total}")
            self.lbl_total.show()
        else:
            self.lbl_total.hide()

    def set_direct_servers(self, count: int):
        pass  # Direct server count no longer displayed

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
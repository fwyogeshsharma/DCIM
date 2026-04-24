"""
sFlow Simulator Panel — controls for the sFlow v5 agent simulator.

Exports sFlow datagrams (counter + flow samples) to a configurable UDP collector.
Log output is forwarded to ConsolePanel.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QLineEdit, QSpinBox,
    QSizePolicy, QFormLayout,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ui.snmp_panel import StatusBadge


_DEVICE_TYPE_LABELS = [
    ("switch",        "Switches"),
    ("router",        "Routers"),
    ("server",        "Servers"),
    ("firewall",      "Firewalls"),
    ("load_balancer", "Load Balancers"),
]

_BTN_BASE = (
    "QPushButton {"
    "  border-radius: 4px; font-size: 9pt; padding: 4px 12px;"
    "  color: #e6edf3; border: none;"
    "}"
    "QPushButton:disabled { color: #484f58; background: #21262d; }"
)
_BTN_GREEN  = _BTN_BASE + "QPushButton:enabled { background: #238636; } QPushButton:hover:enabled { background: #2ea043; }"
_BTN_RED    = _BTN_BASE + "QPushButton:enabled { background: #b62324; } QPushButton:hover:enabled { background: #da3633; }"


def _group_style() -> str:
    return (
        "QGroupBox { color: #8b949e; font-size: 8pt; border: 1px solid #30363d;"
        " border-radius: 4px; margin-top: 6px; padding-top: 4px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 2px; }"
    )


class SFlowPanel(QWidget):
    sig_start = Signal()
    sig_stop  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    #  Build UI                                                            #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet(
            "background: #21262d; border-bottom: 1px solid #30363d;"
        )
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("sFlow Simulator")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        root.addWidget(title_bar)

        # ── Content ────────────────────────────────────────────────────────
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(8)
        root.addWidget(content, stretch=1)
        layout = cl

        # ── Status row ─────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ── Collector config ───────────────────────────────────────────────
        cfg_group = QGroupBox("Collector")
        cfg_group.setStyleSheet(_group_style())
        cfg_layout = QFormLayout(cfg_group)
        cfg_layout.setContentsMargins(6, 6, 6, 6)
        cfg_layout.setSpacing(6)

        self._ip_edit = QLineEdit("127.0.0.1")
        self._ip_edit.setStyleSheet(
            "QLineEdit { background: #0d1117; color: #e6edf3; border: 1px solid #30363d;"
            " border-radius: 3px; padding: 2px 4px; font-size: 9pt; }"
        )
        self._ip_edit.setPlaceholderText("Collector IP")
        cfg_layout.addRow("Collector IP:", self._ip_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(6343)
        self._port_spin.setStyleSheet(
            "QSpinBox { background: #0d1117; color: #e6edf3; border: 1px solid #30363d;"
            " border-radius: 3px; padding: 2px; font-size: 9pt; }"
        )
        cfg_layout.addRow("UDP Port:", self._port_spin)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(5, 3600)
        self._interval_spin.setValue(30)
        self._interval_spin.setSuffix(" s")
        self._interval_spin.setStyleSheet(self._port_spin.styleSheet())
        cfg_layout.addRow("Interval:", self._interval_spin)

        self._rate_spin = QSpinBox()
        self._rate_spin.setRange(1, 65535)
        self._rate_spin.setValue(1000)
        self._rate_spin.setPrefix("1:")
        self._rate_spin.setStyleSheet(self._port_spin.styleSheet())
        cfg_layout.addRow("Sample Rate:", self._rate_spin)

        layout.addWidget(cfg_group)

        # ── Active devices ─────────────────────────────────────────────────
        self._dev_group = QGroupBox("Active Devices")
        self._dev_group.setStyleSheet(_group_style())
        self._dev_group.hide()
        dev_layout = QVBoxLayout(self._dev_group)
        dev_layout.setContentsMargins(6, 4, 6, 6)
        dev_layout.setSpacing(3)

        self._device_labels: dict = {}
        for key, name in _DEVICE_TYPE_LABELS:
            lbl = QLabel(f"{name}: 0")
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            lbl.hide()
            dev_layout.addWidget(lbl)
            self._device_labels[key] = lbl

        self._lbl_total = QLabel("Total: 0")
        self._lbl_total.setFont(QFont("Consolas", 9))
        self._lbl_total.setStyleSheet("color: #8b949e;")
        dev_layout.addWidget(self._lbl_total)
        layout.addWidget(self._dev_group)

        # ── Collector info (visible when running) ──────────────────────────
        self._lbl_collector = QLabel()
        self._lbl_collector.setFont(QFont("Consolas", 8))
        self._lbl_collector.setStyleSheet("color: #8b949e;")
        self._lbl_collector.setWordWrap(True)
        self._lbl_collector.hide()
        layout.addWidget(self._lbl_collector)

        layout.addStretch()

        # ── Buttons ────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start sFlow")
        self.btn_start.setStyleSheet(_BTN_GREEN)
        self.btn_start.clicked.connect(self._on_start)
        btn_row.addWidget(self.btn_start)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet(_BTN_RED)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self.btn_stop)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    #  Slots                                                               #
    # ------------------------------------------------------------------ #

    def _on_start(self):
        if not self._running:
            self.sig_start.emit()

    def _on_stop(self):
        if self._running:
            self.sig_stop.emit()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def get_config(self) -> dict:
        return {
            "collector_ip":   self._ip_edit.text().strip() or "127.0.0.1",
            "collector_port": self._port_spin.value(),
            "interval":       self._interval_spin.value(),
            "sample_rate":    self._rate_spin.value(),
        }

    def set_status(self, status: str):
        self.status_badge.set_status(status)

    def set_running(self, running: bool):
        self._running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        # Lock config while running
        for w in (self._ip_edit, self._port_spin, self._interval_spin, self._rate_spin):
            w.setEnabled(not running)

    def set_device_counts(self, counts: dict):
        """counts: {device_type_str: int}"""
        total = 0
        for key, lbl in self._device_labels.items():
            n = counts.get(key, 0)
            type_name = next(name for k, name in _DEVICE_TYPE_LABELS if k == key)
            lbl.setText(f"{type_name}: {n}")
            lbl.setVisible(n > 0)
            total += n
        self._lbl_total.setText(f"Total: {total}")
        if total > 0:
            self._dev_group.show()

    def set_collector_info(self, collector: str):
        self._lbl_collector.setText(f"Exporting to {collector}")
        self._lbl_collector.show()
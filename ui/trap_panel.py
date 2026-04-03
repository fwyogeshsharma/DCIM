"""
Trap Panel — dock widget that configures the trap receiver and logs sent traps.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QLineEdit, QSpinBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from core.trap_definitions import TRAP_DEFINITIONS, SEVERITY_COLOR
from core.trap_engine import TrapEvent


class TrapPanel(QWidget):
    """
    Signals
    -------
    sig_apply(ip: str, port: int)   — user clicked Apply
    sig_simulate(bool)              — Simulate button toggled on/off
    """
    sig_apply    = Signal(str, int)
    sig_simulate = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._severity_counts: dict[str, int] = {s: 0 for s in SEVERITY_COLOR}
        self._build_ui()

    # ── Construction ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet(
            "background: #21262d; border-bottom: 1px solid #30363d;"
        )
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("SNMP Traps")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        root.addWidget(title_bar)

        # ── Content ───────────────────────────────────────────────────────
        content = QWidget()
        inner = QVBoxLayout(content)
        inner.setSpacing(6)
        inner.setContentsMargins(4, 4, 4, 4)
        root.addWidget(content)
        root = inner   # redirect remaining additions to content

        # ── Receiver config group ─────────────────────────────────────────
        cfg = QGroupBox("Trap Receiver")
        cfg_layout = QVBoxLayout(cfg)
        cfg_layout.setSpacing(4)
        cfg_layout.setContentsMargins(6, 4, 6, 6)

        # Row 1 — IP / Port / Apply
        addr_row = QHBoxLayout()
        addr_row.setSpacing(4)
        addr_row.addWidget(QLabel("IP:"))
        self._ip_edit = QLineEdit("127.0.0.1")
        self._ip_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        addr_row.addWidget(self._ip_edit, stretch=1)
        addr_row.addWidget(QLabel("Port:"))
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(162)
        self._port_spin.setFixedWidth(60)
        addr_row.addWidget(self._port_spin)
        apply_btn = QPushButton("Apply")
        apply_btn.setFixedWidth(50)
        apply_btn.clicked.connect(self._on_apply)
        addr_row.addWidget(apply_btn)
        cfg_layout.addLayout(addr_row)

        # Row 2 — Simulate toggle (full width)
        self._sim_btn = QPushButton("▶  Simulate")
        self._sim_btn.setCheckable(True)
        self._sim_btn.toggled.connect(self._on_simulate_toggled)
        cfg_layout.addWidget(self._sim_btn)

        root.addWidget(cfg)

        # ── Severity counters ─────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(3)
        self._count_labels: dict[str, QLabel] = {}
        for sev, color in SEVERITY_COLOR.items():
            badge = QLabel("0")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedWidth(32)
            badge.setStyleSheet(
                f"background:{color}; color:white; border-radius:3px;"
                f" padding:1px 3px; font-weight:bold; font-size:10px;"
            )
            badge.setToolTip(sev.capitalize())
            self._count_labels[sev] = badge
            stats_row.addWidget(badge)
        stats_row.addStretch()

        clr_btn = QPushButton("Clear")
        clr_btn.setFixedWidth(46)
        clr_btn.clicked.connect(self.clear)
        stats_row.addWidget(clr_btn)
        root.addLayout(stats_row)

        # ── Trap log table ────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Time", "Device", "IP", "Trap Type", "Details"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setFont(QFont("Consolas", 8))
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._table)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_event(self, event: TrapEvent):
        defn = event.defn
        row  = self._table.rowCount()
        self._table.insertRow(row)

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
            self._table.setItem(row, col, item)

        self._table.scrollToBottom()

        self._severity_counts[defn.severity] = (
            self._severity_counts.get(defn.severity, 0) + 1
        )
        self._count_labels[defn.severity].setText(
            str(self._severity_counts[defn.severity])
        )

    def add_error(self, msg: str):
        row = self._table.rowCount()
        self._table.insertRow(row)
        item = QTableWidgetItem(msg)
        item.setForeground(QColor("#e74c3c"))
        self._table.setItem(row, 4, item)
        self._table.scrollToBottom()

    def clear(self):
        self._table.setRowCount(0)
        self._severity_counts = {s: 0 for s in SEVERITY_COLOR}
        for lbl in self._count_labels.values():
            lbl.setText("0")

    def set_simulating(self, active: bool):
        self._sim_btn.blockSignals(True)
        self._sim_btn.setChecked(active)
        self._sim_btn.setText("⏹  Stop" if active else "▶  Simulate")
        self._sim_btn.blockSignals(False)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_apply(self):
        self.sig_apply.emit(self._ip_edit.text().strip(), self._port_spin.value())

    def _on_simulate_toggled(self, checked: bool):
        self._sim_btn.setText("⏹  Stop" if checked else "▶  Simulate")
        self.sig_simulate.emit(checked)
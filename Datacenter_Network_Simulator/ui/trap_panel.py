"""
SNMP Trap Panel — receiver configuration, Rule Engine toggle, and trap event log.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QLineEdit, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from core.trap_definitions import SEVERITY_COLOR
from core.trap_engine import TrapEvent


class TrapPanel(QWidget):
    sig_trap_apply  = Signal(str, int)   # (ip, port)
    sig_rule_engine = Signal(bool)       # enable/disable rule engine

    def __init__(self, parent=None):
        super().__init__(parent)
        self._severity_counts: dict[str, int] = {s: 0 for s in SEVERITY_COLOR}
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
        title_lbl = QLabel("SNMP Traps")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        root.addWidget(title_bar)

        # ── Content ───────────────────────────────────────────────────────
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        root.addWidget(content, stretch=1)

        # ── Receiver config ────────────────────────────────────────────────
        recv_group = QGroupBox("Receiver")
        recv_group.setStyleSheet(self._group_style())
        recv_layout = QVBoxLayout(recv_group)
        recv_layout.setContentsMargins(6, 4, 6, 6)
        recv_layout.setSpacing(4)

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
        apply_btn = QPushButton("Apply")
        apply_btn.setFixedWidth(48)
        apply_btn.setStyleSheet(self._btn_secondary_style())
        apply_btn.clicked.connect(self._on_trap_apply)
        recv_row.addWidget(apply_btn)
        recv_layout.addLayout(recv_row)
        layout.addWidget(recv_group)

        # ── Rule Engine toggle ─────────────────────────────────────────────
        self._rule_engine_btn = QPushButton("⚙  Trap Simulation  OFF")
        self._rule_engine_btn.setCheckable(True)
        self._rule_engine_btn.setEnabled(False)
        self._rule_engine_btn.setStyleSheet(self._btn_secondary_style())
        self._rule_engine_btn.toggled.connect(self._on_rule_engine_toggled)
        self._rule_engine_btn.setToolTip(
            "Enable rule-driven trap generation based on simulated device metrics\n"
            "(available only while the SNMP simulator is running)"
        )
        layout.addWidget(self._rule_engine_btn)

        # ── Severity counter badges + clear ────────────────────────────────
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
        clr_btn = QPushButton("Clear")
        clr_btn.setFixedWidth(46)
        clr_btn.setStyleSheet(self._btn_secondary_style())
        clr_btn.clicked.connect(self.clear_traps)
        sev_row.addWidget(clr_btn)
        layout.addLayout(sev_row)

        # ── Trap log table ─────────────────────────────────────────────────
        self._trap_table = QTableWidget(0, 5)
        self._trap_table.setHorizontalHeaderLabels(
            ["Time", "Device", "IP", "Trap Type", "Details"]
        )
        hdr = self._trap_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        self._trap_table.setColumnWidth(0, 68)
        self._trap_table.setColumnWidth(1, 120)
        self._trap_table.setColumnWidth(2, 100)
        self._trap_table.setColumnWidth(3, 120)
        self._trap_table.setColumnWidth(4, 200)
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
        layout.addWidget(self._trap_table, stretch=1)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
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
            # Agent that fired the trap: server OS → prod IP, BMC → mgmt IP,
            # NOS/UPS/PDU/sensor → mgmt IP.
            getattr(event, "source_ip", "") or event.device.ip_address,
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

    def set_rule_engine_active(self, active: bool):
        self._rule_engine_btn.blockSignals(True)
        self._rule_engine_btn.setChecked(active)
        if active:
            self._rule_engine_btn.setText("⚙  Trap Simulation  ON")
            self._rule_engine_btn.setStyleSheet(
                "QPushButton { background:#1f6feb; color:white; border:none; "
                "border-radius:6px; padding:8px; font-weight:bold; } "
                "QPushButton:hover { background:#388bfd; } "
                "QPushButton:checked { background:#1158c7; }"
            )
        else:
            self._rule_engine_btn.setText("⚙  Trap Simulation  OFF")
            self._rule_engine_btn.setStyleSheet(self._btn_secondary_style())
        self._rule_engine_btn.blockSignals(False)

    def set_rule_engine_available(self, available: bool):
        self._rule_engine_btn.setEnabled(available)
        if not available:
            self.set_rule_engine_active(False)

    # ------------------------------------------------------------------ #
    #  Private slots                                                       #
    # ------------------------------------------------------------------ #

    def _on_trap_apply(self):
        self.sig_trap_apply.emit(self._trap_ip.text().strip(), self._trap_port.value())

    def _on_rule_engine_toggled(self, checked: bool):
        if checked:
            self._rule_engine_btn.setText("⚙  Trap Simulation  ON")
            self._rule_engine_btn.setStyleSheet(
                "QPushButton { background:#1f6feb; color:white; border:none; "
                "border-radius:6px; padding:8px; font-weight:bold; } "
                "QPushButton:hover { background:#388bfd; } "
                "QPushButton:checked { background:#1158c7; }"
            )
        else:
            self._rule_engine_btn.setText("⚙  Trap Simulation  OFF")
            self._rule_engine_btn.setStyleSheet(self._btn_secondary_style())
        self.sig_rule_engine.emit(checked)

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

    def _lineedit_style(self) -> str:
        return (
            "QLineEdit { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 4px; padding: 3px 6px; }"
            "QLineEdit:focus { border-color: #58a6ff; }"
        )

    def _btn_secondary_style(self) -> str:
        return (
            "QPushButton { background: #21262d; color: #e6edf3; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 8px; } "
            "QPushButton:hover { background: #30363d; } "
            "QPushButton:pressed { background: #0d1117; } "
            "QPushButton:disabled { color: #6e7681; }"
        )
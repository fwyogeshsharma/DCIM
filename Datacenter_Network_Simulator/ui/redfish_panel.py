"""
Redfish Simulator Panel — controls for the per-server Redfish/BMC simulator.

Serves a DMTF Redfish tree (ServiceRoot / Systems / Chassis / Managers) over
plain HTTP on every server's IP. Log output is forwarded to ConsolePanel.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QLineEdit, QSpinBox, QFormLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QPen, QColor, QAction

from ui.snmp_panel import StatusBadge


def _eye_icon(open_eye: bool, color: str = "#8b949e") -> QIcon:
    """Draw a small eye icon (open or struck-through) for the password toggle."""
    px = QPixmap(16, 16)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    # almond outline
    p.drawArc(QRectF(1.5, 3.5, 13, 9), 20 * 16, 140 * 16)
    p.drawArc(QRectF(1.5, 3.5, 13, 9), 200 * 16, 140 * 16)
    # pupil
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(6.0, 5.6, 4.0, 4.0))
    if not open_eye:
        p.setBrush(Qt.NoBrush)
        p.drawLine(3, 13, 13, 3)   # strike-through = hidden
    p.end()
    return QIcon(px)

_BTN_BASE = (
    "QPushButton {"
    "  border-radius: 4px; font-size: 9pt; padding: 4px 12px;"
    "  color: #e6edf3; border: none;"
    "}"
    "QPushButton:disabled { color: #484f58; background: #21262d; }"
)
_BTN_GREEN = _BTN_BASE + "QPushButton:enabled { background: #238636; } QPushButton:hover:enabled { background: #2ea043; }"
_BTN_RED   = _BTN_BASE + "QPushButton:enabled { background: #b62324; } QPushButton:hover:enabled { background: #da3633; }"


def _group_style() -> str:
    return (
        "QGroupBox { color: #8b949e; font-size: 8pt; border: 1px solid #30363d;"
        " border-radius: 4px; margin-top: 6px; padding-top: 4px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 2px; }"
    )


def _field_style() -> str:
    return ("background: #0d1117; color: #e6edf3; border: 1px solid #30363d;"
            " border-radius: 3px; padding: 2px 4px; font-size: 9pt;")


class RedfishPanel(QWidget):
    sig_start = Signal()
    sig_stop  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet("background: #21262d; border-bottom: 1px solid #30363d;")
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("Redfish Simulator")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        root.addWidget(title_bar)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        root.addWidget(content, stretch=1)

        # Status
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Config
        cfg_group = QGroupBox("BMC Service")
        cfg_group.setStyleSheet(_group_style())
        cfg = QFormLayout(cfg_group)
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setSpacing(6)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(443)
        self._port_spin.setStyleSheet("QSpinBox {" + _field_style() + "}")
        cfg.addRow("HTTP Port:", self._port_spin)

        self._user_edit = QLineEdit("admin")
        self._user_edit.setStyleSheet("QLineEdit {" + _field_style() + "}")
        cfg.addRow("Username:", self._user_edit)

        self._pass_edit = QLineEdit("password")
        self._pass_edit.setStyleSheet("QLineEdit {" + _field_style() + "}")
        self._pass_edit.setEchoMode(QLineEdit.Password)
        self._eye_open   = _eye_icon(True)
        self._eye_closed = _eye_icon(False)
        self._pass_action = self._pass_edit.addAction(
            self._eye_closed, QLineEdit.TrailingPosition)
        self._pass_action.setToolTip("Show password")
        self._pass_action.triggered.connect(self._toggle_password)
        cfg.addRow("Password:", self._pass_edit)
        layout.addWidget(cfg_group)

        # Device table
        self._dev_group = QGroupBox("Active BMCs")
        self._dev_group.setStyleSheet(_group_style())
        self._dev_group.hide()
        dl = QVBoxLayout(self._dev_group)
        dl.setContentsMargins(6, 4, 6, 6)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Server", "Vendor", "URL"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setStyleSheet(
            "QTableWidget { background: #0d1117; color: #e6edf3; border: none;"
            " font-size: 8pt; gridline-color: #21262d; }"
            "QHeaderView::section { background: #161b22; color: #8b949e;"
            " border: none; padding: 2px; font-size: 8pt; }")
        dl.addWidget(self._table)
        layout.addWidget(self._dev_group)

        layout.addStretch()

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Redfish")
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
    def _on_start(self):
        if not self._running:
            self.sig_start.emit()

    def _on_stop(self):
        if self._running:
            self.sig_stop.emit()

    def _toggle_password(self):
        show = self._pass_edit.echoMode() == QLineEdit.Password
        self._pass_edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self._pass_action.setIcon(self._eye_open if show else self._eye_closed)
        self._pass_action.setToolTip("Hide password" if show else "Show password")

    # ------------------------------------------------------------------ #
    def get_config(self) -> dict:
        txt = self._port_spin.lineEdit().text().strip()
        try:
            port = max(1, min(65535, int(txt)))
        except ValueError:
            port = self._port_spin.value()
        return {
            "port": port,
            "username": self._user_edit.text().strip() or "admin",
            "password": self._pass_edit.text() or "password",
        }

    def set_status(self, status: str):
        self.status_badge.set_status(status)

    def set_running(self, running: bool):
        self._running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        for w in (self._port_spin, self._user_edit, self._pass_edit):
            w.setEnabled(not running)

    def refresh_device_table(self, rows: list[dict]):
        """rows: [{name, vendor, url, ...}] from RedfishController.get_device_summary()."""
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(row.get("name", "")))
            self._table.setItem(r, 1, QTableWidgetItem(row.get("vendor", "")))
            self._table.setItem(r, 2, QTableWidgetItem(row.get("url", "")))
        self._dev_group.setVisible(bool(rows))

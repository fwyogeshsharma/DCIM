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
_BTN_GREY  = _BTN_BASE + "QPushButton:enabled { background: #30363d; } QPushButton:hover:enabled { background: #3c444d; }"


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
    sig_start    = Signal()
    sig_stop     = Signal()
    sig_action   = Signal(str, str)   # (ip, action)
    sig_view_log = Signal(str)        # (ip)

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
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Server", "Power", "LED", "IP"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setStyleSheet(
            "QTableWidget { background: #0d1117; color: #e6edf3; border: none;"
            " font-size: 8pt; gridline-color: #21262d; }"
            "QTableWidget::item:selected { background: #1f6feb; }"
            "QHeaderView::section { background: #161b22; color: #8b949e;"
            " border: none; padding: 2px; font-size: 8pt; }")
        dl.addWidget(self._table)
        layout.addWidget(self._dev_group)

        # Server Operations (act on the selected row)
        self._ops_group = QGroupBox("Server Operations")
        self._ops_group.setStyleSheet(_group_style())
        self._ops_group.hide()
        og = QVBoxLayout(self._ops_group)
        og.setContentsMargins(6, 4, 6, 6)
        og.setSpacing(3)
        self._sel_label = QLabel("Select a server above")
        self._sel_label.setStyleSheet("color: #8b949e; font-size: 8pt;")
        og.addWidget(self._sel_label)

        def _op_btn(text, action, danger=False):
            b = QPushButton(text)
            b.setStyleSheet(_BTN_RED if danger else _BTN_GREY)
            b.clicked.connect(lambda: self._emit_action(action))
            return b

        row1 = QHBoxLayout(); row1.setSpacing(3)
        row1.addWidget(_op_btn("Power On", "power_on"))
        row1.addWidget(_op_btn("Power Off", "power_off"))
        row1.addWidget(_op_btn("Reboot", "reboot"))
        og.addLayout(row1)
        row2 = QHBoxLayout(); row2.setSpacing(3)
        row2.addWidget(_op_btn("Power Cycle", "power_cycle"))
        row2.addWidget(_op_btn("LED On", "led_on"))
        row2.addWidget(_op_btn("LED Off", "led_off"))
        og.addLayout(row2)
        row3 = QHBoxLayout(); row3.setSpacing(3)
        row3.addWidget(_op_btn("Refresh", "refresh"))
        _view = QPushButton("View Log"); _view.setStyleSheet(_BTN_GREY)
        _view.clicked.connect(self._emit_view_log)
        row3.addWidget(_view)
        row3.addWidget(_op_btn("Clear Log", "clear_log", danger=True))
        og.addLayout(row3)
        layout.addWidget(self._ops_group)

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
        self._ops_group.setVisible(running)

    def refresh_device_table(self, rows: list[dict]):
        """rows from RedfishController.get_device_summary() — includes power_state,
        indicator_led, sel_count. The selected row's IP drives Server Operations."""
        sel_ip = self._selected_ip()
        self._table.setRowCount(len(rows))
        sel_row = -1
        for r, row in enumerate(rows):
            ip = row.get("ip", "")
            name_item = QTableWidgetItem(row.get("name", ""))
            name_item.setData(Qt.UserRole, ip)
            self._table.setItem(r, 0, name_item)
            ps = row.get("power_state", "On")
            ps_item = QTableWidgetItem(ps)
            ps_item.setForeground(QColor("#3fb950") if ps == "On" else QColor("#d29922"))
            self._table.setItem(r, 1, ps_item)
            led = row.get("indicator_led", "Off")
            led_item = QTableWidgetItem("ON" if led != "Off" else "off")
            led_item.setForeground(QColor("#e3b341") if led != "Off" else QColor("#6e7681"))
            self._table.setItem(r, 2, led_item)
            self._table.setItem(r, 3, QTableWidgetItem(ip))
            if ip and ip == sel_ip:
                sel_row = r
        self._dev_group.setVisible(bool(rows))
        self._ops_group.setVisible(self._running and bool(rows))
        if sel_row >= 0:
            self._table.selectRow(sel_row)

    # ── Server Operations ────────────────────────────────────────────────
    def _selected_ip(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return item.data(Qt.UserRole) if item else ""

    def _emit_action(self, action: str):
        ip = self._selected_ip()
        if not ip:
            self._sel_label.setText("Select a server above first")
            return
        self.sig_action.emit(ip, action)

    def _emit_view_log(self):
        ip = self._selected_ip()
        if not ip:
            self._sel_label.setText("Select a server above first")
            return
        self.sig_view_log.emit(ip)

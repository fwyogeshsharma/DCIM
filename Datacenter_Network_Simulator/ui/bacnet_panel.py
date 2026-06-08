"""
BACnet/IP Simulator Panel — controls for Verdigris EV2 BACnet devices.

Mirrors GNMIPanel / SFlowPanel interface.
Log output forwarded to ConsolePanel (BACnet/IP tab).
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QSpinBox, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ui.snmp_panel import StatusBadge   # reuse the same badge widget


_FREQ_OPTIONS    = [
    ("50 Hz (EU/Asia)", 50.0),
    ("60 Hz (US/CA)",   60.0),
]


class BACnetPanel(QWidget):
    sig_start          = Signal()   # user pressed Start
    sig_stop           = Signal()   # user pressed Stop

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._build_ui()

    # ─────────────────────────────────────────────────────────────
    #  Build UI
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet(
            "background: #21262d; border-bottom: 1px solid #30363d;"
        )
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("BACnet/IP Simulator")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        self._badge = StatusBadge()
        tb_row.addWidget(self._badge)
        layout.addWidget(title_bar)

        # ── Content wrapper — gives left/right/bottom padding ─────
        content_w = QWidget()
        c_lay = QVBoxLayout(content_w)
        c_lay.setContentsMargins(8, 8, 8, 8)
        c_lay.setSpacing(8)

        # ── Config group ──────────────────────────────────────────
        cfg = QGroupBox("Configuration")
        cfg.setStyleSheet(_group_style())
        cfg_lay = QVBoxLayout(cfg)
        cfg_lay.setContentsMargins(8, 8, 8, 8)
        cfg_lay.setSpacing(6)

        # Base instance
        row1 = QHBoxLayout()
        lbl1 = QLabel("Base Device Instance:")
        lbl1.setStyleSheet("color:#8b949e; font-size:9pt;")
        self._instance_spin = QSpinBox()
        self._instance_spin.setRange(1, 4194302)
        self._instance_spin.setValue(40001)
        self._instance_spin.setToolTip(
            "First BACnet device instance number.\n"
            "Subsequent devices increment by 1."
        )
        self._instance_spin.setStyleSheet(_spin_style())
        row1.addWidget(lbl1)
        row1.addStretch()
        row1.addWidget(self._instance_spin)
        cfg_lay.addLayout(row1)

        # Mains frequency
        row3 = QHBoxLayout()
        lbl3 = QLabel("Mains Frequency:")
        lbl3.setStyleSheet("color:#8b949e; font-size:9pt;")
        self._freq_combo = QComboBox()
        for label, val in _FREQ_OPTIONS:
            self._freq_combo.addItem(label, val)
        self._freq_combo.setStyleSheet(_combo_style())
        row3.addWidget(lbl3)
        row3.addStretch()
        row3.addWidget(self._freq_combo)
        cfg_lay.addLayout(row3)

        # UDP port (editable)
        row4 = QHBoxLayout()
        lbl4 = QLabel("UDP Port:")
        lbl4.setStyleSheet("color:#8b949e; font-size:9pt;")
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(47808)
        self._port_spin.setToolTip(
            "BACnet/IP UDP port.\n"
            "Standard is 47808 (0xBAC0).\n"
            "Change if another service already holds that port."
        )
        self._port_spin.setStyleSheet(_spin_style())
        row4.addWidget(lbl4)
        row4.addStretch()
        row4.addWidget(self._port_spin)
        cfg_lay.addLayout(row4)

        c_lay.addWidget(cfg)

        # ── Start / Stop buttons ──────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        self._btn_start = QPushButton("▶  Start BACnet")
        self._btn_stop  = QPushButton("■  Stop")
        self._btn_stop.setEnabled(False)
        for btn in (self._btn_start, self._btn_stop):
            btn.setFixedHeight(28)
            btn.setStyleSheet(_btn_style())
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        c_lay.addLayout(btn_row)

        # ── Device status table ───────────────────────────────────
        tbl_grp = QGroupBox("Active Devices")
        tbl_grp.setStyleSheet(_group_style())
        tbl_lay = QVBoxLayout(tbl_grp)
        tbl_lay.setContentsMargins(6, 4, 6, 6)
        tbl_lay.setSpacing(4)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["IP", "Instance", "Circuits", "Status"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(_table_style())
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(120)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        tbl_lay.addWidget(self._table, stretch=1)
        c_lay.addWidget(tbl_grp, stretch=1)

        layout.addWidget(content_w)

    # ─────────────────────────────────────────────────────────────
    #  Public API (called by MainWindow)
    # ─────────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        """Return current configuration for BACnetController.start()."""
        return {
            "base_instance": self._instance_spin.value(),
            "frequency_hz":  self._freq_combo.currentData(),
            "port":          self._port_spin.value(),
        }

    def set_running(self, running: bool):
        self._running = running
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._instance_spin.setEnabled(not running)
        self._freq_combo.setEnabled(not running)
        self._port_spin.setEnabled(not running)
        if running:
            self._badge.set_status("Running")
        else:
            self._badge.set_status("Stopped")

    def refresh_device_table(self, devices: list):
        """
        Populate the active-devices table.

        *devices*: list of dicts with keys: ip, instance, circuits, status
        """
        self._table.setRowCount(len(devices))
        for row, d in enumerate(devices):
            self._table.setItem(row, 0, QTableWidgetItem(d.get("ip", "")))
            self._table.setItem(row, 1, QTableWidgetItem(str(d.get("instance", ""))))
            self._table.setItem(row, 2, QTableWidgetItem(str(d.get("circuits", ""))))
            status_item = QTableWidgetItem(d.get("status", ""))
            status_item.setForeground(
                Qt.green if d.get("status") == "Active" else Qt.gray
            )
            self._table.setItem(row, 3, status_item)

    def set_enabled(self, enabled: bool):
        """Disable all controls while IPs are being bound."""
        self._btn_start.setEnabled(enabled and not self._running)
        self._instance_spin.setEnabled(enabled and not self._running)
        self._freq_combo.setEnabled(enabled and not self._running)
        self._port_spin.setEnabled(enabled and not self._running)

    # ─────────────────────────────────────────────────────────────
    #  Button slots
    # ─────────────────────────────────────────────────────────────

    def _on_start(self):
        self.sig_start.emit()

    def _on_stop(self):
        self.sig_stop.emit()


# ─────────────────────────────────────────────────────────────────
#  Stylesheet helpers
# ─────────────────────────────────────────────────────────────────

def _group_style() -> str:
    return """
        QGroupBox {
            color: #8b949e;
            border: 1px solid #30363d;
            border-radius: 4px;
            margin-top: 6px;
            font-size: 9pt;
            padding: 4px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 3px;
        }
    """


def _btn_style() -> str:
    return """
        QPushButton {
            background: #21262d;
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 4px;
            font-size: 9pt;
            padding: 0 8px;
        }
        QPushButton:hover  { background: #2d333b; }
        QPushButton:pressed{ background: #1c2128; }
        QPushButton:disabled{ color: #484f58; border-color: #21262d; }
    """


def _spin_style() -> str:
    return """
        QSpinBox {
            background: #0d1117;
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 3px;
            padding: 2px 4px;
            font-size: 9pt;
            min-width: 70px;
        }
        QSpinBox:disabled { color: #484f58; }
    """


def _combo_style() -> str:
    return """
        QComboBox {
            background: #0d1117;
            color: #e6edf3;
            border: 1px solid #30363d;
            border-radius: 3px;
            padding: 2px 4px;
            font-size: 9pt;
            min-width: 80px;
        }
        QComboBox:disabled { color: #484f58; }
        QComboBox QAbstractItemView {
            background: #161b22;
            color: #e6edf3;
            selection-background-color: #1f6feb;
        }
    """


def _table_style() -> str:
    return """
        QTableWidget {
            background: #161b22;
            color: #e6edf3;
            border: 1px solid #30363d;
            alternate-background-color: #0d1117;
            gridline-color: #30363d;
            font-size: 8pt;
        }
        QHeaderView::section {
            background: #21262d;
            color: #8b949e;
            padding: 2px;
            border: none;
            border-bottom: 1px solid #30363d;
            font-size: 8pt;
        }
        QTableWidget::item:selected { background: #1f6feb; }
    """

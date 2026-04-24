"""
SNMP Topology Discovery Dialog.
Walks LLDP-MIB on every simulated device and shows how the discovered
adjacencies compare with the configured topology.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QWidget, QTextEdit, QGroupBox, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QColor, QFont

from core.discovery_engine import DiscoveryEngine, DiscoveryResult


# ------------------------------------------------------------------ #
#  Background worker                                                   #
# ------------------------------------------------------------------ #

class _DiscoveryWorker(QObject):
    progress = Signal(int, int, str)   # current, total, message
    finished = Signal(object)          # DiscoveryResult
    error    = Signal(str)

    def __init__(self, topology, host: str, port: int):
        super().__init__()
        self.topology = topology
        self.host = host
        self.port = port

    def run(self):
        try:
            engine = DiscoveryEngine(self.host, self.port)
            result = engine.discover(self.topology, self.progress.emit)
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ------------------------------------------------------------------ #
#  Dialog                                                              #
# ------------------------------------------------------------------ #

_STYLE_BASE = "background:#0d1117; color:#e6edf3;"
_STYLE_BTN_PRIMARY = (
    "QPushButton{background:#238636;color:white;border-radius:6px;padding:8px 20px;font-weight:bold;}"
    "QPushButton:hover{background:#2ea043;}"
    "QPushButton:disabled{background:#21262d;color:#6e7681;}"
)
_STYLE_BTN_SECONDARY = (
    "QPushButton{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:8px 20px;}"
    "QPushButton:hover{background:#30363d;}"
)
_GREEN  = QColor("#3fb950")
_ORANGE = QColor("#d29922")
_RED    = QColor("#f85149")
_GRAY   = QColor("#8b949e")


class DiscoveryDialog(QDialog):
    def __init__(self, topology, snmpsim_running: bool = False,
                 host: str = "127.0.0.1", port: int = 161, parent=None):
        super().__init__(parent)
        self.topology = topology
        self.snmpsim_running = snmpsim_running
        self.host = host
        self.port = port
        self._thread: QThread | None = None
        self._worker: _DiscoveryWorker | None = None
        self._setup_ui()
        self._apply_theme()

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _setup_ui(self):
        self.setWindowTitle("SNMP Topology Discovery")
        self.setMinimumSize(860, 580)
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Description
        desc = QLabel(
            "Scans every simulated device via SNMP LLDP-MIB walk and compares the "
            "discovered adjacencies against the configured topology.\n"
            "SNMPSim must be running for the scan to work."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#8b949e; padding:4px 0;")
        root.addWidget(desc)

        # Progress group
        pg_group = QGroupBox("Scan Progress")
        pg_layout = QVBoxLayout(pg_group)
        self.status_label = QLabel("Ready — click Start Discovery to begin.")
        self.status_label.setStyleSheet("color:#e6edf3;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        pg_layout.addWidget(self.status_label)
        pg_layout.addWidget(self.progress_bar)
        root.addWidget(pg_group)

        # Tabs
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        # Tab 1 — Summary
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Courier New", 9))
        self.summary_text.setStyleSheet(_STYLE_BASE)
        self.tabs.addTab(self.summary_text, "Summary")

        # Tab 2 — Discovered Links
        self.links_table = QTableWidget(0, 5)
        self.links_table.setHorizontalHeaderLabels([
            "Local Device", "Local Port", "Remote Device", "Remote Port", "Status",
        ])
        self.links_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.links_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.links_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.links_table.setAlternatingRowColors(True)
        self.links_table.setStyleSheet(
            "QTableWidget{background:#0d1117;color:#e6edf3;gridline-color:#21262d;}"
            "QTableWidget::item:alternate{background:#161b22;}"
            "QHeaderView::section{background:#161b22;color:#8b949e;border:none;padding:4px;}"
        )
        self.tabs.addTab(self.links_table, "Discovered Links")

        # Tab 3 — Errors
        self.errors_text = QTextEdit()
        self.errors_text.setReadOnly(True)
        self.errors_text.setFont(QFont("Courier New", 9))
        self.errors_text.setStyleSheet("background:#0d1117; color:#f85149;")
        self.tabs.addTab(self.errors_text, "Errors (0)")

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Discovery")
        self.btn_start.setStyleSheet(_STYLE_BTN_PRIMARY)
        if not self.snmpsim_running:
            self.btn_start.setEnabled(False)
            self.btn_start.setToolTip("Start SNMPSim first")
        self.btn_start.clicked.connect(self._start)

        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet(_STYLE_BTN_SECONDARY)
        self.btn_close.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_start)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_close)
        root.addLayout(btn_row)

    def _apply_theme(self):
        self.setStyleSheet(
            "QDialog{background:#161b22;}"
            "QGroupBox{color:#8b949e;border:1px solid #30363d;border-radius:6px;margin-top:8px;padding:8px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:8px;}"
            "QTabWidget::pane{border:1px solid #30363d;border-radius:4px;}"
            "QTabBar::tab{background:#21262d;color:#8b949e;padding:6px 14px;margin-right:2px;border-radius:4px 4px 0 0;}"
            "QTabBar::tab:selected{background:#161b22;color:#e6edf3;}"
            "QProgressBar{border:1px solid #30363d;border-radius:4px;background:#21262d;color:#e6edf3;text-align:center;}"
            "QProgressBar::chunk{background:#238636;border-radius:4px;}"
        )

    # ------------------------------------------------------------------ #
    #  Discovery                                                           #
    # ------------------------------------------------------------------ #

    def _start(self):
        self.btn_start.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting…")
        self.links_table.setRowCount(0)
        self.errors_text.clear()
        self.summary_text.clear()
        self.tabs.setTabText(2, "Errors (0)")

        self._thread = QThread()
        self._worker = _DiscoveryWorker(self.topology, self.host, self.port)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(lambda: self.btn_start.setEnabled(True))
        self._thread.start()

    def _on_progress(self, current: int, total: int, msg: str):
        pct = int(current * 100 / total) if total else 0
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_finished(self, result: DiscoveryResult):
        self.progress_bar.setValue(100)
        self.status_label.setText(
            f"Done — scanned {result.devices_scanned} devices, "
            f"found {len(result.discovered_links)} LLDP entries."
        )
        self._populate(result)

    def _on_error(self, msg: str):
        self.status_label.setText(f"Error: {msg}")
        self.errors_text.append(msg)

    # ------------------------------------------------------------------ #
    #  Populate results                                                    #
    # ------------------------------------------------------------------ #

    def _populate(self, result: DiscoveryResult):
        self._populate_links(result)
        self._populate_summary(result)
        self._populate_errors(result)
        # Switch to summary tab
        self.tabs.setCurrentIndex(0)

    def _populate_links(self, result: DiscoveryResult):
        self.links_table.setRowCount(len(result.discovered_links))
        for row, link in enumerate(result.discovered_links):
            self._set_cell(row, 0, link.local_device)
            self._set_cell(row, 1, str(link.local_port))
            self._set_cell(row, 2, link.remote_device)
            self._set_cell(row, 3, link.remote_port)
            if link.in_actual:
                self._set_cell(row, 4, "Matched", _GREEN)
            else:
                self._set_cell(row, 4, "Extra / unexpected", _RED)
        self.tabs.setTabText(1, f"Discovered Links ({len(result.discovered_links)})")

    def _populate_summary(self, result: DiscoveryResult):
        total_actual = len(result.matched) + len(result.missing)
        unique_discovered = len(result.matched) + len(result.extra)

        lines = [
            "=" * 52,
            "  SNMP TOPOLOGY DISCOVERY — SUMMARY",
            "=" * 52,
            f"  Devices scanned        : {result.devices_scanned}",
            f"  Actual topology links  : {total_actual}",
            f"  Discovered (unique)    : {unique_discovered}",
            "",
            f"  [OK] Matched           : {len(result.matched)}",
            f"  [!!] Missing from SNMP : {len(result.missing)}",
            f"  [??] Extra / unexpected: {len(result.extra)}",
            f"  [EE] Scan errors       : {len(result.errors)}",
            "=" * 52,
        ]

        if result.matched:
            lines += ["", "MATCHED LINKS (discovered correctly):"]
            for src_id, dst_id in result.matched:
                src = self.topology.get_device(src_id)
                dst = self.topology.get_device(dst_id)
                sn = src.name if src else src_id
                dn = dst.name if dst else dst_id
                lines.append(f"  [OK] {sn}  <-->  {dn}")

        if result.missing:
            lines += ["", "MISSING LINKS (in topology, not seen via SNMP):"]
            for src_id, dst_id in result.missing:
                src = self.topology.get_device(src_id)
                dst = self.topology.get_device(dst_id)
                sn = src.name if src else src_id
                dn = dst.name if dst else dst_id
                lines.append(f"  [!!] {sn}  <-->  {dn}")

        if result.extra:
            lines += ["", "EXTRA / UNEXPECTED links (in SNMP, not in topology):"]
            for link in result.extra:
                lines.append(f"  [??] {link.local_device}  <-->  {link.remote_device}")

        self.summary_text.setPlainText("\n".join(lines))

    def _populate_errors(self, result: DiscoveryResult):
        if result.errors:
            self.errors_text.setPlainText("\n".join(result.errors))
        self.tabs.setTabText(2, f"Errors ({len(result.errors)})")

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _set_cell(self, row: int, col: int, text: str,
                  color: QColor | None = None):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if color:
            item.setForeground(color)
        self.links_table.setItem(row, col, item)
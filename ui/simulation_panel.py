"""
Simulation Control Panel - start/stop/generate/clear controls
plus network interface selector for IP binding.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLabel, QGroupBox, QProgressBar,
    QFrame, QComboBox, QLineEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QObject
from PySide6.QtGui import QFont, QTextCursor


# ------------------------------------------------------------------ #
#  Log console                                                         #
# ------------------------------------------------------------------ #

class LogConsole(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setStyleSheet("""
            QTextEdit {
                background-color: #0d1117;
                color: #58a6ff;
                border: 1px solid #30363d;
                border-radius: 4px;
            }
        """)
        self.setMaximumHeight(200)

    def append_log(self, message: str, level: str = "info"):
        colors = {
            "info":    "#58a6ff",
            "success": "#3fb950",
            "warning": "#d29922",
            "error":   "#f85149",
        }
        color = colors.get(level, "#58a6ff")
        self.append(f'<span style="color:{color};">{message}</span>')
        self.moveCursor(QTextCursor.End)

    def clear_log(self):
        self.clear()


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
#  Interface loader (runs in background thread)                        #
# ------------------------------------------------------------------ #

class _InterfaceLoader(QObject):
    """Fetches Windows interfaces on a worker thread."""
    finished = Signal()   # no args – avoids marshaling Python list across thread boundary

    def run(self):
        from core.ip_binder import get_interfaces
        self.result = get_interfaces()
        self.finished.emit()


# ------------------------------------------------------------------ #
#  Main panel widget                                                   #
# ------------------------------------------------------------------ #

class SimulationPanel(QWidget):
    sig_generate  = Signal()
    sig_start     = Signal()
    sig_stop      = Signal()
    sig_clear     = Signal()
    sig_randomize = Signal()
    sig_refresh_interfaces = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._build_ui()
        self._load_interfaces()

    # ------------------------------------------------------------------ #
    #  Build UI                                                            #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # ---- Title ----
        title = QLabel("Simulation Control")
        title.setFont(QFont("Arial", 11, QFont.Bold))
        title.setStyleSheet("color: #e6edf3; padding: 4px 0;")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #30363d;")
        layout.addWidget(sep)

        # ---- Status ----
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_badge = StatusBadge()
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ---- Network binding group ----
        net_group = QGroupBox("Network Interface Binding")
        net_group.setStyleSheet(self._group_style())
        net_layout = QVBoxLayout(net_group)
        net_layout.setSpacing(4)

        # Explanation micro-text
        hint = QLabel(
            "Device IPs will be added to the selected adapter\n"
            "so SNMPSim can listen on each IP:161."
        )
        hint.setFont(QFont("Arial", 8))
        hint.setStyleSheet("color: #8b949e;")
        hint.setWordWrap(True)
        net_layout.addWidget(hint)

        # Interface combo + refresh button
        iface_row = QHBoxLayout()
        iface_row.setSpacing(4)
        self.iface_combo = QComboBox()
        self.iface_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.iface_combo.setMinimumWidth(160)
        self.iface_combo.setStyleSheet(self._combo_style())
        self.iface_combo.setPlaceholderText("Loading interfaces...")
        self.iface_combo.setToolTip(
            "Select the network adapter that device IPs will be added to"
        )
        iface_row.addWidget(self.iface_combo, stretch=1)

        self.btn_refresh_ifaces = QPushButton("Refresh")
        self.btn_refresh_ifaces.setFixedWidth(64)
        self.btn_refresh_ifaces.setStyleSheet(self._btn_secondary_style())
        self.btn_refresh_ifaces.setToolTip("Re-scan network adapters")
        self.btn_refresh_ifaces.clicked.connect(self._load_interfaces)
        iface_row.addWidget(self.btn_refresh_ifaces)
        net_layout.addLayout(iface_row)

        # Subnet mask
        mask_row = QHBoxLayout()
        mask_label = QLabel("Subnet Mask:")
        mask_label.setFont(QFont("Arial", 9))
        mask_label.setStyleSheet("color: #8b949e;")
        mask_row.addWidget(mask_label)
        self.mask_edit = QLineEdit("255.255.255.0")
        self.mask_edit.setFixedWidth(130)
        self.mask_edit.setFont(QFont("Consolas", 9))
        self.mask_edit.setStyleSheet(self._lineedit_style())
        self.mask_edit.setToolTip("Subnet mask applied when adding IPs via netsh")
        mask_row.addWidget(self.mask_edit)
        mask_row.addStretch()
        net_layout.addLayout(mask_row)

        # Bound IPs info label
        self.bound_label = QLabel("IPs bound: 0")
        self.bound_label.setFont(QFont("Consolas", 8))
        self.bound_label.setStyleSheet("color: #3fb950;")
        net_layout.addWidget(self.bound_label)

        layout.addWidget(net_group)

        # ---- Stats ----
        stats_group = QGroupBox("Topology Stats")
        stats_group.setStyleSheet(self._group_style())
        stats_layout = QVBoxLayout(stats_group)
        self.devices_label = QLabel("Devices: 0")
        self.links_label   = QLabel("Links:   0")
        self.files_label   = QLabel("Files:   0")
        for lbl in (self.devices_label, self.links_label, self.files_label):
            lbl.setFont(QFont("Consolas", 9))
            lbl.setStyleSheet("color: #e6edf3;")
            stats_layout.addWidget(lbl)
        layout.addWidget(stats_group)

        # ---- Progress bar ----
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

        # ---- Action buttons ----
        self.btn_generate = QPushButton("Generate Datasets")
        self.btn_generate.setStyleSheet(self._btn_generate_style())
        self.btn_generate.clicked.connect(self.sig_generate.emit)
        layout.addWidget(self.btn_generate)

        ss_row = QHBoxLayout()
        self.btn_start = QPushButton("Start Simulator")
        self.btn_start.setStyleSheet(self._btn_start_style())
        self.btn_start.clicked.connect(self.sig_start.emit)
        self.btn_start.setEnabled(False)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet(self._btn_stop_style())
        self.btn_stop.clicked.connect(self.sig_stop.emit)
        self.btn_stop.setEnabled(False)

        ss_row.addWidget(self.btn_start)
        ss_row.addWidget(self.btn_stop)
        layout.addLayout(ss_row)

        self.btn_randomize = QPushButton("Randomize Metrics")
        self.btn_randomize.setStyleSheet(self._btn_secondary_style())
        self.btn_randomize.clicked.connect(self.sig_randomize.emit)
        layout.addWidget(self.btn_randomize)

        self.btn_clear = QPushButton("Clear Simulation")
        self.btn_clear.setStyleSheet(self._btn_secondary_style())
        self.btn_clear.clicked.connect(self.sig_clear.emit)
        layout.addWidget(self.btn_clear)

        # ---- Log console ----
        log_group = QGroupBox("Console Output")
        log_group.setStyleSheet(self._group_style())
        log_layout = QVBoxLayout(log_group)
        self.log_console = LogConsole()
        log_layout.addWidget(self.log_console)

        clr_btn = QPushButton("Clear Log")
        clr_btn.setStyleSheet(self._btn_secondary_style())
        clr_btn.clicked.connect(self.log_console.clear_log)
        log_layout.addWidget(clr_btn)

        layout.addWidget(log_group)
        layout.addStretch()

    # ------------------------------------------------------------------ #
    #  Interface loading (background thread)                              #
    # ------------------------------------------------------------------ #

    def _load_interfaces(self):
        self.btn_refresh_ifaces.setEnabled(False)
        self.btn_refresh_ifaces.setText("...")
        self.iface_combo.setEnabled(False)

        self._iface_thread = QThread()
        self._iface_worker = _InterfaceLoader()
        self._iface_worker.moveToThread(self._iface_thread)
        self._iface_thread.started.connect(self._iface_worker.run)
        self._iface_worker.finished.connect(self._on_interfaces_loaded)
        self._iface_thread.start()

    def _on_interfaces_loaded(self):
        self._iface_thread.quit()
        self._iface_thread.wait()
        ifaces = self._iface_worker.result
        self.btn_refresh_ifaces.setEnabled(True)
        self.btn_refresh_ifaces.setText("Refresh")
        self.iface_combo.setEnabled(True)

        prev = self.iface_combo.currentData()
        self.iface_combo.clear()

        if not ifaces:
            self.iface_combo.addItem("(no adapters found)", None)
            return

        for name, label in ifaces:
            self.iface_combo.addItem(label, name)

        # Restore previous selection if still present
        if prev:
            idx = self.iface_combo.findData(prev)
            if idx >= 0:
                self.iface_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def log(self, msg: str, level: str = "info"):
        self.log_console.append_log(msg, level)

    def set_status(self, status: str):
        self.status_badge.set_status(status)

    def set_stats(self, devices: int, links: int, files: int = 0):
        self.devices_label.setText(f"Devices: {devices}")
        self.links_label.setText(f"Links:   {links}")
        self.files_label.setText(f"Files:   {files}")

    def set_bound_count(self, count: int):
        if count > 0:
            self.bound_label.setText(f"IPs bound: {count}")
            self.bound_label.setStyleSheet("color: #3fb950;")
        else:
            self.bound_label.setText("IPs bound: 0")
            self.bound_label.setStyleSheet("color: #8b949e;")

    def show_progress(self, value: int, maximum: int = 100):
        self.progress.setMaximum(maximum)
        self.progress.setValue(value)
        self.progress.show()
        if value >= maximum:
            QTimer.singleShot(1500, self.progress.hide)

    def set_simulator_running(self, running: bool):
        self._running = running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_generate.setEnabled(not running)
        self.iface_combo.setEnabled(not running)
        self.mask_edit.setEnabled(not running)
        self.btn_refresh_ifaces.setEnabled(not running)

    def set_datasets_ready(self, ready: bool):
        self.btn_start.setEnabled(ready and not self._running)

    @property
    def selected_interface(self) -> str:
        """The adapter name currently chosen in the dropdown (empty string if none)."""
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

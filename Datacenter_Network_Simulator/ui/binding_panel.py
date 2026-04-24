"""
Network Interface Binding Panel — shared adapter and subnet configuration
used by both the SNMP and gNMI simulators.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QGroupBox, QLineEdit, QComboBox, QSizePolicy, QProgressBar,
)
from PySide6.QtCore import QThread, QObject, Signal, QTimer
from PySide6.QtGui import QFont


# ------------------------------------------------------------------ #
#  Background interface loader                                         #
# ------------------------------------------------------------------ #

class _IfaceLoader(QObject):
    finished = Signal()

    def run(self):
        from core.ip_binder import get_interfaces
        self.result = get_interfaces()
        self.finished.emit()


# ------------------------------------------------------------------ #
#  BindingPanel                                                        #
# ------------------------------------------------------------------ #

class BindingPanel(QWidget):
    """Displays the shared network adapter selector and shows how many
    IPs are currently bound for each simulator."""

    sig_bind   = Signal()
    sig_unbind = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snmp_locked  = False
        self._gnmi_locked  = False
        self._total_bound  = 0
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
        title_lbl = QLabel("Network Interface Bindings")
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
        layout = cl

        # ── Adapter group ──────────────────────────────────────────────────
        adapter_group = QGroupBox("Adapter")
        adapter_group.setStyleSheet(self._group_style())
        adapter_layout = QVBoxLayout(adapter_group)
        adapter_layout.setContentsMargins(6, 4, 6, 6)
        adapter_layout.setSpacing(4)

        hint = QLabel("Device IPs will be added to the selected adapter")
        hint.setFont(QFont("Arial", 8))
        hint.setStyleSheet("color: #8b949e;")
        hint.setWordWrap(True)
        adapter_layout.addWidget(hint)

        iface_row = QHBoxLayout()
        iface_row.setSpacing(4)
        self.iface_combo = QComboBox()
        self.iface_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.iface_combo.setMinimumWidth(100)
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
        adapter_layout.addLayout(iface_row)

        mask_row = QHBoxLayout()
        mask_label = QLabel("Subnet Mask:")
        mask_label.setFont(QFont("Arial", 9))
        mask_label.setStyleSheet("color: #8b949e;")
        mask_row.addWidget(mask_label)
        self.mask_edit = QLineEdit("255.255.255.0")
        self.mask_edit.setMaximumWidth(120)
        self.mask_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.mask_edit.setFont(QFont("Consolas", 9))
        self.mask_edit.setStyleSheet(self._lineedit_style())
        mask_row.addWidget(self.mask_edit, stretch=1)
        adapter_layout.addLayout(mask_row)

        self.bound_label = QLabel("IPs bound: 0")
        self.bound_label.setFont(QFont("Consolas", 8))
        self.bound_label.setStyleSheet("color: #8b949e;")
        adapter_layout.addWidget(self.bound_label)

        layout.addWidget(adapter_group)

        # ── Progress bar ───────────────────────────────────────────────────
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

        # ── Action buttons ─────────────────────────────────────────────────
        self.btn_bind = QPushButton("Bind IPs")
        self.btn_bind.setStyleSheet(self._btn_bind_style())
        self.btn_bind.setToolTip("Bind all device IPs to the selected adapter")
        self.btn_bind.clicked.connect(self.sig_bind.emit)
        layout.addWidget(self.btn_bind)

        self.btn_unbind = QPushButton("Remove Binding")
        self.btn_unbind.setStyleSheet(self._btn_secondary_style())
        self.btn_unbind.setToolTip("Remove all bound IPs from the adapter")
        self.btn_unbind.setEnabled(False)
        self.btn_unbind.clicked.connect(self.sig_unbind.emit)
        layout.addWidget(self.btn_unbind)

        layout.addStretch()

    # ------------------------------------------------------------------ #
    #  Interface loading                                                   #
    # ------------------------------------------------------------------ #

    def _load_interfaces(self):
        self.btn_refresh_ifaces.setEnabled(False)
        self.btn_refresh_ifaces.setText("...")
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
        for name, label in ifaces:
            self.iface_combo.addItem(label, name)
        if prev:
            idx = self.iface_combo.findData(prev)
            if idx >= 0:
                self.iface_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def show_progress(self, value: int, maximum: int = 100):
        self.progress.setMaximum(maximum)
        self.progress.setValue(value)
        self.progress.show()
        if value >= maximum:
            QTimer.singleShot(1500, self.progress.hide)

    @property
    def selected_interface(self) -> str:
        data = self.iface_combo.currentData()
        return data if data else ""

    @property
    def subnet_mask(self) -> str:
        return self.mask_edit.text().strip() or "255.255.255.0"

    def set_bound_count(self, count: int):
        self._total_bound = count
        if count > 0:
            self.bound_label.setText(f"IPs bound: {count}")
            self.bound_label.setStyleSheet("color: #3fb950;")
        else:
            self.bound_label.setText("IPs bound: 0")
            self.bound_label.setStyleSheet("color: #8b949e;")
        self._update_lock_state()

    def set_snmp_locked(self, locked: bool):
        """Lock the adapter controls while SNMP binding/simulation is active."""
        self._snmp_locked = locked
        self._update_lock_state()

    def set_gnmi_locked(self, locked: bool):
        """Lock the adapter controls while gNMI binding is in progress."""
        self._gnmi_locked = locked
        self._update_lock_state()

    def _update_lock_state(self):
        unlocked = not (self._snmp_locked or self._gnmi_locked)
        self.iface_combo.setEnabled(unlocked)
        self.mask_edit.setEnabled(unlocked)
        self.btn_refresh_ifaces.setEnabled(unlocked)
        self.btn_bind.setEnabled(unlocked)
        self.btn_unbind.setEnabled(unlocked and self._total_bound > 0)

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

    def _btn_bind_style(self) -> str:
        return (
            "QPushButton { background: #238636; color: white; border: none; "
            "border-radius: 6px; padding: 8px; font-weight: bold; } "
            "QPushButton:hover { background: #2ea043; } "
            "QPushButton:pressed { background: #196127; } "
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
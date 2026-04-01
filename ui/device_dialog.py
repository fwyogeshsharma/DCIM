"""
Device Creation / Edit Dialog.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QVBoxLayout, QGroupBox,
    QLabel, QHBoxLayout, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from core.device_manager import Device, DeviceType, Vendor


class DeviceDialog(QDialog):
    """Dialog for creating or editing a device."""

    def __init__(self, parent=None, device: Device = None, ip_manager=None):
        super().__init__(parent)
        self.device = device
        self.ip_manager = ip_manager
        self._editing = device is not None
        self.setWindowTitle("Edit Device" if self._editing else "Add Device")
        self.setMinimumWidth(420)
        self._build_ui()
        if self._editing:
            self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ---- Identity group ----
        id_group = QGroupBox("Device Identity")
        id_form = QFormLayout(id_group)
        id_form.setLabelAlignment(Qt.AlignRight)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Router1")
        id_form.addRow("Name:", self.name_edit)

        self.type_combo = QComboBox()
        for dt in DeviceType:
            self.type_combo.addItem(dt.value.capitalize(), dt)
        id_form.addRow("Type:", self.type_combo)

        self.vendor_combo = QComboBox()
        for v in Vendor:
            self.vendor_combo.addItem(v.value, v)
        id_form.addRow("Vendor:", self.vendor_combo)

        layout.addWidget(id_group)

        # ---- Network group ----
        net_group = QGroupBox("Network Settings")
        net_form = QFormLayout(net_group)
        net_form.setLabelAlignment(Qt.AlignRight)

        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("192.168.1.x  (auto if blank)")
        net_form.addRow("IP Address:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(161, 65535)
        self.port_spin.setValue(161)
        net_form.addRow("SNMP Port:", self.port_spin)

        self.community_edit = QLineEdit("public")
        net_form.addRow("Community:", self.community_edit)

        layout.addWidget(net_group)

        # ---- Interface group ----
        iface_group = QGroupBox("Interfaces")
        iface_form = QFormLayout(iface_group)
        iface_form.setLabelAlignment(Qt.AlignRight)

        self.iface_spin = QSpinBox()
        self.iface_spin.setRange(1, 96)
        self.iface_spin.setValue(4)
        iface_form.addRow("Interface Count:", self.iface_spin)

        self.metrics_check = QCheckBox("Enable metric simulation")
        self.metrics_check.setChecked(True)
        iface_form.addRow("", self.metrics_check)

        layout.addWidget(iface_group)

        # ---- Buttons ----
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self):
        self.name_edit.setText(self.device.name)

        idx = self.type_combo.findData(self.device.device_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

        idx = self.vendor_combo.findData(self.device.vendor)
        if idx >= 0:
            self.vendor_combo.setCurrentIndex(idx)

        self.ip_edit.setText(self.device.ip_address)
        self.port_spin.setValue(self.device.snmp_port)
        self.community_edit.setText(self.device.snmp_community)
        self.iface_spin.setValue(self.device.interface_count)
        self.metrics_check.setChecked(self.device.metrics_enabled)

    def _validate_and_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            self.name_edit.setStyleSheet("border: 1px solid red;")
            return
        self.name_edit.setStyleSheet("")
        self.accept()

    def get_values(self) -> dict:
        ip = self.ip_edit.text().strip()
        if not ip and self.ip_manager:
            try:
                ip = self.ip_manager.next_ip()
            except RuntimeError:
                ip = "192.168.1.100"

        return {
            "name": self.name_edit.text().strip(),
            "device_type": self.type_combo.currentData(),
            "vendor": self.vendor_combo.currentData(),
            "ip_address": ip,
            "snmp_port": self.port_spin.value(),
            "snmp_community": self.community_edit.text().strip() or "public",
            "interface_count": self.iface_spin.value(),
            "metrics_enabled": self.metrics_check.isChecked(),
        }

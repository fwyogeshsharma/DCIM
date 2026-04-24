"""
Device Creation / Edit Dialog.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QCheckBox, QDialogButtonBox, QVBoxLayout, QGroupBox,
    QLabel, QFrame,
)
from PySide6.QtCore import Qt

from core.device_manager import Device, DeviceType, Vendor
from core.device_models import DEVICE_MODELS, IFACE_SHORT_LABEL

VENDORS_BY_TYPE = {
    DeviceType.ROUTER: [
        Vendor.CISCO_SYSTEMS,
        Vendor.JUNIPER_NETWORKS,
        Vendor.ARISTA_NETWORKS,
        Vendor.HPE,
        Vendor.EXTREME_NETWORKS,
        Vendor.HUAWEI,
        Vendor.DELL,
    ],
    DeviceType.SWITCH: [
        Vendor.CISCO_SYSTEMS,
        Vendor.JUNIPER_NETWORKS,
        Vendor.ARISTA_NETWORKS,
        Vendor.HPE,
        Vendor.EXTREME_NETWORKS,
        Vendor.HUAWEI,
        Vendor.DELL,
    ],
    DeviceType.SERVER: [
        Vendor.DELL,
        Vendor.HPE,
        Vendor.LENOVO,
        Vendor.SUPERMICRO,
        Vendor.CISCO_SYSTEMS,
        Vendor.IBM,
    ],
    DeviceType.FIREWALL: [
        Vendor.PALO_ALTO_NETWORKS,
    ],
    DeviceType.LOAD_BALANCER: [
        Vendor.F5_NETWORKS,
    ],
}


class DeviceDialog(QDialog):
    """Dialog for creating or editing a device."""

    def __init__(self, parent=None, device: Device = None, ip_manager=None):
        super().__init__(parent)
        self.device = device
        self.ip_manager = ip_manager
        self._editing = device is not None
        self.setWindowTitle("Edit Device" if self._editing else "Add Device")
        self.setMinimumWidth(440)
        self._build_ui()
        if self._editing:
            self._populate()

    # ------------------------------------------------------------------ #
    #  UI construction                                                     #
    # ------------------------------------------------------------------ #

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
        id_form.addRow("Vendor:", self.vendor_combo)

        self.model_combo = QComboBox()
        id_form.addRow("Model:", self.model_combo)

        # Wire cascading updates
        self.type_combo.currentIndexChanged.connect(self._update_vendors)
        self._update_vendors()                              # seed vendor + model
        self.vendor_combo.currentIndexChanged.connect(self._update_models)
        self.model_combo.currentIndexChanged.connect(self._update_port_info)

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

        self.gnmi_port_spin = QSpinBox()
        self.gnmi_port_spin.setRange(1, 65535)
        self.gnmi_port_spin.setValue(57400)
        net_form.addRow("gNMI Port:", self.gnmi_port_spin)

        self.community_edit = QLineEdit()
        self.community_edit.setReadOnly(True)
        self.community_edit.setPlaceholderText("auto (mirrors IP address)")
        net_form.addRow("Community:", self.community_edit)

        self.ip_edit.textChanged.connect(self.community_edit.setText)

        layout.addWidget(net_group)

        # ---- Interface group (read-only, driven by model) ----
        iface_group = QGroupBox("Interfaces")
        iface_form = QFormLayout(iface_group)
        iface_form.setLabelAlignment(Qt.AlignRight)

        self.port_info_label = QLabel("—")
        self.port_info_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        iface_form.addRow("Port Configuration:", self.port_info_label)

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

    # ------------------------------------------------------------------ #
    #  Cascading combo updates                                             #
    # ------------------------------------------------------------------ #

    def _update_vendors(self, _=None):
        device_type = self.type_combo.currentData()
        vendors = VENDORS_BY_TYPE.get(device_type, list(Vendor))
        self.vendor_combo.blockSignals(True)
        self.vendor_combo.clear()
        for v in vendors:
            self.vendor_combo.addItem(v.value, v)
        self.vendor_combo.blockSignals(False)
        self._update_models()

    def _update_models(self, _=None):
        device_type = self.type_combo.currentData()
        vendor = self.vendor_combo.currentData()
        models = DEVICE_MODELS.get((device_type, vendor), [])
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for m in models:
            self.model_combo.addItem(m.name, m)
        self.model_combo.blockSignals(False)
        self._update_port_info()

    def _update_port_info(self, _=None):
        if not hasattr(self, 'port_info_label'):
            return
        model = self.model_combo.currentData()
        if not model:
            self.port_info_label.setText("—")
            return
        lines = []
        for g in model.interface_groups:
            short = IFACE_SHORT_LABEL.get(g["iface_type"], g["iface_type"].value)
            lines.append(f"  {g['count']} × {short}")
        lines.append(f"  ──────────────")
        lines.append(f"  Total: {model.total_ports} ports")
        self.port_info_label.setText("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  Populate (edit mode)                                                #
    # ------------------------------------------------------------------ #

    def _populate(self):
        self.name_edit.setText(self.device.name)

        idx = self.type_combo.findData(self.device.device_type)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        # _update_vendors → _update_models triggered above; now set vendor
        idx = self.vendor_combo.findData(self.device.vendor)
        if idx >= 0:
            self.vendor_combo.setCurrentIndex(idx)
        # _update_models triggered above; restore model by name
        idx = self.model_combo.findText(self.device.model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)

        self.ip_edit.setText(self.device.ip_address)
        self.port_spin.setValue(self.device.snmp_port)
        self.gnmi_port_spin.setValue(self.device.gnmi_port)
        self.metrics_check.setChecked(self.device.metrics_enabled)

    # ------------------------------------------------------------------ #
    #  Validation & result                                                 #
    # ------------------------------------------------------------------ #

    def _validate_and_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            self.name_edit.setStyleSheet("border: 1px solid red;")
            return
        self.name_edit.setStyleSheet("")
        if not self.model_combo.currentData():
            self.model_combo.setStyleSheet("border: 1px solid red;")
            return
        self.model_combo.setStyleSheet("")
        self.accept()

    def get_values(self) -> dict:
        ip = self.ip_edit.text().strip()
        if not ip and self.ip_manager:
            try:
                ip = self.ip_manager.next_ip()
            except RuntimeError:
                ip = "192.168.1.100"

        model = self.model_combo.currentData()
        return {
            "name":             self.name_edit.text().strip(),
            "device_type":      self.type_combo.currentData(),
            "vendor":           self.vendor_combo.currentData(),
            "model_name":       model.name if model else "",
            "ip_address":       ip,
            "snmp_port":        self.port_spin.value(),
            "gnmi_port":        self.gnmi_port_spin.value(),
            "snmp_community":   ip,
            "interface_groups": list(model.interface_groups) if model else [],
            "metrics_enabled":  self.metrics_check.isChecked(),
        }
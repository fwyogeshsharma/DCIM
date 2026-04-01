"""
Device Manager - Manages all simulated network devices.
"""
from __future__ import annotations
import uuid
import random
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from enum import Enum


class DeviceType(str, Enum):
    ROUTER = "router"
    SWITCH = "switch"
    SERVER = "server"


class Vendor(str, Enum):
    CISCO = "Cisco"
    JUNIPER = "Juniper"
    LINUX = "Linux"
    GENERIC = "Generic"


VENDOR_SYSOID = {
    Vendor.CISCO: "1.3.6.1.4.1.9.1.1",
    Vendor.JUNIPER: "1.3.6.1.4.1.2636.1.1.1.2.1",
    Vendor.LINUX: "1.3.6.1.4.1.8072.3.2.10",
    Vendor.GENERIC: "1.3.6.1.4.1.0.0",
}

VENDOR_SYSDESCR = {
    Vendor.CISCO: "Cisco IOS Software, Version 15.7(3)M, RELEASE SOFTWARE (fc2)",
    Vendor.JUNIPER: "Juniper Networks, Inc. mx480 internet router, kernel JUNOS 20.4R3",
    Vendor.LINUX: "Linux server 5.15.0-91-generic #101-Ubuntu SMP x86_64",
    Vendor.GENERIC: "Generic SNMP Device v1.0",
}


@dataclass
class Interface:
    index: int
    name: str
    speed: int = 1000000000  # 1 Gbps
    oper_status: int = 1      # 1=up, 2=down
    in_octets: int = field(default_factory=lambda: random.randint(1000000, 999999999))
    out_octets: int = field(default_factory=lambda: random.randint(1000000, 999999999))
    in_errors: int = field(default_factory=lambda: random.randint(0, 100))
    out_errors: int = field(default_factory=lambda: random.randint(0, 100))
    mac_address: str = field(default_factory=lambda: ":".join(
        f"{random.randint(0,255):02x}" for _ in range(6)))
    connected_to_device: Optional[str] = None
    connected_to_iface: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Device:
    name: str
    device_type: DeviceType
    vendor: Vendor
    ip_address: str
    snmp_port: int = 161
    snmp_community: str = "public"
    interface_count: int = 4
    metrics_enabled: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    interfaces: List[Interface] = field(default_factory=list)

    # Dynamic metrics (randomized per device)
    cpu_usage: int = field(default_factory=lambda: random.randint(5, 95))
    memory_total: int = field(default_factory=lambda: random.choice([2, 4, 8, 16, 32]) * 1024 * 1024 * 1024)
    memory_used: int = 0
    disk_total: int = field(default_factory=lambda: random.choice([100, 250, 500, 1000]) * 1024 * 1024 * 1024)
    disk_used: int = 0
    sys_uptime: int = field(default_factory=lambda: random.randint(100000, 9999999))

    def __post_init__(self):
        if isinstance(self.device_type, str):
            self.device_type = DeviceType(self.device_type)
        if isinstance(self.vendor, str):
            self.vendor = Vendor(self.vendor)
        self.memory_used = int(self.memory_total * random.uniform(0.2, 0.85))
        self.disk_used = int(self.disk_total * random.uniform(0.1, 0.75))
        if not self.interfaces:
            self._generate_interfaces()

    def _generate_interfaces(self):
        self.interfaces = []
        iface_names = self._get_interface_names()
        for i in range(self.interface_count):
            self.interfaces.append(Interface(
                index=i + 1,
                name=iface_names[i] if i < len(iface_names) else f"eth{i}",
                speed=self._get_interface_speed(),
            ))

    def _get_interface_names(self) -> List[str]:
        if self.device_type == DeviceType.ROUTER:
            if self.vendor == Vendor.CISCO:
                return [f"GigabitEthernet0/{i}" for i in range(self.interface_count)]
            elif self.vendor == Vendor.JUNIPER:
                return [f"ge-0/0/{i}" for i in range(self.interface_count)]
            else:
                return [f"eth{i}" for i in range(self.interface_count)]
        elif self.device_type == DeviceType.SWITCH:
            if self.vendor == Vendor.CISCO:
                return [f"FastEthernet0/{i}" for i in range(self.interface_count)]
            else:
                return [f"port{i+1}" for i in range(self.interface_count)]
        else:
            return [f"eth{i}" for i in range(self.interface_count)]

    def _get_interface_speed(self) -> int:
        if self.device_type == DeviceType.SWITCH:
            return random.choice([100000000, 1000000000])
        elif self.device_type == DeviceType.ROUTER:
            return random.choice([1000000000, 10000000000])
        else:
            return 1000000000

    @property
    def sys_descr(self) -> str:
        return VENDOR_SYSDESCR.get(self.vendor, "Generic Device")

    @property
    def sys_oid(self) -> str:
        return VENDOR_SYSOID.get(self.vendor, "1.3.6.1.4.1.0.0")

    def randomize_metrics(self):
        """Refresh metrics with new random values."""
        self.cpu_usage = random.randint(5, 95)
        self.memory_used = int(self.memory_total * random.uniform(0.2, 0.85))
        self.disk_used = int(self.disk_total * random.uniform(0.1, 0.75))
        self.sys_uptime += random.randint(100, 1000)
        for iface in self.interfaces:
            iface.in_octets += random.randint(1000, 10000000)
            iface.out_octets += random.randint(1000, 10000000)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["device_type"] = self.device_type.value
        d["vendor"] = self.vendor.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        interfaces_data = data.pop("interfaces", [])
        device = cls(**data)
        device.interfaces = [Interface(**i) for i in interfaces_data]
        return device


class DeviceManager:
    """Central registry for all simulated devices."""

    def __init__(self):
        self._devices: Dict[str, Device] = {}

    def add_device(self, device: Device) -> Device:
        self._devices[device.id] = device
        return device

    def remove_device(self, device_id: str) -> Optional[Device]:
        return self._devices.pop(device_id, None)

    def get_device(self, device_id: str) -> Optional[Device]:
        return self._devices.get(device_id)

    def get_all_devices(self) -> List[Device]:
        return list(self._devices.values())

    def get_devices_by_type(self, device_type: DeviceType) -> List[Device]:
        return [d for d in self._devices.values() if d.device_type == device_type]

    def update_device(self, device_id: str, **kwargs) -> Optional[Device]:
        device = self._devices.get(device_id)
        if device:
            for key, value in kwargs.items():
                if hasattr(device, key):
                    setattr(device, key, value)
            # Rebuild interfaces if interface_count changed
            if "interface_count" in kwargs:
                device._generate_interfaces()
        return device

    def clear(self):
        self._devices.clear()

    def count(self) -> int:
        return len(self._devices)

    def randomize_all_metrics(self):
        for device in self._devices.values():
            device.randomize_metrics()

    def bulk_add(self, device_type: DeviceType, vendor: Vendor,
                 count: int, ip_manager) -> List[Device]:
        """Add many devices at once."""
        devices = []
        type_prefix = device_type.value[:1].upper()
        for i in range(count):
            existing = [d.name for d in self._devices.values()]
            idx = 1
            while f"{type_prefix}{idx}" in existing:
                idx += 1
            name = f"{type_prefix}{idx + i}"
            ip = ip_manager.next_ip()
            device = Device(
                name=name,
                device_type=device_type,
                vendor=vendor,
                ip_address=ip,
                interface_count=random.choice([4, 8, 12, 24]),
            )
            self.add_device(device)
            devices.append(device)
        return devices

    def to_list(self) -> List[dict]:
        return [d.to_dict() for d in self._devices.values()]

    def load_list(self, data: List[dict]):
        self._devices.clear()
        for item in data:
            device = Device.from_dict(item)
            self._devices[device.id] = device

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
    # Networking vendors (routers & switches)
    CISCO_SYSTEMS = "Cisco Systems"
    JUNIPER_NETWORKS = "Juniper Networks"
    ARISTA_NETWORKS = "Arista Networks"
    HPE = "Hewlett Packard Enterprise"
    EXTREME_NETWORKS = "Extreme Networks"
    HUAWEI = "Huawei Technologies"
    DELL = "Dell Technologies"
    # Server-only vendors
    LENOVO = "Lenovo"
    SUPERMICRO = "Supermicro"
    IBM = "IBM"
    PALO_ALTO_NETWORKS = "Palo Alto Networks"
    F5_NETWORKS = "F5 Networks"


class InterfaceType(str, Enum):
    FAST_ETHERNET    = "Fast Ethernet (100 Mbps)"
    GIGABIT_ETHERNET = "Gigabit Ethernet (1 Gbps)"
    TEN_GIG_ETHERNET = "10 Gigabit Ethernet (10 Gbps)"
    TWENTY_FIVE_GIG  = "25 Gigabit Ethernet (25 Gbps)"
    FORTY_GIG        = "40 Gigabit Ethernet (40 Gbps)"
    HUNDRED_GIG      = "100 Gigabit Ethernet (100 Gbps)"


IFACE_SPEED = {
    InterfaceType.FAST_ETHERNET:    100_000_000,
    InterfaceType.GIGABIT_ETHERNET: 1_000_000_000,
    InterfaceType.TEN_GIG_ETHERNET: 10_000_000_000,
    InterfaceType.TWENTY_FIVE_GIG:  25_000_000_000,
    InterfaceType.FORTY_GIG:        40_000_000_000,
    InterfaceType.HUNDRED_GIG:      100_000_000_000,
}


def iface_name(vendor: Vendor, iface_type: InterfaceType, index: int) -> str:
    """Return the vendor-specific interface name for a given type and index."""
    if vendor == Vendor.CISCO_SYSTEMS:
        return {
            InterfaceType.FAST_ETHERNET:    f"FastEthernet0/{index}",
            InterfaceType.GIGABIT_ETHERNET: f"GigabitEthernet0/{index}",
            InterfaceType.TEN_GIG_ETHERNET: f"TenGigabitEthernet0/{index}",
            InterfaceType.TWENTY_FIVE_GIG:  f"TwentyFiveGigE0/{index}",
            InterfaceType.FORTY_GIG:        f"FortyGigabitEthernet0/{index}",
            InterfaceType.HUNDRED_GIG:      f"HundredGigE0/{index}",
        }.get(iface_type, f"GigabitEthernet0/{index}")
    if vendor == Vendor.JUNIPER_NETWORKS:
        return {
            InterfaceType.GIGABIT_ETHERNET: f"ge-0/0/{index}",
            InterfaceType.TEN_GIG_ETHERNET: f"xe-0/0/{index}",
        }.get(iface_type, f"et-0/0/{index}")
    if vendor == Vendor.HUAWEI:
        return (f"GigabitEthernet0/0/{index}"
                if iface_type == InterfaceType.GIGABIT_ETHERNET
                else f"XGigabitEthernet0/0/{index}")
    if vendor == Vendor.EXTREME_NETWORKS:
        return f"1:{index + 1}"
    if vendor == Vendor.ARISTA_NETWORKS:
        return f"Ethernet{index}"
    if vendor in (Vendor.HPE, Vendor.DELL):
        return f"eth1/{index + 1}"
    return f"eth{index}"


VENDOR_SYSOID = {
    Vendor.CISCO_SYSTEMS:   "1.3.6.1.4.1.9.1.1",
    Vendor.JUNIPER_NETWORKS:"1.3.6.1.4.1.2636.1.1.1.2.1",
    Vendor.ARISTA_NETWORKS: "1.3.6.1.4.1.30065.1.3011.7060.5310.18.548",
    Vendor.HPE:             "1.3.6.1.4.1.11.2.3.7.11.1",
    Vendor.EXTREME_NETWORKS:"1.3.6.1.4.1.1916.2.1",
    Vendor.HUAWEI:          "1.3.6.1.4.1.2011.2.239.1",
    Vendor.DELL:            "1.3.6.1.4.1.674.10895.3000",
    Vendor.LENOVO:          "1.3.6.1.4.1.19046.11.1.1",
    Vendor.SUPERMICRO:      "1.3.6.1.4.1.10876.2.1",
    Vendor.IBM:             "1.3.6.1.4.1.2.6.190",
}

VENDOR_SYSDESCR = {
    Vendor.CISCO_SYSTEMS:   "Cisco IOS Software, Version 17.9.4, RELEASE SOFTWARE (fc3)",
    Vendor.JUNIPER_NETWORKS:"Juniper Networks, Inc. MX480 Internet Router, JUNOS 22.4R1",
    Vendor.ARISTA_NETWORKS: "Arista Networks EOS version 4.28.3M running on an Arista Networks DCS-7050CX3",
    Vendor.HPE:             "HPE FlexFabric 5945 JH175A, Comware Software Version 7.1.070",
    Vendor.EXTREME_NETWORKS:"ExtremeXOS version 31.7.1.4 v31.7.1.4-patch1-4 by release-manager",
    Vendor.HUAWEI:          "Huawei Versatile Routing Platform Software VRP (R) version V200R010C10SPC600",
    Vendor.DELL:            "Dell EMC PowerSwitch S5248F-ON, OS10 Enterprise version 10.5.3.4",
    Vendor.LENOVO:          "Lenovo ThinkSystem SR650 V2, BMC version 3.00",
    Vendor.SUPERMICRO:      "Supermicro SYS-220U-TNR, IPMI firmware version 3.88.09",
    Vendor.IBM:             "IBM System x3850 X6, IMM2 firmware version 4.70",
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
    interface_groups: List[dict] = field(default_factory=list)
    model_name: str = ""
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
        # Normalize interface_groups (str → InterfaceType)
        if self.interface_groups:
            normalized = []
            for g in self.interface_groups:
                itype = g["iface_type"]
                if isinstance(itype, str):
                    try:
                        itype = InterfaceType(itype)
                    except ValueError:
                        itype = InterfaceType.GIGABIT_ETHERNET
                normalized.append({"iface_type": itype, "count": int(g["count"])})
            self.interface_groups = normalized
            self.interface_count = sum(g["count"] for g in self.interface_groups)
        else:
            # Auto-generate a single group from interface_count (e.g. topology scripts)
            self.interface_groups = [
                {"iface_type": InterfaceType.GIGABIT_ETHERNET, "count": self.interface_count}
            ]
        # Community string always mirrors the IP address (default "public" is a placeholder)
        if self.snmp_community == "public":
            self.snmp_community = self.ip_address
        self.memory_used = int(self.memory_total * random.uniform(0.2, 0.85))
        self.disk_used = int(self.disk_total * random.uniform(0.1, 0.75))
        if not self.interfaces:
            self._generate_interfaces()

    def _generate_interfaces(self):
        self.interfaces = []
        idx = 1
        for group in self.interface_groups:
            itype = group["iface_type"]
            speed = IFACE_SPEED.get(itype, 1_000_000_000)
            for i in range(group["count"]):
                self.interfaces.append(Interface(
                    index=idx,
                    name=iface_name(self.vendor, itype, i),
                    speed=speed,
                ))
                idx += 1

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
        d["model_name"] = self.model_name
        d["interface_groups"] = [
            {"iface_type": (g["iface_type"].value
                            if isinstance(g["iface_type"], InterfaceType)
                            else g["iface_type"]),
             "count": g["count"]}
            for g in self.interface_groups
        ]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        interfaces_data = data.pop("interfaces", [])
        data.pop("interface_type", None)  # removed field — drop from legacy JSON
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
            # Rebuild interfaces if interface layout changed
            if "interface_count" in kwargs or "interface_groups" in kwargs:
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

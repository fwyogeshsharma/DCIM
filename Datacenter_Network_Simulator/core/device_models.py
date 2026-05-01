"""
Device Model Registry — maps (DeviceType, Vendor) to a list of real hardware models,
each carrying its exact port configuration.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple

from core.device_manager import DeviceType, Vendor, InterfaceType


@dataclass
class DeviceModel:
    name: str
    vendor: Vendor
    device_type: DeviceType
    interface_groups: List[dict]   # [{"iface_type": InterfaceType, "count": int}, ...]
    description: str = ""

    @property
    def total_ports(self) -> int:
        return sum(g["count"] for g in self.interface_groups)


# Abbreviated speed labels used in the port-info display
IFACE_SHORT_LABEL = {
    InterfaceType.FAST_ETHERNET:    "100M",
    InterfaceType.GIGABIT_ETHERNET: "1GbE",
    InterfaceType.TEN_GIG_ETHERNET: "10GbE",
    InterfaceType.TWENTY_FIVE_GIG:  "25GbE",
    InterfaceType.FORTY_GIG:        "40GbE",
    InterfaceType.HUNDRED_GIG:      "100GbE",
}


def _g(iface_type: InterfaceType, count: int) -> dict:
    return {"iface_type": iface_type, "count": count}


# ── shorthand aliases ─────────────────────────────────────────────────────────
_FE   = InterfaceType.FAST_ETHERNET
_GE   = InterfaceType.GIGABIT_ETHERNET
_10G  = InterfaceType.TEN_GIG_ETHERNET
_25G  = InterfaceType.TWENTY_FIVE_GIG
_40G  = InterfaceType.FORTY_GIG
_100G = InterfaceType.HUNDRED_GIG

# ── registry ──────────────────────────────────────────────────────────────────
# Key: (DeviceType, Vendor)   Value: list of DeviceModel
DEVICE_MODELS: Dict[Tuple[DeviceType, Vendor], List[DeviceModel]] = {

    # ── Cisco — Routers ───────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.CISCO_SYSTEMS): [
        DeviceModel("Cisco ISR 4321", Vendor.CISCO_SYSTEMS, DeviceType.ROUTER,
                    [_g(_GE, 2)],
                    "2 × GE WAN/LAN"),
        DeviceModel("Cisco ISR 4431", Vendor.CISCO_SYSTEMS, DeviceType.ROUTER,
                    [_g(_GE, 4)],
                    "4 × GE"),
        DeviceModel("Cisco ASR 1001-X", Vendor.CISCO_SYSTEMS, DeviceType.ROUTER,
                    [_g(_GE, 6), _g(_10G, 2)],
                    "6 × GE + 2 × 10GE SFP+"),
        DeviceModel("Cisco ASR 9001", Vendor.CISCO_SYSTEMS, DeviceType.ROUTER,
                    [_g(_10G, 4), _g(_100G, 2)],
                    "4 × 10GE + 2 × 100GE"),
        DeviceModel("Cisco ASR 9904", Vendor.CISCO_SYSTEMS, DeviceType.ROUTER,
                    [_g(_100G, 8)],
                    "8 × 100GE (expandable)"),
    ],

    # ── Cisco — Switches ──────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.CISCO_SYSTEMS): [
        DeviceModel("Cisco Catalyst 2960-X-24TS", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_GE, 24), _g(_10G, 4)],
                    "24 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("Cisco Catalyst 3850-48", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("Cisco Catalyst 9300-48P", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_25G, 4)],
                    "48 × 1GE PoE+ + 4 × 25GE uplink"),
        DeviceModel("Cisco Nexus 9372PX", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_10G, 48), _g(_40G, 6)],
                    "48 × 10GE + 6 × 40GE"),
        DeviceModel("Cisco Nexus 93180YC-FX", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 6)],
                    "48 × 1/10/25GE + 6 × 40/100GE"),
        DeviceModel("Cisco Nexus 9336C-FX2", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_100G, 36)],
                    "36 × 40/100GE QSFP28"),
        DeviceModel("Cisco Nexus 9364C", Vendor.CISCO_SYSTEMS, DeviceType.SWITCH,
                    [_g(_100G, 64)],
                    "64 × 100GE QSFP28"),
    ],

    # ── Cisco — Servers ───────────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.CISCO_SYSTEMS): [
        DeviceModel("Cisco UCS C220 M6", Vendor.CISCO_SYSTEMS, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("Cisco UCS C240 M6", Vendor.CISCO_SYSTEMS, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
        DeviceModel("Cisco UCS B200 M6", Vendor.CISCO_SYSTEMS, DeviceType.SERVER,
                    [_g(_25G, 2)],
                    "2 × 25GE (blade)"),
    ],

    # ── Juniper — Routers ─────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.JUNIPER_NETWORKS): [
        DeviceModel("Juniper SRX345", Vendor.JUNIPER_NETWORKS, DeviceType.ROUTER,
                    [_g(_GE, 16)],
                    "16 × GE"),
        DeviceModel("Juniper SRX4600", Vendor.JUNIPER_NETWORKS, DeviceType.ROUTER,
                    [_g(_10G, 8), _g(_100G, 4)],
                    "8 × 10GE + 4 × 100GE"),
        DeviceModel("Juniper MX204", Vendor.JUNIPER_NETWORKS, DeviceType.ROUTER,
                    [_g(_10G, 8), _g(_100G, 4)],
                    "8 × 10GE + 4 × 100GE"),
        DeviceModel("Juniper MX480", Vendor.JUNIPER_NETWORKS, DeviceType.ROUTER,
                    [_g(_10G, 20), _g(_100G, 8)],
                    "20 × 10GE + 8 × 100GE (typical config)"),
    ],

    # ── Juniper — Switches ────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.JUNIPER_NETWORKS): [
        DeviceModel("Juniper EX2300-24T", Vendor.JUNIPER_NETWORKS, DeviceType.SWITCH,
                    [_g(_GE, 24), _g(_10G, 4)],
                    "24 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("Juniper EX4300-48T", Vendor.JUNIPER_NETWORKS, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("Juniper EX9253", Vendor.JUNIPER_NETWORKS, DeviceType.SWITCH,
                    [_g(_10G, 48), _g(_100G, 6)],
                    "48 × 10GE + 6 × 100GE"),
        DeviceModel("Juniper QFX5120-48Y", Vendor.JUNIPER_NETWORKS, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 8)],
                    "48 × 25GE + 8 × 100GE"),
        DeviceModel("Juniper QFX10002-36Q", Vendor.JUNIPER_NETWORKS, DeviceType.SWITCH,
                    [_g(_40G, 36)],
                    "36 × 40GE QSFP+"),
    ],

    # ── Arista — Routers ──────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.ARISTA_NETWORKS): [
        DeviceModel("Arista 7280R3-48YC8", Vendor.ARISTA_NETWORKS, DeviceType.ROUTER,
                    [_g(_25G, 48), _g(_100G, 8)],
                    "48 × 25GE + 8 × 100GE"),
        DeviceModel("Arista 7500R3-24D", Vendor.ARISTA_NETWORKS, DeviceType.ROUTER,
                    [_g(_100G, 24)],
                    "24 × 100GE"),
    ],

    # ── Arista — Switches ─────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.ARISTA_NETWORKS): [
        DeviceModel("Arista 7010T-48", Vendor.ARISTA_NETWORKS, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("Arista 7050CX3-32S", Vendor.ARISTA_NETWORKS, DeviceType.SWITCH,
                    [_g(_100G, 32), _g(_10G, 2)],
                    "32 × 100GE QSFP28 + 2 × 10GE management"),
        DeviceModel("Arista 7060CX2-32S", Vendor.ARISTA_NETWORKS, DeviceType.SWITCH,
                    [_g(_100G, 32)],
                    "32 × 100GE QSFP28"),
        DeviceModel("Arista 7280CR3-96", Vendor.ARISTA_NETWORKS, DeviceType.SWITCH,
                    [_g(_100G, 96)],
                    "96 × 100GE"),
    ],

    # ── HPE — Routers ─────────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.HPE): [
        DeviceModel("HPE FlexNetwork 7510", Vendor.HPE, DeviceType.ROUTER,
                    [_g(_GE, 8), _g(_10G, 2)],
                    "8 × GE + 2 × 10GE"),
    ],

    # ── HPE — Switches ────────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.HPE): [
        DeviceModel("HPE Aruba 2930F-48G", Vendor.HPE, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("HPE Aruba 6300M-48G", Vendor.HPE, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE SFP+"),
        DeviceModel("HPE FlexFabric 5940-48SFP+", Vendor.HPE, DeviceType.SWITCH,
                    [_g(_10G, 48), _g(_40G, 6)],
                    "48 × 10GE SFP+ + 6 × 40GE QSFP+"),
        DeviceModel("HPE Aruba 8325-48Y8C", Vendor.HPE, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 8)],
                    "48 × 25GE + 8 × 100GE"),
    ],

    # ── HPE — Servers ─────────────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.HPE): [
        DeviceModel("HPE ProLiant DL360 Gen10", Vendor.HPE, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("HPE ProLiant DL380 Gen10", Vendor.HPE, DeviceType.SERVER,
                    [_g(_10G, 4)],
                    "4 × 10GE"),
        DeviceModel("HPE ProLiant DL380 Gen11", Vendor.HPE, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
        DeviceModel("HPE ProLiant DL560 Gen10", Vendor.HPE, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
    ],

    # ── Extreme Networks — Routers ────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.EXTREME_NETWORKS): [
        DeviceModel("Extreme SLX 9640", Vendor.EXTREME_NETWORKS, DeviceType.ROUTER,
                    [_g(_10G, 24), _g(_100G, 4)],
                    "24 × 10GE + 4 × 100GE"),
    ],

    # ── Extreme Networks — Switches ───────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.EXTREME_NETWORKS): [
        DeviceModel("Extreme X460-G2-48t", Vendor.EXTREME_NETWORKS, DeviceType.SWITCH,
                    [_g(_GE, 48), _g(_10G, 4)],
                    "48 × 1GE + 4 × 10GE"),
        DeviceModel("Extreme X695-48Y", Vendor.EXTREME_NETWORKS, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 8)],
                    "48 × 25GE + 8 × 100GE"),
        DeviceModel("Extreme X870-96x", Vendor.EXTREME_NETWORKS, DeviceType.SWITCH,
                    [_g(_25G, 96), _g(_100G, 8)],
                    "96 × 25GE + 8 × 100GE"),
    ],

    # ── Huawei — Routers ──────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.HUAWEI): [
        DeviceModel("Huawei NE40E-X3", Vendor.HUAWEI, DeviceType.ROUTER,
                    [_g(_GE, 16), _g(_10G, 4)],
                    "16 × GE + 4 × 10GE"),
        DeviceModel("Huawei NE8000-F8", Vendor.HUAWEI, DeviceType.ROUTER,
                    [_g(_100G, 8)],
                    "8 × 100GE"),
    ],

    # ── Huawei — Switches ─────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.HUAWEI): [
        DeviceModel("Huawei S6730-H48Y6C", Vendor.HUAWEI, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 6)],
                    "48 × 25GE + 6 × 100GE"),
        DeviceModel("Huawei CE6870-48S6CQ", Vendor.HUAWEI, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 6)],
                    "48 × 25GE + 6 × 100GE"),
        DeviceModel("Huawei CE8850-64CQ", Vendor.HUAWEI, DeviceType.SWITCH,
                    [_g(_100G, 64)],
                    "64 × 100GE QSFP28"),
    ],

    # ── Dell — Routers ────────────────────────────────────────────────────────
    (DeviceType.ROUTER, Vendor.DELL): [
        DeviceModel("Dell EMC PowerSwitch Z9332F-ON", Vendor.DELL, DeviceType.ROUTER,
                    [_g(_100G, 32), _g(_10G, 2)],
                    "32 × 100GE + 2 × 10GE management"),
    ],

    # ── Dell — Switches ───────────────────────────────────────────────────────
    (DeviceType.SWITCH, Vendor.DELL): [
        DeviceModel("Dell S5248F-ON", Vendor.DELL, DeviceType.SWITCH,
                    [_g(_25G, 48), _g(_100G, 2), _g(_10G, 2)],
                    "48 × 25GE + 2 × 100GE + 2 × 10GE management"),
        DeviceModel("Dell S5296F-ON", Vendor.DELL, DeviceType.SWITCH,
                    [_g(_25G, 96), _g(_100G, 4)],
                    "96 × 25GE + 4 × 100GE"),
        DeviceModel("Dell Z9264F-ON", Vendor.DELL, DeviceType.SWITCH,
                    [_g(_100G, 64), _g(_10G, 2)],
                    "64 × 100GE + 2 × 10GE management"),
    ],

    # ── Dell — Servers ────────────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.DELL): [
        DeviceModel("Dell PowerEdge R640", Vendor.DELL, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("Dell PowerEdge R740", Vendor.DELL, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("Dell PowerEdge R750", Vendor.DELL, DeviceType.SERVER,
                    [_g(_25G, 2)],
                    "2 × 25GE"),
        DeviceModel("Dell PowerEdge R940", Vendor.DELL, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
        DeviceModel("Dell PowerEdge R7525", Vendor.DELL, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE (AMD EPYC)"),
    ],

    # ── Lenovo — Servers ──────────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.LENOVO): [
        DeviceModel("Lenovo ThinkSystem SR630 V2", Vendor.LENOVO, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("Lenovo ThinkSystem SR650 V2", Vendor.LENOVO, DeviceType.SERVER,
                    [_g(_25G, 2)],
                    "2 × 25GE"),
        DeviceModel("Lenovo ThinkSystem SR860 V2", Vendor.LENOVO, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
    ],

    # ── Supermicro — Servers ──────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.SUPERMICRO): [
        DeviceModel("Supermicro SYS-120U-TNR", Vendor.SUPERMICRO, DeviceType.SERVER,
                    [_g(_10G, 2)],
                    "2 × 10GE"),
        DeviceModel("Supermicro SYS-220U-TNR", Vendor.SUPERMICRO, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE"),
        DeviceModel("Supermicro AS-4124GS-TNR", Vendor.SUPERMICRO, DeviceType.SERVER,
                    [_g(_25G, 4)],
                    "4 × 25GE (GPU optimized)"),
    ],

    # ── Palo Alto Networks — Firewalls ────────────────────────────────────────
    (DeviceType.FIREWALL, Vendor.PALO_ALTO_NETWORKS): [
        DeviceModel("PA-820", Vendor.PALO_ALTO_NETWORKS, DeviceType.FIREWALL,
                    [_g(_GE, 4), _g(_10G, 4)],
                    "4 × GE + 4 × 10GE SFP+"),
        DeviceModel("PA-3220", Vendor.PALO_ALTO_NETWORKS, DeviceType.FIREWALL,
                    [_g(_GE, 4), _g(_10G, 8)],
                    "4 × GE + 8 × 10GE SFP+"),
        DeviceModel("PA-5220", Vendor.PALO_ALTO_NETWORKS, DeviceType.FIREWALL,
                    [_g(_10G, 8), _g(_40G, 4)],
                    "8 × 10GE SFP+ + 4 × 40GE QSFP+"),
        DeviceModel("PA-5260", Vendor.PALO_ALTO_NETWORKS, DeviceType.FIREWALL,
                    [_g(_10G, 8), _g(_100G, 4)],
                    "8 × 10GE SFP+ + 4 × 100GE QSFP28"),
    ],

    # ── F5 Networks — Load Balancers ──────────────────────────────────────────
    (DeviceType.LOAD_BALANCER, Vendor.F5_NETWORKS): [
        DeviceModel("BIG-IP i2800", Vendor.F5_NETWORKS, DeviceType.LOAD_BALANCER,
                    [_g(_GE, 4), _g(_10G, 4)],
                    "4 × GE + 4 × 10GE SFP+"),
        DeviceModel("BIG-IP i4800", Vendor.F5_NETWORKS, DeviceType.LOAD_BALANCER,
                    [_g(_10G, 8)],
                    "8 × 10GE SFP+"),
        DeviceModel("BIG-IP i5800", Vendor.F5_NETWORKS, DeviceType.LOAD_BALANCER,
                    [_g(_10G, 8), _g(_40G, 2)],
                    "8 × 10GE SFP+ + 2 × 40GE QSFP+"),
        DeviceModel("BIG-IP i10800", Vendor.F5_NETWORKS, DeviceType.LOAD_BALANCER,
                    [_g(_10G, 8), _g(_100G, 4)],
                    "8 × 10GE SFP+ + 4 × 100GE QSFP28"),
    ],

    # ── IBM — Servers ─────────────────────────────────────────────────────────
    (DeviceType.SERVER, Vendor.IBM): [
        DeviceModel("IBM Power System S922", Vendor.IBM, DeviceType.SERVER,
                    [_g(_10G, 4)],
                    "4 × 10GE"),
        DeviceModel("IBM System x3850 X6", Vendor.IBM, DeviceType.SERVER,
                    [_g(_10G, 4)],
                    "4 × 10GE"),
        DeviceModel("IBM FlexSystem x240 M5", Vendor.IBM, DeviceType.SERVER,
                    [_g(_25G, 2)],
                    "2 × 25GE (blade)"),
    ],

    # ── APC — UPS ─────────────────────────────────────────────────────────────
    (DeviceType.UPS, Vendor.APC): [
        DeviceModel("APC Smart-UPS 1500", Vendor.APC, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "1500VA/1000W, 1 × GE management"),
        DeviceModel("APC Smart-UPS 3000", Vendor.APC, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "3000VA/2700W, 1 × GE management"),
        DeviceModel("APC Smart-UPS SRT 5000", Vendor.APC, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "5000VA/4500W, 1 × GE management"),
        DeviceModel("APC Symmetra PX 100", Vendor.APC, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "100kVA scalable, 1 × GE management"),
    ],

    # ── Eaton — UPS ───────────────────────────────────────────────────────────
    (DeviceType.UPS, Vendor.EATON): [
        DeviceModel("Eaton 5PX 2200", Vendor.EATON, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "2200VA/2200W, 1 × GE management"),
        DeviceModel("Eaton 9PX 5000", Vendor.EATON, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "5000VA/4500W, 1 × GE management"),
        DeviceModel("Eaton 9E 20kVA", Vendor.EATON, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "20kVA/18kW, 1 × GE management"),
        DeviceModel("Eaton 93E 40kVA", Vendor.EATON, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "40kVA/36kW, 1 × GE management"),
    ],

    # ── Vertiv — UPS ──────────────────────────────────────────────────────────
    (DeviceType.UPS, Vendor.VERTIV): [
        DeviceModel("Vertiv Liebert GXT5 2000VA", Vendor.VERTIV, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "2000VA/1800W, 1 × GE management"),
        DeviceModel("Vertiv Liebert EXL S1 20kVA", Vendor.VERTIV, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "20kVA/18kW, 1 × GE management"),
        DeviceModel("Vertiv Liebert APS 20kVA", Vendor.VERTIV, DeviceType.UPS,
                    [_g(_GE, 1)],
                    "20kVA/18kW, 1 × GE management"),
    ],

    # ── APC — PDU ─────────────────────────────────────────────────────────────
    (DeviceType.PDU, Vendor.APC): [
        DeviceModel("APC AP8941", Vendor.APC, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched ZeroU, 30A/208V, 21×C13 + 3×C19"),
        DeviceModel("APC AP8886", Vendor.APC, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Metered ZeroU, 20A/208V, 21×C13 + 3×C19"),
        DeviceModel("APC AP8959", Vendor.APC, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched 1U, 30A/208V, 12×C13 + 4×C19"),
        DeviceModel("APC AP8681", Vendor.APC, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Metered-by-Outlet 1U, 16A/230V, 12×C13 + 4×C19"),
    ],

    # ── Raritan — PDU ─────────────────────────────────────────────────────────
    (DeviceType.PDU, Vendor.RARITAN): [
        DeviceModel("Raritan PX3-5190R", Vendor.RARITAN, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched 1U, 30A/208V, 24×C13 + 6×C19"),
        DeviceModel("Raritan PX3-5161R", Vendor.RARITAN, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched 1U, 16A/208V, 12×C13 + 4×C19"),
        DeviceModel("Raritan PX2-5170CR", Vendor.RARITAN, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched 0U, 30A/208V, 24×C13 + 6×C19"),
    ],

    # ── Eaton — PDU ───────────────────────────────────────────────────────────
    (DeviceType.PDU, Vendor.EATON): [
        DeviceModel("Eaton ePDU G3 MA 1U 16A", Vendor.EATON, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Managed 1U, 16A/230V, 12×C13 + 4×C19"),
        DeviceModel("Eaton ePDU G3 MA 1U 32A", Vendor.EATON, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Managed 1U, 32A/230V, 18×C13 + 6×C19"),
        DeviceModel("Eaton ePDU G3 MI 1U 32A", Vendor.EATON, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Metered Input 1U, 32A/230V, 18×C13 + 6×C19"),
    ],

    # ── Vertiv Geist — PDU ────────────────────────────────────────────────────
    (DeviceType.PDU, Vendor.VERTIV): [
        DeviceModel("Vertiv Geist rPDU2 15A", Vendor.VERTIV, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Intelligent 0U, 15A/120V, 16×NEMA 5-20R"),
        DeviceModel("Vertiv Geist rPDU2 30A", Vendor.VERTIV, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Intelligent 0U, 30A/208V, 24×C13 + 6×C19"),
    ],

    # ── Server Technology — PDU ───────────────────────────────────────────────
    (DeviceType.PDU, Vendor.SERVER_TECHNOLOGY): [
        DeviceModel("Sentry PT40", Vendor.SERVER_TECHNOLOGY, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Power Tower 0U, 30A/208V, 40 outlets"),
        DeviceModel("Sentry 4805-XLS", Vendor.SERVER_TECHNOLOGY, DeviceType.PDU,
                    [_g(_GE, 1)],
                    "Switched 1U, 30A/208V, 48 outlets"),
    ],

    # ── APC — Floor PDU / RPP ─────────────────────────────────────────────────
    (DeviceType.FLOOR_PDU, Vendor.APC): [
        DeviceModel("APC FlexPDU 40kVA", Vendor.APC, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "40kVA 3-phase, 6 breaker groups, floor-mounted"),
        DeviceModel("APC Galaxy RPP 80A", Vendor.APC, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "80A 3-phase RPP, 12 branch circuits, floor-mounted"),
    ],

    # ── Eaton — Floor PDU / RPP ───────────────────────────────────────────────
    (DeviceType.FLOOR_PDU, Vendor.EATON): [
        DeviceModel("Eaton PDU 80kVA", Vendor.EATON, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "80kVA 3-phase, floor-mounted, feeds rack PDUs"),
        DeviceModel("Eaton PDU 160kVA", Vendor.EATON, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "160kVA 3-phase, floor-mounted, feeds rack PDUs"),
    ],

    # ── Vertiv — Floor PDU / RPP ──────────────────────────────────────────────
    (DeviceType.FLOOR_PDU, Vendor.VERTIV): [
        DeviceModel("Vertiv Liebert MPX 60kVA", Vendor.VERTIV, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "60kVA 3-phase, 12×30A branch circuits, floor-mounted"),
        DeviceModel("Vertiv Liebert MPH2 24kVA", Vendor.VERTIV, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "24kVA 3-phase modular, wall/floor mounted"),
    ],

    # ── Raritan — Floor PDU ───────────────────────────────────────────────────
    (DeviceType.FLOOR_PDU, Vendor.RARITAN): [
        DeviceModel("Raritan PX3-5000 Floor 30A", Vendor.RARITAN, DeviceType.FLOOR_PDU,
                    [_g(_GE, 1)],
                    "30A/208V, 24×C19 outlets, floor-mounted"),
    ],
}
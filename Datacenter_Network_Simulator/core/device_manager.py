"""
Device Manager - Manages all simulated network devices.
"""
from __future__ import annotations
import math
import uuid
import random
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from enum import Enum


class DeviceType(str, Enum):
    ROUTER        = "router"
    SWITCH        = "switch"
    SERVER        = "server"
    FIREWALL      = "firewall"
    LOAD_BALANCER = "load_balancer"
    UPS           = "ups"
    PDU           = "pdu"
    FLOOR_PDU     = "floor_pdu"
    OOB_SWITCH    = "oob_switch"   # Out-of-band management switch
    SENSOR         = "sensor"        # Environmental sensor (temp/humidity)
    ENERGY_MONITOR = "energy_monitor"  # BACnet/IP energy intelligence platform
    GENERATOR      = "generator"     # Diesel/gas standby generator
    UTILITY_FEED   = "utility_feed"  # Utility service entrance -- revenue/ION meter (Modbus TCP)
    SWITCHGEAR     = "switchgear"    # LV main board / generator paralleling bus -- SNMP + Modbus
    ATS            = "ats"           # Automatic Transfer Switch -- SNMP (ASCO/Eaton/APC)
    MCC            = "mcc"           # Motor Control Center -- unprotected mechanical distribution
    MPP            = "mpp"           # Mechanical Power Panel -- per-hall CRAH panelboard, fed from an MCC
    RPP            = "rpp"           # Remote Power Panel -- passive breaker panel, no SNMP
    CRAH           = "crah"          # Computer Room Air Handler (chilled water) -- SNMP + BACnet (native comm card)
    CHILLER        = "chiller"       # Chiller unit (compressors + evaporator + condenser) -- BACnet only
    PUMP           = "pump"          # Chilled-/condenser-water pump (VFD) -- BACnet only
    COOLING_TOWER  = "cooling_tower" # Cooling tower (fan + basin) -- BACnet only
    VALVE          = "valve"         # Control/isolation valve (actuator position) -- BACnet only
    CDU            = "cdu"           # Coolant Distribution Unit (direct-to-chip liquid cooling) -- SNMP + BACnet (native comm card)
    MODBUS_GATEWAY = "modbus_gateway"  # Modbus TCP/RTU gateway (Moxa MGate class) -- fronts an RS-485 trunk of field transmitters
    BACNET_ROUTER  = "bacnet_router"    # BACnet/IP <-> MS/TP router (Loytec/Distech class) -- fronts an RS-485 trunk of field controllers


# Floor-standing / plant equipment: located by room/area, NOT mounted in an IT rack.
# Their rack_row/rack_num are only a synthetic room-grid coordinate, so sysLocation
# must NOT emit "Row/Rack/U" tokens for them (that falsely implies rack mounting).
FACILITY_TYPES = frozenset({
    DeviceType.GENERATOR,
    DeviceType.UTILITY_FEED,
    DeviceType.SWITCHGEAR,
    DeviceType.ATS,
    DeviceType.MCC,
    DeviceType.MPP,
    DeviceType.UPS,
    DeviceType.RPP,
    DeviceType.CRAH,
    DeviceType.CHILLER,
    DeviceType.PUMP,
    DeviceType.COOLING_TOWER,
    # NOTE: CDU is intentionally NOT here. In-rack CDUs (4U, e.g. CoolIT CHx)
    # are rack-mounted, so sysLocation / Redfish Placement SHOULD emit Row/Rack/U.
    DeviceType.VALVE,
    DeviceType.ENERGY_MONITOR,
    # Wall/rack-mounted in the plant room, not in an IT rack.
    DeviceType.MODBUS_GATEWAY,
    DeviceType.BACNET_ROUTER,
})


class Vendor(str, Enum):
    # Networking vendors (routers & switches)
    CISCO_SYSTEMS = "Cisco Systems"
    JUNIPER_NETWORKS = "Juniper Networks"
    ARISTA_NETWORKS = "Arista Networks"
    HPE = "Hewlett Packard Enterprise"
    EXTREME_NETWORKS = "Extreme Networks"
    HUAWEI = "Huawei Technologies"
    DELL = "Dell Technologies"
    # Server vendors
    LENOVO = "Lenovo"
    SUPERMICRO = "Supermicro"
    IBM = "IBM"
    # Security appliance vendors
    PALO_ALTO_NETWORKS = "Palo Alto Networks"
    # Load balancer vendors
    F5_NETWORKS = "F5 Networks"
    # UPS / PDU vendors
    APC               = "APC by Schneider Electric"
    EATON             = "Eaton"
    VERTIV            = "Vertiv (Liebert)"
    RARITAN           = "Raritan"
    SERVER_TECHNOLOGY = "Server Technology"
    # Energy monitoring
    VERDIGRIS = "Verdigris Technologies"
    # Generator vendors
    CUMMINS    = "Cummins"
    CATERPILLAR = "Caterpillar"
    KOHLER     = "Kohler Power"
    SCHNEIDER  = "Schneider Electric"
    # Transfer switches / paralleling switchgear
    ASCO       = "ASCO Power Technologies"
    # Cooling-plant vendors (chillers, pumps, towers, valves)
    CARRIER          = "Carrier"
    TRANE            = "Trane"
    DAIKIN           = "Daikin Applied"
    GRUNDFOS         = "Grundfos"
    ARMSTRONG        = "Armstrong Fluid Technology"
    BAC              = "Baltimore Aircoil Company"
    MARLEY           = "SPX Cooling (Marley)"
    BELIMO           = "Belimo"
    # Modbus TCP/RTU gateways — the box that brings an RS-485 trunk of field
    # transmitters onto Ethernet. Moxa MGate is the commodity choice; Schneider
    # Link150 and Eaton PXG occupy the same slot.
    MOXA             = "Moxa"
    # BACnet/IP <-> MS/TP routers. Loytec LINX is the commodity choice; Distech
    # ECLYPSE and Contemporary Controls BASrouter fill the same slot.
    LOYTEC           = "Loytec"
    JOHNSON_CONTROLS = "Johnson Controls"
    # Direct-to-chip CDU vendors
    COOLIT           = "CoolIT Systems"
    MOTIVAIR         = "Motivair"
    NVENT            = "nVent"


class InterfaceRole(str, Enum):
    """What an interface is FOR, which is not the same as what it is wired to.

    Real gear separates the two planes physically: front-panel switchports carry
    production traffic, while out-of-band management terminates on a dedicated
    port on a separate path (Nexus `mgmt0`, Arista `Management1`, Juniper `fxp0`,
    PAN `management`, F5 `mgmt`) or, on a server, the BMC NIC (iDRAC / iLO / XCC /
    IPMI). That separation is the point of OOB — the mgmt plane must survive a
    data-plane outage — so the role is a property of the PORT, fixed at build
    time, not something inferred from the links that happen to land on it.

    An unroled port is DATA: that is what the generator emits from
    interface_groups, and mgmt ports are added deliberately on top.
    """
    DATA = "data"
    MGMT = "mgmt"


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


# The dedicated baseboard-management-controller port each server vendor ships, by
# its real product name. This is NOT cosmetic: the name is what _MGMT_PORT_NAMES
# (fleet_lifecycle) matches to land a BMC edge on the right port, and what an
# operator sees in the port picker. A server without one has no out-of-band path —
# its Redfish/IPMI would have to ride the data plane, which defeats OOB.
BMC_PORT_NAME = {
    Vendor.DELL:          "iDRAC",
    Vendor.HPE:           "iLO",
    Vendor.LENOVO:        "XCC",
    Vendor.SUPERMICRO:    "IPMI",
    Vendor.IBM:           "IMM",
    Vendor.CISCO_SYSTEMS: "CIMC",
}
MGMT_PORT_SPEED = 1_000_000_000   # BMC and switch/router mgmt NICs are 1G throughout


# Facility / electrical / mechanical gear: EVERY port is management. This gear has no
# data plane at all — its only Ethernet is the monitoring card (SNMP / Modbus / BACnet),
# and nothing production ever lands there. So the role is a property of the TYPE, and
# these devices need no extra mgmt port: the NIC they already have IS the mgmt NIC.
#
# Mirrors FACILITY_TYPES in tools/set_iface_roles.py, the one-shot that stamped the
# curated topology, PLUS floor_pdu — that tool's set omits it only because the curated
# topology contains no floor_pdu to stamp, and a floor PDU is RPP-class distribution
# gear with a monitoring card. Deliberately NOT rpp, which is passive — see
# FACILITY_PASSIVE_TYPES below.
#
# Type-driven, never name-driven: set_iface_roles.py matched names because a one-shot
# migration over a fixed, inspected set of files can. At runtime that would drift —
# vendor conventions differ and a renamed port would silently change role.
FACILITY_MGMT_TYPES = frozenset({
    DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.UPS,
    DeviceType.ATS, DeviceType.MCC, DeviceType.MPP, DeviceType.SWITCHGEAR,
    DeviceType.UTILITY_FEED, DeviceType.GENERATOR, DeviceType.ENERGY_MONITOR,
    DeviceType.SENSOR, DeviceType.CRAH, DeviceType.CHILLER,
    DeviceType.COOLING_TOWER, DeviceType.PUMP, DeviceType.VALVE, DeviceType.CDU,
    # A Modbus gateway is one Ethernet port on the BMS management plane plus one
    # or more RS-485 trunks. The Ethernet side is the only part that gets an
    # address, and it is the reason the trunk's instruments no longer need one.
    DeviceType.MODBUS_GATEWAY,
    DeviceType.BACNET_ROUTER,
})


# Passive electrical gear: NO monitoring card, therefore no Ethernet port at all.
# A bare RPP (remote power panel / branch panelboard) is breakers on a busbar — a main
# breaker and 12-42 branch breakers feeding rack PDUs, with no electronics to network.
# It has no IP, no SNMP agent (see _NO_SNMP_TYPES in core/snmprec_generator.py), and no
# BACnet/Modbus points; the only way to see its load is the EV2 sub-meter clamped to its
# output conductors, which is its own device.
#
# Panels that DO ship branch-circuit monitoring (Schneider PowerLogic BCPM, Vertiv
# PowerIT, Packet Power, Starline) are modelled as MPP — that type stays in
# FACILITY_MGMT_TYPES and keeps its metering NIC on the OOBM plane.
#
# This wins over every other port rule: a passive panel gets zero interfaces whatever a
# saved topology, the model registry, or a caller-supplied interface_count claims.
# Topologies written before this carry a phantom, uncabled, IP-less eth0 here.
FACILITY_PASSIVE_TYPES = frozenset({DeviceType.RPP})


# Facility gear that ships TWO network management interfaces, for a redundant network
# path or to daisy-chain/cascade units on one drop: managed rack PDUs (Raritan PX3
# ETH1/ETH2, ServerTech PRO2 Link, Vertiv Geist), UPS with dual NMC slots, ATS and
# switchgear. Mirrors TARGET in tools/add_redundant_mgmt_port.py. Deliberately NOT
# generator (genset controllers are single-Ethernet) and NOT mcc (mechanical, not
# critical power) — those keep one.
FACILITY_REDUNDANT_MGMT_TYPES = frozenset({
    DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.UPS,
    DeviceType.ATS, DeviceType.SWITCHGEAR,
})


def facility_mgmt_nic_count(device_type: "DeviceType") -> int:
    """How many monitoring NICs this facility device physically has: 0 for passive gear
    with no card at all, 2 for gear with a redundant/cascade NMC, else 1.

    The TYPE decides this, not the model registry — a registry entry describes a data
    fit-out, and this gear has no data plane to describe. It is also why the
    interface_count fallback must not apply here: a caller-supplied 4 would give a CRAH
    four Ethernet ports when it has one BACnet/Modbus card."""
    if device_type in FACILITY_PASSIVE_TYPES:
        return 0
    return 2 if device_type in FACILITY_REDUNDANT_MGMT_TYPES else 1


def mgmt_port_name(device_type: "DeviceType", vendor: "Vendor") -> Optional[str]:
    """The dedicated out-of-band management port this device ships, or None when it
    has none. Mirrors tools/add_network_mgmt_port.py's mgmt_name(), which stamped
    the curated topology — the two must agree or a hand-added switch gets a port the
    curated ones don't have.

    Real network gear has a management Ethernet port separate from the numbered data
    switchports (Nexus `mgmt0`, Arista `Management1`, Juniper `fxp0`, PAN
    `management`, F5 `mgmt`); a server has its BMC NIC. That separation IS the point
    of OOB — the management plane must survive a data-plane outage — so it is a
    dedicated port, not a data port that happens to carry a console.

    Deliberately None for OOB_SWITCH and facility gear:
      * An OOB switch BUILDS the management plane. Its management-layer links are its
        DATA plane (uplinks to the OOB cores, peer links, access ports to BMCs), not
        consoles. Giving it a mgmt port would invent a console nothing plugs into and
        would hide an access port from the picker.
      * A PDU/CRAH/sensor has ONE network port and that port is already its
        management NIC — it needs no second one.
    """
    if device_type == DeviceType.SERVER:
        return BMC_PORT_NAME.get(vendor)
    if device_type == DeviceType.FIREWALL:
        return "management"               # PAN-OS names it by function, not vendor
    if device_type == DeviceType.LOAD_BALANCER:
        return "mgmt"                     # F5 TMOS
    if device_type in (DeviceType.SWITCH, DeviceType.ROUTER):
        if vendor == Vendor.ARISTA_NETWORKS:
            return "Management1"
        if vendor == Vendor.JUNIPER_NETWORKS:
            return "fxp0"
        return "mgmt0"                    # Cisco / Dell / generic switch + router
    return None


def model_interface_groups(device_type: "DeviceType", vendor: "Vendor",
                           model_name: str) -> Optional[List[dict]]:
    """The DATA-port fit-out of a real SKU, from the model registry, or None when
    the model is unknown.

    The SKU decides how many ports a box has and how fast they are — a caller-
    supplied interface_count cannot know that, and guessing yields a device whose
    ports contradict its own sysDescr. The BMC port is deliberately NOT here: the
    registry describes the data NICs, and management is a separate plane added on
    top by _generate_interfaces.

    Imported lazily: core.device_models imports this module, so a module-level
    import would be circular."""
    if not model_name:
        return None
    try:
        from core.device_models import DEVICE_MODELS
    except Exception:
        return None
    for m in DEVICE_MODELS.get((device_type, vendor), []):
        if m.name == model_name:
            return [dict(g) for g in m.interface_groups]
    return None


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
    Vendor.APC:               "1.3.6.1.4.1.318.1.3.2.7",
    Vendor.EATON:             "1.3.6.1.4.1.534.2.14",
    Vendor.VERTIV:            "1.3.6.1.4.1.476.1.42.2.10.2.1.1",
    Vendor.RARITAN:           "1.3.6.1.4.1.13742.6",
    Vendor.SERVER_TECHNOLOGY: "1.3.6.1.4.1.1718.3.1",
    Vendor.VERDIGRIS:         "1.3.6.1.4.1.57628.1",   # Verdigris Technologies
}

VENDOR_SYSDESCR = {
    Vendor.CISCO_SYSTEMS:   "Cisco IOS XE Software, Version 17.9.4a, RELEASE SOFTWARE (fc3)",
    Vendor.JUNIPER_NETWORKS:"Juniper Networks, Inc. MX480 Internet Router, JUNOS 22.4R1",
    Vendor.ARISTA_NETWORKS: "Arista Networks EOS version 4.28.3M running on an Arista Networks DCS-7050CX3",
    Vendor.HPE:             "HPE FlexFabric 5945 JH175A, Comware Software Version 7.1.070",
    Vendor.EXTREME_NETWORKS:"ExtremeXOS version 31.7.1.4 v31.7.1.4-patch1-4 by release-manager",
    Vendor.HUAWEI:          "Huawei Versatile Routing Platform Software VRP (R) version V200R010C10SPC600",
    Vendor.DELL:            "Enterprise SONiC Distribution by Dell Technologies - 4.2.0 - HwSku: DellEMC-S5248f-P-25G-DPB - Distribution: Debian - Kernel: 5.10.0-18-2-amd64",
    Vendor.LENOVO:          "Lenovo ThinkSystem SR650 V2, Red Hat Enterprise Linux 8.7",
    Vendor.SUPERMICRO:      "Supermicro SYS-220U-TNR, Ubuntu Server 22.04 LTS",
    Vendor.IBM:             "IBM System x3850 X6, Red Hat Enterprise Linux 9.2",
    Vendor.PALO_ALTO_NETWORKS: "Palo Alto Networks PAN-OS, Version 11.0.2",
    Vendor.F5_NETWORKS:     "F5 Networks BIG-IP, TMOS Version 17.1.0",
    Vendor.APC:               "APC Web/SNMP Management Card (AP9630), firmware v6.9.6, APC Smart-UPS",
    Vendor.EATON:             "Eaton Network Management Card 2, firmware version 2.6, Eaton 9PX UPS",
    Vendor.VERTIV:            "Liebert IntelliSlot Web/SNMP Card, firmware version 1.55, Liebert GXT5 UPS",
    Vendor.RARITAN:           "Raritan PX3 Rack PDU SNMP Agent, firmware version 3.7.0",
    Vendor.SERVER_TECHNOLOGY: "Server Technology Sentry SNMP Agent, firmware version 8.2a",
    Vendor.VERDIGRIS:         "Verdigris EV2 Energy Intelligence Platform, BACnet/IP, firmware 2.4.1",
    Vendor.CUMMINS:           "Cummins PowerCommand 3.3, SNMP Agent v2.1, Diesel Standby Generator",
    Vendor.CATERPILLAR:       "Caterpillar EMCP 4.4B, SNMP Agent v1.4, Diesel Generator Set",
    Vendor.KOHLER:            "Kohler Decision-Maker 3500, SNMP Agent v1.0, Standby Generator",
    Vendor.SCHNEIDER:         "Schneider Electric Remote Power Panel -- passive distribution, no SNMP agent",
}

# Per-model sysDescr overrides — more specific than vendor defaults.
# Cisco NX-OS (Nexus) and IOS XR (ASR 9K) differ significantly from IOS XE.
MODEL_SYSDESCR = {
    # Cisco IOS XE — ISR/ASR 1K routers
    "Cisco ISR 4321":   "Cisco IOS XE Software, Version 17.9.4a, RELEASE SOFTWARE (fc3)",
    "Cisco ISR 4431":   "Cisco IOS XE Software, Version 17.9.4a, RELEASE SOFTWARE (fc3)",
    "Cisco ASR 1001-X": "Cisco IOS XE Software, Version 17.9.4a, RELEASE SOFTWARE (fc3)",
    # Cisco IOS XR — ASR 9K routers
    "Cisco ASR 9001":   "Cisco IOS XR Software, Version 7.9.1, Copyright (c) 2013-2023 by Cisco Systems, Inc.",
    "Cisco ASR 9904":   "Cisco IOS XR Software, Version 7.9.1, Copyright (c) 2013-2023 by Cisco Systems, Inc.",
    # Cisco IOS / IOS XE — Catalyst switches
    "Cisco Catalyst 2960-X-24TS": "Cisco IOS Software, Version 15.2(7)E6, RELEASE SOFTWARE (fc2)",
    "Cisco Catalyst 3850-48":     "Cisco IOS XE Software, Version 16.12.7, RELEASE SOFTWARE (fc3)",
    "Cisco Catalyst 9300-48P":    "Cisco IOS XE Software, Version 17.12.1, RELEASE SOFTWARE (fc3)",
    # Cisco NX-OS — Nexus switches
    "Cisco Nexus 9372PX":    "Cisco NX-OS(tm) n9000, Software (n9000-dk9), Version 10.3(2), RELEASE SOFTWARE",
    "Cisco Nexus 93180YC-FX":"Cisco NX-OS(tm) n9000, Software (n9000-dk9), Version 10.3(2), RELEASE SOFTWARE",
    "Cisco Nexus 9336C-FX2": "Cisco NX-OS(tm) n9000, Software (n9000-dk9), Version 10.4(1), RELEASE SOFTWARE",
    "Cisco Nexus 9364C":     "Cisco NX-OS(tm) n9000, Software (n9000-dk9), Version 10.4(1), RELEASE SOFTWARE",
    # Dell Enterprise SONiC — switches and routers
    "Dell S5248F-ON":              "Enterprise SONiC Distribution by Dell Technologies - 4.2.0 - HwSku: DellEMC-S5248f-P-25G-DPB - Distribution: Debian - Kernel: 5.10.0-18-2-amd64",
    "Dell S5296F-ON":              "Enterprise SONiC Distribution by Dell Technologies - 4.2.0 - HwSku: DellEMC-S5296f-P-25G-DPB - Distribution: Debian - Kernel: 5.10.0-18-2-amd64",
    "Dell Z9264F-ON":              "Enterprise SONiC Distribution by Dell Technologies - 4.2.0 - HwSku: DellEMC-Z9264f-ON - Distribution: Debian - Kernel: 5.10.0-18-2-amd64",
    "Dell EMC PowerSwitch Z9332F-ON": "Enterprise SONiC Distribution by Dell Technologies - 4.2.0 - HwSku: DellEMC-Z9332f-ON - Distribution: Debian - Kernel: 5.10.0-18-2-amd64",
    # APC Rack PDU
    "APC AP8941":      "APC Rack PDU 2G, Switched, ZeroU, 30A, 208V, (21)C13&(3)C19, NMC3 fw v1.4.2",
    "APC AP8886":      "APC Rack PDU 2G, Metered, ZeroU, 22.0kW(32A), 230V 3-phase, (30)C13&(12)C19, NMC3 fw v1.4.2",
    "APC AP8959":      "APC Rack PDU 2G, Switched, 1U, 30A, 208V, (12)C13&(4)C19, NMC3 fw v1.4.2",
    "APC AP8681":      "APC Rack PDU 2G, Metered-by-Outlet, 1U, 16A, 230V, (12)C13&(4)C19, NMC3 fw v1.4.2",
    "APC AP8865":      "APC Rack PDU 2G, Metered, ZeroU, 8.6kW, 208V 3-phase, 30A, (36)C13&(6)C19&(2)5-20R, NMC3 fw v1.4.2",
    # Raritan PX3
    "Raritan PX3-5878":   "Raritan PX3 Rack PDU, 0U, 32A, 415V 3-phase, (24)C13&(12)C19, fw 3.7.0",
    "Raritan PX3-5190R":  "Raritan PX3 Rack PDU, 1U, 30A, 208V, (24)C13&(6)C19, fw 3.7.0",
    "Raritan PX3-5161R":  "Raritan PX3 Rack PDU, 1U, 16A, 208V, (12)C13&(4)C19, fw 3.7.0",
    "Raritan PX2-5170CR": "Raritan PX2 Rack PDU, 0U, 30A, 208V, (24)C13&(6)C19, fw 3.5.20",
    # Eaton ePDU G3
    "Eaton ePDU G3 MA 1U 16A":  "Eaton ePDU G3 Managed, 1U, 16A, 230V, (12)C13&(4)C19, fw 3.0.8",
    "Eaton ePDU G3 MA 1U 32A":  "Eaton ePDU G3 Managed, 1U, 32A, 230V, (18)C13&(6)C19, fw 3.0.8",
    "Eaton ePDU G3 MI 1U 32A":  "Eaton ePDU G3 Metered Input, 1U, 32A, 230V, (18)C13&(6)C19, fw 3.0.8",
    # Vertiv Geist rPDU2
    "Vertiv Geist rPDU2 15A":   "Geist rPDU2 Intelligent Rack PDU, 0U, 15A, 120V, (16)NEMA 5-20R, fw 4.6.0",
    "Vertiv Geist rPDU2 30A":   "Geist rPDU2 Intelligent Rack PDU, 0U, 30A, 208V, (24)C13&(6)C19, fw 4.6.0",
    # Server Technology Sentry
    "Sentry PT40":     "Server Technology Sentry Power Tower, 40-outlet, 30A, 208V, fw 8.2a",
    "Sentry 4805-XLS": "Server Technology Sentry 4800 series, 1U, 48-outlet, 30A, 208V, fw 8.2a",
    # APC Floor PDU / RPP
    "APC FlexPDU 40kVA":  "APC FlexPDU 40kVA, 3-phase, (6) 3-phase breakers, NMC3 fw v1.4.2",
    "APC Galaxy RPP 80A": "APC Galaxy Remote Power Panel, 80A, 3-phase, (12) branch circuits, NMC3 fw v1.4.2",
    # Eaton Floor PDU
    "Eaton PDU 80kVA":    "Eaton Power Distribution Unit, 80kVA, 3-phase, floor-mounted, fw 3.0.8",
    "Eaton PDU 160kVA":   "Eaton Power Distribution Unit, 160kVA, 3-phase, floor-mounted, fw 3.0.8",
    # Vertiv Liebert Floor PDU
    "Vertiv Liebert MPX 60kVA":  "Liebert MPX Floor PDU, 60kVA, 3-phase, (12) 30A branch circuits, fw 2.1.0",
    "Vertiv Liebert MPH2 24kVA": "Liebert MPH2 Modular Power Hub, 24kVA, 3-phase, wall/floor mount, fw 2.1.0",
    # Raritan Floor PDU
    "Raritan PX3-5000 Floor 30A": "Raritan PX3 Floor PDU, 30A, 208V, (24)C19 outlets, fw 3.7.0",
    # OOB Management Switches
    "Cisco Catalyst 1000-48T": "Cisco IOS Software, Version 15.2(7)E6, RELEASE SOFTWARE (fc2), Catalyst 1000 48-port",
    "Cisco Catalyst 1000-24T": "Cisco IOS Software, Version 15.2(7)E6, RELEASE SOFTWARE (fc2), Catalyst 1000 24-port",
    "HPE Aruba 2530-48G":      "HP J9775A Aruba 2530-48G Switch, ProCurve OS, Version WB.16.10.0023",
    "HPE Aruba 2530-24G":      "HP J9776A Aruba 2530-24G Switch, ProCurve OS, Version WB.16.10.0023",
    "Dell N1148T-ON":          "Dell EMC Networking N1148T-ON, DNOS 6.5.1.9, 48-port GbE + 4-port SFP+",
    "Dell N1124T-ON":          "Dell EMC Networking N1124T-ON, DNOS 6.5.1.9, 24-port GbE + 4-port SFP+",
    # Environmental Sensors
    "Raritan DPX2-T1H1":  "Raritan DPX2 Environmental Sensor, 1× temperature, 1× humidity, fw 3.7.0",
    "Raritan DPX2-T3H1":  "Raritan DPX2 Environmental Sensor, 3× temperature, 1× humidity, fw 3.7.0",
    "Raritan DPX2-CC2":   "Raritan DPX2 Contact Closure Sensor, 2× contact closure (water rope + temp probe), fw 3.7.0",
    "Vertiv Geist GTHD":  "Geist Temperature/Humidity/Dewpoint Sensor, fw 4.6.0",
    "Vertiv Geist IMD-3": "Geist Intelligent Rack Monitoring Device, 3-sensor, fw 4.6.0",
    "APC NetBotz 250":    "APC NetBotz Room Monitor 250, temperature/humidity/airflow, fw 5.2.0",
    "APC NetBotz 355":    "APC NetBotz Rack Monitor 355, temperature/humidity/camera, fw 5.2.0",
}

# Per-model sysObjectID overrides (vendor-level fallback in VENDOR_SYSOID).
MODEL_SYSOID = {
    "Cisco ISR 4321":         "1.3.6.1.4.1.9.1.1861",
    "Cisco ISR 4431":         "1.3.6.1.4.1.9.1.1863",
    "Cisco ASR 1001-X":       "1.3.6.1.4.1.9.1.1867",
    "Cisco ASR 9001":         "1.3.6.1.4.1.9.1.1646",
    "Cisco ASR 9904":         "1.3.6.1.4.1.9.1.1681",
    "Cisco Catalyst 2960-X-24TS": "1.3.6.1.4.1.9.1.1208",
    "Cisco Catalyst 3850-48":     "1.3.6.1.4.1.9.1.1790",
    "Cisco Catalyst 9300-48P":    "1.3.6.1.4.1.9.1.2482",
    "Cisco Nexus 9372PX":     "1.3.6.1.4.1.9.12.3.1.3.1291",
    "Cisco Nexus 93180YC-FX": "1.3.6.1.4.1.9.12.3.1.3.1649",
    "Cisco Nexus 9336C-FX2":  "1.3.6.1.4.1.9.12.3.1.3.1268",
    "Cisco Nexus 9364C":      "1.3.6.1.4.1.9.12.3.1.3.1538",
    # Dell PowerSwitch (SONiC) OIDs
    "Dell S5248F-ON":              "1.3.6.1.4.1.674.10895.3224",
    "Dell S5296F-ON":              "1.3.6.1.4.1.674.10895.3254",
    "Dell Z9264F-ON":              "1.3.6.1.4.1.674.10895.3272",
    "Dell EMC PowerSwitch Z9332F-ON": "1.3.6.1.4.1.674.10895.3278",
    # APC Rack PDU OIDs
    "APC AP8941":      "1.3.6.1.4.1.318.1.3.5.1",
    "APC AP8886":      "1.3.6.1.4.1.318.1.3.5.4",
    "APC AP8959":      "1.3.6.1.4.1.318.1.3.5.8",
    "APC AP8681":      "1.3.6.1.4.1.318.1.3.5.16",
    "APC AP8865":      "1.3.6.1.4.1.318.1.3.5.17",
    # Raritan PDU OIDs
    "Raritan PX3-5878":   "1.3.6.1.4.1.13742.6.3.2.28",
    "Raritan PX3-5190R":  "1.3.6.1.4.1.13742.6.3.2.21",
    "Raritan PX3-5161R":  "1.3.6.1.4.1.13742.6.3.2.22",
    "Raritan PX2-5170CR": "1.3.6.1.4.1.13742.6.3.2.14",
    # Eaton ePDU OIDs
    "Eaton ePDU G3 MA 1U 16A":  "1.3.6.1.4.1.534.6.6.7.1",
    "Eaton ePDU G3 MA 1U 32A":  "1.3.6.1.4.1.534.6.6.7.2",
    "Eaton ePDU G3 MI 1U 32A":  "1.3.6.1.4.1.534.6.6.7.3",
    # Vertiv Geist rPDU OIDs
    "Vertiv Geist rPDU2 15A":   "1.3.6.1.4.1.21239.5.1.1",
    "Vertiv Geist rPDU2 30A":   "1.3.6.1.4.1.21239.5.1.2",
    # Server Technology Sentry OIDs
    "Sentry PT40":     "1.3.6.1.4.1.1718.3.1.1",
    "Sentry 4805-XLS": "1.3.6.1.4.1.1718.3.1.3",
    # APC Floor PDU / RPP OIDs
    "APC FlexPDU 40kVA":  "1.3.6.1.4.1.318.1.3.5.32",
    "APC Galaxy RPP 80A": "1.3.6.1.4.1.318.1.3.5.33",
    # Eaton Floor PDU OIDs
    "Eaton PDU 80kVA":    "1.3.6.1.4.1.534.6.6.7.10",
    "Eaton PDU 160kVA":   "1.3.6.1.4.1.534.6.6.7.11",
    # Vertiv Liebert Floor PDU OIDs
    "Vertiv Liebert MPX 60kVA":  "1.3.6.1.4.1.476.1.42.2.10.3.1.1",
    "Vertiv Liebert MPH2 24kVA": "1.3.6.1.4.1.476.1.42.2.10.3.1.2",
    # Raritan Floor PDU OIDs
    "Raritan PX3-5000 Floor 30A": "1.3.6.1.4.1.13742.6.3.2.50",
    # OOB Management Switch OIDs
    "Cisco Catalyst 1000-48T": "1.3.6.1.4.1.9.1.2776",
    "Cisco Catalyst 1000-24T": "1.3.6.1.4.1.9.1.2775",
    "HPE Aruba 2530-48G":      "1.3.6.1.4.1.11.2.3.7.11.136",
    "HPE Aruba 2530-24G":      "1.3.6.1.4.1.11.2.3.7.11.137",
    "Dell N1148T-ON":          "1.3.6.1.4.1.674.10895.5000",
    "Dell N1124T-ON":          "1.3.6.1.4.1.674.10895.5001",
    # Environmental Sensor OIDs
    "Raritan DPX2-T1H1":  "1.3.6.1.4.1.13742.8.1.1",
    "Raritan DPX2-T3H1":  "1.3.6.1.4.1.13742.8.1.2",
    "Raritan DPX2-CC2":   "1.3.6.1.4.1.13742.8.1.3",
    "Vertiv Geist GTHD":  "1.3.6.1.4.1.21239.5.2.1",
    "Vertiv Geist IMD-3": "1.3.6.1.4.1.21239.5.2.2",
    "APC NetBotz 250":    "1.3.6.1.4.1.318.1.3.8.1",
    "APC NetBotz 355":    "1.3.6.1.4.1.318.1.3.8.2",
}

# OS name + version for server vendors (used in sysDescr and hrSWInstalled).
SERVER_OS_INFO = {
    Vendor.CISCO_SYSTEMS: ("VMware ESXi",              "8.0.1"),
    Vendor.HPE:           ("Red Hat Enterprise Linux",  "8.7"),
    Vendor.DELL:          ("Ubuntu Server",             "22.04 LTS"),
    Vendor.LENOVO:        ("Windows Server",            "2022 Standard"),
    Vendor.SUPERMICRO:    ("Ubuntu Server",             "22.04 LTS"),
    Vendor.IBM:           ("Red Hat Enterprise Linux",  "9.2"),
}


@dataclass
class Outlet:
    """One receptacle on a rack PDU — what a power cord actually plugs into.

    Indexed 1-based to match the number silk-screened on the PDU and the outlet
    index in the vendor MIBs (APC rPDU2OutletMeteredStatusTable /
    rPDU2OutletSwitchedControlTable, Raritan PDU2-MIB outletTable, ServerTech
    Sentry3/4-MIB outletTable) — an off-by-one here would misname every outlet an
    operator reads over SNMP.

    `bank` is the branch-breaker group: outlets share an overcurrent device, so a
    bank trip drops every outlet behind it. `phase` is the pair the bank is fed
    from on a 3-phase PDU, which is what makes phase balance a real concern.
    Neither is simulated yet — they are carried so branch-breaker and
    phase-balance work does not need a re-migration.
    """
    index: int
    type: str                 # "C13" | "C19" — IEC 60320 receptacle
    bank: int = 1
    phase: str = "L1"         # "L1" 1-phase, or "L1-L2"/"L2-L3"/"L3-L1" on 3-phase
    rated_a: float = 10.0     # C13 = 10A, C19 = 16A

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PowerSupply:
    """One PSU in a load device — the other end of the cord.

    Redfish exposes these as Chassis/{id}/Power#/PowerSupplies[{MemberId}]; IPMI
    as PSU status sensors.

    Inventory only — deliberately no `feed` or `line_v` here. Which side a PSU is
    corded to and what voltage it sees are facts about the CORD, so they are read
    from the edges (TopologyEngine.power_feeds) on demand. Cached here they would
    be right until the first re-cord and wrong forever after — the same trap as
    Interface.connected_to_device.
    """
    index: int
    name: str                 # "PSU1" / "PSU2"
    inlet: str = "C14"        # "C14" (takes a C13 outlet) | "C20" (takes a C19)
    capacity_w: int = 1100

    def to_dict(self) -> dict:
        return asdict(self)


# Outlet layout per rack-PDU SKU: (C13 count, C19 count, phases, input A, input V).
#
# `volts` is the INPUT nameplate (line-to-line for 3-phase), NOT what an outlet
# delivers — see outlet_voltage(), which does that conversion.
#
# PROVENANCE: entries marked "verified" were checked against the vendor's own datasheet
# on 2026-07-17 and the source is named. The rest are UNVERIFIED and were inherited from
# MODEL_SYSDESCR — which is a string this repo wrote, so agreeing with it proves nothing.
# That circularity hid three wrong SKUs: the AP8865 was modelled as a 415V/32A 22 kW
# strip with 21 C13 when it is really a 208V/30A 8.6 kW strip with 36 C13, and the
# AP8886 was modelled as a 20A/208V unit when it is really the 22 kW/32A 3-phase one
# this fleet needs. Do not add or trust a row here without a datasheet.
#
# banks/phases: the phase count and current rating come from the SKU. The BANK
# COUNT is a modelled convention, NOT a datasheet figure: a 1-phase 30A rPDU is
# split into 2 breakered banks and a 3-phase into 6 (two per phase pair), which is
# the common layout but varies by SKU. Confirm against the datasheet before relying
# on bank identity for breaker simulation.
PDU_OUTLET_CATALOG = {
    # model_name:        (C13 count, C19 count, phases, rating A, volts)
    # VERIFIED — se.com/us/en/product/AP8941: "(21) C13 & (3) C19", 30A, 200/208V,
    # NEMA L6-30P (single-phase), 4992 VA.
    "APC AP8941":        (21,  3, 1, 30, 208),
    # VERIFIED — apc.com/us/en/product/AP8865: "0U, 8.6kW, 208V, 3 phase, 30A, 36 C13,
    # 6 C19 and 2 NEMA 5-20R outlets". The 2 NEMA 5-20R are not modelled (nothing here
    # takes one). Was fabricated as (21,12,3,32,415)/22kW — a strip that does not exist.
    "APC AP8865":        (36,  6, 3, 30, 208),
    # VERIFIED — apc.com .../P-AP8886 + AP8886 datasheet: "0U, 3PH, 22kW 230V 32A or
    # 17.3kW 230V 24A, x30 C13 and x12 C19, IEC 309 3P+N+PE". 230V is the OUTLET (L-N)
    # voltage of a 400V wye, so the input nameplate here is 400 (400/sqrt(3) = 231V).
    # Was fabricated as a 20A/208V (21)C13 unit.
    "APC AP8886":        (30, 12, 3, 32, 400),
    "APC AP8959":        (12,  4, 1, 30, 208),   # UNVERIFIED
    "Raritan PX2-5170CR": (24,  6, 1, 30, 208),
    "Raritan PX3-5878":  (24, 12, 3, 32, 415),
    "Raritan PX3-5190R": (24,  6, 1, 30, 208),
    "Raritan PX3-5161R": (12,  4, 1, 16, 208),
    "Sentry PT40":       (40,  0, 1, 30, 208),
    "Sentry 4805-XLS":   (48,  0, 1, 30, 208),
}

# 3-phase rPDUs alternate their banks across the phase pairs in this order.
_PHASE_PAIRS = ("L1-L2", "L2-L3", "L3-L1")


def outlet_voltage(model_name: str) -> int:
    """What a load actually sees at this PDU's outlet.

    NOT the PDU's headline figure. A 415V 3-phase rPDU is a WYE unit: its outlets
    are wired line-to-neutral, so a C13 on it delivers 415/sqrt(3) = 240V, not 415V
    — nothing in a rack is fed 415V. A 208V unit is line-to-line and delivers its
    208V as-is. Reporting the nameplate here would tell an operator their server is
    running on 415V, which would be alarming and wrong.
    """
    spec = PDU_OUTLET_CATALOG.get(model_name)
    if not spec:
        return 230                      # unknown SKU: nominal
    _c13, _c19, phases, _a, volts = spec
    # 3-phase is NOT enough to imply a line-to-neutral outlet. Two different animals:
    #   400/415V WYE (EU/hyperscale): outlets are wired L-N -> 400/sqrt(3) = 231V.
    #   208V 3-phase (US):            outlets are wired L-L  -> 208V as-is. A C13 here
    #                                 sees 208V; L-N would be 120V, which no C13 load in
    #                                 a rack runs on.
    # Testing `phases == 3` alone gave the real 208V/3-phase AP8865 a 120V outlet. The
    # split is the voltage, not the phase count.
    if phases == 3 and volts >= 380:
        return round(volts / math.sqrt(3))
    return volts


def feed_side(pdu_name: str) -> str:
    """"A" / "B" from a PDU's name, or "" when it carries no side.

    The leading code in a device name is the functional role the runtime already
    parses (PDUA/PDUB are an A/B pair — see canvas_layout's PDUA/PDUB -> PDU
    normalisation), so this reads the same signal rather than inventing a second
    source of truth. Renaming a PDU out of that scheme changes its feed side, which
    is why the naming rule is what it is.
    """
    n = (pdu_name or "").upper()
    if n.startswith("PDUA"):
        return "A"
    if n.startswith("PDUB"):
        return "B"
    return ""

# How many PSUs a load device has, by type. IT gear is dual-corded (1+1) — that is
# what the 2N A/B feed exists for. Devices absent from this map get none:
#   sensor  — a Raritan DPX2 has no PSU. It plugs into the PDU's SENSOR port
#             (RJ-12/RJ-45) and is powered over it, which is why it is single-fed.
#             Giving it a PSU would invent a cord that does not exist.
#   pdu/rpp/ups and other distribution gear — they SUPPLY power; their own feed is
#             an upstream breaker position, not an outlet, and is out of scope here.
PSU_COUNT_BY_TYPE = {
    "server": 2, "switch": 2, "oob_switch": 2, "router": 2,
    "firewall": 2, "load_balancer": 2, "cdu": 2,
}

# A C13 outlet / C14 inlet is rated 10A; derated to 80% for a continuous load that
# is 8A — ~1.66 kW at 208V. Above that the cord steps up to C19/C20 (16A).
C13_CONTINUOUS_W = 1660


# Chassis fan speed range (min duty RPM, full duty RPM), keyed by rack height.
#
# Fan RPM is a property of the FAN, and fan diameter is set by how much room the
# chassis has. A 1U lid leaves ~40 mm; a 4U leaves 80-92 mm. To push air through the
# same restrictive heatsink stack, the small fan must spin several times faster —
# which is why a 1U server screams at 18-20 krpm under load while a 4U at the same
# load sits under 9 krpm. Modelling every server on one 3000-7000 RPM curve got the
# ratio between load levels right but every absolute number wrong: a real 1U idles
# above where that curve topped out.
#
# Representative of 2-socket rack servers at min duty and full duty (Dell R6xx/R7xx,
# HPE DL360/DL380, Supermicro 1U/2U service manuals). Deliberately the FAN's range,
# not a specific SKU's — exact tables are per-model and per-fan-part-number.
FAN_RPM_BY_U = {
    1: (7000, 20000),   # 40 mm high-static-pressure
    2: (4800, 14000),   # 60 mm
    3: (3800, 11000),   # 60-80 mm
    4: (3000,  9000),   # 80-92 mm
}
_FAN_RPM_DEFAULT = FAN_RPM_BY_U[2]


def fan_rpm_range(model_name: str = "") -> tuple[int, int]:
    """(min duty, full duty) chassis fan RPM for a server SKU, by its rack height.

    Chassis heights over 4U clamp to the 4U profile — bigger boxes take bigger fans,
    but the curve flattens out rather than continuing to fall. device_models is
    imported lazily: it imports this module, so a module-level import would cycle."""
    try:
        from core.device_models import MODEL_U_HEIGHT
    except Exception:
        return _FAN_RPM_DEFAULT
    u = MODEL_U_HEIGHT.get(model_name or "")
    if not u:
        return _FAN_RPM_DEFAULT
    return FAN_RPM_BY_U.get(min(u, 4), _FAN_RPM_DEFAULT)


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
    in_discards: int = 0
    out_discards: int = 0
    mac_address: str = field(default_factory=lambda: ":".join(
        f"{random.randint(0,255):02x}" for _ in range(6)))
    connected_to_device: Optional[str] = None
    connected_to_iface: Optional[int] = None
    # Data or dedicated out-of-band mgmt port — see InterfaceRole. Last field so a
    # topology written before roles existed still loads: the default fills in.
    role: str = InterfaceRole.DATA.value

    @property
    def is_mgmt(self) -> bool:
        return self.role == InterfaceRole.MGMT.value

    def to_dict(self) -> dict:
        return asdict(self)


# Realistic full-load nameplate power (W) by device type. Used to fill
# power_draw_w when a topology leaves it 0 so the live power cascade
# (PDU / UPS / EV2 / PUE) reflects real IT load. Only IT loads feed the cascade;
# infra (pdu/ups/rpp/generator/plant) carry power, they don't add it, so they
# stay 0 here.
DEFAULT_NAMEPLATE_W = {
    DeviceType.SERVER:        550,   # modern 2U dual-socket at full load
    DeviceType.SWITCH:        250,   # ToR / access switch
    DeviceType.ROUTER:        450,
    DeviceType.FIREWALL:      500,   # NGFW appliance
    DeviceType.LOAD_BALANCER: 400,
    DeviceType.OOB_SWITCH:    90,    # 1U management switch
    DeviceType.SENSOR:        10,
}
# Per-SKU nameplate draw (W) for the server models the app actually offers
# (webui/src/data/deviceConstants.ts → MODELS). Keyed by a distinctive lowercase
# substring of the model string; the first key contained in the model wins, so
# more-specific keys are listed before less-specific ones (gen11 before gen10).
#
# NOTE on what "nameplate" means here: these are representative FULL-LOAD
# operating draws at a common CPU/DIMM config — the value the sim uses as the
# power_draw_w nominal (live telemetry scales 0.55–1.0× it, and the rack power
# budget sums it). They are NOT the PSU-label max (that's the redundant supply
# rating, ~1.5–2× higher and mostly stranded) and NOT a specific SKU's exact
# vendor-calculator figure — real draw swings widely with CPU TDP, DIMM count,
# drives and especially GPUs. Treat as a realistic default, override per device
# via power_draw_w when an exact figure is known.
#
# Rough tiers: 1U 2-socket ~450–550, 2U 2-socket ~650–750, 4U/4-socket
# ~1100–1400, dense-GPU chassis ~6000. POWER/RISC boxes run hotter than x86.
_MODEL_NAMEPLATE_W = {
    # Cisco UCS
    "ucs c220":      500,   # 1U 2S
    "ucs c240":      650,   # 2U 2S
    "ucs b200":      500,   # 2S blade (chassis-fed; represent per-blade)
    # HPE ProLiant  (gen11 before gen10 so the more-specific key matches first)
    "dl380a gen11": 2800,   # 4U 4-GPU, direct liquid cooled — GPUs dominate the draw
    "dl380 gen11":   750,   # 2U 2S, Sapphire Rapids — higher TDP
    "dl360 gen10":   500,   # 1U 2S
    "dl380 gen10":   650,   # 2U 2S
    "dl560 gen10":  1100,   # 2U 4S
    # Dell PowerEdge
    "poweredge r640":  500,  # 1U 2S
    "poweredge r740":  650,  # 2U 2S
    "poweredge r750":  750,  # 2U 2S, newer
    "poweredge r940": 1200,  # 3U 4S
    "poweredge r7525":1000,  # 2U dual-Epyc, GPU-capable
    "poweredge r760": 900,   # 2U 2S DLC — cold plates carry a higher-TDP CPU pair
    "poweredge r660": 700,   # 1U 2S DLC
    # Lenovo ThinkSystem
    "sr630":  500,   # 1U 2S
    "sr650":  700,   # 2U 2S
    "sr860": 1300,   # 4U 4S
    # Supermicro
    "sys-120u": 550,  # 1U 2S
    "sys-220u": 700,  # 2U 2S
    "sys-121h": 800,  # 1U 2S liquid-cooled chassis, high-TDP pair
    "sys-221h": 900,  # 2U 2S liquid-cooled chassis
    "as-4124gs":6000, # 4U dual-Epyc + up to 8 GPU — dense accelerated
    # IBM
    "power system s922": 1000,  # 2U 2S POWER9
    "x3850 x6":          1400,  # 4U 4S
    "flexsystem x240":    450,  # 2S blade
    # ── Cooling plant ──
    # These are MOTOR / COMPRESSOR nameplates off the spec sheet, not the tiny
    # placeholder values the curated topology used to carry (a 7.3 kW "800 kW
    # chiller"). The chiller follows from the model's own design efficiency:
    # 800 kW cooling ÷ COP 5.5 (cooling_model.CHILLER_COP_RATED) ≈ 145 kW, i.e.
    # ~0.64 kW/ton — a water-cooled centrifugal at its design point.
    "19dv 800":          145000,  # Carrier 19DV, 800 kW cooling
    "nb 100-200":         18500,  # Grundfos end-suction, primary chilled water
    "nb 65-200":          11000,  # smaller end-suction frame
    "tp 100-360":         18500,  # Grundfos in-line, condenser water
    "pt2 series":         30000,  # BAC counterflow tower, VFD fan motor
    "liebert pcw 100kw":   6500,  # Vertiv CRAH, EC plug fans at design airflow
    "chx80":               1800,  # CoolIT CDU, coolant pump module
}

# Rated COOLING capacity (W of heat removed) for the units that remove heat.
# Distinct from the electrical draw above: a chiller's spec sheet carries both, and
# their ratio is its kW/ton. The live model needs the capacity to know a chiller's
# true part-load ratio — without it, a chiller cooling 88 kW of IT with an 800 kW
# machine would be evaluated on its part-load curve as if it were nearly fully
# loaded, reporting an efficient COP while actually crawling along at ~11 % load.
_MODEL_COOL_CAPACITY_W = {
    "19dv 800":          800000,
    "liebert pcw 100kw": 100000,
    "chx80":              80000,
}


def cooling_capacity_w(model_name: str = "") -> int:
    """Rated heat-removal capacity (W) for a chiller / CRAH / CDU SKU; 0 if the
    model is unknown (callers then fall back to a load-ratio proxy)."""
    m = (model_name or "").lower()
    for key, w in _MODEL_COOL_CAPACITY_W.items():
        if key in m:
            return w
    return 0


# CDU mounting class. An IN-RACK CDU (CoolIT CHx80: 4U, bolted into the cabinet it
# serves) feeds that one rack's manifold — the cold-plate hoses land on UQDs inside
# the cabinet and do not leave it. A ROW or FACILITY CDU (Vertiv XDU, nVent Modular,
# Motivair, CoolIT's floor-standing CHx750) is a skid on the floor feeding a header
# that several racks tap off, so it legitimately serves a whole row or hall.
#
# The distinction decides which CDUs a new liquid-cooled server may be plumbed into:
# offering an in-rack unit from a different cabinet would draw a coolant hose across
# the aisle, which is not a connection that exists.
_IN_RACK_CDU_KEYS = ("chx80", "chx40", "chx150")


# UQD hose pairs on the manifold a CDU feeds — the real limit on how many servers
# can join a coolant loop, and the direct analogue of a PDU's outlet count. Thermal
# capacity is almost never what binds: a CHx80 is rated 80 kW but its rack manifold
# terminates a fixed number of dripless quick-disconnect pairs, so the loop runs out
# of PORTS long before it runs out of kW (18 × ~900 W DLC servers is ~16 kW, a fifth
# of the unit's rating).
#
# Counts are representative of a common configuration, not a fixed vendor spec —
# manifolds are ordered per deployment in several port counts, and a rack can carry
# more than one. Row/facility skids feed a header serving many racks, hence the much
# larger counts.
_MODEL_MANIFOLD_PORTS = {
    "chx80":        18,    # 4U in-rack, vertical rack manifold
    "chx40":        12,
    "chx150":       24,
    "chx750":       64,    # floor-standing row CDU
    "xdu 1350":     96,
    "cdu 600kw":    64,
    "modular cdu":  96,
}


def cdu_manifold_ports(model_name: str = "") -> int:
    """UQD hose pairs available on this CDU's manifold; 0 when the SKU is unknown.

    Callers treat 0 as UNLIMITED rather than zero — the permissive default, for the
    same reason as cdu_serves_own_rack_only: an unclassified CDU that reported no
    ports would make every rack it serves un-buildable."""
    m = (model_name or "").lower()
    for key, n in _MODEL_MANIFOLD_PORTS.items():
        if key in m:
            return n
    return 0


def cdu_serves_own_rack_only(model_name: str = "") -> bool:
    """True for a CDU that mounts inside the rack it cools (see _IN_RACK_CDU_KEYS).

    Unknown models answer False — a CDU we cannot classify is treated as a floor
    skid, which is the permissive answer. Getting that wrong offers an extra
    candidate; the strict default would instead hide the only CDU in the hall and
    make a legitimate DLC build impossible."""
    m = (model_name or "").lower()
    return any(k in m for k in _IN_RACK_CDU_KEYS)
# Model-name keywords that imply a much higher draw (GPU / AI / accelerated),
# used as a fallback for models not in the per-SKU table above.
_HIGH_POWER_KEYWORDS = ("gpu", "dgx", "a100", "h100", "l40", "mi300", "accel", "ai-", "ml-")


def nameplate_power_w(device_type: "DeviceType", model_name: str = "") -> int:
    """Realistic full-load draw (W) for a device type/model, for filling an unset
    power_draw_w. Prefers a per-SKU value from the real model catalog; falls back
    to a GPU/AI keyword bump, then a per-device-type default."""
    m = (model_name or "").lower()
    if m:
        for key, w in _MODEL_NAMEPLATE_W.items():
            if key in m:
                return w
    if device_type == DeviceType.SERVER and any(k in m for k in _HIGH_POWER_KEYWORDS):
        return 2200
    return DEFAULT_NAMEPLATE_W.get(device_type, 0)


# ── Per-SKU THROUGHPUT rating (W) for power-distribution / backup gear ─────────
# Distinct from the nameplate DRAW above: these nodes carry power, they don't
# consume it. This is the device's rated continuous throughput — the value the
# live power model divides downstream load by to get load% (a real rack PDU
# breaker / UPS module rating / genset prime rating), so an undersized node in a
# growing fleet can legitimately read OVERLOAD instead of the load÷0.8 self-
# derivation that always sat at ~80%. Keyed by a distinctive lowercase substring
# of the model string; first match wins (list more-specific keys first).
#
# Sizing basis (nameplate, not derated):
#   • Rack PDU  : 3-phase input rating (17.3/22 kW = 415V 24/32A); single-phase
#                 legacy units carry their true small rating so a mis-specced
#                 small PDU on a big rack correctly reads overload.
#   • RPP/panel : 3-phase kVA at 415V = A × 415 × √3 (80A≈57.5kVA, 400A≈287kVA).
#   • UPS       : module real power (kW); "40kVA"→36kW at 0.9 PF, PX/93PM in kW.
#   • Generator : prime/standby real power (kW), i.e. the ...D5/kW figure.
#   • Switchgear/ATS/MCC : bus or switch ampacity at 400 V 3-phase, converted to
#                 real power as A × 400 × √3 × 0.9 PF. A 3000 A ATS ≈ 1870 kW;
#                 a 4000 A main board ≈ 2490 kW; an 800 A MCC ≈ 499 kW.
#   • Utility feed : the service transformer's rating (kVA → kW at 0.9 PF).
_MODEL_RATED_W = {
    # ── Rack PDU (0U/1U) ──
    # VERIFIED 2026-07-17 against the vendor datasheets. The three below were not just
    # wrong, they were SHUFFLED: ap8941 carried the real ap8865's 8.6 kW, ap8865 carried
    # a 22 kW that belongs to the ap8886, and the ap8886 — the only strip in this
    # catalog that can actually 2N a 17.6 kW rack — was buried at 7.1 kW. Their comments
    # were wrong too (the ap8941 is L6-30P SINGLE-phase, not 3-phase).
    "ap8886":       22000,   # APC Metered ZeroU, 22.0kW(32A) 230V 3-phase, (30)C13&(12)C19
    "ap8865":        8600,   # APC Metered ZeroU, 8.6kW 208V 3-phase 30A, (36)C13&(6)C19
    "ap8941":        4992,   # APC Switched ZeroU, 208V 1-phase 30A (L6-30P), 4992 VA
    "px3-5878":     22000,   # UNVERIFIED — real unit looks like 6xC13/18xC19 @415V, not this
    "ap8959":        8600,   # UNVERIFIED
    "ap8681":        3700,   # APC Metered-by-Outlet 1U, 16A 230V single-phase
    # VERIFIED 2026-09-01 against the breaker each one actually has. All three
    # Raritan strips below are SINGLE-phase 208 V and were carrying three-phase
    # class numbers - px2-5170cr claimed 8.6 kW on a 30 A 208 V inlet, which is a
    # 6240 VA nameplate. North American rack PDUs are rated for continuous duty
    # at 80% of the breaker (NEC 210.20(A)), so the usable figure is 24 A x 208 V
    # = 4992 VA - the number this catalog already carries for the ap8941, which
    # is the same electrical spec from the other vendor.
    #
    # It was not a harmless label. rated_power_w is the denominator of load%, and
    # an injected 85% load derives its current back out of it: against 8600 W
    # that produced 42.6 A on a 30 A strip, so one injected fault raised Load
    # High AND tripped the over-current rule. The rating has to agree with the
    # breaker or the two alarms contradict each other.
    "px3-5190r":     4992,   # Raritan Switched 1U, 30A 208V 1-phase (24A cont.)
    "px3-5161r":     2662,   # Raritan Switched 1U, 16A 208V 1-phase (12.8A cont.)
    "px2-5170cr":    4992,   # Raritan Switched 0U, 30A 208V 1-phase (24A cont.)
    "epdu g3 ma 1u 32a": 7400,
    "epdu g3 mi 1u 32a": 7400,
    "epdu g3 ma 1u 16a": 3700,
    "geist rpdu2 30a":   5000,
    "geist rpdu2 15a":   1800,
    "sentry pt40":       5000,
    "sentry 4805-xls":   5000,
    # ── Floor PDU ──
    "flexpdu 40kva":     40000,
    "eaton pdu 80kva":   80000,
    "eaton pdu 160kva": 160000,
    "liebert mpx 60kva": 60000,
    "liebert mph2 24kva":24000,
    "px3-5000 floor":     8600,
    # ── RPP / panelboard (415V 3-phase kVA) ──
    "galaxy rpp 80a":    57500,
    "galaxy rpp 100a":   71900,
    "galaxy rpp 125a":   89800,
    "galaxy rpp 150a":  107800,
    "galaxy rpp 160a":  115000,
    "panelboard 400a":  287500,
    "eaton rpp 250a":   179700,
    # ── UPS (real power, kW) ──
    "exl s1 1200":     1200000,   # Vertiv Liebert EXL S1, large 2N-bus frame
    "symmetra px 250":  250000,
    "symmetra px 160":  160000,
    "symmetra px 100":  100000,
    "93pm 200":         200000,
    "93pm 160":         160000,
    "93pm 120":         120000,
    "93e 40kva":         36000,
    "9e 20kva":          18000,
    "exl s1 125":       125000,
    "exl s1 20kva":      18000,
    "liebert aps 20kva": 18000,
    "srt 5000":           4500,
    "smart-ups 3000":     2700,
    "smart-ups 1500":     1000,
    "9px 5000":           4500,
    "5px 2200":           1980,
    "gxt5 2000":          1800,
    # ── Generator (prime/standby real power, kW) ──
    "3512c":           1500000,   # Caterpillar 3512C, 1500 kW standby (2N genset)
    "c1000d5":          800000,
    "c500d5":           400000,
    "c250d5":           200000,
    "xq600":            480000,
    "xq230":            184000,
    "3516b":           2000000,
    "600reozjb":        480000,
    "250reozjb":        200000,
    # ── Utility service entrance (transformer kVA at 0.9 PF) ──
    "ion9000":         2500000,   # 2.78 MVA service transformer + revenue meter
    # ── LV switchgear / paralleling switchgear (bus ampacity) ──
    "magnum ds 4000a":            2490000,   # Eaton main switchboard, 4000 A
    "7000 paralleling switchgear": 3000000,  # ASCO gen bus — sum of both gensets
    # ── Automatic transfer switch ──
    # An ATS carries its side's UPS plus its MCC. With the mechanical bus tie closed
    # (its sibling's transfer switch failed) it carries BOTH MCCs, so the 4000 A frame
    # is what a site with a real mechanical load actually installs.
    "7000 series 4000a": 2490000,   # ASCO 4000 A bypass-isolation ATS
    "7000 series 3000a": 1870000,
    "7000 series 2000a": 1250000,
    "atc-900 3000a":     1870000,   # Eaton
    # ── Motor control center (mechanical distribution) ──
    "freedom 2100 mcc 1600a": 997000,   # Eaton, 1600 A — sized to carry BOTH mech buses
    "freedom 2100 mcc 1200a": 748000,   #   across a closed tie, not just its own half
    "freedom 2100 mcc 800a":  499000,
    "model 6 mcc 800a":       499000,   # Schneider
    # ── Mechanical power panel (per-hall CRAH panelboard) ──
    # CRAH fans are small (~6.5 kW each) and numerous; they hang off a panelboard
    # in the hall, not off the chiller plant's motor control center.
    "pow-r-line 3a 225a":     140300,   # Eaton, 225 A
    "pow-r-line 3a 150a":      93500,   # Eaton, 150 A
}
# Device types that carry power (rated by throughput) rather than draw it.
_DIST_RATED_TYPES = {DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.RPP,
                     DeviceType.UPS, DeviceType.GENERATOR,
                     DeviceType.UTILITY_FEED, DeviceType.SWITCHGEAR,
                     DeviceType.ATS, DeviceType.MCC, DeviceType.MPP}


#: Supply phases and per-phase breaker rating (A) per rack/floor-PDU SKU.
#:
#: A strip's overload reference is its OWN input breaker, per phase — not a
#: fleet-wide constant. Both numbers are already stated in the _MODEL_RATED_W
#: comments above; this makes them machine-readable so the current a PDU reports
#: and the current it alarms on are derived from the same nameplate.
#:
#: Phases matter twice over. A 3-phase strip carries its load on three
#: conductors, so per-phase current is a THIRD of the single-phase equivalent —
#: computing it single-phase and then comparing against a per-phase breaker
#: over-reads by 3x, which is how a 27%-loaded AP8886 read as sitting on its
#: 32 A breaker.
_MODEL_PHASES_BREAKER: dict[str, tuple[int, float]] = {
    # ── Rack PDU (0U/1U) ──
    "ap8886":       (3, 32.0),   # 22.0 kW, 230 V 3-phase, 32 A/phase
    "ap8865":       (3, 30.0),   # 8.6 kW, 208 V 3-phase, 30 A/phase
    "ap8941":       (1, 30.0),   # 4992 VA, 208 V 1-phase, L6-30P
    "ap8959":       (1, 30.0),   # 208 V 1-phase 30 A
    "ap8681":       (1, 16.0),   # 3.7 kW, 230 V 1-phase 16 A
    "px3-5878":     (3, 32.0),   # UNVERIFIED — rating unconfirmed, see above
    "px3-5190r":    (1, 30.0),
    "px3-5161r":    (1, 16.0),
    "px2-5170cr":   (1, 30.0),
    "epdu g3 ma 1u 32a": (1, 32.0),
    "epdu g3 mi 1u 32a": (1, 32.0),
    "epdu g3 ma 1u 16a": (1, 16.0),
    "geist rpdu2 30a":   (1, 30.0),
    "geist rpdu2 15a":   (1, 15.0),
    "sentry pt40":       (1, 30.0),
    "sentry 4805-xls":   (1, 30.0),
}


#: Whether a PDU SKU has OUTLET RELAYS - i.e. whether an outlet can be switched.
#:
#: A rack PDU is sold in tiers, and only the Switched tier can open a receptacle:
#: a Metered strip measures and never interrupts, and Metered-by-Outlet measures
#: PER outlet and still has no relay. On real hardware the difference is not a
#: policy, it is the absence of the object: rPDU2OutletSwitchedControlCommand and
#: Raritan's outletSwitchingOperation do not exist on a metered strip, so a SET
#: comes back noSuchObject. There is nothing to command.
#:
#: Floor PDUs are False for a different reason - a panelboard distributes through
#: breakers, and a breaker is not a remotely operable outlet.
#:
#: None means the catalog does not know, and callers must NOT read that as False.
#: Refusing an operation because we are ignorant of the SKU is worse than
#: allowing one the hardware might not have; the entries here are the ones whose
#: tier the vendor states.
_MODEL_OUTLET_SWITCHING: dict[str, bool] = {
    # ── switched: has relays ──
    "ap8941":            True,   # APC Switched ZeroU
    "ap8959":            True,   # APC Switched 1U
    "px2-5170cr":        True,   # Raritan Switched 0U
    "px3-5878":          True,   # Raritan Switched 0U
    "px3-5190r":         True,   # Raritan Switched 1U
    "px3-5161r":         True,   # Raritan Switched 1U
    "sentry 4805-xls":   True,   # ServerTech Switched 1U
    "epdu g3 ma":        True,   # Eaton MANAGED - metered plus switched outlets
    # ── metered only: measures, never interrupts ──
    "ap8886":            False,  # APC Metered ZeroU
    "ap8865":            False,  # APC Metered ZeroU
    "ap8681":            False,  # APC Metered-BY-OUTLET: per-outlet metering, no relay
    "epdu g3 mi":        False,  # Eaton Metered Input
    # ── floor PDUs: breakers, not outlets ──
    "flexpdu":           False,
    "eaton pdu":         False,
    "px3-5000 floor":    False,
}


def pdu_outlet_switching(device_type: "DeviceType",
                         model_name: str = "") -> bool | None:
    """Can an outlet on this SKU be switched? None when the catalog cannot say.

    Substring-matched on the lowercased model name, the same way the rating
    catalog is, so it covers both spellings a topology may carry.
    """
    if device_type not in (DeviceType.PDU, DeviceType.FLOOR_PDU):
        return None
    m = (model_name or "").lower()
    if m:
        for key, switched in _MODEL_OUTLET_SWITCHING.items():
            if key in m:
                return switched
    return None


def pdu_phases_breaker(device_type: "DeviceType",
                       model_name: str = "") -> tuple[int, float]:
    """(phases, per-phase breaker A) for a PDU SKU.

    Returns (0, 0.0) for anything not in the catalog, which callers read as "no
    nameplate known" and fall back on. Deliberately NOT guessed from the wattage:
    8.6 kW is a 3-phase 30 A strip in one SKU and a 1-phase 40 A one in another,
    so a guess would be wrong silently rather than absent honestly.
    """
    if device_type not in (DeviceType.PDU, DeviceType.FLOOR_PDU):
        return (0, 0.0)
    m = (model_name or "").lower()
    if m:
        for key, spec in _MODEL_PHASES_BREAKER.items():
            if key in m:
                return spec
    return (0, 0.0)


def rated_capacity_w(device_type: "DeviceType", model_name: str = "") -> int:
    """Rated continuous THROUGHPUT (W) for a distribution/backup SKU, for filling
    an unset rated_power_w. 0 if the type isn't a distribution node or the model
    is unknown (caller then keeps the load÷0.8 self-derivation)."""
    if device_type not in _DIST_RATED_TYPES:
        return 0
    m = (model_name or "").lower()
    if m:
        for key, w in _MODEL_RATED_W.items():
            if key in m:
                return w
    return 0


# ---------------------------------------------------------------- serials

# Vendor serial-number formats, as the real ones read.
#
#   Dell        7-char Service Tag, alphanumeric.
#   HPE         10 chars: 2 letters, 3 digits, 5 alphanumerics ("SGH421X9KL").
#   Cisco       11 chars: 3-letter site code, 2-digit year, 2-digit week,
#               4-char sequence ("FOC2314A1B2").
#   APC         12 chars, alphanumeric, no fixed public structure.
#   default     8 alphanumerics.
#
# Format matters because a DCIM will one day parse it - a Cisco serial carries
# its manufacturing site and week, and vendor tooling matches on the shape. A
# simulator that emits one flat hash for every vendor cannot exercise any of
# that, and the day real gear arrives the parsing has never been tested.
#
# The CHARSET excludes I, O, 0 and 1 the way real service tags do: a serial is
# read off a sticker by a person under a rack, and a font where those pairs
# collide is how an asset gets filed against the wrong machine.
_SERIAL_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SERIAL_DIGITS = "23456789"
_SERIAL_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

# Real Cisco manufacturing site codes.
_CISCO_SITES = ("FOC", "FDO", "JAE", "SAL", "TAE")


def _from_hash(digest: bytes, offset: int, count: int, alphabet: str) -> str:
    """Pick `count` characters out of the digest, deterministically."""
    return "".join(alphabet[digest[(offset + i) % len(digest)] % len(alphabet)]
                   for i in range(count))


def device_serial(device: "Device") -> str:
    """The chassis serial for one device. ONE string, every protocol.

    This is the single source of truth, and that is the whole point of it
    existing. Before it, SNMP served ``sha1(id)[:7]`` through ENTITY-MIB while
    Redfish served ``SN-<id[:8]>`` for the SAME machine, so a DCIM polling both
    planes read two different serials off one server and had every reason to
    file it as two assets. Real hardware has one serial burned in at manufacture
    and reports it identically over Redfish, IPMI FRU and entPhysicalSerialNum;
    a simulator whose planes disagree is teaching the collector a lesson that is
    false.

    Deterministic in the device id, so it survives a restart and a re-export -
    which is what makes reconciliation testable at all. An explicitly set
    ``serial_number`` always wins: a serial is a physical fact somebody may have
    recorded, not something the simulator gets to overwrite.
    """
    explicit = (getattr(device, "serial_number", "") or "").strip()
    if explicit:
        return explicit

    import hashlib
    seed = (device.id or device.name or "").encode()
    d = hashlib.sha256(seed).digest()
    vendor = getattr(device, "vendor", None)
    vendor_value = getattr(vendor, "value", vendor) or ""

    if vendor_value == Vendor.DELL.value:
        return _from_hash(d, 0, 7, _SERIAL_CHARS)
    if vendor_value == Vendor.HPE.value:
        return (_from_hash(d, 0, 2, _SERIAL_LETTERS)
                + _from_hash(d, 2, 3, _SERIAL_DIGITS)
                + _from_hash(d, 5, 5, _SERIAL_CHARS))
    if vendor_value == Vendor.CISCO_SYSTEMS.value:
        site = _CISCO_SITES[d[0] % len(_CISCO_SITES)]
        # Year 21-25 and week 01-52: a plausible in-service age, not a date the
        # simulator pretends to know.
        year = 21 + (d[1] % 5)
        week = 1 + (d[2] % 52)
        return f"{site}{year:02d}{week:02d}{_from_hash(d, 3, 4, _SERIAL_CHARS)}"
    if vendor_value == Vendor.APC.value:
        return _from_hash(d, 0, 12, _SERIAL_CHARS)
    return _from_hash(d, 0, 8, _SERIAL_CHARS)


@dataclass
class Device:
    name: str
    device_type: DeviceType
    vendor: Vendor
    ip_address: str
    snmp_port: int = 161
    gnmi_port: int = 57400
    snmp_community: str = "public"
    interface_count: int = 4
    interface_groups: List[dict] = field(default_factory=list)
    model_name: str = ""
    metrics_enabled: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    # Chassis serial. Persisted in the topology because a serial is a physical
    # fact about the unit, not something regenerated per run - and because a
    # DCIM reconciles against it, so it has to survive an export/import round
    # trip unchanged. Left empty it is filled deterministically from the device
    # id in __post_init__; set explicitly it is never overwritten.
    serial_number: str = ""
    interfaces: List[Interface] = field(default_factory=list)
    # Power terminations. A PDU has outlets; a load device has PSUs. Which of them
    # a given cord uses lives on the EDGE, not here — these are inventory only.
    # (Interface.connected_to_device is the cached-termination pattern that already
    # went stale once; power does not repeat it.)
    outlets: List[Outlet] = field(default_factory=list)
    psus: List[PowerSupply] = field(default_factory=list)

    # Management network
    mgmt_ip: str = ""      # OOB management IP (192.168.x.y)
    mgmt_vlan: int = 10    # VLAN tag for management segment

    # Modbus plane.
    #   "server"    native Modbus/TCP on this device's own IP
    #   "gateway"   owns an IP and fronts an RS-485 trunk, addressed by unit id
    #   "rtu_slave" a field transmitter ON that trunk. It has NO IP of its own —
    #               that is the entire point. A chilled-water thermowell is two
    #               wires into a transmitter, not a network node, and the gateway
    #               is what the BMS and the NMS both actually talk to.
    modbus_role: str = ""
    modbus_unit_id: int = 0
    modbus_gateway_ip: str = ""              # rtu_slave -> its gateway's IP
    modbus_children: List[str] = field(default_factory=list)   # gateway -> slave names

    # BACnet MS/TP plane. A device with mstp_mac set sits on an RS-485 trunk
    # behind a BACnet/IP router and owns NO address: mstp_router_ip is the
    # router's, and (mstp_net, mstp_mac) is what identifies it on the wire.
    # A Belimo "-BAC" actuator and a Grundfos CIM 300 pump card are exactly this.
    mstp_net: int = 0
    mstp_mac: int = 0
    mstp_router_ip: str = ""
    mstp_children: List[str] = field(default_factory=list)     # router -> device names

    # Sensor-port plane. A Raritan DPX2 probe plugs into a PX2's RJ-12 SENSOR
    # port and is read THROUGH that PDU's agent — it has no processor, no IP and
    # no Ethernet. host_pdu_ip is the PDU that carries it; sensor_slot is its
    # base index in that PDU's external-sensor table (DPX2 units daisy-chain, so
    # a T3H1 occupies four consecutive slots and a CC2 two).
    host_pdu_ip: str = ""
    sensor_slot: int = 0
    sensor_children: List[str] = field(default_factory=list)   # PDU -> probe names

    # Power chain
    power_draw_w: int = 0   # typical power draw in watts
    # Nameplate THROUGHPUT rating (W) for power-distribution / backup gear
    # (PDU / RPP / floor-PDU / UPS / generator). Fixed at install: load% = live
    # downstream draw ÷ this, so it stays constant as the fleet grows and the
    # node marches toward overload. 0 = auto-derive from the initial downstream
    # nameplate sum (÷0.8) and freeze at that install baseline. IT devices leave
    # this 0 (they are loads, not distribution nodes).
    rated_power_w: int = 0
    # Supply phases and per-phase input breaker (A) for a PDU. 0 = SKU not in the
    # catalog. Current is derived per PHASE from these, and the overload rules
    # measure against this device's own breaker rather than a fleet constant.
    pdu_phases: int = 0
    pdu_breaker_a: float = 0.0
    # NO power_source / power_source_a / power_source_b. Which PDU feeds a device is a
    # fact about the CORD, so it is read from the power edge — TopologyEngine.power_feeds
    # (psu index -> supply id/name/model, outlet, A|B side) — exactly as PowerSupply
    # carries no `feed` field, and for the same reason:
    #
    #   "Cached here they would be right until the first re-cord and wrong forever
    #    after — the same trap as Interface.connected_to_device."
    #
    # These fields WERE that trap. Cross-checking them against the cords in 2026-07
    # found 14 network devices naming the wrong HALL's PDUs (their pod was cloned and
    # the record came along while the cords were redone), 6 CDUs corded but unrecorded,
    # 55 records for cords that do not exist, and 72 references to devices that had been
    # deleted. Every cord was correct; only the cache lied. Nothing read them at runtime
    # (Redfish and /power-terminations already went to power_feeds), so they were
    # write-only drift. Ask the edges.
    ups_backup: str = ""    # device ID of UPS protecting this device
    power_state: str = "On"  # chassis power ("On"/"Off") — driven by Redfish ops

    # Physical location
    country: str = ""
    datacenter_city: str = ""
    datacenter: str = ""
    room: str = ""
    floor: str = ""
    rack_row: int = 0
    rack_num: int = 0
    rack_unit: int = 0

    # Floor-plan placement (room-local metres + aisle containment). NOT device
    # telemetry — a device never reports these; they're the DCIM asset layer the
    # floor-plan exporter reads. None on devices that aren't floor-placed.
    floor_x: Optional[float] = None     # rack centre x within the room (m)
    floor_y: Optional[float] = None     # rack centre y within the room (m)
    rack_facing: str = ""               # 'N' (faces lower y) or 'S' (faces higher y)
    cold_aisle: str = ""                # cold-aisle id this rack fronts (e.g. CA1)
    hot_aisle: str = ""                 # hot-aisle id behind this rack (e.g. HA1)
    sub_floor: bool = False             # device sits in the raised-floor plenum

    # Dual-homing (MLAG/vPC) forward-compat — see core/rack_capacity.py.
    # Single-homed today; these only mark a leaf rack as ready for a future 2nd
    # ToR so the flip is non-disruptive. mlag_ready=True on a leaf means its rack
    # reserves mlag_peer_unit (RU) and spare uplink ports for the peer leaf.
    mlag_ready: bool = False
    mlag_peer_unit: int = 0

    # Dynamic metrics (randomized per device)
    cpu_usage: int = field(default_factory=lambda: random.randint(5, 95))
    memory_total: int = field(default_factory=lambda: random.choice([2, 4, 8, 16, 32]) * 1024 * 1024 * 1024)
    memory_used: int = 0
    disk_total: int = field(default_factory=lambda: random.choice([100, 250, 500, 1000]) * 1024 * 1024 * 1024)
    disk_used: int = 0
    sys_uptime: int = field(default_factory=lambda: random.randint(100000, 9999999))
    # Temperatures in °C — CPU/ASIC and chassis inlet
    cpu_temp: float = field(default_factory=lambda: round(random.uniform(40.0, 80.0), 1))
    inlet_temp: float = field(default_factory=lambda: round(random.uniform(22.0, 38.0), 1))
    fan_rpm: int = 0   # server chassis fan speed (RPM); advanced by the ticker, read by Redfish
    mid_temp:    float = 0.0   # mid-rack temp °C (Raritan DPX2-T3H1 probe 2)
    outlet_temp: float = 0.0   # exhaust temp °C (Raritan DPX2-T3H1 probe 3)
    # Environmental sensor readings (populated for DeviceType.SENSOR; 0.0 on other devices)
    humidity: float = 0.0   # relative humidity %RH
    dewpoint: float = 0.0   # dew-point °C
    airflow:  float = 0.0   # airflow m/s (APC NetBotz)
    # SNMP-SET writable identity fields (empty = use computed defaults)
    sys_contact: str = ""           # sysContact; blank → "admin@{name}.example.com"
    sys_location_override: str = "" # sysLocation override; blank → computed from rack fields

    def __post_init__(self):
        if isinstance(self.device_type, str):
            self.device_type = DeviceType(self.device_type)
        if isinstance(self.vendor, str):
            self.vendor = Vendor(self.vendor)
        # Fill an unset nameplate so the power cascade reflects real IT load
        # instead of reading 0 for devices the topology never sized.
        if not self.power_draw_w or self.power_draw_w <= 0:
            self.power_draw_w = nameplate_power_w(self.device_type, self.model_name)
        # Fill an unset THROUGHPUT rating for distribution/backup gear from the
        # real per-SKU catalog, so load% is measured against the device's true
        # nameplate (breaker/module/genset rating) — an undersized SKU on a
        # growing fleet then reads a real OVERLOAD instead of the load÷0.8 self-
        # derivation that always sits at ~80%. Unknown SKUs keep rated_power_w=0
        # and fall back to that self-derivation in the power model.
        if (not self.rated_power_w or self.rated_power_w <= 0):
            self.rated_power_w = rated_capacity_w(self.device_type, self.model_name)
        # Materialise the serial onto the device so every plane reads the same
        # attribute rather than each re-deriving it - which is how SNMP and
        # Redfish came to disagree in the first place. Derived AFTER vendor
        # coercion above, because the format depends on the vendor.
        if not self.serial_number:
            self.serial_number = device_serial(self)
        # Supply phases and input-breaker rating, from the same per-SKU catalog.
        # Both stay 0 for an unknown SKU; the current model treats that as
        # single-phase with no breaker reference rather than inventing one.
        if not self.pdu_phases or not self.pdu_breaker_a:
            _ph, _br = pdu_phases_breaker(self.device_type, self.model_name)
            self.pdu_phases = self.pdu_phases or _ph
            self.pdu_breaker_a = self.pdu_breaker_a or _br
        # Normalize interface_groups (str → InterfaceType)
        if (self.device_type in FACILITY_PASSIVE_TYPES
                or self.modbus_role == "rtu_slave"
                or self.mstp_mac
                or self.host_pdu_ip):
            # Passive panel — no monitoring card, so no port. Checked FIRST and it
            # discards whatever came in: topologies written before this carry a phantom
            # eth0 (no IP, cabled to nothing, no SNMP dataset generated) that made a
            # breaker panel look like a pollable network node. mgmt_vlan goes with it —
            # there is no port to tag.
            #
            # A Modbus RTU slave lands here for the same reason and by a different
            # route: it is a field transmitter on an RS-485 drop — an RTD in a pipe
            # wired to a two-wire trunk. It has no Ethernet port, no MAC and no IP,
            # and the gateway that fronts the trunk is the only addressable thing on
            # it. Clearing the port here rather than in the migration tool is what
            # makes it stick: a saved topology's empty interface list is treated as
            # "generation stands", so the port would grow straight back.
            #
            # A BACnet MS/TP device lands here for the same reason: a Belimo
            # actuator on an RS-485 trunk is two wires and a MAC, not a network
            # node. Its router holds the only Ethernet port on the trunk.
            #
            # So does a DPX2 environmental probe: an RJ-12 lead into a PDU's
            # sensor port. The PDU is what answers for it.
            self.interface_groups = []
            self.interface_count = 0
            self.interfaces = []
            self.mgmt_vlan = 0
            self.metrics_enabled = False
            # Addresses go too. Without this a caller could hand a breaker panel an IP
            # and an SNMP port it has no card to answer on — the device would then read
            # as pollable in every list and inspector while no agent exists at that
            # address (RPP is in _NO_SNMP_TYPES, so no dataset is ever written for it).
            # snmp_port 0 is the honest value: not "the default port", but no port.
            self.ip_address = ""
            self.mgmt_ip = ""
            self.snmp_port = 0
        elif self.interface_groups:
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
        elif self.device_type in FACILITY_MGMT_TYPES:
            # Facility gear's port count is a fact about the TYPE, not the SKU: it has
            # no data plane, just 1 monitoring NIC (or 2 where a redundant NMC ships).
            # This wins over both the registry and interface_count — those describe a
            # data fit-out this gear does not have, and the default count of 4 would
            # give a CRAH four Ethernets when it has one.
            self.interface_groups = [{"iface_type": InterfaceType.GIGABIT_ETHERNET,
                                      "count": facility_mgmt_nic_count(self.device_type)}]
            self.interface_count = self.interface_groups[0]["count"]
        else:
            # No explicit groups: let the SKU speak before falling back to a count.
            # A model in the registry knows its real port fit-out (an R750 has 2 ×
            # 25G, not 4 × 1G), so a caller-supplied interface_count is a worse
            # answer than the catalog whenever the catalog has one — and it is what
            # the Add-Device dialog already SHOWS, so display and reality agree.
            groups = model_interface_groups(self.device_type, self.vendor, self.model_name)
            if groups:
                self.interface_groups = groups
                self.interface_count = sum(g["count"] for g in groups)
            else:
                # Unknown SKU (or none): a flat group from interface_count, which is
                # what topology scripts and hand-built devices rely on.
                self.interface_groups = [
                    {"iface_type": InterfaceType.GIGABIT_ETHERNET, "count": self.interface_count}
                ]
        # Community string always mirrors the IP address (default "public" is a placeholder)
        if self.snmp_community == "public":
            self.snmp_community = self.ip_address
        self.memory_used = int(self.memory_total * random.uniform(0.2, 0.85))
        self.disk_used = int(self.disk_total * random.uniform(0.1, 0.75))
        if self.device_type == DeviceType.SENSOR and self.humidity == 0.0:
            self.humidity = round(random.uniform(30.0, 70.0), 1)
            self.dewpoint = round(self.inlet_temp - ((100.0 - self.humidity) / 5.0), 1)
            self.airflow  = round(random.uniform(0.5, 2.5), 2)
        if self.model_name == "Raritan DPX2-T3H1" and self.mid_temp == 0.0:
            self.mid_temp    = round(self.inlet_temp + random.uniform(3.0, 7.0), 1)
            self.outlet_temp = round(self.inlet_temp + random.uniform(8.0, 14.0), 1)
        if not self.interfaces:
            self._generate_interfaces()
        if not self.outlets:
            self._generate_outlets()
        if not self.psus:
            self._generate_psus()

    def _generate_outlets(self):
        """Build this PDU's receptacles from its SKU. Non-PDUs get none."""
        if self.device_type != DeviceType.PDU:
            return
        spec = PDU_OUTLET_CATALOG.get(self.model_name)
        if not spec:
            return          # unknown SKU: no invented outlets — absent beats wrong
        n_c13, n_c19, phases, _rating_a, _volts = spec
        n_banks = 6 if phases == 3 else 2
        self.outlets = []
        # C13s first, then C19s — the order they are numbered on the unit. Banks are
        # filled in contiguous runs (bank 1 = the first outlets), matching how a
        # breakered group maps to a physical section of the strip.
        total = n_c13 + n_c19
        per_bank = max(1, -(-total // n_banks))       # ceil
        for i in range(total):
            bank = min(n_banks, i // per_bank + 1)
            self.outlets.append(Outlet(
                index=i + 1,
                type="C13" if i < n_c13 else "C19",
                bank=bank,
                phase=_PHASE_PAIRS[(bank - 1) % 3] if phases == 3 else "L1",
                rated_a=10.0 if i < n_c13 else 16.0,
            ))

    def _generate_psus(self):
        """Build this load's PSUs. Supply gear and sensor-port devices get none."""
        n = PSU_COUNT_BY_TYPE.get(self.device_type.value, 0)
        if not n:
            return
        # The cord is sized past C13's derated 8A. Size for the FAILURE case, not the
        # happy one: in a 1+1 pair each PSU normally carries about half the chassis,
        # but when one drops the survivor carries all of it — and that is exactly
        # when you must not be over a cord's rating. So compare the FULL draw.
        # power_draw_w (not rated_power_w) is the IT nameplate; rated_power_w is
        # throughput on distribution SKUs and stays 0 on loads.
        watts = self.power_draw_w or 0
        inlet = "C20" if watts > C13_CONTINUOUS_W else "C14"
        self.psus = [
            PowerSupply(index=i + 1, name=f"PSU{i + 1}", inlet=inlet,
                        capacity_w=1100 if inlet == "C14" else 2400)
            for i in range(n)
        ]

    def _generate_interfaces(self):
        self.interfaces = []
        idx = 1
        # On facility gear the ports FROM the groups are already the mgmt NICs — a
        # PDU/CRAH/chiller has no data plane, so its monitoring card is all there is.
        # Everything else generates data ports and gets its dedicated mgmt port below.
        group_role = (InterfaceRole.MGMT.value
                      if self.device_type in FACILITY_MGMT_TYPES
                      else InterfaceRole.DATA.value)
        for group in self.interface_groups:
            itype = group["iface_type"]
            speed = IFACE_SPEED.get(itype, 1_000_000_000)
            for i in range(group["count"]):
                self.interfaces.append(Interface(
                    index=idx,
                    name=iface_name(self.vendor, itype, i),
                    speed=speed,
                    role=group_role,
                ))
                idx += 1
        # A server's lights-out port (iDRAC / iLO / …) and a switch/router's mgmt
        # port (mgmt0 / Management1 / fxp0) are ADDITIONAL dedicated interfaces on a
        # separate path from the data plane — that is what the OOB switch's access
        # ports terminate, and what still answers when the data plane is down. Added
        # here rather than carried in interface_groups because they are not part of
        # the SKU's data fit-out; interface_count is resynced to include them so a
        # device's count matches the ports it actually has. See mgmt_port_name for
        # why OOB switches and facility gear get none.
        mgmt = mgmt_port_name(self.device_type, self.vendor)
        if mgmt:
            self.interfaces.append(Interface(
                index=idx, name=mgmt, speed=MGMT_PORT_SPEED,
                role=InterfaceRole.MGMT.value,
            ))
        self.interface_count = len(self.interfaces)

    @property
    def sys_descr(self) -> str:
        if self.model_name and self.model_name in MODEL_SYSDESCR:
            return MODEL_SYSDESCR[self.model_name]
        if self.device_type == DeviceType.SERVER:
            os_name, os_ver = SERVER_OS_INFO.get(self.vendor, ("Linux", "5.15"))
            hw = self.model_name or self.vendor.value
            return f"{hw} running {os_name} {os_ver}"
        if self.device_type in (DeviceType.UPS, DeviceType.PDU):
            base = VENDOR_SYSDESCR.get(self.vendor, "SNMP Management Card")
            if self.model_name:
                return f"{self.model_name}, {base}"
            return base
        if self.device_type == DeviceType.OOB_SWITCH:
            return VENDOR_SYSDESCR.get(self.vendor, "Out-of-Band Management Switch")
        if self.device_type == DeviceType.SENSOR:
            base = VENDOR_SYSDESCR.get(self.vendor, "Environmental Monitoring Sensor")
            if self.model_name:
                return f"{self.model_name}, {base}"
            return base
        if self.device_type == DeviceType.ENERGY_MONITOR:
            base = VENDOR_SYSDESCR.get(self.vendor,
                                        "BACnet/IP Energy Monitoring Device")
            if self.model_name:
                return f"{self.model_name}, {base}"
            return base
        return VENDOR_SYSDESCR.get(self.vendor, "Generic Device")

    @property
    def sys_oid(self) -> str:
        if self.model_name and self.model_name in MODEL_SYSOID:
            return MODEL_SYSOID[self.model_name]
        return VENDOR_SYSOID.get(self.vendor, "1.3.6.1.4.1.0.0")

    @property
    def sys_location(self) -> str:
        if self.sys_location_override:
            return self.sys_location_override
        if self.datacenter:
            parts = []
            if self.country:
                parts.append(self.country)
            if self.datacenter_city:
                parts.append(self.datacenter_city)
            parts.append(self.datacenter)
            if self.floor:
                parts.append(f"Floor {self.floor}")
            if self.room:
                parts.append(f"Room {self.room}")
            # Floor-standing plant gear is located by room only -- no rack tokens.
            if self.device_type not in FACILITY_TYPES:
                if self.rack_row:
                    parts.append(f"Row {self.rack_row}")
                if self.rack_num:
                    parts.append(f"Rack {self.rack_num}")
                if self.rack_unit:
                    parts.append(f"U{self.rack_unit}")
            return ", ".join(parts)
        return "Network Lab"

    @property
    def os_name(self) -> str:
        """Human-readable OS family name for this device."""
        if self.device_type == DeviceType.SERVER:
            return SERVER_OS_INFO.get(self.vendor, ("Linux", ""))[0]
        if self.device_type == DeviceType.UPS:
            return {
                Vendor.APC:    "APC NMC firmware",
                Vendor.EATON:  "Eaton NMC firmware",
                Vendor.VERTIV: "Liebert IntelliSlot firmware",
            }.get(self.vendor, "UPS firmware")
        if self.device_type == DeviceType.PDU:
            return {
                Vendor.APC:               "APC NMC firmware",
                Vendor.EATON:             "Eaton ePDU firmware",
                Vendor.VERTIV:            "Geist rPDU firmware",
                Vendor.RARITAN:           "Raritan PDU firmware",
                Vendor.SERVER_TECHNOLOGY: "Sentry firmware",
            }.get(self.vendor, "PDU firmware")
        if self.device_type == DeviceType.FLOOR_PDU:
            return {
                Vendor.APC:    "APC NMC firmware",
                Vendor.EATON:  "Eaton PDU firmware",
                Vendor.VERTIV: "Liebert MPX firmware",
                Vendor.RARITAN:"Raritan PDU firmware",
            }.get(self.vendor, "Floor PDU firmware")
        if self.device_type == DeviceType.OOB_SWITCH:
            return {
                Vendor.CISCO_SYSTEMS: "Cisco IOS",
                Vendor.HPE:           "HPE ArubaOS",
                Vendor.DELL:          "Dell OS6",
            }.get(self.vendor, "OOB Switch OS")
        if self.device_type == DeviceType.SENSOR:
            return {
                Vendor.RARITAN: "Raritan DPX2 firmware",
                Vendor.VERTIV:  "Geist GTHD firmware",
                Vendor.APC:     "APC NetBotz firmware",
            }.get(self.vendor, "Sensor firmware")
        if self.device_type == DeviceType.ENERGY_MONITOR:
            return {
                Vendor.VERDIGRIS: "Verdigris EV2 BACnet/IP firmware",
            }.get(self.vendor, "Energy Monitor firmware")
        if self.device_type == DeviceType.GENERATOR:
            return {
                Vendor.CUMMINS:    "Cummins PowerCommand firmware",
                Vendor.CATERPILLAR:"Caterpillar EMCP firmware",
                Vendor.KOHLER:     "Kohler Decision-Maker firmware",
            }.get(self.vendor, "Generator controller firmware")
        descr = self.sys_descr
        if "NX-OS"     in descr: return "Cisco NX-OS"
        if "IOS XR"    in descr: return "Cisco IOS XR"
        if "IOS XE"    in descr: return "Cisco IOS XE"
        if "IOS"       in descr: return "Cisco IOS"
        if "JUNOS"     in descr: return "Juniper JUNOS"
        if "EOS"       in descr: return "Arista EOS"
        if "VRP"       in descr: return "Huawei VRP"
        if "Comware"   in descr: return "HPE Comware"
        if "ExtremeXOS"in descr: return "ExtremeXOS"
        if "SONiC"     in descr: return "Dell Enterprise SONiC"
        if "OS10"      in descr: return "Dell OS10"
        if "PAN-OS"    in descr: return "Palo Alto PAN-OS"
        if "TMOS"      in descr: return "F5 TMOS"
        return "Unknown"

    @property
    def os_version(self) -> str:
        """OS version string extracted from sysDescr."""
        import re
        if self.device_type == DeviceType.SERVER:
            return SERVER_OS_INFO.get(self.vendor, ("", "Unknown"))[1]
        descr = self.sys_descr
        # SONiC format: "... - 4.2.0 - ..."
        if "SONiC" in descr:
            m = re.search(r'-\s*([\d]+\.[\d]+\.[\d]+)\s*-', descr)
            if m:
                return m.group(1)
        # Standard "Version X.Y.Z" format used by Cisco, Juniper, Arista, etc.
        m = re.search(r'[Vv]ersion\s+([\d][.\d\w()]+)', descr)
        return m.group(1) if m else "Unknown"

    @property
    def firmware_version(self) -> str:
        """Firmware version string for management-card / controller devices."""
        if self.device_type in (DeviceType.UPS, DeviceType.PDU, DeviceType.FLOOR_PDU):
            import re
            m = re.search(r'fw(?:.*?)\s+([\d][.\d\w]+)', self.sys_descr, re.IGNORECASE)
            if m:
                return m.group(1)
            m = re.search(r'firmware\s+v?([\d][.\d\w]+)', self.sys_descr, re.IGNORECASE)
            if m:
                return m.group(1)
        if self.device_type == DeviceType.SENSOR:
            import re
            m = re.search(r'fw\s+([\d][.\d\w]+)', self.sys_descr, re.IGNORECASE)
            return m.group(1) if m else "Unknown"
        if self.device_type == DeviceType.ENERGY_MONITOR:
            import re
            m = re.search(r'firmware\s+([\d][.\d\w]+)', self.sys_descr, re.IGNORECASE)
            return m.group(1) if m else "Unknown"
        if self.device_type == DeviceType.GENERATOR:
            import re
            m = re.search(r'[Vv](\d[\d.]+)', self.sys_descr)
            return m.group(1) if m else "Unknown"
        if self.device_type == DeviceType.RPP:
            return "Passive RPP -- no firmware"
        return "N/A"

    def randomize_metrics(self):
        """Refresh metrics with new random values."""
        self.cpu_usage   = random.randint(5, 95)
        self.memory_used = int(self.memory_total * random.uniform(0.2, 0.85))
        self.disk_used   = int(self.disk_total   * random.uniform(0.1, 0.75))
        self.sys_uptime += random.randint(100, 1000)
        self.cpu_temp    = round(38.0 + self.cpu_usage * 0.45 + random.uniform(-3, 3), 1)
        self.inlet_temp  = round(22.0 + self.cpu_usage * 0.12 + random.uniform(-1, 1), 1)
        if self.device_type == DeviceType.SERVER:
            # Air-cooled curve over this chassis's own RPM range. A Device cannot know
            # whether it sits on a CDU loop — that is a property of the cooling EDGES,
            # not the device — so the direct-to-chip curve lives in DeviceStateStore,
            # which can read them. This value is a seed: the ticker overwrites it on
            # its first pass.
            _lo, _hi = fan_rpm_range(self.model_name or "")
            self.fan_rpm = int(_lo + (_hi - _lo)
                               * max(0.0, min(1.0, (self.cpu_temp - 40.0) / 45.0)))
        if self.device_type == DeviceType.SENSOR:
            self.humidity = round(random.uniform(30.0, 70.0), 1)
            self.dewpoint = round(self.inlet_temp - ((100.0 - self.humidity) / 5.0), 1)
            self.airflow  = round(random.uniform(0.5, 2.5), 2)
        for iface in self.interfaces:
            iface.in_octets  += random.randint(1000, 10_000_000)
            iface.out_octets += random.randint(1000, 10_000_000)

    def attach_to_mstp_trunk(self, net: int, mac: int, router_ip: str) -> None:
        """Move an existing device onto a BACnet MS/TP trunk.

        Assigning mstp_mac by hand is NOT enough: the portless rule lives in
        __post_init__, which has already run by then, so the device keeps an
        Ethernet port and an address it cannot have. That is a quiet failure —
        the device works, it just reads as a network node in the canvas, the port
        counts and the interface tables. Anything moving a device onto a trunk at
        runtime (fleet commissioning, a fixture, a migration) should come through
        here rather than setting the fields directly.
        """
        self.mstp_net = int(net)
        self.mstp_mac = int(mac)
        self.mstp_router_ip = router_ip
        # Same strip __post_init__ applies — a trunk device is two wires and a
        # MAC, so it has no port, no address and nothing to poll over IP.
        self.interface_groups = []
        self.interface_count = 0
        self.interfaces = []
        self.mgmt_vlan = 0
        self.metrics_enabled = False
        self.ip_address = ""
        self.mgmt_ip = ""
        self.snmp_port = 0

    def attach_to_sensor_port(self, host_pdu_ip: str, slot: int) -> None:
        """Plug this probe into a PDU's sensor port.

        Same trap as attach_to_mstp_trunk: the portless rule runs in
        __post_init__, so setting host_pdu_ip by hand on an existing device
        leaves it holding an Ethernet port and an IP a DPX2 does not have.
        """
        self.host_pdu_ip = host_pdu_ip
        self.sensor_slot = int(slot)
        self.interface_groups = []
        self.interface_count = 0
        self.interfaces = []
        self.mgmt_vlan = 0
        self.metrics_enabled = False
        self.ip_address = ""
        self.mgmt_ip = ""
        self.snmp_port = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["device_type"] = self.device_type.value
        d["vendor"] = self.vendor.value
        d["model_name"] = self.model_name
        # Catalog nameplate, resolved here rather than left for the consumer to
        # look up. The rating lives in a model->watts table in this module, so
        # anything reading the export (the DCIM importer, a capacity report)
        # would otherwise have to carry a copy of that table and keep it in
        # step. 0 means "not a distribution SKU, or model not in the catalog" -
        # not "rated at zero watts".
        d["rated_power_w"] = rated_capacity_w(self.device_type, self.model_name)
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
        from dataclasses import fields as _fields
        # Copy first: popping straight off the caller's dict strips interfaces /
        # outlets / psus out of THEIR data, so loading the same parsed topology
        # twice yields devices with no ports the second time round.
        data = dict(data)
        interfaces_data = data.pop("interfaces", [])
        outlets_data = data.pop("outlets", None)
        psus_data = data.pop("psus", None)
        data.pop("interface_type", None)  # removed field — drop from legacy JSON
        valid = {f.name for f in _fields(cls)}
        data = {k: v for k, v in data.items() if k in valid}
        device = cls(**data)
        device.interfaces = [Interface(**i) for i in interfaces_data]
        # A loaded device's port LIST is the truth; the count must follow it, not the
        # throwaway list __post_init__ generated and this line just replaced. Curated
        # servers already carry their BMC inside interface_groups, so that generation
        # appends a second one and lands on a count the device does not have (an
        # R7525 reading 4 ports while holding 3). Resync only when the file actually
        # supplied ports — an empty list means "generation stands".
        if interfaces_data:
            device.interface_count = len(device.interfaces)
        # Absent (pre-outlet topology) => let __post_init__'s generation stand.
        # Present-but-empty => honour it; the SKU legitimately has none.
        # Unknown keys are dropped rather than raising: a topology written before a
        # field was retired (psus carried a cached "feed" until it moved onto the
        # edges) must still load instead of taking the whole file down.
        def _only(cls_, rows):
            valid_ = {f.name for f in _fields(cls_)}
            return [cls_(**{k: v for k, v in r.items() if k in valid_}) for r in rows]
        if outlets_data is not None:
            device.outlets = _only(Outlet, outlets_data)
        if psus_data is not None:
            device.psus = _only(PowerSupply, psus_data)
        return device


class DeviceManager:
    """Central registry for all simulated devices."""

    def __init__(self):
        self._devices: Dict[str, Device] = {}
        # Fleet Lifecycle mutates the registry from its scheduler thread while the
        # API/ticker threads iterate it (to_list, get_all_devices). A reentrant
        # lock serialises mutation against iteration so a live add/remove can't
        # raise "dictionary changed size during iteration".
        self._lock = threading.RLock()

    def add_device(self, device: Device) -> Device:
        with self._lock:
            self._devices[device.id] = device
        return device

    def remove_device(self, device_id: str) -> Optional[Device]:
        with self._lock:
            return self._devices.pop(device_id, None)

    def get_device(self, device_id: str) -> Optional[Device]:
        return self._devices.get(device_id)

    def get_all_devices(self) -> List[Device]:
        with self._lock:
            return list(self._devices.values())

    def get_devices_by_type(self, device_type: DeviceType) -> List[Device]:
        with self._lock:
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
        with self._lock:
            self._devices.clear()

    def count(self) -> int:
        return len(self._devices)

    def randomize_all_metrics(self):
        with self._lock:
            devs = list(self._devices.values())
        for device in devs:
            device.randomize_metrics()

    def to_list(self) -> List[dict]:
        with self._lock:
            devs = list(self._devices.values())
        return [d.to_dict() for d in devs]

    def load_list(self, data: List[dict]):
        with self._lock:
            self._devices.clear()
            for item in data:
                device = Device.from_dict(item)
                self._devices[device.id] = device

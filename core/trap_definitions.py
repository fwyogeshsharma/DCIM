"""
SNMP Trap Definitions — OIDs, severity levels, and applicable device types.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TrapType(str, Enum):
    COLD_START        = "coldStart"
    LINK_DOWN         = "linkDown"
    LINK_UP           = "linkUp"
    AUTH_FAILURE      = "authenticationFailure"
    CPU_HIGH          = "cpuHighUsage"
    TEMPERATURE_ALERT = "temperatureAlert"
    BGP_DOWN          = "bgpSessionDown"


SEVERITY_COLOR = {
    "informational": "#2ecc71",
    "minor":         "#f39c12",
    "major":         "#e67e22",
    "critical":      "#e74c3c",
}


@dataclass(frozen=True)
class TrapDefinition:
    trap_type:    TrapType
    oid:          str
    display_name: str
    description:  str
    severity:     str   # informational | minor | major | critical


TRAP_DEFINITIONS: dict[TrapType, TrapDefinition] = {
    TrapType.COLD_START: TrapDefinition(
        TrapType.COLD_START,
        "1.3.6.1.6.3.1.1.5.1",
        "Cold Start",
        "Device has restarted from a power cycle",
        "informational",
    ),
    TrapType.LINK_DOWN: TrapDefinition(
        TrapType.LINK_DOWN,
        "1.3.6.1.6.3.1.1.5.3",
        "Link Down",
        "A network interface has gone operationally down",
        "major",
    ),
    TrapType.LINK_UP: TrapDefinition(
        TrapType.LINK_UP,
        "1.3.6.1.6.3.1.1.5.4",
        "Link Up",
        "A network interface has come operationally up",
        "informational",
    ),
    TrapType.AUTH_FAILURE: TrapDefinition(
        TrapType.AUTH_FAILURE,
        "1.3.6.1.6.3.1.1.5.5",
        "Authentication Failure",
        "SNMP request received with incorrect community string",
        "major",
    ),
    TrapType.CPU_HIGH: TrapDefinition(
        TrapType.CPU_HIGH,
        "1.3.6.1.4.1.9999.0.1",
        "CPU High Usage",
        "CPU utilisation has exceeded 80 %",
        "major",
    ),
    TrapType.TEMPERATURE_ALERT: TrapDefinition(
        TrapType.TEMPERATURE_ALERT,
        "1.3.6.1.4.1.9999.0.2",
        "Temperature Alert",
        "Device chassis temperature has exceeded safe threshold",
        "critical",
    ),
    TrapType.BGP_DOWN: TrapDefinition(
        TrapType.BGP_DOWN,
        "1.3.6.1.2.1.15.7",
        "BGP Session Down",
        "A BGP peer session has transitioned to Idle/Active",
        "critical",
    ),
}

# Traps that make sense for each device type
APPLICABLE_TRAPS: dict[str, list[TrapType]] = {
    "router": list(TrapType),
    "switch": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN,
        TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH,
        TrapType.TEMPERATURE_ALERT,
    ],
    "server": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN,
        TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH,
        TrapType.TEMPERATURE_ALERT,
    ],
}

# Vendors that run BGP on switch/fabric infrastructure (data-centre leaf-spine,
# EVPN underlay, etc.) — these platforms get BGP_DOWN added to their switch traps.
_BGP_CAPABLE_SWITCH_VENDORS = {
    "Cisco Systems",       # Nexus EVPN fabrics
    "Arista Networks",     # EOS BGP/EVPN leaf-spine
    "Juniper Networks",    # QFX BGP/EVPN
    "Dell Technologies",   # OS10 BGP underlay
    "Huawei Technologies", # CE series BGP
}


def get_applicable_traps(device_type: str, vendor: str) -> list[TrapType]:
    """Return applicable trap types for a device, accounting for vendor capabilities.

    Routers support all traps (including BGP).
    Servers support all traps except BGP.
    Switches support all non-BGP traps by default; BGP_DOWN is added for
    data-centre fabric vendors that commonly run BGP on the underlay.
    """
    base = list(APPLICABLE_TRAPS.get(device_type, list(TrapType)))
    if device_type == "switch" and vendor in _BGP_CAPABLE_SWITCH_VENDORS:
        if TrapType.BGP_DOWN not in base:
            base.append(TrapType.BGP_DOWN)
    return base
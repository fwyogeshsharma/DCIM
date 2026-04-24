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
    # Firewalls behave like routers: they run routing protocols (BGP/OSPF between
    # security zones) and can generate the full trap set including BGP_DOWN.
    "firewall": list(TrapType),
    # Load balancers are L4-7 appliances: no routing protocols, but they do
    # generate link, CPU, and temperature alerts.
    "load_balancer": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN,
        TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH,
        TrapType.TEMPERATURE_ALERT,
    ],
}

# Switch model name substrings that indicate BGP support.
# Vendor-level checks are too broad (e.g. Cisco 2960-X is Cisco but has no BGP).
# Each entry is matched as a substring of the device's model_name.
#
# Cisco  : Nexus (NX-OS) and Catalyst 9xxx (IOS XE DC) — NOT 2960/3850 (L2 campus)
# Juniper: QFX leaf-spine and EX9xxx core — NOT EX2300/EX4300 (campus)
# Arista : all EOS platforms (BGP is universally available)
# HPE    : AOS-CX DC line (6300M, 8325) and FlexFabric 5940 — NOT Aruba 2930F (campus)
# Extreme: X695 and X870 DC spine/leaf — NOT X460-G2 (campus)
# Huawei : CloudEngine CE6xxx/CE8xxx and S6730-H DC — NOT general S-series
# Dell   : all OS10 open-networking switches (S5xxx-ON, Z92xx-ON)
_BGP_CAPABLE_SWITCH_MODELS: set[str] = {
    "Nexus",          "Catalyst 9",       # Cisco DC
    "QFX",            "EX9",              # Juniper DC
    "Arista",                             # Arista (EOS universal)
    "Aruba 6300",     "Aruba 8325",       # HPE AOS-CX DC
    "FlexFabric 5940",                    # HPE FlexFabric DC
    "X695",           "X870",             # Extreme DC
    "CE6",            "CE8",   "S6730-H", # Huawei CloudEngine / DC
    "S5248F",         "S5296F", "Z9264F", # Dell OS10
}


def get_applicable_traps(device_type: str, vendor: str,
                         model_name: str = "") -> list[TrapType]:
    """Return applicable trap types for a device.

    BGP_DOWN is added for switches only when the specific model is known to
    support BGP (data-centre fabric platforms).  Vendor alone is not sufficient
    — e.g. a Cisco Catalyst 2960-X is Cisco but has no BGP capability.
    """
    base = list(APPLICABLE_TRAPS.get(device_type, list(TrapType)))
    if device_type == "switch" and any(kw in model_name for kw in _BGP_CAPABLE_SWITCH_MODELS):
        if TrapType.BGP_DOWN not in base:
            base.append(TrapType.BGP_DOWN)
    return base
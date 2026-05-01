"""
SNMP Trap Definitions — OIDs, severity levels, and applicable device types.

Enterprise OID tree: 1.3.6.1.4.1.99999
  .1.1  highCpuUsage
  .1.2  highMemoryUsage
  .1.3  highTemperature
  .1.4  linkFlap
  .1.5  rackFailure
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TrapType(str, Enum):
    # SNMPv2-MIB standard traps
    COLD_START        = "coldStart"
    WARM_START        = "warmStart"
    LINK_DOWN         = "linkDown"
    LINK_UP           = "linkUp"
    AUTH_FAILURE      = "authenticationFailure"
    # Routing protocol traps
    BGP_DOWN          = "bgpSessionDown"
# UPS-MIB power traps
    UPS_ON_BATTERY    = "upsOnBattery"
    UPS_LOW_BATTERY   = "upsLowBattery"
    # Enterprise resource traps (1.3.6.1.4.1.99999)
    CPU_HIGH          = "cpuHighUsage"
    MEMORY_HIGH       = "memoryHighUsage"
    TEMPERATURE_ALERT = "temperatureAlert"
    LINK_FLAP         = "linkFlap"
    RACK_FAILURE      = "rackFailure"


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
    TrapType.WARM_START: TrapDefinition(
        TrapType.WARM_START,
        "1.3.6.1.6.3.1.1.5.2",
        "Warm Start",
        "Device has restarted without a power cycle",
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
    TrapType.BGP_DOWN: TrapDefinition(
        TrapType.BGP_DOWN,
        "1.3.6.1.2.1.15.0.2",
        "BGP Session Down",
        "A BGP peer session has transitioned to Idle/Active",
        "critical",
    ),
TrapType.UPS_ON_BATTERY: TrapDefinition(
        TrapType.UPS_ON_BATTERY,
        "1.3.6.1.2.1.33.2.0.1",
        "UPS On Battery",
        "UPS has switched to battery power",
        "critical",
    ),
    TrapType.UPS_LOW_BATTERY: TrapDefinition(
        TrapType.UPS_LOW_BATTERY,
        "1.3.6.1.2.1.33.2.0.2",
        "UPS Low Battery",
        "UPS battery level is critically low",
        "critical",
    ),
    TrapType.CPU_HIGH: TrapDefinition(
        TrapType.CPU_HIGH,
        "1.3.6.1.4.1.99999.1.1",
        "CPU High Usage",
        "CPU utilisation has exceeded 90 %",
        "major",
    ),
    TrapType.MEMORY_HIGH: TrapDefinition(
        TrapType.MEMORY_HIGH,
        "1.3.6.1.4.1.99999.1.2",
        "Memory High Usage",
        "Memory utilisation has exceeded 85 %",
        "major",
    ),
    TrapType.TEMPERATURE_ALERT: TrapDefinition(
        TrapType.TEMPERATURE_ALERT,
        "1.3.6.1.4.1.99999.1.3",
        "Temperature Alert",
        "Device chassis temperature has exceeded safe threshold",
        "critical",
    ),
    TrapType.LINK_FLAP: TrapDefinition(
        TrapType.LINK_FLAP,
        "1.3.6.1.4.1.99999.1.4",
        "Link Flap",
        "Interface has flapped more than 3 times in 60 seconds",
        "critical",
    ),
    TrapType.RACK_FAILURE: TrapDefinition(
        TrapType.RACK_FAILURE,
        "1.3.6.1.4.1.99999.1.5",
        "Rack Failure",
        "Three or more devices in the same rack are unreachable",
        "critical",
    ),
}

# Reverse lookup: OID string → TrapType  (used by rule engine to map OIDs)
OID_TO_TRAP_TYPE: dict[str, TrapType] = {
    defn.oid: trap_type
    for trap_type, defn in TRAP_DEFINITIONS.items()
}

# Traps that make sense for each device type
APPLICABLE_TRAPS: dict[str, list[TrapType]] = {
    "router": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.BGP_DOWN,
    ],
    "switch": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.LINK_FLAP,
    ],
    "server": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.MEMORY_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.UPS_ON_BATTERY, TrapType.UPS_LOW_BATTERY,
    ],
    "firewall": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.BGP_DOWN,
    ],
    "load_balancer": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.MEMORY_HIGH, TrapType.TEMPERATURE_ALERT,
    ],
}

# Switch model name substrings that indicate BGP support.
_BGP_CAPABLE_SWITCH_MODELS: set[str] = {
    "Nexus",          "Catalyst 9",
    "QFX",            "EX9",
    "Arista",
    "Aruba 6300",     "Aruba 8325",
    "FlexFabric 5940",
    "X695",           "X870",
    "CE6",            "CE8",   "S6730-H",
    "S5248F",         "S5296F", "Z9264F",
}


def get_applicable_traps(device_type: str, vendor: str,
                         model_name: str = "") -> list[TrapType]:
    """Return applicable trap types for a device."""
    base = list(APPLICABLE_TRAPS.get(device_type, [
        TrapType.COLD_START, TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
    ]))
    if device_type == "switch" and any(kw in model_name for kw in _BGP_CAPABLE_SWITCH_MODELS):
        if TrapType.BGP_DOWN not in base:
            base.append(TrapType.BGP_DOWN)
    return base
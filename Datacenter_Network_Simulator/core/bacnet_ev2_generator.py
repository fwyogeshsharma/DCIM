"""
Verdigris EV2 BACnet Object Tree Generator.

Builds the complete BACnet object hierarchy for one EV2 energy monitoring
device.  Object instance numbering is deterministic so IDs remain stable
across simulator restarts.

Instance ranges
---------------
1000–1019  Panel-level electrical objects (AI)
1020–1029  Panel-level alarm objects (BI)
N*1000+1 … N*1000+5   Circuit N objects (current, kW, kWh, PF, THD)
  where N = 2 for circuit 1, 3 for circuit 2, …

Example (42-circuit EV2):
  1001  Panel_Total_kW
  1002  Panel_Total_kWh
  1003–1005  Voltage PhA/PhB/PhC
  1006–1008  Current PhA/PhB/PhC
  1009  Line_Frequency
  1010  Panel_PF
  1011  Voltage_THD
  1012  Current_THD
  1013  Harmonic_3_Current
  1014  Harmonic_5_Current
  1015  Harmonic_7_Current
  1016  Harmonic_9_Current
  1020  Alarm_Overcurrent    (BI)
  1021  Alarm_VoltageImbalance (BI)
  1022  Alarm_HighTHD        (BI)
  1023  Alarm_PhaseLoss      (BI)
  1024  Alarm_SensorFault    (BI)
  2001–2005  Ckt01: Current, kW, kWh, PF, THD
  3001–3005  Ckt02 …
  …
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from core.bacnet_object_model import (
    BACnetObject,
    OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT,
    UNIT_KILOWATTS, UNIT_KILOWATT_HOURS,
    UNIT_VOLTS, UNIT_AMPERES, UNIT_HERTZ,
    UNIT_PERCENT, UNIT_NO_UNITS,
)

# Supported circuit counts
CIRCUIT_COUNT_OPTIONS = (24, 42, 84)
DEFAULT_CIRCUITS      = 42


# ─────────────────────────────────────────────────────────────────
#  Panel-level object definitions
# ─────────────────────────────────────────────────────────────────

_PANEL_AI = [
    # (instance, name, description, units, min, max, cov_increment, min_cov_interval_sec)
    # Operational signals — moderate cadence (30–60 s min interval)
    (1001, "Panel_Total_kW",       "Panel Total Active Power",          UNIT_KILOWATTS,      0.0,  200.0, 0.5,  60),
    # Accumulators — very slow (5 min min interval, large increment)
    (1002, "Panel_Total_kWh",      "Panel Total Energy Accumulation",   UNIT_KILOWATT_HOURS, 0.0,  None,  2.0,  300),
    # Stable signals — slow movement, high threshold, 2 min min interval
    (1003, "Voltage_PhA",          "Line-to-Neutral Voltage Phase A",   UNIT_VOLTS,          200.0, 260.0, 1.0,  120),
    (1004, "Voltage_PhB",          "Line-to-Neutral Voltage Phase B",   UNIT_VOLTS,          200.0, 260.0, 1.0,  120),
    (1005, "Voltage_PhC",          "Line-to-Neutral Voltage Phase C",   UNIT_VOLTS,          200.0, 260.0, 1.0,  120),
    # Operational signals — current changes are visible but not chatty
    (1006, "Current_PhA",          "Line Current Phase A",              UNIT_AMPERES,        0.0,  200.0, 1.0,  60),
    (1007, "Current_PhB",          "Line Current Phase B",              UNIT_AMPERES,        0.0,  200.0, 1.0,  60),
    (1008, "Current_PhC",          "Line Current Phase C",              UNIT_AMPERES,        0.0,  200.0, 1.0,  60),
    # Stable signals — grid frequency barely moves
    (1009, "Line_Frequency",       "Mains Supply Frequency",            UNIT_HERTZ,          45.0,  65.0,  0.05, 120),
    # Stable signals — PF drifts slowly
    (1010, "Panel_PF",             "Panel Power Factor",                UNIT_NO_UNITS,       0.0,   1.0,   0.02, 120),
    # Burst/event signals — THD normally quiet, spikes on harmonic events
    (1011, "Voltage_THD",          "Voltage Total Harmonic Distortion", UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
    (1012, "Current_THD",          "Current Total Harmonic Distortion", UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
    (1013, "Harmonic_3_Current",   "3rd Harmonic Current (%)",          UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
    (1014, "Harmonic_5_Current",   "5th Harmonic Current (%)",          UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
    (1015, "Harmonic_7_Current",   "7th Harmonic Current (%)",          UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
    (1016, "Harmonic_9_Current",   "9th Harmonic Current (%)",          UNIT_PERCENT,        0.0,  50.0,  0.5,  120),
]

_PANEL_BI = [
    # (instance, name, description)
    (1020, "Alarm_Overcurrent",       "Overcurrent Alarm — any phase exceeds threshold"),
    (1021, "Alarm_VoltageImbalance",  "Voltage Imbalance Alarm — phase deviation > 5 V"),
    (1022, "Alarm_HighTHD",           "High THD Alarm — current THD exceeds 7%"),
    (1023, "Alarm_PhaseLoss",         "Phase Loss Alarm — any phase voltage < 10 V"),
    (1024, "Alarm_SensorFault",       "Sensor Fault — internal measurement error"),
]

_CKT_AI_OFFSETS = [
    # (offset, name_suffix, description_tmpl, units, cov_increment, min_cov_interval_sec)
    (1, "Current", "Circuit {n} Current",            UNIT_AMPERES,        0.3,  60),
    (2, "kW",      "Circuit {n} Active Power",        UNIT_KILOWATTS,      0.05, 60),
    (3, "kWh",     "Circuit {n} Energy Accumulation", UNIT_KILOWATT_HOURS, 1.0,  300),
    (4, "PF",      "Circuit {n} Power Factor",        UNIT_NO_UNITS,       0.02, 120),
    (5, "THD",     "Circuit {n} Current THD",         UNIT_PERCENT,        0.3,  120),
]


def build_ev2_object_tree(
    device_instance: int,
    device_name: str,
    circuits: int = DEFAULT_CIRCUITS,
) -> Dict[Tuple[int, int], BACnetObject]:
    """
    Build the full BACnet object tree for one Verdigris EV2 device.

    Returns:
        dict keyed by (object_type, instance) → BACnetObject
    """
    objects: Dict[Tuple[int, int], BACnetObject] = {}

    # ── Panel-level Analog Inputs ─────────────────────────────────
    for inst, name, desc, units, mn, mx, cov, min_interval in _PANEL_AI:
        obj = BACnetObject(
            object_type=OBJ_ANALOG_INPUT,
            instance=inst,
            name=name,
            description=desc,
            units=units,
            min_pres_value=mn,
            max_pres_value=mx,
            cov_increment=cov,
            min_cov_interval_sec=float(min_interval),
        )
        objects[(OBJ_ANALOG_INPUT, inst)] = obj

    # ── Panel-level Binary Inputs (alarms) ────────────────────────
    # min_cov_interval_sec=0 → immediate, no delay on alarm state changes
    for inst, name, desc in _PANEL_BI:
        obj = BACnetObject(
            object_type=OBJ_BINARY_INPUT,
            instance=inst,
            name=name,
            description=desc,
            units=UNIT_NO_UNITS,
            cov_increment=1.0,
            min_cov_interval_sec=0.0,
        )
        objects[(OBJ_BINARY_INPUT, inst)] = obj

    # ── Per-circuit Analog Inputs ─────────────────────────────────
    # Circuit N: base instance = (N+1) * 1000
    # Ckt01 → base 2000, Ckt42 → base 43000
    for ckt in range(1, circuits + 1):
        base = (ckt + 1) * 1000
        label = f"Ckt{ckt:02d}"
        for offset, suffix, desc_tmpl, units, cov, min_interval in _CKT_AI_OFFSETS:
            inst = base + offset
            name = f"{label}_{suffix}"
            desc = desc_tmpl.format(n=ckt)
            obj = BACnetObject(
                object_type=OBJ_ANALOG_INPUT,
                instance=inst,
                name=name,
                description=desc,
                units=units,
                min_pres_value=0.0,
                cov_increment=cov,
                min_cov_interval_sec=float(min_interval),
            )
            objects[(OBJ_ANALOG_INPUT, inst)] = obj

    return objects


def get_panel_instance_map() -> Dict[str, int]:
    """Return name → instance mapping for panel-level objects (convenience)."""
    result: Dict[str, int] = {}
    for inst, name, *_ in _PANEL_AI:
        result[name] = inst
    for inst, name, *_ in _PANEL_BI:
        result[name] = inst
    return result


def circuit_base_instance(circuit_num: int) -> int:
    """Return the base instance for circuit N (1-based)."""
    return (circuit_num + 1) * 1000


def get_all_panel_instances() -> List[int]:
    """Return all panel-level AI + BI instance numbers."""
    return [inst for inst, *_ in _PANEL_AI] + [inst for inst, *_ in _PANEL_BI]
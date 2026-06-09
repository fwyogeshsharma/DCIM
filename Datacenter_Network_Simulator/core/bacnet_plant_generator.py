"""BACnet object trees + live telemetry engines for chiller-plant devices
(chiller / pump / cooling_tower / valve).

A single per-type spec (PLANT_SPEC) drives BOTH:
  * the BACnet object tree (one Analog Input per numeric point, one Binary
    Input per status/alarm), built by build_plant_object_tree(), and
  * a PlantTelemetryEngine that produces live present-values each tick.

Point names mirror the SNMP enterprise OIDs in snmprec_generator so the same
telemetry is visible over both planes.
"""
from __future__ import annotations
import math
import random
import time
from typing import Dict, Tuple, List

from core.bacnet_object_model import (
    BACnetObject, OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT,
    UNIT_DEGREES_CELSIUS, UNIT_LITERS_PER_SECOND, UNIT_LITERS_PER_MINUTE,
    UNIT_KILOPASCALS, UNIT_HOURS, UNIT_MILLIMETERS_PER_SECOND,
    UNIT_PERCENT, UNIT_KILOWATTS, UNIT_HERTZ, UNIT_NO_UNITS,
)

_C, _LPS, _LPM = UNIT_DEGREES_CELSIUS, UNIT_LITERS_PER_SECOND, UNIT_LITERS_PER_MINUTE
_KPA, _H, _MMS = UNIT_KILOPASCALS, UNIT_HOURS, UNIT_MILLIMETERS_PER_SECOND
_PCT, _KW, _HZ, _NO = UNIT_PERCENT, UNIT_KILOWATTS, UNIT_HERTZ, UNIT_NO_UNITS

# Analog point: (name, units, base, amplitude)
#   base = None  -> use the device's rated_kw (electrical draw), amp is a fraction
#   amplitude 0  -> constant (setpoints, nameplate, run-hours counter)
# Binary point: name. Listed in 'on' -> starts at 1 (running); else 0 (normal).
PLANT_SPEC: Dict[str, dict] = {
    "chiller": {
        "ai": [
            ("CHW_Supply_Temp",   _C,   7.0,  0.4),
            ("CHW_Return_Temp",   _C,  12.0,  0.6),
            ("CHW_Setpoint",      _C,   7.0,  0.0),
            ("CHW_Flow",          _LPS, 22.0, 3.0),
            ("Cond_Supply_Temp",  _C,  30.5,  0.8),
            ("Cond_Return_Temp",  _C,  35.5,  0.8),
            ("Compressor_Load",   _PCT,70.0, 18.0),
            ("Active_Power",      _KW,  None, 0.12),
            ("Cooling_Capacity",  _KW, 800.0, 0.0),
            ("COP",               _NO,  5.5,  0.4),
            ("Evap_Pressure",     _KPA,350.0,20.0),
            ("Cond_Pressure",     _KPA,900.0,40.0),
            ("Run_Hours",         _H, 25000.0,0.0),
        ],
        "bi": ["Chiller_Running", "Alarm_HighPressure", "Alarm_LowEvapTemp", "Alarm_FlowLoss"],
        "on": {"Chiller_Running"},
    },
    "pump": {
        "ai": [
            ("Speed",             _PCT, 75.0, 18.0),
            ("Flow",              _LPS, 22.0, 3.0),
            ("Discharge_Pressure",_KPA,450.0,40.0),
            ("Suction_Pressure",  _KPA,130.0,20.0),
            ("Diff_Pressure",     _KPA,300.0,30.0),
            ("Motor_Power",       _KW,  None, 0.12),
            ("Motor_Temp",        _C,  50.0,  8.0),
            ("VFD_Frequency",     _HZ,  37.5, 8.0),
            ("Run_Hours",         _H, 20000.0,0.0),
        ],
        "bi": ["Run_Status", "Alarm_Fault", "Alarm_LowFlow"],
        "on": {"Run_Status"},
    },
    "cooling_tower": {
        "ai": [
            ("Fan_Speed",         _PCT, 70.0, 20.0),
            ("Basin_Temp",        _C,  27.0,  1.5),
            ("Cond_Water_In",     _C,  35.0,  1.0),
            ("Cond_Water_Out",    _C,  30.0,  1.0),
            ("Fan_Power",         _KW,  None, 0.15),
            ("Basin_Level",       _PCT, 75.0,  8.0),
            ("Makeup_Flow",       _LPM,  5.0,  2.0),
            ("Vibration",         _MMS,  1.5,  0.6),
            ("Run_Hours",         _H, 22000.0,0.0),
        ],
        "bi": ["Fan_Status", "Alarm_HighVibration", "Alarm_LowBasin"],
        "on": {"Fan_Status"},
    },
    "valve": {
        "ai": [
            ("Position",          _PCT, 65.0, 25.0),
            ("Commanded_Position",_PCT, 65.0, 25.0),
            ("Actuator_Temp",     _C,  40.0,  6.0),
        ],
        "bi": ["Status_Modulating", "Alarm_ActuatorFault"],
        "on": {"Status_Modulating"},
    },
    "cdu": {
        # Coolant Distribution Unit — isolates the secondary technology-cooling
        # system (TCS / cold-plate) loop from the facility CHW and rejects chip
        # heat into that same CHW the CRAH use. Warm TCS supply (~32 °C) is the
        # point: it lets the chillers run at a higher COP.
        "ai": [
            ("TCS_Supply_Temp",    _C,  32.0,  0.8),
            ("TCS_Return_Temp",    _C,  45.0,  1.2),
            ("TCS_Setpoint",       _C,  32.0,  0.0),
            ("TCS_Flow",           _LPS, 18.0, 3.0),
            ("Facility_CHW_Valve", _PCT, 60.0, 20.0),
            ("Facility_CHW_Flow",  _LPS, 16.0, 3.0),
            ("Heat_Load",          _KW, 450.0, 60.0),
            ("Pump_Power",         _KW,  None, 0.12),
            ("Pump_Speed",         _PCT, 70.0, 18.0),
            ("Approach_Temp",      _C,   3.0,  0.5),
            ("Filter_DP",          _KPA, 35.0,  8.0),
            ("Run_Hours",          _H, 12000.0, 0.0),
        ],
        "bi": ["Unit_Running", "Alarm_Leak", "Alarm_HighSupplyTemp",
               "Alarm_PumpFault", "Alarm_LowFlow"],
        "on": {"Unit_Running"},
    },
    "crah": {
        "ai": [
            ("Supply_Air_Temp",   _C,  22.0,  1.0),
            ("Return_Air_Temp",   _C,  32.0,  1.5),
            ("Setpoint",          _C,  22.0,  0.0),
            ("Fan_Speed",         _PCT, 70.0, 18.0),
            ("CHW_Valve",         _PCT, 60.0, 20.0),
            ("Cooling_Capacity",  _PCT, 65.0, 20.0),
            ("Supply_Humidity",   _PCT, 45.0,  6.0),
            ("Airflow",           _PCT, 80.0, 12.0),
            ("Fan_Power",         _KW,  None, 0.15),
            ("Run_Hours",         _H, 18000.0, 0.0),
        ],
        "bi": ["Unit_Running", "Alarm_HighTemp", "Alarm_AirflowLoss", "Filter_Dirty"],
        "on": {"Unit_Running"},
    },
}

# Points whose magnitude tracks plant load (scaled by the diurnal multiplier).
_LOAD_TOKENS = ("Power", "Load", "Flow", "Speed")
def _is_load(name: str) -> bool:
    return any(t in name for t in _LOAD_TOKENS)


def build_plant_object_tree(device_type: str, rated_kw: float = 0.0
                            ) -> Tuple[Dict[Tuple[int, int], BACnetObject], Dict[str, Tuple[int, int]]]:
    """Build (objects, name_to_key) for a plant device. AI instances 1..N,
    BI instances 1..M (object type disambiguates equal instance numbers)."""
    spec = PLANT_SPEC[device_type]
    objects: Dict[Tuple[int, int], BACnetObject] = {}
    name_to_key: Dict[str, Tuple[int, int]] = {}

    for i, (name, units, base, amp) in enumerate(spec["ai"], start=1):
        bv = rated_kw if base is None else base
        objects[(OBJ_ANALOG_INPUT, i)] = BACnetObject(
            object_type=OBJ_ANALOG_INPUT, instance=i, name=name,
            description=name.replace("_", " "), units=units,
            min_pres_value=0.0, max_pres_value=max(bv * 2.0, 100.0),
            cov_increment=0.5, min_cov_interval_sec=30.0,
        )
        name_to_key[name] = (OBJ_ANALOG_INPUT, i)

    for j, name in enumerate(spec["bi"], start=1):
        objects[(OBJ_BINARY_INPUT, j)] = BACnetObject(
            object_type=OBJ_BINARY_INPUT, instance=j, name=name,
            description=name.replace("_", " "), units=UNIT_NO_UNITS,
            cov_increment=1.0, min_cov_interval_sec=0.0,
        )
        name_to_key[name] = (OBJ_BINARY_INPUT, j)

    return objects, name_to_key


class PlantTelemetryEngine:
    """Live telemetry generator for one plant device. tick(dt) -> {name: value}.

    Numeric points random-walk around their base; load-correlated points
    (power/flow/speed/load) are scaled by a diurnal multiplier. Binary points
    hold their nominal state (running=1, alarms=0)."""

    def __init__(self, device_type: str, rated_kw: float = 0.0, seed: int = 0):
        self._type = device_type
        spec = PLANT_SPEC[device_type]
        self._points: List[tuple] = []   # (name, base, amp, is_load, is_hours)
        self._values: Dict[str, float] = {}
        rng = random.Random(seed)
        for name, _u, base, amp in spec["ai"]:
            bv = rated_kw if base is None else base
            av = (amp * bv) if base is None else amp
            is_hours = "Run_Hours" in name
            self._points.append((name, bv, av, _is_load(name), is_hours))
            # start slightly off base so devices don't look identical
            self._values[name] = bv + (rng.uniform(-0.3, 0.3) * av)
        self._binaries: Dict[str, float] = {}
        for name in spec["bi"]:
            self._binaries[name] = 1.0 if name in spec["on"] else 0.0
        self._start = time.time()

    def _diurnal(self) -> float:
        t = time.localtime()
        hour = t.tm_hour + t.tm_min / 60.0
        phase = 2.0 * math.pi * (hour - 2.0) / 24.0
        return 0.6 + 0.2 * (1.0 + math.sin(phase))   # 0.6–1.0

    @staticmethod
    def _ema(new: float, old: float, a: float) -> float:
        return a * new + (1.0 - a) * old

    def tick(self, dt: float) -> Dict[str, float]:
        mul = self._diurnal()
        out: Dict[str, float] = {}
        for name, base, amp, is_load, is_hours in self._points:
            if is_hours:
                self._values[name] += dt / 3600.0          # accumulate run-hours
                out[name] = round(self._values[name], 2)
                continue
            if amp == 0.0:
                out[name] = round(base, 2)                  # constant (setpoint/nameplate)
                continue
            target = base * mul if is_load else base
            raw = target + random.uniform(-amp, amp) * 0.35
            self._values[name] = self._ema(raw, self._values[name], 0.2)
            lo, hi = target - amp, target + amp
            self._values[name] = max(lo, min(hi, self._values[name]))
            out[name] = round(self._values[name], 2)
        for name, val in self._binaries.items():
            out[name] = val
        return out

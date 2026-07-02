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
            ("TCS_Loop_Pressure",  _KPA, 250.0, 12.0),
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
        # Primary electrical-power point for this unit — overridden by the live
        # load-/weather-coupled cooling model when the controller supplies it.
        _PWR = ("Active_Power", "Motor_Power", "Pump_Power", "Fan_Power")
        self._power_point = next(
            (n for (n, *_r) in self._points if n in _PWR), None)
        self._nameplate_kw = float(rated_kw)
        # VFD speed point coupled to the affinity power (P ∝ speed³) for centrifugal
        # pumps/fans, so the published Speed tracks the metered draw. Chillers have no
        # such point (compressor unloads instead) → None, speed left on its walk.
        _SPD = {"pump": "Speed", "cooling_tower": "Fan_Speed",
                "crah": "Fan_Speed", "cdu": "Pump_Speed"}
        self._speed_point = _SPD.get(device_type)

        self._binaries: Dict[str, float] = {}
        for name in spec["bi"]:
            self._binaries[name] = 1.0 if name in spec["on"] else 0.0
        self._start = time.time()
        # Direct-to-chip leak event state (CDU only). A leak bleeds coolant from
        # the sealed TCS loop, so loop pressure + flow fall, the pump ramps to
        # compensate, supply/approach temps rise, and Alarm_Leak / Alarm_LowFlow
        # latch. The DCIM sees it via COV on those points (no trap needed).
        self._leak_active = False
        self._leak_t = 0.0      # seconds elapsed since leak onset
        self._leak_dur = 0.0    # leak duration before repair
        # Per-alarm severity ramp timers (seconds a forced alarm has been held),
        # used to ramp its coupled metric effects in _apply_alarm_couplings.
        self._alarm_t: Dict[str, float] = {}

    def _diurnal(self) -> float:
        t = time.localtime()
        hour = t.tm_hour + t.tm_min / 60.0
        phase = 2.0 * math.pi * (hour - 2.0) / 24.0
        return 0.6 + 0.2 * (1.0 + math.sin(phase))   # 0.6–1.0

    @staticmethod
    def _ema(new: float, old: float, a: float) -> float:
        return a * new + (1.0 - a) * old

    def tick(self, dt: float, force_leak: bool = False, forced=None,
             live_power: float | None = None) -> Dict[str, float]:
        # `forced` is the set of binary alarm point-names the operator has locked
        # "on" for this device (Limits tab). Back-compat: force_leak maps to it.
        # `live_power` (kW) — when supplied, the primary electrical-power point is
        # driven by the load-/weather-coupled cooling model instead of the
        # nameplate×diurnal curve, so cooling draw tracks the real IT heat and the
        # site ambient.
        if forced is None:
            forced = {"Alarm_Leak"} if force_leak else set()
        mul = self._diurnal()
        out: Dict[str, float] = {}
        _pwr_out: float | None = None
        for name, base, amp, is_load, is_hours in self._points:
            if is_hours:
                self._values[name] += dt / 3600.0          # accumulate run-hours
                out[name] = round(self._values[name], 2)
                continue
            if live_power is not None and name == self._power_point:
                # Load-/weather-coupled draw + a little metering jitter.
                raw = max(0.0, live_power) + random.uniform(-0.02, 0.02) * max(1.0, live_power)
                self._values[name] = self._ema(raw, self._values[name], 0.3)
                out[name] = round(max(0.0, self._values[name]), 2)
                _pwr_out = out[name]
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
        # Couple the VFD speed point to the metered affinity power (inverse of
        # P ∝ speed³) so a throttled pump/fan reports the matching speed, not an
        # independent random walk. VFD_Frequency (Hz) follows the same speed.
        if _pwr_out is not None and self._speed_point and self._speed_point in out:
            from core.cooling_model import affinity_speed_frac
            spd = affinity_speed_frac(_pwr_out, self._nameplate_kw)
            self._values[self._speed_point] = self._ema(
                spd * 100.0, self._values[self._speed_point], 0.3)
            out[self._speed_point] = round(
                max(0.0, min(100.0, self._values[self._speed_point])), 2)
            if "VFD_Frequency" in out:
                out["VFD_Frequency"] = round(spd * 50.0, 2)   # 50 Hz mains at 100 %

        for name, val in self._binaries.items():
            out[name] = val
        if self._type == "cdu":
            self._apply_cdu_leak(out, dt, "Alarm_Leak" in forced)
        self._apply_alarm_couplings(out, forced, dt)
        return out

    def _apply_cdu_leak(self, out: Dict[str, float], dt: float,
                        force_leak: bool = False) -> None:
        """Couple a coolant leak to the TCS-loop physics. A leak is raised ONLY
        by a manual force (Limits tab → cdu:Alarm_Leak locked on) and held until
        the user clears it — there is no automatic/random onset. Severity ramps
        over the first ~minute after onset."""
        _HELD = float("inf")
        if force_leak:
            if not self._leak_active:
                self._leak_active = True
                self._leak_t = 0.0
                self._leak_dur = _HELD          # held until the force is released
            self._leak_t += dt
        elif self._leak_active:
            self._leak_active = False            # force released → repaired
        if not self._leak_active:
            return
        sev = min(1.0, 0.25 + self._leak_t / 60.0)   # 0.25 → 1.0 over ~45 s
        out["Alarm_Leak"] = 1.0
        if "TCS_Loop_Pressure" in out:
            out["TCS_Loop_Pressure"] = round(out["TCS_Loop_Pressure"] * (1.0 - 0.45 * sev), 2)
        if "TCS_Flow" in out:
            out["TCS_Flow"] = round(out["TCS_Flow"] * (1.0 - 0.50 * sev), 2)
        if "Pump_Speed" in out:
            out["Pump_Speed"] = round(min(100.0, out["Pump_Speed"] * (1.0 + 0.25 * sev)), 2)
        if "Pump_Power" in out:
            out["Pump_Power"] = round(out["Pump_Power"] * (1.0 + 0.18 * sev), 2)
        if "TCS_Supply_Temp" in out:
            out["TCS_Supply_Temp"] = round(out["TCS_Supply_Temp"] + 4.0 * sev, 2)
        if "Approach_Temp" in out:
            out["Approach_Temp"] = round(out["Approach_Temp"] + 2.0 * sev, 2)
        if sev > 0.4:                          # flow collapsed → low-flow trip
            out["Alarm_LowFlow"] = 1.0

    def _apply_alarm_couplings(self, out: Dict[str, float], forced: set,
                               dt: float) -> None:
        """Local physics for every forced alarm EXCEPT the CDU leak (which has its
        own richer handler). When the operator locks an alarm on, the device's own
        coupled metrics move the way they would in the real fault, ramping with a
        severity that grows ~0.25→1.0 over the first ~45 s. Effects are confined to
        this device's points; plant-wide cascades are a separate layer."""
        active = {a for a in forced if a != "Alarm_Leak"}
        for a in [a for a in self._alarm_t if a not in active]:
            del self._alarm_t[a]                       # cleared → reset ramp
        if not active:
            return
        for a in active:
            self._alarm_t[a] = self._alarm_t.get(a, 0.0) + dt

        def mul(k, f):
            v = out.get(k)
            if v is not None:
                out[k] = round(v * f, 2)

        def add(k, d, hi=None):
            v = out.get(k)
            if v is not None:
                nv = v + d
                out[k] = round(min(hi, nv) if hi is not None else nv, 2)

        t = self._type
        for a in active:
            s = min(1.0, 0.25 + self._alarm_t[a] / 60.0)
            out[a] = 1.0
            if t == "cdu":
                if a == "Alarm_HighSupplyTemp":       # coolant too warm to setpoint
                    add("TCS_Supply_Temp", 6.0 * s); add("Approach_Temp", 3.0 * s)
                    add("Facility_CHW_Valve", 25.0 * s, 100.0)   # opens to compensate
                elif a == "Alarm_PumpFault":          # pump degraded → flow + pressure fall
                    mul("Pump_Speed", 1 - 0.60 * s); mul("Pump_Power", 1 - 0.50 * s)
                    mul("TCS_Flow", 1 - 0.50 * s); mul("TCS_Loop_Pressure", 1 - 0.40 * s)
                    add("TCS_Supply_Temp", 5.0 * s)
                elif a == "Alarm_LowFlow":
                    mul("TCS_Flow", 1 - 0.60 * s); mul("TCS_Loop_Pressure", 1 - 0.30 * s)
                    add("TCS_Supply_Temp", 3.0 * s)
            elif t == "chiller":
                if a == "Alarm_HighPressure":         # high head pressure
                    add("Cond_Pressure", 150.0 * s); mul("Active_Power", 1 + 0.20 * s)
                    mul("COP", 1 - 0.20 * s); add("Compressor_Load", 15.0 * s, 100.0)
                elif a == "Alarm_LowEvapTemp":        # evaporator near freeze → unload
                    mul("Evap_Pressure", 1 - 0.18 * s); mul("Cooling_Capacity", 1 - 0.20 * s)
                    mul("COP", 1 - 0.10 * s)
                elif a == "Alarm_FlowLoss":           # evap flow lost → chiller sheds
                    mul("Cooling_Capacity", 1 - 0.70 * s); mul("Compressor_Load", 1 - 0.50 * s)
                    mul("Active_Power", 1 - 0.50 * s); add("Cond_Supply_Temp", 3.0 * s)
            elif t == "pump":
                if a == "Alarm_Fault":                # motor/pump failing
                    for k, f in (("Speed", .7), ("Flow", .7), ("Discharge_Pressure", .6),
                                 ("Diff_Pressure", .6), ("Motor_Power", .6), ("VFD_Frequency", .6)):
                        mul(k, 1 - f * s)
                    add("Motor_Temp", 10.0 * s)
                elif a == "Alarm_LowFlow":
                    mul("Flow", 1 - 0.60 * s); mul("Diff_Pressure", 1 - 0.30 * s)
                    mul("Discharge_Pressure", 1 - 0.20 * s)
            elif t == "cooling_tower":
                if a == "Alarm_HighVibration":        # imbalance → protective slowdown
                    add("Vibration", 6.0 * s); mul("Fan_Speed", 1 - 0.40 * s)
                    mul("Fan_Power", 1 - 0.30 * s); add("Cond_Water_Out", 3.0 * s)
                elif a == "Alarm_LowBasin":           # basin low → makeup tries to refill
                    mul("Basin_Level", 1 - 0.50 * s); add("Makeup_Flow", 4.0 * s)
                    add("Basin_Temp", 2.0 * s)
            elif t == "valve":
                if a == "Alarm_ActuatorFault":        # stuck actuator: stops tracking command
                    add("Actuator_Temp", 12.0 * s)
                    cmd = out.get("Commanded_Position")
                    if cmd is not None and "Position" in out:
                        out["Position"] = round(max(0.0, cmd - 20.0 * s), 2)
            elif t == "crah":
                if a == "Alarm_HighTemp":             # cooling shortfall
                    add("Supply_Air_Temp", 6.0 * s); add("Return_Air_Temp", 5.0 * s)
                    add("CHW_Valve", 30.0 * s, 100.0); add("Cooling_Capacity", 15.0 * s, 100.0)
                elif a == "Alarm_AirflowLoss":        # fan/filter failure
                    mul("Airflow", 1 - 0.60 * s); mul("Fan_Speed", 1 - 0.50 * s)
                    add("Supply_Air_Temp", 4.0 * s); add("Return_Air_Temp", 6.0 * s)
                elif a == "Filter_Dirty":             # clogged filter → fan ramps to hold flow
                    mul("Airflow", 1 - 0.20 * s); add("Fan_Speed", 10.0 * s, 100.0)
                    mul("Fan_Power", 1 + 0.15 * s)

"""Sensor-fault point namespace and platform metric hints (doc 32 §4.6, S0-7).

``point`` is the name the simulator renders from:

* BACnet plant devices — the BACnet object name (``Supply_Air_Temp``);
* servers and rack sensors — the ``Device`` attribute (``inlet_temp``);
* UPS, genset, ATS, transformer, switchgear, plant probes … — the ext key
  (``battery_temp_c``, ``gen_fuel_pct``, ``water_temp``).

Each point carries a **platform metric hint** ``{metric, instance}`` copied into
the ledger, so the platform scorer can join without knowing simulator point
names. The BACnet half is duplicated BY HAND from
``dcim-platform/contracts/mappings/bacnet/objects.yaml`` (a test pins it when
both repos are checked out); the attribute and ext halves are advisory
[judgement: the SNMP/Modbus mappings name these by OID, not by key].
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.fault_injection import CONTROL_POINTS, is_binary

PLANT_TYPES = ("chiller", "pump", "cooling_tower", "valve", "cdu", "crah")

#: CRAH and CDU carry a native SNMP card; the rest of the plant is BACnet only.
SNMP_PLANT_TYPES = {"crah", "cdu"}

#: Points the store overwrites every tick (doc 32 §2.2). A ramp/hold on these
#: never reaches the wire; a sensor fault does, because it applies after the merge.
STORE_PUBLISHED: Dict[str, set] = {
    "chiller": {"CHW_Supply_Temp", "CHW_Return_Temp", "CHW_Setpoint", "CHW_Flow",
                "Cond_Supply_Temp", "Cond_Return_Temp", "COP", "Active_Power",
                "Compressor_Load", "Cooling_Capacity", "Cond_Pressure",
                "Cond_Approach_Temp", "Evap_Approach_Temp"},
    "crah": {"Return_Air_Temp", "Supply_Air_Temp", "CHW_Valve", "Filter_DP"},
    "cdu": {"TCS_Supply_Temp", "TCS_Return_Temp", "TCS_Setpoint", "Facility_CHW_Valve",
            "TCS_Flow", "Approach_Temp", "Filter_DP"},
    "pump": {"Flow", "Speed", "Diff_Pressure", "Suction_Pressure", "Discharge_Pressure",
             "VFD_Frequency"},
    "cooling_tower": {"Makeup_Flow", "Cond_Water_In", "Cond_Water_Out", "Basin_Temp",
                      "OA_Dry_Bulb_Temp", "OA_Wet_Bulb_Temp"},
    "valve": set(),
}

# (metric, instance) per BACnet object name — from objects.yaml.
BACNET_HINTS: Dict[str, Dict[str, tuple]] = {
    "chiller": {
        "CHW_Supply_Temp": ("water_supply_temp", "CHW"), "CHW_Return_Temp": ("water_return_temp", "CHW"),
        "CHW_Setpoint": ("water_setpoint_temp", "CHW"), "CHW_Flow": ("water_flow", "CHW"),
        "Cond_Supply_Temp": ("water_supply_temp", "COND"), "Cond_Return_Temp": ("water_return_temp", "COND"),
        "Compressor_Load": ("compressor_load_pct", ""), "Active_Power": ("power_draw", ""),
        "Cooling_Capacity": ("cooling_capacity", ""), "COP": ("cop", ""),
        "Evap_Pressure": ("water_pressure", "EVAP"), "Cond_Pressure": ("water_pressure", "COND"),
        "Run_Hours": ("run_hours", ""), "Chiller_Running": ("equipment_state", "Chiller_Running"),
        "Cond_Approach_Temp": ("approach_temp", "COND"),
        "Evap_Approach_Temp": ("approach_temp", "EVAP"),
    },
    "pump": {
        "Speed": ("pump_speed_pct", ""), "Flow": ("water_flow", ""),
        "Discharge_Pressure": ("water_pressure", "DISCHARGE"),
        "Suction_Pressure": ("water_pressure", "SUCTION"),
        "Diff_Pressure": ("water_diff_pressure", "PUMP"), "Motor_Power": ("power_draw", ""),
        "Motor_Temp": ("motor_temp", ""), "VFD_Frequency": ("vfd_frequency", ""),
        "Run_Hours": ("run_hours", ""), "Run_Status": ("equipment_state", "Run_Status"),
    },
    "cooling_tower": {
        "Fan_Speed": ("fan_speed_pct", ""), "Basin_Temp": ("water_supply_temp", "BASIN"),
        "Cond_Water_In": ("water_return_temp", "COND"), "Cond_Water_Out": ("water_supply_temp", "COND"),
        "Fan_Power": ("power_draw", ""), "Basin_Level": ("basin_level_pct", ""),
        "Makeup_Flow": ("makeup_water_flow", ""), "Vibration": ("vibration", ""),
        "Run_Hours": ("run_hours", ""), "OA_Dry_Bulb_Temp": ("outdoor_dry_bulb_temp", ""),
        "OA_Wet_Bulb_Temp": ("outdoor_wet_bulb_temp", ""), "Fan_Status": ("equipment_state", "Fan_Status"),
    },
    "valve": {
        "Position": ("valve_position_pct", "MEASURED"),
        "Commanded_Position": ("valve_position_pct", "COMMANDED"),
        "Actuator_Temp": ("motor_temp", ""), "Status_Modulating": ("equipment_state", "Status_Modulating"),
    },
    "cdu": {
        "TCS_Supply_Temp": ("water_supply_temp", ""), "TCS_Return_Temp": ("water_return_temp", ""),
        "TCS_Setpoint": ("water_setpoint_temp", ""), "TCS_Flow": ("water_flow", ""),
        "Facility_CHW_Valve": ("valve_position_pct", "FACILITY_CHW"),
        "Facility_CHW_Flow": ("water_flow", "FACILITY_CHW"),
        "TCS_Loop_Pressure": ("water_pressure", ""), "Heat_Load": ("thermal_load", ""),
        "Pump_Power": ("power_draw", ""), "Pump_Speed": ("pump_speed_pct", ""),
        "Approach_Temp": ("approach_temp", ""), "Filter_DP": ("filter_diff_pressure", ""),
        "Run_Hours": ("run_hours", ""), "Unit_Running": ("equipment_state", "Unit_Running"),
    },
    "crah": {
        "Supply_Air_Temp": ("supply_air_temp", ""), "Return_Air_Temp": ("return_air_temp", ""),
        "Setpoint": ("air_setpoint_temp", ""), "Fan_Speed": ("fan_speed_pct", ""),
        "CHW_Valve": ("valve_position_pct", "CHW"), "Cooling_Capacity": ("cooling_output_pct", ""),
        "Supply_Humidity": ("relative_humidity", ""), "Airflow": ("airflow", ""),
        "Fan_Power": ("power_draw", ""), "Run_Hours": ("run_hours", ""),
        "Unit_Running": ("equipment_state", "Unit_Running"),
        "Filter_Dirty": ("alarm_state", "Filter_Dirty"),
        # Published in Pa; the platform mapping scales by 0.001 to kPa.
        "Filter_DP": ("filter_diff_pressure", ""),
    },
}

#: Setpoint points: holdable and rampable (doc 32 §6.2), ledgered as context.
SETPOINT_POINTS = {"Setpoint", "CHW_Setpoint", "TCS_Setpoint"}
#: Physical band an operator setpoint must sit in (refused with 422 outside).
SETPOINT_BAND = {"Setpoint": (15.0, 30.0), "CHW_Setpoint": (4.0, 16.0),
                 "TCS_Setpoint": (12.0, 45.0)}

#: Device attributes a renderer serves, per device type.
ATTR_POINTS: Dict[str, List[str]] = {
    "server": ["inlet_temp", "outlet_temp", "cpu_temp", "fan_rpm"],
    "sensor": ["inlet_temp", "mid_temp", "outlet_temp", "humidity", "dewpoint"],
}
#: A rack probe serves the channels its model has and no others: a DPX2-T1 is
#: one thermistor, a T1H1 adds a hygrometer, a T3H1 fills four slots. The
#: catalogue follows core.device_manager.PROBE_CHANNELS; a model not listed
#: there (Geist GTHD, NetBotz) keeps the full head, dew point included.
_CHANNEL_ATTR = {"inlet": "inlet_temp", "mid": "mid_temp", "outlet": "outlet_temp",
                 "humidity": "humidity"}

#: The ext keys a device type's OWN renderer serves, by prefix. The store seeds
#: every device's ext with the whole electrical template (ups_*, gen_*, mcc_*,
#: util_* ...) so any renderer can read any key; the catalogue must not mistake
#: that seed for instrumentation a door contact or a switch carries. A type not
#: listed here has no ext instrumentation of its own.
TYPE_EXT_PREFIX: Dict[str, tuple] = {
    "ups": ("ups_",), "pdu": ("pdu_",), "floor_pdu": ("pdu_",),
    "generator": ("gen_",), "ats": ("ats_",), "mcc": ("mcc_",), "mpp": ("mpp_",),
    "switchgear": ("swgr_",), "utility_feed": ("util_",), "transformer": ("xfmr_",),
}
#: Ext keys that are read off the wire but are not a sensor a fault can bend:
#: an enum a controller derives, a bitmask, a tap changer's position.
_EXT_NOT_SENSOR_KEYS = frozenset({
    "fire_zones", "fire_zone_count", "smoke_stage", "gas_state", "gas_countdown_s",
    "xfmr_tap", "tank_volume_l",
})
ATTR_HINTS: Dict[str, tuple] = {
    "inlet_temp": ("inlet_temperature", ""), "outlet_temp": ("exhaust_temperature", ""),
    "mid_temp": ("ambient_temperature", "MID"), "cpu_temp": ("cpu_temperature", ""),
    "fan_rpm": ("fan_speed", ""), "humidity": ("relative_humidity", ""),
    "dewpoint": ("dew_point", ""),
}
EXT_HINTS: Dict[str, tuple] = {
    "battery_temp_c": ("battery_temperature", ""), "ups_battery_temp_c": ("battery_temperature", ""),
    "ups_battery_health": ("battery_health_pct", ""), "ups_output_load": ("load_pct", ""),
    "ups_input_voltage": ("voltage_ln", "INPUT"), "gen_fuel_pct": ("fuel_level_pct", ""),
    "gen_coolant_temp_c": ("coolant_temperature", ""), "xfmr_oil_c": ("oil_temperature", ""),
    "xfmr_hotspot_c": ("winding_temperature", ""), "room_air_temp_c": ("ambient_temperature", ""),
    "room_air_rh_pct": ("relative_humidity", ""), "pdu_temperature": ("ambient_temperature", ""),
    "pdu_humidity": ("relative_humidity", ""), "water_temp": ("water_supply_temp", ""),
    "water_flow_lps": ("water_flow", ""), "tank_level_pct": ("fuel_level_pct", ""),
    "h2_pct_lel": ("hydrogen_lel_pct", ""),
    # S3 instruments and life safety (templates.yaml, same file as above).
    "door_open": ("door_open", ""), "fire_alarm": ("fire_alarm", ""),
    "fire_trouble": ("fire_trouble", ""), "fire_supervisory": ("fire_supervisory", ""),
    "smoke_obsc_pct_m": ("smoke_obscuration", ""), "asd_flow_fault": ("alarm_state", "Airflow_Fault"),
    "agent_pressure_bar": ("agent_pressure", ""), "agent_released": ("agent_released", ""),
    "agent_low_pressure": ("alarm_state", "Agent_Low_Pressure"), "gas_abort": ("alarm_state", "Abort_Active"),
    "h2_alarm": ("alarm_state", "Alarm_High"), "tank_low": ("alarm_state", "Alarm_Low_Level"),
    "xfmr_load_pct": ("load_pct", ""), "xfmr_ia": ("current", "A"), "xfmr_ib": ("current", "B"),
    "xfmr_ic": ("current", "C"),
}
#: The same transmitter on the makeup water tank binds to the towers' key.
_ROLE_HINTS: Dict[str, Dict[str, tuple]] = {
    "water_level": {"tank_level_pct": ("water_level_pct", "")},
}
#: Ext keys that are identities, counters or states, never sensor readings.
_EXT_NOT_SENSOR_SUFFIX = ("_status", "_state", "_count", "_kwh", "_hours", "_s", "_slot")


def _dtype(device) -> str:
    dt = getattr(device, "device_type", "")
    return getattr(dt, "value", dt) or ""


def _hint(t: Optional[tuple]) -> Optional[dict]:
    return {"metric": t[0], "instance": t[1]} if t else None


def metric_hint(dtype: str, point: str) -> Optional[dict]:
    if dtype in BACNET_HINTS:
        if point in BACNET_HINTS[dtype]:
            return _hint(BACNET_HINTS[dtype][point])
        if point.startswith("Alarm_"):
            return {"metric": "alarm_state", "instance": point}
        return None
    return _hint(ATTR_HINTS.get(point) or EXT_HINTS.get(point))


def _probe_role(device) -> Optional[str]:
    """The BMS-trunk instrument a SENSOR is (door contact, fire panel,
    thermowell ...), or None for a rack air probe. Identity only, as the
    store reads it."""
    from core.device_state_store import _probe_role as _role
    return _role(device)


def own_ext_keys(device, ext: Optional[Dict[str, Any]] = None):
    """``(keys, bits)``: the ext keys *device*'s own renderer serves, and the
    subset that are one-bit states (a contact, a relay, an alarm output)."""
    dt = _dtype(device)
    if dt == "sensor":
        role = _probe_role(device)
        if not role:
            return set(), set()
        from core.modbus_register_map import BIT_SPACES, get_probe_map
        m = get_probe_map(role)
        if m is None:
            return set(), set()
        keys, bits = set(), set()
        for space, pts in m.points.items():
            for p in pts:
                if not p.key or p.presence_of:
                    continue
                keys.add(p.key)
                if space in BIT_SPACES:
                    bits.add(p.key)
        return keys, bits
    prefixes = TYPE_EXT_PREFIX.get(dt)
    if not prefixes:
        return set(), set()
    return {k for k in (ext or {}) if k.startswith(prefixes)}, set()


def _ext_protocols(device) -> List[str]:
    from core.modbus_register_map import MODBUS_DEVICE_TYPES
    dt = _dtype(device)
    out = []
    if dt not in ("transformer", "rpp", "chiller", "pump", "cooling_tower", "valve"):
        out.append("snmp")
    # A BMS-trunk instrument IS a Modbus slave by identity: the role names the
    # map, whether or not the topology wrote modbus_role on the device.
    if dt in MODBUS_DEVICE_TYPES or getattr(device, "modbus_role", "") == "rtu_slave"             or (dt == "sensor" and _probe_role(device)):
        out.append("modbus")
    return out


def device_points(device, ext: Optional[Dict[str, Any]] = None) -> List[dict]:
    """Every injectable point on *device*: ``{point, surface, protocols,
    binary, control, store_published, metric_hint}``."""
    dt = _dtype(device)
    out: List[dict] = []
    if dt in PLANT_TYPES:
        from core.bacnet_plant_generator import PLANT_SPEC
        spec = PLANT_SPEC.get(dt, {})
        names = [p[0] for p in spec.get("ai", [])] + list(spec.get("bi", []))
        protos = ["bacnet"] + (["snmp"] if dt in SNMP_PLANT_TYPES else [])
        for nm in names:
            out.append({"point": nm, "surface": "bacnet", "protocols": list(protos),
                        "binary": is_binary(nm),
                        "control": nm in CONTROL_POINTS.get(dt, set()),
                        "store_published": nm in STORE_PUBLISHED.get(dt, set()),
                        "metric_hint": metric_hint(dt, nm)})
        return out
    role = _probe_role(device) if dt == "sensor" else None
    attrs = ATTR_POINTS.get(dt, [])
    if dt == "sensor":
        if role:
            # A thermowell, a door contact, a fire panel: one instrument with
            # its own registers, and no air head to fabricate readings for.
            attrs = []
        else:
            from core.device_manager import PROBE_CHANNELS
            chans = PROBE_CHANNELS.get(str(getattr(device, "model_name", "")))
            if chans:
                attrs = [a for a in attrs if a in {_CHANNEL_ATTR.get(c) for c in chans}]
    for nm in attrs:
        protos = ["snmp"] + (["redfish"] if dt == "server" else [])
        out.append({"point": nm, "surface": "attr", "protocols": protos, "binary": False,
                    "control": False, "store_published": False,
                    "metric_hint": metric_hint(dt, nm)})
    seen = {e["point"] for e in out}
    keys, bits = own_ext_keys(device, ext)
    hints = _ROLE_HINTS.get(role or "", {})
    for k, v in sorted((ext or {}).items()):
        if k not in keys or k in seen or k in _EXT_NOT_SENSOR_KEYS:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if k.endswith(_EXT_NOT_SENSOR_SUFFIX):
            continue
        out.append({"point": k, "surface": "ext", "protocols": _ext_protocols(device),
                    "binary": k in bits, "control": False, "store_published": False,
                    "metric_hint": _hint(hints[k]) if k in hints else metric_hint(dt, k)})
    return out


def resolve(device, point: str, ext: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    for e in device_points(device, ext):
        if e["point"] == point:
            return e
    return None

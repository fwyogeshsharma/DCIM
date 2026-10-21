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
                "Compressor_Load", "Cooling_Capacity"},
    "crah": {"Return_Air_Temp", "Supply_Air_Temp", "CHW_Valve"},
    "cdu": {"TCS_Supply_Temp", "TCS_Return_Temp", "TCS_Setpoint", "Facility_CHW_Valve",
            "TCS_Flow"},
    "pump": {"Flow", "Speed", "Diff_Pressure"},
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
    },
}

#: Device attributes a renderer serves, per device type.
ATTR_POINTS: Dict[str, List[str]] = {
    "server": ["inlet_temp", "outlet_temp", "cpu_temp", "fan_rpm"],
    "sensor": ["inlet_temp", "mid_temp", "outlet_temp", "humidity", "dewpoint"],
}
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


def _ext_protocols(device) -> List[str]:
    from core.modbus_register_map import MODBUS_DEVICE_TYPES
    dt = _dtype(device)
    out = []
    if dt not in ("transformer", "rpp", "chiller", "pump", "cooling_tower", "valve"):
        out.append("snmp")
    if dt in MODBUS_DEVICE_TYPES or getattr(device, "modbus_role", "") == "rtu_slave":
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
    for nm in ATTR_POINTS.get(dt, []):
        protos = ["snmp"] + (["redfish"] if dt == "server" else [])
        out.append({"point": nm, "surface": "attr", "protocols": protos, "binary": False,
                    "control": False, "store_published": False,
                    "metric_hint": metric_hint(dt, nm)})
    seen = {e["point"] for e in out}
    for k, v in sorted((ext or {}).items()):
        if k in seen or k.startswith("_") or k.startswith("probe_"):
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if k.endswith(_EXT_NOT_SENSOR_SUFFIX):
            continue
        out.append({"point": k, "surface": "ext", "protocols": _ext_protocols(device),
                    "binary": False, "control": False, "store_published": False,
                    "metric_hint": metric_hint(dt, k)})
    return out


def resolve(device, point: str, ext: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    for e in device_points(device, ext):
        if e["point"] == point:
            return e
    return None

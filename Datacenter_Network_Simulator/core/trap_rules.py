"""
Default SNMP trap rule definitions and JSON serialization helpers.

All mandatory rules from the specification are included.
Rules can be exported/imported as JSON for runtime reconfiguration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from core.rule_engine import Rule, Condition

log = logging.getLogger(__name__)


# ── Builder helpers ───────────────────────────────────────────────────────────

def _threshold(metric: str, op: str, value: float, duration: float = 0.0) -> Condition:
    return Condition("threshold", metric=metric, operator=op,
                     threshold=value, duration_sec=duration)


def _state_change(metric: str, from_s: str | None, to_s: str | None) -> Condition:
    return Condition("state_change", metric=metric, from_state=from_s, to_state=to_s)


def _temporal(event_type: str, count: int, window: float) -> Condition:
    return Condition("temporal", event_type=event_type,
                     event_count=count, window_sec=window)


def _composite(*conds: Condition, logic: str = "AND") -> Condition:
    return Condition("composite", logic=logic, conditions=list(conds))


def _rack(min_devices: int) -> Condition:
    return Condition("rack_failure", threshold=float(min_devices))


def _rule(name: str, cond: Condition, oid: str, *,
          severity: str = "major",
          priority: int = 100,
          device_types: list | None = None,
          model_names: list | None = None,
          recovery: bool = False,
          recovery_of: str = "") -> Rule:
    return Rule(
        rule_name=name,
        condition=cond,
        trap_oid=oid,
        severity=severity,
        priority=priority,
        device_types=device_types or [],
        model_names=model_names or [],
        is_recovery=recovery,
        recovery_of=recovery_of,
    )


# ── Default ruleset ───────────────────────────────────────────────────────────

DEFAULT_RULES: List[Rule] = [

    # ── Standard interface traps (SNMPv2-MIB) ────────────────────────────────

    _rule("LinkDown",
          _state_change("interface_status", "up", "down"),
          "1.3.6.1.6.3.1.1.5.3",
          severity="major", priority=200),

    _rule("LinkUp",
          _state_change("interface_status", "down", "up"),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=200),

    # ── Enterprise: resource threshold traps ─────────────────────────────────

    _rule("HighCPU",
          _threshold("cpu_usage", ">", 90.0),
          "1.3.6.1.4.1.99999.1.1",
          severity="major", priority=150),

    _rule("HighCPUSustained",
          _threshold("cpu_usage", ">", 90.0, duration=300.0),
          "1.3.6.1.4.1.99999.1.1",
          severity="critical", priority=160),

    _rule("HighMemory",
          _threshold("memory_usage", ">", 85.0),
          "1.3.6.1.4.1.99999.1.2",
          severity="major", priority=150),

    _rule("HighTemperature",
          _threshold("temperature", ">", 60.0),
          "1.3.6.1.4.1.99999.1.3",
          severity="critical", priority=180),

    # ── Enterprise: link flap (temporal) ─────────────────────────────────────

    _rule("LinkFlap",
          _temporal("linkDown", count=3, window=60.0),
          "1.3.6.1.4.1.99999.1.4",
          severity="critical", priority=170),

    # ── Enterprise: rack failure (cross-device correlation) ───────────────────

    _rule("RackFailure",
          _rack(min_devices=3),
          "1.3.6.1.4.1.99999.1.5",
          severity="critical", priority=190),

    # ── UPS traps (UPS-MIB) ───────────────────────────────────────────────────

    _rule("UPSOnBattery",
          _state_change("ups_status", "normal", "on_battery"),
          "1.3.6.1.2.1.33.2.0.1",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSLowBattery",
          _state_change("ups_status", None, "low_battery"),
          "1.3.6.1.2.1.33.2.0.2",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSBatteryNormal",
          _state_change("ups_battery_status", None, "normal"),
          "1.3.6.1.4.1.99999.2.1",
          severity="informational", priority=200,
          device_types=["ups"]),

    _rule("UPSUtilityRestored",
          _state_change("ups_status", None, "normal"),
          "1.3.6.1.4.1.99999.2.2",
          severity="informational", priority=200,
          device_types=["ups"],
          recovery=True, recovery_of="UPSOnBattery"),

    _rule("UPSOutputOverload",
          _threshold("ups_output_load", ">", 90.0),
          "1.3.6.1.4.1.99999.2.3",
          severity="critical", priority=195,
          device_types=["ups"]),

    _rule("UPSOutputNormal",
          _threshold("ups_output_load", "<", 70.0),
          "1.3.6.1.4.1.99999.2.4",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSOutputOverload"),

    _rule("UPSFanFailure",
          _state_change("ups_fan_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.5",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSBatteryFailure",
          _state_change("ups_battery_status", "normal", "failure"),
          "1.3.6.1.4.1.99999.2.6",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSBatteryDisconnected",
          _state_change("ups_battery_status", None, "disconnected"),
          "1.3.6.1.4.1.99999.2.7",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSChargerFailure",
          _state_change("ups_charger_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.8",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSInputVoltageHigh",
          _threshold("ups_input_voltage", ">", 250.0),
          "1.3.6.1.4.1.99999.2.9",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSInputVoltageLow",
          _threshold("ups_input_voltage", "<", 190.0),
          "1.3.6.1.4.1.99999.2.10",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSFrequencyOutOfRange",
          _composite(
              _threshold("ups_input_frequency", "<", 49.0),
              _threshold("ups_input_frequency", ">", 51.0),
              logic="OR",
          ),
          "1.3.6.1.4.1.99999.2.11",
          severity="major", priority=175,
          device_types=["ups"]),

    _rule("UPSRectifierFailure",
          _state_change("ups_rectifier_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.12",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSPhaseFailure",
          _state_change("ups_phase_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.13",
          severity="critical", priority=190,
          device_types=["ups"]),

    # ── PDU traps ─────────────────────────────────────────────────────────────

    _rule("PDUOutletOn",
          _state_change("pdu_outlet_status", "off", "on"),
          "1.3.6.1.4.1.99999.6.1",
          severity="informational", priority=150,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUOutletOff",
          _state_change("pdu_outlet_status", "on", "off"),
          "1.3.6.1.4.1.99999.6.2",
          severity="major", priority=150,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUBreakerTripped",
          _state_change("pdu_breaker_status", "ok", "tripped"),
          "1.3.6.1.4.1.99999.6.3",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDULoadHigh",
          _threshold("pdu_load", ">", 80.0),
          "1.3.6.1.4.1.99999.6.4",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDULoadCritical",
          _threshold("pdu_load", ">", 90.0),
          "1.3.6.1.4.1.99999.6.5",
          severity="critical", priority=185,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUVoltageHigh",
          _threshold("pdu_voltage", ">", 240.0),
          "1.3.6.1.4.1.99999.6.6",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUVoltageLow",
          _threshold("pdu_voltage", "<", 200.0),
          "1.3.6.1.4.1.99999.6.7",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUPhaseImbalance",
          _threshold("pdu_phase_imbalance", ">", 20.0),
          "1.3.6.1.4.1.99999.6.8",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUPowerFactorLow",
          _threshold("pdu_power_factor", "<", 0.70),
          "1.3.6.1.4.1.99999.6.9",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUOutletFailure",
          _state_change("pdu_outlet_failure", "ok", "failed"),
          "1.3.6.1.4.1.99999.6.10",
          severity="critical", priority=195,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUSmokeDetected",
          _state_change("pdu_smoke", "no", "yes"),
          "1.3.6.1.4.1.99999.6.11",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUOutletCurrentHigh",
          _threshold("pdu_outlet_current", ">", 20.0),
          "1.3.6.1.4.1.99999.6.12",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUGroundFault",
          _state_change("pdu_ground_fault", "no", "yes"),
          "1.3.6.1.4.1.99999.6.13",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    # ── Routing protocol traps ────────────────────────────────────────────────

    _rule("BGPSessionDown",
          _state_change("bgp_session", "established", "idle"),
          "1.3.6.1.2.1.15.0.2",
          severity="critical", priority=190,
          device_types=["router", "firewall"]),

    # ── Composite trap: CPU and Temperature both high ─────────────────────────

    _rule("CriticalCPUAndTemp",
          _composite(
              _threshold("cpu_usage", ">", 90.0),
              _threshold("temperature", ">", 60.0),
              logic="AND",
          ),
          "1.3.6.1.4.1.99999.1.1",
          severity="critical", priority=200),

    # ── Recovery rules ────────────────────────────────────────────────────────

    _rule("CPUNormal",
          _threshold("cpu_usage", "<", 70.0),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighCPU"),

    _rule("MemoryNormal",
          _threshold("memory_usage", "<", 70.0),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighMemory"),

    _rule("TemperatureNormal",
          _threshold("temperature", "<", 55.0),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighTemperature"),

    # ── Management layer: OOB switch link events ──────────────────────────────

    _rule("OOBSwitchLinkDown",
          _state_change("interface_status", "up", "down"),
          "1.3.6.1.6.3.1.1.5.3",
          severity="critical", priority=210,
          device_types=["oob_switch"]),

    _rule("OOBSwitchLinkUp",
          _state_change("interface_status", "down", "up"),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=210,
          device_types=["oob_switch"]),

    # ── Environmental sensor alerts ───────────────────────────────────────────

    _rule("SensorAmbientTempHigh",
          _threshold("ambient_temp", ">", 32.0),
          "1.3.6.1.4.1.99999.1.3",
          severity="major", priority=180,
          device_types=["sensor"]),

    _rule("SensorAmbientTempCritical",
          _threshold("ambient_temp", ">", 38.0),
          "1.3.6.1.4.1.99999.1.3",
          severity="critical", priority=185,
          device_types=["sensor"]),

    _rule("SensorAmbientTempNormal",
          _threshold("ambient_temp", "<", 28.0),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          recovery=True, recovery_of="SensorAmbientTempHigh"),

    # ── Humidity alerts (Raritan + Vertiv + APC) ──────────────────────────────

    _rule("SensorHighHumidity",
          _threshold("humidity", ">", 70.0),
          "1.3.6.1.4.1.99999.1.6",
          severity="major", priority=175,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    _rule("SensorCriticalHumidity",
          _threshold("humidity", ">", 80.0),
          "1.3.6.1.4.1.99999.1.6",
          severity="critical", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    _rule("SensorLowHumidity",
          _threshold("humidity", "<", 30.0),
          "1.3.6.1.4.1.99999.1.6",
          severity="major", priority=175,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    _rule("SensorHumidityNormal",
          _composite(
              _threshold("humidity", ">=", 30.0),
              _threshold("humidity", "<=", 70.0),
              logic="AND",
          ),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorHighHumidity"),

    # ── Dew-point alert (Vertiv Geist GTHD — condensation risk) ──────────────

    _rule("SensorHighDewPoint",
          _threshold("dewpoint", ">", 21.0),
          "1.3.6.1.4.1.99999.1.7",
          severity="critical", priority=185,
          device_types=["sensor"],
          model_names=["Vertiv Geist GTHD"]),

    _rule("SensorDewPointNormal",
          _threshold("dewpoint", "<=", 17.0),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorHighDewPoint"),

    # ── Airflow alert (APC NetBotz 250 — cooling anomaly) ────────────────────

    _rule("SensorHighAirflow",
          _threshold("airflow", ">", 3.5),
          "1.3.6.1.4.1.99999.1.8",
          severity="major", priority=170,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"]),

    _rule("SensorLowAirflow",
          _threshold("airflow", "<", 0.3),
          "1.3.6.1.4.1.99999.1.8",
          severity="critical", priority=180,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"]),

    _rule("SensorAirflowNormal",
          _composite(
              _threshold("airflow", ">=", 0.3),
              _threshold("airflow", "<=", 3.5),
              logic="AND",
          ),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"],
          recovery=True, recovery_of="SensorHighAirflow"),

    # ── UPS extended traps ────────────────────────────────────────────────────

    _rule("UPSBatteryLowHealth",
          _threshold("ups_battery_health", "<", 50.0),
          "1.3.6.1.4.1.99999.2.14",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSBatteryHealthRestored",
          _threshold("ups_battery_health", ">", 70.0),
          "1.3.6.1.4.1.99999.2.15",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBatteryLowHealth"),

    _rule("UPSBypassActive",
          _state_change("ups_bypass_status", "off", "on"),
          "1.3.6.1.4.1.99999.2.16",
          severity="major", priority=190,
          device_types=["ups"]),

    _rule("UPSBypassCleared",
          _state_change("ups_bypass_status", "on", "off"),
          "1.3.6.1.4.1.99999.2.17",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBypassActive"),

    # ── PDU environment traps ─────────────────────────────────────────────────

    _rule("PDUFrequencyFault",
          _threshold("pdu_frequency", "<", 49.5),
          "1.3.6.1.4.1.99999.6.14",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUFrequencyNormal",
          _threshold("pdu_frequency", ">", 49.5),
          "1.3.6.1.4.1.99999.6.15",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUFrequencyFault"),

    _rule("PDUTempHigh",
          _threshold("pdu_temperature", ">", 35.0),
          "1.3.6.1.4.1.99999.6.16",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUTempNormal",
          _threshold("pdu_temperature", "<", 30.0),
          "1.3.6.1.4.1.99999.6.17",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUTempHigh"),

    _rule("PDUHumidityHigh",
          _threshold("pdu_humidity", ">", 70.0),
          "1.3.6.1.4.1.99999.6.18",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUHumidityNormal",
          _threshold("pdu_humidity", "<", 60.0),
          "1.3.6.1.4.1.99999.6.19",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUHumidityHigh"),

    # ── Sensor mid/outlet temp traps ──────────────────────────────────────────

    _rule("SensorMidTempHigh",
          _threshold("mid_temp", ">", 38.0),
          "1.3.6.1.4.1.99999.1.9",
          severity="major", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"]),

    _rule("SensorMidTempNormal",
          _threshold("mid_temp", "<", 35.0),
          "1.3.6.1.4.1.99999.1.11",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"],
          recovery=True, recovery_of="SensorMidTempHigh"),

    _rule("SensorOutletTempHigh",
          _threshold("outlet_temp", ">", 45.0),
          "1.3.6.1.4.1.99999.1.10",
          severity="major", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"]),

    _rule("SensorOutletTempNormal",
          _threshold("outlet_temp", "<", 42.0),
          "1.3.6.1.4.1.99999.1.12",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"],
          recovery=True, recovery_of="SensorOutletTempHigh"),
]


# ── JSON serialization ────────────────────────────────────────────────────────

def rules_to_json(rules: List[Rule], indent: int = 2) -> str:
    return json.dumps([r.to_dict() for r in rules], indent=indent)


def rules_from_json(text: str) -> List[Rule]:
    data = json.loads(text)
    return [Rule.from_dict(d) for d in data]


def save_rules(rules: List[Rule], path: str | Path):
    Path(path).write_text(rules_to_json(rules), encoding="utf-8")
    log.info("[TrapRules] Saved %d rules to %s", len(rules), path)


def load_rules(path: str | Path) -> List[Rule]:
    text = Path(path).read_text(encoding="utf-8")
    rules = rules_from_json(text)
    log.info("[TrapRules] Loaded %d rules from %s", len(rules), path)
    return rules
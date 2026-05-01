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
          recovery: bool = False,
          recovery_of: str = "") -> Rule:
    return Rule(
        rule_name=name,
        condition=cond,
        trap_oid=oid,
        severity=severity,
        priority=priority,
        device_types=device_types or [],
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
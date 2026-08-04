"""Rule Engine REST endpoints."""
from __future__ import annotations

import dataclasses
from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    RuleResponse,
    RulesTableResponse,
    OkResponse,
    AutonomousFaultsRequest,
)

router = APIRouter(prefix="/rules", tags=["Rule Engine"])


def _state() -> AppState:
    return AppState.get()


@router.get("", response_model=RulesTableResponse)
def get_all_rules():
    """Get all rule engine rules with their current state, fire counts, and conditions."""
    s = _state()
    if s.rule_engine is None:
        raise HTTPException(status_code=503, detail="Rule engine not initialized")

    # ONE state snapshot for the whole table. Asking per rule took (2 x rules + 1)
    # full copies of every device's rule state under the lock the ticker needs on
    # every tick — polling this endpoint stalled the ticker outright. See
    # RuleEngine.get_rules_table_stats.
    _fired, _last, _grand = s.rule_engine.get_rules_table_stats()

    rules_out = []
    for rule in s.rule_engine.get_rules():
        try:
            condition_dict = dataclasses.asdict(rule.condition)
        except Exception:
            condition_dict = {}

        rules_out.append(RuleResponse(
            name=rule.rule_name,
            enabled=getattr(rule, "enabled", True),
            description=getattr(rule, "description", ""),
            total_fired=_fired.get(rule.rule_name, 0),
            last_fired=_last.get(rule.rule_name, "") or "",
            conditions=[condition_dict] if condition_dict else [],
            actions=[rule.trap_oid] if getattr(rule, "trap_oid", None) else [],
            severity=getattr(rule, "severity", None),
        ))

    return RulesTableResponse(
        rule_engine_enabled=s.rule_engine_enabled,
        total_fired_grand=_grand,
        rules=rules_out,
    )


@router.post("/autonomous-faults", response_model=OkResponse)
def set_autonomous_faults(body: AutonomousFaultsRequest):
    """Toggle spontaneous fault generation. OFF (default) keeps every device
    healthy so no trap fires unless the user injects one; ON lets the sim breach
    thresholds on its own (realistic live-monitoring demo). The rule engine is
    always enabled either way."""
    s = _state()
    if s.state_store is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    s.state_store.autonomous_faults = bool(body.enabled)
    s.notify_ui("sync_snmp")
    return OkResponse(message=f"Autonomous faults {'enabled' if body.enabled else 'disabled'}")


@router.post("/reset-counts", response_model=OkResponse)
def reset_fired_counts():
    """Reset all fired counts and timestamps in the rule engine."""
    s = _state()
    if s.rule_engine is None:
        raise HTTPException(status_code=503, detail="Rule engine not initialized")
    s.rule_engine.reset_fired_counts()
    s.notify_ui("sync_rules")
    return OkResponse(message="Fired counts reset")


@router.get("/{rule_name}", response_model=RuleResponse)
def get_rule(rule_name: str):
    """Get a specific rule by name."""
    s = _state()
    if s.rule_engine is None:
        raise HTTPException(status_code=503, detail="Rule engine not initialized")
    rule = s.rule_engine.get_rule(rule_name)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_name}' not found")

    try:
        condition_dict = dataclasses.asdict(rule.condition)
    except Exception:
        condition_dict = {}

    return RuleResponse(
        name=rule.rule_name,
        enabled=getattr(rule, "enabled", True),
        description=getattr(rule, "description", ""),
        total_fired=s.rule_engine.get_total_fired_count(rule.rule_name),
        last_fired=s.rule_engine.get_last_fire_ts(rule.rule_name) or "",
        conditions=[condition_dict] if condition_dict else [],
        actions=[rule.trap_oid] if getattr(rule, "trap_oid", None) else [],
        severity=getattr(rule, "severity", None),
    )


@router.post("/{rule_name}/enable", response_model=OkResponse)
def enable_rule(rule_name: str):
    """Enable a specific rule."""
    s = _state()
    if s.rule_engine is None:
        raise HTTPException(status_code=503, detail="Rule engine not initialized")
    if s.rule_engine.get_rule(rule_name) is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_name}' not found")
    s.rule_engine.enable_rule(rule_name, True)
    s.notify_ui("sync_rules")
    return OkResponse(message=f"Rule '{rule_name}' enabled")


@router.post("/{rule_name}/disable", response_model=OkResponse)
def disable_rule(rule_name: str):
    """Disable a specific rule."""
    s = _state()
    if s.rule_engine is None:
        raise HTTPException(status_code=503, detail="Rule engine not initialized")
    if s.rule_engine.get_rule(rule_name) is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_name}' not found")
    s.rule_engine.enable_rule(rule_name, False)
    s.notify_ui("sync_rules")
    return OkResponse(message=f"Rule '{rule_name}' disabled")

"""Rule Engine REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    RuleResponse,
    RulesTableResponse,
    OkResponse,
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

    rules_out = []
    for rule in s.rule_engine.get_rules():
        conditions = []
        for cond in getattr(rule, "conditions", []):
            try:
                conditions.append(cond.__dict__ if hasattr(cond, "__dict__") else vars(cond))
            except Exception:
                conditions.append({"raw": str(cond)})

        actions = []
        for action in getattr(rule, "actions", []):
            try:
                actions.append(str(action))
            except Exception:
                pass

        rules_out.append(RuleResponse(
            name=rule.rule_name,
            enabled=getattr(rule, "enabled", True),
            description=getattr(rule, "description", ""),
            total_fired=s.rule_engine.get_total_fired_count(rule.rule_name),
            last_fired=s.rule_engine.get_last_fire_ts(rule.rule_name) or "",
            conditions=conditions,
            actions=actions,
        ))

    return RulesTableResponse(
        rule_engine_enabled=s.rule_engine_enabled,
        total_fired_grand=s.rule_engine.get_grand_total_fired(),
        rules=rules_out,
    )


@router.post("/enable", response_model=OkResponse)
def enable_rule_engine():
    """Enable the rule engine — rules will fire SNMP traps based on device metrics."""
    s = _state()
    if s.trap_engine is None:
        raise HTTPException(status_code=503, detail="Trap engine not initialized")
    if not s.snmpsim or not s.snmpsim.is_running():
        raise HTTPException(status_code=409, detail="SNMP simulator must be running to enable rule engine")
    s.trap_engine.set_rule_engine_enabled(True)
    s.rule_engine_enabled = True
    s.notify_ui("sync_rules")
    return OkResponse(message="Rule engine enabled")


@router.post("/disable", response_model=OkResponse)
def disable_rule_engine():
    """Disable the rule engine — no automatic traps will be fired."""
    s = _state()
    if s.trap_engine is None:
        raise HTTPException(status_code=503, detail="Trap engine not initialized")
    s.trap_engine.set_rule_engine_enabled(False)
    s.rule_engine_enabled = False
    s.notify_ui("sync_rules")
    return OkResponse(message="Rule engine disabled")


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

    conditions = []
    for cond in getattr(rule, "conditions", []):
        try:
            conditions.append(cond.__dict__ if hasattr(cond, "__dict__") else vars(cond))
        except Exception:
            conditions.append({"raw": str(cond)})

    return RuleResponse(
        name=rule.rule_name,
        enabled=getattr(rule, "enabled", True),
        description=getattr(rule, "description", ""),
        total_fired=s.rule_engine.get_total_fired_count(rule.rule_name),
        last_fired=s.rule_engine.get_last_fire_ts(rule.rule_name) or "",
        conditions=conditions,
        actions=[str(a) for a in getattr(rule, "actions", [])],
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

"""Metrics Tick Settings REST endpoints."""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState

router = APIRouter(prefix="/tick", tags=["Tick Settings"])


def _state() -> AppState:
    return AppState.get()


def _store():
    s = _state()
    if s.state_store is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    return s.state_store


# ── GET /tick/settings ──────────────────────────────────────────────────────

@router.get("/settings")
def get_tick_settings():
    store = _store()
    limits_out: dict = {}
    for k, v in store.metric_limits.items():
        limits_out[k] = {
            "enabled": v.get("enabled", False),
            "min":     v.get("min"),
            "max":     v.get("max"),
            "lock":    v.get("lock"),
            "options": v.get("options", []),
        }
    return {
        "running":       store.is_running(),
        "paused":        store.is_paused(),
        "interval":      int(store._tick_interval),
        "metric_flags":  dict(store.metric_flags),
        "metric_limits": limits_out,
    }


# ── POST /tick/settings ─────────────────────────────────────────────────────

class TickUpdate(BaseModel):
    paused:        Optional[bool]            = None
    interval:      Optional[int]             = None
    metric_flags:  Optional[Dict[str, bool]] = None
    metric_limits: Optional[Dict[str, dict]] = None


@router.post("/settings")
def apply_tick_settings(body: TickUpdate):
    store = _store()

    if body.interval is not None:
        store.set_tick_interval(max(1, body.interval))

    if body.paused is not None and store.is_running():
        store.set_paused(body.paused)

    if body.metric_flags:
        for k, v in body.metric_flags.items():
            if k in store.metric_flags:
                store.metric_flags[k] = bool(v)

    if body.metric_limits:
        for k, v in body.metric_limits.items():
            if k not in store.metric_limits:
                continue
            lim = store.metric_limits[k]
            if "enabled" in v:
                lim["enabled"] = bool(v["enabled"])
            if v.get("min") is not None:
                lim["min"] = float(v["min"])
            if v.get("max") is not None:
                lim["max"] = float(v["max"])
            if v.get("lock") is not None:
                lim["lock"] = str(v["lock"])

    _state().persist_tick()

    return {
        "ok":      True,
        "running": store.is_running(),
        "paused":  store.is_paused(),
        "interval": int(store._tick_interval),
    }

"""Persist fitted champion models to disk (joblib), keyed by scope/target.

v1 keeps the latest champion per (scope, scope_id, target). Persistence is
best-effort — a missing/corrupt artifact never blocks a run, since the pipeline
always re-fits from fresh data anyway.
"""
from __future__ import annotations

import re
from pathlib import Path

import joblib

from app.config import get_settings
from app.logging_conf import get_logger
from app.models.base import Forecaster

log = get_logger("dcim_ml.registry")

MODEL_VERSION = "1.0.0"


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", part)[:120]


def _path(scope: str, scope_id: str, target: str) -> Path:
    base = Path(get_settings().model_path)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{_safe(scope)}__{_safe(scope_id)}__{_safe(target)}.joblib"


def save(scope: str, scope_id: str, target: str, model: Forecaster) -> None:
    try:
        joblib.dump(model, _path(scope, scope_id, target))
    except Exception as exc:  # noqa: BLE001
        log.warning("model_save_failed", target=target, error=str(exc))


def load(scope: str, scope_id: str, target: str) -> Forecaster | None:
    p = _path(scope, scope_id, target)
    if not p.exists():
        return None
    try:
        return joblib.load(p)
    except Exception as exc:  # noqa: BLE001
        log.warning("model_load_failed", target=target, error=str(exc))
        return None

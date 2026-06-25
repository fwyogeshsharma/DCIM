"""Recommendation engine (spec sections 10 & 12).

Rule layer over the capacity summary + utilization forecasts. Each recommendation
carries a severity (info/warning/critical) and a confidence derived from the
model's backtest error and how far out the predicted event is.
"""
from __future__ import annotations

from datetime import date

from app.models.selection import Selection


def _severity_from_days(days: int | None) -> str:
    if days is None:
        return "info"
    if days <= 30:
        return "critical"
    if days <= 60:
        return "warning"
    return "info"


def _confidence(mape: float | None, days_ahead: int | None) -> float:
    base = 0.6 if mape is None else max(0.3, min(0.95, 1.0 - mape / 100.0))
    if days_ahead is not None:
        base *= max(0.4, 1.0 - days_ahead / 365.0)  # decay with horizon
    return round(base, 2)


def _first_crossing(sel: Selection | None, threshold: float):
    if sel is None:
        return None
    for p in sel.points:
        if p.value >= threshold:
            return p
    return None


def build_recommendations(
    scope: str,
    scope_id: str,
    cap_summary: dict,
    util: dict[str, Selection | None],
    *,
    today: date,
    cpu_threshold: float,
    mem_threshold: float,
    server_mape: float | None,
) -> list[dict]:
    recs: list[dict] = []

    def add(category, severity, message, confidence, predicted_date=None, details=None):
        recs.append({
            "scope": scope, "scope_id": scope_id, "category": category,
            "severity": severity, "message": message, "confidence": confidence,
            "predicted_date": predicted_date, "details": details or {},
        })

    # ── Rack capacity ─────────────────────────────────────────────────────────
    rack_days = cap_summary.get("days_until_rack_full")
    rack_date = cap_summary.get("rack_exhaustion_date")
    if rack_days is not None:
        racks_needed = (
            cap_summary.get("predicted_new_racks_90d")
            or cap_summary.get("predicted_new_racks_60d")
            or cap_summary.get("predicted_new_racks_30d")
            or 0
        )
        buy = f" Purchase {racks_needed} additional rack(s) before {rack_date}." if racks_needed else ""
        add("rack", _severity_from_days(rack_days),
            f"Rack capacity will be exhausted in {rack_days} days (by {rack_date}).{buy}",
            _confidence(server_mape, rack_days), rack_date,
            {"days_until_full": rack_days, "racks_needed": racks_needed})

    # ── Storage capacity ──────────────────────────────────────────────────────
    stor_days = cap_summary.get("days_until_storage_full")
    stor_date = cap_summary.get("storage_exhaustion_date")
    if stor_days is not None:
        add("storage", _severity_from_days(stor_days),
            f"Storage will exceed capacity in {stor_days} days (by {stor_date}). "
            f"Plan a storage expansion before then.",
            _confidence(server_mape, stor_days), stor_date,
            {"days_until_full": stor_days})

    # ── Power headroom ────────────────────────────────────────────────────────
    headroom = cap_summary.get("power_headroom_pct")
    if headroom is not None and headroom < 20:
        sev = "critical" if headroom < 10 else "warning"
        add("power", sev,
            f"Projected power headroom is only {headroom}% — power capacity may be "
            f"exceeded soon. Review power provisioning.",
            _confidence(None, 30), None, {"power_headroom_pct": headroom})

    # ── CPU / Memory overload trend ───────────────────────────────────────────
    for key, thr, label in (("cpu", cpu_threshold, "CPU"), ("memory", mem_threshold, "Memory")):
        sel = util.get(key)
        hit = _first_crossing(sel, thr)
        if hit is not None:
            d = max((hit.forecast_for.date() - today).days, 0)
            add(key if key != "memory" else "server", _severity_from_days(d),
                f"{label} utilization is trending toward {thr:.0f}% — expected to be "
                f"reached around {hit.forecast_for.date()}.",
                _confidence(sel.mape if sel else None, d), hit.forecast_for.date(),
                {"threshold_pct": thr, "days_ahead": d})

    return recs

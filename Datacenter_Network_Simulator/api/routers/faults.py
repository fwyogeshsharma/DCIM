"""Anomaly ground-truth endpoints (doc 32 §4.7, §7.4, §10; S0-3, S0-7, S0-10).

* ``POST   /api/faults/sensor``       — a publish-layer sensor fault (201)
* ``DELETE /api/faults/{id}``         — remove it; returns the closed ledger entry
* ``GET    /api/faults``              — active faults with their live state
* ``GET    /api/faults/ledger``       — every injection ever, from every channel
* ``GET    /api/faults/points?device=`` — injectable points for one device
* ``GET    /api/faults/{id}``         — one entry, active or historical
* ``GET    /api/air/influence``       — the spatial model's rack <-> CRAH weights

Devices are ADDRESSED by topology id and KEYED by name internally, the
identity rule of ``POST /api/bacnet/plant/overrides`` (doc 32 §1.2).
Everything here is additive; no existing endpoint changes shape.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from core import fault_ledger as _fl
from core import fault_points as _fp
from core.fault_injection import FaultConflict, SensorFault, sigma_for

router = APIRouter(tags=["Faults"])


def _state() -> AppState:
    return AppState.get()


def _store():
    st = getattr(_state(), "state_store", None)
    if st is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    return st


def _device(device_id: str):
    dm = _state().device_manager
    dev = dm.get_device(device_id) if dm else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return dev


def _ext(name: str) -> dict:
    from core.device_state_store import _ext_state_cache
    return _ext_state_cache.get(name, {})


# ── sensor faults ────────────────────────────────────────────────────────────

class SensorFaultBody(BaseModel):
    device: str                       # topology device id
    point: str                        # §4.6 namespace
    bias: float = 0.0
    drift_per_h: float = 0.0
    noise_mult: float = 1.0
    stuck: bool = False
    stuck_value: Optional[float] = None
    dropout_pct: float = 0.0
    dropout_window_s: float = 30.0
    duration_s: Optional[float] = None
    affects_control: bool = False
    campaign: Optional[str] = None


def _entry_shape(e: dict, f: Optional[SensorFault] = None, protocols=None) -> dict:
    out = {"id": e.get("id"), "kind": e.get("kind", "sensor"), "device": e.get("device"),
           "device_id": e.get("device_id"), "point": e.get("point"),
           "start": e.get("start"), "end": e.get("end"), "params": e.get("params", {})}
    for k in ("expected_kind", "metric_hint", "source", "campaign", "end_reason"):
        if k in e:
            out[k] = e[k]
    if protocols is not None:
        out["protocols"] = protocols
    return out


@router.post("/faults/sensor", status_code=201)
def add_sensor_fault(body: SensorFaultBody):
    """Make one point's READING lie. Physics stays true unless
    ``affects_control`` is set on a control sensor (§4.4)."""
    st = _store()
    dev = _device(body.device)
    info = _fp.resolve(dev, body.point, _ext(dev.name))
    if info is None:
        raise HTTPException(status_code=422,
                            detail=f"'{body.point}' is not an injectable point on {dev.name}; "
                                   f"see GET /api/faults/points?device={body.device}")
    if not 0.0 <= float(body.dropout_pct) <= 100.0:
        raise HTTPException(status_code=422, detail="dropout_pct must be within 0-100")
    if body.dropout_window_s < 1.0:
        raise HTTPException(status_code=422, detail="dropout_window_s must be >= 1")
    if body.noise_mult < 0.0:
        raise HTTPException(status_code=422, detail="noise_mult must be >= 0")
    if body.duration_s is not None and body.duration_s <= 0:
        raise HTTPException(status_code=422, detail="duration_s must be > 0 (or null)")
    noop = (body.bias == 0.0 and body.drift_per_h == 0.0 and body.noise_mult == 1.0
            and not body.stuck and body.stuck_value is None and body.dropout_pct == 0.0)
    if noop:
        raise HTTPException(status_code=422,
                            detail="no-op fault: set bias, drift_per_h, noise_mult, stuck, "
                                   "stuck_value or dropout_pct")
    if body.affects_control and not info["control"]:
        raise HTTPException(status_code=422,
                            detail=f"affects_control applies only to a control sensor; "
                                   f"{dev.name}/{body.point} is not in a store control loop")
    if info["binary"] and (body.bias or body.drift_per_h or body.noise_mult != 1.0):
        raise HTTPException(status_code=422,
                            detail=f"{body.point} is binary: only stuck, stuck_value or "
                                   f"dropout apply")
    dtype = getattr(dev.device_type, "value", str(dev.device_type))
    now = st.faults.clock()
    f = SensorFault(id=_fl.new_fault_id(now), device=dev.name, device_id=dev.id,
                    point=body.point, bias=float(body.bias),
                    drift_per_h=float(body.drift_per_h), noise_mult=float(body.noise_mult),
                    stuck=bool(body.stuck), stuck_value=body.stuck_value,
                    dropout_pct=float(body.dropout_pct),
                    dropout_window_s=float(body.dropout_window_s),
                    affects_control=bool(body.affects_control), duration_s=body.duration_s,
                    start_ts=now, surface=info["surface"],
                    sigma=sigma_for(info["surface"], dtype, body.point))
    try:
        st.faults.add_sensor(f, ledger_entry={
            "metric_hint": info["metric_hint"], "source": "POST /api/faults/sensor",
            "campaign": body.campaign, "expected_kind": "sensor",
            "extra": {"protocols": info["protocols"]}})
    except FaultConflict as exc:
        raise HTTPException(status_code=409,
                            detail=f"{exc}. Faults on one point do not stack: "
                                   f"DELETE it first.")
    e = st.faults.ledger.get(f.id) or {"id": f.id, "device": f.device,
                                        "device_id": f.device_id, "point": f.point,
                                        "start": _fl.utc_iso(now), "params": f.params()}
    return _entry_shape(e, f, info["protocols"])


@router.get("/faults")
def list_faults():
    """Active sensor faults with their live state (``current``)."""
    st = _store()
    out = []
    for a in st.faults.active():
        a = dict(a)
        a["start"] = _fl.utc_iso(a.pop("start_ts"))
        out.append(a)
    return {"active": out}


@router.get("/faults/ledger")
def faults_ledger(since: Optional[str] = None, until: Optional[str] = None,
                  kind: Optional[str] = None, device: Optional[str] = None,
                  campaign: Optional[str] = None, limit: int = 500,
                  cursor: Optional[str] = None):
    """Every injection, from every channel (§7.1), folded into entries; newest
    first, paginated by an opaque cursor."""
    return _fl.get_ledger().query(since=since, until=until, kind=kind, device=device,
                                  campaign=campaign, limit=limit, cursor=cursor)


@router.get("/faults/points")
def fault_points(device: str):
    """The injectable points on one device: protocols that render each, whether
    it is a control sensor, whether the store overwrites it every tick, and the
    platform metric hint the ledger will carry."""
    dev = _device(device)
    return {"device": device, "name": dev.name,
            "device_type": getattr(dev.device_type, "value", str(dev.device_type)),
            "points": _fp.device_points(dev, _ext(dev.name))}


@router.get("/faults/{fault_id}")
def get_fault(fault_id: str):
    e = _fl.get_ledger().get(fault_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Fault '{fault_id}' not found")
    return e


@router.delete("/faults/{fault_id}")
def delete_fault(fault_id: str, restore: bool = False):
    """Remove an active fault and return its closed ledger entry. ``restore``
    is for degradations (S1: a fouled coil stays fouled until cleaned); a
    sensor fault simply ends."""
    st = _store()
    if st.faults.get(fault_id) is None:
        raise HTTPException(status_code=404, detail=f"No active fault '{fault_id}'")
    closed = st.faults.remove(fault_id, "deleted")
    return _entry_shape(closed or {"id": fault_id})


# ── air influence (C6) ───────────────────────────────────────────────────────

@router.get("/air/influence")
def air_influence(datacenter: Optional[str] = None, room: Optional[str] = None):
    """The weights the spatial air model used this tick, per rack and CRAH.
    409 in uniform mode: there is no influence structure to export (D10)."""
    st = _store()
    out = st.get_air_influence(datacenter=datacenter, room=room)
    if out is None:
        raise HTTPException(status_code=409,
                            detail="air model is 'uniform' — no influence structure exists")
    return out

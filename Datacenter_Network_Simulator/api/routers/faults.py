"""Anomaly ground-truth endpoints (doc 32 §4.7, §7.4, §10; S0-3, S0-7, S0-10).

* ``POST   /api/faults/sensor``       — a publish-layer sensor fault (201)
* ``DELETE /api/faults/{id}``         — remove it; returns the closed ledger entry
* ``GET    /api/faults``              — active faults with their live state
* ``GET    /api/faults/ledger``       — every injection ever, from every channel
* ``GET    /api/faults/points?device=`` — injectable points for one device
* ``GET    /api/faults/{id}``         — one entry, active or historical
* ``GET    /api/air/influence``       — the spatial model's rack <-> CRAH weights
* ``POST   /api/faults/degradation``  — a slow process fault in the MACHINE (S1-1)
* ``POST   /api/weather/override``    — move one site's (or every site's) sky (S1-9)
* ``DELETE /api/weather/override``    — clear it early
* ``GET    /api/weather``             — live dry / wet bulb per DC with any override

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
from core.fault_injection import (DEGRADATION_KINDS, Degradation, FaultConflict,
                                  SensorFault, degradation_healthy, sigma_for)

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


# ── degradations (C7) ────────────────────────────────────────────────────────

class DegradationBody(BaseModel):
    device: str                       # topology device id
    kind: str                         # cop | tower_approach | hx_approach | filter_loading | pump_derate
    rate_per_h: float                 # magnitude, kind's unit per hour (> 0)
    target: float                     # final value, kind's unit
    campaign: Optional[str] = None


@router.post("/faults/degradation", status_code=201)
def add_degradation(body: DegradationBody):
    """Degrade the MACHINE: physics changes (truth), and every protocol shows
    the consequence. Advances on the simulator's own clock toward *target*,
    then holds; the entry stays open (a fouled coil does not unfoul)."""
    st = _store()
    dev = _device(body.device)
    spec = DEGRADATION_KINDS.get(body.kind)
    dtype = getattr(dev.device_type, "value", str(dev.device_type))
    if spec is None:
        raise HTTPException(status_code=422,
                            detail=f"unknown degradation kind '{body.kind}'; one of "
                                   f"{sorted(DEGRADATION_KINDS)}")
    if dtype not in spec["types"]:
        raise HTTPException(status_code=422,
                            detail=f"'{body.kind}' does not apply to a {dtype}; "
                                   f"valid for {list(spec['types'])}")
    if not body.rate_per_h > 0.0:
        raise HTTPException(status_code=422, detail="rate_per_h must be > 0")
    lo, hi = spec["band"]
    if not lo <= float(body.target) <= hi:
        raise HTTPException(status_code=422,
                            detail=f"target {body.target} outside the physical band "
                                   f"{lo}-{hi} for {body.kind} ({spec['unit']})")
    cur = st.faults.machine_value(dev.name, body.kind)
    healthy = degradation_healthy(body.kind)
    if abs(float(body.target) - healthy) < abs(cur - healthy) - 1e-9:
        raise HTTPException(status_code=422,
                            detail=f"target {body.target} is healthier than the machine "
                                   f"({cur:.4g}); equipment does not heal itself — clean "
                                   f"it with DELETE /api/faults/{{id}}?restore=true")
    now = st.faults.clock()
    d = Degradation(id=_fl.new_fault_id(now), device=dev.name, device_id=dev.id,
                    kind=body.kind, rate_per_h=float(body.rate_per_h),
                    target=float(body.target), start_ts=now)
    try:
        st.faults.add_degradation(d, ledger_entry={
            "source": "POST /api/faults/degradation", "campaign": body.campaign,
            "expected_kind": "process"})
    except FaultConflict as exc:
        raise HTTPException(status_code=409,
                            detail=f"{exc}. One degradation per (device, kind): DELETE "
                                   f"it first.")
    e = st.faults.ledger.get(d.id) or {"id": d.id, "device": d.device,
                                        "device_id": d.device_id, "point": None,
                                        "start": _fl.utc_iso(now), "params": d.params()}
    out = _entry_shape(e)
    out["kind"] = "degradation"
    out["point"] = None
    eta = d.eta_ts()
    out["eta_target"] = _fl.utc_iso(eta) if eta is not None else None
    return out


def _active_ramps(st) -> list:
    """Ramps, oscillations and setpoint holds on plant overrides, for the list."""
    from core.fault_points import SETPOINT_POINTS
    out = []
    led = st.faults.ledger
    ramps = getattr(st, "plant_ramps", {}) or {}
    for name, pts in list(ramps.items()):
        for point, r in list((pts or {}).items()):
            fid = led.open_id(("override", name, point))
            e = led.get(fid) if fid else None
            out.append({"id": fid, "kind": "setpoint" if point in SETPOINT_POINTS else "ramp",
                        "device": name, "device_id": (e or {}).get("device_id"),
                        "point": point, "start": (e or {}).get("start"),
                        "params": (e or {}).get("params", {}),
                        "current": {"value": (st.plant_alarm_overrides.get(name) or {})
                                    .get(point), "reached": bool(r.get("reached"))}})
    for name, pts in list((st.plant_alarm_overrides or {}).items()):
        for point, v in list((pts or {}).items()):
            if point in SETPOINT_POINTS and point not in (ramps.get(name) or {}):
                fid = led.open_id(("override", name, point))
                e = led.get(fid) if fid else None
                out.append({"id": fid, "kind": "setpoint", "device": name,
                            "device_id": (e or {}).get("device_id"), "point": point,
                            "start": (e or {}).get("start"),
                            "params": (e or {}).get("params", {}),
                            "current": {"value": v}})
    return out


def _active_weather() -> list:
    from core import cooling_model as cm
    out = []
    for city, w in list(cm._WEATHER.items()):
        out.append({"id": w.get("id"), "kind": "weather", "device": None, "point": None,
                    "city": city, "start": None,
                    "params": {"dry_bulb_offset_c": w["dry"], "wet_bulb_offset_c": w["wet"]},
                    "current": {"until": _fl.utc_iso(w["until"])}})
    return out


@router.get("/faults")
def list_faults():
    """Everything active: sensor faults, degradations (with their current
    value), ramps, setpoint events and weather overrides."""
    st = _store()
    out = []
    for a in st.faults.active():
        a = dict(a)
        ts = a.pop("start_ts")
        a["start"] = _fl.utc_iso(ts) if ts is not None else None
        out.append(a)
    out.extend(_active_ramps(st))
    out.extend(_active_weather())
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
    """Remove an active fault and return its closed ledger entry.

    A sensor fault simply ends. Deleting a DEGRADATION stops the ramp and does
    NOT heal the equipment: the machine keeps its value and a ``held``
    degradation entry stays open over it. ``?restore=true`` cleans it (filter
    changed, tubes brushed), resets the machine and ledgers a separate
    ``maintenance`` entry. A held entry can only be ended by a restore (409)."""
    st = _store()
    held = st.faults.held_key(fault_id)
    if st.faults.get(fault_id) is None and held is None:
        raise HTTPException(status_code=404, detail=f"No active fault '{fault_id}'")
    if held is not None and not restore:
        raise HTTPException(status_code=409,
                            detail=f"{held[0]} stays degraded ({held[1]}) until it is "
                                   f"restored: DELETE /api/faults/{fault_id}?restore=true")
    closed = st.faults.remove(fault_id, "deleted", restore=restore)
    return _entry_shape(closed or {"id": fault_id})


# ── weather override (C10) ───────────────────────────────────────────────────

def _dc_cities() -> dict:
    from core.device_state_store import _dc_city_map
    dm = _state().device_manager
    return _dc_city_map(dm.get_all_devices() if dm else [])


class WeatherBody(BaseModel):
    datacenter: Optional[str] = None      # default: every site
    dry_bulb_offset_c: float = 0.0
    # The wet-bulb SHIFT; omitted, it follows the dry bulb (constant depression).
    wet_bulb_offset_c: Optional[float] = None
    until: Optional[str] = None           # ISO-8601; or duration_s
    duration_s: Optional[float] = None
    campaign: Optional[str] = None


@router.post("/weather/override", status_code=201)
def set_weather(body: WeatherBody):
    """Offset one site's (or every site's) outdoor dry and wet bulb until
    *until*. Everything that reads the climate follows: chiller kW, the
    economiser, condenser water, the tower OA points. A context event, not a
    fault (expected_kind none)."""
    from core import cooling_model as cm
    st = _store()
    now = st.faults.clock()
    until = _fl.parse_iso(body.until) if body.until else None
    if until is None and body.duration_s is not None:
        until = now + float(body.duration_s)
    if until is None or until <= now:
        raise HTTPException(status_code=422,
                            detail="until (ISO-8601, in the future) or duration_s > 0 required")
    wet = (float(body.wet_bulb_offset_c) if body.wet_bulb_offset_c is not None
           else float(body.dry_bulb_offset_c))
    for v, nm in ((body.dry_bulb_offset_c, "dry_bulb_offset_c"), (wet, "wet_bulb_offset_c")):
        if not -25.0 <= float(v) <= 25.0:
            raise HTTPException(status_code=422, detail=f"{nm} must be within ±25 K")
    if body.dry_bulb_offset_c == 0.0 and wet == 0.0:
        raise HTTPException(status_code=422, detail="no-op override: set an offset")
    cities = _dc_cities()
    if body.datacenter is not None:
        if body.datacenter not in cities:
            raise HTTPException(status_code=404,
                                detail=f"datacenter '{body.datacenter}' not found")
        targets = {body.datacenter: cities[body.datacenter]}
    else:
        targets = dict(cities)
    entries = []
    led = st.faults.ledger
    seen = set()
    for dc, city in sorted(targets.items()):
        ck = cm._city_key(city)
        if ck in seen:
            continue
        seen.add(ck)
        params = {"datacenter": dc, "city": ck,
                  "dry_bulb_offset_c": float(body.dry_bulb_offset_c),
                  "wet_bulb_offset_c": wet,
                  "until": _fl.utc_iso(until)}
        e = led.open("weather", params=params, expected_kind="none",
                     source="POST /api/weather/override", campaign=body.campaign,
                     key=("weather", ck), start=now)
        cm.set_weather_override(city, body.dry_bulb_offset_c, wet,
                                until, fault_id=e["id"])
        entries.append({"id": e["id"], "kind": "weather", "datacenter": dc, "city": ck,
                        "start": e["start"], "until": _fl.utc_iso(until), "params": params})
    if body.datacenter is not None and entries:
        return entries[0]
    return {"kind": "weather", "entries": entries}


@router.delete("/weather/override")
def clear_weather(datacenter: Optional[str] = None):
    from core import cooling_model as cm
    st = _store()
    cities = _dc_cities()
    if datacenter is not None and datacenter not in cities:
        raise HTTPException(status_code=404, detail=f"datacenter '{datacenter}' not found")
    targets = [cities[datacenter]] if datacenter is not None else list(cities.values())
    closed = []
    for city in targets:
        w = cm.clear_weather_override(city)
        if w and w.get("id"):
            e = st.faults.ledger.close(w["id"], "cleared")
            if e:
                closed.append(e)
    return {"cleared": closed}


@router.get("/weather")
def get_weather():
    """Live outdoor dry and wet bulb per DC, with any override in force."""
    from core import cooling_model as cm
    out = []
    for dc, city in sorted(_dc_cities().items()):
        w = cm.weather_override(city)
        out.append({"datacenter": dc, "city": cm._city_key(city),
                    "dry_bulb_c": round(cm.ambient_c(city), 2),
                    "wet_bulb_c": round(cm.wet_bulb_c(city), 2),
                    "override": None if w is None else {
                        "id": w.get("id"), "dry_bulb_offset_c": w["dry"],
                        "wet_bulb_offset_c": w["wet"], "until": _fl.utc_iso(w["until"])}})
    return {"weather": out}


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


# ── context events from campaign tooling (doc 32 §13.1) ─────────────────────

class ContextBody(BaseModel):
    context: str                       # e.g. "detector_enabled", "campaign_run"
    params: dict = {}
    campaign: Optional[str] = None
    close_id: Optional[str] = None     # close an entry this endpoint opened


@router.post("/faults/context", status_code=201)
def ledger_context(body: ContextBody):
    """Ledger a context window the platform must exclude, not score: a
    detector enabled for one campaign run, a run boundary. Opens an entry (or
    closes ``close_id``). Physics is untouched."""
    led = _fl.get_ledger()
    if body.close_id:
        e = led.close(body.close_id, "cleared")
        if e is None:
            raise HTTPException(status_code=404, detail=f"No open entry '{body.close_id}'")
        return e
    params = dict(body.params or {})
    params["context"] = body.context
    return led.open("context", params=params, expected_kind="none",
                    source="POST /api/faults/context", campaign=body.campaign)

"""Publish-layer sensor faults (doc 32 §4, C4): physics stays true, the reading lies.

A sensor fault changes what a PROTOCOL serves. It never changes what the physics
reads back. The store keeps two copies of everything a renderer reads:

* **truth** — ``Device`` attributes, ``_ext_state_cache`` and
  ``_plant_state_cache``; every physics reader takes its inputs from these;
* **wire** — what BACnet, SNMP, Modbus and Redfish render, produced here from
  truth after physics and before each renderer, and always as a COPY.

The single exception is a fault on a CONTROL sensor with ``affects_control``
(§4.4): the controller holds its setpoint on the lying reading, so the store's
control block subtracts :meth:`FaultBook.control_bias` from the setpoint it
controls to. The process moves by −bias and the reading sits on setpoint, which
is exactly the hidden fault a real biased control sensor produces.

This module is pure: no store, no I/O beyond the ledger hand-off, and the clock
is injectable, so the maths is unit-testable like ``air_model.py``.

Transform order (§4.2): ``stuck`` first (returns the held value), then
``truth + bias + drift_per_h × elapsed_h + (noise_mult − 1) × σ × N(0,1)``, then
a clamp to the instrument's span, then dropout gating. A dropout lasts
``dropout_window_s`` (≥ one platform poll); each window is dropped with
probability ``dropout_pct / 100`` from the fault's own seeded RNG.

Per-tick consistency: Device-attribute and ext faults are evaluated ONCE per tick
(:meth:`FaultBook.step`) and cached, so SNMP, Modbus and Redfish serving the same
quantity in the same tick agree — as one real instrument read by two agents does.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from core.sim_seed import GLOBAL_SEED, stable_hash

# ── instrument characteristics ───────────────────────────────────────────────

#: σ of a Device attribute's own measurement noise (engineering units). The
#: inlet figure matches the store's U(±0.2) walk; the others are [judgement].
ATTR_SIGMA: Dict[str, float] = {
    "inlet_temp": 0.2, "mid_temp": 0.2, "outlet_temp": 0.3, "cpu_temp": 0.5,
    "humidity": 0.5, "dewpoint": 0.3, "fan_rpm": 50.0, "airflow": 5.0,
}
_EXT_SIGMA_DEFAULT = 0.1

#: Probe-port ext keys that render a SENSOR device's own attribute through the
#: host PDU's agent (a DPX2 on a PX2 reads ``probe_inlet_c``, which is the
#: probe's inlet_temp). A fault on the attribute must reach both renderings.
ATTR_EXT_ALIASES: Dict[str, tuple] = {
    "inlet_temp": ("probe_inlet_c",),
    "mid_temp": ("probe_mid_c",),
    "outlet_temp": ("probe_outlet_c",),
    "humidity": ("probe_humidity_pct",),
    "dewpoint": ("probe_dewpoint_c",),
}

#: Store-side control loops a lying sensor can move (§4.4).
CONTROL_POINTS = {
    "crah": {"Supply_Air_Temp"},
    "chiller": {"CHW_Supply_Temp"},
    "cdu": {"TCS_Supply_Temp"},
}

_THERMISTOR_SPAN = (-40.0, 125.0)
_PCT_SPAN = (0.0, 100.0)


def point_span(point: str) -> Optional[tuple]:
    """The transmitter range a reading cannot leave [judgement]: −40…125 °C for
    a thermistor, 0…100 % for RH, valves and speeds. None = unclamped."""
    p = point.lower()
    if p in ("humidity", "supply_humidity") or p.endswith("_pct") or p.endswith("_rh_pct") \
            or p.endswith("valve") or p in ("position", "commanded_position", "fan_speed",
                                             "speed", "pump_speed", "compressor_load",
                                             "cooling_capacity"):
        return _PCT_SPAN
    if "temp" in p or p.endswith("_c") or p == "dewpoint":
        return _THERMISTOR_SPAN
    return None


def is_binary(point: str) -> bool:
    p = point
    return (p.startswith("Alarm_") or p in ("Chiller_Running", "Run_Status", "Fan_Status",
                                             "Unit_Running", "Status_Modulating",
                                             "Filter_Dirty"))


def plant_sigma(dtype: str, point: str) -> float:
    """σ for a BACnet plant point: the PLANT_SPEC walk amplitude, which is the
    point's own noise. Store-driven points carry amp 0 there; they get a small
    floor so noise_mult still means something."""
    try:
        from core.bacnet_plant_generator import PLANT_SPEC
        for name, _u, base, amp in PLANT_SPEC.get(dtype, {}).get("ai", []):
            if name == point:
                if base is None:
                    return 0.0
                return float(amp) if amp else 0.1
    except Exception:
        pass
    return 0.1


# ── the fault ────────────────────────────────────────────────────────────────

@dataclass
class SensorFault:
    id: str                          # "flt_<utc yyyymmddThhmmss>_<6 hex>"
    device: str                      # device NAME (internal key)
    device_id: str                   # topology id (API identity)
    point: str                       # §4.6 point namespace
    bias: float = 0.0
    drift_per_h: float = 0.0
    noise_mult: float = 1.0
    stuck: bool = False
    stuck_value: Optional[float] = None
    dropout_pct: float = 0.0
    dropout_window_s: float = 30.0
    affects_control: bool = False
    duration_s: Optional[float] = None
    start_ts: float = 0.0
    seed: int = 0
    surface: str = "attr"            # attr | ext | bacnet (which cache renders it)
    sigma: float = 0.0
    # runtime, not API
    held: Optional[float] = None
    last_wire: Optional[float] = None
    rng: random.Random = field(default=None, repr=False, compare=False)  # type: ignore[assignment]
    _win: int = field(default=-1, repr=False, compare=False)
    _win_drop: bool = field(default=False, repr=False, compare=False)
    alias_last: Dict[str, float] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        if not self.seed:
            self.seed = (stable_hash(self.id) ^ GLOBAL_SEED) & 0xFFFFFFFF
        if self.rng is None:
            self.rng = random.Random(self.seed)

    # The API shape of the fault's parameters (ledger ``params``).
    def params(self) -> Dict[str, Any]:
        p = {"bias": self.bias, "drift_per_h": self.drift_per_h,
             "noise_mult": self.noise_mult, "stuck": self.stuck,
             "dropout_pct": self.dropout_pct, "dropout_window_s": self.dropout_window_s,
             "duration_s": self.duration_s, "affects_control": self.affects_control}
        if self.stuck_value is not None:
            p["stuck_value"] = self.stuck_value
        if self.affects_control:
            p["process_symptoms"] = True
        return p

    def current_bias(self, now: float) -> float:
        return self.bias + self.drift_per_h * max(0.0, now - self.start_ts) / 3600.0

    def expired(self, now: float) -> bool:
        return self.duration_s is not None and now >= self.start_ts + float(self.duration_s)

    def in_dropout(self, now: float) -> bool:
        """Is the current dropout window dropped? Draws once per window."""
        if self.dropout_pct <= 0.0:
            return False
        win = int(max(0.0, now - self.start_ts) // max(1.0, self.dropout_window_s))
        if win != self._win:
            self._win = win
            self._win_drop = self.rng.random() < (self.dropout_pct / 100.0)
        return self._win_drop

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "device": self.device, "device_id": self.device_id,
                "point": self.point, "bias": self.bias, "drift_per_h": self.drift_per_h,
                "noise_mult": self.noise_mult, "stuck": self.stuck,
                "stuck_value": self.stuck_value, "dropout_pct": self.dropout_pct,
                "dropout_window_s": self.dropout_window_s,
                "affects_control": self.affects_control, "duration_s": self.duration_s,
                "start_ts": self.start_ts, "seed": self.seed, "surface": self.surface,
                "sigma": self.sigma, "held": self.held}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "SensorFault":
        keys = {"id", "device", "device_id", "point", "bias", "drift_per_h", "noise_mult",
                "stuck", "stuck_value", "dropout_pct", "dropout_window_s",
                "affects_control", "duration_s", "start_ts", "seed", "surface",
                "sigma", "held"}
        return cls(**{k: v for k, v in d.items() if k in keys})


class FaultConflict(Exception):
    """A sensor fault is already active on (device, point)."""


# ── the read-only view ───────────────────────────────────────────────────────

class DeviceView:
    """Read-only proxy over a ``Device``: faulted attributes return their wire
    value, everything else delegates. It never writes a faulted attribute to the
    Device, because physics and the rule engine read those same attributes;
    any other write is delegated so a renderer behaves exactly as before.

    ``_dropped_attrs`` names the attributes inside a dropout window. Renderers
    that can say "unavailable" (Redfish ``Reading: null``) read it with
    ``getattr(device, "_dropped_attrs", ())``, which is ``()`` on a plain Device.
    """
    __slots__ = ("_dev", "_vals", "_dropped_attrs")

    def __init__(self, dev, vals: Dict[str, float], dropped: Iterable[str] = ()):
        object.__setattr__(self, "_dev", dev)
        object.__setattr__(self, "_vals", dict(vals))
        object.__setattr__(self, "_dropped_attrs", frozenset(dropped))

    def __getattr__(self, name):
        vals = object.__getattribute__(self, "_vals")
        if name in vals:
            return vals[name]
        return getattr(object.__getattribute__(self, "_dev"), name)

    def __setattr__(self, name, value):
        if name in object.__getattribute__(self, "_vals"):
            return                                  # never write a lie into truth
        setattr(object.__getattribute__(self, "_dev"), name, value)

    def __repr__(self):
        return f"DeviceView({self._dev!r}, faulted={sorted(self._vals)})"


# ── the registry ─────────────────────────────────────────────────────────────

class FaultBook:
    """Thread-safe registry of active sensor faults. One instance per store
    (``store.faults``). API threads write under ``self._lock``; the ticker works
    from a snapshot taken once per call, never iterating the live dict."""

    def __init__(self, ledger=None, clock: Callable[[], float] = time.time):
        self._lock = threading.RLock()
        self._faults: Dict[str, SensorFault] = {}
        self._by_point: Dict[tuple, str] = {}
        self._devices: Set[str] = set()
        self._ledger = ledger
        self.clock = clock
        # Per-tick wire cache for attr/ext faults: (device, point) -> (wire|None, delta)
        self._wire: Dict[tuple, tuple] = {}
        self._wire_tick: Dict[tuple, float] = {}

    # -- plumbing ---------------------------------------------------------------
    @property
    def ledger(self):
        if self._ledger is None:
            from core.fault_ledger import get_ledger
            return get_ledger()
        return self._ledger

    def has(self, device: str) -> bool:
        return device in self._devices

    def _snapshot(self) -> List[SensorFault]:
        with self._lock:
            return list(self._faults.values())

    def _reindex(self):
        self._devices = {f.device for f in self._faults.values()}

    def for_point(self, device: str, point: str) -> Optional[SensorFault]:
        with self._lock:
            fid = self._by_point.get((device, point))
            return self._faults.get(fid) if fid else None

    def get(self, fault_id: str) -> Optional[SensorFault]:
        with self._lock:
            return self._faults.get(fault_id)

    # -- lifecycle --------------------------------------------------------------
    def add_sensor(self, f: SensorFault, ledger_entry: Optional[dict] = None,
                   resumed: bool = False) -> SensorFault:
        """Register *f*. Faults on one point do not stack (409): stacking would
        make the ground truth ambiguous. *ledger_entry* (kwargs for
        ``FaultLedger.open``) opens the ledger entry under the same id."""
        with self._lock:
            key = (f.device, f.point)
            if key in self._by_point:
                raise FaultConflict(f"sensor fault {self._by_point[key]} already active "
                                    f"on {f.device}/{f.point}")
            if not f.start_ts:
                f.start_ts = self.clock()
            self._faults[f.id] = f
            self._by_point[key] = f.id
            self._reindex()
        if ledger_entry is not None and not resumed:
            kw = dict(ledger_entry)
            kw.setdefault("device", f.device)
            kw.setdefault("device_id", f.device_id)
            kw.setdefault("point", f.point)
            kw.setdefault("params", f.params())
            kw.setdefault("expected_kind", "sensor")
            self.ledger.open("sensor", start=f.start_ts, fault_id=f.id, **kw)
        return f

    def remove(self, fault_id: str, reason: str = "deleted",
               at: Optional[float] = None) -> Optional[dict]:
        """Remove a fault and close its ledger entry. Returns the closed entry
        (or a minimal dict when the ledger had no open entry), None if unknown."""
        with self._lock:
            f = self._faults.pop(fault_id, None)
            if f is None:
                return None
            self._by_point.pop((f.device, f.point), None)
            for k in [k for k in self._wire if k[0] == f.device and
                      (k[1] == f.point or k[1] in ATTR_EXT_ALIASES.get(f.point, ()))]:
                self._wire.pop(k, None)
                self._wire_tick.pop(k, None)
            self._reindex()
        closed = self.ledger.close(fault_id, reason, end=at)
        return closed or {"id": fault_id, "kind": "sensor", "device": f.device,
                          "device_id": f.device_id, "point": f.point,
                          "end_reason": reason}

    def expire(self, now: Optional[float] = None) -> List[dict]:
        now = self.clock() if now is None else now
        out = []
        for f in self._snapshot():
            if f.expired(now):
                e = self.remove(f.id, "expired", at=f.start_ts + float(f.duration_s))
                if e:
                    out.append(e)
        return out

    def active(self, now: Optional[float] = None) -> List[dict]:
        now = self.clock() if now is None else now
        out = []
        for f in self._snapshot():
            out.append({"id": f.id, "kind": "sensor", "device": f.device,
                        "device_id": f.device_id, "point": f.point,
                        "start_ts": f.start_ts, "params": f.params(),
                        "current": {"bias": round(f.current_bias(now), 6),
                                    "stuck": f.stuck, "held": f.held,
                                    "in_dropout": f.in_dropout(now) if f.dropout_pct else False,
                                    "last_wire": f.last_wire}})
        return out

    # -- the transform ----------------------------------------------------------
    def _transform(self, f: SensorFault, truth: float, now: float,
                   sigma: Optional[float] = None) -> Optional[float]:
        """One reading through the fault. None = inside a dropout window."""
        binary = is_binary(f.point)
        if f.stuck or f.stuck_value is not None:
            if f.held is None:
                f.held = float(f.stuck_value) if f.stuck_value is not None else float(truth)
            val = f.held
        elif binary:
            val = float(truth)
        else:
            val = float(truth) + f.current_bias(now)
            sg = f.sigma if sigma is None else sigma
            if f.noise_mult != 1.0 and sg > 0.0:
                val += (f.noise_mult - 1.0) * sg * f.rng.gauss(0.0, 1.0)
            span = point_span(f.point)
            if span is not None:
                val = max(span[0], min(span[1], val))
        if f.in_dropout(now):
            return None
        f.last_wire = val
        return val

    def wire_value(self, device: str, point: str, truth: float, now: Optional[float] = None,
                   sigma: Optional[float] = None) -> Optional[float]:
        """The doc 32 §4.2 entry point: *truth* through the active fault on
        (device, point), or *truth* unchanged when there is none. None means the
        point is inside a dropout window this instant."""
        f = self.for_point(device, point)
        if f is None:
            return truth
        return self._transform(f, truth, self.clock() if now is None else now, sigma)

    def _held(self, f: SensorFault, truth: float) -> float:
        """What a renderer holds during dropout: the last good wire reading."""
        return f.last_wire if f.last_wire is not None else truth

    # -- per-protocol application -------------------------------------------------
    def apply_bacnet(self, device: str, values: Dict[str, float],
                     now: Optional[float] = None) -> Dict[str, float]:
        """A COPY of *values* with every faulted point replaced by its wire
        value. Dropped points hold their last wire value; :meth:`dropped`
        reports them so the controller can set Reliability / Status_Flags."""
        if device not in self._devices:
            return values
        now = self.clock() if now is None else now
        out = dict(values)
        for f in self._snapshot():
            if f.device != device or f.point not in out or f.surface != "bacnet":
                continue
            truth = float(out[f.point])
            w = self._transform(f, truth, now)
            out[f.point] = self._held(f, truth) if w is None else w
        return out

    def dropped(self, device: str, now: Optional[float] = None) -> Dict[str, bool]:
        """{point: inside a dropout window} for every faulted point on *device*
        that has a dropout rate (so a window's end can be restored)."""
        if device not in self._devices:
            return {}
        now = self.clock() if now is None else now
        return {f.point: f.in_dropout(now) for f in self._snapshot()
                if f.device == device and f.dropout_pct > 0.0}

    def step(self, devices: Iterable, ext_cache: Dict[str, dict],
             now: Optional[float] = None) -> None:
        """Once per tick, after physics: expire faults, then evaluate every
        Device-attribute and ext fault ONCE into the wire cache, so every
        renderer that reads it this tick sees the same reading."""
        now = self.clock() if now is None else now
        self.expire(now)
        faults = [f for f in self._snapshot() if f.surface in ("attr", "ext")]
        if not faults:
            return
        by_name = {}
        want = {f.device for f in faults}
        for d in devices:
            nm = getattr(d, "name", None)
            if nm in want:
                by_name[nm] = d
        for f in faults:
            if f.surface == "attr":
                dev = by_name.get(f.device)
                truth = getattr(dev, f.point, None) if dev is not None else None
            else:
                truth = (ext_cache.get(f.device) or {}).get(f.point)
            if not isinstance(truth, (int, float)) or isinstance(truth, bool):
                continue
            w = self._transform(f, float(truth), now)
            delta = 0.0 if w is None else (w - float(truth))
            self._wire[(f.device, f.point)] = (w, delta, float(truth))
            self._wire_tick[(f.device, f.point)] = now

    def _cached(self, f: SensorFault, truth: float, now: float) -> tuple:
        """(wire|None, delta) for an attr/ext fault, from this tick's cache, or
        evaluated now if the ticker has not stepped it yet."""
        key = (f.device, f.point)
        hit = self._wire.get(key)
        if hit is not None:
            return hit[0], hit[1]
        w = self._transform(f, truth, now)
        delta = 0.0 if w is None else (w - truth)
        self._wire[key] = (w, delta, truth)
        self._wire_tick[key] = now
        return w, delta

    def view(self, device, now: Optional[float] = None):
        """A :class:`DeviceView` for a faulted device; the Device itself
        otherwise (zero cost on the unfaulted fleet)."""
        name = getattr(device, "name", None)
        if name not in self._devices:
            return device
        now = self.clock() if now is None else now
        vals, dropped = {}, []
        for f in self._snapshot():
            if f.device != name or f.surface != "attr":
                continue
            truth = getattr(device, f.point, None)
            if not isinstance(truth, (int, float)) or isinstance(truth, bool):
                continue
            w, _d = self._cached(f, float(truth), now)
            if w is None:
                dropped.append(f.point)
                vals[f.point] = self._held(f, float(truth))
            else:
                vals[f.point] = w
        if not vals:
            return device
        return DeviceView(device, vals, dropped)

    def apply_ext(self, device: str, ext: Dict[str, Any],
                  now: Optional[float] = None) -> Dict[str, Any]:
        """A COPY of the ext dict with faulted keys on the wire. Dropped keys
        hold their last reading and are listed under ``"__invalid__"`` so the
        Modbus map clears their validity bit and SNMP can say unavailable.
        Attribute faults on a sensor reach its probe-port keys too."""
        if device not in self._devices or not ext:
            return ext
        now = self.clock() if now is None else now
        out = None
        invalid: Set[str] = set()
        for f in self._snapshot():
            if f.device != device:
                continue
            if f.surface == "ext" and f.point in ext:
                truth = ext.get(f.point)
                if not isinstance(truth, (int, float)) or isinstance(truth, bool):
                    continue
                out = dict(ext) if out is None else out
                w, _d = self._cached(f, float(truth), now)
                if w is None:
                    out[f.point] = self._held(f, float(truth))
                    invalid.add(f.point)
                else:
                    out[f.point] = w
            elif f.surface == "attr":
                hit = self._wire.get((f.device, f.point))
                for alias in ATTR_EXT_ALIASES.get(f.point, ()):
                    a_truth = ext.get(alias)
                    if not isinstance(a_truth, (int, float)) or isinstance(a_truth, bool):
                        continue
                    out = dict(ext) if out is None else out
                    if hit is None:
                        continue                  # not stepped yet: truth this tick
                    w, delta = hit[0], hit[1]
                    if w is None:                 # dropout: hold the last reading
                        out[alias] = f.alias_last.get(alias, a_truth)
                        invalid.add(alias)
                    elif f.stuck or f.stuck_value is not None:
                        out[alias] = f.held if f.held is not None else a_truth
                    else:
                        out[alias] = float(a_truth) + delta
                        f.alias_last[alias] = out[alias]
        if out is None:
            return ext
        if invalid:
            out["__invalid__"] = invalid
        return out

    def control_bias(self, device: str, point: str, now: Optional[float] = None) -> float:
        """The bias a control loop must subtract from its setpoint (§4.4). Zero
        unless an ``affects_control`` fault is active on that control point."""
        if device not in self._devices:
            return 0.0
        f = self.for_point(device, point)
        if f is None or not f.affects_control:
            return 0.0
        return f.current_bias(self.clock() if now is None else now)

    # -- persistence (doc 32 §7.3) ------------------------------------------------
    def to_json(self) -> List[dict]:
        return [f.to_json() for f in self._snapshot()]

    def resume(self, records: Iterable[dict], saved_at: Optional[float],
               now: Optional[float] = None) -> Dict[str, List[str]]:
        """Re-register faults persisted in ``active.json``. A fault whose
        ``duration_s`` ran out during the downtime is closed at its expiry time;
        the rest are resumed with an ``op: resume`` ledger line carrying the
        gap ``[saved_at, now]``."""
        now = self.clock() if now is None else now
        got = {"resumed": [], "expired": []}
        led = self.ledger
        for rec in records or []:
            try:
                f = SensorFault.from_json(rec)
            except TypeError:
                continue
            if f.expired(now):
                led.close(f.id, "expired", end=f.start_ts + float(f.duration_s))
                got["expired"].append(f.id)
                continue
            try:
                self.add_sensor(f, resumed=True)
            except FaultConflict:
                continue
            if led.adopt(f.id):
                led.resume(f.id, saved_at if saved_at else now, now)
            got["resumed"].append(f.id)
        return got


# ── the process-wide active book (renderers hold no store reference) ─────────

_ACTIVE: Optional[FaultBook] = None


def set_active(book: Optional[FaultBook]) -> None:
    global _ACTIVE
    _ACTIVE = book


def get_active() -> Optional[FaultBook]:
    return _ACTIVE


def sigma_for(surface: str, dtype: str, point: str) -> float:
    if surface == "bacnet":
        return plant_sigma(dtype, point)
    if surface == "attr":
        return ATTR_SIGMA.get(point, 0.1)
    return _EXT_SIGMA_DEFAULT


__all__ = ["SensorFault", "FaultBook", "FaultConflict", "DeviceView", "point_span",
           "is_binary", "CONTROL_POINTS", "ATTR_SIGMA", "ATTR_EXT_ALIASES",
           "set_active", "get_active", "sigma_for"]

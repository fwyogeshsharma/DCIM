"""BACnet/IP simulator REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from api.models.schemas import OkResponse, EV2DeviceSnapshot, EV2PanelMetrics, EV2CircuitMetrics

router = APIRouter(prefix="/bacnet", tags=["BACnet"])


def _state() -> AppState:
    return AppState.get()


class BACnetConfig(BaseModel):
    base_instance: int   = 40001
    frequency_hz:  float = 50.0
    port:          int   = 47808


@router.get("/power-summary")
def bacnet_power_summary():
    """Live facility power draw and PUE, from the power cascade
    (server live watts → PDU → UPS → EV2 → facility = IT + cooling)."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        return {"it_watts": 0.0, "cooling_watts": 0.0, "facility_watts": 0.0, "pue": 0.0}
    return st.get_power_summary()


class UtilityOutage(BaseModel):
    datacenter: str
    failed: bool


class ATSFault(BaseModel):
    device_id: str
    failed: bool


@router.get("/electrical")
def electrical_status():
    """Per-DC utility/generator transfer state: ATS position, genset status, how
    many mechanical load blocks are energized, and whether the UPS input is live."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        return {}
    return st.get_electrical_status()


@router.get("/electrical/devices")
def electrical_device_metrics():
    """Per-device live metrics for utility feed / switchgear / ATS / MCC / MPP —
    powers the Live Metrics page electrical tabs (same values served over SNMP)."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        return {"devices": []}
    return {"devices": st.get_electrical_device_metrics()}


@router.post("/electrical/utility", response_model=OkResponse)
def set_utility(body: UtilityOutage):
    """Drop or restore one datacenter's utility feed. Everything downstream — the
    genset start signal, the ATS transfer, the staged mechanical restart, the UPS
    battery discharge — follows from this single input, as it does on site."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        raise HTTPException(503, "simulator not running")
    st.set_utility_outage(body.datacenter, body.failed)
    return OkResponse(ok=True)


@router.post("/electrical/ats", response_model=OkResponse)
def set_ats(body: ATSFault):
    """Fail or clear one transfer switch. In this 2N electrical plant that kills
    only its own side: that UPS drops to battery and that MCC's half of the
    mechanical plant stops, leaving the other side carrying the datacenter."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        raise HTTPException(503, "simulator not running")
    st.set_ats_failed(body.device_id, body.failed)
    return OkResponse(ok=True)


@router.get("/status")
def bacnet_status():
    s  = _state()
    bc = s.bacnet
    if bc is None or not bc.is_running():
        return {
            "running":        False,
            "base_instance":  getattr(bc, "_base_instance", 40001),
            "frequency_hz":   getattr(bc, "_frequency_hz",  50.0),
            "port":           getattr(bc, "_port",          47808),
            "active_devices": 0,
            "devices":        [],
        }
    return {
        "running":        True,
        "base_instance":  getattr(bc, "_base_instance", 40001),
        "frequency_hz":   getattr(bc, "_frequency_hz",  50.0),
        "port":           getattr(bc, "_port",          47808),
        "active_devices": bc.device_count(),
        "devices":        bc.get_device_summary(),
    }


@router.post("/start", response_model=OkResponse)
def bacnet_start(cfg: BACnetConfig):
    import re as _re
    from core.bacnet_ev2_generator import clamp_circuit_count as _clamp_circuit_count
    s = _state()
    if s.bacnet is None:
        raise HTTPException(status_code=503, detail="BACnet controller not initialised")
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.bacnet.is_running():
        raise HTTPException(status_code=409, detail="BACnet already running")

    from core.device_manager import DeviceType
    ev2_devices = [
        d for d in s.device_manager.get_all_devices()
        if d.device_type == DeviceType.ENERGY_MONITOR
    ]
    if not ev2_devices:
        raise HTTPException(
            status_code=400,
            detail="No energy_monitor devices in topology. "
                   "Add Verdigris EV2 devices (device_type=energy_monitor) and bind IPs first."
        )

    # EV2 devices are mgmt_only: ip_address is "" and real IP is mgmt_ip.
    # Only start on IPs that are actually bound — unbound IPs have no OS route.
    bound_set = set(s.bound_ips) | set(s.gnmi_bound_ips)
    bound_devices = [
        d for d in ev2_devices
        if (d.ip_address and d.ip_address in bound_set)
        or (getattr(d, "mgmt_ip", None) and d.mgmt_ip in bound_set)
    ]

    if not bound_devices:
        raise HTTPException(
            status_code=400,
            detail=f"Found {len(ev2_devices)} EV2 device(s) but none of their IPs are bound. "
                   "Bind IPs first (Binding panel → Bind IPs), then start BACnet."
        )

    # Build per-device circuit count.
    # Walk the power graph: EV2 → panel → count panel's other power connections.
    # This is correct regardless of whether monitored_panel is set on the Device.
    # Fallback: parse capacity from model_name ("Verdigris EV2-42" → 42).
    _power_edges = s.topology.get_edges_by_layer("power") if s.topology else []

    # Build id→device_type and id→power_draw_w maps.
    _id_to_type: dict = {}
    _id_to_draw: dict = {}
    # NOTE: source the type/draw maps from device_manager, NOT s.topology.
    # TopologyEngine has no `.devices` attribute (only get_all_devices()), so the
    # old `hasattr(s.topology, 'devices')` guard was always False — leaving both
    # maps empty. That silently broke the UPS/generator exclusion below (every
    # .get() returned None, so upstream feeds were counted as active circuits)
    # and the rated_kw sizing (_through stayed empty). Mirrors main_window.py.
    if s.device_manager:
        for dev in s.device_manager.get_all_devices():
            _id_to_type[dev.id] = dev.device_type
            _id_to_draw[dev.id] = getattr(dev, 'power_draw_w', 0) or 0

    # Power-chain rank orders the distribution hierarchy from source (0) to leaf
    # load. A device's *parents* are its lower-rank power neighbours (the feeds
    # above it); loads/plant fall through to the leaf rank. Borrowed from the state
    # store rather than restated, so adding an electrical tier there cannot leave
    # this endpoint walking a stale hierarchy.
    from core.device_state_store import DeviceStateStore as _Store
    _POWER_RANK = _Store._POWER_RANK
    _LEAF_RANK = _Store._LEAF_RANK

    def _rank(i):
        return _POWER_RANK.get(_id_to_type.get(i), _LEAF_RANK)

    def _parents(i):
        out = []
        for u, v, _w in _power_edges:
            if i in (u, v):
                nb = v if u == i else u
                if _id_to_type.get(nb) == "energy_monitor":
                    continue            # meters clamp on, they don't feed power
                if _rank(nb) < _rank(i):
                    out.append(nb)
        return out

    # Power-flow with redundant-feed splitting: each device's draw divides
    # equally among its feeds and propagates up the hierarchy. Dual-corded
    # servers (2 PDU feeds) put half their load on each side, so an IT meter
    # reads only the current through its own branch — summing both A/B meters
    # recovers the full load instead of double-counting it. _through[panel] is
    # then the real kW a meter clamped on that panel measures.
    _through: dict = {}
    if _power_edges and _id_to_draw:
        _incoming: dict = {}
        for nid in sorted(_id_to_type, key=_rank, reverse=True):
            thr = _id_to_draw.get(nid, 0) + _incoming.get(nid, 0)
            _through[nid] = thr
            ps = _parents(nid)
            if ps and thr:
                share = thr / len(ps)
                for p in ps:
                    _incoming[p] = _incoming.get(p, 0) + share

    circuits_map: dict = {}
    rated_kw_map: dict = {}
    device_ips: list = []
    for d in bound_devices:
        ip = d.ip_address or getattr(d, "mgmt_ip", None)
        if ip:
            device_ips.append(ip)
            if _power_edges:
                # Step 1: find the PDU this EV2 is wired to (its power neighbour)
                pdu_id = next(
                    (v if u == d.id else u)
                    for u, v, _ in _power_edges
                    if d.id in (u, v)
                ) if any(d.id in (u, v) for u, v, _ in _power_edges) else None

                if pdu_id:
                    # Real peak load flowing through the monitored panel
                    # (redundant feeds already split). Drives the meter's kW.
                    watts = _through.get(pdu_id, 0)
                    if watts > 0:
                        rated_kw_map[ip] = watts / 1000.0

                    # Step 2: count DOWNSTREAM power connections only.
                    # Exclude the EV2 itself and any upstream device (UPS/generator)
                    # — CTs only clamp onto output breaker conductors.
                    _upstream_types = {"ups", "generator"}
                    downstream = [
                        (v if u == pdu_id else u)
                        for u, v, _ in _power_edges
                        if pdu_id in (u, v) and d.id not in (u, v)
                        and _id_to_type.get(v if u == pdu_id else u) not in _upstream_types
                    ]
                    active = len(downstream)
                    if active > 0:
                        # capacity = the meter's CT-channel count from its model name
                        # (e.g. "EV2-84" → 84), snapped to a real EV2 size (24/42/84).
                        # A branch can only be metered if there's a channel for it, so
                        # active is capped at the channel count — the fleet is expected
                        # to size the meter (and spill to a new RPP) so this rarely binds.
                        m = _re.search(r"EV2-(\d+)", d.model_name or "")
                        channels = _clamp_circuit_count(int(m.group(1))) if m \
                            else _clamp_circuit_count(active)
                        circuits_map[ip] = (channels, min(active, channels))
                        continue

            # Fallback: derive from model name — all circuits active
            m = _re.search(r"EV2-(\d+)", d.model_name or "")
            cap = _clamp_circuit_count(int(m.group(1))) if m else 42
            circuits_map[ip] = (cap, cap)

    unbound = len(ev2_devices) - len(bound_devices)

    # Chiller-plant BACnet devices (chiller/pump/cooling_tower/valve) on bound
    # mgmt IPs. rated_kw = nameplate electrical draw, used to size the kW points.
    _PLANT_TYPES = {DeviceType.CHILLER, DeviceType.PUMP,
                    DeviceType.COOLING_TOWER, DeviceType.VALVE, DeviceType.CRAH,
                    DeviceType.CDU}
    plant_devices: list = []
    _plant_total = 0
    for d in s.device_manager.get_all_devices():
        if d.device_type not in _PLANT_TYPES:
            continue
        _plant_total += 1
        ip = (d.ip_address if d.ip_address in bound_set else None) \
            or (d.mgmt_ip if getattr(d, "mgmt_ip", None) in bound_set else None)
        if ip:
            plant_devices.append({
                "ip": ip,
                "name": d.name,
                "device_type": d.device_type.value,
                "rated_kw": (d.power_draw_w or 0) / 1000.0,
            })

    _plant_unbound = _plant_total - len(plant_devices)
    if _plant_unbound:
        s.notify_ui("log_bacnet",
                    f"[BACnet] Warning: {_plant_unbound} of {_plant_total} chiller-plant "
                    f"device(s) skipped — mgmt IPs not bound. Bind IPs, then restart BACnet.",
                    "warning")

    s.start_ticker_if_needed()

    if not s.bacnet._log_cb:
        s.bacnet.set_log_callback(
            lambda msg, lvl="info": s.notify_ui("log_bacnet", msg, lvl)
        )

    started = s.bacnet.start(
        device_ips=device_ips,
        base_instance=cfg.base_instance,
        circuits_map=circuits_map,
        frequency_hz=cfg.frequency_hz,
        port=cfg.port,
        rated_kw_map=rated_kw_map,
        plant_devices=plant_devices,
    )

    if not started:
        # start() already logged the specific failure (e.g. port in use).
        s.notify_ui("sync_bacnet")
        raise HTTPException(
            status_code=409,
            detail=f"BACnet failed to start — could not bind port {cfg.port}. "
                   f"Another instance or BACnet tool may already own it.",
        )

    if s.state_store and hasattr(s.state_store, "enable_bacnet"):
        s.state_store.enable_bacnet(s.bacnet)

    if unbound:
        s.notify_ui("log_bacnet",
                    f"[BACnet] Warning: {unbound} EV2 device(s) skipped — IPs not bound.",
                    "warning")
    s.notify_ui("console_log",
                f"[BACnet] Started — {len(device_ips)} EV2 device(s), "
                f"{len(plant_devices)} chiller-plant device(s).", "success")
    s.notify_ui("sync_bacnet")
    return OkResponse(message="BACnet simulator started")


@router.post("/stop", response_model=OkResponse)
def bacnet_stop():
    s = _state()
    if s.bacnet is None:
        raise HTTPException(status_code=503, detail="BACnet controller not initialised")
    if not s.bacnet.is_running():
        return OkResponse(message="BACnet was not running")

    if s.state_store and hasattr(s.state_store, "disable_bacnet"):
        s.state_store.disable_bacnet()

    s.bacnet.stop()
    s.stop_ticker_if_idle()
    s.notify_ui("console_log", "[BACnet] Stopped.", "info")
    s.notify_ui("sync_bacnet")
    return OkResponse(message="BACnet simulator stopped")


@router.get("/ev2/metrics", response_model=list[EV2DeviceSnapshot])
def ev2_metrics():
    """Return current BACnet present-values for all running EV2 devices.

    Returns an empty list (not 503) when BACnet is not running so the
    frontend can show a graceful offline state.
    """
    s = _state()
    if s.bacnet is None or not s.bacnet.is_running():
        return []

    snapshots = s.bacnet.get_telemetry_snapshot()

    # Build topology helpers once for all snapshots
    from core.device_manager import DeviceType as _DT
    _power_edges = s.topology.get_edges_by_layer("power") if s.topology else []
    _dm          = s.device_manager

    # IP → EV2 Device object (for topology graph lookups)
    _ip_to_ev2: dict = {}
    if _dm:
        for _d in _dm.get_devices_by_type(_DT.ENERGY_MONITOR):
            _ip = _d.ip_address or getattr(_d, "mgmt_ip", "")
            if _ip:
                _ip_to_ev2[_ip] = _d

    result: list[EV2DeviceSnapshot] = []

    for snap in snapshots:
        if snap.get("kind", "ev2") != "ev2":
            continue   # chiller-plant devices are served by /plant/metrics
        v = snap["values"]

        def _f(key: str) -> float | None:
            val = v.get(key)
            return float(val) if val is not None else None

        # ── Resolve panel name + ordered circuit→device map ───────────
        pdu_name:          str | None            = None
        circuit_names:     dict[int, str]        = {}

        ev2_dev = _ip_to_ev2.get(snap["ip"])
        if ev2_dev and _power_edges and _dm:
            ev2_id = ev2_dev.id

            # Step 1: find the electrical panel this EV2 is connected to via power edge
            pdu_id = next(
                (v2 if u == ev2_id else u)
                for u, v2, _ in _power_edges
                if ev2_id in (u, v2)
            ) if any(ev2_id in (u, v2) for u, v2, _ in _power_edges) else None

            if pdu_id:
                pdu_dev = _dm.get_device(pdu_id)
                pdu_name = pdu_dev.name if pdu_dev else None

                # Step 2: circuit→device names come from the store's authoritative,
                # slot-stable branch order (get_ev2_circuit_pdus) — NOT a fresh
                # name-sort here, which would drift out of step with the live values
                # (those meter that same ordered list). None slots are spare/freed CT
                # channels and get no device name.
                order = (s.state_store.get_ev2_circuit_pdus().get(snap["ip"], [])
                         if s.state_store else [])
                for i, did in enumerate(order):
                    if did is None:
                        continue
                    bd = _dm.get_device(did)
                    if bd is not None:
                        circuit_names[i + 1] = bd.name

        # ── Build panel metrics ────────────────────────────────────────
        panel = EV2PanelMetrics(
            total_kw=_f("Panel_Total_kW"),
            total_kwh=_f("Panel_Total_kWh"),
            rated_kw=(float(snap["rated_kw"])
                      if snap.get("rated_kw") is not None else None),
            voltage_pha=_f("Voltage_PhA"),
            voltage_phb=_f("Voltage_PhB"),
            voltage_phc=_f("Voltage_PhC"),
            current_pha=_f("Current_PhA"),
            current_phb=_f("Current_PhB"),
            current_phc=_f("Current_PhC"),
            rated_current=(float(snap["rated_current"])
                           if snap.get("rated_current") is not None else None),
            frequency=_f("Line_Frequency"),
            power_factor=_f("Panel_PF"),
            voltage_thd=_f("Voltage_THD"),
            current_thd=_f("Current_THD"),
            harmonic_3=_f("Harmonic_3_Current"),
            harmonic_5=_f("Harmonic_5_Current"),
            harmonic_7=_f("Harmonic_7_Current"),
            harmonic_9=_f("Harmonic_9_Current"),
            alarm_overcurrent=       bool(v.get("Alarm_Overcurrent",      0)),
            alarm_voltage_imbalance= bool(v.get("Alarm_VoltageImbalance", 0)),
            alarm_high_thd=          bool(v.get("Alarm_HighTHD",          0)),
            alarm_phase_loss=        bool(v.get("Alarm_PhaseLoss",        0)),
            alarm_sensor_fault=      bool(v.get("Alarm_SensorFault",      0)),
            alarm_undervoltage=      bool(v.get("Alarm_Undervoltage",     0)),
            alarm_underfrequency=    bool(v.get("Alarm_UnderFrequency",   0)),
        )

        # ── Build circuit list ─────────────────────────────────────────
        circuits: list[EV2CircuitMetrics] = []
        for n in range(1, snap["circuits"] + 1):
            lb = f"Ckt{n:02d}"
            circuits.append(EV2CircuitMetrics(
                circuit=n,
                label=lb,
                device_name=circuit_names.get(n),
                current=_f(f"{lb}_Current"),
                kw=_f(f"{lb}_kW"),
                kwh=_f(f"{lb}_kWh"),
                pf=_f(f"{lb}_PF"),
                thd=_f(f"{lb}_THD"),
            ))

        result.append(EV2DeviceSnapshot(
            ip=snap["ip"],
            instance=snap["instance"],
            name=snap["name"],
            circuits=snap["circuits"],
            monitored_pdu_name=pdu_name,
            panel=panel,
            circuit_list=circuits,
        ))

    return result


@router.get("/plant/metrics")
def plant_metrics():
    """Current BACnet present-values for all running chiller-plant devices
    (chiller / pump / cooling_tower / valve). Empty list when not running."""
    s = _state()
    if s.bacnet is None or not s.bacnet.is_running():
        return []
    out = []
    for snap in s.bacnet.get_telemetry_snapshot():
        kind = snap.get("kind", "ev2")
        if not kind.startswith("plant:"):
            continue
        out.append({
            "ip":          snap["ip"],
            "instance":    snap["instance"],
            "name":        snap["name"],
            "device_type": kind.split(":", 1)[1],
            "values":      snap["values"],
        })
    return out


# ── Per-device plant alarm overrides (Metric Tick window) ────────────────────

class PlantOverride(BaseModel):
    device: str               # topology device id (stable across renames)
    point:  str               # binary point, e.g. "Alarm_FlowLoss" or "Chiller_Running"
    value:  float | None = None   # None = clear; alarm-on = 1.0; unit-off = 0.0


def _device_ip(device_id: str) -> str | None:
    """Resolve a topology device id to the IP the override map is keyed by — the
    device's BACnet bind address, which the controller matches on (dev.device_ip).
    Plant/EV2 devices are OT gear on the management plane, so that address is
    mgmt_ip; their production ip_address is empty. Falling back to ip_address kept
    keying them all under "" — a single colliding, never-matched bucket, so plant
    fault injection silently did nothing and never showed ACTIVE."""
    dm = _state().device_manager
    if dm is None:
        return None
    dev = dm.get_device(device_id)
    if dev is None:
        return None
    return (getattr(dev, "mgmt_ip", None) or getattr(dev, "ip_address", None)) or None


@router.get("/plant/overrides")
def get_plant_overrides(device: str | None = None):
    """Current per-device forced binary overrides (keyed by IP). With ?device=ID,
    returns just that device's forced points; otherwise the whole map."""
    st = getattr(_state(), "state_store", None)
    ov = getattr(st, "plant_alarm_overrides", {}) if st else {}
    if device is not None:
        ip = _device_ip(device)
        return {"device": device, "overrides": ov.get(ip, {}) if ip else {}}
    return {"overrides": ov}


@router.post("/plant/overrides", response_model=OkResponse)
def set_plant_override(body: PlantOverride):
    """Force (or clear) a single plant device's binary point. Forcing
    Alarm_* = 1.0 trips that one unit's fault physics + cascade; a running-status
    point = 0.0 stops it. value=null clears the override. The device is addressed
    by its stable topology id (resolved to IP server-side)."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    ip = _device_ip(body.device)
    if ip is None:
        raise HTTPException(status_code=404, detail=f"Device '{body.device}' not found")
    ov = st.plant_alarm_overrides
    dev = ov.setdefault(ip, {})
    if body.value is None:
        dev.pop(body.point, None)
        if not dev:
            ov.pop(ip, None)
        return OkResponse(message=f"Cleared {body.point}")
    dev[body.point] = float(body.value)
    return OkResponse(message=f"Set {body.point}={body.value}")


class ChillerReset(BaseModel):
    device: str               # topology device id of the chiller


@router.get("/plant/chiller-trips")
def get_chiller_trips():
    """Chillers latched out by their high head-pressure safety.

    Autonomous protection, NOT an operator override: it fires when the condenser
    loop overheats (typically because tower rejection was lost), so it does not
    appear in /plant/overrides.
    """
    st = getattr(_state(), "state_store", None)
    names = st.get_chiller_trips() if st else []
    dm = _state().device_manager
    ids = {}
    if dm:
        ids = {d.name: d.id for d in dm.get_all_devices() if d.name in set(names)}
    out = []
    for n in names:
        dc = st._dc_of_chiller(n) if st and hasattr(st, "_dc_of_chiller") else None
        degraded = bool(dc and hasattr(st, "cooling_degraded") and st.cooling_degraded(dc))
        out.append({"name": n, "device": ids.get(n), "dc": dc, "degraded": degraded})
    # A trip is a real cooling loss only where no standby could cover it.
    return {"tripped": out, "degraded": any(t["degraded"] for t in out)}


@router.post("/plant/chiller-reset", response_model=OkResponse)
def reset_chiller_trip(body: ChillerReset):
    """Manually reset a latched high head-pressure trip.

    Refuses while condenser water is still hot: a real HP cutout will not hold in
    until head pressure has actually come down, and restarting into the same
    condition would only trip the machine again.
    """
    s = _state()
    st = getattr(s, "state_store", None)
    if st is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    dev = s.device_manager.get_device(body.device) if s.device_manager else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{body.device}' not found")
    result = st.reset_chiller_trip(dev.name)
    if result == "reset":
        s.notify_ui("sync_devices")
        return OkResponse(message=f"{dev.name} high-pressure trip reset")
    # A refused reset is an EXPECTED outcome (loop still hot / not tripped), not an
    # error — return it as ok:false with the real reason so the UI can show it,
    # instead of a generic 4xx the frontend flattens to "conflicts with state".
    if result == "not tripped":
        return OkResponse(ok=False, message=f"{dev.name} is not tripped")
    return OkResponse(ok=False, message=f"Reset refused — {result}")

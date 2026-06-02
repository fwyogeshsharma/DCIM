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
    # Fallback: parse capacity from model_name ("Verdigris EV2-24" → 24).
    _power_edges = s.topology.get_edges_by_layer("power") if s.topology else []

    # Build id→device_type map for upstream-filtering
    _id_to_type: dict = {}
    if s.topology and hasattr(s.topology, 'devices'):
        for dev in s.topology.devices:
            _id_to_type[dev.id] = getattr(dev, 'device_type', None)

    circuits_map: dict = {}
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
                        # capacity = model name (e.g. "EV2-12" → 12), active = downstream count
                        m = _re.search(r"EV2-(\d+)", d.model_name or "")
                        capacity = int(m.group(1)) if m else active
                        circuits_map[ip] = (max(capacity, active), active)
                        continue

            # Fallback: derive from model name — all circuits active
            m = _re.search(r"EV2-(\d+)", d.model_name or "")
            cap = int(m.group(1)) if m else 42
            circuits_map[ip] = (cap, cap)

    unbound = len(ev2_devices) - len(bound_devices)

    s.start_ticker_if_needed()

    if not s.bacnet._log_cb:
        s.bacnet.set_log_callback(
            lambda msg, lvl="info": s.notify_ui("log_bacnet", msg, lvl)
        )

    s.bacnet.start(
        device_ips=device_ips,
        base_instance=cfg.base_instance,
        circuits_map=circuits_map,
        frequency_hz=cfg.frequency_hz,
        port=cfg.port,
    )

    if s.state_store and hasattr(s.state_store, "enable_bacnet"):
        s.state_store.enable_bacnet(s.bacnet)

    if unbound:
        s.notify_ui("log_bacnet",
                    f"[BACnet] Warning: {unbound} EV2 device(s) skipped — IPs not bound.",
                    "warning")
    s.notify_ui("console_log",
                f"[BACnet] Started — {len(device_ips)} EV2 device(s).", "success")
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

                # Step 2: collect DOWNSTREAM power neighbours only (exclude upstream UPS/generator)
                _upstream_types = {"ups", "generator"}
                neighbor_ids = [
                    (v2 if u == pdu_id else u)
                    for u, v2, _ in _power_edges
                    if pdu_id in (u, v2) and ev2_id not in (u, v2)
                ]
                neighbors = [
                    _dm.get_device(nid) for nid in neighbor_ids
                ]
                neighbors = [
                    n for n in neighbors
                    if n is not None
                    and getattr(n, 'device_type', None) not in _upstream_types
                ]
                neighbors.sort(key=lambda d: d.name)
                circuit_names = {i + 1: d.name for i, d in enumerate(neighbors)}

        # ── Build panel metrics ────────────────────────────────────────
        panel = EV2PanelMetrics(
            total_kw=_f("Panel_Total_kW"),
            total_kwh=_f("Panel_Total_kWh"),
            voltage_pha=_f("Voltage_PhA"),
            voltage_phb=_f("Voltage_PhB"),
            voltage_phc=_f("Voltage_PhC"),
            current_pha=_f("Current_PhA"),
            current_phb=_f("Current_PhB"),
            current_phc=_f("Current_PhC"),
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

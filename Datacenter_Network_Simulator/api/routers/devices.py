"""Device CRUD REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from core.device_state_store import _get_ext_state
from core.redfish_data_generator import _live_watts
from api.models.schemas import (
    DeviceInfo,
    IfaceStats,
    DevicesResponse,
    AddDeviceRequest,
    EditDeviceRequest,
    NewDeviceLink,
    OkResponse,
)

router = APIRouter(prefix="/devices", tags=["Devices"])


def _state() -> AppState:
    return AppState.get()


def _invalidate_power(s: AppState) -> None:
    """Drop the live power-cascade cache so a topology change (device add/remove
    or power-feed edit) ripples up to the PDU/UPS/RPP/EV2 meters next tick."""
    ss = getattr(s, "state_store", None)
    if ss is not None:
        try:
            ss.invalidate_power_context()
        except Exception:
            pass


def _invalidate_cooling(s: AppState) -> None:
    """Drop the cached CDU cold-plate loop map after a cooling-topology change, so a
    hot-added DLC server is counted on its CDU's loop (and off the room air balance)
    from the next tick instead of only after a restart."""
    ss = getattr(s, "state_store", None)
    if ss is not None:
        try:
            ss.invalidate_cooling_context()
        except Exception:
            pass


_NO_IFACE_TYPES = {"ups", "pdu", "floor_pdu", "rpp", "sensor", "generator"}


def _liquid_servers() -> set:
    """Server names on a CDU cold-plate (direct-to-chip liquid) loop — derived
    from the cooling topology, so it's valid even when BACnet isn't running."""
    st = getattr(_state(), "state_store", None)
    if st is None:
        return set()
    try:
        return st._liquid_cooled_servers()
    except Exception:
        return set()


def _server_power_state(device) -> str | None:
    """Live BMC power state — only available while the Redfish sim runs."""
    rf = _state().redfish
    if rf is None or not rf.is_running():
        return None
    return rf.get_power_state(getattr(device, "mgmt_ip", "") or device.ip_address)


def _iface_aggregates(device, dt: str) -> dict:
    if dt in _NO_IFACE_TYPES or not hasattr(device, "interfaces"):
        return {
            "total_rx_bytes": None, "total_tx_bytes": None,
            "total_errors": None,   "total_discards": None,
            "flapping_count": None, "interfaces_up": None,
            "interfaces_total": None, "iface_stats": [],
        }
    ifaces = device.interfaces
    # Effective oper-status: an interface with no neighbor is down regardless of
    # its raw oper_status (which defaults to 1). This mirrors the SNMP agent
    # (snmprec_generator) so the live-metrics up-count matches CDP/LLDP links
    # instead of reporting every unconnected port as up.
    def _eff_oper(i) -> int:
        return 2 if i.connected_to_device is None else i.oper_status

    # Counter reporting rules:
    #   - unconnected port  → no traffic ever, report zeros
    #   - connected but down (manually broken / flapping) → freeze last value
    #     (the tick loop already stops incrementing it)
    #   - connected and up  → live values
    def _val(i, attr) -> int:
        return 0 if i.connected_to_device is None else getattr(i, attr)

    stats = [
        IfaceStats(
            index=i.index,
            name=i.name,
            oper_status=_eff_oper(i),
            speed=i.speed,
            in_octets=_val(i, "in_octets"),
            out_octets=_val(i, "out_octets"),
            in_errors=_val(i, "in_errors"),
            out_errors=_val(i, "out_errors"),
            in_discards=_val(i, "in_discards"),
            out_discards=_val(i, "out_discards"),
            in_unicast_pkts=_val(i, "in_octets") // 1500,
            out_unicast_pkts=_val(i, "out_octets") // 1500,
        )
        for i in ifaces
    ]
    return {
        "total_rx_bytes":   sum(_val(i, "in_octets")  for i in ifaces),
        "total_tx_bytes":   sum(_val(i, "out_octets") for i in ifaces),
        "total_errors":     sum(_val(i, "in_errors")  + _val(i, "out_errors")   for i in ifaces),
        "total_discards":   sum(_val(i, "in_discards") + _val(i, "out_discards") for i in ifaces),
        "flapping_count":   sum(1 for i in ifaces if _eff_oper(i) != 1),
        "interfaces_up":    sum(1 for i in ifaces if _eff_oper(i) == 1),
        "interfaces_total": len(ifaces),
        "iface_stats":      stats,
    }


def _device_to_info(device) -> DeviceInfo:
    ext = _get_ext_state(device.name)
    dt  = device.device_type.value
    sessions = ext.get("bgp_sessions", [])
    return DeviceInfo(
        id=device.id,
        name=device.name,
        device_type=dt,
        vendor=device.vendor.value,
        ip_address=device.ip_address,
        mgmt_ip=getattr(device, "mgmt_ip", None),
        snmp_port=device.snmp_port,
        gnmi_port=device.gnmi_port,
        interface_count=device.interface_count,
        cpu_usage=getattr(device, "cpu_usage", 0.0),
        memory_used=getattr(device, "memory_used", 0.0),
        memory_total=getattr(device, "memory_total", 0.0),
        disk_used=getattr(device, "disk_used", 0.0),
        disk_total=getattr(device, "disk_total", 0.0),
        sys_location=getattr(device, "sys_location", ""),
        sys_contact=getattr(device, "sys_contact", ""),
        uptime=getattr(device, "sys_uptime", 0),
        model_name=getattr(device, "model_name", None),
        os_name=getattr(device, "os_name", None),
        os_version=getattr(device, "os_version", None),
        country=getattr(device, "country", ""),
        datacenter_city=getattr(device, "datacenter_city", ""),
        datacenter=getattr(device, "datacenter", ""),
        room=getattr(device, "room", ""),
        floor=getattr(device, "floor", ""),
        rack_row=getattr(device, "rack_row", 0),
        rack_num=getattr(device, "rack_num", 0),
        rack_unit=getattr(device, "rack_unit", 0),
        metrics_enabled=getattr(device, "metrics_enabled", True),
        cpu_temp=getattr(device, "cpu_temp", None),
        inlet_temp=getattr(device, "inlet_temp", None),
        power_watts=(float(_live_watts(device)) or None) if dt == "server" else None,
        liquid_cooled=(device.name in _liquid_servers()) if dt == "server" else None,
        fan_rpm=getattr(device, "fan_rpm", None) if dt == "server" else None,
        power_state=_server_power_state(device) if dt == "server" else None,
        mid_temp=getattr(device, "mid_temp", None)       if dt == "sensor" and getattr(device, "model_name", "") == "Raritan DPX2-T3H1" else None,
        # Exhaust temp: servers report chassis exhaust (BMC/Redfish); the
        # Raritan DPX2-T3H1 rack sensor reports its top probe.
        outlet_temp=getattr(device, "outlet_temp", None) if dt == "server" or (dt == "sensor" and getattr(device, "model_name", "") == "Raritan DPX2-T3H1") else None,
        humidity=getattr(device, "humidity", None) if dt == "sensor" else None,
        dewpoint=getattr(device, "dewpoint", None) if dt == "sensor" else None,
        # Airflow (m/s): server chassis exhaust velocity + NetBotz room sensors.
        airflow=getattr(device, "airflow", None)   if dt in ("server", "sensor") else None,
        ups_status=ext.get("ups_status")           if dt == "ups" else None,
        ups_output_load=ext.get("ups_output_load") if dt == "ups" else None,
        ups_battery_status=ext.get("ups_battery_status") if dt == "ups" else None,
        ups_input_voltage=ext.get("ups_input_voltage")   if dt == "ups" else None,
        ups_input_frequency=ext.get("ups_input_frequency") if dt == "ups" else None,
        ups_fan_status=ext.get("ups_fan_status")         if dt == "ups" else None,
        ups_charger_status=ext.get("ups_charger_status") if dt == "ups" else None,
        ups_rectifier_status=ext.get("ups_rectifier_status") if dt == "ups" else None,
        ups_phase_status=ext.get("ups_phase_status")     if dt == "ups" else None,
        ups_operating_mode=(
            "bypass"  if ext.get("ups_bypass_status") == "on"
            else "battery" if ext.get("ups_status") in ("on_battery", "low_battery")
            else "online"  if "ups_status" in ext
            else None) if dt == "ups" else None,
        ups_battery_health=ext.get("ups_battery_health") if dt == "ups" else None,
        ups_energy_kwh=ext.get("ups_energy_kwh")         if dt == "ups" else None,
        # Battery string voltage — a 480 V VRLA string (40× 12 V blocks, 240 cells):
        # float/charged ~544 V, sagging toward the ~420 V end-of-discharge cutoff as it
        # drains. 416 + 128×charge-fraction ⇒ 544 (normal) / 480 (on battery) / 435
        # (low). Matches the SNMP upsBatteryVoltage plane.
        ups_battery_voltage=(round(416.0 + 128.0 * (100 if ext.get("ups_status") == "normal" else 50 if ext.get("ups_status") == "on_battery" else 15) / 100.0, 1) if dt == "ups" else None),
        ups_output_voltage=(400.0 if dt == "ups" else None),
        # Output/input power + currents derive from the REAL watts through this UPS
        # (ups_output_kw, from the live power graph) — NOT a fixed 3 kW frame, so a
        # 1.2 MW UPS reads MW-scale power that tracks the IT load and fleet growth.
        # 3-phase 400 V line-to-line (matches the rest of the plant): I = P/(√3·V·PF),
        # PF 0.9; double-conversion input = output ÷ 0.92 efficiency. Constants match
        # the SNMP UPS plane so both report the same telemetry.
        ups_output_power=(round(float(ext.get("ups_output_kw", 0.0)) * 1000.0, 1) if dt == "ups" else None),
        ups_output_current=(round(float(ext.get("ups_output_kw", 0.0)) * 1000.0 / (1.7320508 * 400.0 * 0.9), 1) if dt == "ups" else None),
        ups_input_power=(round(float(ext.get("ups_output_kw", 0.0)) * 1000.0 / 0.92, 1) if dt == "ups" else None),
        ups_input_current=(round(float(ext.get("ups_output_kw", 0.0)) * 1000.0 / 0.92 / (1.7320508 * max(float(ext.get("ups_input_voltage", 400.0)), 1.0) * 0.9), 1) if dt == "ups" else None),
        pdu_load=ext.get("pdu_load")                     if dt in ("pdu", "floor_pdu") else None,
        pdu_voltage=ext.get("pdu_voltage")               if dt in ("pdu", "floor_pdu") else None,
        pdu_power_factor=ext.get("pdu_power_factor")     if dt in ("pdu", "floor_pdu") else None,
        pdu_phase_imbalance=ext.get("pdu_phase_imbalance") if dt in ("pdu", "floor_pdu") else None,
        pdu_outlet_status=ext.get("pdu_outlet_status")   if dt in ("pdu", "floor_pdu") else None,
        pdu_breaker_status=ext.get("pdu_breaker_status") if dt in ("pdu", "floor_pdu") else None,
        pdu_outlet_failure=ext.get("pdu_outlet_failure") if dt in ("pdu", "floor_pdu") else None,
        pdu_smoke=ext.get("pdu_smoke")                   if dt in ("pdu", "floor_pdu") else None,
        pdu_outlet_current=ext.get("pdu_outlet_current") if dt in ("pdu", "floor_pdu") else None,
        pdu_ground_fault=ext.get("pdu_ground_fault")     if dt in ("pdu", "floor_pdu") else None,
        pdu_real_power=(
            ext["pdu_voltage"] * ext["pdu_outlet_current"] * ext["pdu_power_factor"]
            if all(k in ext for k in ("pdu_voltage", "pdu_outlet_current", "pdu_power_factor")) else None
        ) if dt in ("pdu", "floor_pdu") else None,
        pdu_apparent_power=(
            ext["pdu_voltage"] * ext["pdu_outlet_current"]
            if all(k in ext for k in ("pdu_voltage", "pdu_outlet_current")) else None
        ) if dt in ("pdu", "floor_pdu") else None,
        pdu_energy_kwh=ext.get("pdu_energy_kwh")         if dt in ("pdu", "floor_pdu") else None,
        pdu_frequency=ext.get("pdu_frequency")           if dt in ("pdu", "floor_pdu") else None,
        pdu_temperature=ext.get("pdu_temperature")       if dt in ("pdu", "floor_pdu") else None,
        pdu_humidity=ext.get("pdu_humidity")             if dt in ("pdu", "floor_pdu") else None,
        pdu_outlet_power=(
            ext["pdu_voltage"] * ext["pdu_outlet_current"] * ext["pdu_power_factor"]
            if all(k in ext for k in ("pdu_voltage", "pdu_outlet_current", "pdu_power_factor")) else None
        ) if dt in ("pdu", "floor_pdu") else None,
        bgp_sessions_up=sum(1 for s in sessions if s.get("state") == "established") if dt in ("router", "firewall") else None,
        bgp_sessions_total=len(sessions) if dt in ("router", "firewall") else None,
        **_iface_aggregates(device, dt),
    )


@router.get("", response_model=DevicesResponse)
async def get_all_devices(device_type: str = None, layer: str = None):
    """Get simulated devices.

    Optional filters (combinable):
    - ?device_type=switch,router   — comma-separated DeviceType values
      Valid values: router, switch, server, firewall, load_balancer,
                    ups, pdu, floor_pdu, oob_switch, sensor
    - ?layer=production            — network devices (router, switch, server, firewall, load_balancer)
    - ?layer=management            — OOB devices (oob_switch)
    - ?layer=power                 — power devices (ups, pdu, floor_pdu)
    - ?layer=environmental         — sensors only (sensor)
    """
    s = _state()
    if s.device_manager is None:
        return DevicesResponse(total=0, devices=[])
    from core.device_manager import DeviceType
    _PRODUCTION_TYPES   = {DeviceType.ROUTER, DeviceType.SWITCH, DeviceType.SERVER,
                           DeviceType.FIREWALL, DeviceType.LOAD_BALANCER}
    _MANAGEMENT_TYPES   = {DeviceType.OOB_SWITCH}
    _POWER_TYPES        = {DeviceType.UPS, DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.RPP,
                           DeviceType.GENERATOR, DeviceType.UTILITY_FEED,
                           DeviceType.SWITCHGEAR, DeviceType.ATS, DeviceType.MCC,
                           DeviceType.MPP}
    _ENVIRONMENTAL_TYPES = {DeviceType.SENSOR}
    devices = s.device_manager.get_all_devices()
    if layer == "production":
        devices = [d for d in devices if d.device_type in _PRODUCTION_TYPES]
    elif layer == "management":
        devices = [d for d in devices if d.device_type in _MANAGEMENT_TYPES]
    elif layer == "power":
        devices = [d for d in devices if d.device_type in _POWER_TYPES]
    elif layer == "environmental":
        devices = [d for d in devices if d.device_type in _ENVIRONMENTAL_TYPES]
    if device_type:
        types = {t.strip().lower() for t in device_type.split(",")}
        devices = [d for d in devices if d.device_type.value in types]
    return DevicesResponse(
        total=len(devices),
        devices=[_device_to_info(d) for d in devices],
    )


def _u_height(device_type, model_name: str = "") -> int:
    """How many rack units a device physically occupies — the shared per-SKU rule from
    core.rack_capacity, which fleet's _next_free_unit uses too, so hand-placed and
    fleet-placed servers agree on what is free."""
    from core.rack_capacity import device_u_height
    return device_u_height(device_type, model_name)


@router.get("/rack-occupancy")
def rack_occupancy(datacenter: str, room: str = "", device_type: str = "",
                   model_name: str = ""):
    """Per-rack server-U occupancy across a datacenter (optionally one room), for
    the Add-Device cascading location picker.

    Lists every rack that has any device (every real rack has 0U PDUs, so empty
    server racks still show) with its room/floor/row/num, how many of the U1–U40
    server slots are used, which U's are free, the next free U, and a full flag.
    `all_full` flags no free server U anywhere in the returned scope. U41/U42 (ToR
    pair) and 0U PDUs are not server slots. The client filters rooms/rows/racks/
    units down to those with space.

    `units` is the whole U1–U40 face of the rack, each slot flagged used/free with its
    occupant — the elevation an operator reads before racking something. The picker
    shows every U (a rack face has no gaps: U19 sits between U18 and U20) and only
    lets a free one be picked.

    Occupancy is a SPAN, not a point: a 2U server at U1 fills U1 and U2. Marking only
    its own rack_unit left every even U reading free, so the picker offered — and
    next_free defaulted to — a slot INSIDE the chassis above it.

    `free_units` is what a device of *device_type* / *model_name* can actually be racked
    at: its whole height must fit in free U's without running off U40. Height comes from
    the SKU, so the answer differs per model — a 1U DL360 fits a lone free U that a 2U
    R750 cannot. Omit both for 1U semantics (any physically free U)."""
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Device manager not initialized")
    from core.rack_capacity import FIRST_SERVER_UNIT, LAST_SERVER_UNIT, TOR_A_UNIT
    from core.device_manager import DeviceType
    total = LAST_SERVER_UNIT - FIRST_SERVER_UNIT + 1
    try:
        want_h = _u_height(DeviceType(device_type.lower()), model_name) if device_type else 1
    except ValueError:
        want_h = 1
    racks: dict = {}
    # Occupancy across the WHOLE rack face (U1-U42), as distinct from `racks` above,
    # which counts only the server area U1-U40. The two differ exactly where it
    # matters for a network rack: a spine pair sits at U41/U42, the reserved ToR
    # positions, so a cabinet holding two spines reported 0/40 and read as empty.
    # The server window is right for PLACEMENT (a server may not use U41/42) and
    # wrong for DISPLAY, so both are carried.
    faces: dict = {}
    roles: dict = {}          # rack key -> what the cabinet is FOR
    # Devices per rack, counting EVERYTHING in the cabinet — 0U side-rail PDUs and
    # gear at U41/42 included. Distinct from `used` below, which is server-U span
    # occupancy: a network rack holding two spines at U41/42 and two 0U PDUs uses
    # zero server U, so reporting only `used` made a 4-device rack read "0/40" and
    # look empty.
    counts: dict = {}
    for d in s.device_manager.get_all_devices():
        if (getattr(d, "datacenter", "") or "") != datacenter:
            continue
        if room and (getattr(d, "room", "") or "") != room:
            continue
        rr = getattr(d, "rack_row", 0) or 0
        rn = getattr(d, "rack_num", 0) or 0
        if rr <= 0 or rn <= 0:
            continue
        key = ((getattr(d, "room", "") or ""), str(getattr(d, "floor", "") or ""), rr, rn)
        occ = racks.setdefault(key, {})
        face = faces.setdefault(key, {})
        counts[key] = counts.get(key, 0) + 1
        # A rack's ROLE comes from what is in it, and it decides what may be added.
        # A power-panel position (RPP + EV2, both 0U) reports 40 free U and would
        # otherwise look like the emptiest rack in the hall — you cannot rack a
        # server on a panel. Compute wins over network wins over facility: a cabinet
        # holding servers is a compute rack even though its PDUs are facility gear.
        _dt = d.device_type.value
        _cur = roles.get(key)
        if _dt in _COMPUTE_RACK_TYPES:
            roles[key] = "compute"
        elif _dt == "switch" and _name_role(d).startswith("SP"):
            if _cur != "compute":
                roles[key] = "spine"
        elif _dt == "oob_switch":
            if _cur not in ("compute", "spine"):
                roles[key] = "oob"
        elif _dt in _NETWORK_RACK_TYPES:
            if _cur is None:
                roles[key] = "compute"      # a lone ToR marks a compute cabinet
        elif _cur is None:
            roles[key] = "facility"
        u = getattr(d, "rack_unit", 0) or 0
        if u <= 0:
            continue                        # 0U side-rail PDUs occupy no U
        # Every U this device's body covers, each naming it as the occupant.
        for cu in range(u, u + _u_height(d.device_type, getattr(d, "model_name", "") or "")):
            if FIRST_SERVER_UNIT <= cu <= LAST_SERVER_UNIT:
                occ[cu] = d.name
            if FIRST_SERVER_UNIT <= cu <= TOR_A_UNIT:
                face[cu] = d.name
    # A direct-to-chip server needs more than free U: the rack must have a manifold
    # with a free UQD pair, which means an eligible CDU. Judged by _rack_cdus, the
    # same function the LINKS section uses, so a rack offered here always has a loop
    # to join. Air-cooled SKUs skip all of this and see every rack as before.
    from core.device_models import is_liquid_cooled
    from core.device_manager import nameplate_power_w
    from core.rack_capacity import (RACK_AIR_BUDGET_W_DEFAULT, device_air_load_w,
                                    rack_has_air_headroom)
    liquid = bool(model_name) and is_liquid_cooled(model_name)
    all_devs = s.device_manager.get_all_devices()
    # Heat the CANDIDATE would add to the room air. A liquid server adds only its
    # residual fraction, which is why a DLC box fits in a rack that an air box of the
    # same draw would not — the reason to plumb it in the first place.
    try:
        _add_dt = DeviceType(device_type.lower()) if device_type else None
    except ValueError:
        _add_dt = None
    add_air_w = (device_air_load_w(_add_dt, nameplate_power_w(_add_dt, model_name), liquid)
                 if _add_dt else 0.0)

    out = []
    for (rm, fl, rr, rn), occ in sorted(racks.items()):
        # The elevation is the WHOLE cabinet, U1-U42 — a rack is 42U, and drawing it
        # 40U tall hid the ToR at U42 and the reserved MLAG position at U41, the two
        # slots an operator most needs to see are spoken for. Occupancy comes from the
        # full-face map; `free_units` below still limits PLACEMENT to the server area,
        # so U41/42 render as present-but-unavailable rather than vanishing.
        _face = faces.get((rm, fl, rr, rn), {})
        units = [{"unit": u, "used": u in _face, "occupant": _face.get(u),
                  "reserved": u > LAST_SERVER_UNIT}
                 for u in range(FIRST_SERVER_UNIT, TOR_A_UNIT + 1)]
        # Pickable = the new device's full height fits here, entirely inside U1–U40.
        free = [u for u in range(FIRST_SERVER_UNIT, LAST_SERVER_UNIT - want_h + 2)
                if all(cu not in occ for cu in range(u, u + want_h))]
        # Air-side co-limit: what this cabinet already puts in the room, and whether
        # the candidate still fits. Independent of free U and of the power budget —
        # a rack can have space and amps and still be out of cooling.
        air_used = _rack_air_load_w(s, all_devs, (datacenter, str(fl), rm), (rr, rn))
        air_ok = rack_has_air_headroom(air_used, add_air_w)

        cdu_name, cdu_used, cdu_ports, liquid_ready = None, None, None, True
        if liquid:
            cdus = _rack_cdus(s, all_devs, (datacenter, str(fl), rm), (rr, rn))
            liquid_ready = bool(cdus)
            if cdus:
                best = cdus[0]
                cdu_name = best["dev"].name
                cdu_used, cdu_ports = best["used"], best["ports"]
        out.append({"room": rm, "floor": fl, "rack_row": rr, "rack_num": rn,
                    "used": len(occ), "total": total, "free_units": free,
                    "device_count": counts.get((rm, fl, rr, rn), 0),
                    "role": roles.get((rm, fl, rr, rn), "facility"),
                    "face_used": len(faces.get((rm, fl, rr, rn), {})),
                    "face_total": TOR_A_UNIT,
                    "units": units,
                    # liquid_ready is True for an air-cooled SKU: every rack takes it.
                    # For a DLC SKU it means "has a CDU with a free manifold pair",
                    # and the cdu_* fields name that unit so the picker can show which
                    # cabinet the server is joining.
                    "liquid_ready": liquid_ready, "cdu_name": cdu_name,
                    "cdu_used": cdu_used, "cdu_ports": cdu_ports,
                    "air_used_w": round(air_used), "air_budget_w": RACK_AIR_BUDGET_W_DEFAULT,
                    "air_add_w": round(add_air_w), "air_ok": air_ok,
                    "next_free": (free[0] if free else None), "full": not free})
    # The height the free_units above were computed for, so the picker can name the
    # SPAN a pick takes ("U39–U40") instead of just its anchor U. A rack_unit is the
    # BOTTOM of the device, which is not self-evident from a bare "U39".
    # Physical rack POSITIONS per row, per room — how many cabinets the hall's floor
    # width actually fits. Lets the picker show unoccupied positions greyed out, so a
    # gap in the rack list reads as "floor space, provision a rack" rather than as
    # missing data. Derived the same way _hall_grid does it: the stored racks_per_row
    # can drift below what the width really fits, so the larger wins.
    grid: dict = {}
    fp = getattr(s.topology, "floorplan", None) or {}
    for _key, _ext in ((fp.get("rooms") or {}) if isinstance(fp, dict) else {}).items():
        _dc, _, _rm = str(_key).partition("/")
        if _dc != datacenter or (room and _rm != room):
            continue
        try:
            from core.hall_geometry import racks_for_width
            _phys = racks_for_width(_ext.get("width_m")) if _ext.get("width_m") else 0
        except Exception:
            _phys = 0
        _n = max(int(_ext.get("racks_per_row") or 0), int(_phys or 0))
        if _n:
            grid[_rm] = _n

    return {"datacenter": datacenter, "racks": out, "device_u_height": want_h,
            "row_positions": grid,
            "role_accepts": {k: sorted(v) for k, v in RACK_ROLE_ACCEPTS.items()},
            # Tells the dialog to apply liquid-ready filtering and to explain itself
            # when a hall comes back empty, rather than showing a bare empty list.
            "liquid_only": liquid,
            "no_liquid_racks": liquid and not any(r["liquid_ready"] for r in out),
            "all_full": bool(out) and all(r["full"] for r in out)}


# ── Link candidates for the Add-Device LINKS section ─────────────────────────
#
# A device that is racked but not cabled is a dead node: it answers SNMP, but it
# carries no traffic, draws no metered power and never shows on an upstream feed.
# Fleet churn never creates one (FleetLifecycleEngine._add_server wires the ToR
# uplink, the BMC and both cords in the same breath); the manual Add Device path
# used to. These endpoints let the dialog demand the same cabling, and they answer
# with the FAR ends only — the near end (this device's NIC / BMC port / PSU) is the
# server's to choose, because the device does not exist yet.

# What each device type must be cabled to before it is a real, live device. Only
# `server` is populated for now; a type absent here keeps the old uncabled add.
# Adding a type here is what turns its LINKS section on, so keep the requirement
# list honest to how that type is really cabled — not to what is convenient.
_LINK_REQUIREMENTS = {
    "server": ("data", "mgmt", "power", "cooling"),
}

# What a rack is FOR, inferred from its occupants — and therefore what may be added
# to it. The MDA cabinets are not interchangeable: a spine rack carries the fabric
# and its fibre plant, the OOB rack carries the management plane, and neither takes
# compute. An RPP/CRAH position takes no IT gear at all. PDUs and sensors live in
# every rack and decide nothing.
#
# Precedence when a rack holds several kinds: compute > spine > oob > facility. A
# cabinet with servers in it is a compute rack even though its ToR is a switch.
_COMPUTE_RACK_TYPES = frozenset({"server"})
_NETWORK_RACK_TYPES = frozenset({"switch", "oob_switch", "router", "firewall",
                                 "load_balancer"})

# Which device types each rack role will accept in the Add-Device picker.
RACK_ROLE_ACCEPTS = {
    "compute":  {"server", "switch", "sensor", "cdu", "pdu"},
    "spine":    {"switch"},          # fabric cabinet — spines and their patch plant
    "oob":      {"oob_switch"},      # management plane cabinet
    "facility": set(),               # RPP panel / CRAH position — no IT gear
}

_OOB_ROLE = "OOB"     # IT access OOB. NOT OOBM (BMS plane) and NOT OOBC (cores).

# The named ports a BMC/console edge belongs on — mirrors fleet_lifecycle's
# _MGMT_PORT_NAMES, imported rather than copied so the two cannot drift.
def _mgmt_iface(dev):
    """List-index of *dev*'s dedicated management/BMC port (iDRAC / iLO / mgmt0), or
    None when it has none — then add_link auto-picks the next free port, which is
    what fleet does (_mgmt_port_iface)."""
    from core.fleet_lifecycle import _MGMT_PORT_NAMES
    for i, itf in enumerate(getattr(dev, "interfaces", None) or []):
        if (getattr(itf, "name", "") or "").strip().lower() in _MGMT_PORT_NAMES:
            return i
    return None


def _first_free_iface(dev, role: str, taken: set):
    """First port of *role* on a BRAND-NEW device not already claimed earlier in this
    same batch. *taken* is what previous links in the batch consumed — without it a
    second data link would land on NIC 0 again (every port still reads free: nothing
    is wired yet, so there are no edges to read)."""
    mgmt_i = _mgmt_iface(dev)
    for i, itf in enumerate(getattr(dev, "interfaces", None) or []):
        if i in taken:
            continue
        if (getattr(itf, "role", "data") or "data") != role:
            continue
        # A production link may not land on the dedicated mgmt port: that port hangs
        # off the management CPU, not the NIC/ASIC, and physically cannot carry it.
        if role == "data" and i == mgmt_i:
            continue
        return i
    return None


def _name_role(dev) -> str:
    """The leading name segment's letters — the functional role the runtime already
    parses (SP=spine, LF=leaf, PDUA/PDUB=A/B pair, OOB/OOBM/OOBC). See
    core/fleet_lifecycle.py's _hall_oobs / _is_spine, which read the same signal."""
    return "".join(c for c in (dev.name or "").split("-", 1)[0] if c.isalpha()).upper()


def _room_key(dev) -> tuple:
    return ((getattr(dev, "datacenter", "") or ""), str(getattr(dev, "floor", "") or ""),
            (getattr(dev, "room", "") or ""))


def _rack_of(dev) -> tuple:
    return ((getattr(dev, "rack_row", 0) or 0), (getattr(dev, "rack_num", 0) or 0))


def _probe_device(device_type: str, vendor: str, model_name: str, interface_count: int):
    """An unregistered Device built from the form's type/vendor/model, purely to ask
    it what ports and PSUs it WOULD have.

    Built rather than re-derived: Device.__post_init__ already fills the nameplate,
    the interface layout and the PSU inlet (C14 vs C20 is decided by the draw, in
    _generate_psus). Re-implementing that rule here would be a second source of
    truth that goes stale the first time the real one changes."""
    from core.device_manager import Device, DeviceType, Vendor
    try:
        dt = DeviceType(device_type.lower())
    except ValueError:
        return None
    try:
        vd = Vendor(vendor)
    except ValueError:
        from core.device_manager import Vendor as _V
        vd = next(iter(_V))
    try:
        return Device(name="_probe", device_type=dt, vendor=vd, ip_address="0.0.0.0",
                      model_name=model_name or "", interface_count=interface_count)
    except Exception:
        return None


def _port_options(s, dev, lo: int = 0, hi: int | None = None, role: str = "data") -> list:
    """EVERY port on *dev* in the [lo, hi) index window, each flagged used/free with
    its peer — the same contract as GET /topology/devices/{id}/ports.

    All ports are listed, not just the free ones: an operator patching a rack reads
    the port map to see what the switch is actually carrying, and a dropdown that
    silently omits the taken ports makes port 22 look like port 1's neighbour and
    hides WHY the obvious port is unavailable. The UI shows them disabled with their
    peer; only free ports can be picked, and add_link/_wire_new_device refuse a taken
    one anyway.

    The window and *role* still filter: those ports are not "taken", they are not
    candidates at all (a leaf's spine-facing uplinks, a dedicated mgmt port).

    Used is judged from the EDGES (_port_terminations), never from the interface's
    cached connected_to_device — that field holds only the last write and reads as
    occupied long after a link is gone."""
    from api.routers.topology import _port_terminations
    term = _port_terminations(s, dev.id)
    out = []
    ifaces = getattr(dev, "interfaces", None) or []
    hi = len(ifaces) if hi is None else min(hi, len(ifaces))
    for i in range(lo, hi):
        itf = ifaces[i]
        if role and (getattr(itf, "role", "data") or "data") != role:
            continue
        conns = term.get(i) or []
        peer = None
        if conns:
            # Prefer the production peer so a ToR/uplink is what's named; a port can
            # carry a data link and a console on different layers.
            c = next((c for c in conns if c["layer"] == "production"), conns[0])
            peer = f"{c['peer']} {c['peer_iface']}" if c["peer_iface"] else c["peer"]
        out.append({"value": i, "label": itf.name, "used": bool(conns), "peer": peer})
    return out


def _free_count(ports: list) -> int:
    return sum(1 for p in ports if not p["used"])


def _leaf_slot(s, devs, rk: tuple, rack: tuple, near_label: str) -> dict:
    """The data-uplink slot: leaf switches in this hall with a free SERVER-FACING
    port, this rack's ToR first.

    Only downlink ports are offered. A leaf's high-speed uplink ports face the
    spines (and two are held for the MLAG peer-link) — hanging a server off one is
    not a thing you can do in a real fabric, so they are not in the list. The split
    comes from core.rack_capacity.leaf_port_roles, the same function the fleet's
    per-rack server cap is measured with."""
    from core.device_manager import DeviceType
    from core.rack_capacity import leaf_port_roles
    cands = []
    for d in devs:
        if d.device_type != DeviceType.SWITCH or _room_key(d) != rk:
            continue
        if _name_role(d).startswith("SP"):
            continue                       # spine — leaf-facing, never server-facing
        downlink, _uplink = leaf_port_roles(getattr(d, "model_name", "") or "",
                                            getattr(d, "interface_count", 54) or 54)
        ports = _port_options(s, d, 0, downlink, role="data")
        # Ports inside the downlink range that actually carry a spine uplink or the
        # peer-link are fabric, not server slots — don't count them as capacity. They
        # still SHOW (used, named by their spine): they are real ports an operator
        # reading the map expects to see, they just were never free to take.
        fabric = s.topology.fabric_ifaces(d.id)
        slots = [p for p in ports if p["value"] not in fabric]
        free = _free_count(slots)
        if not free:
            continue                       # leaf downlinks exhausted — nothing to pick
        same_rack = _rack_of(d) == rack
        cands.append({
            "id": d.id, "name": d.name, "same_rack": same_rack,
            # Suffix dropped for the same reason as the CDU slot — the dialog's
            # "This rack" heading says it once, above the entry.
            "detail": f"R{d.rack_row or 0}-{str(d.rack_num or 0).zfill(2)}"
                      f" · {free}/{len(slots)} downlinks free",
            "ports": ports,
        })
    cands.sort(key=lambda c: (not c["same_rack"], c["name"]))
    return {"key": "data0", "label": "Leaf switch", "port_label": "Downlink port",
            "near_end": near_label, "candidates": cands}


def _oob_slot(s, devs, rk: tuple, near_label: str) -> dict:
    """The management slot: this hall's IT access OOB switches with a free port.

    Filtered to name role exactly 'OOB': OOBM is the BMS/facility plane (BACnet
    gear) and OOBC are the OOB cores that the access switches uplink INTO — a
    server BMC belongs on neither. Same rule as _hall_oobs in fleet_lifecycle."""
    from core.device_manager import DeviceType
    cands = []
    for d in devs:
        if d.device_type != DeviceType.OOB_SWITCH or _room_key(d) != rk:
            continue
        if _name_role(d) != _OOB_ROLE:
            continue
        # OOB-plane switches carry the mgmt plane on DATA-role ports by design —
        # their own 'mgmt' port is the switch's console, not an access port.
        ports = _port_options(s, d, role="data")
        free = _free_count(ports)
        if not free:
            continue
        cands.append({"id": d.id, "name": d.name, "same_rack": False,
                      "detail": f"{free}/{len(ports)} ports free",
                      "ports": ports})
    cands.sort(key=lambda c: c["name"])
    return {"key": "mgmt0", "label": "OOB switch", "port_label": "Access port",
            "near_end": near_label, "candidates": cands}


def _cdu_loop_members(s, cdu_id: str) -> set:
    """Server ids already on this CDU's cold-plate loop, read off the cooling edges —
    the same source of truth DeviceStateStore._cdu_loop_servers uses."""
    from core.device_manager import DeviceType
    members = set()
    for u, v, ed in s.topology.get_links():
        if ed.get("layer") != "cooling":
            continue
        other = v if u == cdu_id else (u if v == cdu_id else None)
        if other is None:
            continue
        od = s.device_manager.get_device(other)
        if od is not None and od.device_type == DeviceType.SERVER:
            members.add(od.id)
    return members


def _rack_air_load_w(s, devs, rk: tuple, rack: tuple) -> float:
    """Watts the kit in this rack rejects into the ROOM AIR.

    Not the rack's electrical draw: a liquid server contributes only its residual air
    fraction, and the CDU/PDUs/sensors contribute nothing (see device_air_load_w).
    This is what the hall's air handling has to carry for this cabinet, and it is the
    number the air budget is measured against."""
    from core.rack_capacity import device_air_load_w
    liquid = _liquid_servers()
    total = 0.0
    for d in devs:
        if _room_key(d) != rk or _rack_of(d) != rack:
            continue
        total += device_air_load_w(d.device_type,
                                   getattr(d, "power_draw_w", 0) or 0,
                                   (d.name or "") in liquid)
    return total


def _rack_cdus(s, devs, rk: tuple, rack: tuple) -> list:
    """CDUs a liquid-cooled server in *rack* may join, with their loop occupancy.

    THE one place that decides coolant-loop eligibility. Both the Add-Device LINKS
    picker and the rack-occupancy cascade read it, so a rack the location picker
    offers is guaranteed to have a CDU the LINKS section will then show — if these
    were two implementations they would drift, and the operator would land on a rack
    with no loop to join.

    Eligibility is two rules:
      * an IN-RACK CDU serves only its own cabinet's manifold, so it is a candidate
        for this rack alone; a row/facility skid feeds a header and serves the hall.
      * the manifold must have a free UQD pair. Ports, not kW, are what run out.
    """
    from core.device_manager import (DeviceType, cdu_manifold_ports,
                                     cdu_serves_own_rack_only)
    out = []
    for d in devs:
        if d.device_type != DeviceType.CDU or _room_key(d) != rk:
            continue
        model = getattr(d, "model_name", "") or ""
        same_rack = _rack_of(d) == rack
        if cdu_serves_own_rack_only(model) and not same_rack:
            continue
        used = len(_cdu_loop_members(s, d.id))
        ports = cdu_manifold_ports(model)          # 0 = unknown ⇒ unlimited
        if ports and used >= ports:
            continue                               # manifold full — no pair to land on
        out.append({"dev": d, "same_rack": same_rack, "used": used, "ports": ports})
    out.sort(key=lambda c: (not c["same_rack"], c["dev"].name))
    return out


def _cdu_slot(s, devs, rk: tuple, rack: tuple) -> dict:
    """The coolant-loop slot: CDUs a direct-to-chip server in this rack can join.

    Only offered for a DLC SKU (see is_liquid_cooled) — an air-cooled server has no
    cold plate and no UQD to land on the manifold, so there is nothing to connect.

    Eligibility itself lives in _rack_cdus, shared with the rack-occupancy picker so
    the two cannot disagree about which racks are liquid-ready.

    Real DLC plumbing runs server cold plate → rack manifold → CDU. The manifold is
    not a device here (the seed cables server↔CDU directly), so the loop edge stands
    in for it and the manifold's PORT COUNT is what caps the loop.

    No ports on the slot itself: a cooling link is a PIPE. It has no ifIndex, and
    add_link/validate_link both refuse an iface on a non-Ethernet layer, so the slot
    carries an empty port list and the dialog shows no port picker."""
    from core.device_manager import cooling_capacity_w
    cands = []
    for c in _rack_cdus(s, devs, rk, rack):
        d = c["dev"]
        model = getattr(d, "model_name", "") or ""
        cap_kw = cooling_capacity_w(model) / 1000.0
        # Ports first — that is the number that actually runs out. kW is shown after
        # it as context, not as the limit.
        detail = (f"{c['used']}/{c['ports']} ports" if c["ports"]
                  else f"{c['used']} on loop")
        if cap_kw:
            detail += f" · {cap_kw:.0f} kW"
        # Name the mounting class on anything offered from OUTSIDE this rack, so the
        # operator can see why a CDU in another cabinet is a legal choice here.
        if not c["same_rack"]:
            detail += " · row CDU"
        # No " · this rack" suffix: the dialog groups candidates under a "This rack"
        # heading off same_rack, so repeating it in the detail is noise.
        cands.append({"id": d.id, "name": d.name, "same_rack": c["same_rack"],
                      "detail": detail, "ports": []})
    # NOT optional. A cold-plated CPU has no air heatsink, so a DLC server with no
    # loop has no cooling path — "leave it unset and run on room air" is not a build
    # that exists. Hybrid racks are still supported: that is an AIR SKU sharing the
    # cabinet, not a DLC SKU left unplumbed.
    return {"key": "cool0", "label": "CDU loop", "port_label": "",
            "near_end": "Cold plate (UQD)", "optional": False, "candidates": cands}


def _pdu_slots(s, devs, rk: tuple, rack: tuple, probe) -> list:
    """One slot per PSU — a real server is dual-corded, PSU1 to the A-side rack PDU
    and PSU2 to the B side. That is the whole point of the 2N feed: a single cord
    makes the A/B pair decorative, and the rack's redundancy claim false.

    Only PDUs in the SAME rack are offered (a cord does not leave the cabinet), and
    only outlets matching this load's inlet — a C20 inlet needs a C19 receptacle,
    which is exactly what TopologyEngine.add_link enforces on the way in."""
    from core.device_manager import DeviceType, feed_side
    psus = getattr(probe, "psus", None) or []
    if not psus:
        return []                          # no PSU = no cord (a DPX2 sensor, a PDU)
    want = "C19" if psus[0].inlet == "C20" else "C13"
    by_side: dict = {}
    for d in devs:
        if d.device_type not in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            continue
        if _room_key(d) != rk or _rack_of(d) != rack:
            continue
        # Every outlet of the right type, taken ones included and flagged — same
        # reasoning as _port_options: an operator reads the strip to see what is on
        # it. Outlets of the WRONG type are omitted entirely: a C19 is not a busy
        # C13, it is a receptacle this cord physically cannot enter.
        used, _ = s.topology._used_power_terminations(d.id)
        peer_of = {}
        for _u, _v, ed in s.topology.get_links():
            if (ed.get("layer") == "power" and ed.get("supply_node") == d.id
                    and ed.get("outlet") is not None):
                load = s.device_manager.get_device(ed.get("load_node"))
                peer_of[ed["outlet"]] = (f"{load.name} PSU{ed.get('psu')}"
                                         if load else ed.get("load_node"))
        outs = [{"value": o.index, "label": f"Outlet {o.index} ({o.type})",
                 "used": o.index in used, "peer": peer_of.get(o.index)}
                for o in (getattr(d, "outlets", None) or []) if o.type == want]
        free = _free_count(outs)
        if not free:
            continue                       # no receptacle this cord can go in
        cand = {"id": d.id, "name": d.name, "same_rack": True,
                "detail": f"{free}/{len(outs)} × {want} free", "ports": outs}
        by_side.setdefault(feed_side(d.name) or "?", []).append(cand)
    slots = []
    for i, p in enumerate(psus):
        side = "A" if i == 0 else ("B" if i == 1 else "?")
        # Side comes from the PDUA/PDUB name code. When a rack's PDUs carry no side
        # (a hand-named pair), offer them all rather than silently showing nothing —
        # the operator can still cord correctly; we just cannot label the sides.
        cands = by_side.get(side) or [c for cs in by_side.values() for c in cs]
        slots.append({"key": f"pwr{side}", "label": f"Feed {side} PDU",
                      "port_label": "Outlet", "near_end": p.name,
                      "candidates": sorted(cands, key=lambda c: c["name"])})
    return slots


@router.get("/link-candidates")
def link_candidates(device_type: str, datacenter: str, room: str, floor: str = "",
                    rack_row: int = 0, rack_num: int = 0,
                    vendor: str = "Cisco Systems", model_name: str = "",
                    interface_count: int = 4):
    """The far ends a NEW device of this type, in this rack, must be cabled to.

    Answers the Add-Device LINKS section: one group per layer, each with slots, each
    slot with the devices that can take the cable and the ports/outlets on them that
    are actually free. The dialog only ever offers what is physically pluggable, so
    the operator cannot compose a link that add_link would then refuse.

    A type with no entry in _LINK_REQUIREMENTS answers supported=false and the
    dialog leaves its LINKS section out entirely (old uncabled behaviour)."""
    s = _state()
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    from core.device_models import is_liquid_cooled
    dt = (device_type or "").lower()
    need = _LINK_REQUIREMENTS.get(dt)
    if not need:
        return {"device_type": dt, "supported": False, "groups": []}
    probe = _probe_device(dt, vendor, model_name, interface_count)
    if probe is None:
        raise HTTPException(status_code=400, detail=f"Invalid device_type '{device_type}'")

    rk = (datacenter or "", str(floor or ""), room or "")
    rack = (rack_row or 0, rack_num or 0)
    devs = s.device_manager.get_all_devices()

    # Near-end labels: what the cable lands on at THIS device. Mirrors the picks
    # _wire_new_device makes at create time — including the mgmt fallback to a data
    # port (in-band) when the model carries no BMC port — so the dialog names the
    # port the cable will really land on rather than a plausible-looking guess.
    ifs = getattr(probe, "interfaces", None) or []
    def _label(i, fallback):
        return ifs[i].name if i is not None and i < len(ifs) else fallback
    data_if = _first_free_iface(probe, "data", set())
    mgmt_if = _mgmt_iface(probe)
    if mgmt_if is None:
        mgmt_if = _first_free_iface(probe, "data", {data_if} if data_if is not None else set())
    data_label = _label(data_if, "NIC 0")
    mgmt_label = _label(mgmt_if, "first free port")

    groups = []
    if "data" in need:
        groups.append({"key": "data", "layer": "production", "label": "Data uplink",
                       "help": f"{data_label} → the rack leaf's server-facing downlink",
                       "slots": [_leaf_slot(s, devs, rk, rack, data_label)]})
    if "mgmt" in need:
        groups.append({"key": "mgmt", "layer": "management", "label": "Management (BMC)",
                       "help": f"{mgmt_label} → a hall OOB access port — this is what "
                               f"answers Redfish/IPMI out-of-band",
                       "slots": [_oob_slot(s, devs, rk, mgmt_label)]})
    if "power" in need:
        slots = _pdu_slots(s, devs, rk, rack, probe)
        if slots:
            groups.append({"key": "power", "layer": "power", "label": "Power feeds",
                           "help": "Dual-corded: one cord per PSU, to opposite sides "
                                   "of the rack's A/B pair",
                           "slots": slots})
    # Cooling is offered only when BOTH ends exist: a SKU that ships with cold plates
    # AND a CDU to plumb it into. Either alone is not a loop — a DLC server in an
    # air-cooled hall has nothing to connect to, and an air-cooled SKU has no cold
    # plate to connect with. The slot stays OPTIONAL: hybrid racks (air servers
    # sharing a cabinet with DLC ones) are real, and the server is a valid device
    # either way — it just runs on room air until it is plumbed.
    if "cooling" in need and is_liquid_cooled(model_name):
        slot = _cdu_slot(s, devs, rk, rack)
        if slot["candidates"]:
            groups.append({"key": "cooling", "layer": "cooling",
                           "label": "Coolant loop (DLC)",
                           "help": "Cold plate → rack manifold → CDU. Required: the "
                                   "cold plates replace the heatsinks, so this server "
                                   "cannot run unplumbed",
                           "slots": [slot]})
    return {"device_type": dt, "supported": True, "groups": groups}


@router.get("/faulted")
def get_faulted_devices():
    """Devices whose injected CONDITION is currently IN ALARM, in one call.

    Feeds the topology canvas's faulted-node styling. Covers both condition
    families the Simulate Fault submenu can start:

      * SNMP metric ramps — gated on the rule engine's in_alert, so the node
        lights when the threshold is crossed and the trap fires (not when the
        ramp starts) and goes dark when the recovery trap fires (not when the
        operator clicks Normal). Both edges lag the click by design.
      * Plant BACnet point overrides — forcing a point is instantaneous and
        does not go through the SNMP rule engine, so those signal immediately.

    Declared before /{device_id}/faults so "faulted" is not captured as a
    device_id by the path parameter.
    """
    s = _state()
    st = getattr(s, "state_store", None)
    if st is None:
        return {"faulted": {}}

    # A node signals ALARM state, not injection state. Starting a ramp only
    # begins pushing the metric; the alarm exists once the threshold is actually
    # crossed and the trap fires, and it ends when the recovery trap fires — not
    # when the operator clicks Normal (the ramp back down takes ticks too).
    # So: an injected ramp says "the operator caused this", and the rule engine's
    # in_alert says "the trap has fired and not yet cleared". Require both.
    ramps = st.get_all_faulted()                  # keyed by device.id
    engine = getattr(s, "rule_engine", None)
    alerting = engine.get_alerting() if engine is not None else {}   # keyed by device.name

    faulted: dict[str, list] = {}
    if ramps and alerting and s.device_manager:
        # The two registries key differently: fault ramps by stable device id,
        # rule states by device name (DeviceFact.device_id IS device.name).
        name_by_id = {d.id: d.name for d in s.device_manager.get_all_devices()}
        for dev_id, metrics in ramps.items():
            rules = alerting.get(name_by_id.get(dev_id, ""))
            if rules:
                faulted[dev_id] = sorted(metrics)

    # Plant devices are forced by BACnet point, and that map is keyed by IP.
    plant_ov = getattr(st, "plant_alarm_overrides", {}) or {}
    if plant_ov and s.device_manager:
        by_ip = {}
        for d in s.device_manager.get_all_devices():
            for ip in (getattr(d, "mgmt_ip", None), getattr(d, "ip_address", None)):
                if ip:
                    by_ip.setdefault(ip, d.id)
        for ip, points in plant_ov.items():
            dev_id = by_ip.get(ip)
            if dev_id and points:
                faulted.setdefault(dev_id, []).extend(sorted(points))

    return {"faulted": faulted}


@router.get("/{device_id}", response_model=DeviceInfo)
async def get_device(device_id: str):
    """Get a specific device by ID."""
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Device manager not initialized")
    device = s.device_manager.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return _device_to_info(device)


# ── Per-device live-metric overrides (Metric Tick window) ────────────────────

def _num(key, label, unit, lo, hi):
    return {"key": key, "label": label, "unit": unit, "kind": "num", "min": lo, "max": hi}
def _state_m(key, label, options):
    return {"key": key, "label": label, "unit": "", "kind": "state", "options": options}

_PROD_TYPES = {"server", "router", "switch", "firewall", "load_balancer", "oob_switch"}


def overridable_metrics(device_type: str) -> list[dict]:
    """The metrics the Metric Tick window may force on a device, with how to
    render/validate each (numeric range or state options)."""
    if device_type in _PROD_TYPES:
        out = [
            _num("cpu_usage",  "CPU Usage",  "%",  0, 100),
            _num("memory_pct", "Memory",     "%",  0, 100),
            _num("disk_pct",   "Disk",       "%",  0, 100),
            _num("cpu_temp",   "CPU Temp",   "°C", 0, 120),
            _num("inlet_temp", "Inlet Temp", "°C", 0, 60),
        ]
        # Servers only: fan_rpm is modelled for a server chassis and nothing else.
        # Forcing it low is how a fan-bank fault is staged — the under-speed and
        # failure rules read the resulting speed against the chassis's own floor.
        if device_type == "server":
            out.append(_num("fan_rpm", "Fan Speed", "RPM", 0, 25000))
        return out
    if device_type == "sensor":
        return [
            _num("inlet_temp",  "Ambient Temp",  "°C",   0, 60),
            _num("humidity",    "Humidity",      "%",    0, 100),
            _num("dewpoint",    "Dewpoint",      "°C",  -10, 40),
            _num("airflow",     "Airflow",       "m/s",  0, 5),
            _num("mid_temp",    "Mid-Rack Temp", "°C",   0, 60),
            _num("outlet_temp", "Exhaust Temp",  "°C",   0, 70),
        ]
    if device_type == "ups":
        return [
            _state_m("ups_status",        "UPS Status",     ["normal", "on_battery", "low_battery"]),
            _state_m("ups_bypass_status", "Bypass",         ["off", "on"]),
            # "failure", NOT "fail": the trap rule watches ok->failure, the state
            # store's own option list and the snmprec encoder both compare against
            # "failure". Offering "fail" here set a value nothing recognised, so the
            # injection silently did nothing — no trap, and the SNMP fan OID kept
            # reading OK while the UI showed the fault as applied.
            _state_m("ups_fan_status",    "Fan",            ["ok", "failure"]),
            _num("ups_output_load",    "Output Load",    "%", 0, 120),
            _num("ups_input_voltage",  "Input Voltage",  "V", 180, 260),
            _num("ups_battery_health", "Battery Health", "%", 0, 100),
        ]
    if device_type in ("pdu", "floor_pdu"):
        return [
            _state_m("pdu_outlet_status",  "Outlet",       ["on", "off"]),
            _state_m("pdu_breaker_status", "Breaker",      ["ok", "tripped"]),
            _state_m("pdu_smoke",          "Smoke",        ["no", "yes"]),
            _state_m("pdu_ground_fault",   "Ground Fault", ["no", "yes"]),
            _num("pdu_load",           "Load",           "%", 0, 120),
            _num("pdu_voltage",        "Voltage",        "V", 180, 260),
            _num("pdu_outlet_current", "Outlet Current", "A", 0, 32),
            _num("pdu_temperature",    "Temp",           "°C", 0, 60),
            _num("pdu_humidity",       "Humidity",       "%", 0, 100),
        ]
    return []


class DeviceOverride(BaseModel):
    metric: str
    value:  float | str | None = None   # None = clear; number or state string


@router.get("/{device_id}/overridable")
def get_overridable(device_id: str):
    """Metrics that can be forced on this device (for the Metric Tick window)."""
    s = _state()
    dev = s.device_manager.get_device(device_id) if s.device_manager else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return {"device": device_id, "metrics": overridable_metrics(dev.device_type.value)}


@router.get("/{device_id}/overrides")
def get_device_overrides(device_id: str):
    """Current forced overrides for a device, keyed by metric."""
    st = getattr(_state(), "state_store", None)
    ov = getattr(st, "device_overrides", {}) if st else {}
    return {"device": device_id, "overrides": ov.get(device_id, {})}


@router.post("/{device_id}/override", response_model=OkResponse)
def set_device_override(device_id: str, body: DeviceOverride):
    """Force (or clear) one metric on a device. The store pins it each tick until
    cleared (value=null). Numeric metrics are clamped; state metrics validated
    against their options."""
    s = _state()
    dev = s.device_manager.get_device(device_id) if s.device_manager else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    spec = {m["key"]: m for m in overridable_metrics(dev.device_type.value)}
    m = spec.get(body.metric)
    if m is None:
        raise HTTPException(status_code=400, detail=f"Metric '{body.metric}' not overridable")
    st = getattr(s, "state_store", None)
    if st is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    ov = st.device_overrides
    dov = ov.setdefault(device_id, {})
    if body.value is None:
        dov.pop(body.metric, None)
        if not dov:
            ov.pop(device_id, None)
        return OkResponse(message=f"Cleared {body.metric}")
    if m["kind"] == "state":
        val: float | str = str(body.value)
        if val not in m["options"]:
            raise HTTPException(status_code=400, detail=f"'{val}' not a valid state for {body.metric}")
    else:
        val = max(float(m["min"]), min(float(m["max"]), float(body.value)))
    dov[body.metric] = val
    return OkResponse(message=f"Set {body.metric}={val}")


# ── Inject Fault — ramp a metric across its SNMP threshold ────────────────────
# Unlike /override (instant pin), a fault eases the metric up over several ticks
# so the rule engine fires the trap organically and an SNMP poll agrees. Toggle
# off (action="clear") ramps it back to baseline, firing the recovery trap.
# Each entry: metric (store ramp key), target (just past the rule threshold),
# rate (per-tick step ≈ realistic inertia), device_types it applies to.

FAULT_MAP = {
    "cpu_high":      {"metric": "cpu_usage",           "target": 93.0, "rate": 12.0,
                      "label": "CPU High",
                      "types": ["server", "router", "switch", "firewall", "load_balancer", "oob_switch"]},
    "memory_high":   {"metric": "memory_pct",          "target": 88.0, "rate": 8.0,
                      "label": "Memory High",
                      "types": ["server", "router", "switch", "firewall", "load_balancer"]},
    "temp_high":     {"metric": "cpu_temp",            "target": 93.0, "rate": 3.0,
                      "label": "Temperature High",
                      "types": ["server", "router", "switch", "firewall", "load_balancer", "oob_switch"]},
    "ambient_high":  {"metric": "sensor_ambient_temp", "target": 34.0, "rate": 1.5,
                      "label": "Ambient Temp High", "types": ["sensor"]},
    "humidity_high": {"metric": "humidity",            "target": 82.0, "rate": 4.0,
                      "label": "High Humidity",      "types": ["sensor"]},
    "ups_overload":  {"metric": "ups_output_load",     "target": 93.0, "rate": 10.0,
                      "label": "Output Overload",    "types": ["ups"]},
    "pdu_load_high": {"metric": "pdu_load",            "target": 85.0, "rate": 8.0,
                      "label": "Load High",          "types": ["pdu", "floor_pdu"]},
    # ATS latching conditions — stateful toggles, not metric ramps (marked by
    # "state"). Handled through the store's set_ats_condition path, not set_fault.
    "ats_not_in_auto":  {"state": "not_in_auto",     "label": "Not in Automatic",
                         "types": ["ats"]},
    "ats_fail_transfer":{"state": "fail_to_transfer","label": "Fail to Transfer",
                         "types": ["ats"]},
    "ats_source_lost":  {"state": "source_lost",     "label": "Normal Source Lost",
                         "types": ["ats"]},
    # UPS latching conditions — reuse the existing ext-state override path + the
    # rule engine's raise/clear pairs (marked by "override"). Toggling one pins the
    # backing metric so its rule fires the alarm; clearing releases it so the metric
    # returns to nominal and the recovery rule fires. No new traps, no new state.
    # On-battery is PHYSICAL, not a status pin: it forces the UPS's source to lost so
    # the real autonomy countdown runs — the string drains, escalates to low-battery,
    # and finally exhausts, dropping the load (a dual-corded 2N feed then carries it).
    "ups_on_battery":   {"battery": "on",  "label": "On Battery",   "types": ["ups"]},
    "ups_low_battery":  {"battery": "low", "label": "Low Battery",  "types": ["ups"]},
    "ups_bypass":       {"override": "ups_bypass_status",  "value": "on",
                         "label": "Bypass Active",        "types": ["ups"]},
    "ups_fan_fail":     {"override": "ups_fan_status",     "value": "failure",
                         "label": "Fan Failure",          "types": ["ups"]},
    "ups_batt_fail":    {"override": "ups_battery_status", "value": "failure",
                         "label": "Battery Failure",      "types": ["ups"]},
    "ups_batt_disc":    {"override": "ups_battery_status", "value": "disconnected",
                         "label": "Battery Disconnected", "types": ["ups"]},
    "ups_charger_fail": {"override": "ups_charger_status", "value": "failure",
                         "label": "Charger Failure",      "types": ["ups"]},
    "ups_rect_fail":    {"override": "ups_rectifier_status","value": "failure",
                         "label": "Rectifier Failure",    "types": ["ups"]},
    "ups_phase_fail":   {"override": "ups_phase_status",   "value": "failure",
                         "label": "Phase Failure",        "types": ["ups"]},
    "ups_vin_high":     {"override": "ups_input_voltage",  "value": 450.0,
                         "label": "Input Voltage High",   "types": ["ups"]},
    "ups_vin_low":      {"override": "ups_input_voltage",  "value": 350.0,
                         "label": "Input Voltage Low",    "types": ["ups"]},
    "ups_freq_out":     {"override": "ups_input_frequency","value": 48.5,
                         "label": "Frequency Out of Range","types": ["ups"]},
    "ups_batt_health":  {"override": "ups_battery_health", "value": 30.0,
                         "label": "Battery Low Health",   "types": ["ups"]},
    # Generator fail-to-start — the genset will not qualify the emergency source.
    # With the utility also down and the UPS drained, this is the double failure
    # that leads to a total blackout. Handled via the store's set_gen_failed path.
    "gen_fail_start":   {"gen": "fail_start", "label": "Fail to Start",
                         "types": ["generator"]},
    # Genset alarm conditions — annunciation (raise/clear trap + controller alarm
    # point), handled through the store's set_gen_condition path.
    "gen_low_fuel":     {"gencond": "low_fuel",       "label": "Low Fuel",
                         "types": ["generator"]},
    "gen_low_coolant":  {"gencond": "low_coolant",    "label": "Low Coolant",
                         "types": ["generator"]},
    "gen_battery_fail": {"gencond": "battery_failure","label": "Battery Failure",
                         "types": ["generator"]},
    "gen_xfer_fault":   {"gencond": "transfer_switch","label": "Transfer Switch Fault",
                         "types": ["generator"]},
    "gen_over_temp":    {"gencond": "over_temp",      "label": "Temperature Alert",
                         "types": ["generator"]},
    # Switchgear main-breaker trip / bus fault — takes the board dead (ATS loses that
    # source, UPS drops to battery) + fires a protective-relay raise/clear trap.
    "swgr_breaker_trip": {"swgrcond": "breaker_trip", "label": "Main Breaker Trip",
                          "types": ["switchgear"]},
    "swgr_bus_fault":    {"swgrcond": "bus_fault",    "label": "Bus Fault",
                          "types": ["switchgear"]},
    # NOTE: a power-feeder cable break is NOT a device fault — a cable is a link, so it
    # is broken by double-clicking the power edge on the canvas (api.breakLink on the
    # 'power' layer). Energization follows intact feeders, so opening one de-energizes
    # everything downstream and drops any UPS below it to battery.
}

# metric → fault id, to report active ramps back to the UI by fault id (metric-
# backed faults only; state conditions carry no metric and are reported separately).
_METRIC_TO_FAULT = {v["metric"]: k for k, v in FAULT_MAP.items() if "metric" in v}
# state-condition kind → fault id, for reporting active ATS conditions to the UI.
_STATE_TO_FAULT = {v["state"]: k for k, v in FAULT_MAP.items() if "state" in v}


class FaultRequest(BaseModel):
    fault: str
    action: str = "start"   # "start" | "clear"


@router.get("/{device_id}/faults")
def get_device_faults(device_id: str):
    """Available faults for this device type + which are currently active."""
    s = _state()
    dev = s.device_manager.get_device(device_id) if s.device_manager else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    dtype = dev.device_type.value
    available = [{"fault": k, "label": v["label"]}
                 for k, v in FAULT_MAP.items() if dtype in v["types"]]
    st = getattr(s, "state_store", None)
    active_metrics = st.get_faults(device_id) if st else {}
    active = [_METRIC_TO_FAULT[m] for m in active_metrics if m in _METRIC_TO_FAULT]
    # Latching state conditions (ATS) are tracked separately from metric ramps.
    if st and dtype == "ats" and hasattr(st, "get_ats_conditions"):
        active += [_STATE_TO_FAULT[k] for k in st.get_ats_conditions(device_id)
                   if k in _STATE_TO_FAULT]
    # Override conditions (UPS) are active when their backing metric is pinned to
    # this fault's alarm value in device_overrides.
    ov_dev = getattr(st, "device_overrides", {}).get(device_id, {}) if st else {}
    if ov_dev:
        active += [k for k, v in FAULT_MAP.items()
                   if "override" in v and dtype in v["types"]
                   and ov_dev.get(v["override"]) == v["value"]]
    # Physical on-battery injection (UPS) — active for the kind that was injected.
    if st and dtype == "ups" and hasattr(st, "get_ups_forced_battery"):
        _bk = st.get_ups_forced_battery(device_id)
        if _bk:
            active += [k for k, v in FAULT_MAP.items()
                       if v.get("battery") == _bk and dtype in v["types"]]
    # Generator fail-to-start injection.
    if st and dtype == "generator" and hasattr(st, "is_gen_failed") \
            and st.is_gen_failed(device_id):
        active += [k for k, v in FAULT_MAP.items()
                   if "gen" in v and dtype in v["types"]]
    # Genset alarm conditions.
    if st and dtype == "generator" and hasattr(st, "get_gen_conditions"):
        _gc = set(st.get_gen_conditions(device_id))
        active += [k for k, v in FAULT_MAP.items()
                   if v.get("gencond") in _gc and dtype in v["types"]]
    # Switchgear faults.
    if st and dtype == "switchgear" and hasattr(st, "get_swgr_conditions"):
        _sc = set(st.get_swgr_conditions(device_id))
        active += [k for k, v in FAULT_MAP.items()
                   if v.get("swgrcond") in _sc and dtype in v["types"]]
    return {"device": device_id, "available": available, "active": active}


@router.post("/{device_id}/fault", response_model=OkResponse)
def set_device_fault(device_id: str, body: FaultRequest):
    """Start or clear an Inject Fault ramp on a device."""
    s = _state()
    dev = s.device_manager.get_device(device_id) if s.device_manager else None
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    spec = FAULT_MAP.get(body.fault)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown fault '{body.fault}'")
    if dev.device_type.value not in spec["types"]:
        raise HTTPException(status_code=400,
                            detail=f"Fault '{body.fault}' not applicable to {dev.device_type.value}")
    st = getattr(s, "state_store", None)
    if st is None:
        raise HTTPException(status_code=503, detail="State store not initialized")
    on = body.action != "clear"
    # State conditions (latching ATS faults) toggle a modeled state rather than
    # ramping a metric; the store fires the raise/clear trap and runs any cascade.
    if "state" in spec:
        st.set_ats_condition(device_id, spec["state"], on)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    # On-battery is a physical drain, not a status pin: force the UPS source to lost
    # so its real autonomy countdown runs (drain → low-battery → exhaustion → load drop).
    if "battery" in spec:
        st.set_ups_forced_battery(device_id, spec["battery"] if on else None)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    # Generator fail-to-start toggles the un-startable set (feeds the double-failure).
    if "gen" in spec:
        st.set_gen_failed(device_id, on)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    # Genset alarm conditions (low fuel / coolant / battery / transfer / temp).
    if "gencond" in spec:
        st.set_gen_condition(device_id, spec["gencond"], on)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    # Switchgear breaker-trip / bus-fault.
    if "swgrcond" in spec:
        st.set_swgr_condition(device_id, spec["swgrcond"], on)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    # Override conditions (UPS state alarms) pin the backing ext-state metric via the
    # same device_overrides path the Metric-Tick panel uses; the rule engine then
    # fires the alarm's own raise trap on set and its recovery trap on clear.
    if "override" in spec:
        ov = st.device_overrides
        if on:
            ov.setdefault(device_id, {})[spec["override"]] = spec["value"]
        else:
            dov = ov.get(device_id)
            if dov:
                dov.pop(spec["override"], None)
                if not dov:
                    ov.pop(device_id, None)
        verb = "Injecting" if on else "Clearing"
        return OkResponse(message=f"{verb} {spec['label']} on {dev.name}")
    if not on:
        st.clear_fault(device_id, spec["metric"])
        return OkResponse(message=f"Clearing {spec['label']} on {dev.name}")
    st.set_fault(device_id, spec["metric"], spec["target"], spec["rate"])
    return OkResponse(message=f"Injecting {spec['label']} on {dev.name}")


def _wire_new_device(s, device, links: list, made: list) -> list:
    """Cable a just-created device, appending each (dst_id, layer) to *made*.

    *made* is the CALLER's list, not a return value: it has to stay readable when
    this raises half-way through, or the rollback would not know which cords are
    already in and would leave them dangling on the far end.

    Near ends are chosen here, not by the caller: while the operator was filling the
    form the device did not exist, so the dialog had no port list to point at. The
    picks mirror FleetLifecycleEngine._add_server exactly — data on the first free
    NIC, management on the dedicated BMC port, power left to add_link so cords land
    on PSU1 then PSU2 in the order the links arrive (hence A before B).

    Raises HTTPException on the first cable that will not go in. add_link returns a
    bare False for every refusal, so the reason is reconstructed here — "PDU has no
    free C13" is actionable; "link failed" is not.
    """
    taken: set = set()
    for ln in links:
        layer = (ln.layer or "production").lower()
        dst = s.device_manager.get_device(ln.dst_id)
        if dst is None:
            raise HTTPException(status_code=404,
                                detail=f"Link target '{ln.dst_id}' not found")
        src_iface = None
        if layer in ("production", "management"):
            if layer == "management":
                # The BMC/iDRAC port when the model has one; otherwise fall back to a
                # free data port, which is in-band management — real, and how the OOB
                # switches themselves carry the plane.
                mgmt_i = _mgmt_iface(device)
                src_iface = (mgmt_i if mgmt_i is not None and mgmt_i not in taken
                             else _first_free_iface(device, "data", taken))
            else:
                src_iface = _first_free_iface(device, "data", taken)
            if src_iface is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"{device.name} has no free port left for the {layer} link "
                           f"to {dst.name}")
            taken.add(src_iface)
        # Same gate POST /topology/links/create uses — it must run here too. add_link
        # takes an explicit dst_iface ON TRUST and overwrites whatever termination is
        # already on it, so without this a stale picker (or a direct API call) could
        # silently steal a leaf port from a live server. It also names the reason,
        # which add_link's bare False cannot.
        from api.routers.topology import validate_link, CreateLinkRequest
        validate_link(s, CreateLinkRequest(
            src_id=device.id, dst_id=dst.id, layer=layer,
            src_iface=src_iface, dst_iface=ln.dst_iface,
            outlet=ln.outlet, psu=None))
        ok = s.topology.add_link(device.id, dst.id, src_iface=src_iface,
                                 dst_iface=ln.dst_iface, layer=layer,
                                 outlet=ln.outlet)
        # A coolant loop is TWO pipes, not one: chilled supply from the CDU to the
        # cold plate and warm return back to it. add_link keys cooling edges by
        # direction precisely so both survive, and the curated topology carries the
        # pair for every one of its loop servers. Making only the return here would
        # leave a server that is plumbed but never fed.
        # One rollback entry covers both pipes: the graph is an undirected
        # MultiGraph, so remove_link(device, dst, "cooling") selects BY LAYER and
        # takes both directional keys with it. Recorded BEFORE the supply half goes
        # in, so a failure on the second pipe still rolls the first one back.
        if ok and layer == "cooling":
            made.append((dst.id, layer))
            ok = s.topology.add_link(dst.id, device.id, layer="cooling")
            if ok:
                continue                # already recorded — don't append it twice
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(f"Could not cable {device.name} → {dst.name} on the {layer} "
                        f"layer — the port/outlet was taken, or {dst.name} has no free "
                        f"receptacle for this PSU's inlet. Reopen Add Device to "
                        f"re-read what is free."))
        made.append((dst.id, layer))
    # No feed to record — the cords ARE the record. add_link stamped supply_node/psu/
    # outlet on each power edge, and Redfish, /power-terminations, the floor-plan export
    # and the cascade all read the A/B split back with TopologyEngine.power_feeds. See
    # Device for why the mirrored power_source_a/b field no longer exists.
    return made


def _rollback_new_device(s, device, made: list) -> None:
    """Undo a half-cabled add so a failed link never leaves an orphan behind.

    Links are removed explicitly BEFORE the node: remove_device drops the edges with
    it, but only remove_link clears the far end's cached connected_to_device — left
    behind, a leaf port would read as occupied by a device that no longer exists and
    the port picker would never offer it again."""
    for dst_id, layer in made:
        try:
            s.topology.remove_link(device.id, dst_id, layer=layer)
        except Exception:
            pass
    try:
        s.device_manager.remove_device(device.id)
        s.topology.remove_device(device.id)
    except Exception:
        pass
    if s.ip_manager:
        try:
            s.ip_manager.release(device.ip_address)
        except Exception:
            pass
    _invalidate_power(s)
    _invalidate_cooling(s)


@router.post("", response_model=DeviceInfo)
def add_device(req: AddDeviceRequest):
    """Add a new device to the topology, with its cabling, atomically."""
    s = _state()
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")

    from core.device_manager import Device, DeviceType, Vendor
    try:
        device_type = DeviceType(req.device_type.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid device_type '{req.device_type}'")
    try:
        vendor = Vendor(req.vendor)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid vendor '{req.vendor}'")

    # Check IP conflict
    existing = s.device_manager.get_all_devices()
    if any(d.ip_address == req.ip_address for d in existing):
        raise HTTPException(status_code=409, detail=f"IP address {req.ip_address} already in use")

    # Rack-location validation. A U-slot is owned by exactly one device — reject a
    # placement that would double-book the same U in the same rack, and sanity-check
    # the coordinates. rack_unit == 0 means "unplaced" and skips the U check.
    if req.rack_row < 0 or req.rack_num < 0 or req.rack_unit < 0:
        raise HTTPException(status_code=400, detail="Row / Rack / Unit cannot be negative")
    if req.rack_unit > 52:
        raise HTTPException(status_code=400, detail="Rack Unit must be 1–52")
    if req.rack_unit > 0 and not (req.datacenter and req.room
                                  and req.rack_row > 0 and req.rack_num > 0):
        raise HTTPException(status_code=400,
                            detail="A Rack Unit needs Datacenter, Room, Row and Rack set")
    # A direct-to-chip server MUST be plumbed. Its cold plates sit where an air-cooled
    # machine has heatsinks, so with no coolant loop it has no cooling path at all —
    # an unplumbed DLC box is not an air-cooled build, it is a server that cannot run.
    # Enforced here rather than only in the dialog: this endpoint is callable directly,
    # and the topology already carried four such servers before they were cleaned up.
    from core.device_models import is_liquid_cooled
    if device_type == DeviceType.SERVER and is_liquid_cooled(req.model_name or ""):
        if not any((getattr(ln, "layer", "") or "").lower() == "cooling"
                   for ln in (req.links or [])):
            raise HTTPException(
                status_code=422,
                detail=(f"{req.model_name} is a direct-to-chip SKU and needs a coolant "
                        f"loop — add a cooling link to a CDU in its rack, or choose an "
                        f"air-cooled model."))

    # Air-side thermal budget. The co-limit to free U and the power budget: the room
    # can only carry so much heat away from one cabinet, and a hybrid rack spends part
    # of that allowance on the residual air fraction of its liquid machines. Refusing
    # here is the honest answer — the alternative is a rack that looks fine on paper
    # and cooks.
    if req.rack_row > 0 and req.rack_num > 0:
        from core.device_manager import nameplate_power_w
        from core.rack_capacity import (DTC_AIR_FRACTION, RACK_AIR_BUDGET_W_DEFAULT,
                                        device_air_load_w, rack_has_air_headroom)
        _rk = (req.datacenter or "", str(req.floor or ""), req.room or "")
        _rack = (req.rack_row, req.rack_num)
        _add_air = device_air_load_w(device_type,
                                     nameplate_power_w(device_type, req.model_name),
                                     is_liquid_cooled(req.model_name or ""))
        _cur_air = _rack_air_load_w(s, existing, _rk, _rack)
        if _add_air and not rack_has_air_headroom(_cur_air, _add_air):
            raise HTTPException(
                status_code=409,
                detail=(f"Rack R{req.rack_row}-{req.rack_num:02d} is out of AIR cooling: "
                        f"{_cur_air/1000:.1f} kW of {RACK_AIR_BUDGET_W_DEFAULT/1000:.0f} kW "
                        f"used, this device adds {_add_air/1000:.1f} kW. Pick another "
                        f"rack, or a direct-to-chip SKU — a liquid server puts only "
                        f"~{int(100 * DTC_AIR_FRACTION)}% of its heat in the air."))

    if req.rack_unit > 0:
        # Overlap, not equality: a 2U server occupies U..U+1, so racking one at U2 when
        # U1 holds a 2U box puts it inside that chassis. An equality check let that
        # through — every even U read free, which is also why next_free pointed at one.
        new_h = _u_height(device_type, req.model_name)
        new_span = range(req.rack_unit, req.rack_unit + new_h)
        if req.rack_unit + new_h - 1 > 40:
            raise HTTPException(
                status_code=409,
                detail=(f"A {new_h}U {device_type.value} at U{req.rack_unit} would run "
                        f"past U40 — U41/U42 are reserved for the ToR pair"))
        for d in existing:
            du = getattr(d, "rack_unit", 0) or 0
            if du <= 0:
                continue
            if (getattr(d, "datacenter", "") == req.datacenter
                    and getattr(d, "room", "") == req.room
                    and str(getattr(d, "floor", "") or "") == str(req.floor or "")
                    and (getattr(d, "rack_row", 0) or 0) == req.rack_row
                    and (getattr(d, "rack_num", 0) or 0) == req.rack_num
                    and set(new_span) & set(range(
                        du, du + _u_height(d.device_type,
                                           getattr(d, "model_name", "") or "")))):
                raise HTTPException(
                    status_code=409,
                    detail=(f"Rack unit U{req.rack_unit} in {req.datacenter}/{req.room} "
                            f"row {req.rack_row} rack {req.rack_num} is already occupied "
                            f"by {d.name}"))

    # Build sys_location from physical location fields if not explicitly set
    loc_parts = []
    if req.datacenter:
        if req.country:      loc_parts.append(req.country)
        if req.datacenter_city: loc_parts.append(req.datacenter_city)
        loc_parts.append(req.datacenter)
        if req.floor:        loc_parts.append(f"Floor {req.floor}")
        if req.room:         loc_parts.append(f"Room {req.room}")
        if req.rack_row:     loc_parts.append(f"Row {req.rack_row}")
        if req.rack_num:     loc_parts.append(f"Rack {req.rack_num}")
        if req.rack_unit:    loc_parts.append(f"U{req.rack_unit}")
    sys_location = ", ".join(loc_parts) if loc_parts else ""

    try:
        device = Device(
            name=req.name,
            device_type=device_type,
            vendor=vendor,
            ip_address=req.ip_address,
            model_name=req.model_name,
            mgmt_ip=req.mgmt_ip,
            snmp_port=req.snmp_port,
            gnmi_port=req.gnmi_port,
            interface_count=req.interface_count,
            sys_contact=req.sys_contact,
            sys_location_override=sys_location,
            metrics_enabled=req.metrics_enabled,
            country=req.country,
            datacenter_city=req.datacenter_city,
            datacenter=req.datacenter,
            room=req.room,
            floor=req.floor,
            rack_row=req.rack_row,
            rack_num=req.rack_num,
            rack_unit=req.rack_unit,
        )
        # Resolve (or create) the fleet engine once — used for both physical
        # placement and hot-commission. Log routes to the app logger so any
        # placement/commission failure is visible, not silently swallowed.
        import logging as _logging
        _clog = _logging.getLogger("api.devices")
        eng = getattr(s, "fleet_engine", None)
        if eng is None:
            from core.fleet_lifecycle import FleetLifecycleEngine
            eng = FleetLifecycleEngine(s, log_cb=_clog.warning)
            s.fleet_engine = eng
        # Physical placement: floor_x/floor_y + hot/cold aisle + facing from the
        # rack grid, and the canvas position from the shared layout — so a device
        # given a rack location renders IN that rack, like a fleet device (not at
        # the origin). No rack coords → parked at the origin, nothing placed.
        try:
            px, py = eng.place_device(device)
        except Exception as _e:
            px, py = 0.0, 0.0
            _clog.warning("[add_device] placement %s failed: %s", device.name, _e)
        s.device_manager.add_device(device)
        s.topology.add_device(device, x=px, y=py)
        if s.ip_manager:
            s.ip_manager.reserve(req.ip_address)
        # Cable it before it goes live. Atomic with the add: any cable that will not
        # physically go in rolls the whole device back, so a failed link can never
        # leave a racked-but-dead node behind — the exact state the LINKS section
        # exists to prevent. Rollback runs before commission, so a rolled-back device
        # was never on the protocol sims either.
        made: list = []
        try:
            _wire_new_device(s, device, req.links or [], made)
        except HTTPException:
            _rollback_new_device(s, device, made)
            raise
        except Exception as _e:
            _rollback_new_device(s, device, made)
            raise HTTPException(status_code=500, detail=f"Cabling {device.name}: {_e}")
        _invalidate_power(s)
        _invalidate_cooling(s)
        # Hot-commission onto the running protocol sims (SNMP/gNMI/Redfish/BACnet)
        # via the same path fleet churn uses, so a hand-added device answers
        # immediately — no regenerate + restart. Best-effort: a sim that isn't
        # running is skipped, and any failure never fails the add.
        try:
            eng.commission_device(device)
            _clog.info("[add_device] commissioned %s (%s) onto live sims",
                       device.name, device.device_type.value)
        except Exception as _e:
            _clog.warning("[add_device] commission %s failed: %s", device.name, _e)
        s.notify_ui("sync_devices")
        return _device_to_info(device)
    except HTTPException:
        # A cabling refusal already carries its real status (404/409) and the reason
        # an operator needs. Re-raise it untouched: the blanket handler below would
        # otherwise relabel "PDU has no free C13 outlet" as an opaque 500.
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{device_id}", response_model=DeviceInfo)
def edit_device(device_id: str, req: EditDeviceRequest):
    """Edit an existing device's properties."""
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Device manager not initialized")
    device = s.device_manager.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    update = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update:
        return _device_to_info(device)

    if "vendor" in update:
        from core.device_manager import Vendor
        try:
            update["vendor"] = Vendor(update["vendor"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid vendor '{update['vendor']}'")

    try:
        for k, v in update.items():
            setattr(device, k, v)
        if "power_draw_w" in update:
            _invalidate_power(s)        # draw changed -> rebuild cascade
            # (Feeds are not editable here: a feed is the cord, so it changes by
            # adding/removing a power link — which invalidates the cascade itself.)
        s.notify_ui("sync_devices")
        if s.topology is not None:
            try:
                from core.snmprec_generator import SNMPRecGenerator
                SNMPRecGenerator(s.snmp_datasets_dir).generate_device(device, s.topology)
            except Exception:
                pass
        return _device_to_info(device)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{device_id}", response_model=OkResponse)
def remove_device(device_id: str):
    """Remove a device from the topology."""
    s = _state()
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    device = s.device_manager.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    try:
        ip = device.ip_address
        s.device_manager.remove_device(device_id)
        s.topology.remove_device(device_id)
        if s.ip_manager:
            s.ip_manager.release(ip)
        _invalidate_power(s)
        _invalidate_cooling(s)
        s.notify_ui("sync_devices")
        return OkResponse(message=f"Device '{device.name}' removed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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


@router.get("/rack-occupancy")
def rack_occupancy(datacenter: str, room: str = ""):
    """Per-rack server-U occupancy across a datacenter (optionally one room), for
    the Add-Device cascading location picker.

    Lists every rack that has any device (every real rack has 0U PDUs, so empty
    server racks still show) with its room/floor/row/num, how many of the U1–U40
    server slots are used, which U's are free, the next free U, and a full flag.
    `all_full` flags no free server U anywhere in the returned scope. U41/U42 (ToR
    pair) and 0U PDUs are not server slots. The client filters rooms/rows/racks/
    units down to those with space."""
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Device manager not initialized")
    from core.rack_capacity import FIRST_SERVER_UNIT, LAST_SERVER_UNIT
    total = LAST_SERVER_UNIT - FIRST_SERVER_UNIT + 1
    all_us = set(range(FIRST_SERVER_UNIT, LAST_SERVER_UNIT + 1))
    racks: dict = {}
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
        occ = racks.setdefault(key, set())
        u = getattr(d, "rack_unit", 0) or 0
        if FIRST_SERVER_UNIT <= u <= LAST_SERVER_UNIT:
            occ.add(u)
    out = []
    for (rm, fl, rr, rn), occ in sorted(racks.items()):
        free = sorted(all_us - occ)
        out.append({"room": rm, "floor": fl, "rack_row": rr, "rack_num": rn,
                    "used": len(occ), "total": total, "free_units": free,
                    "next_free": (free[0] if free else None), "full": len(occ) >= total})
    return {"datacenter": datacenter, "racks": out,
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
    "server": ("data", "mgmt", "power"),
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


def _free_ports(s, dev, lo: int = 0, hi: int | None = None, role: str = "data") -> list:
    """Free ports on *dev* within the [lo, hi) index window, as picker options.

    Free is judged from the EDGES (_port_terminations), never from the interface's
    cached connected_to_device — that field holds only the last write and reads as
    occupied long after a link is gone."""
    from api.routers.topology import _port_terminations
    term = _port_terminations(s, dev.id)
    out = []
    ifaces = getattr(dev, "interfaces", None) or []
    hi = len(ifaces) if hi is None else min(hi, len(ifaces))
    for i in range(lo, hi):
        itf = ifaces[i]
        if term.get(i):
            continue
        if role and (getattr(itf, "role", "data") or "data") != role:
            continue
        out.append({"value": i, "label": itf.name})
    return out


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
        ports = _free_ports(s, d, 0, downlink, role="data")
        if not ports:
            continue                       # leaf downlinks exhausted
        same_rack = _rack_of(d) == rack
        cands.append({
            "id": d.id, "name": d.name, "same_rack": same_rack,
            "detail": f"R{d.rack_row or 0}-{str(d.rack_num or 0).zfill(2)}"
                      f" · {len(ports)}/{downlink} downlinks free"
                      + (" · this rack" if same_rack else ""),
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
        ports = _free_ports(s, d, role="data")
        if not ports:
            continue
        cands.append({"id": d.id, "name": d.name, "same_rack": False,
                      "detail": f"{len(ports)} ports free",
                      "ports": ports})
    cands.sort(key=lambda c: c["name"])
    return {"key": "mgmt0", "label": "OOB switch", "port_label": "Access port",
            "near_end": near_label, "candidates": cands}


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
        used, _ = s.topology._used_power_terminations(d.id)
        outs = [{"value": o.index, "label": f"Outlet {o.index} ({o.type})"}
                for o in (getattr(d, "outlets", None) or [])
                if o.type == want and o.index not in used]
        if not outs:
            continue                       # no receptacle this cord can go in
        cand = {"id": d.id, "name": d.name, "same_rack": True,
                "detail": f"{len(outs)} × {want} free", "ports": outs}
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
    return {"device_type": dt, "supported": True, "groups": groups}


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
        return [
            _num("cpu_usage",  "CPU Usage",  "%",  0, 100),
            _num("memory_pct", "Memory",     "%",  0, 100),
            _num("disk_pct",   "Disk",       "%",  0, 100),
            _num("cpu_temp",   "CPU Temp",   "°C", 0, 120),
            _num("inlet_temp", "Inlet Temp", "°C", 0, 60),
        ]
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
            _state_m("ups_fan_status",    "Fan",            ["ok", "fail"]),
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
}

# metric → fault id, to report active ramps back to the UI by fault id
_METRIC_TO_FAULT = {v["metric"]: k for k, v in FAULT_MAP.items()}


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
    if body.action == "clear":
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
        ok = s.topology.add_link(device.id, dst.id, src_iface=src_iface,
                                 dst_iface=ln.dst_iface, layer=layer,
                                 outlet=ln.outlet)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail=(f"Could not cable {device.name} → {dst.name} on the {layer} "
                        f"layer — the port/outlet was taken, or {dst.name} has no free "
                        f"receptacle for this PSU's inlet. Reopen Add Device to "
                        f"re-read what is free."))
        made.append((dst.id, layer))
    # Record the A/B feed ids the same way fleet does: the live cascade runs off the
    # power EDGES, but the DCIM/Redfish power-source view and the redundancy split
    # read these fields.
    pdus = [d for d, layer in made if layer == "power"]
    upd = {}
    if len(pdus) >= 1:
        upd["power_source_a"] = pdus[0]
    if len(pdus) >= 2:
        upd["power_source_b"] = pdus[1]
    if upd:
        s.device_manager.update_device(device.id, **upd)
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
    if req.rack_unit > 0:
        for d in existing:
            if (getattr(d, "datacenter", "") == req.datacenter
                    and getattr(d, "room", "") == req.room
                    and str(getattr(d, "floor", "") or "") == str(req.floor or "")
                    and (getattr(d, "rack_row", 0) or 0) == req.rack_row
                    and (getattr(d, "rack_num", 0) or 0) == req.rack_num
                    and (getattr(d, "rack_unit", 0) or 0) == req.rack_unit):
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
        if any(k in update for k in ("power_draw_w", "power_source_a",
                                     "power_source_b", "power_source")):
            _invalidate_power(s)        # power feeds/draw changed -> rebuild cascade
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
        s.notify_ui("sync_devices")
        return OkResponse(message=f"Device '{device.name}' removed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

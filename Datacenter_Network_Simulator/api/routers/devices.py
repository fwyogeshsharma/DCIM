"""Device CRUD REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import AppState
from core.device_state_store import _get_ext_state
from core.redfish_data_generator import _live_watts
from api.models.schemas import (
    DeviceInfo,
    IfaceStats,
    DevicesResponse,
    AddDeviceRequest,
    EditDeviceRequest,
    OkResponse,
)

router = APIRouter(prefix="/devices", tags=["Devices"])


def _state() -> AppState:
    return AppState.get()


_NO_IFACE_TYPES = {"ups", "pdu", "floor_pdu", "rpp", "sensor", "generator"}


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
    stats = [
        IfaceStats(
            index=i.index,
            name=i.name,
            oper_status=_eff_oper(i),
            speed=i.speed,
            in_octets=i.in_octets,
            out_octets=i.out_octets,
            in_errors=i.in_errors,
            out_errors=i.out_errors,
            in_discards=i.in_discards,
            out_discards=i.out_discards,
            in_unicast_pkts=i.in_octets // 1500,
            out_unicast_pkts=i.out_octets // 1500,
        )
        for i in ifaces
    ]
    return {
        "total_rx_bytes":   sum(i.in_octets  for i in ifaces),
        "total_tx_bytes":   sum(i.out_octets for i in ifaces),
        "total_errors":     sum(i.in_errors  + i.out_errors   for i in ifaces),
        "total_discards":   sum(i.in_discards + i.out_discards for i in ifaces),
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
        power_state=_server_power_state(device) if dt == "server" else None,
        mid_temp=getattr(device, "mid_temp", None)       if dt == "sensor" and getattr(device, "model_name", "") == "Raritan DPX2-T3H1" else None,
        outlet_temp=getattr(device, "outlet_temp", None) if dt == "sensor" and getattr(device, "model_name", "") == "Raritan DPX2-T3H1" else None,
        humidity=getattr(device, "humidity", None) if dt == "sensor" else None,
        dewpoint=getattr(device, "dewpoint", None) if dt == "sensor" else None,
        airflow=getattr(device, "airflow", None)   if dt == "sensor" else None,
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
        ups_battery_voltage=(round(180.0 + 40.0 * (100 if ext.get("ups_status") == "normal" else 50 if ext.get("ups_status") == "on_battery" else 15), 1) if dt == "ups" else None),
        ups_output_voltage=(220.0 if dt == "ups" else None),
        ups_output_current=(round(ext.get("ups_output_load", 40.0) * 3000.0 / 100.0 / 220.0, 2) if dt == "ups" else None),
        ups_output_power=(round(ext.get("ups_output_load", 40.0) * 3000.0 / 100.0 * 0.9, 1) if dt == "ups" else None),
        ups_input_current=(round(ext.get("ups_output_load", 40.0) * 3000.0 / 100.0 * 0.9 / 0.92 / max(float(ext.get("ups_input_voltage", 220.0)), 1.0), 2) if dt == "ups" else None),
        ups_input_power=(round(ext.get("ups_output_load", 40.0) * 3000.0 / 100.0 * 0.9 / 0.92, 1) if dt == "ups" else None),
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
    _POWER_TYPES        = {DeviceType.UPS, DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.RPP, DeviceType.GENERATOR}
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


@router.post("", response_model=DeviceInfo)
def add_device(req: AddDeviceRequest):
    """Add a new device to the topology."""
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
            sys_location=sys_location,
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
        s.device_manager.add_device(device)
        s.topology.add_device(device, x=0.0, y=0.0)
        if s.ip_manager:
            s.ip_manager.reserve(req.ip_address)
        s.notify_ui("sync_devices")
        return _device_to_info(device)
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
        s.notify_ui("sync_devices")
        return OkResponse(message=f"Device '{device.name}' removed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

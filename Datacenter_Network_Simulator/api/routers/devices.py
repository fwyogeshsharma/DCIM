"""Device CRUD REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    DeviceInfo,
    DevicesResponse,
    AddDeviceRequest,
    EditDeviceRequest,
    OkResponse,
)

router = APIRouter(prefix="/devices", tags=["Devices"])


def _state() -> AppState:
    return AppState.get()


def _device_to_info(device) -> DeviceInfo:
    return DeviceInfo(
        id=device.id,
        name=device.name,
        device_type=device.device_type.value,
        vendor=device.vendor.value,
        ip_address=device.ip_address,
        mgmt_ip=getattr(device, "mgmt_ip", None),
        snmp_port=device.snmp_port,
        gnmi_port=device.gnmi_port,
        interface_count=device.interface_count,
        cpu_usage=getattr(device, "cpu_usage", 0.0),
        memory_used=getattr(device, "memory_used", 0.0),
        disk_used=getattr(device, "disk_used", 0.0),
        sys_location=getattr(device, "sys_location", ""),
        sys_contact=getattr(device, "sys_contact", ""),
        uptime=getattr(device, "uptime", 0),
    )


@router.get("", response_model=DevicesResponse)
def get_all_devices(device_type: str = None, layer: str = None):
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
    _POWER_TYPES        = {DeviceType.UPS, DeviceType.PDU, DeviceType.FLOOR_PDU}
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
def get_device(device_id: str):
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
        vendor = Vendor(req.vendor.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid vendor '{req.vendor}'")

    # Check IP conflict
    existing = s.device_manager.get_all_devices()
    if any(d.ip_address == req.ip_address for d in existing):
        raise HTTPException(status_code=409, detail=f"IP address {req.ip_address} already in use")

    try:
        device = Device(
            name=req.name,
            device_type=device_type,
            vendor=vendor,
            ip_address=req.ip_address,
            snmp_port=req.snmp_port,
            gnmi_port=req.gnmi_port,
            interface_count=req.interface_count,
            sys_location=req.sys_location,
            sys_contact=req.sys_contact,
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
            update["vendor"] = Vendor(update["vendor"].lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid vendor '{update['vendor']}'")

    try:
        for k, v in update.items():
            setattr(device, k, v)
        s.notify_ui("sync_devices")
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

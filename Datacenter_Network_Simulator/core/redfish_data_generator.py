"""
Redfish Data Generator — builds DMTF Redfish resource documents for a server.

MVP scope (plain-HTTP, read-only telemetry):
  • ServiceRoot                       /redfish/v1/
  • Systems collection + member       /redfish/v1/Systems[/{id}]
  • Chassis collection + member       /redfish/v1/Chassis[/{id}]
  • Chassis Thermal / Power           /redfish/v1/Chassis/{id}/Thermal|Power
  • Managers collection + member      /redfish/v1/Managers[/{id}]   (the BMC)
  • SessionService + Sessions         /redfish/v1/SessionService[/Sessions]

All documents are built on demand from the *live* Device object, so values
(cpu_usage, cpu_temp, inlet_temp, power_draw_w, memory_used …) reflect whatever
the DeviceStateStore ticker has most recently written.

This module is pure data — no sockets, no auth. simulator/redfish_device.py
handles routing and authentication; simulator/redfish_controller.py handles the
HTTP server lifecycle.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.device_manager import Device

REDFISH_VERSION = "1.6.0"          # ServiceRoot RedfishVersion
_UUID_NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

# Fixed member ids (one server per BMC, so collections have a single member).
MANAGER_ID = "BMC"


# Vendor → (BMC product name, BMC short id, firmware version).
# Mirrors the real out-of-band controller each server vendor ships.
def _bmc_branding(vendor_value: str) -> tuple[str, str, str]:
    table = {
        "Dell Technologies":            ("iDRAC9",                "iDRAC.Embedded.1", "6.10.30.00"),
        "Hewlett Packard Enterprise":   ("iLO 6",                 "iLO.Embedded.1",   "1.55"),
        "Lenovo":                       ("XClarity Controller",   "XCC.Embedded.1",   "22A"),
        "Supermicro":                   ("Supermicro BMC",        "BMC.Embedded.1",   "01.04.16"),
        "IBM":                          ("IMM2",                  "IMM.Embedded.1",   "9.10"),
        "Cisco Systems":                ("Cisco IMC",             "CIMC.Embedded.1",  "4.3(2.240009)"),
    }
    return table.get(vendor_value, ("BMC", "BMC.Embedded.1", "1.00"))


def _proc_model(vendor_value: str) -> str:
    return {
        "Lenovo":     "Intel(R) Xeon(R) Gold 6338 CPU @ 2.00GHz",
        "Supermicro": "AMD EPYC 7763 64-Core Processor",
        "IBM":        "Intel(R) Xeon(R) Platinum 8380 CPU @ 2.30GHz",
    }.get(vendor_value, "Intel(R) Xeon(R) Gold 6338 CPU @ 2.00GHz")


def _device_uuid(device: "Device") -> str:
    return str(uuid.uuid5(_UUID_NS, device.id or device.name))


def member_id(device: "Device") -> str:
    """Stable URL id for this server's System/Chassis member."""
    return device.id or device.name.replace(" ", "_")


# ─────────────────────────────────────────────────────────────────────────────
#  Resource builders
# ─────────────────────────────────────────────────────────────────────────────

def service_root() -> dict:
    return {
        "@odata.type": "#ServiceRoot.v1_5_0.ServiceRoot",
        "@odata.id": "/redfish/v1/",
        "Id": "RootService",
        "Name": "Root Service",
        "RedfishVersion": REDFISH_VERSION,
        "Systems": {"@odata.id": "/redfish/v1/Systems"},
        "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
        "Managers": {"@odata.id": "/redfish/v1/Managers"},
        "SessionService": {"@odata.id": "/redfish/v1/SessionService"},
        "Links": {
            "Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"},
        },
    }


def _collection(odata_id: str, name: str, members: list[str]) -> dict:
    return {
        "@odata.type": "#Collection.Collection",
        "@odata.id": odata_id,
        "Name": name,
        "Members@odata.count": len(members),
        "Members": [{"@odata.id": m} for m in members],
    }


def systems_collection(device: "Device") -> dict:
    sid = member_id(device)
    return _collection("/redfish/v1/Systems", "Computer System Collection",
                       [f"/redfish/v1/Systems/{sid}"])


def chassis_collection(device: "Device") -> dict:
    cid = member_id(device)
    return _collection("/redfish/v1/Chassis", "Chassis Collection",
                       [f"/redfish/v1/Chassis/{cid}"])


def managers_collection() -> dict:
    return _collection("/redfish/v1/Managers", "Manager Collection",
                       [f"/redfish/v1/Managers/{MANAGER_ID}"])


def computer_system(device: "Device") -> dict:
    sid = member_id(device)
    vendor = device.vendor.value
    total_gib = round(device.memory_total / (1024 ** 3))
    return {
        "@odata.type": "#ComputerSystem.v1_13_0.ComputerSystem",
        "@odata.id": f"/redfish/v1/Systems/{sid}",
        "Id": sid,
        "Name": device.name,
        "SystemType": "Physical",
        "Manufacturer": vendor,
        "Model": device.model_name or vendor,
        "SerialNumber": f"SN-{(device.id or device.name)[:8].upper()}",
        "UUID": _device_uuid(device),
        "HostName": device.name,
        "BiosVersion": "U30 v2.66",
        "PowerState": "On",
        "Status": {"State": "Enabled", "Health": "OK"},
        "ProcessorSummary": {
            "Count": 2,
            "Model": _proc_model(vendor),
            "Status": {"State": "Enabled", "Health": "OK"},
        },
        "MemorySummary": {
            "TotalSystemMemoryGiB": total_gib,
            "Status": {"State": "Enabled", "Health": "OK"},
        },
        "Links": {
            "Chassis": [{"@odata.id": f"/redfish/v1/Chassis/{sid}"}],
            "ManagedBy": [{"@odata.id": f"/redfish/v1/Managers/{MANAGER_ID}"}],
        },
        # Live, non-spec convenience fields some clients surface under Oem.
        "Oem": {
            "Simulator": {
                "CpuUtilizationPercent": device.cpu_usage,
                "MemoryUsedBytes": device.memory_used,
                "DiskUsedBytes": device.disk_used,
                "DiskTotalBytes": device.disk_total,
            }
        },
    }


def chassis(device: "Device") -> dict:
    cid = member_id(device)
    vendor = device.vendor.value
    return {
        "@odata.type": "#Chassis.v1_14_0.Chassis",
        "@odata.id": f"/redfish/v1/Chassis/{cid}",
        "Id": cid,
        "Name": device.name,
        "ChassisType": "RackMount",
        "Manufacturer": vendor,
        "Model": device.model_name or vendor,
        "SerialNumber": f"SN-{(device.id or device.name)[:8].upper()}",
        "PowerState": "On",
        "Status": {"State": "Enabled", "Health": "OK"},
        "Thermal": {"@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal"},
        "Power": {"@odata.id": f"/redfish/v1/Chassis/{cid}/Power"},
        "Links": {
            "ComputerSystems": [{"@odata.id": f"/redfish/v1/Systems/{cid}"}],
            "ManagedBy": [{"@odata.id": f"/redfish/v1/Managers/{MANAGER_ID}"}],
        },
    }


def _temperature(cid: str, idx: int, name: str, reading: float,
                 ctx: str, crit: float) -> dict:
    return {
        "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal#/Temperatures/{idx}",
        "MemberId": str(idx),
        "Name": name,
        "ReadingCelsius": round(reading, 1),
        "PhysicalContext": ctx,
        "UpperThresholdCritical": crit,
        "Status": {"State": "Enabled",
                   "Health": "OK" if reading < crit else "Critical"},
    }


def thermal(device: "Device") -> dict:
    cid = member_id(device)
    temps = [
        _temperature(cid, 0, "CPU Temp", device.cpu_temp, "CPU", 95.0),
        _temperature(cid, 1, "Inlet Temp", device.inlet_temp, "Intake", 45.0),
    ]
    return {
        "@odata.type": "#Thermal.v1_7_0.Thermal",
        "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal",
        "Id": "Thermal",
        "Name": "Thermal",
        "Temperatures": temps,
    }


def power(device: "Device") -> dict:
    cid = member_id(device)
    watts = int(device.power_draw_w or 0)
    return {
        "@odata.type": "#Power.v1_7_0.Power",
        "@odata.id": f"/redfish/v1/Chassis/{cid}/Power",
        "Id": "Power",
        "Name": "Power",
        "PowerControl": [{
            "@odata.id": f"/redfish/v1/Chassis/{cid}/Power#/PowerControl/0",
            "MemberId": "0",
            "Name": "System Power Control",
            "PowerConsumedWatts": watts,
            "PowerMetrics": {
                "IntervalInMin": 1,
                "AverageConsumedWatts": watts,
                "MaxConsumedWatts": int(watts * 1.15),
                "MinConsumedWatts": int(watts * 0.85),
            },
            "Status": {"State": "Enabled", "Health": "OK"},
        }],
    }


def manager(device: "Device") -> dict:
    name, _short, fw = _bmc_branding(device.vendor.value)
    return {
        "@odata.type": "#Manager.v1_10_0.Manager",
        "@odata.id": f"/redfish/v1/Managers/{MANAGER_ID}",
        "Id": MANAGER_ID,
        "Name": name,
        "ManagerType": "BMC",
        "Manufacturer": device.vendor.value,
        "Model": name,
        "UUID": _device_uuid(device),
        "FirmwareVersion": fw,
        "PowerState": "On",
        "Status": {"State": "Enabled", "Health": "OK"},
        "Links": {
            "ManagerForServers": [{"@odata.id": f"/redfish/v1/Systems/{member_id(device)}"}],
            "ManagerForChassis": [{"@odata.id": f"/redfish/v1/Chassis/{member_id(device)}"}],
        },
    }


def session_service() -> dict:
    return {
        "@odata.type": "#SessionService.v1_1_8.SessionService",
        "@odata.id": "/redfish/v1/SessionService",
        "Id": "SessionService",
        "Name": "Session Service",
        "ServiceEnabled": True,
        "SessionTimeout": 1800,
        "Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"},
    }


def sessions_collection(session_ids: list[str]) -> dict:
    return _collection(
        "/redfish/v1/SessionService/Sessions", "Session Collection",
        [f"/redfish/v1/SessionService/Sessions/{s}" for s in session_ids],
    )


def session_member(session_id: str, username: str) -> dict:
    return {
        "@odata.type": "#Session.v1_3_0.Session",
        "@odata.id": f"/redfish/v1/SessionService/Sessions/{session_id}",
        "Id": session_id,
        "Name": f"User Session {session_id}",
        "UserName": username,
    }
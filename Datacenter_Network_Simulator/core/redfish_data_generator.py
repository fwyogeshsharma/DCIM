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
        "EventService": {"@odata.id": "/redfish/v1/EventService"},
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


# ResetType values the simulated BMC accepts.
RESET_TYPES = ["On", "ForceOn", "ForceOff", "GracefulShutdown",
               "GracefulRestart", "ForceRestart", "PowerCycle", "PushPowerButton"]


def computer_system(device: "Device", power_state: str = "On",
                    indicator_led: str = "Off") -> dict:
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
        "PowerState": power_state,
        "IndicatorLED": indicator_led,
        "LocationIndicatorActive": indicator_led != "Off",
        "Status": {"State": "Enabled" if power_state == "On" else "StandbyOffline",
                   "Health": "OK"},
        "Actions": {
            "#ComputerSystem.Reset": {
                "target": f"/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset",
                "ResetType@Redfish.AllowableValues": RESET_TYPES,
            },
            "Oem": {
                "#Simulator.RefreshInventory": {
                    "target": f"/redfish/v1/Systems/{sid}/Actions/Oem/Simulator.RefreshInventory",
                },
            },
        },
        "ProcessorSummary": {
            "Count": 2,
            "Model": _proc_model(vendor),
            "Status": {"State": "Enabled", "Health": "OK"},
        },
        "MemorySummary": {
            "TotalSystemMemoryGiB": total_gib,
            "Status": {"State": "Enabled", "Health": "OK"},
        },
        "EthernetInterfaces": {"@odata.id": f"/redfish/v1/Systems/{sid}/EthernetInterfaces"},
        "Storage": {"@odata.id": f"/redfish/v1/Systems/{sid}/Storage"},
        "LogServices": {"@odata.id": f"/redfish/v1/Systems/{sid}/LogServices"},
        "Links": {
            "Chassis": [{"@odata.id": f"/redfish/v1/Chassis/{sid}"}],
            "ManagedBy": [{"@odata.id": f"/redfish/v1/Managers/{MANAGER_ID}"}],
        },
        # Live, non-spec convenience fields some clients surface under Oem.
        "Oem": {
            "Simulator": {
                "CpuUtilizationPercent": device.cpu_usage,
                "MemoryUtilizationPercent": _mem_pct(device),
                "DiskUtilizationPercent": _disk_pct(device),
                "MemoryUsedBytes": device.memory_used,
                "DiskUsedBytes": device.disk_used,
                "DiskTotalBytes": device.disk_total,
                "NetworkRxMbps": _net_totals(device)[0],
                "NetworkTxMbps": _net_totals(device)[1],
                "AlarmCount": len(_alarms(device)),
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
        "Fans": _fans(device),
    }


def _fans(device: "Device") -> list:
    """Synthesize fan RPMs that rise with CPU temperature."""
    cid = member_id(device)
    base = 3000 + max(0.0, device.cpu_temp - 40.0) * 95.0
    fans = []
    for i in range(4):
        rpm = int(base + (i - 1.5) * 110 + (device.cpu_usage % 7) * 18)
        fans.append({
            "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal#/Fans/{i}",
            "MemberId": str(i),
            "Name": f"Fan {i + 1}",
            "Reading": rpm,
            "ReadingUnits": "RPM",
            "Status": {"State": "Enabled",
                       "Health": "OK" if rpm < 14000 else "Warning"},
        })
    return fans


def _live_watts(device: "Device") -> int:
    """Live power draw: idle floor + load component driven by CPU utilization.

    ``power_draw_w`` is a static config field the ticker never updates, so we
    anchor it as the full-load nominal and scale with cpu_usage so the reading
    moves every tick. At 100% CPU it equals the configured nominal.
    """
    if getattr(device, "power_state", "On") == "Off":
        return 0
    nominal = int(device.power_draw_w or 0)
    if nominal <= 0:
        return 0
    load = max(0.0, min(1.0, device.cpu_usage / 100.0))
    return int(nominal * (0.55 + 0.45 * load))


def power(device: "Device") -> dict:
    cid = member_id(device)
    watts = _live_watts(device)
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
        "PowerSupplies": _power_supplies(device),
    }


def _power_supplies(device: "Device") -> list:
    """Two redundant PSUs splitting the chassis load."""
    cid = member_id(device)
    watts = _live_watts(device)
    half = watts / 2.0
    psus = []
    for i in range(2):
        psus.append({
            "@odata.id": f"/redfish/v1/Chassis/{cid}/Power#/PowerSupplies/{i}",
            "MemberId": str(i),
            "Name": f"PSU {i + 1}",
            "PowerSupplyType": "AC",
            "LineInputVoltage": 230,
            "PowerCapacityWatts": 1100,
            "LastPowerOutputWatts": round(half, 1),
            "Model": "PWR-1100W-AC",
            "Status": {"State": "Enabled", "Health": "OK"},
        })
    return psus


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
        "Actions": {
            "#Manager.Reset": {
                "target": f"/redfish/v1/Managers/{MANAGER_ID}/Actions/Manager.Reset",
                "ResetType@Redfish.AllowableValues": ["GracefulRestart", "ForceRestart"],
            },
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


# ─────────────────────────────────────────────────────────────────────────────
#  EventService — push-model subscriptions (EventDestination)
# ─────────────────────────────────────────────────────────────────────────────

# EventTypes the simulated BMC raises (legacy EventType enum, still widely used).
EVENT_TYPES = ["Alert", "ResourceUpdated", "StatusChange"]
# RegistryPrefixes events are tagged with (used for subscription filtering).
REGISTRY_PREFIXES = ["Base", "ResourceEvent"]


def event_service(sub_ids: list[str]) -> dict:
    return {
        "@odata.type": "#EventService.v1_5_0.EventService",
        "@odata.id": "/redfish/v1/EventService",
        "Id": "EventService",
        "Name": "Event Service",
        "ServiceEnabled": True,
        "DeliveryRetryAttempts": 3,
        "DeliveryRetryIntervalSeconds": 5,
        "EventFormatTypes": ["Event"],
        "RegistryPrefixes": REGISTRY_PREFIXES,
        "ResourceTypes": ["ComputerSystem", "Chassis", "Manager"],
        "EventTypesForSubscription": EVENT_TYPES,
        "Subscriptions": {"@odata.id": "/redfish/v1/EventService/Subscriptions"},
        "Actions": {
            "#EventService.SubmitTestEvent": {
                "target": "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent",
            },
        },
    }


def subscriptions_collection(sub_ids: list[str]) -> dict:
    return _collection(
        "/redfish/v1/EventService/Subscriptions", "Event Subscription Collection",
        [f"/redfish/v1/EventService/Subscriptions/{s}" for s in sub_ids],
    )


def subscription_member(sub: dict) -> dict:
    """sub: stored subscription dict (see RedfishDevice._create_subscription)."""
    sid = sub["id"]
    body = {
        "@odata.type": "#EventDestination.v1_10_0.EventDestination",
        "@odata.id": f"/redfish/v1/EventService/Subscriptions/{sid}",
        "Id": sid,
        "Name": sub.get("Name") or f"Event Subscription {sid}",
        "Destination": sub["Destination"],
        "Protocol": "Redfish",
        "SubscriptionType": "RedfishEvent",
        "Context": sub.get("Context", ""),
        "EventTypes": sub.get("EventTypes") or list(EVENT_TYPES),
    }
    if sub.get("RegistryPrefixes"):
        body["RegistryPrefixes"] = sub["RegistryPrefixes"]
    return body


def event_record(seq: int, severity: str, message: str,
                 event_type: str, message_id: str,
                 origin: Optional[str] = None,
                 context: str = "") -> dict:
    """One Redfish Event document — the body POSTed to a subscriber."""
    ev = {
        "EventType": event_type,
        "EventId": str(seq),
        "Severity": severity,
        "Message": message,
        "MessageId": message_id,
        "MemberId": str(seq),
    }
    if origin:
        ev["OriginOfCondition"] = {"@odata.id": origin}
    return {
        "@odata.type": "#Event.v1_7_0.Event",
        "Id": str(seq),
        "Name": "Simulator Event",
        "Context": context,
        "Events": [ev],
        "Events@odata.count": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Derived live-metric helpers (pure functions of the live Device)
# ─────────────────────────────────────────────────────────────────────────────

def _mem_pct(device: "Device") -> float:
    return round(device.memory_used / device.memory_total * 100, 1) if device.memory_total else 0.0


def _disk_pct(device: "Device") -> float:
    return round(device.disk_used / device.disk_total * 100, 1) if device.disk_total else 0.0


def _iface_rates(device: "Device") -> list:
    """Synthesize per-interface Rx/Tx (Mbps) from link speed and CPU load.

    Deterministic per request (varies as the ticker moves cpu_usage), so the
    BMC reports plausible live throughput without needing counter-delta state.
    Returns list of (index, iface, rx_mbps, tx_mbps).
    """
    load = max(0.0, min(1.0, device.cpu_usage / 100.0))
    out = []
    for iface in device.interfaces:
        # Unconnected ports carry no traffic — report zero throughput.
        if iface.connected_to_device is None:
            out.append((iface.index, iface, 0.0, 0.0))
            continue
        speed_mbps = iface.speed / 1_000_000.0
        seed = ((device.cpu_usage + iface.index * 7) % 100) / 100.0
        rx_u = max(0.01, min(0.95, load * (0.50 + 0.50 * seed)))
        tx_u = max(0.01, min(0.95, load * (0.30 + 0.60 * (1.0 - seed))))
        out.append((iface.index, iface, round(speed_mbps * rx_u, 1),
                    round(speed_mbps * tx_u, 1)))
    return out


def _net_totals(device: "Device") -> tuple[float, float]:
    rates = _iface_rates(device)
    return (round(sum(r[2] for r in rates), 1), round(sum(r[3] for r in rates), 1))


def _alarms(device: "Device") -> list[tuple[str, str]]:
    """Active alarm conditions → list of (severity, message)."""
    al: list[tuple[str, str]] = []
    if device.cpu_temp >= 85.0:
        al.append(("Critical", f"CPU temperature high: {device.cpu_temp:.1f} C"))
    if device.inlet_temp >= 40.0:
        al.append(("Warning", f"Inlet temperature high: {device.inlet_temp:.1f} C"))
    if _mem_pct(device) >= 90.0:
        al.append(("Warning", f"Memory utilization high: {_mem_pct(device):.0f}%"))
    if _disk_pct(device) >= 90.0:
        al.append(("Warning", f"Disk utilization high: {_disk_pct(device):.0f}%"))
    for f in _fans(device):
        if (f.get("Status") or {}).get("Health") != "OK":
            al.append(("Warning", f"Fan over-speed: {f['Name']} {f['Reading']} RPM"))
            break
    return al


# ─────────────────────────────────────────────────────────────────────────────
#  EthernetInterfaces  (Network Rx/Tx)
# ─────────────────────────────────────────────────────────────────────────────

def _nic_id(idx: int) -> str:
    return f"NIC.{idx}"


def ethernet_collection(device: "Device") -> dict:
    sid = member_id(device)
    ids = [f"/redfish/v1/Systems/{sid}/EthernetInterfaces/{_nic_id(i)}"
           for (i, *_rest) in _iface_rates(device)]
    return _collection(f"/redfish/v1/Systems/{sid}/EthernetInterfaces",
                       "Ethernet Interface Collection", ids)


def ethernet_interface(device: "Device", nic_id: str):
    """Return the EthernetInterface member for nic_id, or None if not found."""
    sid = member_id(device)
    for idx, iface, rx, tx in _iface_rates(device):
        if _nic_id(idx) == nic_id:
            # Unconnected ports are down regardless of raw oper_status (defaults
            # to 1), matching the SNMP agent so Redfish reports only real links up.
            up = iface.connected_to_device is not None and iface.oper_status == 1
            return {
                "@odata.type": "#EthernetInterface.v1_6_0.EthernetInterface",
                "@odata.id": f"/redfish/v1/Systems/{sid}/EthernetInterfaces/{nic_id}",
                "Id": nic_id,
                "Name": iface.name,
                "PermanentMACAddress": iface.mac_address,
                "MACAddress": iface.mac_address,
                "SpeedMbps": int(iface.speed / 1_000_000),
                "FullDuplex": True,
                "LinkStatus": "LinkUp" if up else "LinkDown",
                "InterfaceEnabled": up,
                "Status": {"State": "Enabled" if up else "Disabled",
                           "Health": "OK" if up else "Critical"},
                "Oem": {"Simulator": {"RxMbps": rx, "TxMbps": tx}},
            }
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Storage  (RAID status)
# ─────────────────────────────────────────────────────────────────────────────

STORAGE_ID = "Storage.1"


def storage_collection(device: "Device") -> dict:
    sid = member_id(device)
    return _collection(f"/redfish/v1/Systems/{sid}/Storage",
                       "Storage Collection",
                       [f"/redfish/v1/Systems/{sid}/Storage/{STORAGE_ID}"])


def storage(device: "Device") -> dict:
    sid = member_id(device)
    vendor = device.vendor.value
    ctrl = {"Lenovo": "ThinkSystem RAID 940-8i",
            "Supermicro": "Broadcom MegaRAID 9560-8i",
            "IBM": "ServeRAID M5210"}.get(vendor, "Dell PERC H755")
    raid_type = "RAID5"
    total_gb = int(device.disk_total / 1024**3)
    used_pct = _disk_pct(device)
    health = "OK" if used_pct < 95 else "Warning"
    return {
        "@odata.type": "#Storage.v1_9_0.Storage",
        "@odata.id": f"/redfish/v1/Systems/{sid}/Storage/{STORAGE_ID}",
        "Id": STORAGE_ID,
        "Name": "Storage Subsystem",
        "Status": {"State": "Enabled", "Health": health},
        "StorageControllers": [{
            "MemberId": "0",
            "Name": ctrl,
            "Manufacturer": vendor,
            "SupportedRAIDTypes": ["RAID0", "RAID1", "RAID5", "RAID10"],
            "Status": {"State": "Enabled", "Health": health},
        }],
        "Oem": {"Simulator": {
            "RaidType": raid_type,
            "RaidStatus": "Optimal" if health == "OK" else "Degraded",
            "VolumeCapacityGiB": total_gb,
            "VolumeUsedPercent": used_pct,
        }},
    }


# ─────────────────────────────────────────────────────────────────────────────
#  LogServices / SEL  (Alarm count)
# ─────────────────────────────────────────────────────────────────────────────

def logservices_collection(device: "Device") -> dict:
    sid = member_id(device)
    return _collection(f"/redfish/v1/Systems/{sid}/LogServices",
                       "Log Service Collection",
                       [f"/redfish/v1/Systems/{sid}/LogServices/SEL"])


def logservice_sel(device: "Device", entries: list) -> dict:
    sid = member_id(device)
    return {
        "@odata.type": "#LogService.v1_3_0.LogService",
        "@odata.id": f"/redfish/v1/Systems/{sid}/LogServices/SEL",
        "Id": "SEL",
        "Name": "System Event Log",
        "OverWritePolicy": "WrapsWhenFull",
        "ServiceEnabled": True,
        "Entries": {"@odata.id": f"/redfish/v1/Systems/{sid}/LogServices/SEL/Entries"},
        "Actions": {
            "#LogService.ClearLog": {
                "target": f"/redfish/v1/Systems/{sid}/LogServices/SEL/Actions/LogService.ClearLog",
            },
        },
        "Oem": {"Simulator": {"EntryCount": len(entries),
                              "AlarmCount": len(_alarms(device))}},
    }


def logservice_entries(device: "Device", entries: list) -> dict:
    """entries: list of stored event dicts {Id, Severity, Message, Created}."""
    sid = member_id(device)
    members = []
    for e in entries:
        members.append({
            "@odata.id": f"/redfish/v1/Systems/{sid}/LogServices/SEL/Entries/{e['Id']}",
            "@odata.type": "#LogEntry.v1_8_0.LogEntry",
            "Id": str(e["Id"]),
            "Name": e.get("Name", f"Event {e['Id']}"),
            "EntryType": "SEL",
            "Severity": e.get("Severity", "OK"),
            "Message": e.get("Message", ""),
            "Created": e.get("Created", ""),
        })
    return {
        "@odata.type": "#LogEntryCollection.LogEntryCollection",
        "@odata.id": f"/redfish/v1/Systems/{sid}/LogServices/SEL/Entries",
        "Name": "System Event Log Entries",
        "Members@odata.count": len(members),
        "Members": members,
    }
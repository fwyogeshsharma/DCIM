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

from core.device_manager import outlet_voltage

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


def _location(device: "Device") -> dict:
    """Redfish Resource.Location for the chassis — the device-side placement
    breadcrumb a DCIM reads to bind a sensor reading to a rack/RU.

    PostalAddress (datacenter/floor/room) is emitted whenever the datacenter is
    known. Placement/PartLocation (rack, RU offset) is emitted only for
    rack-mounted gear; floor-standing facility equipment (UPS, generator, CRAH…)
    carries synthetic room-grid coordinates that are not real rack positions, so
    rack tokens are suppressed — mirroring the sysLocation rule in device_manager.
    """
    from core.device_manager import FACILITY_TYPES
    loc: dict = {}

    postal = {}
    if device.country:
        postal["Country"] = device.country
    if device.datacenter_city:
        postal["City"] = device.datacenter_city
    if device.datacenter:
        postal["Building"] = device.datacenter
    if device.floor:
        postal["Floor"] = str(device.floor)
    if device.room:
        postal["Room"] = device.room
    if postal:
        loc["PostalAddress"] = postal

    if device.device_type not in FACILITY_TYPES and device.rack_row and device.rack_num:
        loc["Placement"] = {
            "Row": str(device.rack_row),
            "Rack": f"{device.datacenter}-R{device.rack_row}-{device.rack_num}",
            "RackOffset": device.rack_unit,
            "RackOffsetUnits": "EIA_310",
        }
        loc["PartLocation"] = {
            "LocationType": "Slot",
            "ServiceLabel": f"U{device.rack_unit}",
            "LocationOrdinalValue": device.rack_unit,
        }
    return loc


def chassis(device: "Device") -> dict:
    cid = member_id(device)
    vendor = device.vendor.value
    doc = {
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
    location = _location(device)
    if location:
        doc["Location"] = location
    return doc


def _temperature(cid: str, idx: int, name: str, reading: float,
                 ctx: str, crit: float, warn: float | None = None) -> dict:
    health = "Critical" if reading >= crit else (
        "Warning" if warn is not None and reading >= warn else "OK")
    d = {
        "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal#/Temperatures/{idx}",
        "MemberId": str(idx),
        "Name": name,
        "ReadingCelsius": round(reading, 1),
        "PhysicalContext": ctx,
        "UpperThresholdCritical": crit,
        "Status": {"State": "Enabled", "Health": health},
    }
    if warn is not None:
        d["UpperThresholdNonCritical"] = warn
    return d


def thermal(device: "Device") -> dict:
    cid = member_id(device)
    # Thresholds match the BMC alarm points in _alarms() and the SNMP trap rules
    # (HighTemperature > 90). CPU: Warning 85 / Critical 90. Inlet: 40 / 45.
    temps = [
        _temperature(cid, 0, "CPU Temp", device.cpu_temp, "CPU", 90.0, warn=85.0),
        _temperature(cid, 1, "Inlet Temp", device.inlet_temp, "Intake", 45.0, warn=40.0),
    ]
    # Exhaust/outlet sensor — populated by the ticker for servers; DCIM reads
    # this (PhysicalContext "Exhaust") to build hot-aisle heatmaps.
    if device.outlet_temp > 0:
        temps.append(_temperature(cid, 2, "Exhaust Temp", device.outlet_temp,
                                   "Exhaust", 60.0, warn=55.0))
    return {
        "@odata.type": "#Thermal.v1_7_0.Thermal",
        "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal",
        "Id": "Thermal",
        "Name": "Thermal",
        "Temperatures": temps,
        "Fans": _fans(device),
    }


def _fans(device: "Device") -> list:
    """Per-fan RPMs spread around the chassis fan speed. Reads the ticker-driven
    ``fan_rpm`` (single source of truth); falls back to a temp-derived value if
    the ticker hasn't run yet."""
    from core.device_manager import fan_rpm_range
    cid = member_id(device)
    lo, hi = fan_rpm_range(getattr(device, "model_name", "") or "")
    base = float(getattr(device, "fan_rpm", 0) or 0)
    if base <= 0:
        base = lo + (hi - lo) * max(0.0, min(1.0, (device.cpu_temp - 40.0) / 45.0))
    # Over-speed is judged against THIS chassis's full duty, not a flat number: a 1U
    # runs to 20 krpm by design, so a fixed 14 krpm threshold would report every
    # loaded 1U as Warning while never flagging a genuinely runaway 4U.
    redline = hi * 1.05
    # Under-speed floors. `lo` is the air-cooled minimum; a direct-to-chip chassis
    # legitimately idles below it, so the floor is relaxed by the same factor the
    # ticker uses. Imported here rather than duplicated — a fan must not read
    # healthy over SNMP and failed over Redfish.
    from core.device_state_store import _DTC_IDLE_FACTOR, _is_liquid_server
    floor = lo * (_DTC_IDLE_FACTOR if _is_liquid_server(device.name) else 1.0)
    off = getattr(device, "power_state", "On") == "Off"
    fans = []
    for i in range(4):
        rpm = int(base + (i - 1.5) * 110 + (device.cpu_usage % 7) * 18)
        # A chassis the operator powered off has stopped fans by definition — that
        # is not a fault, and reporting it as one would light up every server that
        # is intentionally down.
        if off:
            state, health = "StandbyOffline", "OK"
        elif rpm < floor * 0.25:
            state, health = "Enabled", "Critical"      # stalled rotor / no drive
        elif rpm < floor * 0.90:
            state, health = "Enabled", "Warning"       # dragging below min duty
        elif rpm >= redline:
            state, health = "Enabled", "Warning"       # runaway
        else:
            state, health = "Enabled", "OK"
        fans.append({
            "@odata.id": f"/redfish/v1/Chassis/{cid}/Thermal#/Fans/{i}",
            "MemberId": str(i),
            "Name": f"Fan {i + 1}",
            "Reading": 0 if off else rpm,
            "ReadingUnits": "RPM",
            "LowerThresholdCritical": int(floor * 0.25),
            "LowerThresholdNonCritical": int(floor * 0.90),
            "UpperThresholdNonCritical": int(redline),
            "Status": {"State": state, "Health": health},
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


def power(device: "Device", topology=None) -> dict:
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
        "PowerSupplies": _power_supplies(device, topology),
    }


# A modern 80 PLUS Platinum PSU runs ~92% efficient around the 30-50% load band
# these chassis sit in. Redfish reports input and output separately, and the
# difference between them is heat — it is not a rounding artefact.
_PSU_EFFICIENCY = 0.92


def _power_supplies(device: "Device", topology=None) -> list:
    """The chassis PSUs, from the device's modelled 1+1 pair.

    Reads device.psus rather than assuming two 1100W supplies: a CDU carries C20
    inlets and 2400W supplies, and inventing a 1100W PSU for it would contradict
    the cord the topology actually plugs in. A device with no PSUs (an environmental
    sensor is powered off the PDU's sensor port) correctly reports none.

    With *topology*, LineInputVoltage comes from the PDU each cord actually runs to
    — 208V on a 1-phase rPDU, 240V line-to-neutral on a 415V 3-phase one — because
    input voltage is a fact about the FEED, not about the supply. Without it (a BMC
    built outside a loaded topology) the reading falls back to a 230V nominal.

    MemberId stays 0-based to keep the @odata.id URLs stable for existing clients;
    Name carries the model's 1-based PSU number, which is what an operator reads.
    """
    cid = member_id(device)
    psus = getattr(device, "psus", None) or []
    if not psus:
        return []
    feeds = topology.power_feeds(device.id) if topology is not None else {}
    watts = _live_watts(device)
    # A 1+1 pair shares the load; both supplies are live, each carrying its share
    # of the WALL draw. (A failed-PSU case would put the whole chassis on the
    # survivor — not modelled here, since nothing fails a PSU yet.)
    input_per_psu = watts / len(psus)
    out = []
    for i, p in enumerate(psus):
        feed = feeds.get(p.index)
        volts = outlet_voltage(feed["supply_model"]) if feed else 230
        entry = {
            "@odata.id": f"/redfish/v1/Chassis/{cid}/Power#/PowerSupplies/{i}",
            "MemberId": str(i),
            "Name": p.name,
            "PowerSupplyType": "AC",
            "LineInputVoltage": volts,
            "PowerCapacityWatts": p.capacity_w,
            "PowerInputWatts": round(input_per_psu, 1),
            "LastPowerOutputWatts": round(input_per_psu * _PSU_EFFICIENCY, 1),
            "InputRanges": [{
                "InputType": "AC",
                # Auto-ranging supply: one SKU covers every rack voltage it might
                # be plugged into, which is why the same server works on a 208V and
                # a 240V feed.
                "MinimumVoltage": 100,
                "MaximumVoltage": 240,
                "OutputWattage": p.capacity_w,
            }],
            "Model": f"PWR-{p.capacity_w}W-AC",
            "Status": {"State": "Enabled", "Health": "OK"},
        }
        if feed:
            # Which cord this supply is on. Redfish's Power schema has no standard
            # field for the upstream outlet (that link lives in PowerDistribution /
            # Circuit on the PDU side), so it goes under Oem — where real vendors
            # put exactly this kind of site-specific detail — rather than being
            # bolted onto a standard property that means something else.
            entry["Oem"] = {"DCSim": {
                "@odata.type": "#DCSimPowerSupply.v1_0_0.DCSimPowerSupply",
                "FeedSide": feed["feed"],
                "SourcePDU": feed["supply_name"],
                "SourceOutlet": feed["outlet"],
                "InletType": p.inlet,
            }}
        out.append(entry)
    return out


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
    # CPU thresholds match the Redfish sensor (Warning 85 / Critical 90) and the
    # SNMP HighTemperature trap (> 90).
    if device.cpu_temp >= 90.0:
        al.append(("Critical", f"CPU temperature critical: {device.cpu_temp:.1f} C"))
    elif device.cpu_temp >= 85.0:
        al.append(("Warning", f"CPU temperature high: {device.cpu_temp:.1f} C"))
    if device.inlet_temp >= 45.0:
        al.append(("Critical", f"Inlet temperature critical: {device.inlet_temp:.1f} C"))
    elif device.inlet_temp >= 40.0:
        al.append(("Warning", f"Inlet temperature high: {device.inlet_temp:.1f} C"))
    if _mem_pct(device) >= 90.0:
        al.append(("Warning", f"Memory utilization high: {_mem_pct(device):.0f}%"))
    if _disk_pct(device) >= 90.0:
        al.append(("Warning", f"Disk utilization high: {_disk_pct(device):.0f}%"))
    for f in _fans(device):
        health = (f.get("Status") or {}).get("Health")
        if health == "OK":
            continue
        rpm = f["Reading"]
        if rpm < f.get("LowerThresholdCritical", 0):
            al.append(("Critical", f"Fan failed: {f['Name']} stopped ({rpm} RPM)"))
        elif rpm < f.get("LowerThresholdNonCritical", 0):
            al.append(("Warning", f"Fan speed low: {f['Name']} {rpm} RPM below "
                                  f"minimum duty"))
        else:
            al.append(("Warning", f"Fan over-speed: {f['Name']} {rpm} RPM"))
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
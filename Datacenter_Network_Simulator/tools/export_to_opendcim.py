#!/usr/bin/env python3
"""Publish the simulated estate into openDCIM via its REST API (v1).

WHY THIS SHAPE
--------------
openDCIM is an asset system of record for IT space and the power chain. It models
datacenters, cabinets, devices (with a make/model template), and panelboards. It does
NOT model mechanical plant: there is no object for a chiller, a cooling tower, a
condenser-water pump or an isolation valve, and a CRAH is only representable by
inventing a cabinet for it. Those stay in the BMS/CMMS, which is where a real operator
keeps them, so this exporter skips them rather than fabricating assets.

What maps, and to what:

    simulator                       openDCIM
    ---------------------------------------------------------------
    DC1 / DC2                       DataCenter      (must PRE-EXIST — see below)
    room + rack_row + rack_num      Cabinet
    vendor                          Manufacturer
    model_name                      DeviceTemplate  (height/ports/PSUs/watts)
    server                          Device, type Server
    switch / oob_switch / router    Device, type Switch
    firewall / load_balancer        Device, type Appliance
    pdu                             Device, type CDU        (zero-U)
    cdu  (in-rack coolant dist.)    Device, type Physical Infrastructure
    sensor                          Device, type Sensor     (zero-U)
    rpp / mpp / mcc / ats /         PowerPanel, parented along the real feed
      ups / switchgear /
      utility_feed
    chiller / cooling_tower /       SKIPPED — no openDCIM object exists
      pump / valve / crah
    energy_monitor                  SKIPPED — a sub-meter is not an asset here

THE API CANNOT CREATE DATACENTERS. /api/v1 exposes PUT for cabinet, device,
devicetemplate, manufacturer and powerpanel, but datacenter, zone and cabrow are
GET-only. Create the DataCenters in the openDCIM UI first (Admin -> Data Centers);
this tool matches them by name and refuses to run if one is missing, rather than
scattering cabinets into whatever DataCenterID happens to be 1.

AUTH. openDCIM accepts an API token or the user Apache authenticated (REMOTE_USER).
This install sits behind Apache Basic auth, so one HTTP Basic credential does both —
but the matching openDCIM People record needs **Site Administrator**: cabinet and
powerpanel creation check `$person->SiteAdmin`, and device creation checks that the
target cabinet's rights are "Write".

NEITHER CAN IT WRITE A DEVICE'S SNMP TEMPLATE. A CDU's outlet OIDs live in
fac_CDUTemplate and a sensor's temperature/humidity OIDs in fac_SensorTemplate — both
shadow rows openDCIM auto-creates alongside the device template but leaves empty, and
no REST route touches either table. Until they are filled, the Status column on a
CDU's Power Connections panel can never leave 'err', the PDU is never polled for
wattage, and a cabinet's Environmental Sensors panel has nothing to show. That, and
everything else the API cannot reach, is emitted as SQL (--post-import-sql, on by
default) next to the breaker SQL; run both against the openDCIM database.

POLLING IS SCHEDULED BY YOU, NOT BY openDCIM. Upstream ships poll_pdu_stats.php and
poll_temperature_sensors.php as CLI scripts and schedules neither, leaving the
interval to the site. Without cron entries fac_PDUStats and fac_SensorReadings are
never refreshed and the UI renders whatever is in them as though it were current —
there is no last-polled indicator anywhere. See deploy/opendcim.cron.

IDEMPOTENT. Every phase reads what exists first and creates only what is missing,
keyed by natural name (manufacturer name, template model, cabinet location, device
label, panel label). Re-run it after the topology changes; it will add the delta.

USAGE
    # plan only — reads the simulator, writes nothing, works without openDCIM creds
    python tools/export_to_opendcim.py --dry-run

    # real run
    python tools/export_to_opendcim.py \
        --dcim-url http://localhost --dcim-user admin --dcim-pass secret

    # smoke test on one datacenter
    python tools/export_to_opendcim.py --only-dc DC1 --limit 25 ...
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rack_capacity import device_u_height, device_weight_kg  # noqa: E402
from core.device_manager import PDU_OUTLET_CATALOG, outlet_voltage  # noqa: E402


# ── mapping tables ────────────────────────────────────────────────────────────

# openDCIM's own enum (classes/DeviceTemplate.class.php): Server, Appliance,
# Storage Array, Switch, Chassis, Patch Panel, Physical Infrastructure, CDU, Sensor.
DEVICE_TYPE_MAP = {
    "server":        "Server",
    "switch":        "Switch",
    "oob_switch":    "Switch",
    # openDCIM has no Router type. A router is an Appliance, not a Switch: it is a
    # distinct managed box, and filing it as Switch pollutes switch-port reporting.
    "router":        "Appliance",
    "firewall":      "Appliance",
    "load_balancer": "Appliance",
    "pdu":           "CDU",
    "cdu":           "Physical Infrastructure",
    "sensor":        "Sensor",
}

# Modelled as panelboards, not devices — they distribute power, they do not consume a U.
PANEL_TYPES = {
    "utility_feed", "switchgear", "ats", "mcc", "mpp", "ups", "rpp", "generator",
}

# No openDCIM object exists. Mechanical plant belongs to the BMS.
SKIP_TYPES = {
    "chiller", "cooling_tower", "pump", "valve", "crah", "energy_monitor",
}

# Devices that hang on the rack rails or the side channel rather than occupying a U.
ZERO_U_TYPES = {"pdu", "sensor"}

# Per-cabinet design power allowance, kW. THIS IS A SITE DESIGN INPUT, NOT A
# MEASUREMENT — the simulator models rack U-space and per-SKU wattage but has no
# rack-level budget to export, so this comes from the datacenter's design, the same
# way it would be handed to you by whoever sized the hall.
#
# It is what the room was built to deliver per rack, and it is the number every
# capacity meter in openDCIM divides by: cabnavigator guards Space, Weight, Computed
# Watts and Measured Watts on MaxKW > 0, so leaving it at 0 renders every meter dead
# and every percentage as zero. It is NOT the strip's ceiling — cabinet 29's A and B
# feeds can each deliver 18.4 kW, but a 2N pair is sized so EITHER side carries the
# whole rack alone, so the design allowance is well under one feed's rating.
CABINET_DESIGN_KW = {
    # A rack's allowance is a property of the ROOM it stands in, because it is the
    # room's distribution that was sized, not the rack. One estate-wide number gave a
    # cabinet of six temperature probes the same 15 kW as a rack of eighteen servers,
    # which reads as 1% forever and quietly asserts that heavy power was run to the
    # plant room. It was not.
    #
    # Measured peak per room at the time of writing, for calibration:
    #   Server Hall A 11.28 kW   Server Hall B 11.47 kW   Network Room 0.37 kW
    # The halls are set just above their real peak: enough headroom to add a machine,
    # tight enough that filling a rack shows up. The others are DESIGN values, not
    # measurements — see the Network Room note below.
    "Server Hall A": 15,
    "Server Hall B": 15,
    # A network rack is lighter than a compute rack but far from trivial: chassis
    # switches, optics and dual supplies. NOT set from the measured 0.37 kW — that
    # figure is an artifact of the simulator barely modelling power draw on network
    # gear (NominalWatts comes from the live power_watts, which stays near zero for
    # switches). Sizing a room's distribution from a known-low measurement would bake
    # the modelling gap into the capacity plan.
    "Network Room": 8,
    # Instruments, control panels and facility gear. These are not IT racks and no one
    # runs a compute rack's feed to them.
    "Central Plant": 3,
    "Mechanical Room": 3,
    "UPS Room": 3,
    "Generator Room": 3,
    "Roof": 3,
}

# Rooms the map does not name. Deliberately the plant figure, not the hall figure:
# an unrecognised room is more likely to be facility space than a new compute hall,
# and under-stating an allowance shows up as a rack over 100% — which someone
# investigates — where over-stating hides a full rack behind a comfortable number.
CABINET_DESIGN_KW_DEFAULT = 3

# Per-cabinet weight limit, KILOGRAMS. Also a site design input: it is the raised
# floor's rating, not anything the simulator knows. openDCIM's Weight field carries no
# unit of its own — the schema and the UI are both unitless — so the only requirement
# is that this and the template weights use the SAME unit. Both are kg here.
CABINET_MAX_WEIGHT = 1200

# Feed order, upstream first — a panel's ParentPanelID must exist before it is set.
PANEL_ORDER = ["utility_feed", "generator", "switchgear", "ats", "mcc", "ups",
               "rpp", "mpp"]

# Nominal panel voltages. Wye 400/230 V would be the other common build; this estate
# is modelled on 480 V distribution with 415/240 V to the racks.
# 400 V, not 480: this estate is an IEC 400/230 V design and its own meters say so.
# Every LV board serves 400.0 V line-to-line (core/snmprec_generator.py seeds 4000 =
# x10 V, and device_state_store computes v_ll_true = 400.0 * (1 - 0.02 * load)), the
# grid runs at 50 Hz, the RPPs are 415 V boards and the rack outlets deliver 230/240 V
# — 415/sqrt(3) = 240. A 480 V panel record contradicted all of it.
#
# It is not cosmetic. openDCIM derives watts from a meter's AMPS with the panel's
# voltage (PowerDistribution::UpdateStats), so 480 against a 400 V bus over-read every
# board by 20%, and panel_main_breaker_a sizes a UPS main by converting its kVA at
# this voltage — 1200 kVA read as 1443 A instead of the 1732 A a 400 V frame draws.
#
# The estate's SITE data still says Chicago, which a US reader would expect to be
# 480/277 V at 60 Hz. That contradiction is in the topology, not here; this table
# follows the telemetry, which is what the panel record is supposed to describe.
PANEL_VOLTAGE = {
    "utility_feed": 400, "generator": 400, "switchgear": 400, "ats": 400,
    "mcc": 400, "ups": 400, "rpp": 415, "mpp": 400,
}


# ── tiny HTTP helpers ─────────────────────────────────────────────────────────

class Http:
    """Keep-alive HTTP client with one reused connection and one reused PHP session.

    A connection per request cost ~26 s stalls every few dozen writes on this host,
    and every request created a fresh PHPSESSID because no cookie was ever sent back —
    hundreds of throwaway session files for one import. One connection, one session.

    Also unwraps openDCIM's error convention: it answers HTTP 200 with {"error":true}
    in the body for a rejected write, so checking the status code alone reports a
    failed import as a success.
    """

    def __init__(self, base: str, user: str = "", password: str = "", timeout: int = 60):
        parts = urllib.parse.urlsplit(base if "://" in base else "http://" + base)
        self.scheme = parts.scheme or "http"
        self.host = parts.netloc
        self.prefix = parts.path.rstrip("/")
        self.timeout = timeout
        self.headers = {"Accept": "application/json", "Connection": "keep-alive"}
        if user:
            token = b64encode(f"{user}:{password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"
        self._conn = None
        self._cookie = ""

    def _connect(self):
        if self._conn is None:
            cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
            self._conn = cls(self.host, timeout=self.timeout)
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def request(self, method: str, path: str, params: dict | None = None,
                raw: bool = False) -> dict:
        target = self.prefix + path
        body = None
        headers = dict(self.headers)
        if self._cookie:
            headers["Cookie"] = self._cookie
        if params and method in ("PUT", "POST"):
            body = urllib.parse.urlencode(params)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif params:
            target += "?" + urllib.parse.urlencode(params)

        for attempt in (1, 2):
            try:
                conn = self._connect()
                conn.request(method, target, body=body, headers=headers)
                resp = conn.getresponse()
                text = resp.read().decode(errors="replace")
                break
            except (http.client.HTTPException, OSError) as e:
                # A keep-alive connection the server already closed fails on reuse.
                self.close()
                if attempt == 2:
                    raise RuntimeError(f"{method} {path} -> {e}") from None

        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie and not self._cookie:
            self._cookie = set_cookie.split(";", 1)[0]

        if resp.status >= 400:
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status}: {text[:300]}")
        if raw:
            return {"text": text, "status": resp.status}
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError:
            raise RuntimeError(f"{method} {path} -> non-JSON reply: {text[:200]}") from None
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"{method} {path} -> {data.get('message', 'rejected')}")
        return data


def devices_from_file(path: str) -> list[dict]:
    """Read the estate from a saved topology JSON instead of the running API.

    The node payload carries the same field names the API returns (name, device_type,
    vendor, model_name, datacenter/room/rack_*, psus), so the planner does not care
    which source it came from. Useful when the simulator is not running — and it is
    the honest source for a bulk load anyway: it is the on-disk topology of record,
    not a snapshot of live metrics.
    """
    with open(path, encoding="utf-8") as fh:
        topo = json.load(fh)
    out = []
    for node in topo.get("nodes", []):
        d = dict(node.get("device") or {})
        if d.get("name"):
            out.append(d)
    return out


class Sim:
    """The simulator's own API — the source of truth for the estate."""

    @staticmethod
    def login(url: str, user: str, password: str) -> Http:
        h = Http(url)
        conn = h._connect()
        conn.request("POST", "/api/auth/login",
                     body=json.dumps({"username": user, "password": password}),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        token = json.loads(resp.read().decode())["token"]
        h.headers["Authorization"] = f"Bearer {token}"
        return h


# ── planning ──────────────────────────────────────────────────────────────────

# fac_Cabinet.Location is varchar(20) and MySQL truncates silently — 'Mechanical Room
# R2-01' (21 chars) landed as 'Mechanical Room R2-0', and two rooms whose names share a
# 20-char prefix would collide onto one cabinet. Use the room CODE the simulator already
# encodes in every device name (CODE-DC-ROOM-Rrow-rack), which keeps a cabinet at 8-9
# chars whatever the room is called. The full room name goes in Notes.
ROOM_CODE_FALLBACK = {
    "Server Hall A": "HA", "Server Hall B": "HB", "Network Room": "NR",
    "Central Plant": "CP", "Mechanical Room": "MR", "UPS Room": "UR",
    "Generator Room": "GR", "Roof": "RF",
}


def room_code(dev: dict) -> str:
    """Short room token: from the device name when it carries one, else the room."""
    parts = str(dev.get("name", "")).split("-")
    if len(parts) >= 3 and 1 <= len(parts[2]) <= 3 and parts[2].isalpha():
        return parts[2].upper()
    room = dev.get("room", "") or ""
    if room in ROOM_CODE_FALLBACK:
        return ROOM_CODE_FALLBACK[room]
    return "".join(w[0] for w in room.split())[:3].upper() or "XX"


def cabinet_location(dev: dict) -> str:
    """Cabinet name, <= 20 chars. Zero-padded so openDCIM's Location sort is natural."""
    return (f"{room_code(dev)}-R{int(dev.get('rack_row') or 0)}"
            f"-{int(dev.get('rack_num') or 0):02d}")


def plan(devices: list[dict], only_dc: str = "",
         max_kw: float | None = None,
         max_weight: float = CABINET_MAX_WEIGHT) -> dict:
    """Group the estate into the objects openDCIM needs, in dependency order."""
    manufacturers: set[str] = set()
    templates: dict[str, dict] = {}
    cabinets: dict[tuple, dict] = {}
    dev_rows: list[dict] = []
    panels: list[dict] = []
    skipped: dict[str, int] = defaultdict(int)

    for d in devices:
        dt = d.get("device_type", "")
        dc = d.get("datacenter", "")
        if only_dc and dc != only_dc:
            continue
        if dt in SKIP_TYPES:
            skipped[dt] += 1
            continue

        if dt in PANEL_TYPES:
            panels.append(d)
            continue

        odt = DEVICE_TYPE_MAP.get(dt)
        if odt is None:
            skipped[dt] += 1
            continue

        if not dc or not d.get("rack_row") or not d.get("rack_num"):
            # No cabinet to hang it on. Rather than invent one, report it.
            skipped[f"{dt} (no rack position)"] += 1
            continue

        vendor = d.get("vendor") or "Unknown"
        model = d.get("model_name") or f"Generic {odt}"
        height = device_u_height(dt, model)
        # Template wattage is the NAMEPLATE, not a live reading: power_draw_w is the
        # SKU's rated IT load (present in the topology), power_watts is whatever the
        # box happened to be drawing this tick. A capacity plan built on the latter
        # under-counts every idle machine.
        watts = int(d.get("power_draw_w") or d.get("power_watts") or 0)
        manufacturers.add(vendor)
        prev = templates.get(model)
        if prev is None:
            templates[model] = {
                "Model": model, "Manufacturer": vendor, "DeviceType": odt,
                "Height": height,
                # Rounded to whole kg: fac_DeviceTemplate.Weight is an int column, and
                # a cabinet total is the sum of ~20 of these, so sub-kg precision on a
                # class estimate would be false accuracy.
                # Floored at 1: fac_DeviceTemplate.Weight is an int, and openDCIM
                # cannot tell "0 = weighs nothing" from "0 = nobody filled this in".
                # A 0.5 kg probe rounds to 0 and would read as the latter.
                "Weight": max(1, int(round(device_weight_kg(dt, model)))),
                "NumPorts": int(d.get("interface_count") or 0),
                "PSCount": len(d.get("psus") or []) or (0 if dt in ZERO_U_TYPES else 2),
                "Wattage": watts,
            }
        elif watts > prev["Wattage"]:
            prev["Wattage"] = watts

        loc = cabinet_location(d)
        cabinets.setdefault((dc, loc), {
            "DataCenter": dc, "Location": loc, "CabinetHeight": 42,
            "MaxKW": (max_kw if max_kw is not None
                      else CABINET_DESIGN_KW.get(d.get("room") or "",
                                                 CABINET_DESIGN_KW_DEFAULT)),
            "MaxWeight": max_weight,
            "Model": "", "Notes": f"{d.get('room', '')} row {d.get('rack_row')} rack {d.get('rack_num')}",
        })

        # PowerSupplyCount is what makes openDCIM materialise power ports
        # (PowerPorts::createPorts loops 1..PowerSupplyCount at device create). Without
        # it a device has nowhere to land a cord. For a rack PDU the "ports" are its
        # OUTLETS, because a cord is stored as load-device-port -> CDU-device-port.
        if dt == "pdu":
            # The live /api/devices payload does not serialise outlets[], only the
            # saved topology does. Falling back to a flat 24 gave every rack PDU a
            # 24-outlet strip whatever it really is — an AP8886 is 42, so a third of
            # it vanished, and getNumPorts() (which reads PowerSupplyCount when there
            # is no outlet-count OID) stopped the status walk at 24. Ask the SKU
            # catalog before inventing a number; 24 only survives as the last resort
            # for a PDU that is in neither.
            spec = PDU_OUTLET_CATALOG.get(d.get("model_name") or "")
            psu_count = (len(d.get("outlets") or [])
                         or (spec[0] + spec[1] if spec else 0)
                         or 24)
        elif dt in ZERO_U_TYPES:
            psu_count = 1
        else:
            psu_count = len(d.get("psus") or []) or 2

        dev_rows.append({
            "Label": d["name"], "DataCenter": dc, "Cabinet": loc, "Model": model,
            "DeviceType": odt, "PowerSupplyCount": psu_count,
            # A zero-U PDU or sensor is NOT at U0 of the face: openDCIM treats
            # Position 0 as "not mounted in a U", which is exactly right for a
            # side-rail PDU and a rail-clipped probe.
            "Position": 0 if dt in ZERO_U_TYPES else int(d.get("rack_unit") or 0),
            "Height": height,
            # openDCIM totals a cabinet's load with SUM(Weight) FROM fac_Device — the
            # DEVICE's own column, not its template's. Setting the template alone
            # leaves every already-imported device at 0 and the cabinet reading 0%.
            "Weight": max(1, int(round(device_weight_kg(dt, model)))),
            # The SNMP index the first outlet / first switch port answers on.
            # fac_Device.FirstPortNum has NO column default, so a device created
            # through the API lands on 0 — and getPortStatus() only starts recording
            # when the walk index EQUALS FirstPortNum, so a 0 there silently returns
            # an empty status list and every Status light stays 'err'. The simulator
            # numbers outlets and ifIndexes from 1.
            "FirstPortNum": 1 if odt in ("CDU", "Switch") else 0,
            "PrimaryIP": d.get("mgmt_ip") or d.get("ip_address") or "",
            # THE COMMUNITY IS THE DEVICE'S IP, not "public". This estate is served
            # by one snmpsim process on a single wildcard endpoint (0.0.0.0:161) —
            # hundreds of --agent-udpv4-endpoint flags would blow the Windows 32 KB
            # command line — so the listener cannot tell devices apart by destination
            # address. It resolves the .snmprec by COMMUNITY STRING instead, and every
            # generated dataset is keyed to the device IP: community "10.50.0.24" ->
            # datasets/snmp/10.50.0.24.snmprec (simulator/snmpsim_controller.py,
            # _build_command). A community of "public" matches no dataset and the
            # request is dropped with no response at all, which reads as an
            # unreachable device rather than an auth failure.
            #
            # This is a SIMULATOR artifact, not how a real estate is credentialled —
            # on real gear the community is a shared secret and using the management
            # address as one would be a finding in any audit. It is what this
            # transport requires, so it is what the exporter writes.
            "SNMPCommunity": ((d.get("mgmt_ip") or d.get("ip_address") or "")
                              if d.get("snmp_agent", True) else ""),
            "Ports": int(d.get("interface_count") or 0),
            # NAMEPLATE, not a live sample — the same precedence the template above
            # uses, and for the same reason. openDCIM shows two power figures per
            # cabinet: Computed Watts sums this column, Measured Watts sums what the
            # PDUs actually report over SNMP. Filling this from power_watts made both
            # of them the same measurement and threw away the comparison. It also
            # under-read every rack: a switch idling at 46 W was costed at 46 W
            # against a 250 W SKU, so the Network Room's racks totalled 0.37 kW.
            "NominalWatts": int(d.get("power_draw_w") or d.get("power_watts") or 0),
        })

    panel_rows = []
    for d in sorted(panels, key=lambda x: PANEL_ORDER.index(x["device_type"])
                    if x["device_type"] in PANEL_ORDER else 99):
        panel_rows.append({
            "PanelLabel": d["name"], "DataCenter": d.get("datacenter", ""),
            "DeviceType": d["device_type"],
            # A 42-pole branch panelboard is physically two columns — odds down the
            # left, evens down the right — and openDCIM only spans a multi-pole
            # breaker across same-parity positions when it is told so. Left at the
            # schema default ("Sequential") a 3-pole breaker is drawn across three
            # CONSECUTIVE poles, which is not how one is installed. Switchgear, ATS
            # and UPS entries have no poles to schedule and stay Sequential.
            "NumberScheme": ("Odd/Even"
                             if PANEL_POLES.get(d["device_type"], 3) > 3
                             else "Sequential"),
            "PanelVoltage": PANEL_VOLTAGE.get(d["device_type"], 480),
            "NumberOfPoles": PANEL_POLES.get(d["device_type"], 3),
            # The board's OWN address, which is right only where the meter is
            # integral to the board (a PM5000 in an MPP door, a Digitrip trip unit).
            # An RPP's meter is a SEPARATE device and this leaves it blank —
            # plan_panel_meters() replaces it with the meter's address.
            "PanelIPAddress": d.get("mgmt_ip") or d.get("ip_address") or "",
            "MainBreakerSize": panel_main_breaker_a(
                d.get("model_name") or "",
                PANEL_VOLTAGE.get(d["device_type"], 480)),
            # Report-only, for plan_panel_meters: the board's own make/model, used
            # where the metering instrument IS the board (the UPS).
            "_vendor": d.get("vendor") or "",
            "_model": d.get("model_name") or "",
            # Filled by plan_panel_meters() — the fac_CDUTemplate the panel points at.
            "_meter_model": "",
        })

    return {
        "manufacturers": sorted(manufacturers),
        "templates": templates,
        "cabinets": cabinets,
        "devices": dev_rows,
        "panels": panel_rows,
        "skipped": dict(skipped),
        "cords": [],
        "panel_feeds": {},
        "breakers": [],
        "port_links": [],
        "cdu_templates": plan_cdu_templates(devices, only_dc),
        "sensor_templates": plan_sensor_templates(devices, only_dc),
        "panel_meters": {},
    }


# A branch panelboard has poles; a switchboard, ATS, UPS or genset does not — its
# "poles" here are just the three phases of its main. Getting this wrong makes
# openDCIM's panel schedule offer 3 breaker positions on a 42-pole RPP.
PANEL_POLES = {"rpp": 42, "mpp": 42}


def panel_main_breaker_a(model_name: str, voltage: int) -> int:
    """The board's main breaker rating, in amps, read off the SKU name.

    Every piece of distribution gear in this estate names its rating: "APC Galaxy RPP
    125A", "Eaton Magnum DS 4000A", "Eaton Freedom 2100 MCC 1600A". A UPS names kVA
    instead ("Vertiv Liebert EXL S1 1200kVA"), which converts at the panel's own
    voltage — three-phase, so I = VA / (V * sqrt(3)).

    Parsed rather than invented, and 0 when the name says nothing: openDCIM treats 0
    as "not set" and simply omits the check, which is the honest outcome for a
    Caterpillar 3516B or an ION9000 meter that carries no breaker rating in its name.

    NOTE the sum of a panel's BRANCH breakers legitimately exceeds its main — that is
    diversity, not an error. On RPPA-DC1-HA-R1-04 the branch ratings total ~190 A per
    phase against a 125 A main, while the measured draw is nearer 62 A. Sizing the
    main to the sum of branches would oversize every board in the estate.
    """
    if not model_name:
        return 0
    m = re.search(r"(\d+)\s*kVA(?![A-Za-z])", model_name, re.I)
    if m and voltage > 0:
        return int(round(int(m.group(1)) * 1000 / (voltage * math.sqrt(3))))
    m = re.search(r"(\d+)\s*A(?![A-Za-z])", model_name)
    return int(m.group(1)) if m else 0


# ── panel meters ──────────────────────────────────────────────────────────────
#
# A panelboard is not itself an instrument. What reports it is a meter, and WHERE
# that meter lives differs by board — which is the whole reason this table exists
# rather than a single "use the device's own IP" rule:
#
#   integral to the board   A switchboard, MCC or MPP is ordered WITH its metering:
#                           an Eaton Magnum's Digitrip trip unit, a Power Xpert on
#                           the MCC main, a PowerLogic PM5000 in the MPP door. One
#                           asset, one address — the board's own.
#   the board IS the meter  A UPS reports its own output over UPS-MIB. Same thing.
#   a SEPARATE device       An RPP is a passive breaker board. Its branch metering
#                           is a bolt-on CT strip + controller (Verdigris, Packet
#                           Power, Veris E30, Server Tech) with its own network
#                           address, and in this estate it is modelled as exactly
#                           that: an `energy_monitor` device fed by the RPP. The
#                           RPP therefore has NO management address of its own, and
#                           filling PanelIPAddress from the board would leave it
#                           blank forever — which is what it was doing.
#   nothing meters it       An ATS is a switch: position, source availability,
#                           voltages, no kW (the sim's ASCO subtree has no current
#                           object at all). A genset controller reports kW but no
#                           per-phase current. Both get NO template, deliberately —
#                           see the note on absent SKUs in SENSOR_SIM_TEMPLATE for
#                           why a blank-OID row is worse than no row.
#
# READ THIS BEFORE EXPANDING IT: openDCIM 23.04 never polls a panel. PanelIPAddress
# appears in exactly two places in the tree (PowerPanel.class.php and the
# power_panel.php form) and fac_PowerPanel is touched by no poller, so neither this
# address nor the template it points at will ever produce a reading in openDCIM.
# They are the record of WHICH instrument reports the board and WHERE it answers —
# which is what an operator needs to go get the number, and what an external poller
# (deploy/opendcim.cron drives the CDU and sensor ones) would read to fetch it.
# openDCIM's own field labels say the same: "Panel Meter IP Address", not "panel
# load".
#
# The OIDs are the SIMULATOR's private electrical subtree (99999.8-12), not a real
# meter's MIB. Replace them per SKU before pointing openDCIM at real gear.
_UTIL_ENT, _SWGR_ENT = "1.3.6.1.4.1.99999.8", "1.3.6.1.4.1.99999.9"
_MCC_ENT, _MPP_ENT = "1.3.6.1.4.1.99999.11", "1.3.6.1.4.1.99999.12"

# Convert3PhAmperes is the profile for every one of these boards, and it is the only
# correct one: openDCIM computes avg(I1,I2,I3)/Multiplier * sqrt(3) * Voltage
# (PowerDistribution::UpdateStats), which is the three-phase power formula against a
# line-to-line voltage. Combine3OIDAmperes — which SUMS the three and multiplies by
# V — would over-read a balanced board by sqrt(3), i.e. 73%.
#
# WHAT IT PRODUCES IS kVA, NOT kW. That formula has no power-factor term anywhere in
# it, so the figure is apparent power — on a mechanical board carrying VFD motors at
# PF 0.78 it reads ~28% above the kW the board's own meter publishes. There is no
# openDCIM profile that takes a PF, and pre-dividing the Voltage to fake one would
# corrupt the same field its capacity arithmetic reads. Take the amps as amps; for
# real power, read the board's own kW object.
#
# NOT SingleOIDWatts against the kW object each of these also serves: openDCIM's
# watts = value / Multiplier and Multiplier is validated against
# {0.01,0.1,1,10,100}, so kW -> W (x1000) is not expressible. A kW register read as
# watts under-reports a 400 kW board as 400 W.
PANEL_METER_SPEC = {
    "utility_feed": {
        "vendor": "Schneider Electric", "model": "Schneider PowerLogic ION9000",
        "profile": "Convert3PhAmperes", "multiplier": "1",
        "oids": (f"{_UTIL_ENT}.11.0", f"{_UTIL_ENT}.12.0", f"{_UTIL_ENT}.13.0"),
        "note": "service-entrance revenue meter (per-phase line current, amps)",
    },
    "switchgear": {
        "vendor": "Eaton", "model": "Eaton Digitrip 1150 Trip Unit",
        "profile": "Convert3PhAmperes", "multiplier": "1",
        "oids": (f"{_SWGR_ENT}.11.0", f"{_SWGR_ENT}.12.0", f"{_SWGR_ENT}.13.0"),
        "note": "energy-metering trip unit on the main breaker",
    },
    "mcc": {
        "vendor": "Eaton", "model": "Eaton Power Xpert PXM 2000",
        "profile": "Convert3PhAmperes", "multiplier": "1",
        "oids": (f"{_MCC_ENT}.11.0", f"{_MCC_ENT}.12.0", f"{_MCC_ENT}.13.0"),
        "note": "metered MCC main",
    },
    "mpp": {
        "vendor": "Schneider Electric", "model": "Schneider PowerLogic PM5000",
        "profile": "Convert3PhAmperes", "multiplier": "1",
        # NOTE the MPP subtree numbers its per-phase currents .10/.11/.12, not
        # .11/.12/.13 like the other three — it carries no separate system-current
        # object, so everything after .6 shifts down one.
        "oids": (f"{_MPP_ENT}.10.0", f"{_MPP_ENT}.11.0", f"{_MPP_ENT}.12.0"),
        "note": "panel-main meter in the MPP door",
    },
    # The UPS meters itself. upsOutputPower is a live WATTS register in this estate
    # (the generator patches it from ups_output_kw every tick), so SingleOIDWatts
    # with no multiplier is exact — no phase arithmetic, no assumed voltage.
    # vendor/model are left out on purpose: they come from the UPS device itself,
    # because here the instrument and the asset are the same box.
    "ups": {
        "vendor": "", "model": "",
        "profile": "SingleOIDWatts", "multiplier": "1",
        "oids": ("1.3.6.1.2.1.33.1.4.4.1.4.1", "", ""),
        "note": "UPS-MIB upsOutputPower, watts",
    },
}

# An RPP's meter is whatever `energy_monitor` the topology hangs off it, so its make
# and model are read from that device rather than declared here. It speaks BACnet/IP
# in this estate (core/bacnet_ev2_generator.py), NOT SNMP — so the template carries
# no OIDs and Managed=0. Writing SNMP objects for it would be inventing a transport
# the device does not have.
PANEL_METER_SNMP_TYPES = frozenset(PANEL_METER_SPEC)


def plan_panel_meters(graph: dict, devices: list[dict], p: dict) -> dict:
    """Point every panel at the instrument that actually reports it.

    Sets each panel row's PanelIPAddress and _meter_model, and returns
    model -> fac_CDUTemplate row for the meters in use. The templates are
    registered in p["templates"] as DeviceType 'CDU' because that is the table
    openDCIM's panel record points at ("CDU/Meter Template") — creating one makes
    DeviceTemplate::CreateTemplate materialise the fac_CDUTemplate shadow row that
    post_import_sql then fills.
    """
    name = {d["id"]: d["name"] for d in graph.get("devices", [])}
    typ = {d["id"]: d.get("device_type", "") for d in graph.get("devices", [])}
    # The graph's node payload carries id/name/device_type and nothing else on the
    # --from-file path, so make/model/address come from the device list.
    dev = {d["name"]: d for d in devices}

    # RPP -> its branch meter, off the power layer. Direction is not assumed: the
    # meter is drawn as a load of the board it measures, but a CT strip is not a
    # load in any electrical sense, so accept the pairing either way round.
    rpp_meter: dict[str, str] = {}
    for link in graph.get("links", []):
        if link.get("layer") != "power":
            continue
        a = link.get("supply_node") or link.get("src_id")
        b = link.get("load_node") or link.get("dst_id")
        for x, y in ((a, b), (b, a)):
            if typ.get(x) == "rpp" and typ.get(y) == "energy_monitor":
                rpp_meter.setdefault(name.get(x, ""), name.get(y, ""))

    meters: dict[str, dict] = {}
    for row in p["panels"]:
        dt = row["DeviceType"]
        if dt == "rpp":
            meter = dev.get(rpp_meter.get(row["PanelLabel"], ""))
            if not meter:
                continue
            model = meter.get("model_name") or ""
            vendor = meter.get("vendor") or "Unknown"
            row["PanelIPAddress"] = (meter.get("mgmt_ip")
                                     or meter.get("ip_address") or "")
            spec = {"profile": "SingleOIDWatts", "multiplier": "1",
                    "oids": ("", "", ""),
                    "note": "branch-circuit meter, BACnet/IP — no SNMP objects"}
            managed = 0
        else:
            spec = PANEL_METER_SPEC.get(dt)
            if not spec:
                continue                      # ATS, genset: no metering point
            model = spec["model"] or row["_model"]
            vendor = spec["vendor"] or row["_vendor"] or "Unknown"
            managed = 1
        # From the PANEL, never a constant in the spec table: this is the voltage
        # openDCIM multiplies the meter's amps by, so it has to be the bus the meter
        # is actually clamped to. A 415 V RPP and a 400 V MCC cannot share one number,
        # and a template hardcoding 480 silently over-read both.
        voltage = int(row["PanelVoltage"])
        if not model:
            continue
        row["_meter_model"] = model
        row_meter = meters.setdefault(model, {
            "Model": model, "Manufacturer": vendor, "Managed": managed,
            "ProcessingProfile": spec["profile"], "Multiplier": spec["multiplier"],
            "Voltage": voltage, "OIDs": spec["oids"],
            "Note": spec["note"], "Panels": [], "VoltageConflict": set(),
        })
        row_meter["Panels"].append(row["PanelLabel"])
        # openDCIM stores the voltage on the TEMPLATE, not per panel, so one SKU
        # metering two different buses can only carry one of them. Record the clash
        # rather than let the first panel seen decide it silently.
        if voltage != row_meter["Voltage"]:
            row_meter["VoltageConflict"].add(voltage)

    # Register the templates so apply() creates them (and the manufacturers they
    # need — a CDUTemplate with no matching fac_Manufacturer row is invisible:
    # CDUTemplate::GetTemplateList inner-joins it, so the panel's dropdown would
    # not even offer the meter).
    mfg = set(p["manufacturers"])
    for model, m in meters.items():
        mfg.add(m["Manufacturer"])
        p["templates"].setdefault(model, {
            "Model": model, "Manufacturer": m["Manufacturer"], "DeviceType": "CDU",
            # Zero-U, and never mounted: these templates exist to carry the meter's
            # OIDs for a panel, not to be racked. Weight is floored at 1 for the
            # same reason as every other template here (0 reads as "unfilled").
            "Height": 0, "Weight": 1, "NumPorts": 1, "PSCount": 0,
            # Wattage is a NAMEPLATE for cabinet capacity sums. No device carries
            # this template, so any figure here would only ever be wrong; 0 is the
            # honest "not applicable".
            "Wattage": 0,
        })
    p["manufacturers"] = sorted(mfg)
    return meters


def plan_panel_feeds(graph: dict, panel_names: set) -> dict:
    """name -> {parent, alternates} from the topology's real panel-to-panel feeds.

    openDCIM carries ONE ParentPanelID, but a transfer switch has two sources by
    definition (normal + emergency), and a paralleling bus has one per genset. Rather
    than drop the second source or invent a second parent field, the parent is the
    NORMAL source — the convention a panel schedule follows — and the alternates go
    into ParentBreakerName, which openDCIM already treats as free text precisely
    because switchgear feeds are not numbered.

    Normal source = first by (feed-order, name): utility-fed SWGR1 beats the
    generator bus SWGR2, and GEN1 beats GEN2. Deterministic, so re-runs do not
    reshuffle the tree.
    """
    name = {d["id"]: d["name"] for d in graph.get("devices", [])}
    typ = {d["id"]: d.get("device_type", "") for d in graph.get("devices", [])}
    parents: dict[str, list[tuple]] = defaultdict(list)
    for link in graph.get("links", []):
        if link.get("layer") != "power":
            continue
        s = link.get("supply_node") or link.get("src_id")
        d = link.get("load_node") or link.get("dst_id")
        sn, dn = name.get(s, ""), name.get(d, "")
        if sn in panel_names and dn in panel_names:
            order = PANEL_ORDER.index(typ.get(s, "")) if typ.get(s) in PANEL_ORDER else 99
            parents[dn].append((order, sn))

    feeds = {}
    for child, cands in parents.items():
        ranked = [n for _, n in sorted(set(cands))]
        feeds[child] = {"parent": ranked[0], "alternates": ranked[1:]}
    return feeds


# Rack-PDU feed breaker. 32 A is this estate's rack breaker (415/240 V distribution);
# openDCIM stores it on the CDU record, not the panel.
RACK_BREAKER_A = 32


def pdu_breaker_amps(devices: list[dict]) -> dict:
    """PDU name -> the trip rating of its feed breaker, from the SKU.

    A rack PDU's breaker is sized to the strip, not to a house standard: a 30 A
    unit is fed by a 30 A breaker. RACK_BREAKER_A stays as the fallback for a SKU
    the catalog does not know, because inventing a rating is worse than inheriting
    the estate's common one — but a 30 A strip declared at 32 A overstates its
    ceiling by 7% on every capacity report that reads InputAmperage.
    """
    out = {}
    for d in devices:
        if d.get("device_type") != "pdu":
            continue
        spec = PDU_OUTLET_CATALOG.get(d.get("model_name") or "")
        out[d["name"]] = spec[3] if spec else RACK_BREAKER_A
    return out


def plan_breakers(graph: dict, pdu_phases: dict, panel_names: set,
                  pdu_amps: dict | None = None) -> list[dict]:
    """Assign each rack PDU a breaker position on its RPP: A feeds odd, B feeds even.

    Panelboard poles run 1,3,5... down the left column and 2,4,6... down the right, so
    "odd for the A feed, even for the B feed" puts each redundant side in its own
    column — which is how a dual-fed row is actually built, and it makes a mis-fed
    cabinet visible at a glance on the panel schedule.

    A three-phase PDU takes three consecutive SAME-PARITY poles (1,3,5), the way a
    3-pole breaker actually straddles a panelboard's phase rotation. Single-phase
    takes one.

    The simulator does not model breaker positions, so these numbers are ASSIGNED
    here, deterministically (sorted by PDU name) rather than discovered. They are a
    consistent scheme, not a claim about the real panel.
    """
    name = {d["id"]: d["name"] for d in graph.get("devices", [])}
    typ = {d["id"]: d.get("device_type", "") for d in graph.get("devices", [])}
    fed: dict[str, list[str]] = defaultdict(list)
    for link in graph.get("links", []):
        if link.get("layer") != "power":
            continue
        s = link.get("supply_node") or link.get("src_id")
        d = link.get("load_node") or link.get("dst_id")
        if typ.get(s) == "rpp" and typ.get(d) == "pdu" and name.get(s) in panel_names:
            fed[name[s]].append(name[d])

    rows = []
    for panel in sorted(fed):
        taken: set = set()
        for pdu in sorted(set(fed[panel])):
            poles_needed = 3 if pdu_phases.get(pdu, 1) == 3 else 1
            # A 3-pole breaker straddles the phase rotation, so on an Odd/Even
            # panelboard it occupies three SAME-PARITY positions (7,9,11) — not three
            # consecutive ones. A 1-pole takes the next free position in either
            # column. Both columns get filled: a panelboard's two columns are
            # phase-ordered positions, NOT the A and B feeds. Every RPP in this
            # estate carries one side only (RPPA feeds eight PDUAs and no PDUBs), so
            # the old "A odd, B even" rule left the entire even column of every panel
            # empty and could not have been read off a real schedule.
            start = None
            for cand in range(1, PANEL_POLES.get("rpp", 42) + 1):
                span = [cand + 2 * i for i in range(poles_needed)]
                if span[-1] <= PANEL_POLES.get("rpp", 42) and not (set(span) & taken):
                    start = cand
                    break
            if start is None:               # panel full — report, do not wrap around
                rows.append({"pdu": pdu, "panel": panel, "poles": [],
                             "phases": poles_needed,
                             "breaker_a": (pdu_amps or {}).get(pdu, RACK_BREAKER_A),
                             "overflow": True})
                continue
            poles = [start + 2 * i for i in range(poles_needed)]
            taken.update(poles)
            rows.append({
                "pdu": pdu, "panel": panel, "poles": poles,
                # openDCIM stores only the FIRST pole and derives the rest itself:
                # getPanelSchedule() walks Pole, Pole+adder, ... BreakerSize times,
                # with adder 2 on an Odd/Even panel. Writing the whole list into
                # PanelPole made it a STRING key ("7,9,11"), so the integer lookup
                # for pole 7 missed and every 3-pole PDU rendered as a blank row.
                "first_pole": poles[0],
                "phases": poles_needed,
                "breaker_a": (pdu_amps or {}).get(pdu, RACK_BREAKER_A),
                "overflow": False,
            })
    return rows


# ── rack-PDU (CDU) SNMP template ──────────────────────────────────────────────
#
# A rack PDU's SNMP definition lives in fac_CDUTemplate, NOT fac_DeviceTemplate.
# openDCIM keeps it as a SHADOW row sharing the device template's TemplateID:
# DeviceTemplate::CreateTemplate() auto-creates one whenever DeviceType is CDU, but
# every OID column comes out EMPTY, because the REST route (PUT /devicetemplate)
# copies only properties declared on the DeviceTemplate class and the OID columns are
# declared on CDUTemplate. The row therefore exists and is useless — which is why the
# Status column on a CDU's Power Connections panel never leaves 'err':
# CDUInfo::getPortStatus() reads OutletStatusOID off that row and bails on a miss.
#
# There is no API route for fac_CDUTemplate (get/put/postRoutes cover device, cabinet,
# devicetemplate, manufacturer, powerpanel, powerport, pdustats and nothing else), so
# this is emitted as reviewable SQL, the same as the breaker assignments.
#
# THE OIDS BELOW ARE THIS SIMULATOR'S, NOT A REAL PDU'S. In a real install a CDU
# template carries the SKU's own MIB objects — APC rPDU2OutletSwitchedStatusState
# (1.3.6.1.4.1.318.1.1.26.9.2.3.1.5), Raritan PDU2-MIB outletSensorState
# (1.3.6.1.4.1.13742.6.5.4.3.1.3), ServerTech Sentry3 outletStatus
# (1.3.6.1.4.1.1718.3.2.3.1.5) — each with its own ON enum, which is NOT the same
# integer across vendors and is deliberately not guessed here. The estate answers
# per-outlet state only on its private table (core/snmprec_generator.py,
# _PDU_OUTLET_ENT / _PDU_ENT), because the vendor per-outlet tables it publishes are
# seeded at index .1 alone, so a real vendor OID would miss on outlet 2 and openDCIM
# stops walking at the first miss.
CDU_SIM_PROFILE = {
    #  ...5.20.1.2.<n>  outlet name   (DisplayString: the device the cord feeds)
    "OutletNameOID":   "1.3.6.1.4.1.99999.5.20.1.2",
    "OutletDescOID":   "1.3.6.1.4.1.99999.5.20.1.2",
    #  ...5.20.1.3.<n>  outlet state  (1 = on, 2 = off) — the Status column
    "OutletStatusOID": "1.3.6.1.4.1.99999.5.20.1.3",
    "OutletStatusOn":  "1",
    # No outlet-count OID is served. Empty ON PURPOSE: CDUInfo::getNumPorts() then
    # falls back to the device's PowerSupplyCount, which this exporter already sets
    # to the SKU's real outlet count.
    "OutletCountOID":  "",
    # sysDescr. openDCIM calls GetSmartCDUVersion() on EVERY poll cycle, and an
    # empty VersionOID makes it log "Could not perform walk for OID " once per PDU —
    # 80 lines every five minutes, ~1.7 MB of noise a day, in which a real failure
    # would be invisible. No PDU here serves a dedicated firmware object (APC's
    # rPDU2IdentFirmwareRev is absent), but sysDescr carries the revision in its
    # text: "APC Rack PDU 2G ... NMC3 fw v1.4.2". Pointing at the object that
    # actually exists beats polling one that does not.
    "VersionOID":      "1.3.6.1.2.1.1.1.0",
    # OID1 (the wattage read) is set PER VENDOR by CDU_POWER_OID below, not here.
    # It used to point at the private scalar 1.3.6.1.4.1.99999.5.11.0 on the claim
    # that it was published for every PDU whatever the vendor. It is not: that
    # fallback block is only reached for a vendor the generator has no mapping for,
    # and an APC or Raritan PDU serves ONLY its own MIB plus the private per-outlet
    # table. openDCIM's poller therefore logged "Could not perform walk" for all 80
    # PDUs and wrote 0 W to every one of them — a silent zeroing, because a failed
    # poll is stored as a reading rather than skipped.
    "OID1":            "",
    "OID2":            "",
    "OID3":            "",
    "ProcessingProfile": "SingleOIDWatts",
    "Multiplier":      "1",          # overridden per vendor, see CDU_POWER_OID

    "ATSStatusOID":    "",
    "ATSDesiredResult": "",
}


# Where each PDU vendor publishes its total real power, and the divisor that turns
# that reading into WATTS for openDCIM's SingleOIDWatts profile (watts = value /
# Multiplier). Verified by walking a live agent of each type rather than read off a
# MIB: APC answers 613 on a 6130 W strip (rPDU2DeviceStatusPower is hundredths of a
# kW, so 10 W per count), Raritan answers 180 on a 180 W strip (PDU2-MIB inlet
# activePower is already watts).
#
# A vendor absent from this map gets an EMPTY OID1, which makes openDCIM skip the
# wattage poll entirely. That is deliberate: a missing reading leaves the previous
# value alone, while a failing one is written as 0 and looks like an idle rack.
CDU_POWER_OID = {
    "apc":     ("1.3.6.1.4.1.318.1.1.26.4.3.1.5.1", "0.1"),
    "raritan": ("1.3.6.1.4.1.13742.6.5.2.3.1.4.1.1.5", "1"),
}


def plan_cdu_templates(devices: list[dict], only_dc: str = "") -> dict:
    """model -> the fac_CDUTemplate row for that rack-PDU SKU.

    Voltage/Amperage are the SKU's INPUT nameplate, which is what openDCIM multiplies
    for the strip's capacity. Note that it multiplies them flat: on a 3-phase SKU the
    real capacity is V x A x sqrt(3), so an AP8886 (400 V, 32 A, 22 kW) reads 12.8 kW
    there. That is openDCIM's model, not an error in the data — do not "fix" it by
    inflating the voltage, which would misreport what the outlets deliver.
    """
    rows: dict[str, dict] = {}
    for d in devices:
        if d.get("device_type") != "pdu":
            continue
        if only_dc and d.get("datacenter", "") != only_dc:
            continue
        model = d.get("model_name") or ""
        if not model or model in rows:
            continue
        spec = PDU_OUTLET_CATALOG.get(model)
        _vkey = str(d.get("vendor") or model).split()[0].lower()
        _cdu_power = CDU_POWER_OID.get(_vkey, ("", "1"))
        n_outlets = len(d.get("outlets") or []) or (spec[0] + spec[1] if spec else 0)
        rows[model] = {
            **CDU_SIM_PROFILE,
            "Model": model,
            # Managed=1 is not cosmetic: PowerDistribution::UpdateStats() joins on
            # `b.Managed=true`, so an unmanaged template is never polled at all.
            "Managed": 1,
            "ATS": 0,
            "SNMPVersion": "2c",
            "OID1": _cdu_power[0], "Multiplier": _cdu_power[1],
            "Voltage": spec[4] if spec else 230,
            "Amperage": spec[3] if spec else 0,
            "NumOutlets": n_outlets,
            # Carried for the report only — what a load actually sees at the outlet.
            "_outlet_v": outlet_voltage(model),
            "_known_sku": bool(spec),
        }
    return rows


# ── environmental sensors ─────────────────────────────────────────────────────

# openDCIM's sensor model is ONE air temperature and ONE relative humidity per
# device (fac_SensorTemplate has exactly two OID columns). A probe head that carries
# more than that has to be reduced to those two, and WHICH point is picked is the
# whole decision — so it is made explicitly here, per SKU, rather than by taking
# whatever answers first.
#
# INLET, always, for the temperature. ASHRAE TC9.9 defines the thermal envelope at
# the EQUIPMENT INLET, openDCIM alerts on TemperatureRed/TemperatureYellow against
# this one value, and its DataCenter average temperature (DataCenter.class.php,
# AvgTemp) filters on BackSide=0 — i.e. it already assumes the number it is given is
# a front-of-rack reading. Handing it an exhaust temperature would put a healthy rack
# permanently in alarm.
#
# The OIDs are the SIMULATOR's agents, verified by GET against a live device rather
# than read off a MIB (10.52.11.43 answered 219/272/335/511 on slots 1-4 = inlet
# 21.9 C, mid 27.2 C, exhaust 33.5 C, RH 51.1%). Replace them per SKU before pointing
# openDCIM at real gear.
#
# A model that is NOT in this map deliberately gets no fac_SensorTemplate row at all,
# which makes Device::UpdateSensorsFilter() call GetTemplate(), get false and
# `continue`. That is the quiet outcome: no reading is recorded. The alternative — a
# row with blank OIDs, which is what openDCIM auto-creates — is worse, because the
# poller then skips the SNMP but still writes Temperature=0, and a fabricated 0.00 C
# is indistinguishable in the UI from a real one.
_RARITAN_EXT_SENSOR = "1.3.6.1.4.1.13742.6.5.5.3.1.4.1"   # externalSensorValue.<slot>

SENSOR_SIM_TEMPLATE = {
    # T3H1: slot 1 inlet, slot 2 mid-rack, slot 3 exhaust, slot 4 humidity.
    "Raritan DPX2-T3H1": {
        "TemperatureOID": f"{_RARITAN_EXT_SENSOR}.1",
        "HumidityOID":    f"{_RARITAN_EXT_SENSOR}.4",
        "TempMultiplier":     0.1,     # served as tenths of a degree
        "HumidityMultiplier": 0.1,     # served as tenths of a percent
    },
    # CC2: slot 1 is a WATER-DETECTION rope (sensorType 28, value 0=dry / 1=wet) and
    # slot 2 is the temperature probe. There is no hygrometer on this head.
    #
    # Pointing TemperatureOID at slot 1 is the trap here — it is the first slot, it
    # answers, and it answers 0. That would publish a steady 0.0 C on fifteen racks:
    # a plausible-looking cold reading that is really a "no leak" flag, and one that
    # AVG(NULLIF(Temperature,0)) would silently drop from the datacenter average
    # rather than flag. Slot 2 is the only real temperature on this SKU.
    "Raritan DPX2-CC2": {
        "TemperatureOID": f"{_RARITAN_EXT_SENSOR}.2",
        "HumidityOID":    "",          # none on this head — leave it unpolled
        "TempMultiplier":     0.1,
        "HumidityMultiplier": 1,
    },
}

# Celsius. openDCIM converts on read if the install's global mUnits disagrees
# (Device.class.php), so the honest thing is to declare what the agent actually
# serves and let it convert, not to pre-convert here.
SENSOR_UNITS = "metric"

# Chiller-plant header instruments — a thermowell in a water header, or a magnetic
# flow meter on the main. They are modelled as SENSOR devices because that is what
# they are on the wire, but they are NOT openDCIM environmental sensors and are
# deliberately left unwired:
#
#   * openDCIM has two fields, air temperature and relative humidity. A flow meter
#     in litres/second maps to neither; forcing it into TemperatureOID would publish
#     l/s as degrees.
#   * Even the genuine water temperatures do not belong in this table. 7 C chilled
#     water supply and 35 C condenser return are healthy plant readings, and
#     DataCenter::AvgTemp averages every non-backside sensor in the datacenter with
#     no room or type filter — mixing them with rack inlets makes that number mean
#     nothing, and TemperatureRed would alarm on the condenser loop forever.
#
# In a real estate these points come off the BMS over BACnet/Modbus and live in the
# BMS historian, which is the same reason this exporter already skips chillers,
# towers and pumps entirely. The SQL below removes the blank shadow row openDCIM
# auto-creates for them so the poller skips them outright.
SENSOR_PLANT_PREFIX = "Plant "


def plan_sensor_templates(devices: list[dict], only_dc: str = "") -> dict:
    """model -> the fac_SensorTemplate row for that environmental-sensor SKU.

    Plant header instruments and unmapped SKUs are returned too, flagged, because the
    SQL has to act on them (delete the shadow row / report them) rather than ignore
    them.
    """
    rows: dict[str, dict] = {}
    for d in devices:
        if d.get("device_type") != "sensor":
            continue
        if only_dc and d.get("datacenter", "") != only_dc:
            continue
        model = d.get("model_name") or ""
        if not model:
            continue
        if model not in rows:
            spec = SENSOR_SIM_TEMPLATE.get(model)
            rows[model] = {
                **(spec or {"TemperatureOID": "", "HumidityOID": "",
                            "TempMultiplier": 1, "HumidityMultiplier": 1}),
                "Model": model,
                "mUnits": SENSOR_UNITS,
                "_plant": model.startswith(SENSOR_PLANT_PREFIX),
                "_known_sku": bool(spec),
                "_count": 0,
            }
        rows[model]["_count"] += 1
    return rows


def sensor_template_sql(rows: dict) -> list[str]:
    """fac_SensorTemplate. No API route reaches it, same wall as fac_CDUTemplate.

    Two statements per wired SKU, mirroring the CDU block: the INSERT IGNORE repairs
    an estate whose templates predate this (normally openDCIM has already made the
    blank shadow row and it is a no-op), and the UPDATE is what fills the OIDs.
    """
    out = [
        "-- Environmental sensor SNMP templates -> fac_SensorTemplate.",
        "-- openDCIM auto-creates a BLANK shadow row beside every Sensor device",
        "-- template (DeviceTemplate.class.php) and no REST route can fill it, so",
        "-- until this runs poll_temperature_sensors.php reads a template with empty",
        "-- OIDs, skips the SNMP and writes Temperature=0 -- which renders as a real",
        "-- 0.00 reading in the UI. See SENSOR_SIM_TEMPLATE in",
        "-- tools/export_to_opendcim.py for why each OID is the one it is.",
        "",
        "-- Display units. The estate is Celsius end to end -- the agents serve",
        "-- tenths of a degree C and the templates above declare mUnits='metric' --",
        "-- so openDCIM has to agree or it converts on every read.",
        "--",
        "-- This is NOT cosmetic. TemperatureRed/TemperatureYellow are compared",
        "-- against the value AFTER conversion (Device.class.php,",
        "-- UpdateSensorsFilter), and they are shipped as 30/25, which are Celsius",
        "-- numbers. Left on 'english' a healthy 22 C rack reads 71.8 F, clears a",
        "-- 'red' of 30, and EVERY sensor in the estate is permanently critical --",
        "-- silently, because it only surfaces once SensorAlertsEmail is enabled.",
        "--",
        "-- Changing this does NOT fix rows already stored: conversion happens at",
        "-- POLL time, so existing readings keep the old unit until the next poll",
        "-- overwrites them. Re-run poll_temperature_sensors.php after this, or the",
        "-- panel labels Fahrenheit numbers as Celsius.",
        "UPDATE fac_Config SET Value='metric' WHERE Parameter='mUnits';",
        "",
    ]
    wired = {m: r for m, r in rows.items() if r["_known_sku"]}
    plant = {m: r for m, r in rows.items() if r["_plant"]}
    other = {m: r for m, r in rows.items()
             if not r["_known_sku"] and not r["_plant"]}

    for model in sorted(wired):
        r = wired[model]
        m = _sq(model)
        out += [
            f"-- {model} x{r['_count']}: inlet temperature"
            + (", relative humidity" if r["HumidityOID"] else
               " only (no hygrometer on this head)"),
            "INSERT IGNORE INTO fac_SensorTemplate (TemplateID, ManufacturerID, Model)",
            "  SELECT dt.TemplateID, dt.ManufacturerID, dt.Model FROM fac_DeviceTemplate dt",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='Sensor';",
            "UPDATE fac_SensorTemplate st JOIN fac_DeviceTemplate dt ON dt.TemplateID=st.TemplateID",
            f"  SET st.TemperatureOID='{_sq(r['TemperatureOID'])}',",
            f"      st.HumidityOID='{_sq(r['HumidityOID'])}',",
            f"      st.TempMultiplier={float(r['TempMultiplier'])},",
            f"      st.HumidityMultiplier={float(r['HumidityMultiplier'])},",
            f"      st.mUnits='{_sq(r['mUnits'])}'",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='Sensor';",
            "",
        ]

    if plant:
        out += [
            "-- Chiller-plant header instruments: REMOVE the auto-created shadow row.",
            "-- These are BMS points, not room environmentals -- a thermowell in a",
            "-- water header and a magnetic flow meter on the main. openDCIM stores",
            "-- only air temperature and RH, and DataCenter::AvgTemp averages every",
            "-- non-backside sensor in the datacenter with no room filter, so a 7 C",
            "-- chilled-water supply and a 35 C condenser return would both land in",
            "-- the hall's average and in TemperatureRed. With no template row",
            "-- UpdateSensorsFilter's GetTemplate() returns false and skips them, so",
            "-- they stay inventoried assets and record no reading at all.",
            "-- Guarded on both OIDs being empty: this never removes a row that",
            "-- someone has deliberately filled in.",
        ]
        for model in sorted(plant):
            m = _sq(model)
            out += [
                "DELETE st FROM fac_SensorTemplate st",
                "  JOIN fac_DeviceTemplate dt ON dt.TemplateID=st.TemplateID",
                f"  WHERE dt.Model='{m}' AND dt.DeviceType='Sensor'",
                "    AND st.TemperatureOID='' AND st.HumidityOID='';",
            ]
        out += [""]

    if other:
        out += [
            "-- Sensor SKUs with no entry in SENSOR_SIM_TEMPLATE -- left unwired ON",
            "-- PURPOSE. A missing reading leaves the previous value alone; a guessed",
            "-- OID that misses is written as 0 and looks like a real measurement.",
            "-- Add them to SENSOR_SIM_TEMPLATE in tools/export_to_opendcim.py.",
        ]
        out += [f"--   {m}  x{other[m]['_count']}" for m in sorted(other)]
        out += [""]

    # Removing the TEMPLATE stops future writes; it does not remove what earlier
    # runs already wrote. Twelve plant probes were left holding Temperature=0 rows
    # from a poll four days earlier — the poller skipped them correctly from then on,
    # so the rows simply froze, and openDCIM shows a reading's age nowhere. The panel
    # rendered a confident 0.00 C that nothing would ever correct.
    #
    # Keyed on the ABSENCE of a sensor template rather than on a model list: if a
    # sensor has no template, UpdateSensorsFilter can never refresh it, so any
    # reading it holds is unmaintainable by construction. That also cleans up after a
    # SKU being dropped from SENSOR_SIM_TEMPLATE later, which a model list would not.
    out += [
        "-- Drop readings that nothing can ever refresh.",
        "-- A sensor with no fac_SensorTemplate row is skipped by the poller",
        "-- (GetTemplate() returns false), so a row left behind by an earlier run",
        "-- freezes at whatever it last held -- and openDCIM displays no reading age",
        "-- anywhere, so a stale 0.00 is indistinguishable from a live one.",
        "DELETE r FROM fac_SensorReadings r",
        "  JOIN fac_Device d ON d.DeviceID=r.DeviceID",
        "  LEFT JOIN fac_SensorTemplate st ON st.TemplateID=d.TemplateID",
        "  WHERE st.TemplateID IS NULL;",
        "",
    ]

    return out


def _sq(value) -> str:
    """Single-quote a value for MySQL. These strings are OIDs and model names."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def post_import_sql(rows: dict, sensor_rows: dict | None = None,
                    panel_meters: dict | None = None) -> str:
    """Everything the openDCIM REST API cannot write, in one runnable file.

    This began as the rack-PDU SNMP templates and grew, because the same wall keeps
    appearing: get/put/postRoutes cover device, cabinet, devicetemplate, manufacturer,
    powerpanel, powerport, deviceport and pdustats — nothing else. Anything outside
    that set is SQL or it does not happen. Currently:

        fac_CDUTemplate   outlet OIDs; without them a CDU's Status stays 'err'
        fac_Tags          the Poll opt-in, or devices.php never calls the poller
        fac_Device        rack-PDU outlet COUNTS, with the ports to match
        fac_PowerPorts    outlet LABELS, receptacle-numbered
        fac_SensorTemplate  temperature/humidity OIDs; without them the cabinet's
                          Environmental Sensors panel has nothing to show
        fac_CDUTemplate   the PANEL meters' OIDs (panel_meter_sql)
        fac_Config        the cabinet power meter thresholds

    Every statement is re-runnable: INSERT IGNORE where a row may already exist,
    UPDATE keyed on a natural name, and nothing that touches a connection.

    On the CDU templates specifically:

    Two statements per SKU. The INSERT IGNORE is a repair for an estate imported
    before this existed (or by any path that did not go through
    DeviceTemplate::CreateTemplate); normally openDCIM has already made the shadow
    row and the INSERT is a no-op. The UPDATE is what actually fills the OIDs.
    """
    out = [
        "-- Rack PDU (CDU) SNMP templates -> fac_CDUTemplate.",
        "-- Generated by tools/export_to_opendcim.py.",
        "-- openDCIM's REST API cannot write this table: the OID columns live on",
        "-- CDUTemplate, and PUT /api/v1/devicetemplate copies only DeviceTemplate's",
        "-- own properties. Without these the Status column on a CDU's Power",
        "-- Connections panel stays 'err' forever, and the PDU is never polled for",
        "-- wattage (UpdateStats joins on Managed=true).",
        "--",
        "-- The OIDs are the SIMULATOR's private PDU table, not a real PDU's MIB.",
        "-- Replace them per SKU before pointing openDCIM at real gear.",
        "",
    ]
    for model in sorted(rows):
        r = rows[model]
        m = _sq(model)
        out += [
            f"-- {model}: {r['NumOutlets']} outlets, {r['Voltage']} V / {r['Amperage']} A "
            f"nameplate, {r['_outlet_v']} V at the outlet"
            + ("" if r["_known_sku"] else "   [SKU NOT IN THE OUTLET CATALOG — verify]"),
            "INSERT IGNORE INTO fac_CDUTemplate (TemplateID, ManufacturerID, Model)",
            "  SELECT dt.TemplateID, dt.ManufacturerID, dt.Model FROM fac_DeviceTemplate dt",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='CDU';",
            "UPDATE fac_CDUTemplate ct JOIN fac_DeviceTemplate dt ON dt.TemplateID=ct.TemplateID",
            f"  SET ct.Managed={r['Managed']}, ct.ATS={r['ATS']}, ct.SNMPVersion='{r['SNMPVersion']}',",
            f"      ct.VersionOID='{_sq(r['VersionOID'])}',",
            f"      ct.OutletNameOID='{_sq(r['OutletNameOID'])}',",
            f"      ct.OutletDescOID='{_sq(r['OutletDescOID'])}',",
            f"      ct.OutletCountOID='{_sq(r['OutletCountOID'])}',",
            f"      ct.OutletStatusOID='{_sq(r['OutletStatusOID'])}',",
            f"      ct.OutletStatusOn='{_sq(r['OutletStatusOn'])}',",
            f"      ct.Multiplier='{_sq(r['Multiplier'])}',",
            f"      ct.OID1='{_sq(r['OID1'])}', ct.OID2='{_sq(r['OID2'])}', ct.OID3='{_sq(r['OID3'])}',",
            f"      ct.ATSStatusOID='{_sq(r['ATSStatusOID'])}',",
            f"      ct.ATSDesiredResult='{_sq(r['ATSDesiredResult'])}',",
            f"      ct.ProcessingProfile='{_sq(r['ProcessingProfile'])}',",
            f"      ct.Voltage={int(r['Voltage'])}, ct.Amperage={int(r['Amperage'])},",
            f"      ct.NumOutlets={int(r['NumOutlets'])}",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='CDU';",
            "",
        ]

    # The poll opt-in. With NetworkCapacityReportOptIn='OptIn' — openDCIM's default,
    # and the safe one for a real estate, because it means you poll only what you
    # have deliberately enrolled — devices.php answers the status refresh with an
    # empty array unless the device carries the 'Poll' tag. It never reaches the
    # SNMP code, so a fully configured CDU template still shows nothing.
    #
    # Tagging the devices is the honest way to satisfy that. Flipping the global to
    # 'OptOut' would also work and is one row instead of eighty, but it silently
    # enrolls every future device in polling, which is exactly the behaviour the
    # OptIn setting exists to prevent.
    #
    # fac_Tags has no API route either, and neither does fac_DeviceTags.
    out += [
        "-- Enroll the rack PDUs in status polling.",
        "-- NetworkCapacityReportOptIn='OptIn' makes devices.php return an empty",
        "-- status array for anything without this tag, before any SNMP happens.",
        "-- Both device types that HAVE a Status column are enrolled: CDU (outlet",
        "-- state, CDUInfo::getPortStatus) and Switch (port link state,",
        "-- SwitchInfo::getPortStatus). No other type renders one, so tagging",
        "-- anything else would buy polling load and no display.",
        "INSERT IGNORE INTO fac_Tags (Name) VALUES ('Poll');",
        "INSERT IGNORE INTO fac_DeviceTags (DeviceID, TagID)",
        "  SELECT d.DeviceID, t.TagID FROM fac_Device d, fac_Tags t",
        "  WHERE d.DeviceType IN ('CDU','Switch') AND t.Name='Poll';",
        "",
    ]
    out += outlet_count_sql(rows)
    out += outlet_label_sql()
    out += sensor_template_sql(sensor_rows or {})
    out += panel_meter_sql(panel_meters or {})
    out += meter_threshold_sql()
    return "\n".join(out) + "\n"


def panel_meter_sql(rows: dict) -> list[str]:
    """fac_CDUTemplate rows for the PANEL meters (see PANEL_METER_SPEC).

    The panel's own TemplateID is set through the REST API — POST
    /api/v1/powerpanel/{id} assigns any PowerPanel property — so this file only has
    to carry what the API cannot reach, which is the OID columns, exactly as for the
    rack PDUs above.

    NumOutlets stays 0 and the outlet OIDs stay empty: a panel meter has no
    switched outlets to enumerate, and openDCIM's outlet walk is driven off a CDU
    DEVICE, which none of these templates is ever attached to.
    """
    if not rows:
        return []
    out = [
        "-- Panel meter templates -> fac_CDUTemplate.",
        "-- openDCIM 23.04 does NOT poll panels: fac_PowerPanel is read by no",
        "-- poller, and PanelIPAddress/TemplateID exist only on the panel form. These",
        "-- rows record WHICH instrument reports each board and what it answers, for",
        "-- an operator and for an external poller. They will not make a number",
        "-- appear on power_panel.php.",
        "",
    ]
    for model in sorted(rows):
        r = rows[model]
        m = _sq(model)
        o1, o2, o3 = (list(r["OIDs"]) + ["", "", ""])[:3]
        out += [
            f"-- {model}: {r['Note']}",
            f"--   on {len(r['Panels'])} panel(s): {', '.join(sorted(r['Panels'])[:4])}"
            + (" ..." if len(r["Panels"]) > 4 else ""),
            "INSERT IGNORE INTO fac_CDUTemplate (TemplateID, ManufacturerID, Model)",
            "  SELECT dt.TemplateID, dt.ManufacturerID, dt.Model FROM fac_DeviceTemplate dt",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='CDU';",
            "UPDATE fac_CDUTemplate ct JOIN fac_DeviceTemplate dt ON dt.TemplateID=ct.TemplateID",
            f"  SET ct.Managed={int(r['Managed'])}, ct.ATS=0, ct.SNMPVersion='2c',",
            f"      ct.ProcessingProfile='{_sq(r['ProcessingProfile'])}',",
            f"      ct.Multiplier='{_sq(r['Multiplier'])}',",
            f"      ct.OID1='{_sq(o1)}', ct.OID2='{_sq(o2)}', ct.OID3='{_sq(o3)}',",
            f"      ct.Voltage={int(r['Voltage'])}, ct.Amperage=0, ct.NumOutlets=0",
            f"  WHERE dt.Model='{m}' AND dt.DeviceType='CDU';",
            "",
        ]
    return out


# Where the cabinet power meters turn yellow and red, as a PERCENTAGE of MaxKW. One
# pair drives three meters: Computed Watts, Measured Watts and the per-PDU bars.
#
# 85/100 is not a taste call, it is what cabnavigator's arithmetic allows.
#
#   * The two CABINET percentages are clamped to 100 BEFORE the colour test
#     (cabnavigator.php: "if($PowerPercent>100){$PowerPercent=100;}"). A threshold at
#     or above 100 can therefore never be exceeded — setting PowerRed=120 to quiet a
#     noisy meter does not calibrate it, it switches the warning off and every rack
#     reads green however overloaded. That mistake was made here and caught.
#   * Computed Watts sums NAMEPLATE, Measured sums live SNMP, so Computed sits
#     structurally higher — about 1.4x on this estate. Sharing one threshold pair
#     means both cannot be tuned: pick a red Computed can clear and Measured never
#     alarms; pick one Measured can reach and every compute rack is permanently red.
#     Yellow at 85 lets a genuinely loaded rack signal, while a nameplate total over
#     the allowance reads as attention rather than fault.
#   * Red at 100 is unreachable for the cabinet meters by that clamp. It stays live
#     for the PER-PDU bars, which are NOT clamped ($PDUPercent) — a feed over 100% of
#     its derated breaker is the alarm that actually matters, and it survives.
CABINET_POWER_YELLOW_PCT = 85
CABINET_POWER_RED_PCT = 100


def meter_threshold_sql() -> list[str]:
    """openDCIM's power meter thresholds. Site config; no API route exists for it."""
    return [
        "-- Cabinet power meter thresholds (percent of the cabinet's MaxKW).",
        "-- Both cabinet percentages are CLAMPED to 100 before the colour test, so a",
        "-- threshold >= 100 can never fire. Raising PowerRed above 100 does not make",
        "-- the meter quieter, it makes every rack green. Read the comment on",
        "-- CABINET_POWER_YELLOW_PCT in tools/export_to_opendcim.py before changing.",
        f"UPDATE fac_Config SET Value={CABINET_POWER_YELLOW_PCT} "
        f"WHERE Parameter='PowerYellow';",
        f"UPDATE fac_Config SET Value={CABINET_POWER_RED_PCT} "
        f"WHERE Parameter='PowerRed';",
        "",
    ]


def outlet_label_sql() -> list[str]:
    """Name every rack-PDU outlet after the receptacle, on every PDU.

    The estate had three schemes at once: openDCIM's generated 'Power Connection N'
    on most PDUs, and on a handful the SNMP outlet-name table read back through
    devices.php's "refresh names" action — which writes the CONNECTED DEVICE'S NAME
    onto the outlet, and 'Outlet N' where nothing is plugged in. The Device Port
    column on every load's Power Connections panel shows this label, so the same
    question got three different-looking answers depending on which PDU fed it.

    Standardise on the receptacle number. An outlet's identity is the number printed
    on the strip: that is what an engineer reads when tracing a cord, it is what the
    vendor MIB indexes, and it does not change. Naming the outlet after its load
    duplicates the Device column, which already says what is plugged in, and goes
    stale the moment that load is decommissioned — leaving an outlet still claiming
    to be SRV18 long after SRV18 has gone.

    Label only. Notes are left alone: the same refresh wrote device names there too,
    but Notes is where a human records "do not unplug — feeds the OOB switch", and
    blanket-clearing it would delete real operator knowledge to fix a cosmetic
    inconsistency. There is no way to tell the two apart from here.

    No API route can do this. POST /api/v1/powerport/{deviceid} populates a FRESH
    PowerPorts object from the posted fields only — no getPort() first — so a
    label-only POST leaves ConnectedDeviceID empty and updatePort() reads that as
    "clear the connection", taking the cord with it. PowerPorts::updateLabel() is a
    pure label write, but nothing exposes it.
    """
    return [
        "-- Rack PDU outlet labels: name every receptacle after its number.",
        "-- Label ONLY — never touches ConnectedDeviceID, so no cord can be lost.",
        "-- Do NOT do this through POST /api/v1/powerport/{id}: that route builds a",
        "-- fresh PowerPorts object from the posted fields, so omitting the",
        "-- connection clears it. See outlet_label_sql() in this file.",
        "UPDATE fac_PowerPorts pp JOIN fac_Device d ON d.DeviceID=pp.DeviceID",
        "  SET pp.Label = CONCAT('Outlet ', pp.PortNumber)",
        "  WHERE d.DeviceType='CDU';",
        "",
    ]


def outlet_count_sql(rows: dict) -> list[str]:
    """Set each rack PDU's outlet count from its SKU, and materialise the ports.

    Two statements because openDCIM's own reconciler cannot be used. Raising
    PowerSupplyCount through the API writes the count and then throws on a duplicate
    key before creating anything (PowerPorts::createPorts re-INSERTs ports 1..N and
    its $update_existing flag only silences the log, not the PDO exception), leaving
    the device declaring more outlets than it has ports. So the count and the ports
    are written together here, and the INSERT IGNORE makes the port fill re-runnable
    and safe against whatever already exists.

    GROW ONLY, by the `n.n <= d.PowerSupplyCount` join and a GREATEST() on the count.
    Shrinking a strip means deleting the ports above the new count, and every cord
    recorded on one goes with it. A PDU that needs to shrink has been re-SKU'd, which
    is a decision for a human, not for a repair pass that would silently unplug racks.
    """
    if not rows:
        return []
    # model -> outlet count, applied by joining through the device's template.
    cases = "\n".join(
        f"      WHEN dt.Model='{_sq(m)}' THEN {int(r['NumOutlets'])}"
        for m, r in sorted(rows.items()) if int(r["NumOutlets"]) > 0
    )
    if not cases:
        return []
    return [
        "-- Rack PDU outlet counts, from the SKU catalog.",
        "-- openDCIM materialises one power port per PowerSupplyCount and a cord is",
        "-- stored as load-port -> (CDU, outlet), so a strip short of ports simply has",
        "-- nowhere to land the cords above the count, and its panel renders short.",
        "-- GROW ONLY — see outlet_count_sql() in tools/export_to_opendcim.py.",
        "UPDATE fac_Device d JOIN fac_DeviceTemplate dt ON dt.TemplateID=d.TemplateID",
        "  SET d.PowerSupplyCount = GREATEST(d.PowerSupplyCount, CASE",
        cases,
        "      ELSE d.PowerSupplyCount END)",
        "  WHERE d.DeviceType='CDU';",
        "",
        "-- Materialise the ports the count now promises. openDCIM's own createPorts()",
        "-- cannot be used: it re-INSERTs from port 1 and dies on the duplicate.",
        "INSERT IGNORE INTO fac_PowerPorts (DeviceID, PortNumber, Label,",
        "                                   ConnectedDeviceID, ConnectedPort, Notes)",
        "  SELECT d.DeviceID, n.n, CONCAT('Power Connection ', n.n), NULL, NULL, ''",
        "  FROM fac_Device d JOIN (",
        "         SELECT a.N + b.N * 10 + 1 AS n FROM",
        "           (SELECT 0 AS N UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL",
        "            SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL",
        "            SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL",
        "            SELECT 9) a,",
        "           (SELECT 0 AS N UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL",
        "            SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL",
        "            SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL",
        "            SELECT 9) b",
        "       ) n ON n.n <= d.PowerSupplyCount",
        "  WHERE d.DeviceType='CDU';",
        "",
    ]


def pdu_phase_map(devices: list[dict]) -> dict:
    """PDU name -> 1 or 3, from the phases its outlets actually sit on."""
    out = {}
    for d in devices:
        if d.get("device_type") != "pdu":
            continue
        phases = {o.get("phase") for o in (d.get("outlets") or []) if o.get("phase")}
        if phases:
            out[d["name"]] = 3 if len(phases) >= 3 else 1
            continue
        # No outlets in the payload — the live /api/devices response omits them, and
        # reading "no phases" as single-phase put every 3-phase strip on one pole.
        # A 3-pole breaker read as 1-pole is not a cosmetic error on a panel
        # schedule: it frees two poles that are physically occupied, so the next
        # PDU gets assigned a position already taken by this one's B and C phases.
        spec = PDU_OUTLET_CATALOG.get(d.get("model_name") or "")
        out[d["name"]] = spec[2] if spec else 1
    return out


def breaker_sql(rows: list[dict]) -> str:
    """SQL for the breaker assignments.

    openDCIM's REST API has no route that writes fac_PowerDistribution (PanelID /
    PanelPole / BreakerSize) — PUT/POST cover devices, cabinets, templates,
    manufacturers and panels only. The UI writes these through devices.php. Emitting
    reviewable SQL is the honest alternative to pretending an endpoint exists; run it
    against the openDCIM database yourself.
    """
    out = [
        "-- Rack PDU -> RPP breaker assignments.",
        "-- Generated by tools/export_to_opendcim.py. A feeds odd poles, B feeds even.",
        "-- Positions are an assigned scheme: the simulator models no breaker numbers.",
        "--",
        "-- BreakerSize is the POLE COUNT (1/2/3), NOT the trip rating. Three places",
        "-- read it that way: cabnavigator derives the strip's ceiling from it",
        "-- (1 -> V/sqrt(3) single-phase, 2 -> V, 3 -> V*sqrt(3) three-phase),",
        "-- GetAllBreakerPoles() walks 1..BreakerSize to span the schedule, and",
        "-- UpdateStats() tests == 3. Writing the AMPERAGE here (this tool used to",
        "-- write 32) claims a 32-pole breaker, and every single-phase strip then",
        "-- gets costed as three-phase -- 18.4 kW of headroom on a 6.2 kW unit.",
        "-- The trip rating belongs in InputAmperage, which is where cabnavigator",
        "-- reads it from.",
        "",
    ]
    for r in rows:
        if r["overflow"]:
            out.append(f"-- SKIPPED {r['pdu']}: {r['panel']} has no free "
                       f"{r['phases']}-pole position left.")
            continue
        poles = str(r["first_pole"])
        out.append(
            "UPDATE fac_PowerDistribution pd "
            "JOIN fac_PowerPanel pp ON pp.PanelLabel = '{panel}' "
            "SET pd.PanelID = pp.PanelID, pd.PanelPole = '{poles}', "
            "pd.BreakerSize = {poles_n}, pd.InputAmperage = {amps} "
            "WHERE pd.Label = '{pdu}';".format(
                panel=r["panel"], poles=poles, poles_n=r["phases"],
                amps=r["breaker_a"], pdu=r["pdu"])
        )
    return "\n".join(out) + "\n"


def plan_cords(graph: dict, importable: set) -> list[dict]:
    """Cords from the topology's power layer: which outlet feeds which PSU.

    Only edges whose BOTH ends were imported can be wired — a cord from a PDU to a
    CRAH has no openDCIM object at the load end. src_id/dst_id order is not the flow
    direction; supply_node/load_node are.
    """
    name = {d["id"]: d["name"] for d in graph.get("devices", [])}
    cords = []
    for link in graph.get("links", []):
        if link.get("layer") != "power":
            continue
        outlet, psu = link.get("outlet"), link.get("psu")
        if not outlet or not psu:
            continue        # a panel feed, not a cord — panels carry no outlets
        supply = name.get(link.get("supply_node") or link.get("src_id"), "")
        load = name.get(link.get("load_node") or link.get("dst_id"), "")
        if supply in importable and load in importable:
            cords.append({"pdu": supply, "device": load,
                          "outlet": int(outlet), "psu": int(psu)})
    return cords


# What /api/devices does NOT serialise. Every one of these is a STATIC property of the
# SKU that the saved topology holds and the live payload drops, and each has already
# cost a wrong import: no outlets[] gave every PDU a flat 24-outlet strip and read
# every 3-phase unit as single-phase, no interfaces[] would have renamed 2026 ports to
# their port number, and no power_draw_w costed a 250 W switch at its 46 W idle.
#
# They are filled from the topology file rather than fixed one at a time, because the
# next field to go missing should not need a fourth workaround.
_TOPOLOGY_ONLY_FIELDS = ("power_draw_w", "outlets", "interfaces", "psus")


def enrich_from_topology(devices: list[dict], topology_path: str) -> int:
    """Fill the SKU facts the live API omits, matched by device name.

    Only fills what is ABSENT or empty — anything the API did send wins, so this can
    never overwrite live state with a stale file. Returns how many devices gained a
    field, so a caller can say when the file did not match the running estate.
    """
    try:
        with open(topology_path, encoding="utf-8") as fh:
            topo = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 0
    by_name = {}
    for node in topo.get("nodes", []):
        d = node.get("device") or {}
        if d.get("name"):
            by_name[d["name"]] = d
    filled = 0
    for dev in devices:
        src = by_name.get(dev.get("name"))
        if not src:
            continue
        touched = False
        for key in _TOPOLOGY_ONLY_FIELDS:
            if not dev.get(key) and src.get(key):
                dev[key] = src[key]
                touched = True
        filled += 1 if touched else 0
    return filled


def iface_label_map(topology_path: str) -> dict:
    """device name -> {port position (0-based) -> interface name}.

    Read from the topology FILE, not /api/devices: the live payload carries
    interface_count but not the interfaces themselves, the same gap that made the
    exporter invent a flat 24 outlets for every PDU. Positions are 0-based because
    that is what the graph's src_iface/dst_iface are — TopologyEngine._next_free_iface
    returns an index into device.interfaces, NOT the 1-based Interface.index. The two
    differ by one, and openDCIM's PortNumber is 1-based like the latter.
    """
    try:
        with open(topology_path, encoding="utf-8") as fh:
            topo = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for node in topo.get("nodes", []):
        d = node.get("device") or {}
        ifaces = d.get("interfaces") or []
        if d.get("name") and ifaces:
            out[d["name"]] = {i: (x.get("name") or "") for i, x in enumerate(ifaces)}
    return out


def plan_port_links(graph: dict, importable: set, labels: dict) -> list[dict]:
    """Ethernet links as openDCIM port connections, one row per cable.

    BOTH planes. A server's BMC drop to an OOB switch is a real cable in a real
    tray and belongs in the record; leaving it out is how a DCIM ends up disagreeing
    with the rack. They are distinguishable after import by the port that carries
    them (a mgmt0/iDRAC/iLO port is the management plane by definition), so nothing
    is lost by importing both into one table.

    ONE row per link, not two. DevicePorts::updatePort() writes the far end itself —
    it clears both old connections and calls updatePort(fasttrack=true) on the peer —
    so posting each cable twice would just clear and re-make the same connection.
    """
    name = {d["id"]: d["name"] for d in graph.get("devices", [])}
    rows = []
    for link in graph.get("links", []):
        if link.get("layer") not in ("production", "management"):
            continue
        # The two sources spell the endpoints differently: /api/topology/graph emits
        # src_id/dst_id, a saved topology file emits src/dst. Reading only the former
        # made --from-file import zero cables without saying so.
        a = name.get(link.get("src_id") or link.get("src"), "")
        b = name.get(link.get("dst_id") or link.get("dst"), "")
        if a not in importable or b not in importable:
            continue
        ai, bi = link.get("src_iface"), link.get("dst_iface")
        if ai is None or bi is None:
            continue
        rows.append({
            "a": a, "a_port": int(ai) + 1, "a_label": labels.get(a, {}).get(int(ai), ""),
            "b": b, "b_port": int(bi) + 1, "b_label": labels.get(b, {}).get(int(bi), ""),
            "layer": link.get("layer"),
        })
    return rows


# ── apply ─────────────────────────────────────────────────────────────────────

def _index(rows, key):
    out = {}
    for r in rows or []:
        out[str(r.get(key, "")).strip()] = r
    return out


def apply(dcim: Http, p: dict, verbose: bool = True) -> dict:
    counts = defaultdict(int)

    def say(msg):
        if verbose:
            print(msg)

    # 0. DataCenters — GET only in the API. Must already exist.
    existing_dc = _index(dcim.request("GET", "/api/v1/datacenter").get("datacenter", []), "Name")
    need = {c["DataCenter"] for c in p["cabinets"].values()} | {x["DataCenter"] for x in p["panels"]}
    missing = sorted(n for n in need if n and n not in existing_dc)
    if missing:
        raise SystemExit(
            "openDCIM has no DataCenter named: " + ", ".join(missing) + "\n"
            "The REST API cannot create datacenters (GET only). Create them in the UI\n"
            "under Admin -> Data Centers, then re-run."
        )
    dc_id = {n: r["DataCenterID"] for n, r in existing_dc.items()}

    # IDs always come from a READ-BACK, never from parsing the PUT response. The
    # create responses are not uniformly shaped, and guessing produced devices with
    # TemplateID 0 — filed with no make, model, picture or port template, which is
    # most of what a DCIM record is for.
    def reload_ids(path: str, collection: str, key: str, id_field: str) -> dict:
        rows = dcim.request("GET", path).get(collection, [])
        return {str(r.get(key, "")).strip(): r[id_field] for r in rows}

    # 1. Manufacturers
    mfg_id = reload_ids("/api/v1/manufacturer", "manufacturer", "Name", "ManufacturerID")
    created = False
    for name in p["manufacturers"]:
        if name in mfg_id:
            continue
        dcim.request("PUT", f"/api/v1/manufacturer/{urllib.parse.quote(name)}", {"Name": name})
        counts["manufacturers"] += 1
        created = True
        say(f"  + manufacturer {name}")
    if created:
        mfg_id = reload_ids("/api/v1/manufacturer", "manufacturer", "Name", "ManufacturerID")

    # 2. Device templates
    tmpl_id = reload_ids("/api/v1/devicetemplate", "devicetemplate", "Model", "TemplateID")
    # DROP THE EMPTY KEY IMMEDIATELY. A template whose shadow row is gone comes back
    # from the list with Model="" (see template_id_by_name), so reload_ids indexes it
    # under "" — and any later tmpl_id.get(<no meter>, "") then resolves to that
    # unrelated template. It did: the four ATSes and four gensets, which are meant to
    # carry NO meter template, were all stamped with the blank row's TemplateID.
    tmpl_id.pop("", None)

    def template_id_by_name(model: str) -> int:
        """The TemplateID of an existing template the LIST cannot name.

        GET /devicetemplate returns Model="" for a Sensor or CDU template whose
        shadow row is missing: DeviceTemplate::RowToObject copies the whole
        SensorTemplate/CDUTemplate over the object, and that object has its own
        (empty) Model property. The six plant-instrument templates are exactly that
        case BY DESIGN — sensor_template_sql deletes their shadow rows so the poller
        skips them (see SENSOR_PLANT_PREFIX) — so a second run could not see them,
        tried to create them again, and died on the UNIQUE(ManufacturerID, Model)
        index with an HTTP 500.

        The SEARCH still matches, because it filters on fac_DeviceTemplate.Model in
        SQL and only blanks the field on the way out. So ask by name and take the ID.
        """
        try:
            rows = dcim.request("GET", "/api/v1/devicetemplate",
                                {"Model": model}).get("devicetemplate", [])
        except RuntimeError:
            return 0
        return rows[0]["TemplateID"] if len(rows) == 1 else 0

    created = False
    for model, t in p["templates"].items():
        if model in tmpl_id:
            continue
        found = template_id_by_name(model)
        if found:
            tmpl_id[model] = found
            counts["templates_adopted"] += 1
            continue
        dcim.request("PUT", f"/api/v1/devicetemplate/{urllib.parse.quote(model)}", {
            "Model": model, "ManufacturerID": mfg_id.get(t["Manufacturer"], 0),
            "DeviceType": t["DeviceType"], "Height": t["Height"],
            "Weight": t["Weight"],
            "NumPorts": t["NumPorts"], "PSCount": t["PSCount"], "Wattage": t["Wattage"],
        })
        counts["templates"] += 1
        created = True
        say(f"  + template {model} ({t['DeviceType']}, {t['Height']}U, {t['Wattage']} W)")
    if created:
        # MERGED, not replaced: the read-back cannot name a template whose shadow row
        # is gone, so overwriting the map here would throw away every ID adopted
        # above and put the run straight back into the missing_tmpl exit below.
        tmpl_id = {**tmpl_id,
                   **reload_ids("/api/v1/devicetemplate", "devicetemplate",
                                "Model", "TemplateID")}
        tmpl_id.pop("", None)

    # Templates imported before weights existed carry Weight 0, and a cabinet's total
    # is the sum of its devices' template weights — so one stale template silently
    # under-reports every rack it appears in. POST /devicetemplate/{id} loads the row
    # first, so Weight is the only field changed.
    have_tmpl = {str(r.get("Model", "")).strip(): r
                 for r in dcim.request("GET", "/api/v1/devicetemplate").get("devicetemplate", [])}
    for model, t in p["templates"].items():
        row = have_tmpl.get(model)
        if row and t["Weight"] and int(row.get("Weight") or 0) != int(t["Weight"]):
            try:
                dcim.request("POST", f"/api/v1/devicetemplate/{row['TemplateID']}",
                             {"Weight": t["Weight"]})
                counts["template_weight_set"] += 1
            except RuntimeError as e:
                counts["template_weight_failed"] += 1
                if counts["template_weight_failed"] <= 5:
                    say(f"  ! weight {model}: {e}")

    missing_tmpl = [m for m in p["templates"] if not tmpl_id.get(m)]
    if missing_tmpl:
        raise SystemExit("templates did not come back with IDs: " + ", ".join(missing_tmpl[:5]))

    # 3. Cabinets
    def reload_cabinets() -> dict:
        rows = dcim.request("GET", "/api/v1/cabinet").get("cabinet", [])
        return {(str(r.get("DataCenterID")), str(r.get("Location", "")).strip()): r["CabinetID"]
                for r in rows}

    cab_id = reload_cabinets()
    have_cab = {(str(r.get("DataCenterID")), str(r.get("Location", "")).strip()): r
                for r in dcim.request("GET", "/api/v1/cabinet").get("cabinet", [])}
    created = False
    for (dc, loc), c in sorted(p["cabinets"].items()):
        if (str(dc_id[dc]), loc) in cab_id:
            # Already there — but a cabinet imported before MaxKW was carried has 0,
            # and cabnavigator guards EVERY meter on MaxKW > 0. A zero there is not a
            # cosmetic gap: Space, Weight, Computed Watts and Measured Watts all
            # render as dead bars reading 0%, so the page looks broken rather than
            # empty. Repair in place; MaxKW is the only field touched.
            row = have_cab.get((str(dc_id[dc]), loc))
            delta = {}
            if row and float(row.get("MaxKW") or 0) != float(c["MaxKW"]):
                delta["MaxKW"] = c["MaxKW"]
            if row and float(row.get("MaxWeight") or 0) != float(c["MaxWeight"]):
                delta["MaxWeight"] = c["MaxWeight"]
            if delta:
                try:
                    dcim.request("POST", f"/api/v1/cabinet/{row['CabinetID']}", delta)
                    for f in delta:
                        counts[f"cabinet_{f}_set"] += 1
                except RuntimeError as e:
                    counts["cabinet_limits_failed"] += 1
                    if counts["cabinet_limits_failed"] <= 5:
                        say(f"  ! cabinet limits {dc}/{loc}: {e}")
            continue
        dcim.request("PUT", "/api/v1/cabinet", {
            "DataCenterID": dc_id[dc], "Location": loc,
            "CabinetHeight": c["CabinetHeight"], "Notes": c["Notes"],
            "MaxKW": c["MaxKW"],
        })
        counts["cabinets"] += 1
        created = True
        say(f"  + cabinet {dc} / {loc}  ({c['Notes']})")
    if created:
        cab_id = reload_cabinets()

    # 4. Devices
    have = _index(dcim.request("GET", "/api/v1/device").get("device", []), "Label")
    for d in p["devices"]:
        if d["Label"] in have:
            # Already imported — repair the two fields an earlier run got wrong, and
            # ONLY those. Both are silent killers of SNMP polling:
            #
            #   FirstPortNum 0 — no column default on fac_Device, so an API-created
            #     device lands on 0, and getPortStatus() only starts recording when
            #     the walk index EQUALS it. Every Status light stays 'err'.
            #   SNMPCommunity holding the IP address — a community of "192.168.1.2"
            #     authenticates against nothing. openDCIM never copies PrimaryIP
            #     here; it came in from the importer, so the importer repairs it.
            #
            # Deliberately a field-by-field delta, not a re-PUT: POST /device/{id}
            # loads the row first, so leaving Ports/PowerSupplyCount untouched keeps
            # UpdateDevice's port reconciler asleep. It would otherwise drop and
            # recreate power ports, taking every cord recorded on them with it.
            row = have[d["Label"]]
            delta = {}
            if d["FirstPortNum"] and int(row.get("FirstPortNum") or 0) != d["FirstPortNum"]:
                delta["FirstPortNum"] = d["FirstPortNum"]
            if d["SNMPCommunity"] and row.get("SNMPCommunity") != d["SNMPCommunity"]:
                delta["SNMPCommunity"] = d["SNMPCommunity"]
            # PrimaryIP drifts whenever the estate is renumbered (see
            # tools/renumber_mgmt_plane.py). It has to move IN THE SAME PASS as
            # SNMPCommunity: the two are the same address, and repairing one alone
            # leaves openDCIM polling the OLD address with the NEW community —
            # which fails as a timeout, indistinguishable from a dead device.
            if d["PrimaryIP"] and row.get("PrimaryIP") != d["PrimaryIP"]:
                delta["PrimaryIP"] = d["PrimaryIP"]
            # PowerSupplyCount is NOT repaired here — see outlet_count_sql(). Raising
            # it through the API half-applies: UpdateDevice writes the new count, then
            # its reconciler calls PowerPorts::createPorts($id, true), which re-INSERTs
            # ports 1..N including the ones already there. The $update_existing flag
            # only suppresses openDCIM's own error LOGGING; the duplicate INSERT still
            # raises a PDOException under ERRMODE_EXCEPTION, so the request 500s with
            # "Duplicate entry '<id>-1' for key 'PRIMARY'" after the count is committed
            # and before a single new port exists.
            if d["Weight"] and int(row.get("Weight") or 0) != d["Weight"]:
                delta["Weight"] = d["Weight"]
            if d["NominalWatts"] and int(row.get("NominalWatts") or 0) != d["NominalWatts"]:
                delta["NominalWatts"] = d["NominalWatts"]
            have_ports = int(row.get("PowerSupplyCount") or 0)
            if d["PowerSupplyCount"] != have_ports:
                counts["outlet_count_needs_sql"] += 1
            if delta:
                try:
                    dcim.request("POST", f"/api/v1/device/{row['DeviceID']}", delta)
                    for field in delta:
                        counts[f"repaired_{field}"] += 1
                except RuntimeError as e:
                    counts["repair_failed"] += 1
                    if counts["repair_failed"] <= 5:
                        say(f"  ! repair {d['Label']} {sorted(delta)}: {e}")
            continue
        cid = cab_id.get((str(dc_id[d["DataCenter"]]), d["Cabinet"]))
        if not cid:
            say(f"  ! no cabinet for {d['Label']} ({d['Cabinet']}) — skipped")
            counts["devices_failed"] += 1
            continue
        dcim.request("PUT", f"/api/v1/device/{urllib.parse.quote(d['Label'])}", {
            "Label": d["Label"], "Cabinet": cid, "Position": d["Position"],
            "Height": d["Height"], "DeviceType": d["DeviceType"],
            "TemplateID": tmpl_id.get(d["Model"], 0) or 0,
            "PrimaryIP": d["PrimaryIP"], "SNMPCommunity": d["SNMPCommunity"],
            "Ports": d["Ports"], "NominalWatts": d["NominalWatts"],
            "PowerSupplyCount": d["PowerSupplyCount"],
            "FirstPortNum": d["FirstPortNum"], "Weight": d["Weight"],
        })
        counts["devices"] += 1
        say(f"  + device {d['Label']} -> {d['Cabinet']} U{d['Position']}")

    # 4b. Power cords: outlet -> PSU, exactly as the topology terminates them.
    #
    # openDCIM stores a cord on the LOAD device's power port, pointing at the feeding
    # CDU device and the outlet number (fac_PowerPorts.ConnectedDeviceID/ConnectedPort)
    # — the same shape its own bulk_power.php importer writes. fac_PowerConnection is
    # the older PDU-centric table that powerconversion.php exists to migrate away from,
    # so it is deliberately not used here.
    if p.get("cords"):
        dev_id = _index(dcim.request("GET", "/api/v1/device").get("device", []), "Label")
        dev_id = {k: v["DeviceID"] for k, v in dev_id.items()}
        wired = 0
        for c in p["cords"]:
            src = dev_id.get(c["pdu"])
            dst = dev_id.get(c["device"])
            if not src or not dst:
                counts["cords_skipped"] += 1
                continue
            try:
                dcim.request("POST", f"/api/v1/powerport/{dst}", {
                    "PortNumber": c["psu"],
                    "Label": f"PSU{c['psu']}",
                    "ConnectedDeviceID": src,
                    "ConnectedPort": c["outlet"],
                })
                counts["cords"] += 1
                wired += 1
                if wired % 100 == 0:
                    say(f"  ... {wired} cords")
            except RuntimeError as e:
                counts["cords_failed"] += 1
                if counts["cords_failed"] <= 5:
                    say(f"  ! cord {c['device']} PSU{c['psu']} <- {c['pdu']} "
                        f"outlet {c['outlet']}: {e}")

    # 4c. Network cabling: production and management, into fac_Ports.
    #
    # PUT /api/v1/deviceport is a genuine PUT — DevicePorts::updatePort() OVERWRITES
    # the row, and its "sanity check" replaces an empty Label with the port NUMBER.
    # Omitting Label would therefore rename GigabitEthernet0/0 to "1" on every port
    # this touches.
    #
    # WRITTEN FROM BOTH ENDS, and that is not redundant. updatePort() makes the far
    # end's CONNECTION but never its NAME — it pushes only ConnectedDeviceID/Port/
    # Notes/Media/Color to the peer. Writing each cable once therefore named one end
    # and left the other showing openDCIM's generated placeholder: a server NIC that
    # is really eth1/1 rendered as "Port1", on 991 of 2026 cable ends. The second
    # write is safe to repeat because the peer's row is loaded before it is rewritten
    # (updatePort(fasttrack=true) re-UPDATEs the Label it already had), so neither
    # pass clobbers the other's name and the connection lands in the same state.
    if p.get("port_links"):
        dev_id = _index(dcim.request("GET", "/api/v1/device").get("device", []), "Label")
        dev_id = {k: v["DeviceID"] for k, v in dev_id.items()}
        wired = 0
        for lk in p["port_links"]:
            a, b = dev_id.get(lk["a"]), dev_id.get(lk["b"])
            if not a or not b:
                counts["links_skipped"] += 1
                continue
            ends = [(a, lk["a_port"], lk["a_label"], b, lk["b_port"])]
            # Only write the far end when we actually know its name; without one the
            # PUT would rename that port to its number, which is the very damage this
            # pass exists to undo.
            if lk["b_label"]:
                ends.append((b, lk["b_port"], lk["b_label"], a, lk["a_port"]))
            else:
                counts["links_farend_unnamed"] += 1
            ok = True
            for dev, port, label, peer, peer_port in ends:
                try:
                    dcim.request("PUT", "/api/v1/deviceport", {
                        "DeviceID": dev,
                        "PortNumber": port,
                        "Label": label,
                        "ConnectedDeviceID": peer,
                        "ConnectedPort": peer_port,
                        "MediaID": 0,
                        "ColorID": 0,
                        "Notes": "",
                    })
                    counts["port_writes"] += 1
                except RuntimeError as e:
                    ok = False
                    counts["links_failed"] += 1
                    if counts["links_failed"] <= 5:
                        say(f"  ! link {lk['a']}:{lk['a_port']} <-> "
                            f"{lk['b']}:{lk['b_port']} ({lk['layer']}): {e}")
            if ok:
                counts["links"] += 1
            wired += 1
            if wired % 200 == 0:
                say(f"  ... {wired} links")

    # 5. Power panels, upstream first so a parent exists before a child names it.
    have = _index(dcim.request("GET", "/api/v1/powerpanel").get("powerpanel", []), "PanelLabel")
    for pan in p["panels"]:
        if pan["PanelLabel"] in have:
            continue
        dcim.request("PUT", f"/api/v1/powerpanel/{urllib.parse.quote(pan['PanelLabel'])}", {
            "PanelLabel": pan["PanelLabel"], "PanelVoltage": pan["PanelVoltage"],
            "NumberOfPoles": pan["NumberOfPoles"], "PanelIPAddress": pan["PanelIPAddress"],
            "NumberScheme": pan["NumberScheme"],
            "MainBreakerSize": pan["MainBreakerSize"],
            "MapDataCenterID": dc_id[pan["DataCenter"]],
        })
        counts["panels"] += 1
        say(f"  + panel {pan['PanelLabel']} ({pan['PanelVoltage']} V, "
            f"{pan['NumberOfPoles']} poles)")

    # 6. Panel parentage — a second pass, because a parent must exist before a child
    #    can point at it, and because this must also repair panels created by an
    #    earlier run that predates the feed map.
    feeds = p.get("panel_feeds") or {}
    if feeds:
        panel_id = _index(dcim.request("GET", "/api/v1/powerpanel").get("powerpanel", []),
                          "PanelLabel")
        panel_id = {k: v["PanelID"] for k, v in panel_id.items()}
        poles = {x["PanelLabel"]: x["NumberOfPoles"] for x in p["panels"]}
        schemes = {x["PanelLabel"]: x["NumberScheme"] for x in p["panels"]}
        mains = {x["PanelLabel"]: x["MainBreakerSize"] for x in p["panels"]}
        volts = {x["PanelLabel"]: x["PanelVoltage"] for x in p["panels"]}
        panel_ips = {x["PanelLabel"]: x["PanelIPAddress"] for x in p["panels"]}
        panel_meter = {x["PanelLabel"]: x.get("_meter_model") or "" for x in p["panels"]}
        # Every planned panel, not just the ones with a parent. The ROOTS — the two
        # utility feeds and the four gensets — have nothing upstream, so a loop over
        # feeds.items() never reaches them, and they kept a stale meter IP through
        # the estate renumber while their children were repaired around them.
        for child in sorted({x["PanelLabel"] for x in p["panels"]} | set(feeds)):
            feed = feeds.get(child)
            cid = panel_id.get(child)
            pid = panel_id.get(feed["parent"]) if feed else None
            if not cid:
                counts["parentage_skipped"] += 1
                continue
            alt = feed["alternates"] if feed else []
            # ParentBreakerName is free text in openDCIM ("for switchgear, this
            # usually won't be numbered"), so it is the honest place to record the
            # source openDCIM's single-parent model cannot hold.
            label = "" if not feed else (
                feed["parent"] if not alt
                else f"{feed['parent']} (alt: {', '.join(alt)})")
            try:
                payload = {
                    "ParentBreakerName": label[:60],
                    "NumberOfPoles": poles.get(child, 3),
                    "NumberScheme": schemes.get(child, "Sequential"),
                    "MainBreakerSize": mains.get(child, 0),
                    # Repaired here for the same reason as the meter IP below: a panel
                    # is created once and never revisited except by this pass, so a
                    # correction to PANEL_VOLTAGE reached only panels that did not
                    # exist yet. Every board imported at 480 V kept it — while its
                    # main breaker, which is derived from the same number, moved.
                    "PanelVoltage": volts.get(child, 480),
                    # Panels are created once and then only ever revisited here, so
                    # this pass is the ONLY thing that can repair them. The estate
                    # renumber fixed fac_Device.PrimaryIP on all 531 devices and left
                    # every panel pointing at its old address — 30 of 46 still held a
                    # 192.168 meter IP that no longer exists anywhere, which reads on
                    # the panel page as a meter load of zero rather than as an error.
                    "PanelIPAddress": panel_ips.get(child, ""),
                }
                # A root panel has no parent to point at; sending ParentPanelID 0
                # would be asserting one.
                if pid:
                    payload["ParentPanelID"] = pid
                # The meter template, where the board has a meter. Sent ONLY when
                # this run knows one: an ATS and a genset have no metering point,
                # and pushing TemplateID 0 at them would also silently wipe a
                # template an operator had picked by hand.
                meter_tid = tmpl_id.get(panel_meter.get(child, ""))
                if meter_tid:
                    payload["TemplateID"] = meter_tid
                dcim.request("POST", f"/api/v1/powerpanel/{cid}", payload)
                counts["parentage" if pid else "panel_fields_only"] += 1
                if pid:
                    say(f"  ~ {child} fed from {label}")
            except RuntimeError as e:
                counts["parentage_failed"] += 1
                say(f"  ! parentage {child} <- {feed['parent']}: {e}")

    return dict(counts)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", default="",
                    help="read the estate from a topology JSON instead of the live API")
    ap.add_argument("--sim-url",  default="http://127.0.0.1:8001")
    ap.add_argument("--sim-user", default="admin")
    ap.add_argument("--sim-pass", default="admin1234")
    ap.add_argument("--dcim-url", default=os.environ.get("DCIM_URL", "http://localhost"))
    ap.add_argument("--dcim-user", default=os.environ.get("DCIM_USER", ""))
    ap.add_argument("--dcim-pass", default=os.environ.get("DCIM_PASS", ""))
    ap.add_argument("--only-dc", default="", help="restrict to one datacenter, e.g. DC1")
    ap.add_argument("--limit", type=int, default=0, help="cap devices created (smoke test)")
    ap.add_argument("--breaker-sql", default="",
                    help="write rack-PDU breaker assignments to this .sql file "
                         "(openDCIM has no API route for fac_PowerDistribution)")
    ap.add_argument("--post-import-sql", "--cdu-sql", dest="post_import_sql",
                    default="opendcim_post_import.sql",
                    help="write the rack-PDU SNMP templates to this .sql file "
                         "(openDCIM has no API route for fac_CDUTemplate). Without "
                         "running it, a CDU's Power Connections Status stays 'err'. "
                         "Pass an empty string to skip.")
    ap.add_argument("--topology-file", default="topologies/dual_dc_enterprise.json",
                    help="where to read interface NAMES from when running against the "
                         "live simulator (/api/devices does not serialise interfaces[])")
    ap.add_argument("--cabinet-max-kw", type=float, default=None,
                    help="override the per-cabinet design allowance (kW) for EVERY "
                         "room. Omit to use the per-room map in CABINET_DESIGN_KW: "
                         + ", ".join(f"{k}={v}" for k, v in CABINET_DESIGN_KW.items())
                         + f", other={CABINET_DESIGN_KW_DEFAULT}. A SITE DESIGN "
                           "INPUT — the simulator has no rack budget to export.")
    ap.add_argument("--cabinet-max-weight", type=float, default=CABINET_MAX_WEIGHT,
                    help=f"per-cabinet weight limit, kg (default {CABINET_MAX_WEIGHT}) "
                         f"— the raised floor's rating, a site design input")
    ap.add_argument("--no-network", action="store_true",
                    help="skip the network-cabling phase (production + management links)")
    ap.add_argument("--no-power", action="store_true",
                    help="skip the power-cord phase (objects only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="read the simulator and print the plan; write nothing")
    args = ap.parse_args()

    sim = None
    if args.from_file:
        devices = devices_from_file(args.from_file)
        print(f"topology file: {len(devices)} devices ({args.from_file})")
    else:
        try:
            sim = Sim.login(args.sim_url, args.sim_user, args.sim_pass)
            devices = sim.request("GET", "/api/devices", {"limit": 5000})["devices"]
        except Exception as e:
            print(f"cannot reach the simulator at {args.sim_url}: {e}\n"
                  f"Start it, or pass --from-file topologies/dual_dc_enterprise.json",
                  file=sys.stderr)
            return 2
        print(f"simulator: {len(devices)} devices")
        enriched = enrich_from_topology(devices, args.topology_file)
        if enriched:
            print(f"  enriched {enriched} from {args.topology_file} "
                  f"({', '.join(_TOPOLOGY_ONLY_FIELDS)} are not in /api/devices)")
        else:
            print(f"  ! {args.topology_file} matched no device — nameplate power, "
                  f"outlet counts and PSU counts will fall back to defaults",
                  file=sys.stderr)

    p = plan(devices, args.only_dc, args.cabinet_max_kw, args.cabinet_max_weight)
    if args.limit:
        p["devices"] = p["devices"][:args.limit]

    # Both the power and network phases read the same graph, so it is fetched once
    # and each phase is gated on its OWN flag. They were nested — --no-power also
    # silently disabled the network import, which is not what the flag says.
    if not (args.no_power and args.no_network):
        importable = {d["Label"] for d in p["devices"]}
        try:
            if args.from_file:
                with open(args.from_file, encoding="utf-8") as fh:
                    topo = json.load(fh)
                graph = {
                    "devices": [{"id": n["id"], "name": n["device"]["name"],
                                 "device_type": n["device"].get("device_type", "")}
                                for n in topo.get("nodes", [])],
                    "links": [{**e, "layer": e.get("layer"),
                               "supply_node": e.get("supply_node") or e.get("src"),
                               "load_node": e.get("load_node") or e.get("dst")}
                              for e in topo.get("edges", [])],
                }
            else:
                graph = sim.request("GET", "/api/topology/graph")
            if not args.no_power:
                p["cords"] = plan_cords(graph, importable)
                # Before the feed map only because it edits the same panel rows —
                # an RPP's PanelIPAddress comes from its branch meter, which is a
                # separate device and therefore a graph lookup.
                p["panel_meters"] = plan_panel_meters(graph, devices, p)
                p["panel_feeds"] = plan_panel_feeds(
                    graph, {x["PanelLabel"] for x in p["panels"]})
                p["breakers"] = plan_breakers(
                    graph, pdu_phase_map(devices), {x["PanelLabel"] for x in p["panels"]},
                    pdu_breaker_amps(devices))

            # Network cabling needs interface NAMES, and only the topology file has
            # them — /api/devices serialises interface_count but not interfaces[].
            # Without them every port this touches would be renamed to its port
            # number by updatePort's empty-Label fallback, so refuse the phase
            # rather than trade 1125 cables for 1125 destroyed port names.
            if not args.no_network:
                labels = iface_label_map(args.from_file or args.topology_file)
                if not labels:
                    print(f"  ! no interface names in "
                          f"{args.from_file or args.topology_file} — skipping the "
                          f"network layer. Pass --topology-file, or --no-network to "
                          f"silence this.", file=sys.stderr)
                else:
                    p["port_links"] = plan_port_links(graph, importable, labels)
                    missing = [l for l in p["port_links"] if not l["a_label"]]
                    if missing:
                        print(f"  ! {len(missing)} link(s) have no interface name for "
                              f"their A-side port and would be renamed — dropped.",
                              file=sys.stderr)
                        p["port_links"] = [l for l in p["port_links"] if l["a_label"]]
        except Exception as e:
            print(f"  ! could not read the topology graph: {e}", file=sys.stderr)

    print(f"\nplan{' for ' + args.only_dc if args.only_dc else ''}:")
    print(f"  manufacturers : {len(p['manufacturers'])}")
    print(f"  templates     : {len(p['templates'])}")
    print(f"  cabinets      : {len(p['cabinets'])}")
    print(f"  devices       : {len(p['devices'])}")
    print(f"  power panels  : {len(p['panels'])}")
    print(f"  power cords   : {len(p['cords'])}")
    if p.get("port_links"):
        by_layer = defaultdict(int)
        for lk in p["port_links"]:
            by_layer[lk["layer"]] += 1
        print(f"  network links : {len(p['port_links'])} ("
              + ", ".join(f"{k}={v}" for k, v in sorted(by_layer.items())) + ")")
    print(f"  panel feeds   : {len(p['panel_feeds'])}"
          + (f" ({sum(1 for f in p['panel_feeds'].values() if f['alternates'])}"
             f" with a second source openDCIM cannot hold)"
             if any(f['alternates'] for f in p['panel_feeds'].values()) else ""))
    if p["skipped"]:
        print("  skipped (no openDCIM object / no rack position):")
        for k, v in sorted(p["skipped"].items(), key=lambda x: -x[1]):
            print(f"      {v:>4}  {k}")

    by_type = defaultdict(int)
    for d in p["devices"]:
        by_type[d["DeviceType"]] += 1
    print("  device types  : " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))

    if p.get("cdu_templates"):
        unknown = [m for m, r in p["cdu_templates"].items() if not r["_known_sku"]]
        print(f"  cdu templates : {len(p['cdu_templates'])} rack-PDU SKUs"
              + (f" — {len(unknown)} not in the outlet catalog" if unknown else ""))

    sensors = p.get("sensor_templates") or {}
    if sensors:
        wired = sum(1 for r in sensors.values() if r["_known_sku"])
        plant = sum(1 for r in sensors.values() if r["_plant"])
        other = len(sensors) - wired - plant
        print(f"  sensor tmpls  : {len(sensors)} SKUs — {wired} wired"
              + (f", {plant} plant instruments left unwired (BMS points)" if plant else "")
              + (f", {other} UNMAPPED — no OIDs, no readings" if other else ""))

    # Gated on either, not on the CDUs alone: the same file now carries the sensor
    # templates, and an estate with no rack PDUs would otherwise write nothing and
    # say nothing about it.
    panel_meters = p.get("panel_meters") or {}
    if panel_meters:
        metered = sum(1 for m in panel_meters.values() for _ in m["Panels"])
        unmetered = sorted({x["DeviceType"] for x in p["panels"]
                            if not x.get("_meter_model")})
        print(f"  panel meters  : {len(panel_meters)} SKUs on {metered}/"
              f"{len(p['panels'])} panels"
              + (f" — no metering point on: {', '.join(unmetered)}"
                 if unmetered else ""))
        for _m, _r in sorted(panel_meters.items()):
            if _r.get("VoltageConflict"):
                print(f"                  ! {_m} meters buses at "
                      f"{_r['Voltage']} V and "
                      f"{', '.join(str(v) for v in sorted(_r['VoltageConflict']))} V"
                      f" — openDCIM holds ONE voltage per template; "
                      f"{_r['Voltage']} V was kept", file=sys.stderr)

    if (p.get("cdu_templates") or sensors or panel_meters) and args.post_import_sql:
        with open(args.post_import_sql, "w", encoding="utf-8") as fh:
            fh.write(post_import_sql(p["cdu_templates"], sensors, panel_meters))
        print(f"                  wrote {args.post_import_sql} — RUN IT against the openDCIM\n"
              f"                  database; it carries everything the REST API\n"
              f"                  cannot write; until it runs a CDU Status stays 'err'\n"
              f"                  and the cabinet Environmental Sensors panel is empty.")

    if p.get("breakers"):
        over = [b for b in p["breakers"] if b["overflow"]]
        three = sum(1 for b in p["breakers"] if b["phases"] == 3)
        print(f"  breakers      : {len(p['breakers'])} rack PDUs on RPP poles "
              f"({three} three-pole, {len(p['breakers']) - three} single-pole)"
              + (f" — {len(over)} PAST POLE 42" if over else ""))
        if args.breaker_sql:
            with open(args.breaker_sql, "w", encoding="utf-8") as fh:
                fh.write(breaker_sql(p["breakers"]))
            print(f"                  wrote {args.breaker_sql}")

    if args.dry_run:
        print("\n--dry-run: nothing written to openDCIM (the .sql files above are "
              "local artifacts).")
        return 0

    if not args.dcim_user:
        print("\nNo openDCIM credential. Pass --dcim-user/--dcim-pass (or DCIM_USER/DCIM_PASS).\n"
              "The account needs Site Administrator: cabinet and panel creation check it.",
              file=sys.stderr)
        return 2

    dcim = Http(args.dcim_url, args.dcim_user, args.dcim_pass)
    print("\napplying:")
    counts = apply(dcim, p)
    print("\ncreated: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing new"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

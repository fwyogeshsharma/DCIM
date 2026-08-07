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

NEITHER CAN IT WRITE A RACK PDU'S SNMP TEMPLATE. A CDU's outlet OIDs live in
fac_CDUTemplate, a shadow row openDCIM auto-creates alongside the device template but
leaves empty, and no REST route touches that table. Until it is filled, the Status
column on a CDU's Power Connections panel can never leave 'err' and the PDU is never
polled for wattage. This tool emits that as SQL (--cdu-sql, on by default) next to the
breaker SQL; run both against the openDCIM database.

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
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rack_capacity import device_u_height  # noqa: E402
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

# Feed order, upstream first — a panel's ParentPanelID must exist before it is set.
PANEL_ORDER = ["utility_feed", "generator", "switchgear", "ats", "mcc", "ups",
               "rpp", "mpp"]

# Nominal panel voltages. Wye 400/230 V would be the other common build; this estate
# is modelled on 480 V distribution with 415/240 V to the racks.
PANEL_VOLTAGE = {
    "utility_feed": 480, "generator": 480, "switchgear": 480, "ats": 480,
    "mcc": 480, "ups": 480, "rpp": 415, "mpp": 480,
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


def plan(devices: list[dict], only_dc: str = "") -> dict:
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
                "NumPorts": int(d.get("interface_count") or 0),
                "PSCount": len(d.get("psus") or []) or (0 if dt in ZERO_U_TYPES else 2),
                "Wattage": watts,
            }
        elif watts > prev["Wattage"]:
            prev["Wattage"] = watts

        loc = cabinet_location(d)
        cabinets.setdefault((dc, loc), {
            "DataCenter": dc, "Location": loc, "CabinetHeight": 42,
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
            "NominalWatts": int(d.get("power_watts") or 0),
        })

    panel_rows = []
    for d in sorted(panels, key=lambda x: PANEL_ORDER.index(x["device_type"])
                    if x["device_type"] in PANEL_ORDER else 99):
        panel_rows.append({
            "PanelLabel": d["name"], "DataCenter": d.get("datacenter", ""),
            "DeviceType": d["device_type"],
            "PanelVoltage": PANEL_VOLTAGE.get(d["device_type"], 480),
            "NumberOfPoles": PANEL_POLES.get(d["device_type"], 3),
            "PanelIPAddress": d.get("mgmt_ip") or d.get("ip_address") or "",
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
        "cdu_templates": plan_cdu_templates(devices, only_dc),
    }


# A branch panelboard has poles; a switchboard, ATS, UPS or genset does not — its
# "poles" here are just the three phases of its main. Getting this wrong makes
# openDCIM's panel schedule offer 3 breaker positions on a 42-pole RPP.
PANEL_POLES = {"rpp": 42, "mpp": 42}


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


def plan_breakers(graph: dict, pdu_phases: dict, panel_names: set) -> list[dict]:
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
    cursor: dict = {}          # (panel, parity) -> next free pole on that side
    for panel in sorted(fed):
        pdus = sorted(set(fed[panel]))
        # Side comes from the PDU's own name (PDUA/PDUB); the panel's letter is the
        # fallback. No panel in this estate mixes sides, but do not assume it.
        for pdu in pdus:
            side = pdu[3:4].upper() if pdu[:3].upper() == "PDU" else panel[3:4].upper()
            start_parity = 1 if side == "A" else 2
            nxt = cursor.setdefault((panel, start_parity), start_parity)
            poles_needed = 3 if pdu_phases.get(pdu, 1) == 3 else 1
            poles = [nxt + 2 * i for i in range(poles_needed)]
            cursor[(panel, start_parity)] = poles[-1] + 2
            rows.append({
                "pdu": pdu, "panel": panel, "poles": poles,
                "phases": poles_needed, "breaker_a": RACK_BREAKER_A,
                "overflow": poles[-1] > 42,
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
    "VersionOID":      "",
    #  ...5.11.0  pduRealPower, in WATTS, published for every PDU whatever the
    # vendor — the vendor-specific power tables are not. SingleOIDWatts with a
    # multiplier of 1 is therefore an exact read, no unit conversion.
    "OID1":            "1.3.6.1.4.1.99999.5.11.0",
    "OID2":            "",
    "OID3":            "",
    "ProcessingProfile": "SingleOIDWatts",
    "Multiplier":      "1",
    "ATSStatusOID":    "",
    "ATSDesiredResult": "",
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
        n_outlets = len(d.get("outlets") or []) or (spec[0] + spec[1] if spec else 0)
        rows[model] = {
            **CDU_SIM_PROFILE,
            "Model": model,
            # Managed=1 is not cosmetic: PowerDistribution::UpdateStats() joins on
            # `b.Managed=true`, so an unmanaged template is never polled at all.
            "Managed": 1,
            "ATS": 0,
            "SNMPVersion": "2c",
            "Voltage": spec[4] if spec else 230,
            "Amperage": spec[3] if spec else 0,
            "NumOutlets": n_outlets,
            # Carried for the report only — what a load actually sees at the outlet.
            "_outlet_v": outlet_voltage(model),
            "_known_sku": bool(spec),
        }
    return rows


def _sq(value) -> str:
    """Single-quote a value for MySQL. These strings are OIDs and model names."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def cdu_template_sql(rows: dict) -> str:
    """SQL for the rack-PDU SNMP templates.

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
        "-- Switches have a Status column too; add DeviceType='Switch' below to",
        "-- enroll those as well.",
        "INSERT IGNORE INTO fac_Tags (Name) VALUES ('Poll');",
        "INSERT IGNORE INTO fac_DeviceTags (DeviceID, TagID)",
        "  SELECT d.DeviceID, t.TagID FROM fac_Device d, fac_Tags t",
        "  WHERE d.DeviceType='CDU' AND t.Name='Poll';",
        "",
    ]
    out += outlet_count_sql(rows)
    return "\n".join(out) + "\n"


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
        "",
    ]
    for r in rows:
        poles = ",".join(str(x) for x in r["poles"])
        out.append(
            "UPDATE fac_PowerDistribution pd "
            "JOIN fac_PowerPanel pp ON pp.PanelLabel = '{panel}' "
            "SET pd.PanelID = pp.PanelID, pd.PanelPole = '{poles}', "
            "pd.BreakerSize = {amps}, pd.InputAmperage = {amps} "
            "WHERE pd.Label = '{pdu}';".format(
                panel=r["panel"], poles=poles, amps=r["breaker_a"], pdu=r["pdu"])
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
    created = False
    for model, t in p["templates"].items():
        if model in tmpl_id:
            continue
        dcim.request("PUT", f"/api/v1/devicetemplate/{urllib.parse.quote(model)}", {
            "Model": model, "ManufacturerID": mfg_id.get(t["Manufacturer"], 0),
            "DeviceType": t["DeviceType"], "Height": t["Height"],
            "NumPorts": t["NumPorts"], "PSCount": t["PSCount"], "Wattage": t["Wattage"],
        })
        counts["templates"] += 1
        created = True
        say(f"  + template {model} ({t['DeviceType']}, {t['Height']}U, {t['Wattage']} W)")
    if created:
        tmpl_id = reload_ids("/api/v1/devicetemplate", "devicetemplate", "Model", "TemplateID")

    missing_tmpl = [m for m in p["templates"] if not tmpl_id.get(m)]
    if missing_tmpl:
        raise SystemExit("templates did not come back with IDs: " + ", ".join(missing_tmpl[:5]))

    # 3. Cabinets
    def reload_cabinets() -> dict:
        rows = dcim.request("GET", "/api/v1/cabinet").get("cabinet", [])
        return {(str(r.get("DataCenterID")), str(r.get("Location", "")).strip()): r["CabinetID"]
                for r in rows}

    cab_id = reload_cabinets()
    created = False
    for (dc, loc), c in sorted(p["cabinets"].items()):
        if (str(dc_id[dc]), loc) in cab_id:
            continue
        dcim.request("PUT", "/api/v1/cabinet", {
            "DataCenterID": dc_id[dc], "Location": loc,
            "CabinetHeight": c["CabinetHeight"], "Notes": c["Notes"],
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
            # PowerSupplyCount is NOT repaired here — see outlet_count_sql(). Raising
            # it through the API half-applies: UpdateDevice writes the new count, then
            # its reconciler calls PowerPorts::createPorts($id, true), which re-INSERTs
            # ports 1..N including the ones already there. The $update_existing flag
            # only suppresses openDCIM's own error LOGGING; the duplicate INSERT still
            # raises a PDOException under ERRMODE_EXCEPTION, so the request 500s with
            # "Duplicate entry '<id>-1' for key 'PRIMARY'" after the count is committed
            # and before a single new port exists.
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
            "FirstPortNum": d["FirstPortNum"],
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

    # 5. Power panels, upstream first so a parent exists before a child names it.
    have = _index(dcim.request("GET", "/api/v1/powerpanel").get("powerpanel", []), "PanelLabel")
    for pan in p["panels"]:
        if pan["PanelLabel"] in have:
            continue
        dcim.request("PUT", f"/api/v1/powerpanel/{urllib.parse.quote(pan['PanelLabel'])}", {
            "PanelLabel": pan["PanelLabel"], "PanelVoltage": pan["PanelVoltage"],
            "NumberOfPoles": pan["NumberOfPoles"], "PanelIPAddress": pan["PanelIPAddress"],
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
        for child, feed in sorted(feeds.items()):
            cid, pid = panel_id.get(child), panel_id.get(feed["parent"])
            if not cid or not pid:
                counts["parentage_skipped"] += 1
                continue
            alt = feed["alternates"]
            # ParentBreakerName is free text in openDCIM ("for switchgear, this
            # usually won't be numbered"), so it is the honest place to record the
            # source openDCIM's single-parent model cannot hold.
            label = feed["parent"] if not alt else f"{feed['parent']} (alt: {', '.join(alt)})"
            try:
                dcim.request("POST", f"/api/v1/powerpanel/{cid}", {
                    "ParentPanelID": pid,
                    "ParentBreakerName": label[:60],
                    "NumberOfPoles": poles.get(child, 3),
                })
                counts["parentage"] += 1
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
    ap.add_argument("--cdu-sql", default="opendcim_cdu_templates.sql",
                    help="write the rack-PDU SNMP templates to this .sql file "
                         "(openDCIM has no API route for fac_CDUTemplate). Without "
                         "running it, a CDU's Power Connections Status stays 'err'. "
                         "Pass an empty string to skip.")
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

    p = plan(devices, args.only_dc)
    if args.limit:
        p["devices"] = p["devices"][:args.limit]

    # Cords need the power layer, which only the graph endpoint carries. A topology
    # file has the same edges, so both sources work.
    if not args.no_power:
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
            p["cords"] = plan_cords(graph, importable)
            p["panel_feeds"] = plan_panel_feeds(
                graph, {x["PanelLabel"] for x in p["panels"]})
            p["breakers"] = plan_breakers(
                graph, pdu_phase_map(devices), {x["PanelLabel"] for x in p["panels"]})
        except Exception as e:
            print(f"  ! could not read the power layer: {e}", file=sys.stderr)

    print(f"\nplan{' for ' + args.only_dc if args.only_dc else ''}:")
    print(f"  manufacturers : {len(p['manufacturers'])}")
    print(f"  templates     : {len(p['templates'])}")
    print(f"  cabinets      : {len(p['cabinets'])}")
    print(f"  devices       : {len(p['devices'])}")
    print(f"  power panels  : {len(p['panels'])}")
    print(f"  power cords   : {len(p['cords'])}")
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
        if args.cdu_sql:
            with open(args.cdu_sql, "w", encoding="utf-8") as fh:
                fh.write(cdu_template_sql(p["cdu_templates"]))
            print(f"                  wrote {args.cdu_sql} — RUN IT against the openDCIM\n"
                  f"                  database; the API cannot write fac_CDUTemplate, and\n"
                  f"                  until it runs every CDU Status light stays 'err'.")

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

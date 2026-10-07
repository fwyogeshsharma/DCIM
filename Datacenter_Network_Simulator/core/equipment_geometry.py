"""Physical geometry of the estate: equipment footprints, how each device is
mounted, how high it sits, which way it faces, and where rooms stand in their
building.

None of this is telemetry. A real device never reports its footprint or the floor
it stands on; in production this is the DCIM's asset layer, drawn from CAD/BIM or
entered by hand. The simulator carries it because it is standing in for the
physical world, and it publishes it (``/api/topology/export``, ``/api/floorplan``)
so a DCIM can import it ONCE as an asset model - the same contract as
``floor_x``/``floor_y`` on Device.

Coordinate frame (shared with core/hall_geometry.py):
  * room-local metres, room corner = (0, 0); +x along the rows, +y across them
  * compass: N = -y, E = +x, S = +y, W = -x
  * ``facing_deg`` = direction the device FRONT faces, degrees clockwise from N
    (N 0, E 90, S 180, W 270) - for a rack, the intake side
  * heights are metres above the finished floor of the room (top of the raised
    floor in a white-space hall, the slab in a plant room)
  * a room's ``origin`` places its (0, 0) corner in BUILDING coordinates on its
    level; a level's ``elevation_m`` is its finished-floor height above grade
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

# ── vertical constants ──────────────────────────────────────────────────────
U_M = 0.04445            # one rack unit (EIA-310)
RACK_BASE_M = 0.10       # U1's bottom edge above the floor (plinth/castors)
RACK_HEIGHT_M = 2.0      # a 42U cabinet stands ~2.0 m
FLOOR_TO_FLOOR_M = 5.0   # slab to slab: 0.6 m raised floor + ~3 m clear + ceiling plenum

# Default centre heights for things that are not in a rack. Wall T/RH
# transmitters go at ~1.5 m (ASHRAE TC9.9 room-air reference practice); pipe
# instruments and valve actuators sit on headers run at about chest height in a
# plant room; DIN-rail gear lives in a control panel at eye level.
_POINT_HEIGHT_M = {"wall": 1.5, "pipe": 1.2, "panel": 1.5, "ceiling": 2.8}

# ── footprints ──────────────────────────────────────────────────────────────
# (width, depth, height) in metres, in the device's own frame: WIDTH is the face
# you stand in front of, DEPTH runs away from you. ``facing_deg`` rotates it.
#
# "datasheet" entries were checked against the vendor's published dimensions;
# "class" entries are class estimates for that kind of equipment and should be
# replaced with the SKU's submittal drawing before anyone plans a room from them.
FOOTPRINTS: dict[str, tuple[float, float, float, str]] = {
    # Vertiv Liebert EXL S1 1000-1200 kVA site planning data: 2654 x 914 x 2009 mm.
    "Vertiv Liebert EXL S1 1200kVA": (2.65, 0.91, 2.01, "datasheet"),
    # Cat 3516B 2000 kW 60 Hz open genset: 260 x 104 x 120 in (L x W x H).
    "Caterpillar 3516B": (2.64, 6.60, 3.05, "datasheet"),
    # Carrier AquaEdge 19DV, smallest frame (F2, 1-pass): 15'6" x 8'4" x 9'3".
    "Carrier 19DV 800kW": (2.55, 4.72, 2.82, "datasheet"),
    # BAC PT2 3-cell line is 27'2" x 7'4" -> ~2.76 m per cell; height 13-14 ft.
    "BAC PT2 Series": (2.76, 2.24, 4.20, "datasheet"),
    # Liebert PCW large frames are published at 1750/2050/2550 x 890 mm, 1970 mm
    # tall; a 100 kW unit is taken as the 1750 frame (frame per kW not verified).
    "Vertiv Liebert PCW 100kW": (1.75, 0.89, 1.97, "datasheet"),
    "Eaton Magnum DS 4000A": (3.60, 1.52, 2.29, "class"),            # 4 sections
    "ASCO 7000 Paralleling Switchgear": (4.80, 1.52, 2.29, "class"),  # 2 gen + bus + feeders
    "ASCO 7000 Series 4000A": (1.12, 1.52, 2.29, "class"),
    "Eaton Freedom 2100 MCC 1600A": (2.54, 0.51, 2.29, "class"),     # 5 x 20 in sections
    "APC Galaxy RPP 160A": (0.60, 1.07, 2.00, "class"),
    "APC Galaxy RPP 80A": (0.60, 1.07, 2.00, "class"),
    "Grundfos NB 100-200": (0.60, 1.30, 0.80, "class"),              # end-suction on baseplate
    "Grundfos TP 100-360": (0.50, 0.50, 1.30, "class"),              # vertical in-line
    "Eaton Pow-R-Line 3a 150A": (0.51, 0.15, 1.52, "class"),         # wall panelboard
}

# Fallback per type, for SKUs not in the table above (fleet clones, new models).
_TYPE_FOOTPRINTS: dict[str, tuple[float, float, float, str]] = {
    "generator":     (2.60, 6.50, 3.00, "class"),
    "ups":           (2.60, 0.90, 2.00, "class"),
    "switchgear":    (3.60, 1.50, 2.30, "class"),
    "ats":           (1.10, 1.50, 2.30, "class"),
    "mcc":           (2.50, 0.50, 2.30, "class"),
    "mpp":           (0.50, 0.15, 1.50, "class"),
    "rpp":           (0.60, 1.07, 2.00, "class"),
    "crah":          (1.75, 0.89, 1.97, "class"),
    "chiller":       (2.50, 4.70, 2.80, "class"),
    "pump":          (0.60, 1.20, 0.90, "class"),
    "cooling_tower": (2.80, 2.20, 4.20, "class"),
}

# ── mounting ────────────────────────────────────────────────────────────────
#   rack        in a rack by U (rack_unit > 0)
#   zero_u      vertical in the rack's rear channel (rack PDUs)
#   rack_front  probe on the front door/rail at its rack_unit (intake air)
#   rack_rear   probe on the rear (exhaust air)
#   floor       free-standing on the floor, has a footprint
#   wall        on a wall (T/RH transmitters, panelboards)
#   pipe        on a pipe or header (immersion probes, flow meters, valves)
#   panel       inside another enclosure (meters, DIN-rail gateways)
#   underfloor  in the raised-floor plenum
RACK_MOUNTS = frozenset({"rack", "zero_u", "rack_front", "rack_rear"})
POINT_MOUNTS = frozenset({"wall", "pipe", "panel", "ceiling", "underfloor"})

_FLOOR_TYPES = frozenset({"generator", "ups", "switchgear", "ats", "mcc", "rpp",
                          "crah", "chiller", "pump", "cooling_tower"})
_DEFAULT_MOUNT = {
    "mpp": "wall",
    "valve": "pipe",
    "energy_monitor": "panel",   # Verdigris EV2: CTs clamped inside the panel it meters
    "utility_feed": "panel",     # revenue meter in the service-entrance section
    "modbus_gateway": "panel",
    "bacnet_router": "panel",
}


def _get(dev: Any, key: str, default=None):
    if isinstance(dev, dict):
        v = dev.get(key, default)
    else:
        v = getattr(dev, key, default)
    return default if v is None else v


def _dtype(dev: Any) -> str:
    t = _get(dev, "device_type", "")
    return getattr(t, "value", t) or ""


def effective_mount(dev: Any) -> str:
    """How the device is physically held. An explicit ``mounting`` wins; otherwise
    it follows from the type and whether the device has a rack unit."""
    m = _get(dev, "mounting", "")
    if m:
        return m
    t = _dtype(dev)
    if t in _FLOOR_TYPES:
        return "floor"
    if t in _DEFAULT_MOUNT:
        return _DEFAULT_MOUNT[t]
    if t in ("pdu", "floor_pdu") and not _get(dev, "rack_unit", 0):
        return "zero_u" if t == "pdu" else "floor"
    if t == "sensor":
        return "rack_front" if _get(dev, "rack_unit", 0) else "wall"
    return "rack" if _get(dev, "rack_unit", 0) else ""


def footprint(dev: Any) -> Optional[dict]:
    """``{width, depth, height, basis}`` in metres for floor-standing and
    wall-hung equipment; None for rack gear and point instruments, whose size is
    the rack's or negligible."""
    mount = effective_mount(dev)
    if mount not in ("floor", "wall"):
        return None
    model = _get(dev, "model_name", "") or ""
    fp = FOOTPRINTS.get(model) or _TYPE_FOOTPRINTS.get(_dtype(dev))
    if fp is None:
        return None
    w, d, h, basis = fp
    return {"width": w, "depth": d, "height": h, "basis": basis}


def facing_deg(dev: Any) -> Optional[float]:
    """Compass direction the front faces. An explicit ``rotation_deg`` wins; rack
    gear inherits its rack's facing."""
    r = _get(dev, "rotation_deg", None)
    if r is not None:
        return float(r) % 360.0
    f = _get(dev, "rack_facing", "")
    return {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}.get(f)


def mount_height_m(dev: Any, u_height: int = 1) -> Optional[float]:
    """Height of the device's CENTRE above the finished floor."""
    mount = effective_mount(dev)
    ru = int(_get(dev, "rack_unit", 0) or 0)
    if mount in ("rack", "rack_front", "rack_rear") and ru > 0:
        return round(RACK_BASE_M + (ru - 1 + max(1, u_height) / 2.0) * U_M, 3)
    if mount == "zero_u":
        return round(RACK_BASE_M + RACK_HEIGHT_M * 0.45, 3)
    if mount == "floor":
        fp = footprint(dev)
        return round(fp["height"] / 2.0, 3) if fp else None
    if mount == "underfloor":
        return -0.3
    if mount in _POINT_HEIGHT_M:
        return _POINT_HEIGHT_M[mount]
    return None


def geometry_fields(dev: Any, u_height: Optional[int]) -> dict:
    """Derived placement fields a device publishes alongside its stored ones."""
    return {
        "mount": effective_mount(dev) or None,
        "u_height": u_height,
        "footprint_m": footprint(dev),
        "facing_deg": facing_deg(dev),
        "mount_height_m": mount_height_m(dev, u_height or 1),
    }


# ── building levels ─────────────────────────────────────────────────────────

def _level_rank(name: str) -> tuple:
    """Sort key for a level label: basements, G, 1..n, then the roof last."""
    s = str(name).strip()
    if s.upper() in ("G", "GF", "GROUND", "0"):
        return (0, 0)
    if s.upper().startswith("B") and s[1:].isdigit():
        return (0, -int(s[1:]))
    if s.isdigit():
        return (0, int(s))
    if s.lower() == "roof":
        return (2, 0)
    return (1, s)


def _room_level(key: str, room: dict, floors: dict) -> str:
    if room.get("level") not in (None, ""):
        return str(room["level"])
    c = floors.get(key)
    if c:
        return min(c.most_common(), key=lambda kv: (-kv[1], kv[0]))[0]
    return "G"


def _plan_extent(room: dict) -> tuple[float, float]:
    w, d = float(room.get("width_m") or 0), float(room.get("depth_m") or 0)
    rot = float(room.get("rotation_deg") or 0) % 180
    return (d, w) if abs(rot - 90) < 1e-6 else (w, d)


def complete_building(floorplan: dict, devices: Iterable[Any]) -> dict:
    """Return a copy of ``floorplan`` in which every room has a level and an
    origin in its building, and every datacenter has a ``buildings`` entry with
    its levels (name, elevation) and plan outline.

    Curated rooms carry an explicit placement (tools/layout_buildings.py). A hall
    the fleet opens at runtime does not: it takes its level from the floor its
    devices are on and is set down beside the rooms already on that level. Levels
    are always DERIVED from the rooms, so a new floor pushes the roof up instead of
    landing on it. Pure: the input is never modified."""
    if not floorplan or not floorplan.get("rooms"):
        return floorplan
    floors: dict = defaultdict(Counter)
    for d in devices:
        dc, room = _get(d, "datacenter", ""), _get(d, "room", "")
        if dc and room:
            floors[f"{dc}/{room}"][str(_get(d, "floor", "") or "")] += 1
    for c in floors.values():
        c.pop("", None)

    out = dict(floorplan)
    rooms = {k: dict(v) for k, v in floorplan.get("rooms", {}).items()}
    buildings = {dc: dict(b) for dc, b in (floorplan.get("buildings") or {}).items()}

    by_dc: dict = defaultdict(list)
    for key in sorted(rooms):
        r = rooms[key]
        r["level"] = _room_level(key, r, floors)
        by_dc[r.get("datacenter") or key.split("/", 1)[0]].append(key)

    for dc, keys in sorted(by_dc.items()):
        b = buildings.setdefault(dc, {})
        b.setdefault("name", f"{dc} Building")
        f2f = float(b.get("floor_to_floor_m") or FLOOR_TO_FLOOR_M)
        b["floor_to_floor_m"] = f2f

        # Auto-place rooms without an origin to the +x side of what is already on
        # their level - deterministic, never overlapping a placed room.
        reach: dict = defaultdict(float)
        for k in keys:
            r = rooms[k]
            if isinstance(r.get("origin"), dict):
                w, _ = _plan_extent(r)
                reach[r["level"]] = max(reach[r["level"]], float(r["origin"].get("x", 0)) + w)
        for k in keys:
            r = rooms[k]
            if not isinstance(r.get("origin"), dict):
                r["origin"] = {"x": round(reach[r["level"]], 3), "y": 0.0}
                r.setdefault("rotation_deg", 0)
                r["placement"] = "auto"
                w, _ = _plan_extent(r)
                reach[r["level"]] += w

        names = sorted({rooms[k]["level"] for k in keys}, key=_level_rank)
        top = max([_level_rank(n)[1] for n in names if _level_rank(n)[0] == 0] or [0])
        levels = []
        for n in names:
            o = _level_rank(n)[1] if _level_rank(n)[0] == 0 else (top := top + 1)
            levels.append({"name": n, "ordinal": o, "elevation_m": round(o * f2f, 3)})
        b["levels"] = levels

        xs, ys = [], []
        for k in keys:
            r = rooms[k]
            if _level_rank(r["level"])[0] == 2:
                continue                      # the roof sits ON the building, not beside it
            w, d = _plan_extent(r)
            ox, oy = float(r["origin"]["x"]), float(r["origin"]["y"])
            xs += [ox, ox + w]
            ys += [oy, oy + d]
        if xs:
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            b["outline_m"] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            b["extent_m"] = {"width": round(x1 - x0, 3), "depth": round(y1 - y0, 3)}

    out["rooms"] = rooms
    out["buildings"] = buildings
    return out

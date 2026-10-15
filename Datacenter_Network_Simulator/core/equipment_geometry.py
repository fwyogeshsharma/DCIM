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

# A room whose "level" is this stands in the compound, not in the building: a
# generator yard, a substation, a cooling yard. It has an origin in the same
# plan frame as the building's rooms (so the building's corner is the datum) and
# no walls, slab or floor-to-floor of its own (dcim-platform docs/27 §8c).
OUTDOOR_LEVEL = "Site"

# Indoor fabric added south of every level's rooms (docs/27 §8b.3): a corridor
# joining the rooms, a stair-and-lift core at the east end on every level, and
# on the ground floor a lobby at the entrance and a loading dock beside it.
CORRIDOR_M = 2.4
SERVICE_BAND_M = 3.6
CORE_W_M = 3.0
LOBBY_W_M = 6.0
DOCK_W_M = 6.0
DOOR_W_M = 1.2
#: Rooms whose door the BMS watches (a contact and a held-open alarm). Drawn
#: as a monitored door; every other opening is drawing only.
MONITORED_DOOR_ROOMS = ("Server Hall", "Network Room", "UPS Room", "Meet-Me Room")

# The fenced compound around the building (docs/27 §8c.3): utility side west,
# generator side east, the road and gate south, plant water north. Margins are
# clear distances from the building's outline.
SITE_WEST_M, SITE_EAST_M, SITE_SOUTH_M, SITE_NORTH_M = 18.0, 22.0, 14.0, 10.0
#: An outdoor room's name decides which kind of zone it is drawn as.
_OUTDOOR_KIND = (("generator", "generator_yard"), ("substation", "substation"),
                 ("cooling", "cooling_yard"), ("fuel", "fuel_tank"), ("water", "water_tank"))

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
    # A 2 MVA oil-immersed ground-mounted unit with radiators: tank plus
    # radiator banks on both long sides, cable boxes at one end.
    "Schneider Minera 2000kVA ONAN 11/0.415kV": (2.40, 1.60, 2.20, "class"),
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
    "transformer":   (2.40, 1.60, 2.20, "class"),
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
                          "crah", "chiller", "pump", "cooling_tower", "transformer"})
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
    if s.lower() == OUTDOOR_LEVEL.lower():
        return (-1, 0)
    return (1, s)


def is_outdoor(level) -> bool:
    return str(level or "").strip().lower() == OUTDOOR_LEVEL.lower()


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
        # their level - deterministic, never overlapping a placed room. Outdoor
        # rooms are not on a level of the building; one without a curated
        # origin is set down east of everything indoors.
        indoor = [k for k in keys if not is_outdoor(rooms[k]["level"])]
        reach: dict = defaultdict(float)
        for k in indoor:
            r = rooms[k]
            if isinstance(r.get("origin"), dict):
                w, _ = _plan_extent(r)
                reach[r["level"]] = max(reach[r["level"]], float(r["origin"].get("x", 0)) + w)
        for k in indoor:
            r = rooms[k]
            if not isinstance(r.get("origin"), dict):
                r["origin"] = {"x": round(reach[r["level"]], 3), "y": 0.0}
                r.setdefault("rotation_deg", 0)
                r["placement"] = "auto"
                w, _ = _plan_extent(r)
                reach[r["level"]] += w
        east = max(reach.values(), default=0.0) + 4.0
        for k in keys:
            r = rooms[k]
            if is_outdoor(r["level"]) and not isinstance(r.get("origin"), dict):
                r["origin"] = {"x": round(east, 3), "y": 0.0}
                r.setdefault("rotation_deg", 0)
                r["placement"] = "auto"
                east += _plan_extent(r)[0] + 2.0

        names = sorted({rooms[k]["level"] for k in indoor}, key=_level_rank)
        top = max([_level_rank(n)[1] for n in names if _level_rank(n)[0] == 0] or [0])
        levels = []
        for n in names:
            o = _level_rank(n)[1] if _level_rank(n)[0] == 0 else (top := top + 1)
            levels.append({"name": n, "ordinal": o, "elevation_m": round(o * f2f, 3)})
        b["levels"] = levels

        xs, ys = [], []
        for k in indoor:
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


# ── the site: fabric and compound (docs/27 §8b.3, §8c.3) ─────────────────────

def _rect(x: float, y: float, w: float, d: float) -> list:
    r = lambda v: round(float(v), 2)  # noqa: E731
    return [[r(x), r(y)], [r(x + w), r(y)], [r(x + w), r(y + d)], [r(x), r(y + d)]]


def _free_cells(boxes: list, x0: float, y0: float, x1: float, y1: float) -> list:
    """The floor of a level's rooms' zone (``x0..x1`` by ``y0..y1``) that no
    room covers, as rectangles: the zone split on every room edge, each cell
    no room holds, runs of cells merged along x and then stacked along y where
    their x-extent matches. Slivers under 0.6 m are not floor anyone walks."""
    eps = 1e-6
    xs = sorted({x0, x1, *(b[0] for b in boxes), *(b[2] for b in boxes)})
    ys = sorted({y0, y1, *(b[1] for b in boxes), *(b[3] for b in boxes)})
    xs = [x for x in xs if x0 - eps <= x <= x1 + eps]
    ys = [y for y in ys if y0 - eps <= y <= y1 + eps]
    rows: list = []
    for j in range(len(ys) - 1):
        cy = (ys[j] + ys[j + 1]) / 2
        run = None
        for i in range(len(xs) - 1):
            cx = (xs[i] + xs[i + 1]) / 2
            free = not any(b[0] < cx < b[2] and b[1] < cy < b[3] for b in boxes)
            if free and run is not None and abs(run[2] - xs[i]) < eps:
                run[2] = xs[i + 1]
            elif free:
                if run is not None:
                    rows.append(run)
                run = [xs[i], ys[j], xs[i + 1], ys[j + 1]]
            elif run is not None:
                rows.append(run)
                run = None
        if run is not None:
            rows.append(run)
    merged: list = []
    for c in rows:
        for m in merged:
            if abs(m[0] - c[0]) < eps and abs(m[2] - c[2]) < eps and abs(m[3] - c[1]) < eps:
                m[3] = c[3]
                break
        else:
            merged.append(list(c))
    return [tuple(m) for m in merged if m[2] - m[0] >= 0.6 - eps and m[3] - m[1] >= 0.6 - eps]


def _door_opening(k: str, boxes: dict, x0: float, y0: float, x1: float, y1: float) -> tuple:
    """Where a room's door goes, and whether it is in the building's face.

    The first of the room's south, east, north and west walls with free floor
    outside it takes a full-width door; if none has room for one, the first
    with a gap a single 0.9 m leaf fits. The corridor band south of the rooms'
    zone counts as floor, so a room on it opens onto it as before. The door is
    centred on the free run of its wall, which is what puts the UPS room's in
    the gap beside the plant rather than behind the mechanical room's wall. A
    room with no such wall - landlocked, like a network room boxed in by the
    hall it serves - opens into its neighbour that is a hall, else the one it
    shares the longest wall with. A door in the building's outer face is the
    last resort, and the only external one."""
    eps = 1e-6
    bx0, by0, bx1, by1 = boxes[k]
    others = [(kk, b) for kk, b in boxes.items() if kk != k]

    def free_run(wall: str):
        # the wall's span, and what stands just outside it along that span
        if wall in ("S", "N"):
            lo, hi = bx0, bx1
            if wall == "S" and abs(by1 - y1) < eps:
                return (lo, hi)                       # the corridor band
            out = by1 + 0.25 if wall == "S" else by0 - 0.25
            if out < y0 or out > y1:
                return None                           # the building's face
            taken = [(max(lo, b[0]), min(hi, b[2])) for _, b in others if b[1] < out < b[3]]
        else:
            lo, hi = by0, by1
            out = bx1 + 0.25 if wall == "E" else bx0 - 0.25
            if out < x0 or out > x1:
                return None
            taken = [(max(lo, b[1]), min(hi, b[3])) for _, b in others if b[0] < out < b[2]]
        best, at = None, lo
        for a, b in sorted(t for t in taken if t[1] > t[0]):
            if a - at > (best[1] - best[0] if best else 0):
                best = (at, a)
            at = max(at, b)
        if hi - at > (best[1] - best[0] if best else 0):
            best = (at, hi)
        return best

    def opening(wall: str, run: tuple, width: float) -> list:
        c = (run[0] + run[1]) / 2
        if wall == "S":
            return _rect(c - width / 2, by1 - 0.1, width, 0.2)
        if wall == "N":
            return _rect(c - width / 2, by0 - 0.1, width, 0.2)
        if wall == "E":
            return _rect(bx1 - 0.1, c - width / 2, 0.2, width)
        return _rect(bx0 - 0.1, c - width / 2, 0.2, width)

    runs = [(wall, free_run(wall)) for wall in ("S", "E", "N", "W")]
    for width in (DOOR_W_M, 0.9):
        for wall, run in runs:
            if run and run[1] - run[0] >= width - eps:
                return opening(wall, run, width), False

    # Landlocked: into a neighbour, a hall first, else the longest shared wall.
    shared = []
    for kk, b in others:
        if abs(b[0] - bx1) < eps or abs(b[2] - bx0) < eps:
            lo, hi = max(by0, b[1]), min(by1, b[3])
            if hi - lo > eps:
                shared.append((kk, "E" if abs(b[0] - bx1) < eps else "W", (lo, hi)))
        if abs(b[1] - by1) < eps or abs(b[3] - by0) < eps:
            lo, hi = max(bx0, b[0]), min(bx1, b[2])
            if hi - lo > eps:
                shared.append((kk, "S" if abs(b[1] - by1) < eps else "N", (lo, hi)))
    if shared:
        hall = [t for t in shared if "server hall" in t[0].lower()]
        kk, wall, run = max(hall or shared, key=lambda t: t[2][1] - t[2][0])
        width = DOOR_W_M if run[1] - run[0] >= DOOR_W_M else 0.9
        return opening(wall, run, width), False

    # Nothing beside it at all: a door in the building's west face, as before.
    wall = "W" if abs(bx0 - x0) < eps else "E"
    return opening(wall, (by0, by1), DOOR_W_M), True


def _space(sid: str, level, kind: str, name: str, poly: list, **attrs) -> dict:
    out = {"id": sid, "level": level, "kind": kind, "name": name, "polygon_m": poly}
    if attrs:
        out["attrs"] = attrs
    return out


def _outdoor_kind(room_name: str) -> str:
    low = (room_name or "").lower()
    for token, kind in _OUTDOOR_KIND:
        if token in low:
            return kind
    return "yard"


def complete_site(floorplan: dict, devices: Iterable[Any]) -> dict:
    """``complete_building`` plus the drawing around it: per datacenter, the
    indoor ``fabric`` (corridor, core, lobby, dock, door openings) and the
    ``compound`` (fenced outline and outdoor zones). The building's outline
    grows by the service strip the fabric adds along its south side.

    Everything here is derived from the rooms, so a hall the fleet opens at
    runtime gets a corridor and a door like the curated ones, and nothing is
    stored that could disagree with the rooms. Fabric and zones carry no
    state and are never coloured by it; a yard that holds equipment is also an
    outdoor ROOM (level "Site"), and that room is what carries the alarms.
    Pure: the input is never modified."""
    out = complete_building(floorplan, devices)
    if not out or not out.get("rooms"):
        return out
    rooms = out["rooms"]
    buildings = {dc: dict(b) for dc, b in (out.get("buildings") or {}).items()}
    fabric: dict = {}
    compound: dict = {}
    for dc, b in sorted(buildings.items()):
        if not b.get("outline_m"):
            continue
        keys = [k for k, r in rooms.items() if (r.get("datacenter") or k.split("/", 1)[0]) == dc]
        (x0, y0), (x1, y1) = b["outline_m"][0], b["outline_m"][2]
        W, D = x1 - x0, y1 - y0
        levels = [lv["name"] for lv in b.get("levels") or [] if _level_rank(lv["name"])[0] != 2]
        ground = levels[0] if levels else "G"

        fab: list = []
        for lv in levels:
            fab.append(_space(f"{dc}/{lv}/corridor", lv, "corridor", "Corridor",
                              _rect(x0, y1, W - CORE_W_M, CORRIDOR_M)))
            fab.append(_space(f"{dc}/{lv}/core", lv, "core", "Stair and lift core",
                              _rect(x1 - CORE_W_M, y1, CORE_W_M, CORRIDOR_M + SERVICE_BAND_M)))
            if lv == ground:
                fab.append(_space(f"{dc}/{lv}/lobby", lv, "lobby", "Lobby",
                                  _rect(x0, y1 + CORRIDOR_M, LOBBY_W_M, SERVICE_BAND_M)))
                fab.append(_space(f"{dc}/{lv}/dock", lv, "dock", "Loading dock",
                                  _rect(x0 + LOBBY_W_M, y1 + CORRIDOR_M, DOCK_W_M, SERVICE_BAND_M)))
                south = y1 + CORRIDOR_M + SERVICE_BAND_M
                fab.append(_space(f"{dc}/{lv}/entrance", lv, "entrance", "Entrance",
                                  _rect(x0 + LOBBY_W_M / 2 - DOOR_W_M, south - 0.2, DOOR_W_M * 2, 0.2),
                                  external=True))
                fab.append(_space(f"{dc}/{lv}/dock-door", lv, "door", "Dock door",
                                  _rect(x0 + LOBBY_W_M + DOCK_W_M / 2 - 1.5, south - 0.2, 3.0, 0.2),
                                  external=True, monitored=False))
            # The level's rooms as plan rectangles, and the floor between them.
            # Whatever the rooms' zone holds that no room does is a passage -
            # the strip south of a hall that stops short of the corridor, the
            # gap between the plant and the rooms beside it - drawn like the
            # corridor, and it is what a room's door opens onto.
            boxes = {}
            for k in keys:
                r = rooms[k]
                if r["level"] != lv or not isinstance(r.get("origin"), dict):
                    continue
                w, d = _plan_extent(r)
                ox, oy = float(r["origin"]["x"]), float(r["origin"]["y"])
                boxes[k] = (ox, oy, ox + w, oy + d)
            for i, (px0, py0, px1, py1) in enumerate(_free_cells(list(boxes.values()), x0, y0, x1, y1)):
                fab.append(_space(f"{dc}/{lv}/passage-{i + 1}", lv, "corridor", "Passage",
                                  _rect(px0, py0, px1 - px0, py1 - py0)))
            # One opening per room, on a wall with floor outside it. Drawing
            # only, unless the BMS watches that room's door.
            for k, box in boxes.items():
                name = rooms[k].get("room") or k.split("/", 1)[1]
                monitored = any(name.startswith(m) for m in MONITORED_DOOR_ROOMS)
                poly, external = _door_opening(k, boxes, x0, y0, x1, y1)
                fab.append(_space(f"{k}/door", lv, "door", f"{name} door", poly,
                                  room=k, monitored=monitored, external=external))
        fabric[dc] = fab

        # The service strip is part of the building.
        bd = y1 + CORRIDOR_M + SERVICE_BAND_M
        b["outline_m"] = [[x0, y0], [x1, y0], [x1, round(bd, 3)], [x0, round(bd, 3)]]
        b["extent_m"] = {"width": round(W, 3), "depth": round(bd - y0, 3)}

        # The compound: fence, road and gate south, substation west, generator
        # yard and fuel east, plant water north, two telecom entrances.
        sx0, sx1 = x0 - SITE_WEST_M, x1 + SITE_EAST_M
        sy0, sy1 = y0 - SITE_NORTH_M, bd + SITE_SOUTH_M
        xm = x0 + W / 2
        zones: list = []
        zones.append(_space(f"{dc}/fence", OUTDOOR_LEVEL, "fence", "Fence", _rect(sx0, sy0, sx1 - sx0, sy1 - sy0)))
        zones.append(_space(f"{dc}/apron", OUTDOOR_LEVEL, "apron", "Loading apron", _rect(x0, bd, W, 4.0)))
        zones.append(_space(f"{dc}/road", OUTDOOR_LEVEL, "road", "Service road", _rect(sx0, bd + 4.0, sx1 - sx0, 3.0)))
        zones.append(_space(f"{dc}/parking", OUTDOOR_LEVEL, "parking", "Car park", _rect(sx0 + 2.0, bd + 7.5, 14.0, 3.5)))
        zones.append(_space(f"{dc}/gate", OUTDOOR_LEVEL, "gate", "Gate", _rect(xm - 2.0, sy1 - 1.0, 4.0, 1.0)))
        # The driveway from the gate to the service road, the gate's width.
        zones.append(_space(f"{dc}/driveway", OUTDOOR_LEVEL, "driveway", "Driveway",
                            _rect(xm - 2.0, bd + 7.0, 4.0, (sy1 - 1.0) - (bd + 7.0))))
        zones.append(_space(f"{dc}/guardhouse", OUTDOOR_LEVEL, "guardhouse", "Guardhouse", _rect(xm + 3.0, sy1 - 4.0, 3.0, 3.0)))
        # Drawn only until the topology holds a Substation room of its own:
        # then the room is the zone, carrying the transformer and its alarms,
        # and a second outline under it would be two substations.
        outdoor_kinds = {_outdoor_kind(rooms[k].get("room") or k) for k in keys
                         if is_outdoor(rooms[k]["level"]) and isinstance(rooms[k].get("origin"), dict)}
        if "substation" not in outdoor_kinds:
            zones.append(_space(f"{dc}/substation", OUTDOOR_LEVEL, "substation", "Utility substation",
                                _rect(x0 - 16.0, y0 + 2.0, 10.0, max(8.0, D - 4.0))))
        zones.append(_space(f"{dc}/water-tank", OUTDOOR_LEVEL, "water_tank", "Makeup water tank", _rect(xm - 2.0, y0 - 6.0, 4.0, 4.0)))
        zones.append(_space(f"{dc}/fire-pump-house", OUTDOOR_LEVEL, "fire_pump_house", "Fire pump house", _rect(xm + 4.0, y0 - 5.0, 4.0, 3.0)))
        zones.append(_space(f"{dc}/telecom-west", OUTDOOR_LEVEL, "telecom_entrance", "Telecom entrance A", _rect(sx0, y0 + D - 1.3, SITE_WEST_M, 0.6)))
        zones.append(_space(f"{dc}/telecom-east", OUTDOOR_LEVEL, "telecom_entrance", "Telecom entrance B", _rect(x1, y0 + 0.7, SITE_EAST_M, 0.6)))
        # Outdoor rooms are zones that carry equipment; the fuel tank stands
        # beside whichever yard holds the generators.
        gen_east = None
        for k in keys:
            r = rooms[k]
            if not is_outdoor(r["level"]) or not isinstance(r.get("origin"), dict):
                continue
            w, d = _plan_extent(r)
            ox, oy = float(r["origin"]["x"]), float(r["origin"]["y"])
            kind = _outdoor_kind(r.get("room") or k)
            zones.append(_space(f"{k}", OUTDOOR_LEVEL, kind, r.get("room") or k, _rect(ox, oy, w, d), room=k))
            if kind == "generator_yard":
                gen_east = max(gen_east or 0.0, ox + w)
        if gen_east is not None:
            zones.append(_space(f"{dc}/fuel-tank", OUTDOOR_LEVEL, "fuel_tank", "Bulk fuel tank", _rect(gen_east + 1.0, y0 + 2.0, 3.0, 8.0)))
        compound[dc] = {"outline_m": _rect(sx0, sy0, sx1 - sx0, sy1 - sy0),
                        "extent_m": {"width": round(sx1 - sx0, 3), "depth": round(sy1 - sy0, 3)},
                        "zones": zones}

    out["buildings"] = buildings
    out["fabric"] = fabric
    out["compound"] = compound
    return out

"""Topology-canvas layout: the rules, and the two ways to apply them.

The canvas is the network-graph view (position.x/y on each node). It is NOT the
floor plan — physical placement lives in rack_row / rack_num / floor_x / floor_y.

RULES

 1. Every datacenter owns an exclusive x-band. No node leaves it.
 2. Every room owns an exclusive rectangle inside its DC's band. Rectangles are
    disjoint.
 3. Coordinates are computed absolutely, never nudged relative to a previous value.
    layout_all() is a pure function of (name, datacenter, room) — same input, same
    output, every time.
 4. One ROLE per row. Role is the leading run of letters of the name's first
    segment, normalised so an A/B pair shares a row (PDUA/PDUB -> PDU) and the
    plant's six differently named sensors share one. Finer than device_type on
    purpose: `switch` is spine, leaf AND core; `router` is the edge router AND the
    OOB WAN router; `firewall` is the production, management and perimeter pairs.
 5. Rows run upstream-first (lower y). Production: RTR -> FW -> LB -> COR -> SP ->
    LF -> SRV. Power: UTIL -> SWGR -> ATS -> UPS/MCC -> RPP -> PDU. Cooling:
    CHL -> pumps -> VALVE -> CRAH.
 6. A meter row sits directly below the row it meters. Rows are LEFT-ALIGNED to the
    room's origin, so an EV2 pair lands under the RPP pair it clamps.
 7. Pitch is never tighter than the node box. Qt draws 90x70 (NODE_W/NODE_H), so
    X_PITCH=120 and ROW_PITCH=130.
 8. A row wraps into sub-rows past MAX_COLS.
 9. Within a row, nodes sort by name, so rack order reads left to right.

WHY LEFT-ALIGNED AND NOT CENTRED

Centring a row means adding one node re-centres it, moving every other node on
that row. The fleet adds devices to a *live* topology where a node may already
have been dragged by a user, so a placement that shifts its neighbours is not
usable. Left-aligned rows let place_one() append a node without touching anything
else. Rule 6 survives because equal-count rows both start at the room's x0.

TWO ENTRY POINTS

  layout_all(records)  — batch. Recomputes every coordinate from scratch. Used by
                         tools/layout_canvas.py against a topology JSON.
  place_one(...)       — incremental. Returns a coordinate for ONE new device and
                         moves nothing. Used by core/fleet_lifecycle when the fleet
                         grows a live topology. Best-effort: a node that overflows
                         its role's rows spills below the room. Re-run
                         tools/layout_canvas.py to tidy.
"""
from __future__ import annotations

import collections
import re
from typing import Dict, Iterable, List, Tuple

NODE_W, NODE_H = 90, 70          # ui/topology_view.py
X_PITCH, ROW_PITCH = 120, 130
MAX_COLS = 20
ROOM_GAP_X, ROOM_GAP_Y = 200, 260
DC_GAP = 600

ROLE_ALIAS = {
    "PDUA": "PDU", "PDUB": "PDU",
    "RPPA": "RPP", "RPPB": "RPP",
    "MPPA": "MPP", "MPPB": "MPP",
    "UPSA": "UPS", "UPSB": "UPS",
    "VCHW": "VALVE", "VCW": "VALVE",
    "CHWS": "SENSOR", "CHWR": "SENSOR", "CWS": "SENSOR",
    "CWR": "SENSOR", "CTB": "SENSOR", "FLOW": "SENSOR",
}

# Row order per room, upstream first. A role present but unlisted is appended at
# the bottom, so a new device class is noticed rather than silently mis-placed.
ROW_ORDER = {
    "Network Room": ["RTR", "FW", "LB", "COR",
                     "OOBR", "FWO", "OOBC", "FWM", "OOB", "JUMP",
                     "RPP", "EV", "PDU"],
    "Server Hall":  ["SP", "LF", "SRV", "OOB", "OOBM",
                     "SEN", "LEAK", "CDU",
                     "RPP", "EV", "PDU", "MPP", "CRAH"],
    "UPS Room":        ["UTIL", "SWGR", "ATS", "UPS", "EV"],
    "Generator Room":  ["GEN", "SWGR", "EV"],
    "Mechanical Room": ["MCC", "EV", "BMSC"],
    "Central Plant":   ["RPP", "EV", "PDU", "OOBM", "BMSC",
                        "CHL", "CHWP", "CWP", "VALVE", "SENSOR"],
    "Roof":            ["CT"],
}

# Rooms laid out as bands of side-by-side cells, top to bottom.
BANDS = [
    ["Network Room"],
    ["Server Hall A", "Server Hall B"],
    ["UPS Room", "Generator Room", "Mechanical Room"],
    ["Central Plant", "Roof"],
]


def role(name: str) -> str:
    """Row key for a device name. 'CHWP3-DC1-CP' -> 'CHWP'; 'PDUB-...' -> 'PDU'."""
    head = (name or "").split("-", 1)[0]
    m = re.match(r"[A-Za-z]+", head)
    r = m.group(0).upper() if m else "?"
    return ROLE_ALIAS.get(r, r)


def row_order_for(room: str) -> List[str]:
    if (room or "").startswith("Server Hall"):
        return ROW_ORDER["Server Hall"]
    return ROW_ORDER.get(room, [])


def _plan_room(names: List[str], room: str) -> Tuple[list, int, float, list]:
    """(rows, cols, height, unknown_roles) for one room. rows = [(role, [names])]."""
    by_role: Dict[str, list] = collections.defaultdict(list)
    for n in names:
        by_role[role(n)].append(n)
    for v in by_role.values():
        v.sort()

    order = row_order_for(room)
    known = [r for r in order if r in by_role]
    extra = sorted(r for r in by_role if r not in order)
    roles = known + extra

    cols = min(MAX_COLS, max(len(by_role[r]) for r in roles))
    rows = []
    for r in roles:
        grp = by_role[r]
        for i in range(0, len(grp), cols):
            rows.append((r, grp[i:i + cols]))
    return rows, cols, len(rows) * ROW_PITCH, extra


def layout_all(records: Iterable[Tuple[str, str, str, str]]) -> Tuple[dict, dict, list]:
    """Compute every coordinate from scratch.

    records: iterable of (key, name, datacenter, room). `key` is whatever the
    caller wants back — a node id, a Device id, anything hashable.

    Returns (positions {key: (x, y)}, rects {(dc, room): (x0, y0, w, h)}, notes).
    """
    recs = list(records)
    by_dc_room: Dict[tuple, list] = collections.defaultdict(list)
    key_of: Dict[tuple, str] = {}
    for key, name, dc, room in recs:
        by_dc_room[(dc, room)].append(name)
        key_of[(dc, room, name)] = key

    positions: Dict[str, Tuple[int, int]] = {}
    rects: Dict[tuple, tuple] = {}
    notes: list = []
    dc_x = 0.0

    for dc in sorted({dc for dc, _ in by_dc_room if dc}):
        plans = {}
        for band in BANDS:
            for room in band:
                names = by_dc_room.get((dc, room))
                if names:
                    plans[room] = _plan_room(names, room)
        # Rooms present but not named in BANDS still need a home.
        for (d, room), names in by_dc_room.items():
            if d == dc and room not in plans and names:
                plans[room] = _plan_room(names, room)
                notes.append(f"{dc}/{room}: room not in BANDS, placed in its own band")

        bands = [b for b in BANDS] + [[r] for r in plans
                                      if not any(r in b for b in BANDS)]

        dc_w = 0.0
        for band in bands:
            present = [r for r in band if r in plans]
            if present:
                w = (sum(plans[r][1] * X_PITCH for r in present)
                     + ROOM_GAP_X * (len(present) - 1))
                dc_w = max(dc_w, w)

        y = 0.0
        for band in bands:
            present = [r for r in band if r in plans]
            if not present:
                continue
            x = dc_x
            band_h = 0.0
            for room in present:
                rows, cols, h, extra = plans[room]
                w = cols * X_PITCH
                rects[(dc, room)] = (x, y, w, h)
                if extra:
                    notes.append(f"{dc}/{room}: roles not in ROW_ORDER, "
                                 f"appended at the bottom: {extra}")
                ry = y
                for _r, group in rows:
                    for i, name in enumerate(group):
                        positions[key_of[(dc, room, name)]] = (
                            round(x + i * X_PITCH), round(ry + ROW_PITCH / 2))
                    ry += ROW_PITCH
                x += w + ROOM_GAP_X
                band_h = max(band_h, h)
            y += band_h + ROOM_GAP_Y
        dc_x += dc_w + DC_GAP

    return positions, rects, notes


def place_one(name: str, dc: str, room: str,
              existing: Iterable[Tuple[str, str, str, float, float]]) -> Tuple[int, int]:
    """Coordinate for ONE new device. Moves nothing that is already placed.

    existing: iterable of (name, datacenter, room, x, y) for every node already on
    the canvas.

    Appends the node to the last non-full sub-row of its role. If every sub-row is
    full, or the role is new to this room, it starts a fresh row below the room's
    current content. That can push past the room's tidy rectangle; a batch
    layout_all() pass squares it up. What it never does is move a neighbour.
    """
    ex = [e for e in existing]
    if not ex:
        return (0, 0)

    occupied = {(round(x), round(y)) for _n, _d, _r, x, y in ex}
    same_room = [(n, x, y) for n, d, r, x, y in ex if d == dc and r == room]

    if not same_room:
        # First node of a room the canvas has never seen. Park it below this DC's
        # content (or below everything, if the DC is new too) at the left edge.
        in_dc = [(x, y) for _n, d, _r, x, y in ex if d == dc]
        pool = in_dc or [(x, y) for _n, _d, _r, x, y in ex]
        x0 = min(x for x, _ in pool)
        y0 = max(y for _, y in pool) + ROOM_GAP_Y
        return _free(x0, y0, occupied)

    room_x0 = min(x for _n, x, _y in same_room)
    room_ys = {round(y) for _n, _x, y in same_room}
    room_ymax = max(room_ys)

    r = role(name)
    mine = [(n, x, y) for n, x, y in same_room if role(n) == r]

    if mine:
        rows: Dict[int, list] = collections.defaultdict(list)
        for n, x, y in mine:
            rows[round(y)].append(x)
        for y in sorted(rows):
            if len(rows[y]) < MAX_COLS:
                return _free(max(rows[y]) + X_PITCH, y, occupied)
        # Every sub-row of this role is full. A fresh one goes directly beneath the
        # role's last row if that band is empty, else below the whole room.
        y_new = max(rows) + ROW_PITCH
        if round(y_new) in room_ys:
            y_new = room_ymax + ROW_PITCH
        return _free(room_x0, y_new, occupied)

    # Role is new to this room (a hall's first CRAH, say): start a row below it.
    return _free(room_x0, room_ymax + ROW_PITCH, occupied)


def _free(x: float, y: float, occupied: set) -> Tuple[int, int]:
    """Step right until the slot is empty. Guarantees no coincident nodes."""
    x, y = round(x), round(y)
    while (x, y) in occupied:
        x += X_PITCH
    return (x, y)


def check(positions: Dict[str, Tuple[int, int]],
          records: Iterable[Tuple[str, str, str, str]],
          rects: Dict[tuple, tuple]) -> List[str]:
    """Assert the invariants. Returns a list of violations (empty when clean)."""
    errs: List[str] = []
    seen: Dict[tuple, str] = {}
    name_of = {k: n for k, n, _d, _r in records}

    for k, xy in positions.items():
        if xy in seen:
            errs.append(f"coincident nodes at {xy}: {seen[xy]} / {name_of.get(k, k)}")
        seen[xy] = name_of.get(k, k)

    for key, name, dc, room in records:
        rect = rects.get((dc, room))
        if rect is None:
            errs.append(f"{name}: no rectangle for {dc}/{room}")
            continue
        x0, y0, w, h = rect
        px, py = positions[key]
        if not (x0 - NODE_W / 2 <= px <= x0 + w and y0 <= py <= y0 + h):
            errs.append(f"{name} at ({px},{py}) outside {dc}/{room} rect {rect}")

    items = sorted(rects.items())
    for i, (ka, ra) in enumerate(items):
        for kb, rb in items[i + 1:]:
            ax, ay, aw, ah = ra
            bx, by, bw, bh = rb
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                errs.append(f"room rects overlap: {ka} {ra} vs {kb} {rb}")

    grid: Dict[tuple, list] = collections.defaultdict(list)
    for k, (px, py) in positions.items():
        grid[(px // 200, py // 200)].append((k, px, py))
    for cell, group in grid.items():
        cand = list(group)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx, dy) != (0, 0):
                    cand += grid.get((cell[0] + dx, cell[1] + dy), [])
        for ka, ax, ay in group:
            for kb, bx, by in cand:
                if ka == kb:
                    continue
                if abs(ax - bx) < NODE_W and abs(ay - by) < NODE_H:
                    errs.append(f"boxes overlap: {name_of.get(ka, ka)} / "
                                f"{name_of.get(kb, kb)}")
    return sorted(set(errs))

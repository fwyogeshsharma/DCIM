"""Server-hall floor geometry — the single source of truth for where a rack
sits on the floor plan and which hot/cold aisle it faces.

A hall is a hot/cold-aisle-contained grid in room-local metres:

    y (depth) ─▶          rack rows run left→right (x = width)
      aisle HA0  (hot,  perimeter)            j=0  even → hot
      row 1      ───────────────  facing S    i=1
      aisle CA1  (cold)                        j=1  odd  → cold
      row 2      ───────────────  facing N    i=2   (front-to-front w/ row1 on CA1)
      aisle HA1  (hot)                         j=2  even → hot
      row 3      ───────────────  facing S    i=3   (back-to-back w/ row2 on HA1)
      ...

Locked against the curated dual_dc topology:
  rack_pitch 0.6 m · row_pitch 2.4 m · aisle 1.2 m · rack footprint 0.6×1.2 m
  rack_x(num) = 0.3 + 0.6·(num-1)      (0.3 = half a rack from the wall)
  row_y(i)    = 1.8 + 2.4·(i-1)        (row 1 at 1.8, then every row_pitch)
  aisle j (before row j+1) at 0.6 + 2.4·j ; hot if j even, cold if j odd.

Imported by the hall-enlarge tool and the fleet engine so both lay racks down
on exactly the same grid.
"""
from __future__ import annotations

from dataclasses import dataclass

RACK_PITCH = 0.6      # m, centre-to-centre between racks across a row (x)
ROW_PITCH = 2.4       # m, centre-to-centre between rows (y) = rack depth + aisle
AISLE_WIDTH = 1.2     # m
RACK_W = 0.6          # m, rack footprint width
RACK_D = 1.2          # m, rack footprint depth
_X0 = 0.3             # first rack centre (half a rack off the wall)
_Y0 = 1.8             # first row centre


def rack_x(num: int) -> float:
    """Local x of rack number *num* (1-based) in its row."""
    return round(_X0 + RACK_PITCH * (num - 1), 4)


def row_y(i: int) -> float:
    """Local y of row *i* (1-based)."""
    return round(_Y0 + ROW_PITCH * (i - 1), 4)


def _aisle_name(j: int) -> tuple[str, str]:
    """(name, type) of aisle index *j* (0-based, sits before row j+1)."""
    if j % 2 == 0:
        return f"HA{j // 2}", "hot"
    return f"CA{(j + 1) // 2}", "cold"


@dataclass(frozen=True)
class Slot:
    rack_row: int
    rack_num: int
    floor_x: float
    floor_y: float
    hot_aisle: str
    cold_aisle: str
    rack_facing: str        # 'N' (faces lower y) or 'S' (faces higher y)


def row_aisles(i: int) -> tuple[str, str, str]:
    """(hot_aisle, cold_aisle, facing) for row *i*.

    Even rows face N onto the cold aisle just BEFORE them; odd rows face S onto
    the cold aisle just AFTER them — the standard alternating containment."""
    before, after = _aisle_name(i - 1), _aisle_name(i)      # (name,type) each
    if i % 2 == 0:                       # cold is the 'before' aisle, face N
        cold, hot, facing = before[0], after[0], "N"
    else:                                # cold is the 'after' aisle, face S
        cold, hot, facing = after[0], before[0], "S"
    return hot, cold, facing


def slot(rack_row: int, rack_num: int) -> Slot:
    """Full placement for a rack at (rack_row, rack_num)."""
    hot, cold, facing = row_aisles(rack_row)
    return Slot(rack_row, rack_num, rack_x(rack_num), row_y(rack_row),
               hot, cold, facing)


def aisles_for_rows(n_rows: int) -> list[dict]:
    """Floorplan 'aisles' list spanning rows 1..n_rows (one closing aisle after
    the last row), matching the curated extent schema."""
    out = []
    for j in range(n_rows + 1):          # aisle before each row + one trailing
        name, typ = _aisle_name(j)
        out.append({"id": name, "type": typ,
                    "between_rows": [j, j + 1],
                    "y": round(0.6 + ROW_PITCH * j, 4), "width": AISLE_WIDTH})
    return out


def racks_for_width(width_m: float) -> int:
    """How many racks physically fit across a row of a *width_m*-wide hall — the
    inverse of hall_extent's width formula (width = racks_per_row·RACK_PITCH +
    2·margin). This is the SINGLE SOURCE OF TRUTH for row capacity: a separately
    stored racks_per_row can drift from width_m (the curated extents disagreed —
    an 8.4 m hall stored racks_per_row=9 but physically fits 13, leaving a dead
    strip), so callers should derive capacity from the physical width instead."""
    if not width_m or width_m <= 0:
        return 0
    return max(1, int(round((float(width_m) - 2 * _X0) / RACK_PITCH)))


def hall_extent(n_rows: int, racks_per_row: int) -> dict:
    """Room width_m/depth_m + rows/racks_per_row/aisles for an *n_rows* ×
    *racks_per_row* hall, sized to the grid plus the standard wall margins."""
    width_m = round(racks_per_row * RACK_PITCH + 2 * _X0, 4)        # racks + margins
    depth_m = round(row_y(n_rows) + RACK_D / 2 + 0.6, 4)            # last row + back margin
    return {"width_m": width_m, "depth_m": depth_m,
            "rows": list(range(1, n_rows + 1)),
            "racks_per_row": racks_per_row,
            "aisles": aisles_for_rows(n_rows)}

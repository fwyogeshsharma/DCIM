"""Spatial air model for a server hall (docs/S2_SPATIAL_THERMAL_MODEL.md, §5).

What a rack breathes, from where the CRAHs stand and what they are doing:

  * TILE SUPPLY (§5.2). Each running unit pressurises the plenum most near its
    discharge; its influence on a rack falls off with distance. A rack's tile air
    is the influence-weighted mix of the units' discharge temperatures.
  * COVERAGE (§5.3). Each unit's CURRENT airflow - its rating scaled by how
    fast group control is running its fans - leaves through the tiles by plenum
    pressure (influence) and by how the tiles were provisioned: a tuned hall puts
    higher-flow tiles in front of denser racks, so share follows influence x heat.
    A rack whose share falls short of its heat is STARVED. Fans are throttled to
    the load, so a trip starves the racks nearest the dead unit until the
    survivors ramp.
  * INLET (§5.4). Tile air plus recirculated hot-aisle air. How much recirculates
    is structural (containment, height in the rack, row ends, missing blanking)
    plus the starved share.
  * RETURN (§5.5). Each unit reads the hot air of the racks it serves, mixed with
    some bypass of its own discharge.
  * HUMIDITY (§5.6). One humidity ratio per room; RH per sensor from its own
    temperature, so dew point is uniform and warm air reads drier.

Pure: dataclasses in, dataclasses out, no store, no I/O, no randomness. The
store gathers inputs once per tick per room and reads results by rack and unit,
so every rule here can be pinned by a hand-worked test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Influence length, m. UNCERTAIN: published CRAH influence zones run ~3-8 m
#: depending on plenum depth and obstructions. Tests pin behaviour, not this.
INFLUENCE_LENGTH_M = 5.0
#: Share of a unit's discharge that returns to it without passing through IT.
#: The same 0.15 the room model used (_RETURN_MIX_FRAC = 0.85), now per unit.
BYPASS_FRAC = 0.15
#: Recirculation can never be total: a rack always gets some tile air.
MAX_RECIRC = 0.9
#: Hot-aisle neighbourhood, in rack positions either side.
HOT_AISLE_REACH = 2
#: Rise assumed across IT when a rack has no exhaust reading yet, K.
DEFAULT_RISE_K = 12.0

#: Structural recirculation (§5.4 table): containment -> (bottom, middle, top,
#: row-end extra, per open-U fraction).
RECIRC = {
    "cold_aisle": (0.01, 0.02, 0.03, 0.02, 0.10),
    "hot_aisle":  (0.01, 0.02, 0.02, 0.01, 0.05),
    "none":       (0.03, 0.08, 0.18, 0.10, 0.10),
}


@dataclass(frozen=True)
class Rack:
    id: str
    #: Point on the cold face (where it draws tile air), room metres.
    cold_x: float
    cold_y: float
    #: Identity of the hot aisle it exhausts into, and its position along the row.
    hot_aisle: str
    position: int
    row_end: bool
    #: Air-side heat, W (IT minus what a CDU loop carries off).
    heat_w: float
    #: Last tick's mean exhaust, °C; None before the first reading.
    exhaust_c: float | None = None
    #: Share of the rack's U left open (no blanking), 0..1.
    open_u: float = 0.0
    #: Cabinet height, m (for the height thirds).
    height_m: float = 2.0


@dataclass(frozen=True)
class Unit:
    id: str
    #: Discharge point (front face centre at floor level), room metres.
    x: float
    y: float
    #: 0 stopped / airflow lost, 0.8 dirty filter, 1 healthy.
    delivered: float
    #: Discharge (supply) temperature, °C.
    supply_c: float
    #: Rated sensible capacity, W, at full fan speed.
    capacity_w: float
    #: Fan speed as a fraction of full (airflow scales with speed). 1.0 when the
    #: caller has no reading.
    fan_frac: float = 1.0


@dataclass
class RackAir:
    tile_c: float | None
    hot_c: float
    starve: float
    cover: float
    #: Recirculated share at bottom / middle / top third.
    recirc: tuple[float, float, float]

    def inlet_at(self, z_m: float, height_m: float) -> float | None:
        """Inlet temperature at height *z_m* in a cabinet *height_m* tall,
        interpolated between the third-centres."""
        if self.tile_c is None:
            return None
        frac = max(0.0, min(1.0, z_m / height_m if height_m > 0 else 0.5))
        b, m, t = self.recirc
        if frac <= 0.5:
            r = b + (m - b) * (frac / 0.5)
        else:
            r = m + (t - m) * ((frac - 0.5) / 0.5)
        return self.tile_c + r * (self.hot_c - self.tile_c)


@dataclass
class RoomAir:
    racks: dict[str, RackAir] = field(default_factory=dict)
    returns: dict[str, float] = field(default_factory=dict)


def influence(rack: Rack, unit: Unit, length_m: float = INFLUENCE_LENGTH_M,
              ignore_state: bool = False) -> float:
    """w_ij = f_j * exp(-d_ij / L). *ignore_state* gives the geometric weight a
    stopped unit would have - what its return sensor sits in."""
    d = math.hypot(rack.cold_x - unit.x, rack.cold_y - unit.y)
    f = 1.0 if ignore_state else max(0.0, unit.delivered)
    return f * math.exp(-d / length_m)


def _hot_air(rack: Rack, racks: list[Rack]) -> float:
    """Heat-weighted mean exhaust of the racks sharing *rack*'s hot aisle within
    HOT_AISLE_REACH positions; its own exhaust; else tile + default rise (the
    caller adds the tile)."""
    near = [r for r in racks if r.hot_aisle == rack.hot_aisle and r.exhaust_c is not None
            and abs(r.position - rack.position) <= HOT_AISLE_REACH]
    wsum = sum(max(r.heat_w, 1.0) for r in near)
    if near and wsum > 0:
        return sum(max(r.heat_w, 1.0) * r.exhaust_c for r in near) / wsum
    return float("nan")


def recirc_profile(rack: Rack, containment: str, starve: float) -> tuple[float, float, float]:
    b, m, t, end, per_open = RECIRC.get(containment, RECIRC["none"])
    extra = (end if rack.row_end else 0.0) + per_open * max(0.0, min(1.0, rack.open_u))
    out = []
    for base in (b, m, t):
        r = base + extra
        r = r + starve * (1.0 - r)
        out.append(max(0.0, min(MAX_RECIRC, r)))
    return out[0], out[1], out[2]


def solve_room(racks: list[Rack], units: list[Unit], containment: str,
               length_m: float = INFLUENCE_LENGTH_M) -> RoomAir:
    """Tile, coverage, recirculation per rack and return per unit (§5.2-5.5)."""
    out = RoomAir()
    w = {(r.id, u.id): influence(r, u, length_m) for r in racks for u in units}

    # Coverage: each unit's airflow (as cooling at design rise) leaves the tiles
    # by plenum pressure (influence) through tiles provisioned to the load (heat).
    share_den = {u.id: sum(w[(r.id, u.id)] * max(r.heat_w, 0.0) for r in racks) for u in units}
    for r in racks:
        wsum = sum(w[(r.id, u.id)] for u in units)
        tile = (sum(w[(r.id, u.id)] * u.supply_c for u in units) / wsum) if wsum > 1e-12 else None
        if r.heat_w > 0:
            got = sum(u.capacity_w * max(0.0, min(1.0, u.fan_frac)) * w[(r.id, u.id)] * r.heat_w
                      / share_den[u.id] for u in units if share_den[u.id] > 0)
            cover = got / r.heat_w
        else:
            cover = float("inf") if wsum > 1e-12 else 0.0
        starve = 1.0 if tile is None else max(0.0, min(1.0, 1.0 - cover))
        hot = _hot_air(r, racks)
        if math.isnan(hot):
            hot = (r.exhaust_c if r.exhaust_c is not None
                   else (tile if tile is not None else 0.0) + DEFAULT_RISE_K)
        out.racks[r.id] = RackAir(tile_c=tile, hot_c=hot, starve=starve,
                                  cover=cover, recirc=recirc_profile(r, containment, starve))

    # Return per unit: what arrives at its inlet - the hot air of the racks it
    # reaches, weighted by heat, plus bypass. A stopped unit's sensor still sits
    # in that air, so it uses the geometric weights.
    for u in units:
        num = den = 0.0
        for r in racks:
            g = influence(r, u, length_m, ignore_state=True) * max(r.heat_w, 1.0)
            num += g * out.racks[r.id].hot_c
            den += g
        if den > 0:
            out.returns[u.id] = (1.0 - BYPASS_FRAC) * (num / den) + BYPASS_FRAC * u.supply_c
    return out


# ---------------------------------------------------------------- humidity (§5.6)
# Magnus constants: the simulator's single source (core/psychrometrics), the
# same pair the platform derives dew point with.
from core.psychrometrics import _MAGNUS_B, _MAGNUS_C  # noqa: E402
_P_ATM_KPA = 101.325


def _es_kpa(t_c: float) -> float:
    return 0.6112 * math.exp(_MAGNUS_B * t_c / (_MAGNUS_C + t_c))


def humidity_ratio(t_c: float, rh_pct: float, p_kpa: float = _P_ATM_KPA) -> float:
    """g of water per kg of dry air."""
    e = max(0.0, min(100.0, rh_pct)) / 100.0 * _es_kpa(t_c)
    return 1000.0 * 0.622 * e / (p_kpa - e)


def rh_from_ratio(t_c: float, w_g_kg: float, p_kpa: float = _P_ATM_KPA) -> float:
    """Relative humidity (%) of air at *t_c* holding *w_g_kg*."""
    w = max(0.0, w_g_kg) / 1000.0
    e = w * p_kpa / (0.622 + w)
    return max(0.0, min(100.0, 100.0 * e / _es_kpa(t_c)))


def dew_point_from_ratio(w_g_kg: float, p_kpa: float = _P_ATM_KPA) -> float:
    w = max(1e-6, w_g_kg) / 1000.0
    e = w * p_kpa / (0.622 + w)
    g = math.log(e / 0.6112)
    return _MAGNUS_C * g / (_MAGNUS_B - g)


#: ASHRAE recommended moisture band, as dew point, °C (the humidifier and the
#: coil's dehumidification hold the room inside it).
DEW_POINT_LOW_C = 5.5
DEW_POINT_HIGH_C = 15.0


def step_room_ratio(w_g_kg: float, mean_supply_c: float, noise_g_kg: float,
                    target_rh: float = 45.0, revert: float = 0.05) -> float:
    """One tick of the room's humidity walk: mean-revert toward *target_rh* at
    the mean supply temperature, add the caller's noise, hold the dew point in
    the recommended band. Deterministic given the noise, so it is testable."""
    target = humidity_ratio(mean_supply_c, target_rh)
    w = w_g_kg + (target - w_g_kg) * revert + noise_g_kg
    lo = humidity_ratio(DEW_POINT_LOW_C, 100.0)
    hi = humidity_ratio(DEW_POINT_HIGH_C, 100.0)
    return max(lo, min(hi, w))

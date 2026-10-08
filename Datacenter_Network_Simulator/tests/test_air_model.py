"""The spatial air model (docs/S2_SPATIAL_THERMAL_MODEL.md §5, §8).

Pure tests against core/air_model.py on a hall built from the real grid
(core/hall_geometry): 12.2 x 12.3 m, CRAHs at the aisle ends (D-1 option A).
They pin behaviour - who rises, who doesn't, in which order - not constants.
"""
from __future__ import annotations

import pytest

from core import air_model as am
from core import hall_geometry as geo

W, D = geo.hall_width(13, geo.HALL_X0), 12.3
CAP_W = 80_000.0


def fan_for(racks, margin=1.25, units=7):
    """Fan speed group control would hold: the load plus a margin, over the
    installed airflow. Real units are throttled to the room, not run flat out."""
    return min(1.0, margin * sum(r.heat_w for r in racks) / (units * CAP_W))


def hall_units(stopped=(), supply=18.0, fan=0.35):
    out = []
    for i, (x, y, _rot) in enumerate(geo.crah_positions(W, D, 7)):
        uid = f"C{i + 1}"
        out.append(am.Unit(id=uid, x=x, y=y, delivered=0.0 if uid in stopped else 1.0,
                           supply_c=supply, capacity_w=CAP_W,
                           fan_frac=0.0 if uid in stopped else fan))
    return out


def hall_racks(rows=(2, 3), per_row=13, heat=12_000.0, dense=None, exhaust=34.0):
    """Racks on the real grid. *dense* = {(row, num): heat_w}."""
    racks = []
    for row in rows:
        sl = [geo.slot(row, n) for n in range(1, per_row + 1)]
        for s in sl:
            x = geo.rack_x(s.rack_num, geo.HALL_X0)
            front = -1 if s.rack_facing == "N" else 1
            racks.append(am.Rack(
                id=f"R{row}-{s.rack_num}", cold_x=x, cold_y=s.floor_y + front * geo.RACK_D / 2,
                hot_aisle=s.hot_aisle, position=s.rack_num,
                row_end=s.rack_num in (1, per_row),
                heat_w=(dense or {}).get((row, s.rack_num), heat), exhaust_c=exhaust))
    return racks


# --- 1. influence -----------------------------------------------------------

def test_influence_falls_with_distance_and_a_stopped_unit_has_none():
    racks = hall_racks()
    near, far = racks[0], racks[12]                      # row 2, ends 1 and 13
    u = hall_units()[0]                                  # west wall
    assert am.influence(near, u) > am.influence(far, u) > 0
    dead = hall_units(stopped={"C1"})[0]
    assert am.influence(near, dead) == 0.0
    assert am.influence(near, dead, ignore_state=True) == pytest.approx(am.influence(near, u))


# --- 3. redundancy covers ----------------------------------------------------

def test_a_healthy_hall_starves_nothing():
    racks = hall_racks()
    room = am.solve_room(racks, hall_units(fan=fan_for(racks)), "cold_aisle")
    assert all(r.starve == 0.0 for r in room.racks.values())
    assert all(r.tile_c == pytest.approx(18.0) for r in room.racks.values())


def test_a_trip_starves_the_racks_nearest_it_first_until_the_survivors_ramp():
    # Fans throttled to the load (group control), then the west unit nearest
    # row 2's cold aisle trips. With L = 5 m in a 12 m hall the loss spreads
    # along the row, so the claim is per row: the nearest row rises, the next
    # row barely, and ramping the survivors covers it again.
    racks = hall_racks()
    fan = fan_for(racks)
    before = am.solve_room(racks, hall_units(fan=fan), "cold_aisle")
    after = am.solve_room(racks, hall_units(stopped={"C1"}, fan=fan), "cold_aisle")
    assert all(r.starve == 0.0 for r in before.racks.values())

    def row_rise(row):
        ids = [f"R{row}-{n}" for n in range(1, 14)]
        return sum(after.racks[i].inlet_at(1.0, 2.0) - before.racks[i].inlet_at(1.0, 2.0)
                   for i in ids) / len(ids)

    assert row_rise(2) > 0.3
    assert row_rise(3) < row_rise(2) / 3
    # Two units down on that wall: the near row starves hardest at the tripped end.
    worse = am.solve_room(racks, hall_units(stopped={"C1", "C2"}, fan=fan), "cold_aisle")
    assert worse.racks["R2-1"].starve > worse.racks["R2-13"].starve
    # The survivors ramp to full: covered again.
    ramped = am.solve_room(racks, hall_units(stopped={"C1"}, fan=1.0), "cold_aisle")
    assert all(r.starve == 0.0 for r in ramped.racks.values())


def test_a_total_loss_starves_every_rack():
    room = am.solve_room(hall_racks(), hall_units(stopped={f"C{i}" for i in range(1, 8)}), "cold_aisle")
    assert all(r.starve == 1.0 and r.tile_c is None for r in room.racks.values())


# --- 4-5. containment and row ends -------------------------------------------

def _rise_bottom_to_top(containment, rack_id="R2-7"):
    racks = hall_racks(exhaust=30.0)
    room = am.solve_room(racks, hall_units(fan=fan_for(racks)), containment)
    r = room.racks[rack_id]
    return r.inlet_at(1.9, 2.0) - r.inlet_at(0.1, 2.0)


def test_a_contained_cold_aisle_has_almost_no_vertical_gradient():
    assert 0.0 < _rise_bottom_to_top("cold_aisle") < 0.5


def test_an_uncontained_hall_rises_to_the_top():
    assert 1.5 < _rise_bottom_to_top("none") < 3.0


def test_row_end_racks_recirculate_more_when_uncontained():
    racks = hall_racks(exhaust=30.0)
    room = am.solve_room(racks, hall_units(fan=fan_for(racks)), "none")
    assert room.racks["R2-1"].inlet_at(1.9, 2.0) > room.racks["R2-7"].inlet_at(1.9, 2.0)


def test_missing_blanking_raises_recirculation():
    a = am.recirc_profile(hall_racks()[6], "cold_aisle", 0.0)
    b = am.recirc_profile(am.Rack(**{**hall_racks()[6].__dict__, "open_u": 0.5}), "cold_aisle", 0.0)
    assert all(y > x for x, y in zip(a, b, strict=True))


# --- 6. per-CRAH return -------------------------------------------------------

def test_each_unit_reads_the_hot_air_of_the_racks_it_serves():
    # Hot racks at the west end only.
    racks = [am.Rack(**{**r.__dict__, "exhaust_c": 40.0 if r.position <= 3 else 30.0})
             for r in hall_racks()]
    room = am.solve_room(racks, hall_units(fan=fan_for(racks)), "cold_aisle")
    west = [room.returns[f"C{i}"] for i in range(1, 5)]
    east = [room.returns[f"C{i}"] for i in range(5, 8)]
    assert min(west) > max(east)


def test_a_stopped_unit_still_reads_a_return():
    room = am.solve_room(hall_racks(), hall_units(stopped={"C2"}, fan=0.5), "cold_aisle")
    assert "C2" in room.returns and room.returns["C2"] > 18.0


# --- 7. humidity --------------------------------------------------------------

def test_dew_point_is_uniform_and_warm_air_reads_drier():
    w = am.humidity_ratio(22.0, 45.0)
    assert am.dew_point_from_ratio(w) == pytest.approx(9.6, abs=0.3)
    assert am.rh_from_ratio(22.0, w) == pytest.approx(45.0, abs=0.1)
    assert am.rh_from_ratio(34.0, w) < 25.0


def test_the_room_walk_reverts_and_stays_in_the_recommended_band():
    w = am.humidity_ratio(22.0, 80.0)                    # far too wet
    for _ in range(200):
        w = am.step_room_ratio(w, 22.0, 0.0)
    assert am.rh_from_ratio(22.0, w) == pytest.approx(45.0, abs=1.0)
    assert am.step_room_ratio(0.1, 22.0, -5.0) == pytest.approx(am.humidity_ratio(am.DEW_POINT_LOW_C, 100.0))


# --- the grid it stands on ----------------------------------------------------

def test_crahs_stand_at_the_aisle_ends_and_fit_their_walls():
    pos = geo.crah_positions(W, D, 7)
    west = [p for p in pos if p[2] == 90.0]
    east = [p for p in pos if p[2] == 270.0]
    assert (len(west), len(east)) == (4, 3)
    for side in (west, east):
        ys = sorted(p[1] for p in side)
        assert all(b - a >= geo.CRAH_WIDTH_M for a, b in zip(ys, ys[1:]))
        assert ys[0] - geo.CRAH_WIDTH_M / 2 >= 0 and ys[-1] + geo.CRAH_WIDTH_M / 2 <= D


def test_a_server_hall_keeps_thirteen_racks_and_the_network_room_its_four():
    assert geo.racks_for_width(W, geo.HALL_X0) == 13
    assert geo.rack_x(1, geo.HALL_X0) - geo.RACK_W / 2 >= geo.CRAH_DEPTH_M + 0.9
    assert geo.racks_for_width(3.0) == 4
    assert geo.room_x0({}) == 0.3 and geo.room_x0({"grid_x0_m": 2.2}) == 2.2

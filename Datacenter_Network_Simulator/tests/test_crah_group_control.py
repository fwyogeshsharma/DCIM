"""A hall's CRAHs carry the load as a GROUP.

Two behaviours that belong together, and were wrong together.

The fan loop sized each unit's duty against the cooling INSTALLED in the room,
so a trip moved nothing: the survivors held their speed and only began to ramp
minutes later, once the hall had warmed enough to drive the temperature gain.
Real units do not wait for that. Modern halls run their CRAHs in a group -
Vertiv iCOM Teamwork, Stulz C7000 group control - and in a raised-floor room
the fans are also chasing underfloor static pressure, which falls the moment a
fan stops. The rest are ramping within seconds.

The room model then charged a fixed share of a 12 K rise for every unit that
was down, whether or not the rest could cover it. Seven units carrying a load
four could hold still warmed the hall on one trip, which is the opposite of
what the spare unit is for. Temperature is what is LEFT when the survivors have
ramped and run out of fan.
"""
from __future__ import annotations

import pytest

from conftest import DC, ROOM, build_plant

RK = (DC, ROOM)


@pytest.fixture
def hall(tmp_path, plant_cache):
    """Four CRAHs, and a room heat this fixture sets per test."""
    return build_plant(tmp_path, crahs=4)


@pytest.fixture
def busy_hall(tmp_path, plant_cache):
    """A hall loaded hard enough to be off the fan floor.

    Fan speed is floored at the drive's turndown, so a lightly loaded room sits
    at 30 % whatever happens to it and a ramp is invisible. These tests are
    about the ramp, so the hall has to be working."""
    return build_plant(tmp_path, crahs=4, servers=260)


def units(hall):
    return [hall.made[f"CRAH{i}"].name for i in (1, 2, 3, 4)]


def run_all(hall, plant_cache):
    for n in units(hall):
        plant_cache.setdefault(n, {})["Unit_Running"] = 1.0


def stop(plant_cache, *names):
    for n in names:
        plant_cache.setdefault(n, {})["Unit_Running"] = 0.0


def load(hall, kw):
    """Pin the room's live IT heat and the SKU map the air model reads."""
    hall.store._room_it_w[RK] = kw * 1000.0
    hall.store._plant_model_by_name = {
        n: hall.name(n).model_name for n in units(hall)}


def supply(hall):
    return hall.store._room_supply_temp(hall.made["CRAH1"])


# ── what the units are rated for ─────────────────────────────────────────────

def test_the_fixture_carries_a_real_sku(hall, plant_cache):
    """Everything here is arithmetic on rated capacity, so a fixture whose CRAHs
    have no catalog SKU would pass these tests while measuring nothing."""
    run_all(hall, plant_cache)
    load(hall, 100.0)
    delivered, installed = hall.store._room_cooling_kw(RK)
    assert installed > 0
    assert delivered == installed


def test_a_stopped_unit_delivers_nothing(hall, plant_cache):
    run_all(hall, plant_cache)
    load(hall, 100.0)
    stop(plant_cache, units(hall)[0])
    delivered, installed = hall.store._room_cooling_kw(RK)
    assert delivered == pytest.approx(installed * 0.75)


def test_a_dirty_filter_is_a_partial_loss_not_a_dead_unit(hall, plant_cache):
    """It still blows cold air, just less of it. Scoring it as a stopped unit
    would make a filter alarm read like a trip."""
    run_all(hall, plant_cache)
    load(hall, 100.0)
    plant_cache[units(hall)[0]]["Filter_Dirty"] = 1.0
    delivered, installed = hall.store._room_cooling_kw(RK)
    assert 0.0 < installed - delivered < installed * 0.25


# ── half 1: the survivors pick up the load at once ───────────────────────────

def _speed(hall):
    """The VFD speed the store hands a surviving unit's fan, this tick."""
    hall.tick()
    return hall.store._plant_speed_by_name.get(units(hall)[1])


def test_a_trip_raises_the_other_units_fan_speed(busy_hall, plant_cache):
    hall = busy_hall
    run_all(hall, plant_cache)
    before = _speed(hall)
    stop(plant_cache, units(hall)[0])
    after = _speed(hall)
    assert before is not None and after is not None
    assert after > before


def test_the_ramp_does_not_wait_for_the_hall_to_warm(busy_hall, plant_cache):
    """The speed rises on the same tick as the trip, with room air unchanged.

    This is the whole distinction from the old behaviour, which only responded
    through the temperature gain - minutes later, and only once racks were
    already breathing warm air."""
    hall = busy_hall
    run_all(hall, plant_cache)
    hall.tick()
    inlet_before = hall.store._room_inlet_c.get(RK)
    before = hall.store._plant_speed_by_name.get(units(hall)[1])
    stop(plant_cache, units(hall)[0])
    hall.tick()
    assert hall.store._room_inlet_c.get(RK) == pytest.approx(inlet_before, abs=1.5)
    assert hall.store._plant_speed_by_name.get(units(hall)[1]) > before


def test_losing_more_units_ramps_the_survivors_harder(busy_hall, plant_cache):
    hall = busy_hall
    run_all(hall, plant_cache)
    stop(plant_cache, units(hall)[0])
    one = _speed(hall)
    stop(plant_cache, units(hall)[1])
    two = _speed(hall)
    assert two > one


# ── half 2: temperature is the residual ──────────────────────────────────────

def test_a_covered_trip_barely_moves_the_hall(hall, plant_cache):
    """N+1 doing its job. A quarter of the cooling is gone and the load fits in
    what is left, so the hall shows the distribution effect and nothing more."""
    run_all(hall, plant_cache)
    load(hall, 40.0)                       # well inside three units' capacity
    calm = supply(hall)
    stop(plant_cache, units(hall)[0])
    assert supply(hall) - calm < 1.0


def test_a_trip_the_survivors_cannot_cover_warms_the_hall(hall, plant_cache):
    """Same trip, a hall loaded to the edge of its installed capacity. Now the
    unit that stopped is cooling nobody else can supply."""
    run_all(hall, plant_cache)
    _delivered, installed = hall.store._room_cooling_kw(RK)
    load(hall, installed)                  # every unit needed
    calm = supply(hall)
    stop(plant_cache, units(hall)[0])
    assert supply(hall) - calm > 2.0


def test_the_rise_tracks_how_much_is_missing(hall, plant_cache):
    run_all(hall, plant_cache)
    _d, installed = hall.store._room_cooling_kw(RK)
    load(hall, installed)
    stop(plant_cache, units(hall)[0])
    one = supply(hall)
    stop(plant_cache, units(hall)[1])
    two = supply(hall)
    assert two > one


def test_a_total_loss_is_the_full_penalty_and_no_more(hall, plant_cache):
    """The 12 K anchor means "no cooling at all". Adding a distribution penalty
    on top of it would put the room past the figure that is supposed to be the
    worst case, counting the same dead machines twice."""
    from core.device_state_store import _CRAH_UNMET_K

    run_all(hall, plant_cache)
    _d, installed = hall.store._room_cooling_kw(RK)
    load(hall, installed)
    calm = supply(hall)
    stop(plant_cache, *units(hall))
    assert supply(hall) - calm == pytest.approx(_CRAH_UNMET_K, abs=0.01)


def test_a_room_with_no_measured_heat_still_shows_a_total_loss(hall, plant_cache):
    """Cold start, or a hall with no live load. There is no demand to compare
    against, so the headcount is the only honest fallback - and a hall with
    every unit down must not read perfectly cool."""
    run_all(hall, plant_cache)
    load(hall, 0.0)
    calm = supply(hall)
    stop(plant_cache, *units(hall))
    assert supply(hall) - calm > 10.0

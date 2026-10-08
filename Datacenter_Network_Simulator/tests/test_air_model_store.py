"""The spatial air model wired into the store (docs/S2_SPATIAL_THERMAL_MODEL.md §6).

Built on the plant fixture, with its servers and CRAHs laid on the real hall
grid (core/hall_geometry, CRAHs at the aisle ends). Pins the three modes:
uniform never solves the spatial model, shadow solves it and publishes uniform,
spatial publishes it.
"""
from __future__ import annotations

import json

import pytest

from conftest import DC, ROOM, build_plant
from core import hall_geometry as geo

RK = (DC, ROOM)


@pytest.fixture
def hall(tmp_path, plant_cache):
    h = build_plant(tmp_path, crahs=4, servers=48)
    width = geo.hall_width(13, geo.HALL_X0)
    depth = 12.3
    h.store._topology.floorplan = {"rooms": {f"{DC}/{ROOM}": {
        "class": "white_space", "containment": "cold_aisle",
        "width_m": width, "depth_m": depth, "grid_x0_m": geo.HALL_X0}}}
    servers = sorted((d for d in h.dm.get_all_devices() if d.device_type.value == "server"),
                     key=lambda d: d.name)
    # 48 servers: rows 2 and 3, 12 racks each, 2 servers per rack (U10, U30).
    for i, d in enumerate(servers):
        rack = i // 2
        row, num = (2, rack + 1) if rack < 12 else (3, rack - 11)
        sl = geo.slot(row, num)
        d.rack_row, d.rack_num, d.rack_unit = row, num, (10 if i % 2 == 0 else 30)
        d.floor_x, d.floor_y = geo.rack_x(num, geo.HALL_X0), sl.floor_y
        d.rack_facing, d.hot_aisle = sl.rack_facing, sl.hot_aisle
        d.room = ROOM
    for n, (x, y, rot) in zip((h.made[f"CRAH{i}"] for i in range(1, 5)),
                              geo.crah_positions(width, depth, 4), strict=True):
        n.floor_x, n.floor_y, n.rotation_deg, n.room = x, y, rot, ROOM
    h.store._cool_ctx = None                      # rebuild crah_by_room with the room set
    return h


def units(hall):
    return [hall.made[f"CRAH{i}"].name for i in range(1, 5)]


def warm(hall, plant_cache, mode, fan=60.0, stopped=()):
    hall.store._air_mode = mode
    for n in units(hall):
        pv = plant_cache.setdefault(n, {})
        pv["Unit_Running"] = 0.0 if n in stopped else 1.0
        pv["Supply_Air_Temp"] = 18.0
        pv["Fan_Speed"] = 0.0 if n in stopped else fan
    hall.store._plant_model_by_name = {n: hall.name(n).model_name for n in units(hall)}
    hall.tick()
    hall.store._tick_count += 1                   # a new tick: re-solve


def server(hall, row, num, unit=10):
    return next(d for d in hall.dm.get_all_devices()
                if d.device_type.value == "server" and (d.rack_row, d.rack_num, d.rack_unit) == (row, num, unit))


def test_uniform_never_solves_the_spatial_model(hall, plant_cache):
    warm(hall, plant_cache, "uniform")
    assert hall.store._air_inlet(server(hall, 2, 1), 0.5, 23.0) == 23.0
    assert hall.store._air_rooms == {} and hall.store._rack_air_in == {}


def test_shadow_publishes_uniform_and_records_the_spatial_figure(hall, plant_cache, tmp_path, monkeypatch):
    monkeypatch.setattr(type(hall.store), "_AIR_SHADOW_PATH", str(tmp_path / "shadow.jsonl"))
    warm(hall, plant_cache, "shadow")
    assert hall.store._air_inlet(server(hall, 2, 1), 0.5, 23.0) == 23.0
    assert hall.store._air_shadow[RK][0][0] == "inlet"
    hall.store._tick_count += 1
    hall.store._room_air(RK)                      # next tick flushes the last
    line = json.loads((tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["room"] == ROOM and line["inlet_uniform"]["mean"] == 23.0
    assert line["inlet_spatial"]["mean"] != 23.0


def test_spatial_warms_the_racks_near_a_tripped_unit(hall, plant_cache):
    warm(hall, plant_cache, "spatial", fan=35.0)
    near0 = hall.store._air_inlet(server(hall, 2, 1), 0.5, 0.0)
    far0 = hall.store._air_inlet(server(hall, 2, 12), 0.5, 0.0)
    trip = units(hall)[0]                         # first unit: the x = 0 wall
    warm(hall, plant_cache, "spatial", fan=35.0, stopped={trip})
    near1 = hall.store._air_inlet(server(hall, 2, 1), 0.5, 0.0)
    far1 = hall.store._air_inlet(server(hall, 2, 12), 0.5, 0.0)
    assert near1 - near0 > far1 - far0


def test_spatial_gives_each_unit_its_own_return(hall, plant_cache):
    # The west end's racks exhaust hotter: the west-wall units read it.
    for d in hall.dm.get_all_devices():
        if d.device_type.value == "server":
            d.outlet_temp = 40.0 if d.rack_num <= 4 else 30.0
    warm(hall, plant_cache, "spatial")
    width = geo.hall_width(13, geo.HALL_X0)
    rets = {n: hall.store._air_return(RK, n) for n in units(hall)}
    west = [r for n, r in rets.items() if hall.name(n).floor_x < width / 2]
    east = [r for n, r in rets.items() if hall.name(n).floor_x > width / 2]
    assert west and east and min(west) > max(east)


def test_spatial_humidity_keeps_one_dew_point_per_room(hall, plant_cache):
    from core import air_model as am
    warm(hall, plant_cache, "spatial")
    d = server(hall, 2, 1)
    cold = hall.store._air_rh(d, 20.0)
    hot = hall.store._air_rh(d, 32.0)
    assert hot < cold
    w = hall.store._room_w_g_kg[RK]
    assert am.rh_from_ratio(20.0, w) == pytest.approx(cold)


@pytest.fixture
def pdu_hall(tmp_path, plant_cache):
    """The same hall with a rack PDU (and its probe) standing in rack 2/1."""
    h = build_plant(tmp_path, crahs=4, servers=4, rack_probes=1)
    width = geo.hall_width(13, geo.HALL_X0)
    h.store._topology.floorplan = {"rooms": {f"{DC}/{ROOM}": {
        "class": "white_space", "containment": "cold_aisle",
        "width_m": width, "depth_m": 12.3, "grid_x0_m": geo.HALL_X0}}}
    sl = geo.slot(2, 1)
    for d in h.dm.get_all_devices():
        if d.device_type.value in ("server", "pdu", "sensor"):
            d.rack_row, d.rack_num, d.rack_unit = 2, 1, (20 if d.device_type.value == "server" else 0)
            d.floor_x, d.floor_y = geo.rack_x(1, geo.HALL_X0), sl.floor_y
            d.rack_facing, d.hot_aisle, d.room = sl.rack_facing, sl.hot_aisle, ROOM
    for n, (x, y, rot) in zip((h.made[f"CRAH{i}"] for i in range(1, 5)),
                              geo.crah_positions(width, 12.3, 4), strict=True):
        n.floor_x, n.floor_y, n.rotation_deg, n.room = x, y, rot, ROOM
    h.store._cool_ctx = None
    return h


def test_a_rack_pdu_probe_reads_the_rooms_moisture_in_spatial_mode(pdu_hall, plant_cache):
    from core import air_model as am
    warm(pdu_hall, plant_cache, "spatial")
    pdu = pdu_hall.made["PDUA"]
    for _ in range(2):                           # the ticker's order: device, then ext state
        pdu_hall.store._step_device(pdu)
        pdu_hall.store._step_ext_state(pdu)
    st = pdu_hall.store._ext_states[pdu.name]
    if "pdu_humidity" not in st or "pdu_temperature" not in st:
        pytest.skip("this PDU model publishes no probe temperature and humidity")
    w = pdu_hall.store._room_w_g_kg[RK]
    assert st["pdu_humidity"] == pytest.approx(am.rh_from_ratio(st["pdu_temperature"], w), abs=0.06)
    # one dew point for the room, whichever probe reports it
    assert am.dew_point_from_ratio(w) == pytest.approx(
        am.dew_point_from_ratio(am.humidity_ratio(st["pdu_temperature"], st["pdu_humidity"])), abs=0.1)


def test_a_rack_pdu_probe_keeps_its_walk_in_uniform_mode(pdu_hall, plant_cache):
    warm(pdu_hall, plant_cache, "uniform")
    pdu = pdu_hall.made["PDUA"]
    pdu_hall.store._step_device(pdu)
    pdu_hall.store._step_ext_state(pdu)
    st = pdu_hall.store._ext_states[pdu.name]
    if "pdu_humidity" in st:
        assert 35.0 <= st["pdu_humidity"] <= 65.0
    assert RK not in pdu_hall.store._room_w_g_kg

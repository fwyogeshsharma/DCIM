"""Fire detection and gas suppression, as a BMS sees them (docs/FIRE_SYSTEM.md).

A fire alarm panel, an aspirating smoke detector per hall and a clean-agent
release panel per hall, on the BMS trunk. What must hold: the detector's
stages are the vendor's thresholds, the release panel behaves like one - first
alarm, a countdown, an abort that holds, a release that latches - nothing is
writable, and every site in the shipped topology has the set.
"""
import json
from pathlib import Path

import pytest

from core.device_state_store import (
    _AGENT_BAR, _AGENT_LOW_BAR, _ASD_BACKGROUND, _ASD_FIRE1, _ASD_FIRE2, _GAS_ABORT_HOLD_S,
    _GAS_DELAY_S, _POINT_ROLES, _PROBE_ROLES, FIRE_ZONE_BIT, GAS_ABORT, GAS_FIRST_ALARM,
    GAS_NORMAL, GAS_PRE_DISCHARGE, GAS_RELEASED, _probe_role_by_name, asd_stage, asd_step,
    gas_release_step,
)
from core.modbus_register_map import (
    SPACE_COIL, SPACE_DISCRETE, SPACE_HOLDING, SPACE_INPUT, get_probe_map,
)

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"
ROLES = (("FACP", "fire_panel"), ("ASD", "smoke_aspirating"), ("GSP", "gas_release"))


@pytest.fixture(scope="module")
def topo():
    return json.loads(TOPO.read_text(encoding="utf-8"))


def test_each_instrument_has_a_role_and_a_map_and_is_not_air():
    for code, role in ROLES:
        assert _PROBE_ROLES[code] == role and _probe_role_by_name(f"{code}-DC1-HA") == role
        assert role in _POINT_ROLES
        assert get_probe_map(role) is not None


def test_life_safety_is_read_only():
    """A BMS monitors a fire system and never commands it: no coils, no
    holding registers, on any of the three."""
    for _, role in ROLES:
        m = get_probe_map(role)
        assert set(m.points) <= {SPACE_INPUT, SPACE_DISCRETE}, role
        assert SPACE_COIL not in m.points and SPACE_HOLDING not in m.points


def test_the_panel_reports_the_three_states_every_panel_has():
    m = get_probe_map("fire_panel")
    assert [p.name for p in m.points[SPACE_DISCRETE]] == ["Fire_Alarm", "Trouble", "Supervisory", "Reading_Valid"]
    assert [p.name for p in m.points[SPACE_INPUT]] == ["Zones_In_Alarm", "Zone_Alarm_Count"]
    assert len(set(FIRE_ZONE_BIT.values())) == len(FIRE_ZONE_BIT) and max(FIRE_ZONE_BIT.values()) < 16


def test_the_detector_reads_obscuration_per_metre_to_a_thousandth():
    m = get_probe_map("smoke_aspirating")
    smoke = m.points[SPACE_INPUT][0]
    assert smoke.units == "%/m" and smoke.scale == 1000
    # A clean hall, at that resolution, is still a number above zero.
    assert round(_ASD_BACKGROUND * smoke.scale) > 0


def test_the_stages_are_the_vendor_thresholds():
    assert [asd_stage(v) for v in (0.01, 0.079, 0.08, 0.14, 0.2, 1.99, 2.0, 12.0)] == [0, 0, 1, 2, 3, 3, 4, 4]


def test_smoke_takes_its_transport_time_to_reach_the_chamber():
    o = _ASD_BACKGROUND
    o = asd_step(o, 1.0, 5.0)
    assert _ASD_BACKGROUND < o < _ASD_FIRE1          # five seconds in: not there yet
    for _ in range(40):
        o = asd_step(o, 1.0, 5.0)
    assert o == pytest.approx(1.0, abs=0.01)         # and then it is
    assert asd_step(1.0, _ASD_BACKGROUND, 0.0) == 1.0


def test_fire_1_sounds_and_releases_nothing():
    assert gas_release_step(GAS_NORMAL, 0.0, 3, False, 5.0) == (GAS_FIRST_ALARM, 0.0)
    # An hour of Fire 1 is still only Fire 1.
    assert gas_release_step(GAS_FIRST_ALARM, 0.0, 3, False, 3600.0) == (GAS_FIRST_ALARM, 0.0)


def test_fire_2_counts_down_and_then_discharges():
    state, cd = gas_release_step(GAS_FIRST_ALARM, 0.0, 4, False, 2.0)
    assert state == GAS_PRE_DISCHARGE and cd == _GAS_DELAY_S - 2.0
    for _ in range(13):
        state, cd = gas_release_step(state, cd, 4, False, 2.0)
    assert state == GAS_PRE_DISCHARGE and cd == pytest.approx(2.0)
    assert gas_release_step(state, cd, 4, False, 2.0) == (GAS_RELEASED, 0.0)


def test_the_abort_switch_holds_the_countdown_and_letting_go_resumes_it():
    state, cd = gas_release_step(GAS_NORMAL, 0.0, 4, False, 1.0)
    for _ in range(100):                               # held for a long time
        state, cd = gas_release_step(state, cd, 4, True, 1.0)
    assert state == GAS_ABORT and cd == _GAS_ABORT_HOLD_S
    state, cd = gas_release_step(state, cd, 4, False, 1.0)
    assert state == GAS_PRE_DISCHARGE and cd == _GAS_ABORT_HOLD_S - 1.0
    for _ in range(10):
        state, cd = gas_release_step(state, cd, 4, False, 1.0)
    assert state == GAS_RELEASED


def test_an_alarm_latches_and_smoke_thinning_out_does_not_stand_the_panel_down():
    """NFPA 72: a control unit holds an alarm until it is reset by hand."""
    # A first alarm stays a first alarm with the room clear again.
    assert gas_release_step(GAS_FIRST_ALARM, 0.0, 0, False, 60.0) == (GAS_FIRST_ALARM, 0.0)
    # A countdown that has started runs on to discharge.
    state, cd = gas_release_step(GAS_NORMAL, 0.0, 4, False, 5.0)
    assert state == GAS_PRE_DISCHARGE
    state, cd = gas_release_step(state, cd, 0, False, 5.0)
    assert state == GAS_PRE_DISCHARGE and cd == _GAS_DELAY_S - 10.0
    for _ in range(4):
        state, cd = gas_release_step(state, cd, 0, False, 5.0)
    assert state == GAS_RELEASED
    # Held by the abort with the smoke gone, it is still held - and still armed.
    state, cd = gas_release_step(GAS_ABORT, _GAS_ABORT_HOLD_S, 0, True, 60.0)
    assert (state, cd) == (GAS_ABORT, _GAS_ABORT_HOLD_S)
    assert gas_release_step(state, cd, 0, False, 60.0) == (GAS_RELEASED, 0.0)
    # And nothing below the first threshold has ever started anything.
    assert gas_release_step(GAS_NORMAL, 0.0, 2, False, 3600.0) == (GAS_NORMAL, 0.0)


def test_a_release_latches_whatever_the_smoke_does_afterwards():
    for stage in (0, 3, 4):
        for abort in (False, True):
            assert gas_release_step(GAS_RELEASED, 0.0, stage, abort, 60.0) == (GAS_RELEASED, 0.0)


def test_the_cylinder_switch_sits_under_the_charge_pressure():
    assert 0 < _AGENT_LOW_BAR < _AGENT_BAR
    assert _ASD_FIRE1 < _ASD_FIRE2


# ── the store, end to end ────────────────────────────────────────────────────

def test_a_fire_in_one_hall_walks_detector_release_panel_and_site_panel(plant, monkeypatch):
    """Through the store's own tick. Smoke in Hall A: the detector climbs, the
    hall's panel counts down and discharges, the site panel names the zone;
    Hall B stays quiet; a reset puts it all back."""
    from core import device_state_store as dss
    from tests.conftest import _device

    for n, (name, model) in enumerate((
            ("ASD-DC1-HA", "Plant Aspirating Smoke Detector"), ("ASD-DC1-HB", "Plant Aspirating Smoke Detector"),
            ("GSP-DC1-HA", "Plant Gas Release Panel"), ("GSP-DC1-HB", "Plant Gas Release Panel"),
            ("FACP-DC1-OR", "Plant Fire Alarm Panel"))):
        _device(plant.dm, name, "sensor", f"10.9.9.{n + 1}", 0, model=model)
    store = plant.store
    clock = [1_000_000.0]
    monkeypatch.setattr(dss.time, "time", lambda: clock[0])

    def tick(n=1, dt=2.0):
        for _ in range(n):
            clock[0] += dt
            plant.tick()

    tick(2)
    quiet = store._fire_pub
    assert quiet["ASD-DC1-HA"]["smoke_stage"] == 0 and quiet["ASD-DC1-HA"]["smoke_obsc_pct_m"] < 0.02
    assert quiet["GSP-DC1-HA"] == {"gas_state": 0.0, "gas_countdown_s": 0.0, "agent_pressure_bar": _AGENT_BAR,
                                   "agent_released": 0.0, "gas_abort": 0.0, "agent_low_pressure": 0.0}
    assert quiet["FACP-DC1-OR"] == {"fire_alarm": 0.0, "fire_trouble": 0.0, "fire_supervisory": 0.0,
                                    "fire_zones": 0.0, "fire_zone_count": 0.0}
    assert store._probe_reading["FACP-DC1-OR"] == ("fire_panel", 0.0)

    # A blocked pipe is a trouble, not an alarm.
    store.set_asd_flow_fault("ASD-DC1-HB", True)
    tick()
    assert store._fire_pub["FACP-DC1-OR"]["fire_trouble"] == 1.0 and store._fire_pub["FACP-DC1-OR"]["fire_alarm"] == 0.0
    store.set_asd_flow_fault("ASD-DC1-HB", False)

    store.set_smoke("DC1", "ha", 5.0)
    states = []
    for _ in range(60):
        tick()
        states.append(int(store._fire_pub["GSP-DC1-HA"]["gas_state"]))
    assert GAS_FIRST_ALARM in states and GAS_PRE_DISCHARGE in states and states[-1] == GAS_RELEASED
    assert states == sorted(states)                    # it only ever goes forward
    pub = store._fire_pub
    assert pub["ASD-DC1-HA"]["smoke_stage"] == 4 and pub["ASD-DC1-HB"]["smoke_stage"] == 0
    assert pub["GSP-DC1-HA"]["agent_released"] == 1.0 and pub["GSP-DC1-HA"]["agent_low_pressure"] == 1.0
    assert pub["GSP-DC1-HA"]["agent_pressure_bar"] < _AGENT_LOW_BAR
    assert pub["GSP-DC1-HB"]["gas_state"] == GAS_NORMAL and pub["GSP-DC1-HB"]["agent_pressure_bar"] == _AGENT_BAR
    assert pub["FACP-DC1-OR"] == {"fire_alarm": 1.0, "fire_trouble": 0.0, "fire_supervisory": 1.0,
                                  "fire_zones": float(1 << FIRE_ZONE_BIT["HA"]), "fire_zone_count": 1.0}

    # Smoke clearing refills no cylinder and silences no panel: both latch.
    store.set_smoke("DC1", "HA", None)
    tick(60)
    assert store._fire_pub["ASD-DC1-HA"]["smoke_stage"] == 0
    assert store._fire_pub["GSP-DC1-HA"]["agent_released"] == 1.0
    assert store._fire_pub["FACP-DC1-OR"]["fire_alarm"] == 1.0 and store._fire_pub["FACP-DC1-OR"]["fire_supervisory"] == 1.0

    store.reset_fire("DC1")
    tick()
    assert store._fire_pub["GSP-DC1-HA"]["gas_state"] == GAS_NORMAL
    assert store._fire_pub["FACP-DC1-OR"]["fire_supervisory"] == 0.0
    assert store._fire_pub["FACP-DC1-OR"]["fire_alarm"] == 0.0


def test_a_held_abort_is_a_supervisory_and_nothing_discharges(plant, monkeypatch):
    from core import device_state_store as dss
    from tests.conftest import _device

    for n, (name, model) in enumerate((("ASD-DC1-HA", "Plant Aspirating Smoke Detector"),
                                       ("GSP-DC1-HA", "Plant Gas Release Panel"),
                                       ("FACP-DC1-OR", "Plant Fire Alarm Panel"))):
        _device(plant.dm, name, "sensor", f"10.9.8.{n + 1}", 0, model=model)
    store = plant.store
    clock = [2_000_000.0]
    monkeypatch.setattr(dss.time, "time", lambda: clock[0])
    store.set_smoke("DC1", "HA", 5.0)
    store.hold_gas_abort("DC1", "HA", True)
    for _ in range(120):
        clock[0] += 2.0
        plant.tick()
    assert store._fire_pub["GSP-DC1-HA"]["gas_state"] == GAS_ABORT
    assert store._fire_pub["GSP-DC1-HA"]["gas_countdown_s"] == _GAS_ABORT_HOLD_S
    assert store._fire_pub["GSP-DC1-HA"]["agent_pressure_bar"] == _AGENT_BAR
    assert store._fire_pub["FACP-DC1-OR"]["fire_supervisory"] == 1.0


def test_the_release_panel_stops_the_rooms_air_handlers_and_a_reset_restarts_them(
        tmp_path, plant_cache, monkeypatch):
    """The HVAC interlock. From the start of the countdown the hall's CRAHs
    are stopped so the agent stays in the room; they stay stopped through the
    discharge, with the smoke gone, until the panel is reset. Stopped on the
    autonomous channel, never as an operator's override."""
    from core import device_state_store as dss
    from tests.conftest import ROOM, _device, build_plant

    plant = build_plant(tmp_path, crahs=3)
    for n, (name, model) in enumerate((("ASD-DC1-HA", "Plant Aspirating Smoke Detector"),
                                       ("GSP-DC1-HA", "Plant Gas Release Panel"),
                                       ("FACP-DC1-OR", "Plant Fire Alarm Panel"))):
        _device(plant.dm, name, "sensor", f"10.9.7.{n + 1}", 0, model=model, room=ROOM)
    store = plant.store
    store._cool_ctx = None                             # the instruments joined after the context was built
    crahs = sorted(n for names in store._cooling_context()["crah_by_room"].values() for n in names)
    assert len(crahs) == 3
    clock = [3_000_000.0]
    monkeypatch.setattr(dss.time, "time", lambda: clock[0])

    def tick(n=1):
        for _ in range(n):
            clock[0] += 2.0
            plant.tick()

    def stopped():
        return sorted(c for c in crahs if plant.auto_points(c).get("Unit_Running") == 0.0)

    tick(2)
    assert stopped() == [] and store._fire_hvac_off["DC1"] == []

    # Fire 1 sounds; the air keeps moving - one detector does not cost a hall its cooling.
    store.set_smoke("DC1", "HA", 0.5)
    tick(60)
    assert store._fire_pub["GSP-DC1-HA"]["gas_state"] == GAS_FIRST_ALARM and stopped() == []

    # Fire 2: the countdown starts and the air handlers stop with it.
    store.set_smoke("DC1", "HA", 5.0)
    store.hold_gas_abort("DC1", "HA", True)            # hold it in the countdown to look
    tick(60)
    assert store._fire_pub["GSP-DC1-HA"]["gas_state"] == GAS_ABORT
    assert stopped() == crahs and store._fire_hvac_off["DC1"] == crahs
    assert store.plant_alarm_overrides == {}           # not an operator's doing

    store.hold_gas_abort("DC1", "HA", False)
    tick(10)
    assert store._fire_pub["GSP-DC1-HA"]["gas_state"] == GAS_RELEASED and stopped() == crahs

    # The hold time: smoke gone, agent in the room, air still off.
    store.set_smoke("DC1", "HA", None)
    tick(60)
    assert stopped() == crahs

    store.reset_fire("DC1")
    tick()
    assert stopped() == [] and store._fire_hvac_off["DC1"] == []


def test_scenario_setters_take_a_room_code_in_either_case():
    from core import device_state_store as dss

    store = dss.DeviceStateStore.__new__(dss.DeviceStateStore)
    store._smoke_room, store._asd_flow_fault, store._gas_abort = {}, set(), set()
    store.set_smoke("DC2", "hb", 0.3)
    assert store._smoke_room == {("DC2", "HB"): 0.3}
    store.set_smoke("DC2", "HB", None)
    assert store._smoke_room == {}
    store.hold_gas_abort("DC2", "hb", True)
    assert ("DC2", "HB") in store._gas_abort
    store.hold_gas_abort("DC2", "HB", False)
    assert store._gas_abort == set()
    store.set_asd_flow_fault("ASD-DC2-HB", True)
    assert store._asd_flow_fault == {"ASD-DC2-HB"}


# ── the shipped topology ────────────────────────────────────────────────────

def test_every_site_has_a_panel_and_each_hall_a_detector_and_a_release_panel(topo):
    devs = {n["device"]["name"]: n["device"] for n in topo["nodes"]}
    rooms = topo["floorplan"]["rooms"]
    for dc in ("DC1", "DC2"):
        gw = next(d for d in devs.values() if d["device_type"] == "modbus_gateway" and d["datacenter"] == dc)
        for code, suffix, room in (("FACP", "OR", "Operations Room"), ("ASD", "HA", "Server Hall A"),
                                   ("ASD", "HB", "Server Hall B"), ("GSP", "HA", "Server Hall A"),
                                   ("GSP", "HB", "Server Hall B")):
            name = f"{code}-{dc}-{suffix}"
            d = devs[name]
            assert d["device_type"] == "sensor" and d["room"] == room
            assert d["model_name"].startswith("Plant ")
            assert d["modbus_role"] == "rtu_slave" and d["modbus_gateway_ip"] == gw["mgmt_ip"]
            assert name in gw["modbus_children"]
            # No address of its own: it is a feed into the BMS, not a host.
            assert not d["mgmt_ip"] and not d["ip_address"]
            r = rooms[f"{dc}/{room}"]
            assert 0 <= d["floor_x"] <= r["width_m"] and 0 <= d["floor_y"] <= r["depth_m"], name
        unit_ids = [d["modbus_unit_id"] for d in devs.values() if d.get("modbus_gateway_ip") == gw["mgmt_ip"]]
        assert len(unit_ids) == len(set(unit_ids)), "unit ids clash on the trunk"

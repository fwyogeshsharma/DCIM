"""A room with no racks still has to have a thermometer in it.

Every environmental probe in this estate hangs off a rack PDU's sensor port, so
the switchroom and the generator hall - which hold no racks and therefore no
PDU - carried none at all. The instrument a real site fits is a two-channel
transmitter on the BMS's RS-485 trunk, which is the same trunk the chilled-water
thermowells are already on.

What must not happen is the thing that makes a plant instrument dangerous: a
device answering for a quantity it does not measure. A thermowell has no
humidity, and a room transmitter has no loop water.
"""

from core.device_state_store import (
    _PROBE_ROLES, _ROOM_AIR_SETPOINT_C, _ROOM_AIR_WEATHER_K, _WATER_ROLES,
    _probe_role_by_name,
)
from core.modbus_register_map import PROBE_MAPS, get_probe_map


def test_the_room_transmitter_has_a_role_of_its_own():
    assert _PROBE_ROLES["THR"] == "room_air"
    # No index in the name: the role is the prefix up to the first "-", and
    # there is one transmitter per room in any case.
    assert _probe_role_by_name("THR-DC1-UR") == "room_air"


def test_room_air_is_not_a_water_role():
    """The cold-aisle air rules must reach it.

    They are kept off the thermowells because 35 C condenser return is healthy
    and a thermowell has no humidity to be low. A room transmitter measures
    exactly what those rules are about, so it has to be outside that set or it
    would be the one instrument in the building nobody could alarm on.
    """
    assert "room_air" not in _WATER_ROLES
    assert _WATER_ROLES == {"chw_supply", "chw_return", "cw_supply",
                            "cw_return", "ct_basin", "chw_flow"}


def test_it_reports_two_channels_and_no_water():
    m = get_probe_map("room_air")
    assert m is not None and m.map_id == "SIM-ROOMTH-TX-v1"
    names = {p.name for pts in m.points.values() for p in pts}
    assert {"Room_Temperature", "Room_Humidity"} <= names
    keys = {p.key for pts in m.points.values() for p in pts if p.key}
    assert "water_temp" not in keys and "water_flow_lps" not in keys


def test_every_role_can_be_decoded():
    """A transmitter whose role has no map is a device nothing can read."""
    for role in set(_PROBE_ROLES.values()):
        assert get_probe_map(role) is not None, role
    assert set(PROBE_MAPS) == set(_PROBE_ROLES.values())


def test_each_kind_of_room_runs_at_its_own_temperature():
    """These rooms are not one room, and one number for all of them says so.

    A battery hall is cooled tightest - VRLA life is written against 25 C and
    halves per ~10 K above it, so a site spends money holding it down. A
    chiller plant is the warmest: pumps, compressors, and a room whose entire
    job is to reject heat, usually on ventilation alone.
    """
    assert _ROOM_AIR_SETPOINT_C["UR"] < _ROOM_AIR_SETPOINT_C["MR"]
    assert _ROOM_AIR_SETPOINT_C["MR"] <= _ROOM_AIR_SETPOINT_C["CP"]
    assert 20.0 <= _ROOM_AIR_SETPOINT_C["UR"] <= 25.0
    assert _ROOM_AIR_SETPOINT_C["CP"] <= 30.0


def test_a_ventilated_room_follows_the_weather_and_a_cooled_one_barely_does():
    """Outdoor air is what cools a plant hall, so it has to move with it.

    None of them is zero. A room pinned to its setpoint whatever the weather
    is a room nobody is measuring.
    """
    assert _ROOM_AIR_WEATHER_K["UR"] < _ROOM_AIR_WEATHER_K["CP"]
    assert min(_ROOM_AIR_WEATHER_K.values()) > 0.0


def test_the_roof_gets_no_room_transmitter():
    """It is outdoors. The towers on it already carry the site's outdoor
    sensor, because a tower is controlled to approach wet bulb - a second
    instrument there would be a second name for the same measurement."""
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_add_room_air_probes",
        pathlib.Path(__file__).resolve().parent.parent
        / "tools" / "add_room_air_probes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "Roof" not in mod.ROOMS
    assert set(mod.ROOMS.values()) == {"UR", "GR", "CP", "MR"}

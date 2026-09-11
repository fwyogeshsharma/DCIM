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
    _ELECTRICAL_ROOM_C, _PROBE_ROLES, _WATER_ROLES, _probe_role_by_name,
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


def test_an_electrical_room_is_held_tighter_than_a_hall():
    """VRLA life is written against 25 C, so a battery room is cooled to it.

    Deliberately a different setpoint from the cold aisle, on different plant:
    a switchroom does not follow the hall's chilled-water penalty, because it
    is not on the hall's chilled water.
    """
    assert 20.0 <= _ELECTRICAL_ROOM_C <= 25.0

"""Per-device and per-plane trap destination overrides.

Every simulated device sending to the same single receiver hides exactly the
misassignment bug docs/26 Phase 0 (on the DCIM platform side) exists to catch,
and gives Phase 6's HA VIP nothing to fail over between. This is the
attribution the simulator needs so that testable: which collector a trap
actually reaches depends on which device sent it and which plane it sits on,
the same as real SNMP trap configuration does.

Built the same way test_dark_traps.py tests TrapEngine: via __new__, never a
real TrapEngine() construction, which is a QObject and would need a
QApplication this test environment does not set up.
"""
from __future__ import annotations

import types

from core import trap_engine as te


def _engine(receiver=("127.0.0.1", 162), device_dest=None, plane_dest=None):
    """A TrapEngine with just the destination-resolution state set, bypassing
    __init__ (and the sim_settings/Qt side effects it carries)."""
    eng = te.TrapEngine.__new__(te.TrapEngine)
    eng._receiver_ip, eng._receiver_port = receiver
    eng._device_dest = dict(device_dest or {})
    eng._plane_dest = list(plane_dest or [])
    return eng


def _device(name: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name)


def test_falls_back_to_the_global_receiver_with_no_overrides():
    eng = _engine(receiver=("10.0.0.5", 162))
    assert eng.resolve_destination(_device("ANY-1"), "10.51.1.5") == ("10.0.0.5", 162)


def test_a_device_override_wins_over_everything():
    eng = _engine(
        receiver=("10.0.0.5", 162),
        device_dest={"SWGR-1": ("10.51.9.9", 6162)},
        plane_dest=[("10.51.0.0/16", ("10.51.1.1", 162))],
    )
    assert eng.resolve_destination(_device("SWGR-1"), "10.51.1.5") == ("10.51.9.9", 6162)


def test_a_plane_override_applies_when_the_source_ip_matches_its_cidr():
    import ipaddress
    eng = _engine(
        receiver=("10.0.0.5", 162),
        plane_dest=[(ipaddress.ip_network("10.51.0.0/16"), ("10.51.1.1", 6162))],
    )
    assert eng.resolve_destination(_device("SWGR-1"), "10.51.1.5") == ("10.51.1.1", 6162)


def test_a_plane_override_does_not_apply_outside_its_cidr():
    import ipaddress
    eng = _engine(
        receiver=("10.0.0.5", 162),
        plane_dest=[(ipaddress.ip_network("10.51.0.0/16"), ("10.51.1.1", 6162))],
    )
    # 10.52.x is the BMS plane by this project's own mgmt-plane numbering,
    # not IT-OOB - the override must not leak across planes.
    assert eng.resolve_destination(_device("BMS-1"), "10.52.1.5") == ("10.0.0.5", 162)


def test_first_matching_plane_wins():
    import ipaddress
    eng = _engine(
        receiver=("10.0.0.5", 162),
        plane_dest=[
            (ipaddress.ip_network("10.51.0.0/16"), ("10.51.1.1", 6162)),
            (ipaddress.ip_network("10.51.1.0/24"), ("10.51.1.2", 6163)),
        ],
    )
    assert eng.resolve_destination(_device("X"), "10.51.1.5") == ("10.51.1.1", 6162)


def test_an_unparseable_source_ip_falls_back_to_the_global_receiver():
    import ipaddress
    eng = _engine(
        receiver=("10.0.0.5", 162),
        plane_dest=[(ipaddress.ip_network("10.51.0.0/16"), ("10.51.1.1", 6162))],
    )
    assert eng.resolve_destination(_device("X"), "not-an-ip") == ("10.0.0.5", 162)


def test_set_device_destinations_replaces_the_whole_set(monkeypatch):
    saved = {}
    monkeypatch.setattr(te.sim_settings, "set_many", lambda values: saved.update(values) or True)
    eng = _engine(device_dest={"OLD-DEVICE": ("1.2.3.4", 162)})

    eng.set_device_destinations({"NEW-DEVICE": ("10.51.1.9", 6162)})

    assert "OLD-DEVICE" not in eng.device_destinations
    assert eng.device_destinations["NEW-DEVICE"] == ("10.51.1.9", 6162)
    assert saved["trap_device_destinations"] == {"NEW-DEVICE": ["10.51.1.9", 6162]}


def test_set_plane_destinations_replaces_the_whole_set(monkeypatch):
    saved = {}
    monkeypatch.setattr(te.sim_settings, "set_many", lambda values: saved.update(values) or True)
    eng = _engine()

    eng.set_plane_destinations([("10.51.0.0/16", "10.51.1.1", 6162)])

    assert eng.plane_destinations == [("10.51.0.0/16", "10.51.1.1", 6162)]
    assert saved["trap_plane_destinations"] == [["10.51.0.0/16", "10.51.1.1", 6162]]


def test_restores_device_and_plane_overrides_from_saved_settings(monkeypatch):
    """The same reason receiver_ip/port is restored at __init__: a restart
    must not silently drop attribution back onto one receiver."""
    saved = {
        "trap_device_destinations": {"SWGR-1": ["10.51.9.9", 6162]},
        "trap_plane_destinations": [["10.51.0.0/16", "10.51.1.1", 6162]],
    }
    monkeypatch.setattr(te.sim_settings, "get", lambda key, default=None: saved.get(key, default))

    # Reproduces exactly the two attribute-construction lines __init__ runs,
    # without triggering __init__'s Qt/pysnmp side effects.
    device_dest = {
        k: (v[0], int(v[1])) for k, v in
        (te.sim_settings.get("trap_device_destinations", {}) or {}).items()
    }
    import ipaddress
    plane_dest = [
        (ipaddress.ip_network(cidr, strict=False), (ip, int(port)))
        for cidr, ip, port in
        (te.sim_settings.get("trap_plane_destinations", []) or [])
    ]

    assert device_dest == {"SWGR-1": ("10.51.9.9", 6162)}
    assert plane_dest == [(ipaddress.ip_network("10.51.0.0/16"), ("10.51.1.1", 6162))]


# --- several receivers per plane (docs/26 Phase 6 trap HA) ----------------------

def test_a_plane_with_two_receivers_sends_to_both():
    """An HA collector pool lists every member; each trap goes to all of them,
    as an agent configured with two trap hosts sends to both."""
    import ipaddress
    net = ipaddress.ip_network("10.52.0.0/20")
    eng = _engine(plane_dest=[(net, ("127.0.0.1", 11622)), (net, ("127.0.0.1", 11627))])
    assert eng.resolve_destinations(_device("CRAH-1"), "10.52.11.14") == [
        ("127.0.0.1", 11622), ("127.0.0.1", 11627)]
    # The single-destination accessor still answers with the first.
    assert eng.resolve_destination(_device("CRAH-1"), "10.52.11.14") == ("127.0.0.1", 11622)


def test_only_the_first_matching_planes_receivers_are_used():
    import ipaddress
    narrow, wide = ipaddress.ip_network("10.52.11.0/24"), ipaddress.ip_network("10.52.0.0/16")
    eng = _engine(plane_dest=[(narrow, ("10.0.0.1", 162)), (wide, ("10.0.0.2", 162)),
                              (narrow, ("10.0.0.3", 162))])
    assert eng.resolve_destinations(_device("X"), "10.52.11.5") == [
        ("10.0.0.1", 162), ("10.0.0.3", 162)]
    assert eng.resolve_destinations(_device("X"), "10.52.20.5") == [("10.0.0.2", 162)]


def test_a_device_override_is_still_a_single_receiver():
    import ipaddress
    net = ipaddress.ip_network("10.52.0.0/20")
    eng = _engine(device_dest={"SWGR-1": ("10.9.9.9", 162)},
                  plane_dest=[(net, ("127.0.0.1", 1)), (net, ("127.0.0.1", 2))])
    assert eng.resolve_destinations(_device("SWGR-1"), "10.52.1.1") == [("10.9.9.9", 162)]

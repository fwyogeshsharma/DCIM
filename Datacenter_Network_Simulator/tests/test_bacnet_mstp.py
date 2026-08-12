"""BACnet MS/TP devices behind a BACnet/IP router.

Field gear on an RS-485 trunk — a Belimo '-BAC' actuator, a Grundfos CIM 300 pump
card — owns no IP. The router's address carries the packet and the (network, MAC)
pair inside the NPDU says which device on the trunk it is. These tests pin that
addressing, because the failure mode when it is wrong is not an error: it is one
device answering for all eighteen.
"""
from __future__ import annotations

import socket
import struct

import pytest

from core.bacnet_object_model import (
    OBJ_ANALOG_INPUT, SVC_READ_PROPERTY,
    build_iam, enc_app_oid, parse_apdu, parse_bvll, parse_npdu,
    parse_npdu_routed, with_source_route,
)
from simulator.bacnet_controller import BACnetController

HOST = "127.0.0.1"
NET = 2001


# ─────────────────────────────────────────────────────────────────────────────
#  NPDU layer
# ─────────────────────────────────────────────────────────────────────────────
def test_source_route_preserves_the_apdu():
    frame = build_iam(40001)
    plain = parse_npdu(frame[4:])
    routed = parse_npdu_routed(with_source_route(frame, NET, bytes([7]))[4:])
    assert routed["apdu"] == plain


def test_source_route_carries_network_and_mac():
    r = parse_npdu_routed(with_source_route(build_iam(40001), NET, bytes([7]))[4:])
    assert r["snet"] == NET and list(r["sadr"]) == [7]


def test_source_route_fixes_the_bvll_length():
    """A stale length header truncates the APDU at the client, which reads as a
    malformed device rather than a framing bug."""
    f = with_source_route(build_iam(40001), NET, bytes([7]))
    assert (f[2] << 8 | f[3]) == len(f)


def test_source_route_is_idempotent():
    once = with_source_route(build_iam(40001), NET, bytes([7]))
    assert with_source_route(once, NET, bytes([7])) == once


def test_inbound_dadr_is_recoverable():
    """DADR is the only field that says WHICH device on the trunk a request is
    for; parse_npdu discards it, which is why parse_npdu_routed exists."""
    apdu = bytes([0x00, 0x05, 0x01, 0x0C])
    npdu = bytes([0x01, 0x24]) + bytes([0x07, 0xD1, 0x01, 0x09, 0xFF]) + apdu
    r = parse_npdu_routed(npdu)
    assert r["dnet"] == NET and list(r["dadr"]) == [9] and r["apdu"] == apdu


# ─────────────────────────────────────────────────────────────────────────────
#  Live trunk
# ─────────────────────────────────────────────────────────────────────────────
def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def trunk():
    ctrl = BACnetController()
    ctrl.set_log_callback(lambda m, l="info": None)
    port = _free_udp_port()
    assert ctrl.start(device_ips=[HOST], base_instance=40001,
                      circuits_map={HOST: (24, 24)}, port=port,
                      plant_devices=[{"ip": HOST, "name": "MBGW1-DC1-CP",
                                      "device_type": "crah", "rated_kw": 1.0}])
    assert ctrl.add_mstp_device(HOST, mac=3, net=NET, device_type="pump",
                                name="CHWP1-DC1-CP", rated_kw=30)
    assert ctrl.add_mstp_device(HOST, mac=11, net=NET, device_type="valve",
                                name="VCHW-DC1-CP")
    ctrl.tick(1.0)
    yield ctrl, port
    ctrl.stop()


def _read_ai(port: int, mac: int, inst: int):
    apdu = (bytes([0x00, 0x05, 0x01, SVC_READ_PROPERTY])
            + enc_app_oid(OBJ_ANALOG_INPUT, inst) + bytes([0x19, 85]))
    npdu = bytes([0x01, 0x24]) + bytes([0x07, 0xD1, 1, mac, 0xFF]) + apdu
    fr = bytes([0x81, 0x0A, (4 + len(npdu)) >> 8, (4 + len(npdu)) & 0xFF]) + npdu
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(fr, (HOST, port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()
    r = parse_npdu_routed(parse_bvll(data)[1])
    p = parse_apdu(r["apdu"])
    d = p["data"]
    i = d.find(b"\x44")
    val = struct.unpack(">f", d[i + 1:i + 5])[0] if i >= 0 and len(d) >= i + 5 else None
    return r, p["type"], val


def test_each_mac_reaches_its_own_device(trunk):
    """The whole point. Reading AI1 from two MACs must hit two different object
    trees — a pump's Speed and a valve's Position."""
    ctrl, port = trunk
    _, t_pump, v_pump = _read_ai(port, 3, 1)
    _, t_valve, v_valve = _read_ai(port, 11, 1)
    assert t_pump == t_valve == "complex_ack"
    assert v_pump is not None and v_valve is not None
    pump = ctrl._mstp[HOST][3]
    valve = ctrl._mstp[HOST][11]
    assert [n for n, k in pump._name_to_key.items() if k == (0, 1)] == ["Speed"]
    assert [n for n, k in valve._name_to_key.items() if k == (0, 1)] == ["Position"]


def test_replies_carry_the_devices_own_source_route(trunk):
    _, port = trunk
    for mac in (3, 11):
        r, _, _ = _read_ai(port, mac, 1)
        assert r["snet"] == NET, "reply lost its network number"
        assert list(r["sadr"]) == [mac], (
            "reply came back with the wrong MAC — a client would fold the trunk "
            "into one device")


def test_trunk_devices_carry_the_right_object_tree(trunk):
    ctrl, _ = trunk
    assert "Flow" in ctrl._mstp[HOST][3]._name_to_key
    assert "Position" in ctrl._mstp[HOST][11]._name_to_key
    assert "Flow" not in ctrl._mstp[HOST][11]._name_to_key


def test_a_duplicate_mac_is_refused(trunk):
    """Two devices on one MAC is a wiring fault a real trunk cannot express, and
    silently overwriting one would lose a device with no error anywhere."""
    ctrl, _ = trunk
    assert not ctrl.add_mstp_device(HOST, mac=3, net=NET, device_type="pump",
                                    name="COLLIDES")


def test_a_trunk_device_without_a_router_is_refused():
    ctrl = BACnetController()
    ctrl.set_log_callback(lambda m, l="info": None)
    assert not ctrl.add_mstp_device("10.99.99.99", mac=1, net=NET,
                                    device_type="pump", name="ORPHAN")


def test_trunk_devices_own_no_socket(trunk):
    """They share the router's. Binding their own would try to bind the router's
    address a second time, and closing one would take the trunk down."""
    ctrl, _ = trunk
    router = ctrl._devices_by_ip[HOST]
    for mac in (3, 11):
        dev = ctrl._mstp[HOST][mac]
        assert dev._owns_sock is False
        assert dev._send_sock is router._send_sock
        dev.close()
    assert router._send_sock is not None, "a child close took the router's socket"

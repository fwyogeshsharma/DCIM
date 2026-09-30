"""BBMD / Foreign Device Registration (Annex J.5.2).

docs/26 Phase S: this simulator runs on one host, so real subnet isolation is
out of scope this pass (that is the network-namespace item, separately
tracked). What IS built here is the application-level stand-in: a device
flagged FDR-gated answers a DIRECTED Who-Is normally but ignores a BROADCAST
one from anyone who has not registered as a foreign device first - which is
exactly the behaviour dcim-platform's own FDR client
(internal/adapters/bacnet/fdr.go, built docs/26 Phase 9) now has something
real to register against, not only its own test fake.

Every request in this file that needs to reach the wildcard socket alone
(not a device's own) targets 127.0.0.2, not 127.0.0.1 - see WILDCARD_ONLY's
comment for why.
"""
from __future__ import annotations

import socket
import time

import pytest

from core.bacnet_object_model import (
    SVC_WHO_IS, parse_apdu, parse_bvll, parse_npdu_routed,
)
from simulator.bacnet_controller import BACnetController

HOST = "127.0.0.1"
GATED_INSTANCE = 40001

# A per-device socket binds to HOST (127.0.0.1) specifically; the wildcard
# binds to 0.0.0.0, which covers every loopback address including this one.
# A request sent to HOST therefore lands on the device's own, more-specific
# socket even when the sender's INTENT is "broadcast" - the same
# most-specific-wins routing per-device sockets rely on for real unicast
# traffic (see BACnetController._recv_loop's own docstring). 127.0.0.2 is
# still loopback - so a reply sent from a HOST-bound device socket can still
# reach it, unlike a real non-loopback LAN address, which Windows refuses to
# route to from a socket explicitly bound to 127.0.0.1 - but it is not
# HOST's own more-specific bind, so it reaches the wildcard alone.
WILDCARD_ONLY = "127.0.0.2"


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _register_foreign_device(port: int, ttl: int) -> bytes:
    """Sends a real Register-Foreign-Device BVLC frame and returns whatever
    came back (a BVLC-Result).

    Targeting WILDCARD_ONLY here too, even though registration is handled
    before the per-device/wildcard split and would work either way, keeps
    this call's source address consistent with _broadcast_whois's - FDR
    registration is matched by source IP alone
    (BACnetController._foreign_devices), so the two must agree on it or the
    registration would not be found."""
    frame = bytes([0x81, 0x05, 0x00, 0x06, (ttl >> 8) & 0xFF, ttl & 0xFF])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(frame, (WILDCARD_ONLY, port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()
    return data


def _broadcast_whois(port: int) -> list:
    """Sends a plain, unrouted, global Who-Is (no instance range) to
    WILDCARD_ONLY and collects every I-Am reply within the timeout - that
    address is what reaches the wildcard fan-out (and this test's FDR
    gating) without also hitting a device's own socket."""
    apdu = bytes([0x10, SVC_WHO_IS])                 # unconfirmed-request, no range
    npdu = bytes([0x01, 0x00]) + apdu                 # version 1, no routing/DNET
    frame = bytes([0x81, 0x0B, (4 + len(npdu)) >> 8, (4 + len(npdu)) & 0xFF]) + npdu
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.6)
    try:
        s.sendto(frame, (WILDCARD_ONLY, port))
        replies = []
        try:
            while True:
                data, _ = s.recvfrom(2048)
                replies.append(data)
        except socket.timeout:
            pass
    finally:
        s.close()
    return replies


def _directed_whois(dest_port: int) -> list:
    """A Who-Is sent to loopback - the device's OWN per-device socket, the
    unicast/directed path FDR gating must never affect."""
    apdu = bytes([0x10, SVC_WHO_IS])
    npdu = bytes([0x01, 0x00]) + apdu
    frame = bytes([0x81, 0x0A, (4 + len(npdu)) >> 8, (4 + len(npdu)) & 0xFF]) + npdu
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.6)
    try:
        s.sendto(frame, (HOST, dest_port))
        replies = []
        try:
            while True:
                data, _ = s.recvfrom(2048)
                replies.append(data)
        except socket.timeout:
            pass
    finally:
        s.close()
    return replies


@pytest.fixture
def gated():
    ctrl = BACnetController()
    ctrl.set_log_callback(lambda m, l="info": None)
    port = _free_udp_port()
    assert ctrl.start(device_ips=[HOST], base_instance=GATED_INSTANCE,
                      circuits_map={HOST: (4, 4)}, port=port)
    ctrl.set_fdr_gated_instances([GATED_INSTANCE])
    ctrl.tick(1.0)
    yield ctrl, port
    ctrl.stop()


def test_register_foreign_device_is_acknowledged_with_a_bvlc_result(gated):
    _, port = gated
    reply = _register_foreign_device(port, ttl=60)
    func, payload = parse_bvll(reply)
    assert func == 0x00  # BVLC-Result
    assert payload == b"\x00\x00"  # success


def test_registration_appears_in_foreign_devices(gated):
    ctrl, port = gated
    _register_foreign_device(port, ttl=60)
    fds = ctrl.foreign_devices()
    assert len(fds) == 1
    ttl_remaining = next(iter(fds.values()))
    assert 0 < ttl_remaining <= 60


def test_gated_device_ignores_a_broadcast_whois_from_an_unregistered_source(gated):
    _, port = gated
    assert _broadcast_whois(port) == []


def test_gated_device_answers_a_broadcast_whois_once_registered(gated):
    _, port = gated
    _register_foreign_device(port, ttl=60)
    replies = _broadcast_whois(port)
    assert len(replies) == 1
    func, npdu = parse_bvll(replies[0])
    apdu = parse_npdu_routed(npdu)["apdu"]
    assert parse_apdu(apdu)["type"] == "unconfirmed"


def test_a_directed_whois_reaches_a_gated_device_without_registering(gated):
    """FDR gates DISCOVERY (broadcast), never REACHABILITY (a directed
    request to an address already known) - the same split Annex J itself
    draws, and the reason dcim-platform's collector can still poll a device
    it already has device_instance for with FDR disabled entirely."""
    _, port = gated
    assert len(_directed_whois(port)) == 1


def test_registration_expires_and_gating_resumes(gated):
    _, port = gated
    _register_foreign_device(port, ttl=1)
    assert _broadcast_whois(port) != []

    time.sleep(1.2)
    assert _broadcast_whois(port) == []


def test_an_ungated_device_answers_broadcast_whois_regardless():
    """The ordinary case, unaffected: a topology with no FDR-gated devices
    behaves exactly as it always did."""
    ctrl = BACnetController()
    ctrl.set_log_callback(lambda m, l="info": None)
    port = _free_udp_port()
    assert ctrl.start(device_ips=[HOST], base_instance=GATED_INSTANCE,
                      circuits_map={HOST: (4, 4)}, port=port)
    ctrl.tick(1.0)
    try:
        assert len(_broadcast_whois(port)) == 1
    finally:
        ctrl.stop()


def test_a_malformed_registration_is_nacked_not_silently_dropped(gated):
    _, port = gated
    frame = bytes([0x81, 0x05, 0x00, 0x04])  # header claims no TTL payload
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(frame, (WILDCARD_ONLY, port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()
    func, payload = parse_bvll(data)
    assert func == 0x00
    assert payload != b"\x00\x00"

"""Switching one outlet drops only what is plugged into it.

The strip-level "Outlet Off" fault models a PDU that has lost its own feed: every
receptacle goes dark together. A switched SKU also gives you ONE relay per outlet,
and that is a different event — the distinction matters because a dual-corded server
is supposed to ride out losing one side. If a single opened outlet killed the server,
an A/B failover test would read as an outage and the redundancy the estate is built
on would be invisible.
"""
import pytest

from core.device_manager import Device, DeviceType, Vendor
from core.topology_engine import TopologyEngine


@pytest.fixture
def rack():
    """One dual-corded server across an A and a B strip, plus a single-corded switch."""
    topo = TopologyEngine()
    pdu_a = Device(name="PDUA-DC1-HA-R1-01", device_type=DeviceType.PDU,
                   vendor=Vendor.APC, model_name="APC AP8941", ip_address="10.1.1.1")
    pdu_b = Device(name="PDUB-DC1-HA-R1-01", device_type=DeviceType.PDU,
                   vendor=Vendor.APC, model_name="APC AP8941", ip_address="10.1.1.2")
    srv = Device(name="SRV01-DC1-HA-R1-01", device_type=DeviceType.SERVER,
                 vendor=Vendor.DELL, model_name="Dell PowerEdge R750",
                 ip_address="10.2.2.1")
    for d in (pdu_a, pdu_b, srv):
        topo.add_device(d)
    topo.add_link(pdu_a.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_a.id, "C13"), psu=1)
    topo.add_link(pdu_b.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_b.id, "C13"), psu=2)
    return topo, pdu_a, pdu_b, srv


def _feeds(topo, srv):
    return topo.power_feeds(srv.id)


def test_server_is_fed_from_both_strips(rack):
    topo, pdu_a, pdu_b, srv = rack
    feeds = _feeds(topo, srv)
    assert len(feeds) == 2
    assert {f["supply_id"] for f in feeds.values()} == {pdu_a.id, pdu_b.id}


def test_one_outlet_off_leaves_the_server_fed(rack):
    """The A relay opens. The server is still live on B — this is the whole point."""
    topo, pdu_a, _pdu_b, srv = rack
    feeds = _feeds(topo, srv)
    a_outlet = next(f["outlet"] for f in feeds.values() if f["supply_id"] == pdu_a.id)
    off = {pdu_a.id: {a_outlet}}
    live = sum(1 for f in feeds.values()
               if f["supply_id"] not in off or f["outlet"] not in off[f["supply_id"]])
    assert live == 1, "losing one cord must not de-energise a dual-corded load"


def test_both_outlets_off_de_energises_the_server(rack):
    topo, pdu_a, pdu_b, srv = rack
    feeds = _feeds(topo, srv)
    off = {f["supply_id"]: {f["outlet"]} for f in feeds.values()}
    live = sum(1 for f in feeds.values()
               if f["supply_id"] not in off or f["outlet"] not in off[f["supply_id"]])
    assert live == 0


def test_switching_an_unrelated_outlet_changes_nothing(rack):
    """Opening a receptacle nothing is plugged into must not affect any load."""
    topo, pdu_a, _pdu_b, srv = rack
    feeds = _feeds(topo, srv)
    used = {f["outlet"] for f in feeds.values() if f["supply_id"] == pdu_a.id}
    spare = next(o.index for o in pdu_a.outlets if o.index not in used)
    off = {pdu_a.id: {spare}}
    live = sum(1 for f in feeds.values()
               if f["supply_id"] not in off or f["outlet"] not in off[f["supply_id"]])
    assert live == 2


def test_surviving_strip_inherits_the_whole_load(rack):
    """Kill every outlet on A and B must carry 100%, not politely keep taking half.

    This is the number a 2N design is sized on: both strips sit near 50% precisely
    so either can take the whole rack. A model that keeps splitting 50/50 after one
    side dies reports a rack as comfortable at the exact moment it is not, and hides
    the only failure the redundancy exists to survive.
    """
    topo, pdu_a, pdu_b, srv = rack
    feeds = _feeds(topo, srv)

    # Both cords live: the draw splits across two active parents.
    active = [f["supply_id"] for f in feeds.values()]
    assert len(active) == 2
    assert 1.0 / len(active) == 0.5

    # A's relay opens -> that cord is dead, so only B remains active.
    dead_cords = {frozenset((srv.id, pdu_a.id))}
    still_active = [f["supply_id"] for f in feeds.values()
                    if frozenset((srv.id, f["supply_id"])) not in dead_cords]
    assert still_active == [pdu_b.id]
    assert 1.0 / len(still_active) == 1.0, "survivor must inherit the full load"


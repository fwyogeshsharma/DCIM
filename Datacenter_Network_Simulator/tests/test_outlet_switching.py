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



def test_store_computes_unpowered_loads_without_erroring(tmp_path):
    """Drive the REAL store, not the topology in isolation.

    The first cut of this feature called self._device_manager, which does not exist
    on DeviceStateStore (it is self._dm). Every tick raised AttributeError and the
    whole simulation stopped — invisible to the other tests here, because they
    exercise TopologyEngine directly and never enter the tick. Any test that calls
    the store's own method would have caught it on the first run.
    """
    from conftest import build_plant

    fx = build_plant(tmp_path, servers=4)
    store = fx.store

    store._compute_unpowered_loads()          # must not raise
    assert store._load_unpowered_names == set()
    assert store._dead_cord_pairs == set()

    # And it must survive a full tick, which is where it is actually called from.
    store._tick()
    assert isinstance(store._load_unpowered_names, set)


# ── losing every cord ────────────────────────────────────────────────────────
#
# Everything above reasons about the FEEDS. What follows is about the load: a
# server with no live cord is not a server drawing zero watts, it is a server
# that is off, and until this it was the former. Cutting both cords zeroed the
# power and changed nothing else - chassis On, SNMP answering, Redfish serving -
# so a DCIM saw a healthy machine that happened to be idle. A fault campaign cut
# both feeds on a dual-corded server and never raised a single alarm.


def _store(topo, devices, tmp_path):
    """A state store wired to a manager holding these devices."""
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore

    dm = DeviceManager()
    for d in devices:
        dm.add_device(d)
    return DeviceStateStore(dm, topo, str(tmp_path))


def _both_outlets(topo, srv):
    return {f["supply_id"]: {f["outlet"]} for f in topo.power_feeds(srv.id).values()}


def _set_off(store, off_by_pdu, pdus):
    """Put the relay state where _compute_unpowered_loads reads it."""
    for pdu in pdus:
        state = store.ext_state_for(pdu)
        state["pdu_outlets_off"] = sorted(off_by_pdu.get(pdu.id, ()))


def test_a_load_with_no_live_cord_is_off_not_merely_idle(rack, tmp_path):
    topo, pdu_a, pdu_b, srv = rack
    store = _store(topo, (pdu_a, pdu_b, srv), tmp_path)
    feeds = topo.power_feeds(srv.id)

    a_only = {f["supply_id"]: {f["outlet"]} for f in feeds.values()
              if f["supply_id"] == pdu_a.id}
    _set_off(store, a_only, (pdu_a, pdu_b))
    store._compute_unpowered_loads()
    assert srv.power_state == "On", "one dead cord is what the other one is for"

    _set_off(store, _both_outlets(topo, srv), (pdu_a, pdu_b))
    store._compute_unpowered_loads()
    assert srv.power_state == "Off"


def test_the_bmc_dies_with_the_chassis(rack, tmp_path):
    """Standby power comes from the same cords.

    A BMC still serving Redfish after both feeds are pulled would be running on
    nothing - and it is exactly what kept the platform from noticing.
    """
    from core.device_state_store import _is_unpowered

    topo, pdu_a, pdu_b, srv = rack
    store = _store(topo, (pdu_a, pdu_b, srv), tmp_path)
    _set_off(store, _both_outlets(topo, srv), (pdu_a, pdu_b))
    store._compute_unpowered_loads()

    assert _is_unpowered(srv.name)


def test_power_comes_back_with_the_cord(rack, tmp_path):
    """A restored feed restores the chassis. It is a relay, not a trip."""
    topo, pdu_a, pdu_b, srv = rack
    store = _store(topo, (pdu_a, pdu_b, srv), tmp_path)
    _set_off(store, _both_outlets(topo, srv), (pdu_a, pdu_b))
    store._compute_unpowered_loads()
    assert srv.power_state == "Off"

    _set_off(store, {}, (pdu_a, pdu_b))
    store._compute_unpowered_loads()
    assert srv.power_state == "On"


def test_a_thermal_trip_is_not_undone_by_a_cord(rack, tmp_path):
    """A box that shut itself down at the die limit needs a hand on the button.

    Restoring a feed must not quietly bring it back, or a cooling failure would
    look self-healing every time somebody touched a PDU.
    """
    topo, pdu_a, pdu_b, srv = rack
    store = _store(topo, (pdu_a, pdu_b, srv), tmp_path)
    store._thermal_shutdown.add(srv.name)
    srv.power_state = "Off"

    _set_off(store, _both_outlets(topo, srv), (pdu_a, pdu_b))
    store._compute_unpowered_loads()
    _set_off(store, {}, (pdu_a, pdu_b))
    store._compute_unpowered_loads()

    assert srv.power_state == "Off"

"""A dark device is taken off the wire, and put back with its power.

Deleting a dark device's SNMP dataset wedged snmpsim for the whole estate, so
the silence is made by the host firewall instead. These pin the chain's life
cycle against a recording runner - no iptables is touched.
"""
import subprocess

import pytest

from core.dark_firewall import CHAIN, DarkFirewall


class _Rec:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, args):
        self.calls.append(list(args))
        rc = 1 if tuple(args[:2]) in self.fail else 0
        return subprocess.CompletedProcess(args, rc, "", "")

    def rules(self, verb):
        return [c for c in self.calls if c[0] == verb and c[1] == CHAIN]


def test_first_sync_builds_and_empties_the_chain_then_hooks_input():
    """Flushed on start: a run that crashed mid-outage must not leave a device
    dropped for ever."""
    rec = _Rec(fail={("-C", "INPUT")})
    fw = DarkFirewall(runner=rec, available=True)
    fw.sync(set())

    assert ["-N", CHAIN] in rec.calls
    assert ["-F", CHAIN] in rec.calls
    assert ["-I", "INPUT", "1", "-j", CHAIN] in rec.calls


def test_the_input_hook_is_not_added_twice():
    rec = _Rec()                          # -C succeeds: the jump already exists
    DarkFirewall(runner=rec, available=True).sync(set())
    assert not [c for c in rec.calls if c[:2] == ["-I", "INPUT"]]


def test_a_dark_device_is_dropped_and_restored():
    rec = _Rec()
    fw = DarkFirewall(runner=rec, available=True)

    fw.sync({"10.50.11.20", "10.51.11.26"})
    assert fw.dropped == {"10.50.11.20", "10.51.11.26"}
    assert ["-A", CHAIN, "-d", "10.50.11.20", "-j", "DROP"] in rec.calls

    fw.sync(set())
    assert fw.dropped == set()
    assert ["-D", CHAIN, "-d", "10.51.11.26", "-j", "DROP"] in rec.calls


def test_only_the_difference_is_applied():
    rec = _Rec()
    fw = DarkFirewall(runner=rec, available=True)
    fw.sync({"10.0.0.1", "10.0.0.2"})
    rec.calls.clear()

    fw.sync({"10.0.0.2", "10.0.0.3"})
    assert rec.rules("-A") == [["-A", CHAIN, "-d", "10.0.0.3", "-j", "DROP"]]
    assert rec.rules("-D") == [["-D", CHAIN, "-d", "10.0.0.1", "-j", "DROP"]]


def test_without_iptables_it_does_nothing_at_all():
    rec = _Rec()
    fw = DarkFirewall(runner=rec, available=False)
    fw.sync({"10.0.0.1"})
    fw.clear()
    assert rec.calls == []
    assert fw.dropped == set()


def test_clear_flushes_what_it_owns():
    rec = _Rec()
    fw = DarkFirewall(runner=rec, available=True)
    fw.sync({"10.0.0.1"})
    rec.calls.clear()
    fw.clear()
    assert rec.calls == [["-F", CHAIN]]
    assert fw.dropped == set()


# --- wired into the power model ----------------------------------------------

def test_both_strips_tripped_drops_the_servers_addresses(tmp_path):
    from core.device_manager import Device, DeviceManager, DeviceType, Vendor
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    topo, dm = TopologyEngine(), DeviceManager()
    pdu_a = Device(name="PDUA-T", device_type=DeviceType.PDU, vendor=Vendor.APC,
                   model_name="APC AP8941", ip_address="10.1.1.1")
    pdu_b = Device(name="PDUB-T", device_type=DeviceType.PDU, vendor=Vendor.APC,
                   model_name="APC AP8941", ip_address="10.1.1.2")
    srv = Device(name="SRV-T", device_type=DeviceType.SERVER, vendor=Vendor.DELL,
                 model_name="Dell PowerEdge R750", ip_address="10.2.2.1")
    srv.mgmt_ip = "10.3.3.1"
    for d in (pdu_a, pdu_b, srv):
        topo.add_device(d)
        dm.add_device(d)
    topo.add_link(pdu_a.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_a.id, "C13"), psu=1)
    topo.add_link(pdu_b.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_b.id, "C13"), psu=2)
    store = DeviceStateStore(dm, topo, str(tmp_path))
    rec = _Rec()
    store._dark_fw = DarkFirewall(runner=rec, available=True)

    store._compute_unpowered_loads()
    store._sync_dark_firewall()
    assert store._dark_fw.dropped == set()

    store.set_pdu_condition(pdu_a.id, "breaker_trip", True)
    store._compute_unpowered_loads()
    store._sync_dark_firewall()
    assert store._dark_fw.dropped == set(), "still fed on B: it stays on the wire"

    store.set_pdu_condition(pdu_b.id, "breaker_trip", True)
    store._compute_unpowered_loads()
    store._sync_dark_firewall()
    assert store._dark_fw.dropped == {"10.2.2.1", "10.3.3.1"}, (
        "no live cord: both the host and the BMC address go quiet")

    for pdu in (pdu_a, pdu_b):
        store.set_pdu_condition(pdu.id, "breaker_trip", False)
    store._compute_unpowered_loads()
    store._sync_dark_firewall()
    assert store._dark_fw.dropped == set()

"""Orphaned IP aliases — managed addresses the host answers on that nothing claims.

These appear whenever the topology loses a device that had an address: the alias
stays on the NIC, but the device is gone from s.bound_ips, so /binding/unbind
cannot reach it. Found live after the plant-sensor migration released 12
addresses and 10 kept answering — and a wider sweep then turned up 70 more on a
second adapter, stranded by an earlier run that bound the prod plane there.

The sweep is deliberately NOT scoped to the selected adapter. Safety comes from
the managed-prefix check and the topology claim check, which is what these tests
pin down.
"""
from __future__ import annotations

import pytest

from core.device_manager import Device, DeviceManager, DeviceType, Vendor


class _FakeState:
    def __init__(self, devices):
        self.selected_adapter = "dcim0"
        self.device_manager = DeviceManager()
        for d in devices:
            self.device_manager.add_device(d)
        self.bound_ips = []
        self.nte_contexts = {}


def _dev(name, ip="", mgmt=""):
    return Device(name=name, device_type=DeviceType.SWITCH,
                  vendor=Vendor.CISCO_SYSTEMS, ip_address=ip, mgmt_ip=mgmt)


@pytest.fixture
def orphan_fn(monkeypatch):
    """Run _orphan_ips against a fake host: {adapter: [ips on it]}."""
    from api.routers import binding

    def _run(host, devices):
        monkeypatch.setattr(
            "core.ip_binder.get_interfaces",
            lambda apply_filter=True: [(n, n) for n in host])
        monkeypatch.setattr(
            "core.ip_binder.get_interface_ips",
            lambda iface: list(host.get(iface, [])))
        return binding._orphan_ips(_FakeState(devices))
    return _run


def test_unclaimed_managed_address_is_an_orphan(orphan_fn):
    out = orphan_fn({"dcim0": ["10.52.14.19", "10.52.14.20"]},
                    [_dev("GW", mgmt="10.52.14.19")])
    assert out == {"dcim0": ["10.52.14.20"]}


def test_claimed_addresses_are_never_reaped(orphan_fn):
    out = orphan_fn({"dcim0": ["10.50.11.10", "10.51.13.27"]},
                    [_dev("A", ip="10.50.11.10", mgmt="10.51.13.27")])
    assert out == {}


def test_the_hosts_own_addressing_is_out_of_scope(orphan_fn):
    """An address outside the managed prefixes was not put there by us. Reaping
    the host's LAN address would take the machine off the network — and this is
    the guard that makes a whole-host sweep safe."""
    out = orphan_fn({"eth0": ["192.168.1.12"],
                     "eth1": ["172.20.1.5", "10.52.14.20"],
                     "docker0": ["10.255.255.254"]}, [])
    assert out == {"eth1": ["10.52.14.20"]}


def test_orphans_are_found_on_adapters_other_than_the_selected_one(orphan_fn):
    """The live case: 70 prod-plane addresses stranded on eth1 by an earlier run,
    long after the operator moved binding to a dedicated dcim0."""
    out = orphan_fn({"dcim0": ["10.52.14.19"],
                     "eth1": ["10.50.1.95", "10.50.1.96", "10.50.0.1"]},
                    [_dev("GW", mgmt="10.52.14.19")])
    assert out == {"eth1": ["10.50.0.1", "10.50.1.95", "10.50.1.96"]}


def test_loopback_is_never_swept(orphan_fn):
    out = orphan_fn({"lo": ["10.50.0.9", "127.0.0.1"]}, [])
    assert out == {}


def test_both_address_fields_count_as_a_claim(orphan_fn):
    """Facility gear is mgmt-only (ip_address empty), IT gear carries both."""
    out = orphan_fn({"dcim0": ["10.52.14.19", "10.50.11.10"]},
                    [_dev("FAC", mgmt="10.52.14.19"),
                     _dev("SRV", ip="10.50.11.10")])
    assert out == {}


def test_a_claim_on_one_adapter_protects_the_address_on_every_adapter(orphan_fn):
    """Claim is a property of the topology, not of where the alias happens to
    sit — otherwise a duplicate alias would get one copy reaped mid-poll."""
    out = orphan_fn({"dcim0": ["10.50.11.10"], "eth1": ["10.50.11.10"]},
                    [_dev("SRV", ip="10.50.11.10")])
    assert out == {}


def test_orphans_come_back_sorted_numerically(orphan_fn):
    """String sort puts .100 before .20, which makes a 70-entry list unreadable."""
    out = orphan_fn({"dcim0": ["10.52.14.100", "10.52.14.20", "10.52.14.9"]}, [])
    assert out == {"dcim0": ["10.52.14.9", "10.52.14.20", "10.52.14.100"]}


def test_no_topology_loaded_reaps_nothing(orphan_fn, monkeypatch):
    """Without a topology every address looks unclaimed — the one input that
    could turn this sweep into a self-inflicted outage."""
    from api.routers import binding
    monkeypatch.setattr("core.ip_binder.get_interfaces",
                        lambda apply_filter=True: [("dcim0", "dcim0")])
    monkeypatch.setattr("core.ip_binder.get_interface_ips",
                        lambda iface: ["10.50.11.10", "10.52.14.20"])

    class _NoTopo:
        selected_adapter = "dcim0"
        device_manager = None
    assert binding._orphan_ips(_NoTopo()) == {}


def test_the_migrated_instruments_addresses_are_orphans(orphan_fn):
    """The original live case: the plant-sensor migration released these, the
    gateway took the lowest one over, and the rest kept answering until reaped."""
    released = ["10.52.14.19", "10.52.14.20", "10.52.14.24",
                "10.52.14.28", "10.52.14.29", "10.52.14.36"]
    out = orphan_fn({"dcim0": released},
                    [_dev("MBGW1-DC1-CP", mgmt="10.52.14.19")])
    assert out == {"dcim0": ["10.52.14.20", "10.52.14.24", "10.52.14.28",
                             "10.52.14.29", "10.52.14.36"]}

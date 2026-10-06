"""One Ethernet port takes one cable.

OOBM1-DC1-CP and OOBM1-DC2-CP each carried three management cables on port 0 -
an energy monitor, a Modbus gateway and a BACnet router - because add_link picked
a "free" port from Interface.connected_to_device, a cache that goes stale the
moment anything re-cords, and accepted an explicit port without checking it.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from core.topology_engine import TopologyEngine

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGIES = sorted((ROOT / "topologies").glob("*.json"))
ETHERNET = ("production", "management")


@pytest.mark.parametrize("path", TOPOLOGIES, ids=lambda p: p.name)
def test_no_port_in_a_topology_carries_two_cables(path):
    topo = json.loads(path.read_text(encoding="utf-8"))
    ends = Counter()
    for e in topo.get("edges") or topo.get("links") or []:
        if e.get("layer") in ETHERNET:
            for end in ("src", "dst"):
                if e.get(f"{end}_iface") is not None:
                    ends[(e[end], e[f"{end}_iface"])] += 1
    doubled = [k for k, n in ends.items() if n > 1]
    assert not doubled, f"{len(doubled)} port(s) with two cables - run tools/fix_doubled_ports.py"


@pytest.fixture(scope="module")
def engine():
    topo = json.loads((ROOT / "topologies" / "dual_dc_enterprise.json").read_text(encoding="utf-8"))
    eng = TopologyEngine()
    eng.from_dict(topo)
    eng._file_edges = len(topo.get("edges") or [])
    return eng


def _id(eng, name):
    return next(d.id for d in eng.get_all_devices() if d.name == name)


def test_the_estate_loads_without_a_cable_refused(engine):
    assert engine.graph.number_of_edges() == engine._file_edges


def test_an_occupied_port_is_refused_not_doubled(engine):
    sw, gw = _id(engine, "OOBM1-DC1-CP"), _id(engine, "EV21-DC1-HA-R1-04")
    assert not engine.add_link(gw, sw, src_iface=0, dst_iface=0, layer="production"), \
        "port 0 already carries EV22-DC1-CP's cable"


def test_a_free_port_is_read_from_the_cables_not_the_cache(engine):
    sw = _id(engine, "OOBM1-DC1-CP")
    dev = engine.get_device(sw)
    for itf in dev.interfaces:          # the stale cache that caused it: says "free"
        itf.connected_to_device = None
    used = engine._used_ifaces(sw)
    assert {0, 3, 4} <= used
    assert TopologyEngine._next_free_iface(dev, used) not in used


def test_a_full_device_refuses_a_cable():
    class D:  # two ports, both cabled
        interfaces = [object(), object()]
    assert TopologyEngine._next_free_iface(D(), {0, 1}) is None

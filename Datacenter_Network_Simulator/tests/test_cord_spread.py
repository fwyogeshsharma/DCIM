"""Cords are spread over a strip's legs and banks, mirrored A/B.

Filling outlets in order put a rack's load on a strip's first banks - 17 A on
a 16 A bank breaker, legs at 15/18/5 A - while the rest of the strip sat
empty. An installer places the heaviest loads first, each on the leg then the
bank carrying least, at the same outlet number on both strips of a pair.
"""
import importlib.util
from collections import Counter
from pathlib import Path

from core.device_manager import Device, DeviceManager, DeviceType, Vendor, outlet_layout
from core.topology_engine import TopologyEngine

_spec = importlib.util.spec_from_file_location(
    "assign_power_terminations",
    Path(__file__).resolve().parents[1] / "tools" / "assign_power_terminations.py")
apt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apt)


def test_every_bank_of_a_3_phase_strip_carries_both_connectors():
    lay = outlet_layout(30, 12, 3)                 # AP8886
    assert len(lay) == 42
    per = Counter((o["bank"], o["type"]) for o in lay)
    assert all(per[(b, "C13")] == 5 and per[(b, "C19")] == 2 for b in range(1, 7))
    assert [o["index"] for o in lay] == list(range(1, 43))
    one = outlet_layout(21, 3, 1)                  # AP8953: 11+2 and 10+1
    assert Counter((o["bank"], o["type"]) for o in one) == {
        (1, "C13"): 11, (1, "C19"): 2, (2, "C13"): 10, (2, "C19"): 1}


def _strip(dm, topo, name, model, ip):
    d = Device(name=name, device_type=DeviceType.PDU, vendor=Vendor.APC,
               model_name=model, ip_address=ip)
    dm.add_device(d)
    topo.add_device(d)
    return d


def test_the_next_cord_goes_on_the_emptiest_leg_then_bank():
    dm, topo = DeviceManager(), TopologyEngine()
    pdu = _strip(dm, topo, "PDUA-DC1-HA-R9-01", "APC AP8886", "10.0.9.1")
    banks = []
    for i in range(6):
        s = Device(name=f"SRV0{i}-DC1-HA-R9-01", device_type=DeviceType.SERVER,
                   vendor=Vendor.DELL, model_name="Dell PowerEdge R750",
                   ip_address=f"10.1.9.{i + 1}", power_draw_w=500)
        dm.add_device(s)
        topo.add_device(s)
        o = topo.next_free_outlet(pdu.id)
        topo.add_link(pdu.id, s.id, layer="power", outlet=o, psu=1)
        banks.append(next(x.bank for x in pdu.outlets if x.index == o))
    # Six cords, six banks: three legs twice over, never two on one bank.
    assert sorted(banks) == [1, 2, 3, 4, 5, 6]
    assert banks[:3] == [1, 2, 3], "legs 1, 2, 3 before any leg takes a second"


def test_the_tool_spreads_heaviest_first_and_mirrors_the_pair():
    outlets = outlet_layout(30, 12, 3)
    byid = {
        "a": {"name": "PDUA-X", "outlets": outlets},
        "b": {"name": "PDUB-X", "outlets": outlets},
    }
    cords = {"a": [], "b": []}
    for i in range(12):
        lid = f"s{i:02d}"
        byid[lid] = {"name": lid, "power_draw_w": 1000 - 50 * i,
                     "psus": [{"index": 1, "inlet": "C14"}, {"index": 2, "inlet": "C14"}]}
        cords["a"].append({"dst": lid})
        cords["b"].append({"dst": lid})
    out = apt.spread_outlets(cords, byid)
    assert all(out[("a", f"s{i:02d}")] == out[("b", f"s{i:02d}")] for i in range(12))
    by_o = {o["index"]: o for o in outlets}
    legs = Counter((by_o[out[("a", f"s{i:02d}")]]["bank"] - 1) % 3 for i in range(12))
    assert set(legs.values()) == {4}, "twelve cords, four a leg"
    banks = Counter(by_o[out[("a", f"s{i:02d}")]]["bank"] for i in range(12))
    assert set(banks.values()) == {2}, "and two a bank"
    assert all(by_o[out[("a", f"s{i:02d}")]]["type"] == "C13" for i in range(12))

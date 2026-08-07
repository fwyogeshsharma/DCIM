"""The rack-PDU outlet table must describe the STRIP, not the list of loads.

An NMS reads this table by walking outlets 1..N and matching each row against the
cord it has recorded on that receptacle. Two ways to get that wrong, both of which
this table did before:

  * number the rows by ranking the connected devices — outlet 3 in the table is then
    whatever sorted third, not the outlet labelled 3 on the unit;
  * publish only the outlets in use — the walk then ends at the last cord, and a
    consumer that stops at the first gap (openDCIM's CDUInfo::getPortStatus does
    exactly that) sees a truncated strip.
"""
import pytest

from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator, _PDU_OUTLET_ENT
from core.topology_engine import TopologyEngine


def _rows(pdu, topo):
    """{outlet index: {column: value}} from the generated OID entries."""
    out: dict = {}
    gen = SNMPRecGenerator.__new__(SNMPRecGenerator)
    for oid, _typ, val in gen._pdu_outlet_entries(pdu, topo):
        col, idx = oid[len(_PDU_OUTLET_ENT) + 1:].split(".")
        out.setdefault(int(idx), {})[col] = val
    return out


@pytest.fixture
def rack():
    """One AP8941 (21 C13 + 3 C19), three 1U servers on C13s, one big box on a C19.

    The big box is named so it sorts FIRST — under name-ranked numbering it landed on
    outlet 1, which is a C13 it physically cannot plug into.
    """
    topo = TopologyEngine()
    pdu = Device(name="PDUA-DC1-HA-R1-01", device_type=DeviceType.PDU,
                 vendor=Vendor.APC, model_name="APC AP8941", ip_address="10.1.1.1")
    topo.add_device(pdu)

    loads = []
    for i in range(3):
        s = Device(name=f"SRV{i + 1}-DC1-HA-R1-01", device_type=DeviceType.SERVER,
                   vendor=Vendor.DELL, model_name="Dell PowerEdge R750",
                   ip_address=f"10.2.2.{i + 1}")
        topo.add_device(s)
        loads.append((s, "C13"))
    big = Device(name="AAA-BIG-DC1-HA-R1-01", device_type=DeviceType.SERVER,
                 vendor=Vendor.DELL, model_name="Dell PowerEdge XE9680",
                 ip_address="10.2.2.99")
    topo.add_device(big)
    loads.append((big, "C19"))

    for dev, want in loads:
        outlet = topo.next_free_outlet(pdu.id, want)
        topo.add_link(pdu.id, dev.id, layer="power", outlet=outlet, psu=1)
    return topo, pdu, big, loads[0][0]


def test_every_receptacle_is_published(rack):
    topo, pdu, _big, _srv = rack
    rows = _rows(pdu, topo)
    assert sorted(rows) == [o.index for o in pdu.outlets]
    assert len(rows) == 24, "AP8941 is a 24-outlet strip"


def test_row_index_is_the_outlet_the_cord_lands_on(rack):
    topo, pdu, big, _srv = rack
    rows = _rows(pdu, topo)
    c19_outlet = topo.outlet_loads(pdu.id)
    landed = next(n for n, ref in c19_outlet.items() if ref["load_name"] == big.name)
    assert landed > 21, "a C19 load belongs on one of the three C19s, not a C13"
    assert rows[landed]["2"] == big.name
    # The name-ranked bug put it here.
    assert rows[1]["2"] != big.name


def test_unplugged_outlet_is_energised_and_reads_zero(rack):
    topo, pdu, _big, _srv = rack
    rows = _rows(pdu, topo)
    empty = rows[10]
    assert empty["3"] == "1", "a PDU does not de-power a receptacle because it is empty"
    assert empty["4"] == "0" and empty["5"] == "0"


def test_dual_corded_load_splits_its_draw_across_both_strips(rack):
    topo, pdu_a, _big, srv = rack
    single = int(_rows(pdu_a, topo)[1]["5"])

    pdu_b = Device(name="PDUB-DC1-HA-R1-01", device_type=DeviceType.PDU,
                   vendor=Vendor.APC, model_name="APC AP8941", ip_address="10.1.1.2")
    topo.add_device(pdu_b)
    topo.add_link(pdu_b.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_b.id, "C13"), psu=2)

    dual = int(_rows(pdu_a, topo)[1]["5"])
    assert dual == round(single / 2), (
        "charging the full chassis draw to the outlet on BOTH strips double-counts "
        "the rack"
    )

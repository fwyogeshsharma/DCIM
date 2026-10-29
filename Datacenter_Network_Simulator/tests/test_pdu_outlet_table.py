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
    """One PX3-5503U (21 C13 + 3 C19, metered by outlet), three 1U servers on C13s,
    one big box on a C19.

    The big box is named so it sorts FIRST — under name-ranked numbering it landed on
    outlet 1, which is a C13 it physically cannot plug into. A metered-by-outlet SKU,
    because the current and power columns exist only on one.
    """
    topo = TopologyEngine()
    pdu = Device(name="PDUA-DC1-HA-R1-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, model_name="Raritan PX3-5503U", ip_address="10.1.1.1")
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
    assert len(rows) == 24, "PX3-5503U is a 24-outlet strip"


def test_row_index_is_the_outlet_the_cord_lands_on(rack):
    topo, pdu, big, _srv = rack
    rows = _rows(pdu, topo)
    c19_outlet = topo.outlet_loads(pdu.id)
    landed = next(n for n, ref in c19_outlet.items() if ref["load_name"] == big.name)
    assert landed > 21, "a C19 load belongs on one of the three C19s, not a C13"
    # The outlet's NAME is its receptacle number, not its load — a real rPDU ships
    # "Outlet <n>" and only an operator changes it. What is plugged in is the
    # CONNECTION, which outlet_loads() holds, not the name.
    assert rows[landed]["2"] == f"Outlet {landed}"
    # The name-ranked bug landed this C19 device on outlet 1, a C13 it cannot use.
    assert c19_outlet[landed]["load_name"] == big.name
    assert 1 not in {n for n, r in c19_outlet.items() if r["load_name"] == big.name}


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
                   vendor=Vendor.RARITAN, model_name="Raritan PX3-5503U", ip_address="10.1.1.2")
    topo.add_device(pdu_b)
    topo.add_link(pdu_b.id, srv.id, layer="power",
                  outlet=topo.next_free_outlet(pdu_b.id, "C13"), psu=2)

    dual = int(_rows(pdu_a, topo)[1]["5"])
    assert dual == round(single / 2), (
        "charging the full chassis draw to the outlet on BOTH strips double-counts "
        "the rack"
    )


def test_a_strip_without_outlet_cts_publishes_no_current_column(rack):
    """A Metered AP8886 and a Switched AP8953 have no current transformer behind
    their receptacles. Their outlet table carries index, name and state and
    stops - a figure in the current column would be a measurement the hardware
    cannot make, and a consumer could not tell it from a real one."""
    topo, _pdu, _big, srv = rack
    for model, vendor in (("APC AP8886", Vendor.APC), ("APC AP8953", Vendor.APC)):
        strip = Device(name=f"PDUC-{model[-6:]}", device_type=DeviceType.PDU,
                       vendor=vendor, model_name=model, ip_address="10.1.1.9")
        topo.add_device(strip)
        topo.add_link(strip.id, srv.id, layer="power",
                      outlet=topo.next_free_outlet(strip.id, "C13"), psu=2)
        rows = _rows(strip, topo)
        assert len(rows) == len(strip.outlets), model
        assert all(set(r) == {"1", "2", "3"} for r in rows.values()), (
            f"{model}: an outlet row carries a current/power column it cannot measure")
        topo.remove_device(strip.id) if hasattr(topo, "remove_device") else None

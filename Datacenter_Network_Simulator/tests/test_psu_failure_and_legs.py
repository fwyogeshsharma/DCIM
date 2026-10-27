"""A failed power supply is a dead cord with the plug still in, and a strip's
legs are what is plugged into them.

Two things a DCIM reads off a rack and could not, because nothing here
modelled them:

  * a 1+1 server loses a supply. The BMC marks it Critical over Redfish, the
    survivor carries the whole chassis, and the strip on the dead side sees
    that cord go quiet. The server is NOT down - which is the whole point of
    the second supply - and a platform that cannot tell "running on one" from
    "running on two" finds out the difference when the second one goes.

  * a 3-phase strip carries its banks on three legs. Which leg a rack is
    heavy on is decided by where the cords were plugged, so the per-leg
    currents and the imbalance between them come from the outlets, not from
    a random walk. PowerNet publishes one row per leg in
    rPDU2PhaseStatusTable; a walk that returned one row for an AP8886 would
    read as a strip with two legs missing.
"""
import pytest

from core import redfish_data_generator as rf
from core import vendor_oids
from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.device_state_store import DeviceStateStore, _get_psu_failed
from core.snmprec_generator import SNMPRecGenerator
from core.topology_engine import TopologyEngine


def _dev(dm, topo, name, dtype, ip, model="", vendor=Vendor.DELL, watts=0):
    d = Device(name=name, device_type=DeviceType(dtype), vendor=vendor,
               model_name=model, ip_address=ip, power_draw_w=watts)
    d.datacenter, d.room = "DC1", "Hall A"
    dm.add_device(d)
    topo.add_device(d)
    return d


@pytest.fixture
def rack(tmp_path):
    """Two AP8886 strips (3-phase, six banks of seven outlets) and three
    dual-corded servers, one per leg: outlet 1 is bank 1 on leg 1, outlet 8
    bank 2 on leg 2, outlet 15 bank 3 on leg 3."""
    dm, topo = DeviceManager(), TopologyEngine()
    pdua = _dev(dm, topo, "PDUA-DC1-HA-R2-01", "pdu", "10.0.1.1", "APC AP8886", Vendor.APC)
    pdub = _dev(dm, topo, "PDUB-DC1-HA-R2-01", "pdu", "10.0.1.2", "APC AP8886", Vendor.APC)
    srv = []
    for i in range(3):
        s = _dev(dm, topo, f"SRV0{i + 1}-DC1-HA-R2-01", "server", f"10.1.0.{i + 1}",
                 "Dell PowerEdge R750", watts=600)
        # The same work on each, so the legs can be compared: a device starts
        # with its own random CPU load, and the draw follows it.
        s.cpu_usage = 50.0
        srv.append(s)
        outlet = 1 + 7 * i
        topo.add_link(pdua.id, s.id, layer="power", outlet=outlet, psu=1)
        topo.add_link(pdub.id, s.id, layer="power", outlet=outlet, psu=2)
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    store._compute_power_flow()
    return store, topo, pdua, pdub, srv


def _flow(store):
    store._compute_unpowered_loads()
    store._compute_power_flow()
    return store._through_live


def test_a_failed_supply_moves_the_whole_draw_to_the_survivor(rack):
    store, topo, pdua, pdub, srv = rack
    before = _flow(store)
    assert before[pdua.id] == pytest.approx(before[pdub.id]), "a 1+1 pair shares the draw"
    own = before[srv[0].id]
    assert own > 0

    store.set_psu_condition(srv[0].id, 1, True)
    after = _flow(store)
    assert frozenset((srv[0].id, pdua.id)) in store._dead_cord_pairs
    assert srv[0].name not in store._load_unpowered_names, "running on one supply, not dark"
    assert after[pdub.id] - after[pdua.id] == pytest.approx(own)
    assert after[srv[0].id] == pytest.approx(own), "the chassis draws what it drew"

    # The part is swapped: nothing latches past that.
    store.set_psu_condition(srv[0].id, 1, False)
    assert _get_psu_failed(srv[0].id) == set()
    again = _flow(store)
    assert frozenset((srv[0].id, pdua.id)) not in store._dead_cord_pairs
    assert again[pdua.id] == pytest.approx(again[pdub.id])


def test_the_bmc_reports_the_failed_supply_critical_and_the_survivor_carrying_it(rack):
    store, topo, pdua, pdub, srv = rack
    healthy = rf._power_supplies(srv[0], topo)
    assert [p["Status"]["Health"] for p in healthy] == ["OK", "OK"]
    assert healthy[0]["PowerInputWatts"] == pytest.approx(healthy[1]["PowerInputWatts"])
    whole = healthy[0]["PowerInputWatts"] + healthy[1]["PowerInputWatts"]

    store.set_psu_condition(srv[0].id, 1, True)
    failed = rf._power_supplies(srv[0], topo)
    assert failed[0]["Status"] == {"State": "UnavailableOffline", "Health": "Critical"}
    assert failed[0]["PowerInputWatts"] == 0 and failed[0]["LineInputVoltage"] == 0
    assert failed[1]["Status"]["Health"] == "OK"
    assert failed[1]["PowerInputWatts"] == pytest.approx(whole), (
        "the survivor carries the whole chassis")


def test_leg_currents_come_from_the_outlets_and_imbalance_from_the_legs(rack):
    store, topo, pdua, pdub, srv = rack
    thr = _flow(store)
    # With an empty state dict the leg model reads 230 V L-N and PF 0.95.
    amps = lambda w: w / (230.0 * 0.95)  # noqa: E731
    legs = store._pdu_leg_currents(pdua, {})
    assert len(legs) == 3, "an AP8886 has three legs"
    for k in range(3):
        # Half of that server's draw on this strip's leg k: one server per
        # leg, 1+1 corded, so each side carries half.
        assert legs[k] == pytest.approx(amps(thr[srv[k].id] / 2), abs=0.02)

    # Lose SRV01's A-side supply: leg 1 on strip A goes quiet, leg 1 on strip
    # B carries the whole chassis.
    store.set_psu_condition(srv[0].id, 1, True)
    thr = _flow(store)
    a = store._pdu_leg_currents(pdua, {})
    b = store._pdu_leg_currents(pdub, {})
    assert a[0] == 0 and a[1] > 0 and a[2] > 0
    assert b[0] == pytest.approx(amps(thr[srv[0].id]), abs=0.02)
    assert b[1] == pytest.approx(amps(thr[srv[1].id] / 2), abs=0.02)
    # NEMA: largest deviation from the mean, over the mean. Roughly
    # (2x, x, x) -> 50 %, give or take the fans.
    mean = sum(b) / 3
    imbalance = 100.0 * max(abs(x - mean) for x in b) / mean
    assert 40.0 < imbalance < 60.0


def test_the_phase_table_has_one_row_per_leg_and_the_bank_table_one_per_bank(rack):
    _store, topo, pdua, _pdub, _srv = rack
    gen = SNMPRecGenerator.__new__(SNMPRecGenerator)
    A = vendor_oids.APC
    rows = {oid: val for oid, _t, val in gen._pdu_entries(pdua, topo)}
    phase_rows = [o for o in rows if o.startswith(A["rpdu2PhaseCurrent"] + ".")]
    bank_rows = [o for o in rows if o.startswith(A["rpdu2BankCurrent"] + ".")]
    assert sorted(phase_rows) == [f"{A['rpdu2PhaseCurrent']}.{i}" for i in (1, 2, 3)]
    assert len(bank_rows) == 6

    single = Device(name="PDUA-DC1-NR-R1-01", device_type=DeviceType.PDU, vendor=Vendor.APC,
                    model_name="APC AP8953", ip_address="10.0.1.9")
    rows1 = {oid for oid, _t, _v in gen._pdu_entries(single, topo)}
    assert [o for o in rows1 if o.startswith(A["rpdu2PhaseCurrent"] + ".")] == [
        f"{A['rpdu2PhaseCurrent']}.1"]
    assert len([o for o in rows1 if o.startswith(A["rpdu2BankCurrent"] + ".")]) == 2

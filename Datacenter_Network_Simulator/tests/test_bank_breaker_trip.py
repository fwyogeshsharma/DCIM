"""A branch breaker trips: the outlets behind it go dead, and nothing else does.

A 32 A strip protects each outlet bank with its own 16 A breaker. When one
opens, the loads corded to that bank lose that feed; a dual-corded load rides it
on the other strip, which takes the whole draw - and can trip in turn if that
pushes one of its banks well past its rating. The old model could only open the
whole strip, which overstated the blast radius of every branch trip.
"""

from __future__ import annotations

import pytest

from core import redfish_data_generator as rf
from core import vendor_oids
from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.device_state_store import DeviceStateStore
from core.topology_engine import TopologyEngine
from core.trap_definitions import TrapType
from core.trap_engine import TrapEngine


def _dev(dm, topo, name, dtype, ip, model="", vendor=Vendor.DELL, watts=0):
    d = Device(name=name, device_type=DeviceType(dtype), vendor=vendor,
               model_name=model, ip_address=ip, power_draw_w=watts)
    d.datacenter, d.room = "DC1", "Hall A"
    dm.add_device(d)
    topo.add_device(d)
    return d


@pytest.fixture
def rack(tmp_path):
    """Two AP8886 strips (six banks of seven outlets) and three dual-corded
    servers: outlet 1 is bank 1, outlet 8 bank 2, outlet 15 bank 3."""
    dm, topo = DeviceManager(), TopologyEngine()
    pdua = _dev(dm, topo, "PDUA-DC1-HA-R2-01", "pdu", "10.0.1.1", "APC AP8886", Vendor.APC)
    pdub = _dev(dm, topo, "PDUB-DC1-HA-R2-01", "pdu", "10.0.1.2", "APC AP8886", Vendor.APC)
    srv = []
    for i in range(3):
        s = _dev(dm, topo, f"SRV0{i + 1}-DC1-HA-R2-01", "server", f"10.1.0.{i + 1}",
                 "Dell PowerEdge R750", watts=600)
        s.cpu_usage = 50.0
        srv.append(s)
        topo.add_link(pdua.id, s.id, layer="power", outlet=1 + 7 * i, psu=1)
        topo.add_link(pdub.id, s.id, layer="power", outlet=1 + 7 * i, psu=2)
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    store._compute_power_flow()
    return store, topo, pdua, pdub, srv


def _flow(store):
    store._compute_unpowered_loads()
    store._compute_power_flow()
    return store._through_live


def test_a_tripped_bank_kills_only_its_own_outlets(rack):
    store, _topo, pdua, pdub, srv = rack
    _flow(store)
    assert store.set_pdu_bank_trip(pdua.id, 1, True)
    _flow(store)
    dead = store._dead_cord_pairs
    assert frozenset((srv[0].id, pdua.id)) in dead, "bank 1 feeds SRV01's A cord"
    assert frozenset((srv[1].id, pdua.id)) not in dead, "bank 2 is still closed"
    assert frozenset((srv[2].id, pdua.id)) not in dead, "bank 3 is still closed"
    assert not store._load_unpowered_names, "every server rides on its B cord"
    a = store._pdu_bank_currents(pdua, {})
    b = store._pdu_bank_currents(pdub, {})
    assert a[0] == 0.0
    assert b[0] == pytest.approx(2 * a[1], rel=0.1), "B bank 1 carries SRV01's whole draw"


def test_a_strip_without_branch_breakers_has_nothing_to_trip(rack):
    store, _topo, pdua, _pdub, _srv = rack
    assert not store.set_pdu_bank_trip(pdua.id, 7, True), "an AP8886 has six banks"
    dm, topo = DeviceManager(), TopologyEngine()
    small = _dev(dm, topo, "PDUA-DC1-NR-R1-01", "pdu", "10.0.1.9", "APC AP8681", Vendor.APC)
    st = DeviceStateStore(dm, topo, store._data_dir if hasattr(store, "_data_dir") else ".",
                          tick_interval=1.0)
    assert not st.set_pdu_bank_trip(small.id, 1, True), "a 16 A strip has no bank breakers"


def test_a_reset_restores_the_feed_and_both_fire_a_trap(rack):
    store, _topo, pdua, _pdub, srv = rack
    fired = []
    store.set_bank_trap_callback(lambda dev, bank, tripped: fired.append((dev.name, bank, tripped)))
    store.set_pdu_bank_trip(pdua.id, 1, True)
    store.set_pdu_bank_trip(pdua.id, 1, True)            # already open: no second trap
    store.set_pdu_bank_trip(pdua.id, 1, False)
    _flow(store)
    assert fired == [(pdua.name, 1, True), (pdua.name, 1, False)]
    assert frozenset((srv[0].id, pdua.id)) not in store._dead_cord_pairs


def _run(store, dev, amps, seconds, monkeypatch, step=1.0):
    """Drive the thermal element at a steady current for *seconds*."""
    clock = {"t": 1000.0}
    monkeypatch.setattr("core.device_state_store.time.monotonic", lambda: clock["t"])
    store._pdu_bank_heat_ts.pop(dev.id, None)
    store._step_bank_breakers(dev, [amps] + [0.0] * 5)
    t = 0.0
    while t < seconds:
        clock["t"] += step
        t += step
        store._step_bank_breakers(dev, [amps] + [0.0] * 5)
        if 1 in store.get_pdu_banks_tripped(dev.id):
            return t
    return None


def test_a_bank_at_its_rating_never_trips(rack, monkeypatch):
    store, _topo, pdua, _pdub, _srv = rack
    assert _run(store, pdua, 16.0, 4 * 3600, monkeypatch, step=10.0) is None
    assert _run(store, pdua, 1.12 * 16, 4 * 3600, monkeypatch, step=10.0) is None


def test_the_trip_time_follows_the_overload(rack, monkeypatch):
    """IEC 60898-1 C curve, In <= 32 A: 2.55 In within 1-60 s, 1.45 In
    within the hour. Heavier is always faster."""
    store, _topo, pdua, _pdub, _srv = rack
    t_255 = _run(store, pdua, 2.55 * 16, 600, monkeypatch)
    store.set_pdu_bank_trip(pdua.id, 1, False)
    t_145 = _run(store, pdua, 1.45 * 16, 3600, monkeypatch)
    assert t_255 is not None and 1 <= t_255 <= 61
    assert t_145 is not None and t_255 < t_145 <= 3600


def test_the_raritan_trap_names_the_breaker_that_opened():
    px = Device(name="PDUA-DC1-NR-R1-01", device_type=DeviceType.PDU,
                vendor=Vendor.RARITAN, model_name="Raritan PX3-5503U", ip_address="10.0.1.9")
    R, ST, SS = (vendor_oids.RARITAN, vendor_oids.RARITAN_SENSOR_TYPE,
                 vendor_oids.RARITAN_SENSOR_STATE)
    trip = {str(o): v for o, v in TrapEngine._vendor_varbinds(px, TrapType.PDU_BREAKER_TRIPPED, bank=2)}
    assert str(trip[R["ocpLabel"]]) == "C2"
    assert int(trip[R["typeOfSensor"]]) == ST["trip"]
    assert int(trip[R["ocpState"]]) == SS["open"]
    assert int(trip[R["oldSensorState"]]) == SS["closed"]
    reset = {str(o): v for o, v in TrapEngine._vendor_varbinds(px, TrapType.PDU_BREAKER_RESET, bank=2)}
    assert int(reset[R["ocpState"]]) == SS["closed"]
    assert vendor_oids.trap_oid(TrapType.PDU_BREAKER_RESET, px.vendor, px.device_type) \
        == "1.3.6.1.4.1.13742.6.0.65"


def test_the_apc_trap_names_the_bank():
    ap = Device(name="PDUA-DC1-HA-R2-01", device_type=DeviceType.PDU,
                vendor=Vendor.APC, model_name="APC AP8886", ip_address="10.0.1.1")
    vbs = {str(o): v for o, v in TrapEngine._vendor_varbinds(ap, TrapType.PDU_BREAKER_TRIPPED, bank=3)}
    assert int(vbs[vendor_oids.APC["bankNumber"]]) == 3
    reset = {str(o): v for o, v in TrapEngine._vendor_varbinds(ap, TrapType.PDU_BREAKER_RESET, bank=3)}
    assert int(reset[vendor_oids.SYNTH_PDU_BANK]) == 3, "the reset rides the plane's own tree"
    assert vendor_oids.APC["bankNumber"] not in reset


def test_the_bmc_reports_lost_input_not_a_failed_supply(rack):
    store, topo, pdua, _pdub, srv = rack
    _flow(store)
    store.set_pdu_bank_trip(pdua.id, 1, True)
    _flow(store)
    psus = rf._power_supplies(srv[0], topo)
    assert psus[0]["Status"] == {"State": "Enabled", "Health": "Critical"}
    assert psus[0]["LineInputVoltage"] == 0 and psus[0]["PowerInputWatts"] == 0
    assert psus[1]["Status"]["Health"] == "OK"
    other = rf._power_supplies(srv[1], topo)
    assert [p["Status"]["Health"] for p in other] == ["OK", "OK"], "bank 2 kept its feed"


def test_the_strip_reads_open_before_its_trap_goes_out(tmp_path):
    """Firmware updates its tables, then notifies. A poll prompted by the trap
    read the breaker still closed - the files are rewritten on a cycle - and
    the rule closed the alarm the trap had just raised (live, 2026-10-28)."""
    from core.snmprec_generator import SNMPRecGenerator

    dm, topo = DeviceManager(), TopologyEngine()
    px = _dev(dm, topo, "PDUA-DC1-NR-R1-01", "pdu", "10.0.1.9", "Raritan PX3-5503U", Vendor.RARITAN)
    gen = SNMPRecGenerator(str(tmp_path))
    path = gen.generate_device(px, topo)
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    store._ext_states.setdefault(px.name, {})
    R, ST, SS = (vendor_oids.RARITAN, vendor_oids.RARITAN_SENSOR_TYPE,
                 vendor_oids.RARITAN_SENSOR_STATE)
    trip_oid = f"{R['ocpState']}.1.1.{ST['trip']}"

    def wire_state():
        for line in open(path, encoding="utf-8"):
            oid, _t, val = line.rstrip("\n").split("|", 2)
            if oid == trip_oid:
                return int(val)
        return None

    seen = []
    store.set_bank_trap_callback(lambda dev, bank, tripped: seen.append(wire_state()))
    assert store.set_pdu_bank_trip(px.id, 1, True)
    store.set_pdu_bank_trip(px.id, 1, False)
    assert seen == [SS["open"], SS["closed"]], "the poll must read what the trap says"

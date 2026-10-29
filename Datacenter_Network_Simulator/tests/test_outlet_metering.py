"""Per-outlet current is a tier the vendor sells, and a figure that follows the cord.

A Metered AP8886 measures its inlet, legs and banks; a Switched AP8953 has a
relay per outlet and no current transformer; only a metered-by-outlet unit (a
Raritan PX3-5xxx, an APC AP86xx) carries a CT behind each receptacle. The
store models every strip's receptacle current from the loads corded to it; the
agent publishes it only where the hardware has something to read it with.
"""

from __future__ import annotations

import pytest

from core import device_manager as dm_mod
from core import vendor_oids
from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.device_state_store import DeviceStateStore
from core.snmprec_generator import SNMPRecGenerator
from core.topology_engine import TopologyEngine

R, ST, SS = vendor_oids.RARITAN, vendor_oids.RARITAN_SENSOR_TYPE, vendor_oids.RARITAN_SENSOR_STATE


def _dev(dm, topo, name, dtype, ip, model="", vendor=Vendor.DELL, watts=0):
    d = Device(name=name, device_type=DeviceType(dtype), vendor=vendor,
               model_name=model, ip_address=ip, power_draw_w=watts)
    d.datacenter, d.room = "DC1", "Hall A"
    dm.add_device(d)
    topo.add_device(d)
    return d


@pytest.fixture
def rack(tmp_path):
    """Two PX3-5503U strips (two 16 A banks) and two dual-corded 600 W servers
    on outlets 1 and 14 (bank 1 is outlets 1-13, bank 2 is 14-24)."""
    dm, topo = DeviceManager(), TopologyEngine()
    pdua = _dev(dm, topo, "PDUA-DC1-HA-R1-01", "pdu", "10.0.1.1", "Raritan PX3-5503U", Vendor.RARITAN)
    pdub = _dev(dm, topo, "PDUB-DC1-HA-R1-01", "pdu", "10.0.1.2", "Raritan PX3-5503U", Vendor.RARITAN)
    srv = []
    for i, outlet in enumerate((1, 14)):
        s = _dev(dm, topo, f"SW0{i + 1}-DC1-HA-R1-01", "server", f"10.1.0.{i + 1}",
                 "Dell PowerEdge R750", watts=600)
        s.cpu_usage = 50.0
        srv.append(s)
        topo.add_link(pdua.id, s.id, layer="power", outlet=outlet, psu=1)
        topo.add_link(pdub.id, s.id, layer="power", outlet=outlet, psu=2)
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    store._compute_unpowered_loads()
    store._compute_power_flow()
    return store, topo, pdua, pdub, srv


def _flow(store):
    store._compute_unpowered_loads()
    store._compute_power_flow()


# ----------------------------------------------------------------- the catalog

def test_metering_is_a_tier_not_a_vendor():
    assert dm_mod.pdu_outlet_metering(DeviceType.PDU, "APC AP8886") is False, "Metered: inlet/leg/bank only"
    assert dm_mod.pdu_outlet_metering(DeviceType.PDU, "APC AP8953") is False, "Switched: relays, no CT"
    assert dm_mod.pdu_outlet_metering(DeviceType.PDU, "APC AP8681") is True
    assert dm_mod.pdu_outlet_metering(DeviceType.PDU, "Raritan PX3-5503U") is True
    assert dm_mod.pdu_outlet_metering(DeviceType.PDU, "Some Unknown Strip") is None
    assert dm_mod.pdu_outlet_metering(DeviceType.SERVER, "Raritan PX3-5503U") is None


# ----------------------------------------------------------------- the store

def test_an_outlet_carries_its_load_share(rack):
    store, _topo, pdua, pdub, _srv = rack
    a = store._pdu_outlet_amps(pdua, {})
    b = store._pdu_outlet_amps(pdub, {})
    # 600 W nameplate scaled by the store's live draw, halved over 1+1 cords,
    # over 230 V x 0.95 - the same arithmetic as the bank and leg figures.
    assert set(a) == {"1", "14"}, "only corded receptacles are in the map"
    assert a["1"] > 0 and a["1"] == pytest.approx(b["1"], rel=0.05)
    # The outlets sum to the bank they sit in - the same physics one level up.
    bank = store._pdu_bank_currents(pdua, {})
    for k, amps in enumerate(bank, 1):
        mine = sum(a.get(str(o.index), 0.0) for o in pdua.outlets if int(o.bank) == k)
        assert amps == pytest.approx(mine, abs=0.02), f"bank {k}"


def test_a_tripped_bank_zeroes_its_outlets_and_the_other_side_doubles(rack):
    store, _topo, pdua, pdub, _srv = rack
    before = store._pdu_outlet_amps(pdub, {})["1"]
    assert store.set_pdu_bank_trip(pdua.id, 1, True)
    _flow(store)
    a = store._pdu_outlet_amps(pdua, {"pdu_banks_tripped": store.get_pdu_banks_tripped(pdua.id)})
    b = store._pdu_outlet_amps(pdub, {})
    assert a["1"] == 0.0, "outlet 1 is behind the open breaker"
    assert a["14"] > 0, "bank 2 is untouched"
    assert b["1"] == pytest.approx(2 * before, rel=0.1), "the B cord carries the whole server"


def test_a_switched_off_outlet_reads_zero_amps(rack):
    store, _topo, pdua, _pdub, _srv = rack
    a = store._pdu_outlet_amps(pdua, {"pdu_outlets_off": [14]})
    assert a["14"] == 0.0 and a["1"] > 0


# ----------------------------------------------------------------- the agent

def test_raritan_publishes_a_current_row_per_receptacle(rack):
    _store, _topo, pdua, _pdub, _srv = rack
    rows = SNMPRecGenerator._raritan_outlet_rows(pdua)
    oids = {o for o, _t, _v in rows}
    for n in range(1, 25):
        assert f"{R['outletValue']}.1.{n}.{ST['current']}" in oids
        assert f"{R['outletDecimals']}.1.{n}.{ST['current']}" in oids
        assert f"{R['outletValue']}.1.{n}.{ST['activePower']}" in oids
        assert f"{R['outletState']}.1.{n}.{ST['onOff']}" in oids, "PX3-5xxx also switches"
    assert f"{R['outletValue']}.1.25.{ST['current']}" not in oids, "24 receptacles, 24 rows"
    assert len(rows) == 24 * 7


def test_live_rows_follow_the_cord_and_the_relay(rack):
    store, _topo, pdua, _pdub, _srv = rack
    amps = store._pdu_outlet_amps(pdua, {})
    ext = {"pdu_outlet_amps": amps, "pdu_power_factor": 0.95}
    up = SNMPRecGenerator._raritan_outlet_updates(pdua, ext, 230.0, 1, {14})
    assert up[f"{R['outletValue']}.1.1.{ST['current']}"] == ("66", str(int(round(amps["1"] * 1000))))
    assert up[f"{R['outletValue']}.1.1.{ST['activePower']}"] == (
        "66", str(int(round(amps["1"] * 230.0 * 0.95))))
    assert up[f"{R['outletState']}.1.1.{ST['onOff']}"] == ("2", str(SS["on"]))
    # Outlet 14's relay is open: off, and nothing flows.
    assert up[f"{R['outletState']}.1.14.{ST['onOff']}"] == ("2", str(SS["off"]))
    assert up[f"{R['outletValue']}.1.14.{ST['current']}"] == ("66", "0")
    # An idle receptacle is energised and reads zero - a row, not an absence.
    assert up[f"{R['outletValue']}.1.7.{ST['current']}"] == ("66", "0")
    assert up[f"{R['outletState']}.1.7.{ST['onOff']}"] == ("2", str(SS["on"]))
    # The whole strip's breaker open: every outlet off.
    down = SNMPRecGenerator._raritan_outlet_updates(pdua, ext, 230.0, 2, set())
    assert down[f"{R['outletState']}.1.1.{ST['onOff']}"] == ("2", str(SS["off"]))


def test_outlet_state_is_judged_against_its_own_rating(rack):
    _store, _topo, pdua, _pdub, _srv = rack
    # A C13 is a 10 A receptacle: 8.5 A is past the continuous-load line,
    # 10.5 A past the rating. Outlet 12 is a C19 (16 A): 10.5 A is merely busy.
    ext = {"pdu_outlet_amps": {"1": 8.5, "2": 10.5, "12": 10.5}}
    up = SNMPRecGenerator._raritan_outlet_updates(pdua, ext, 230.0, 1, set())
    assert up[f"{R['outletState']}.1.1.{ST['current']}"] == ("2", str(SS["aboveUpperWarning"]))
    assert up[f"{R['outletState']}.1.2.{ST['current']}"] == ("2", str(SS["aboveUpperCritical"]))
    assert up[f"{R['outletState']}.1.12.{ST['current']}"] == ("2", str(SS["normal"]))

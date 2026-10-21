"""S0-6 / S0-8 (doc 32 §4.3, §4.4, §14) — THE load-bearing test.

A sensor fault changes what a PROTOCOL serves and never what the physics reads
back. Each test below drives the real renderers — BACnet object present values,
the SNMP .snmprec patch path (file-backed: the value an snmpsim poll returns),
the Modbus register banks and the Redfish BMC dispatcher — off a real store tick
on the offline plant fixture, and checks two things on each:

* WIRE: the protocol serves truth + bias;
* TRUTH: the store's caches, the room and every rack inlet are unchanged against
  a control run with the same seed.

The one exception is ``affects_control``, where the process is MEANT to move.
"""
from __future__ import annotations

import base64
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import DC, ROOM, build_plant
from core import hall_geometry as geo

SEED = 20261021
BIAS = 3.0


# ── rig: the plant fixture, laid out as a real hall, with a real BACnet plane ─

def _lay_out(h, n_crah):
    width = geo.hall_width(13, geo.HALL_X0)
    depth = 12.3
    h.store._topology.floorplan = {"rooms": {f"{DC}/{ROOM}": {
        "class": "white_space", "containment": "cold_aisle",
        "width_m": width, "depth_m": depth, "grid_x0_m": geo.HALL_X0}}}
    servers = sorted((d for d in h.dm.get_all_devices() if d.device_type.value == "server"),
                     key=lambda d: d.name)
    for i, d in enumerate(servers):
        rack = i // 2
        row, num = (2, rack + 1) if rack < 12 else (3, rack - 11)
        sl = geo.slot(row, num)
        d.rack_row, d.rack_num, d.rack_unit = row, num, (10 if i % 2 == 0 else 30)
        d.floor_x, d.floor_y = geo.rack_x(num, geo.HALL_X0), sl.floor_y
        d.rack_facing, d.hot_aisle = sl.rack_facing, sl.hot_aisle
        d.room = ROOM
    for n, (x, y, rot) in zip((h.made[f"CRAH{i}"] for i in range(1, n_crah + 1)),
                              geo.crah_positions(width, depth, n_crah), strict=True):
        n.floor_x, n.floor_y, n.rotation_deg, n.room = x, y, rot, ROOM
    h.store._cool_ctx = None


def _attach_bacnet(h, tmp_path):
    """A real BACnetController with a real object tree per plant device, wired
    to the store exactly as the running app wires it. No sockets are bound."""
    from core.bacnet_plant_generator import (PLANT_SPEC, PlantTelemetryEngine,
                                             build_plant_object_tree)
    from core.sim_seed import stable_seed
    from simulator.bacnet_controller import BACnetController
    from simulator.bacnet_device import EV2BACnetDevice
    ctrl = BACnetController(datasets_dir=str(tmp_path / "bacnet"))
    inst = 60000
    by_name = {}
    for d in sorted(h.dm.get_all_devices(), key=lambda d: d.name):
        t = d.device_type.value
        if t not in PLANT_SPEC:
            continue
        rkw = float(d.power_draw_w or 0.0) / 1000.0
        tree, n2k = build_plant_object_tree(t, rkw)
        dev = EV2BACnetDevice(device_ip=d.ip_address or "10.0.8.1", device_instance=inst,
                              device_name=d.name, object_tree=tree, name_to_key=n2k,
                              kind=f"plant:{t}")
        ctrl._devices[inst] = dev
        ctrl._telemetry[inst] = PlantTelemetryEngine(t, rated_kw=rkw, seed=stable_seed(d.name))
        by_name[d.name] = dev
        inst += 1
    ctrl._running = True
    h.store.enable_bacnet(ctrl)
    h.store._plant_model_by_name = {d.name: d.model_name for d in h.dm.get_all_devices()}
    return ctrl, by_name


def _clear_caches():
    from core import device_state_store as dss
    dss._plant_state_cache.clear()
    dss._plant_wire_cache.clear()
    dss._ext_state_cache.clear()


class Rig(SimpleNamespace):
    def tick(self, n=1):
        for _ in range(n):
            self.store._tick()

    def crah(self, i=1):
        return self.h.made[f"CRAH{i}"]

    def servers(self):
        return sorted((d for d in self.h.dm.get_all_devices()
                       if d.device_type.value == "server"), key=lambda d: d.name)


def _rig(tmp_path, n_crah=4, mode="spatial", probes=True):
    _clear_caches()
    random.seed(SEED)
    h = build_plant(tmp_path, crahs=n_crah, servers=48, probes=probes)
    _lay_out(h, n_crah)
    h.store._air_mode = mode
    ctrl, bac = _attach_bacnet(h, tmp_path)
    return Rig(h=h, store=h.store, ctrl=ctrl, bac=bac)


def _truth_state(r):
    """Everything physics owns, for the control comparison."""
    from core import device_state_store as dss
    crahs = [r.crah(i).name for i in range(1, 5) if f"CRAH{i}" in r.h.made]
    return {
        "plant_cache": {n: dss._plant_state_cache.get(n, {}).get("Supply_Air_Temp")
                        for n in crahs},
        "auto": {n: r.store._plant_auto_points.get(n, {}).get("Supply_Air_Temp") for n in crahs},
        "inlets": {d.name: d.inlet_temp for d in r.servers()},
        "room_supply": r.store._room_supply_temp(r.servers()[0]),
    }


def _add(r, dev, point, **kw):
    from api.routers.faults import SensorFaultBody, add_sensor_fault
    return add_sensor_fault(SensorFaultBody(device=dev.id, point=point, **kw))


@pytest.fixture
def api_state(monkeypatch):
    """Point AppState at whichever rig the test builds."""
    from api.state import AppState
    holder = SimpleNamespace(s=None)
    monkeypatch.setattr(AppState, "get", classmethod(lambda cls: holder.s))

    def bind(r):
        holder.s = SimpleNamespace(state_store=r.store, device_manager=r.h.dm,
                                   topology=r.h.topo, notify_ui=lambda *a: None)
    return bind


SETTLE, AFTER = 40, 40


@pytest.fixture
def control(tmp_path):
    r = _rig(tmp_path / "control")
    r.tick(SETTLE + AFTER)
    return _truth_state(r)


# ── BACnet ───────────────────────────────────────────────────────────────────

def test_bacnet_wire_is_biased_and_physics_is_untouched(tmp_path, control, api_state):
    r = _rig(tmp_path / "fault")
    api_state(r)
    r.tick(SETTLE)
    crah = r.crah(1)
    out = _add(r, crah, "Supply_Air_Temp", bias=BIAS, campaign="crah-supply-bias/t")
    assert out["kind"] == "sensor" and out["device"] == crah.name
    assert out["protocols"] == ["bacnet", "snmp"]
    assert out["metric_hint"] == {"metric": "supply_air_temp", "instance": ""}
    r.tick(AFTER)

    from core import device_state_store as dss
    truth = dss._plant_state_cache[crah.name]["Supply_Air_Temp"]
    wire = r.bac[crah.name].get_snapshot()["Supply_Air_Temp"]
    assert wire == pytest.approx(truth + BIAS, abs=0.05)
    assert dss._plant_wire_cache[crah.name]["Supply_Air_Temp"] == pytest.approx(wire)
    # The store's own published point is truth too.
    assert r.store._plant_auto_points[crah.name]["Supply_Air_Temp"] == pytest.approx(truth)
    # Truth snapshot API view vs wire.
    t_snap = {s["name"]: s["values"] for s in r.ctrl.get_truth_snapshot()}
    assert t_snap[crah.name]["Supply_Air_Temp"] == pytest.approx(truth)

    # Against a control run with the same seed: physics unchanged everywhere.
    got = _truth_state(r)
    assert got["plant_cache"] == pytest.approx(control["plant_cache"], abs=1e-9)
    assert got["auto"] == pytest.approx(control["auto"], abs=1e-9)
    assert got["room_supply"] == pytest.approx(control["room_supply"], abs=1e-9)
    assert got["inlets"] == pytest.approx(control["inlets"], abs=1e-9)

    # Ledgered with the campaign tag and the protocols it reaches.
    from core.fault_ledger import get_ledger
    e = get_ledger().get(out["id"])
    assert e["campaign"] == "crah-supply-bias/t" and e["expected_kind"] == "sensor"


def test_bacnet_wire_lands_within_two_ticks(tmp_path, api_state):
    r = _rig(tmp_path)
    api_state(r)
    r.tick(SETTLE)
    _add(r, r.crah(2), "Supply_Air_Temp", bias=BIAS)
    r.tick(2)
    from core import device_state_store as dss
    truth = dss._plant_state_cache[r.crah(2).name]["Supply_Air_Temp"]
    assert r.bac[r.crah(2).name].get_snapshot()["Supply_Air_Temp"] == pytest.approx(
        truth + BIAS, abs=0.05)


def test_plant_metrics_view_param(tmp_path, api_state):
    from api.routers.bacnet import plant_metrics
    r = _rig(tmp_path)
    api_state(r)
    r.store._bacnet_ctrl.is_running = lambda: True
    from api.state import AppState
    AppState.get().bacnet = r.ctrl
    r.tick(5)
    _add(r, r.crah(1), "Supply_Air_Temp", bias=BIAS)
    r.tick(2)
    w = {x["name"]: x["values"] for x in plant_metrics(view="wire")}
    t = {x["name"]: x["values"] for x in plant_metrics(view="truth")}
    n = r.crah(1).name
    assert w[n]["Supply_Air_Temp"] == pytest.approx(t[n]["Supply_Air_Temp"] + BIAS, abs=0.05)
    n2 = r.crah(2).name
    assert w[n2] == t[n2]


def test_stuck_is_flat_on_the_wire_with_no_cov(tmp_path, api_state):
    from core.bacnet_object_model import BACnetObject  # noqa: F401  (object model in use)
    r = _rig(tmp_path)
    api_state(r)
    r.tick(SETTLE)
    crah = r.crah(1)
    dev = r.bac[crah.name]
    key = dev._name_to_key["Return_Air_Temp"]

    class _Sock:
        sent = 0

        def sendto(self, *a):
            _Sock.sent += 1
    dev._send_sock = _Sock()
    dev._cov_subs[key] = [SimpleNamespace(expiry=0, subscriber_addr=("10.9.9.9", 47808),
                                          process_id=1)]
    _add(r, crah, "Return_Air_Temp", stuck=True)
    r.tick(1)
    dev._cov_events.clear()
    seen = []
    truths = []
    from core import device_state_store as dss
    for _ in range(60):
        r.tick(1)
        seen.append(dev.get_snapshot()["Return_Air_Temp"])
        truths.append(dss._plant_state_cache[crah.name]["Return_Air_Temp"])
    assert len(set(seen)) == 1                         # zero variance on the wire
    assert not [e for e in dev._cov_events if e["obj_name"] == "Return_Air_Temp"]
    assert len(set(truths)) > 1                         # while the truth moves


def test_dropout_sets_reliability_and_fault_flag(tmp_path, api_state):
    r = _rig(tmp_path)
    api_state(r)
    r.tick(5)
    crah = r.crah(1)
    out = _add(r, crah, "Supply_Air_Temp", dropout_pct=100.0, dropout_window_s=30)
    r.tick(2)
    dev = r.bac[crah.name]
    obj = dev._objects[dev._name_to_key["Supply_Air_Temp"]]
    assert obj.reliability == 1 and obj.status_flags & 0b0100
    from api.routers.faults import delete_fault
    closed = delete_fault(out["id"])
    assert closed["end_reason"] == "deleted"
    r.tick(1)
    assert obj.reliability == 0 and not obj.status_flags & 0b0100


# ── SNMP (file-backed .snmprec patch path) ───────────────────────────────────

def _snmp_file(gen, dev, lines):
    p = Path(gen.output_dir) / f"{gen.snmp_address(dev)}.snmprec"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _oid_value(path, oid):
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith(oid + "|"):
            return int(ln.split("|")[2])
    raise AssertionError(f"{oid} not in {path}")


def test_crah_snmp_card_serves_the_wire(tmp_path, api_state):
    from core.snmprec_generator import SNMPRecGenerator
    r = _rig(tmp_path)
    api_state(r)
    r.tick(SETTLE)
    crah = r.crah(1)
    _add(r, crah, "Supply_Air_Temp", bias=BIAS)
    r.tick(2)
    gen = SNMPRecGenerator(str(tmp_path / "snmp"))
    oid = "1.3.6.1.4.1.99999.24.1.0"                   # Supply_Air_Temp ×10
    p = _snmp_file(gen, crah, [f"{oid}|2|0"])
    gen.patch_metrics(crah)
    from core import device_state_store as dss
    wire = dss._plant_wire_cache[crah.name]["Supply_Air_Temp"]
    truth = dss._plant_state_cache[crah.name]["Supply_Air_Temp"]
    assert _oid_value(p, oid) == int(round(wire * 10))
    assert _oid_value(p, oid) == pytest.approx((truth + BIAS) * 10, abs=1)


def test_server_inlet_bias_on_redfish_and_snmp_truth_unchanged(tmp_path, api_state):
    from core.snmprec_generator import SNMPRecGenerator
    from simulator.redfish_device import RedfishDevice
    r = _rig(tmp_path)
    api_state(r)
    r.tick(SETTLE)
    srv = r.servers()[0]
    _add(r, srv, "inlet_temp", bias=BIAS)
    r.tick(1)
    truth = srv.inlet_temp
    # Redfish Thermal, through the real BMC dispatcher.
    bmc = RedfishDevice(srv, "admin", "password")
    hdr = {"Authorization": "Basic " + base64.b64encode(b"admin:password").decode()}
    code, _h, body = bmc.dispatch("GET", f"/redfish/v1/Chassis/{bmc.member_id}/Thermal", hdr, None)
    assert code == 200
    inlet = next(t for t in body["Temperatures"] if t["Name"] == "Inlet Temp")
    assert inlet["ReadingCelsius"] == pytest.approx(round(truth + BIAS, 1), abs=0.051)
    # SNMP (host agent ENTITY-SENSOR inlet), file-backed patch.
    gen = SNMPRecGenerator(str(tmp_path / "snmp"))
    oid = "1.3.6.1.2.1.99.1.1.1.4.1"
    p = _snmp_file(gen, srv, [f"{oid}|2|0"])
    gen.patch_metrics(srv)
    assert _oid_value(p, oid) == int((truth + BIAS) * 10)
    # Truth: the Device attribute physics reads is unchanged.
    assert srv.inlet_temp == truth


def test_redfish_dropout_reads_null(tmp_path, api_state):
    from simulator.redfish_device import RedfishDevice
    r = _rig(tmp_path)
    api_state(r)
    r.tick(3)
    srv = r.servers()[1]
    _add(r, srv, "inlet_temp", dropout_pct=100.0)
    r.tick(1)
    bmc = RedfishDevice(srv, "admin", "password")
    hdr = {"Authorization": "Basic " + base64.b64encode(b"admin:password").decode()}
    _c, _h, body = bmc.dispatch("GET", f"/redfish/v1/Chassis/{bmc.member_id}/Thermal", hdr, None)
    inlet = next(t for t in body["Temperatures"] if t["Name"] == "Inlet Temp")
    assert inlet["ReadingCelsius"] is None
    assert inlet["Status"]["State"] == "UnavailableOffline"


# ── Modbus + SNMP on an ext quantity ─────────────────────────────────────────

def test_ext_bias_on_modbus_and_snmp_ext_cache_unchanged(tmp_path, api_state):
    """A CHW header thermowell (``water_temp``): Modbus RTU behind the gateway,
    and the gateway's ENTITY-SENSOR table on SNMP. (``battery_temp_c`` is
    Modbus-only in this simulator — see the Phase 0 report.)"""
    from core import device_state_store as dss
    from core.modbus_register_map import SPACE_DISCRETE, SPACE_INPUT, get_probe_map
    from core.snmprec_generator import SNMPRecGenerator
    from simulator.modbus_controller import ModbusController
    from simulator.modbus_device import ModbusSlave
    r = _rig(tmp_path)
    api_state(r)
    r.tick(SETTLE)
    probe = r.h.made["CHWS"]
    truth0 = dss._ext_state_cache[probe.name]["water_temp"]
    out = _add(r, probe, "water_temp", bias=BIAS)
    assert set(out["protocols"]) == {"snmp", "modbus"}
    r.tick(1)
    truth = dss._ext_state_cache[probe.name]["water_temp"]
    assert r.store._ext_states[probe.name]["water_temp"] == truth      # truth untouched
    assert abs(truth - truth0) < 1.0
    # Modbus, through the controller's real tick.
    mb = ModbusController(str(tmp_path / "modbus"))
    slave = ModbusSlave(probe.name, "sensor", get_probe_map("chw_supply"), unit_id=1)
    mb._by_name[probe.name] = slave
    mb._running = True
    mb.tick(1.0)
    raw = slave._regs[SPACE_INPUT][0]
    raw = raw - 0x10000 if raw >= 0x8000 else raw
    assert raw / 10.0 == pytest.approx(truth + BIAS, abs=0.051)
    assert slave._bits[SPACE_DISCRETE][0] == 1
    # SNMP: the gateway's trunk table, file-backed.
    gw = r.h.made["MBGW"]
    gen = SNMPRecGenerator(str(tmp_path / "snmp"))
    idx = gw.modbus_children.index(probe.name) + 1
    oid = f"1.3.6.1.2.1.99.1.1.1.4.{idx}"
    p = _snmp_file(gen, gw, [f"{oid}|2|0", f"1.3.6.1.2.1.99.1.1.1.5.{idx}|2|1"])
    gen.patch_metrics(gw)
    assert _oid_value(p, oid) == int(round((truth + BIAS) * 10))


def test_ext_dropout_clears_modbus_validity_and_snmp_oper_status(tmp_path, api_state):
    from core.modbus_register_map import SPACE_DISCRETE, get_probe_map
    from core.snmprec_generator import SNMPRecGenerator
    from simulator.modbus_controller import ModbusController
    from simulator.modbus_device import ModbusSlave
    r = _rig(tmp_path)
    api_state(r)
    r.tick(5)
    probe = r.h.made["CHWS"]
    _add(r, probe, "water_temp", dropout_pct=100.0)
    r.tick(1)
    mb = ModbusController(str(tmp_path / "modbus"))
    slave = ModbusSlave(probe.name, "sensor", get_probe_map("chw_supply"), unit_id=1)
    mb._by_name[probe.name] = slave
    mb._running = True
    mb.tick(1.0)
    assert slave._bits[SPACE_DISCRETE][0] == 0               # Reading_Valid cleared
    gw = r.h.made["MBGW"]
    gen = SNMPRecGenerator(str(tmp_path / "snmp"))
    idx = gw.modbus_children.index(probe.name) + 1
    oper = f"1.3.6.1.2.1.99.1.1.1.5.{idx}"
    p = _snmp_file(gen, gw, [f"1.3.6.1.2.1.99.1.1.1.4.{idx}|2|0", f"{oper}|2|1"])
    gen.patch_metrics(gw)
    assert _oid_value(p, oper) == 2                          # unavailable(2)


# ── rule-engine facts see the reading ────────────────────────────────────────

def test_rule_engine_facts_see_the_wire(tmp_path, api_state):
    r = _rig(tmp_path)
    api_state(r)
    facts = []
    r.store.set_rule_engine_callback(lambda f, d=None: facts.append(f))
    r.tick(3)
    srv = r.servers()[0]
    _add(r, srv, "cpu_temp", bias=10.0)
    facts.clear()
    r.tick(1)
    f = next(x for x in facts if getattr(x, "device_id", None) == srv.name)
    temp = getattr(f, "cpu_temp", None) or getattr(f, "temperature", None)
    assert temp == pytest.approx(srv.cpu_temp + 10.0, abs=0.05)


# ── affects_control: the process moves, the reading sits on setpoint ─────────

def test_affects_control_crah_supply(tmp_path, control, api_state):
    from core.device_state_store import _SUPPLY_SETPOINT_C
    r = _rig(tmp_path / "fault")
    api_state(r)
    r.tick(SETTLE)
    crah = r.crah(1)
    _add(r, crah, "Supply_Air_Temp", bias=BIAS, affects_control=True)
    r.tick(AFTER)
    from core import device_state_store as dss
    pen = r.store._chw_pen.get(DC, 0.0)
    wire = r.bac[crah.name].get_snapshot()["Supply_Air_Temp"]
    truth = dss._plant_state_cache[crah.name]["Supply_Air_Temp"]
    assert wire == pytest.approx(_SUPPLY_SETPOINT_C + pen, abs=0.2)    # reads on setpoint
    assert truth == pytest.approx(_SUPPLY_SETPOINT_C + pen - BIAS, abs=0.2)
    # The rack inlets fall where the unit's air reaches them: by
    # bias × that unit's live tile share × (1 − recirculation), within 0.3 K.
    inf = r.store.get_air_influence()
    room = inf["rooms"][0]
    got = _truth_state(r)["inlets"]
    by_rack = {}
    for s in r.servers():
        by_rack.setdefault(f"{s.rack_row}/{s.rack_num}", []).append(s.name)
    zone = 0
    for rk in room["racks"]:
        w = rk["weights"][crah.name]["d_inlet_d_supply"]
        for n in by_rack.get(rk["rack"], []):
            assert got[n] - control["inlets"][n] == pytest.approx(-BIAS * w, abs=0.3)
        if rk["weights"][crah.name]["live"] > 0.5:
            zone += 1
            for n in by_rack[rk["rack"]]:
                assert got[n] < control["inlets"][n] - 0.5 * BIAS * 0.9
    assert zone > 0


def test_affects_control_refused_on_a_monitoring_point(tmp_path, api_state):
    from fastapi import HTTPException
    r = _rig(tmp_path)
    api_state(r)
    with pytest.raises(HTTPException) as ei:
        _add(r, r.crah(1), "Return_Air_Temp", bias=1.0, affects_control=True)
    assert ei.value.status_code == 422


def test_affects_control_chiller_and_cdu_move_the_process(tmp_path):
    """The other two control loops: the chiller header falls by the mean bias
    over the running units; the CDU's coolant falls by its own bias."""
    from core.fault_injection import SensorFault
    _clear_caches()
    random.seed(SEED)
    fx = build_plant(tmp_path, cdus=1)
    for _ in range(5):
        fx.tick()
    chl = fx.made["CHL1"].name
    base = fx.store._chw_supply_c[DC]
    running = len(fx.running_chillers())
    fx.store.faults.add_sensor(SensorFault(id="flt_20231114T221320_c0ffee", device=chl,
                                           device_id=fx.made["CHL1"].id,
                                           point="CHW_Supply_Temp", bias=BIAS,
                                           affects_control=True, surface="bacnet"))
    fx.tick()
    assert chl in fx.running_chillers()
    assert fx.store._chw_supply_c[DC] == pytest.approx(base - BIAS / running, abs=0.15)
    own = fx.auto_points(chl)["CHW_Supply_Temp"]
    assert own == pytest.approx(base - BIAS, abs=0.15)
    cdu = fx.made["CDU1"].name
    tcs0 = fx.auto_points(cdu).get("TCS_Supply_Temp")
    fx.store.faults.add_sensor(SensorFault(id="flt_20231114T221320_cdu001", device=cdu,
                                           device_id=fx.made["CDU1"].id,
                                           point="TCS_Supply_Temp", bias=BIAS,
                                           affects_control=True, surface="bacnet"))
    fx.tick()
    tcs1 = fx.auto_points(cdu).get("TCS_Supply_Temp")
    assert tcs0 is not None and tcs1 is not None
    assert tcs1 == pytest.approx(tcs0 - BIAS, abs=0.6)

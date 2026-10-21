"""S0-3 / S0-4 (doc 32 §7, §14): the injection ledger, and a hook on EVERY
existing injection channel.

Event-level scoring counts a finding as false when it overlaps no injection, so
a channel that forgot to ledger would score a correct detection as a false
alarm. One case per channel below; each drives the real router handler.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from core import fault_ledger as fl
from core.device_manager import Device, DeviceType, Vendor


# ── core ─────────────────────────────────────────────────────────────────────

def test_open_close_fold_roundtrip(tmp_path):
    L = fl.FaultLedger(tmp_path / "l.jsonl")
    e = L.open("sensor", device="CRAH1", device_id="abc", point="Supply_Air_Temp",
               params={"bias": 3.0}, expected_kind="sensor",
               metric_hint={"metric": "supply_air_temp", "instance": ""},
               source="test", campaign="c/r1", start=1_700_000_000.123)
    assert e["id"].startswith("flt_20231114T221320_")
    assert e["start"] == "2023-11-14T22:13:20.123Z"
    L.close(e["id"], "expired", end=1_700_000_100.0)
    got = L.get(e["id"])
    for k in ("id", "kind", "device", "point", "start", "end", "params", "device_id",
              "expected_kind", "metric_hint", "source", "campaign", "end_reason",
              "marks", "resumes", "sim"):
        assert k in got, k
    assert got["end_reason"] == "expired"
    assert set(got["sim"]) == {"seed", "air_mode", "head"}
    lines = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert [x["op"] for x in lines] == ["open", "close"]
    assert all(x["v"] == 1 for x in lines)


def test_end_reason_is_validated(tmp_path):
    L = fl.FaultLedger(tmp_path / "l.jsonl")
    e = L.open("pin")
    with pytest.raises(ValueError):
        L.close(e["id"], "because")


def test_key_supersedes(tmp_path):
    L = fl.FaultLedger(tmp_path / "l.jsonl")
    a = L.open("override", key=("override", "X", "P"), params={"value": 1})
    b = L.open("override", key=("override", "X", "P"), params={"value": 0})
    assert L.get(a["id"])["end_reason"] == "superseded"
    assert L.get(b["id"])["end"] is None
    L.close_key(("override", "X", "P"))
    assert L.get(b["id"])["end_reason"] == "cleared"


def test_mark_and_resume(tmp_path):
    L = fl.FaultLedger(tmp_path / "l.jsonl")
    e = L.open("degradation")
    L.mark(e["id"], "target_reached", at=1_700_000_000.0)
    L.resume(e["id"], 1_700_000_000.0, 1_700_000_600.0)
    got = L.get(e["id"])
    assert got["marks"][0]["what"] == "target_reached"
    assert got["resumes"][0]["gap"] == ["2023-11-14T22:13:20.000Z", "2023-11-14T22:23:20.000Z"]
    assert got["end"] is None              # a fouled coil does not unfoul


def test_survives_restart_and_stale_entries_close(tmp_path):
    p = tmp_path / "l.jsonl"
    L = fl.FaultLedger(p)
    keep = L.open("sensor")
    stale = L.open("override", key=("override", "X", "P"))
    L2 = fl.FaultLedger(p)                   # a new process
    assert {e["id"] for e in L2.open_entries()} == {keep["id"], stale["id"]}
    closed = L2.close_stale(at=1_700_000_000.0, keep=[keep["id"]])
    assert closed == [stale["id"]]
    assert L2.get(stale["id"])["end_reason"] == "sim_restart"
    assert L2.get(stale["id"])["end"] == "2023-11-14T22:13:20.000Z"
    assert L2.get(keep["id"])["end"] is None


def test_truncated_last_line_is_skipped(tmp_path, caplog):
    p = tmp_path / "l.jsonl"
    L = fl.FaultLedger(p)
    e = L.open("sensor")
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"v":1,"op":"close","id":"' + e["id"] + '","end":"2026-')   # crash mid-write
    with caplog.at_level("WARNING"):
        L2 = fl.FaultLedger(p)
    assert L2.get(e["id"])["end"] is None
    assert any("unreadable" in r.getMessage() for r in caplog.records)


def test_close_without_open_is_tolerated(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"v":1,"op":"close","id":"flt_x","end":"2026-01-01T00:00:00.000Z",'
                 '"end_reason":"deleted"}\n', encoding="utf-8")
    L = fl.FaultLedger(p)
    assert L.query()["entries"] == []


def test_query_filters_newest_first_and_paginates(tmp_path):
    L = fl.FaultLedger(tmp_path / "l.jsonl")
    ids = [L.open("sensor" if i % 2 else "pin", device=f"D{i}", campaign="c" if i < 3 else None,
                  start=1_700_000_000.0 + i * 100)["id"] for i in range(6)]
    q = L.query()
    assert [e["id"] for e in q["entries"]] == ids[::-1]
    assert [e["id"] for e in L.query(kind="pin")["entries"]] == [ids[4], ids[2], ids[0]]
    assert [e["id"] for e in L.query(campaign="c")["entries"]] == ids[:3][::-1]
    assert [e["id"] for e in L.query(device="D3")["entries"]] == [ids[3]]
    page1 = L.query(limit=4)
    assert len(page1["entries"]) == 4 and page1["next_cursor"]
    page2 = L.query(limit=4, cursor=page1["next_cursor"])
    assert [e["id"] for e in page2["entries"]] == [ids[1], ids[0]]
    assert page2["next_cursor"] is None
    # since/until overlap: entries starting after `until` are out
    q = L.query(until="2023-11-14T22:15:00Z")
    assert [e["id"] for e in q["entries"]] == [ids[1], ids[0]]


def test_get_ledger_honours_set_ledger(tmp_path):
    L = fl.FaultLedger(tmp_path / "x.jsonl")
    fl.set_ledger(L)
    assert fl.get_ledger() is L


def test_ledger_lives_outside_snmp_datasets():
    """snmpsim serves every file in datasets/snmp and the reaper deletes strays."""
    assert fl.DEFAULT_DIR.parts[-2:] == ("datasets", "faults")


# ── per-channel hooks ────────────────────────────────────────────────────────

def _dev(name, dtype, **kw):
    d = Device(name=name, device_type=DeviceType(dtype), vendor=Vendor.SUPERMICRO,
               ip_address=kw.pop("ip", ""), **kw)
    return d


class _DM:
    def __init__(self, *devs):
        self.by = {d.id: d for d in devs}

    def get_device(self, i):
        return self.by.get(i)

    def get_all_devices(self):
        return list(self.by.values())


class _Store(SimpleNamespace):
    pass


@pytest.fixture
def env(monkeypatch):
    """A stub AppState carrying just what each router handler touches."""
    from api.state import AppState
    crah = _dev("CRAH1-DC1-HA-R9-01", "crah", ip="10.0.5.1")
    valve = _dev("VCHW-DC1-CP", "valve")
    srv = _dev("SRV01-DC1-HA-R1-01", "server", ip="10.1.0.1")
    sen = _dev("SEN1-DC1-HA-R2-01", "sensor")
    ups = _dev("UPS1-DC1-ER", "ups")
    ats = _dev("ATS1-DC1-ER", "ats")
    calls = []
    st = _Store(
        plant_alarm_overrides={}, device_overrides={}, pdu_fault_outlet={},
        metric_flags={}, metric_limits={}, _tick_interval=1,
        set_utility_outage=lambda dc, f: calls.append(("utility", dc, f)),
        set_ats_failed=lambda i, f: calls.append(("ats", i, f)),
        set_fault=lambda *a: calls.append(("fault",) + a),
        clear_fault=lambda *a: calls.append(("clear",) + a),
        set_ats_condition=lambda *a: None, set_ups_forced_battery=lambda *a: None,
        set_smoke=lambda *a: None, set_asd_flow_fault=lambda *a: None,
        hold_gas_abort=lambda *a: None, reset_fire=lambda *a: None,
        is_running=lambda: True, is_paused=lambda: False,
        set_paused=lambda p: None, set_tick_interval=lambda i: None,
    )
    topo = SimpleNamespace(break_link=lambda *a: None, restore_link=lambda *a: None)
    rf = SimpleNamespace(is_running=lambda: True,
                         perform_action=lambda ip, a: {"device": "SRV01-DC1-HA-R1-01",
                                                       "ok": True, "message": "done"})
    eng = SimpleNamespace(start=lambda: None, stop=lambda: None,
                          cfg=SimpleNamespace(minutes_per_day=1.0),
                          advance_day=lambda: SimpleNamespace(day=3, added=[1], removed=[],
                                                              total_servers=9))
    s = SimpleNamespace(state_store=st, device_manager=_DM(crah, valve, srv, sen, ups, ats),
                        topology=topo, redfish=rf, fleet_engine=eng,
                        notify_ui=lambda *a: None)
    monkeypatch.setattr(AppState, "get", classmethod(lambda cls: s))
    from api.routers import topology as topo_r
    monkeypatch.setattr(topo_r, "_sync_iface_history", lambda *a: None)
    monkeypatch.setattr(topo_r, "_send_link_traps", lambda *a, **k: None)
    monkeypatch.setattr(topo_r, "_patch_link_endpoints", lambda *a: None)
    return SimpleNamespace(s=s, crah=crah, valve=valve, srv=srv, sen=sen, ups=ups, ats=ats,
                           calls=calls)


def _entries(**kw):
    return fl.get_ledger().query(**kw)["entries"]


def test_plant_override_value_and_clear(env):
    from api.routers.bacnet import set_plant_override, PlantOverride
    set_plant_override(PlantOverride(device=env.crah.id, point="Alarm_HighTemp", value=1.0))
    e = _entries(kind="override")[0]
    assert (e["device"], e["device_id"], e["point"]) == (env.crah.name, env.crah.id, "Alarm_HighTemp")
    assert e["expected_kind"] == "process" and e["end"] is None
    assert e["source"] == "POST /api/bacnet/plant/overrides"
    set_plant_override(PlantOverride(device=env.crah.id, point="Alarm_HighTemp", value=None))
    assert _entries(kind="override")[0]["end_reason"] == "cleared"


def test_plant_setpoint_is_context(env):
    from api.routers.bacnet import set_plant_override, PlantOverride
    set_plant_override(PlantOverride(device=env.crah.id, point="Setpoint", value=24.0))
    e = _entries(kind="setpoint")[0]
    assert e["expected_kind"] == "none" and e["params"]["to"] == 24.0
    assert e["metric_hint"] == {"metric": "air_setpoint_temp", "instance": ""}


def test_valve_position_hold_without_actuator_fault_is_sensor(env):
    from api.routers.bacnet import set_plant_override, PlantOverride
    set_plant_override(PlantOverride(device=env.valve.id, point="Position", value=40.0))
    assert _entries(kind="override")[0]["expected_kind"] == "sensor"
    set_plant_override(PlantOverride(device=env.valve.id, point="Alarm_ActuatorFault", value=1.0))
    set_plant_override(PlantOverride(device=env.valve.id, point="Position", value=30.0))
    e = [x for x in _entries(kind="override") if x["point"] == "Position"][0]
    assert e["expected_kind"] == "process"


def test_utility_and_ats(env):
    from api.routers.bacnet import set_utility, set_ats, UtilityOutage, ATSFault
    set_utility(UtilityOutage(datacenter="DC1", failed=True))
    set_ats(ATSFault(device_id=env.ats.id, failed=True))
    es = _entries(kind="electrical")
    assert {e["params"]["what"] for e in es} == {"utility", "ats"}
    assert all(e["expected_kind"] == "process" and e["end"] is None for e in es)
    set_utility(UtilityOutage(datacenter="DC1", failed=False))
    set_ats(ATSFault(device_id=env.ats.id, failed=False))
    assert all(e["end_reason"] == "cleared" for e in _entries(kind="electrical"))


def test_fault_menu(env):
    from api.routers.devices import set_device_fault, FaultRequest
    set_device_fault(env.srv.id, FaultRequest(fault="cpu_high"))
    e = _entries(kind="fault_menu")[0]
    assert e["expected_kind"] == "process" and e["params"]["fault"] == "cpu_high"
    set_device_fault(env.srv.id, FaultRequest(fault="cpu_high", action="clear"))
    assert _entries(kind="fault_menu")[0]["end_reason"] == "cleared"


def test_fault_menu_ambient_on_a_sensor_is_a_sensor_fault(env):
    from api.routers.devices import set_device_fault, FaultRequest, FAULT_MAP
    if "sensor" not in FAULT_MAP["ambient_high"]["types"]:
        pytest.skip("ambient_high not offered on sensors")
    set_device_fault(env.sen.id, FaultRequest(fault="ambient_high"))
    assert _entries(kind="fault_menu")[0]["expected_kind"] == "sensor"


def test_refused_fault_menu_ledgers_nothing(env):
    from fastapi import HTTPException
    from api.routers.devices import set_device_fault, FaultRequest
    with pytest.raises(HTTPException):
        set_device_fault(env.srv.id, FaultRequest(fault="no_such_fault"))
    assert _entries() == []


def test_metric_pin(env):
    from api.routers.devices import set_device_override, DeviceOverride
    set_device_override(env.srv.id, DeviceOverride(metric="inlet_temp", value=40.0))
    set_device_override(env.srv.id, DeviceOverride(metric="cpu_usage", value=90.0))
    es = {e["point"]: e for e in _entries(kind="pin")}
    assert es["inlet_temp"]["expected_kind"] == "sensor"
    assert es["cpu_usage"]["expected_kind"] == "process"
    set_device_override(env.srv.id, DeviceOverride(metric="inlet_temp", value=None))
    es = {e["point"]: e for e in _entries(kind="pin")}
    assert es["inlet_temp"]["end_reason"] == "cleared" and es["cpu_usage"]["end"] is None


def test_fire_channels(env):
    from api.routers.modbus import (smoke_in_room, gas_abort, fire_reset, SmokeAction,
                                    AbortAction, FireReset)
    smoke_in_room(SmokeAction(datacenter="DC1", room="ha", pct_per_m=0.2))
    gas_abort(AbortAction(datacenter="DC1", room="HA", held=True))
    es = _entries(kind="fire")
    assert {e["params"]["what"] for e in es} == {"smoke", "abort"}
    assert all(e["expected_kind"] == "none" for e in es)
    fire_reset(FireReset(datacenter="DC1"))
    es = _entries(kind="fire")
    assert all(e["end"] is not None for e in es)
    assert any(e["params"]["what"] == "reset" for e in es)


def test_link_break_restore(env):
    from api.routers.topology import break_link, restore_link
    from api.models.schemas import LinkActionRequest
    req = LinkActionRequest(src_id=env.srv.id, dst_id=env.crah.id)
    break_link(req)
    e = _entries(kind="link")[0]
    assert e["end"] is None and e["expected_kind"] == "none" and e["device"] == env.srv.name
    restore_link(LinkActionRequest(src_id=env.crah.id, dst_id=env.srv.id))   # either order
    assert _entries(kind="link")[0]["end_reason"] == "cleared"


def test_redfish_power(env):
    from api.routers.redfish import redfish_action, RedfishAction
    redfish_action(RedfishAction(ip="10.1.0.1", action="power_off"))
    e = _entries(kind="power")[0]
    assert e["end"] is None and e["expected_kind"] == "process"
    assert (e["device"], e["device_id"]) == (env.srv.name, env.srv.id)
    redfish_action(RedfishAction(ip="10.1.0.1", action="power_on"))
    assert _entries(kind="power")[0]["end_reason"] == "cleared"
    redfish_action(RedfishAction(ip="10.1.0.1", action="led_on"))
    assert len(_entries(kind="power")) == 1                  # LED does not move telemetry


def test_tick_pause_and_interval(env):
    from api.routers.tick import apply_tick_settings, TickUpdate
    apply_tick_settings(TickUpdate(paused=True))
    e = _entries(kind="context")[0]
    assert e["params"]["context"] == "tick_pause" and e["end"] is None
    apply_tick_settings(TickUpdate(paused=False, interval=2))
    es = {x["params"]["context"]: x for x in _entries(kind="context")}
    assert es["tick_pause"]["end_reason"] == "cleared"
    assert es["tick_interval"]["params"]["interval"] == 2


def test_fleet(env):
    from api.routers.fleet import fleet_start, fleet_stop, fleet_advance
    fleet_start()
    fleet_advance()
    fleet_stop()
    es = {x["params"]["context"]: x for x in _entries(kind="context")}
    assert es["fleet_running"]["end_reason"] == "cleared"
    assert "day 3" in es["fleet_advance"]["params"]["detail"]


def test_protocol_start_stop_and_regeneration():
    from api.routers import _ledger
    _ledger.protocol_event("/api/snmp/stop", "POST", 200)
    _ledger.protocol_event("/api/snmp/datasets/generate", "POST", 200)
    _ledger.protocol_event("/api/bacnet/start", "POST", 409)        # refused: nothing
    _ledger.protocol_event("/api/devices/x/fault", "POST", 200)     # not a protocol
    _ledger.protocol_event("/api/snmp/start", "POST", 200)
    es = _entries(kind="context")
    ctx = sorted(e["params"]["context"] for e in es)
    assert ctx == ["protocol_datasets_generate", "protocol_start", "protocol_stopped"]
    stopped = [e for e in es if e["params"]["context"] == "protocol_stopped"][0]
    assert stopped["end_reason"] == "cleared" and stopped["params"]["protocol"] == "snmp"


def test_protocol_middleware_is_registered():
    import api.main as m
    src = inspect.getsource(m)
    assert "_ledger.protocol_event(" in src


def test_a_ledger_failure_never_breaks_the_injection(env, monkeypatch):
    from api.routers.bacnet import set_plant_override, PlantOverride

    class _Boom:
        def __getattr__(self, n):
            raise RuntimeError("disk full")
    monkeypatch.setattr(fl, "get_ledger", lambda: _Boom())
    r = set_plant_override(PlantOverride(device=env.crah.id, point="Alarm_HighTemp", value=1.0))
    assert r.ok
    assert env.s.state_store.plant_alarm_overrides[env.crah.name]["Alarm_HighTemp"] == 1.0


def test_store_boot_closes_stale_and_ledgers_start(tmp_path):
    """A plant override open on disk from the last process is closed as
    sim_restart at the last save time; the start and the one-time C1 offset
    step are ledgered as context."""
    from tests.conftest import build_plant
    L = fl.get_ledger()
    stale = L.open("override", device="X", point="P")
    fx = build_plant(tmp_path)
    (L.path.parent / "active.json").write_text(json.dumps(
        {"v": 1, "saved_at": 1_700_000_000.0, "sensor_faults": []}), encoding="utf-8")
    fx.store._boot_faults()
    got = L.get(stale["id"])
    assert got["end_reason"] == "sim_restart" and got["end"] == "2023-11-14T22:13:20.000Z"
    ctx = [e["params"] for e in L.query(kind="context")["entries"]]
    assert any(p.get("context") == "sim_start" for p in ctx)
    assert sum(1 for p in ctx if p.get("change") == "c1_stable_hash") == 1
    fx.store._faults_booted = False
    fx.store._boot_faults()                        # a second process start
    ctx = [e["params"] for e in L.query(kind="context")["entries"]]
    assert sum(1 for p in ctx if p.get("change") == "c1_stable_hash") == 1

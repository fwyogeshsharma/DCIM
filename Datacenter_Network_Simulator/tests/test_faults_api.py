"""S0-7 (doc 32 §4.6, §4.7, §7.4): the /api/faults router.

Handlers are called directly (the repo's venv has no httpx, so FastAPI's
TestClient cannot load); every status code in the §4.7 table is pinned.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from conftest import build_plant
from api.routers import faults as F


@pytest.fixture
def rig(tmp_path, plant_cache, monkeypatch):
    from api.state import AppState
    fx = build_plant(tmp_path, crahs=2, servers=4, probes=True)
    for _ in range(3):
        fx.store._tick()
    s = SimpleNamespace(state_store=fx.store, device_manager=fx.dm, topology=fx.topo)
    monkeypatch.setattr(AppState, "get", classmethod(lambda cls: s))
    return SimpleNamespace(fx=fx, s=s, crah=fx.made["CRAH1"], srv=fx.made["SRV1"],
                           chws=fx.made["CHWS"])


def _post(**kw):
    return F.add_sensor_fault(F.SensorFaultBody(**kw))


def _status(fn, *a, **kw):
    with pytest.raises(HTTPException) as ei:
        fn(*a, **kw)
    return ei.value.status_code


def test_post_shape(rig):
    out = _post(device=rig.crah.id, point="Supply_Air_Temp", bias=3.0, duration_s=21600,
                campaign="crah-supply-bias/r1")
    assert set(out) >= {"id", "kind", "device", "device_id", "point", "start", "end",
                        "params", "protocols", "expected_kind", "metric_hint"}
    assert out["id"].startswith("flt_") and len(out["id"].split("_")[-1]) == 6
    assert out["kind"] == "sensor" and out["expected_kind"] == "sensor"
    assert out["device"] == rig.crah.name and out["device_id"] == rig.crah.id
    assert out["end"] is None and out["start"].endswith("Z")
    assert out["params"] == {"bias": 3.0, "drift_per_h": 0.0, "noise_mult": 1.0,
                             "stuck": False, "dropout_pct": 0.0, "dropout_window_s": 30.0,
                             "duration_s": 21600, "affects_control": False}
    assert out["protocols"] == ["bacnet", "snmp"]
    assert out["metric_hint"] == {"metric": "supply_air_temp", "instance": ""}


def test_affects_control_records_process_symptoms(rig):
    out = _post(device=rig.crah.id, point="Supply_Air_Temp", bias=2.0, affects_control=True)
    assert out["expected_kind"] == "sensor" and out["params"]["process_symptoms"] is True


def test_status_codes(rig):
    assert _status(_post, device="nope", point="Supply_Air_Temp", bias=1.0) == 404
    assert _status(_post, device=rig.crah.id, point="No_Such_Point", bias=1.0) == 422
    assert _status(_post, device=rig.crah.id, point="Supply_Air_Temp") == 422       # no-op
    assert _status(_post, device=rig.crah.id, point="Supply_Air_Temp",
                   dropout_pct=101) == 422
    assert _status(_post, device=rig.crah.id, point="Fan_Speed", bias=1.0,
                   affects_control=True) == 422
    assert _status(_post, device=rig.crah.id, point="Unit_Running", bias=1.0) == 422  # binary
    _post(device=rig.crah.id, point="Supply_Air_Temp", bias=1.0)
    assert _status(_post, device=rig.crah.id, point="Supply_Air_Temp", bias=2.0) == 409


def test_503_without_a_store(monkeypatch):
    from api.state import AppState
    monkeypatch.setattr(AppState, "get", classmethod(
        lambda cls: SimpleNamespace(state_store=None, device_manager=None)))
    assert _status(_post, device="x", point="p", bias=1.0) == 503
    assert _status(F.list_faults) == 503


def test_stuck_value_on_a_binary_is_allowed(rig):
    out = _post(device=rig.crah.id, point="Unit_Running", stuck_value=0.0)
    assert out["params"]["stuck_value"] == 0.0


def test_list_get_delete(rig):
    a = _post(device=rig.crah.id, point="Supply_Air_Temp", bias=3.0, drift_per_h=1.0)
    b = _post(device=rig.srv.id, point="inlet_temp", bias=2.0)
    act = F.list_faults()["active"]
    assert {x["id"] for x in act} == {a["id"], b["id"]}
    one = next(x for x in act if x["id"] == a["id"])
    assert one["kind"] == "sensor" and one["start"].endswith("Z")
    assert one["current"]["bias"] >= 3.0
    assert F.get_fault(a["id"])["end"] is None
    closed = F.delete_fault(a["id"])
    assert closed["end_reason"] == "deleted" and closed["end"] is not None
    assert F.get_fault(a["id"])["end_reason"] == "deleted"          # historical still readable
    assert {x["id"] for x in F.list_faults()["active"]} == {b["id"]}
    assert _status(F.delete_fault, a["id"]) == 404
    assert _status(F.get_fault, "flt_nope") == 404


def test_ledger_endpoint(rig):
    a = _post(device=rig.crah.id, point="Supply_Air_Temp", bias=3.0, campaign="c/r1")
    _post(device=rig.srv.id, point="inlet_temp", bias=2.0)
    q = F.faults_ledger(campaign="c/r1")
    assert [e["id"] for e in q["entries"]] == [a["id"]]
    assert q["next_cursor"] is None
    assert len(F.faults_ledger(kind="sensor")["entries"]) == 2
    assert F.faults_ledger(device=rig.srv.name)["entries"][0]["point"] == "inlet_temp"


def test_points_endpoint(rig):
    pts = {p["point"]: p for p in F.fault_points(device=rig.crah.id)["points"]}
    sa = pts["Supply_Air_Temp"]
    assert sa["control"] is True and sa["store_published"] is True
    assert sa["protocols"] == ["bacnet", "snmp"]
    assert pts["Fan_Speed"]["control"] is False and pts["Fan_Speed"]["store_published"] is False
    assert pts["Unit_Running"]["binary"] is True
    srv = {p["point"]: p for p in F.fault_points(device=rig.srv.id)["points"]}
    assert srv["inlet_temp"]["protocols"] == ["snmp", "redfish"]
    assert srv["inlet_temp"]["metric_hint"]["metric"] == "inlet_temperature"
    probe = {p["point"]: p for p in F.fault_points(device=rig.chws.id)["points"]}
    assert "water_temp" in probe and set(probe["water_temp"]["protocols"]) == {"snmp", "modbus"}
    assert _status(F.fault_points, device="nope") == 404


def test_expiry_through_the_tick(rig):
    import time
    out = _post(device=rig.crah.id, point="Supply_Air_Temp", bias=3.0, duration_s=1)
    rig.fx.store.faults.clock = lambda: time.time() + 5
    rig.fx.store._tick()
    assert F.list_faults()["active"] == []
    assert F.get_fault(out["id"])["end_reason"] == "expired"


def test_air_influence_409_in_uniform(rig):
    rig.fx.store._air_mode = "uniform"
    assert _status(F.air_influence) == 409


def test_router_is_registered_under_api_with_auth():
    import api.main as m
    paths = m.app.openapi()["paths"]
    for p in ("/api/faults/sensor", "/api/faults", "/api/faults/ledger", "/api/faults/points",
              "/api/faults/{fault_id}", "/api/air/influence"):
        assert p in paths, p
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "app.include_router(faults_router.router, prefix=\"/api\", dependencies=_AUTH)" in src


_REL = Path("dcim-platform") / "contracts" / "mappings" / "bacnet" / "objects.yaml"
_OBJECTS = next((p / _REL for p in Path(__file__).resolve().parents[1:4] if (p / _REL).exists()),
                Path(__file__).resolve().parents[2] / _REL)


@pytest.mark.skipif(not _OBJECTS.exists(), reason="dcim-platform not checked out beside")
def test_bacnet_hints_match_the_platform_mapping():
    """The hint table is duplicated by hand; pin it when both repos exist."""
    import re
    from core.fault_points import BACNET_HINTS
    txt = _OBJECTS.read_text(encoding="utf-8")
    cur = None
    found = {}
    for ln in txt.splitlines():
        m = re.match(r"^  ([a-z_]+):\s*$", ln)
        if m:
            cur = m.group(1)
            continue
        m = re.search(r"name:\s*\"?([A-Za-z_*]+)\"?,\s*metric:\s*([a-z_]+)(?:.*instance:\s*([A-Za-z_]+))?", ln)
        if m and cur:
            found.setdefault(cur, {})[m.group(1)] = (m.group(2), m.group(3) or "")
    for dtype, pts in BACNET_HINTS.items():
        for point, (metric, inst) in pts.items():
            assert found[dtype][point] == (metric, inst), (dtype, point)

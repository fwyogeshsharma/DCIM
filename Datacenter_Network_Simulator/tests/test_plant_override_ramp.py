"""S1-7 (doc 32 §6.1, §14): ramp and oscillate on plant overrides.

A ramp is a generic test instrument for a gradual change on a point nothing
else drives (bearing temperature, vibration, basin level, loop pressure). On a
point the store publishes every tick a ramp would be overwritten and ledger a
fault that never reached the wire, so it is refused with 409 naming the right
tool.
"""
from __future__ import annotations

import math

import pytest
from fastapi import HTTPException

from test_degradation import _rig
from test_fault_publish_truth import api_state  # noqa: F401  (fixture)


def _post(dev, point, value=None, ramp=None, oscillate=None, campaign=None):
    from api.routers.bacnet import (OverrideOscillate, OverrideRamp, PlantOverride,
                                    set_plant_override)
    return set_plant_override(PlantOverride(
        device=dev.id, point=point, value=value, campaign=campaign,
        ramp=OverrideRamp(**ramp) if ramp else None,
        oscillate=OverrideOscillate(**oscillate) if oscillate else None))


def _wire(r, name, point):
    return r.bac[name].get_snapshot()[point]


@pytest.fixture
def rig(tmp_path, api_state):
    r = _rig(tmp_path, cdus=1)
    api_state(r)
    r.tick(20)
    return r


def test_ramp_on_a_store_published_point_is_409_naming_the_tools(rig):
    crah = rig.h.made["CRAH1"]
    with pytest.raises(HTTPException) as ei:
        _post(crah, "Supply_Air_Temp", ramp={"rate_per_s": 0.01, "target": 30.0})
    assert ei.value.status_code == 409
    assert "/api/faults/sensor" in ei.value.detail and "/api/faults/degradation" in ei.value.detail
    pump = rig.h.made["CHWP1"]
    for pt in ("Flow", "Suction_Pressure"):
        with pytest.raises(HTTPException) as ei:
            _post(pump, pt, ramp={"rate_per_s": 0.1, "target": 1.0})
        assert ei.value.status_code == 409
    # Nothing was ledgered for a refused ramp.
    assert not rig.store.faults.ledger.query(kind="ramp")["entries"]


def test_ramp_on_a_binary_is_422(rig):
    with pytest.raises(HTTPException) as ei:
        _post(rig.h.made["CHWP1"], "Alarm_Fault", ramp={"rate_per_s": 0.1, "target": 1.0})
    assert ei.value.status_code == 422


def test_motor_temp_ramp_reaches_its_target_at_the_stated_rate(rig):
    pump = rig.h.made["CHWP1"]
    _post(pump, "Motor_Temp", value=50.0, ramp={"rate_per_s": 0.1, "target": 62.0},
          campaign="pump-bearing-heat/t")
    rig.tick(60)                                        # 6 K of 12
    assert _wire(rig, pump.name, "Motor_Temp") == pytest.approx(56.0, rel=0.01)
    rig.tick(100)
    assert _wire(rig, pump.name, "Motor_Temp") == pytest.approx(62.0, abs=1e-6)
    e = rig.store.faults.ledger.query(kind="ramp")["entries"][0]
    assert e["point"] == "Motor_Temp" and e["expected_kind"] == "process"
    assert e["params"] == {"from": 50.0, "to": 62.0, "rate_per_s": 0.1}
    assert e["campaign"] == "pump-bearing-heat/t"
    assert [m["what"] for m in e["marks"]] == ["target_reached"]
    # Clearing ends the ramp and closes the entry.
    _post(pump, "Motor_Temp", value=None)
    rig.tick(2)
    assert "Motor_Temp" not in (rig.store.plant_alarm_overrides.get(pump.name) or {})
    assert pump.name not in rig.store.plant_ramps
    assert rig.store.faults.ledger.get(e["id"])["end_reason"] == "cleared"


def test_a_ramp_starts_from_the_current_truth(rig):
    tower = rig.h.made["CT1"]
    from core.device_state_store import _plant_state_cache
    v0 = _plant_state_cache[tower.name]["Vibration"]
    _post(tower, "Vibration", ramp={"rate_per_s": 0.001, "target": 6.0})
    assert rig.store.plant_ramps[tower.name]["Vibration"]["start"] == pytest.approx(v0)


def test_oscillate_on_a_valve(rig):
    """valve-hunting (VA-2): command and feedback move together about the
    current position, so VA-1 (mismatch) must not fire."""
    rig2 = rig
    from core.device_state_store import _plant_state_cache  # noqa: F401
    cdu = rig2.h.made["CDU1"]              # any device with a holdable valve-like point
    tower = rig2.h.made["CT1"]
    _post(tower, "Basin_Level", value=60.0, oscillate={"period_s": 240.0, "amplitude": 15.0})
    vals = []
    for _ in range(240):
        rig2.tick(1)
        vals.append(_wire(rig2, tower.name, "Basin_Level"))
    assert max(vals) == pytest.approx(75.0, abs=0.2) and min(vals) == pytest.approx(45.0, abs=0.2)
    # A sine: a quarter period in it sits at +amplitude.
    assert vals[59] == pytest.approx(60.0 + 15.0 * math.sin(2 * math.pi * 60 / 240), abs=0.01)
    e = rig2.store.faults.ledger.query(kind="ramp")["entries"][0]
    assert e["params"]["oscillate"] == {"period_s": 240.0, "amplitude": 15.0}
    assert cdu  # the fixture plant carries no stand-alone valve unless asked


def test_oscillation_is_clamped_to_the_instrument_span(tmp_path, api_state):
    from conftest import build_plant
    from test_fault_publish_truth import Rig, _attach_bacnet, _clear_caches
    _clear_caches()
    h = build_plant(tmp_path, crahs=1, valves=True)
    ctrl, bac = _attach_bacnet(h, tmp_path)
    r = Rig(h=h, store=h.store, ctrl=ctrl, bac=bac)
    api_state(r)
    r.tick(5)
    v = h.made["VCHW"]
    for pt in ("Commanded_Position", "Position"):
        _post(v, pt, value=95.0, oscillate={"period_s": 40.0, "amplitude": 15.0})
    seen = []
    for _ in range(40):
        r.tick(1)
        snap = bac[v.name].get_snapshot()
        seen.append((snap["Commanded_Position"], snap["Position"]))
    assert max(c for c, _p in seen) == 100.0                 # 0-100 % span
    assert all(c == p for c, p in seen)                       # in phase: no mismatch

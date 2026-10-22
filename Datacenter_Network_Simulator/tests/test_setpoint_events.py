"""S1-8 (doc 32 §6.2, §14): setpoints are honoured by physics.

Before this, an operator "changing a setpoint" changed a label: CHW_Setpoint and
TCS_Setpoint were rewritten from constants every tick and the CRAH Setpoint was
ignored by the discharge model. Setpoint changes are the negative controls an
L3 baseline must NOT call a fault, so they have to move the process.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from conftest import DC
from test_degradation import _rig
from test_fault_publish_truth import api_state  # noqa: F401  (fixture)
from test_plant_override_ramp import _post, _wire


@pytest.fixture
def pair(tmp_path, api_state):
    ctl = _rig(tmp_path / "ctl")
    ctl.tick(40)
    r = _rig(tmp_path / "sp")
    api_state(r)
    r.tick(40)
    return ctl, r


def test_crah_setpoint_moves_the_discharge_and_the_room(pair):
    ctl, r = pair
    crahs = [r.h.made[f"CRAH{i}"] for i in range(1, 5)]
    room0 = r.call(r.store._room_supply_temp, r.servers()[0])
    for c in crahs:
        _post(c, "Setpoint", value=24.0, campaign="crah-setpoint-raise/t")
    r.tick(30)
    ctl.tick(30)
    pen = r.store._chw_pen.get(DC, 0.0)
    for c in crahs:
        assert _wire(r, c.name, "Setpoint") == 24.0
        assert _wire(r, c.name, "Supply_Air_Temp") == pytest.approx(24.0 + pen, abs=0.15)
    assert r.call(r.store._room_supply_temp, r.servers()[0]) - room0 == pytest.approx(2.0, abs=0.2)
    # The valve closes a little for a warmer discharge at the same load.
    assert _wire(r, crahs[0].name, "CHW_Valve") < _wire(ctl, crahs[0].name, "CHW_Valve")
    # Rack inlets follow the room.
    srv = r.servers()[0]
    r.call(r.store._step_device, srv)
    ctl.call(ctl.store._step_device, ctl.servers()[0])
    assert srv.inlet_temp > ctl.servers()[0].inlet_temp + 1.0
    e = r.store.faults.ledger.query(kind="setpoint")["entries"]
    assert len(e) == 4 and all(x["expected_kind"] == "none" for x in e)
    assert e[0]["params"]["from"] == 22.0 and e[0]["params"]["to"] == 24.0


def test_crah_setpoint_ramp(pair):
    _ctl, r = pair
    c = r.h.made["CRAH1"]
    _post(c, "Setpoint", ramp={"rate_per_s": 0.01, "target": 24.0})
    r.tick(100)
    assert _wire(r, c.name, "Setpoint") == pytest.approx(23.0, abs=0.02)
    r.tick(120)
    assert _wire(r, c.name, "Setpoint") == pytest.approx(24.0)
    e = r.store.faults.ledger.query(kind="setpoint")["entries"][0]
    assert e["params"]["rate_per_s"] == 0.01 and e["params"]["to"] == 24.0


def test_chw_reset_is_not_overwritten_and_lifts_cop(pair):
    ctl, r = pair
    ch = r.h.made["CHL1"]
    _post(ch, "CHW_Setpoint", value=9.0, campaign="chw-reset/t")
    # Applied to every chiller of the DC: one header, one setpoint.
    for i in (1, 2, 3):
        assert r.store.plant_alarm_overrides[f"CHL{i}-{DC}-CP"]["CHW_Setpoint"] == 9.0
    r.tick(60)
    ctl.tick(60)
    run = [t["chiller"] for t in r.store._plant_trains_run[DC]]
    lead = run[0]
    assert _wire(r, lead, "CHW_Setpoint") == 9.0                       # not 7.0
    pen = r.store._chw_pen.get(DC, 0.0)
    assert r.store._chw_supply_c[DC] == pytest.approx(9.0 + pen, abs=0.05)
    assert _wire(r, lead, "CHW_Supply_Temp") == pytest.approx(9.0 + pen, abs=0.15)
    assert "Alarm_HighCHWSupply" not in r.store._plant_auto_points.get(lead, {})
    # Less lift: ~2.5 %/K cheaper at the same load, so COP rises.
    kw_r, kw_c = r.store._plant_power_by_name[lead], ctl.store._plant_power_by_name[lead]
    assert kw_r / kw_c == pytest.approx(1.0 - 0.025 * 2.0, rel=0.01)
    assert r.store._plant_cop_by_name[lead] > ctl.store._plant_cop_by_name[lead]
    assert len(r.store.faults.ledger.query(kind="setpoint")["entries"]) == 3
    # Clearing returns every chiller to design.
    _post(ch, "CHW_Setpoint", value=None)
    r.tick(5)
    assert r.store._chw_sp(DC) == 7.0


def test_tcs_setpoint_ramp_drives_the_coolant(pair):
    _ctl, r = pair
    cdu = r.h.made["CDU1"]
    _post(cdu, "TCS_Setpoint", ramp={"rate_per_s": 0.1, "target": 24.0},
          campaign="cdu-condensation/t")
    r.tick(100)
    assert _wire(r, cdu.name, "TCS_Setpoint") == 24.0
    assert _wire(r, cdu.name, "TCS_Supply_Temp") <= 24.0 + 1.0
    assert r.store.faults.ledger.query(kind="setpoint")["entries"][0]["campaign"] == \
        "cdu-condensation/t"


def test_a_setpoint_outside_its_band_is_422(pair):
    _ctl, r = pair
    for dev, pt, v in ((r.h.made["CRAH1"], "Setpoint", 40.0),
                       (r.h.made["CHL1"], "CHW_Setpoint", 1.0),
                       (r.h.made["CDU1"], "TCS_Setpoint", 60.0)):
        with pytest.raises(HTTPException) as ei:
            _post(dev, pt, value=v)
        assert ei.value.status_code == 422

"""S0-5 (doc 32 §4.2): FaultBook / SensorFault / DeviceView — the pure maths.

Transform order: stuck first (held), then truth + bias + drift·h + (noise−1)·σ·N,
then the instrument span clamp, then dropout gating. Faults do not stack.
"""
from __future__ import annotations

import statistics
from types import SimpleNamespace

import pytest

from core import fault_ledger as fl
from core.fault_injection import (DeviceView, FaultBook, FaultConflict, SensorFault,
                                  point_span)


class _Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _book(tmp_path, clock=None):
    return FaultBook(ledger=fl.FaultLedger(tmp_path / "l.jsonl"), clock=clock or _Clock())


def _f(point="Supply_Air_Temp", surface="bacnet", **kw):
    kw.setdefault("start_ts", 1_700_000_000.0)
    return SensorFault(id=kw.pop("id", fl.new_fault_id(kw["start_ts"])), device="CRAH1",
                       device_id="abc", point=point, surface=surface, **kw)


def test_bias_and_drift(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    fb.add_sensor(_f(bias=3.0, drift_per_h=0.5))
    assert fb.wire_value("CRAH1", "Supply_Air_Temp", 22.0) == pytest.approx(25.0)
    clk.t += 7200
    assert fb.wire_value("CRAH1", "Supply_Air_Temp", 22.0) == pytest.approx(26.0)
    # Other points and other devices pass through untouched.
    assert fb.wire_value("CRAH1", "Return_Air_Temp", 30.0) == 30.0
    assert fb.wire_value("CRAH2", "Supply_Air_Temp", 22.0) == 22.0


def test_noise_scales_sigma_and_is_seeded(tmp_path):
    fb = _book(tmp_path)
    fb.add_sensor(_f(id="flt_20231114T221320_aaaaaa", noise_mult=3.0, sigma=0.2))
    xs = [fb.wire_value("CRAH1", "Supply_Air_Temp", 22.0) for _ in range(4000)]
    assert statistics.mean(xs) == pytest.approx(22.0, abs=0.03)
    assert statistics.pstdev(xs) == pytest.approx(0.4, rel=0.06)     # (3-1)·0.2
    fb2 = _book(tmp_path / "b")
    fb2.add_sensor(_f(id="flt_20231114T221320_aaaaaa", noise_mult=3.0, sigma=0.2))
    ys = [fb2.wire_value("CRAH1", "Supply_Air_Temp", 22.0) for _ in range(50)]
    assert ys == xs[:50]                    # same id + seed -> same noise sequence


def test_stuck_holds_activation_value(tmp_path):
    fb = _book(tmp_path)
    fb.add_sensor(_f(stuck=True, bias=5.0))
    assert fb.wire_value("CRAH1", "Supply_Air_Temp", 22.3) == 22.3
    assert fb.wire_value("CRAH1", "Supply_Air_Temp", 30.0) == 22.3    # stuck wins over bias


def test_stuck_value_on_a_binary(tmp_path):
    """PU-3's proof-lost case: the proof switch reads 0 while the pump runs."""
    fb = _book(tmp_path)
    fb.add_sensor(_f(point="Run_Status", stuck_value=0.0))
    assert fb.wire_value("CRAH1", "Run_Status", 1.0) == 0.0


def test_span_clamp():
    assert point_span("Supply_Air_Temp") == (-40.0, 125.0)
    assert point_span("humidity") == (0.0, 100.0)
    assert point_span("CHW_Valve") == (0.0, 100.0)


def test_drift_cannot_leave_the_transmitter_range(tmp_path):
    fb = _book(tmp_path)
    fb.add_sensor(_f(point="humidity", surface="attr", bias=80.0))
    assert fb.wire_value("CRAH1", "humidity", 45.0) == 100.0


def test_dropout_windows_are_whole_and_seeded(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    fb.add_sensor(_f(id="flt_20231114T221320_bbbbbb", dropout_pct=50.0, dropout_window_s=30.0))
    states = []
    for _ in range(3000):
        states.append(fb.wire_value("CRAH1", "Supply_Air_Temp", 22.0) is None)
        clk.t += 1.0
    # Each 30 s window is all-dropped or all-present: a per-tick coin flip
    # would be invisible at 30-120 s polling.
    for w in range(0, 3000, 30):
        assert len(set(states[w:w + 30])) == 1
    frac = sum(states) / len(states)
    assert 0.35 < frac < 0.65


def test_faults_do_not_stack(tmp_path):
    fb = _book(tmp_path)
    fb.add_sensor(_f(bias=1.0))
    with pytest.raises(FaultConflict):
        fb.add_sensor(_f(bias=2.0))


def test_expiry_closes_the_ledger_at_expiry_time(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    f = fb.add_sensor(_f(bias=1.0, duration_s=600), ledger_entry={"source": "t"})
    clk.t += 599
    assert fb.expire() == []
    clk.t += 5
    out = fb.expire()
    assert out and out[0]["end_reason"] == "expired"
    assert fb.ledger.get(f.id)["end"] == fl.utc_iso(1_700_000_600.0)
    assert fb.wire_value("CRAH1", "Supply_Air_Temp", 22.0) == 22.0


def test_remove_closes_ledger(tmp_path):
    fb = _book(tmp_path)
    f = fb.add_sensor(_f(bias=1.0), ledger_entry={"source": "t"})
    e = fb.remove(f.id, "deleted")
    assert e["end_reason"] == "deleted"
    assert fb.remove(f.id, "deleted") is None


def test_apply_bacnet_is_a_copy(tmp_path):
    fb = _book(tmp_path)
    fb.add_sensor(_f(bias=3.0))
    truth = {"Supply_Air_Temp": 22.0, "Return_Air_Temp": 30.0}
    wire = fb.apply_bacnet("CRAH1", truth)
    assert truth == {"Supply_Air_Temp": 22.0, "Return_Air_Temp": 30.0}
    assert wire["Supply_Air_Temp"] == pytest.approx(25.0)
    assert fb.apply_bacnet("CRAH2", truth) is truth       # zero cost when unfaulted


def test_dropout_holds_last_wire_on_bacnet(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    f = fb.add_sensor(_f(id="flt_20231114T221320_cccccc", bias=1.0, dropout_pct=100.0))
    f._win, f._win_drop = 0, False          # first window forced present
    assert fb.apply_bacnet("CRAH1", {"Supply_Air_Temp": 22.0})["Supply_Air_Temp"] == 23.0
    clk.t += 31                             # next window: dropped (pct 100)
    assert fb.dropped("CRAH1") == {"Supply_Air_Temp": True}
    assert fb.apply_bacnet("CRAH1", {"Supply_Air_Temp": 26.0})["Supply_Air_Temp"] == 23.0


def test_device_view_reads_wire_and_never_writes_truth(tmp_path):
    fb = _book(tmp_path)
    dev = SimpleNamespace(name="SRV1", inlet_temp=24.0, cpu_temp=50.0, ip_address="10.0.0.1")
    fb.add_sensor(SensorFault(id="flt_20231114T221320_dddddd", device="SRV1", device_id="x",
                              point="inlet_temp", bias=2.0, surface="attr",
                              start_ts=1_700_000_000.0))
    fb.step([dev], {})
    v = fb.view(dev)
    assert isinstance(v, DeviceView)
    assert v.inlet_temp == pytest.approx(26.0)
    assert v.cpu_temp == 50.0 and v.ip_address == "10.0.0.1"
    v.inlet_temp = 99.0                     # a renderer may not write the lie back
    assert dev.inlet_temp == 24.0
    v.cpu_temp = 51.0                       # other writes delegate as before
    assert dev.cpu_temp == 51.0
    assert fb.view(SimpleNamespace(name="SRV2")).name == "SRV2"     # unfaulted: itself


def test_step_makes_renderers_agree(tmp_path):
    """SNMP and Redfish reading one instrument in one tick must agree."""
    fb = _book(tmp_path)
    dev = SimpleNamespace(name="SRV1", inlet_temp=24.0)
    fb.add_sensor(SensorFault(id="flt_20231114T221320_eeeeee", device="SRV1", device_id="x",
                              point="inlet_temp", noise_mult=5.0, sigma=0.2, surface="attr",
                              start_ts=1_700_000_000.0))
    fb.step([dev], {})
    assert fb.view(dev).inlet_temp == fb.view(dev).inlet_temp


def test_apply_ext_and_validity(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    fb.add_sensor(SensorFault(id="flt_20231114T221320_ffffff", device="UPS1", device_id="u",
                              point="battery_temp_c", bias=4.0, surface="ext",
                              start_ts=clk.t))
    ext = {"battery_temp_c": 25.0, "ups_status": "normal"}
    fb.step([], {"UPS1": ext})
    w = fb.apply_ext("UPS1", ext)
    assert w["battery_temp_c"] == pytest.approx(29.0) and ext["battery_temp_c"] == 25.0
    assert "__invalid__" not in w


def test_attr_fault_reaches_the_probe_port_keys(tmp_path):
    """A DPX2 probe is rendered by its host PDU from probe_* ext keys."""
    fb = _book(tmp_path)
    probe = SimpleNamespace(name="SNS1", inlet_temp=21.0)
    fb.add_sensor(SensorFault(id="flt_20231114T221320_111111", device="SNS1", device_id="s",
                              point="inlet_temp", bias=10.0, surface="attr",
                              start_ts=1_700_000_000.0))
    ext = {"probe_inlet_c": 21.0, "probe_humidity_pct": 40.0}
    fb.step([probe], {"SNS1": ext})
    w = fb.apply_ext("SNS1", ext)
    assert w["probe_inlet_c"] == pytest.approx(31.0)
    assert w["probe_humidity_pct"] == 40.0


def test_control_bias(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    fb.add_sensor(_f(bias=3.0, drift_per_h=1.0, affects_control=True))
    assert fb.control_bias("CRAH1", "Supply_Air_Temp") == pytest.approx(3.0)
    clk.t += 3600
    assert fb.control_bias("CRAH1", "Supply_Air_Temp") == pytest.approx(4.0)
    assert fb.control_bias("CRAH1", "Return_Air_Temp") == 0.0
    fb2 = _book(tmp_path / "b")
    fb2.add_sensor(_f(bias=3.0))                      # monitoring only
    assert fb2.control_bias("CRAH1", "Supply_Air_Temp") == 0.0


def test_persist_and_resume(tmp_path):
    clk = _Clock()
    fb = _book(tmp_path, clk)
    a = fb.add_sensor(_f(bias=3.0), ledger_entry={"source": "t"})
    b = fb.add_sensor(_f(point="Return_Air_Temp", bias=1.0, duration_s=60),
                      ledger_entry={"source": "t"})
    recs = fb.to_json()
    clk.t += 600                              # down for 10 minutes
    fb2 = FaultBook(ledger=fl.FaultLedger(tmp_path / "l.jsonl"), clock=clk)
    got = fb2.resume(recs, saved_at=1_700_000_000.0)
    assert got == {"resumed": [a.id], "expired": [b.id]}
    assert fb2.wire_value("CRAH1", "Supply_Air_Temp", 22.0) == pytest.approx(25.0)
    led = fb2.ledger
    assert led.get(a.id)["resumes"][0]["gap"][0] == fl.utc_iso(1_700_000_000.0)
    assert led.get(b.id)["end"] == fl.utc_iso(1_700_000_060.0)


def test_seed_follows_the_global_seed(monkeypatch):
    from core import fault_injection as fi
    monkeypatch.setattr(fi, "GLOBAL_SEED", 42)
    a = SensorFault(id="flt_x", device="d", device_id="i", point="p")
    monkeypatch.setattr(fi, "GLOBAL_SEED", 43)
    b = SensorFault(id="flt_x", device="d", device_id="i", point="p")
    assert a.seed != b.seed

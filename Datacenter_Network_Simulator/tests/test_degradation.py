"""S1-1 … S1-6 (doc 32 §5, §14): degradations change the MACHINE.

A sensor fault lies on the wire and leaves physics alone; a degradation is the
opposite. Each test drives the real store tick with a real BACnet plane (the
rig from test_fault_publish_truth) and checks the signature the real equipment
shows, against a control run with the same seed where that matters:

* ``cop``            — kW up at the same load, published COP down by the same
                       factor, and Q = COP × kW still the delivered heat;
* ``hx_approach``    — chiller: condenser approach and head pressure up, draw up
                       ~2.7 %/K; CDU: approach up, facility valve opens further;
* ``tower_approach`` — condenser water further above wet bulb, fans faster;
* ``filter_loading`` — CRAH: Filter_DP (Pa) rises, the fan speeds up to hold
                       airflow, delivered stays 1.0 until it saturates, and
                       Filter_Dirty = 1 exactly at L ≥ 1; CDU: strainer dP, pump
                       speed, Alarm_LowFlow at L ≥ 1;
* ``pump_derate``    — the pump speeds up to hold its dP, and draws more.

Plus persistence/resume, the API refusals, stop-without-restore and restore.
"""
from __future__ import annotations

import json
import random

import pytest

from conftest import DC, build_plant
from test_fault_publish_truth import (SEED, Rig, _attach_bacnet, _clear_caches, _lay_out,
                                      api_state)  # noqa: F401  (fixture)

SETTLE = 60

_DICT_CACHES = ("_plant_state_cache", "_plant_wire_cache", "_ext_state_cache")
_SET_CACHES = ("_unpowered_cache", "_lifecycle_offline_cache", "_liquid_server_cache")


class IsoRig(Rig):
    """A rig that owns its share of the store module's process-wide caches and
    of the global random stream. Two rigs in one test (a control and a faulted
    twin built from the same seed) can then be ticked INTERLEAVED and still be
    exact twins: without this the second rig reads the first one's published
    plant state, and they draw different numbers from one random stream."""

    def _swap_in(self):
        from core import device_state_store as dss
        from core import fault_injection
        for n in _DICT_CACHES:
            g = getattr(dss, n)
            g.clear()
            g.update(self.caches[n])
        for n in _SET_CACHES:
            setattr(dss, n, set(self.caches[n]))
        random.setstate(self.rstate)
        fault_injection.set_active(self.store.faults)

    def _swap_out(self):
        from core import device_state_store as dss
        for n in _DICT_CACHES:
            self.caches[n] = {k: dict(v) if isinstance(v, dict) else v
                              for k, v in getattr(dss, n).items()}
        for n in _SET_CACHES:
            self.caches[n] = set(getattr(dss, n))
        self.rstate = random.getstate()

    def tick(self, n=1):
        self._swap_in()
        try:
            for _ in range(n):
                self.store._tick()
        finally:
            self._swap_out()

    def call(self, fn, *a, **kw):
        """Run *fn* with this rig's caches in place (reads that hit them)."""
        self._swap_in()
        try:
            return fn(*a, **kw)
        finally:
            self._swap_out()


def _rig(tmp_path, n_crah=4, cdus=1, servers=48):
    _clear_caches()
    random.seed(SEED)
    h = build_plant(tmp_path, crahs=n_crah, servers=servers, cdus=cdus)
    _lay_out(h, n_crah)
    h.store._air_mode = "uniform"
    ctrl, bac = _attach_bacnet(h, tmp_path)
    r = IsoRig(h=h, store=h.store, ctrl=ctrl, bac=bac,
               caches={n: {} for n in _DICT_CACHES})
    r.caches.update({n: set() for n in _SET_CACHES})
    r.rstate = random.getstate()
    return r


def _wire(r, name, point):
    return r.bac[name].get_snapshot()[point]


def _lead_chiller(r):
    run = [t["chiller"] for t in r.store._plant_trains_run[DC] if t.get("chiller")]
    assert run, "no chiller staged on"
    return run[0]


def _degrade(r, dev, kind, target, rate_per_h=3600.0, campaign=None):
    from api.routers.faults import DegradationBody, add_degradation
    return add_degradation(DegradationBody(device=dev.id, kind=kind, target=target,
                                           rate_per_h=rate_per_h, campaign=campaign))


@pytest.fixture
def pair(tmp_path, api_state):
    """A control rig and a rig to degrade, same seed, both settled."""
    ctl = _rig(tmp_path / "ctl")
    ctl.tick(SETTLE)
    deg = _rig(tmp_path / "deg")
    api_state(deg)
    deg.tick(SETTLE)
    return ctl, deg


# ── cop (S1-2) ───────────────────────────────────────────────────────────────

def test_cop_decay_raises_kw_and_lowers_cop_and_q_still_balances(pair):
    ctl, deg = pair
    ch = _lead_chiller(deg)
    out = _degrade(deg, deg.h.name(ch), "cop", 0.80, rate_per_h=72.0)   # 10 s to target
    assert out["kind"] == "degradation" and out["point"] is None
    assert out["expected_kind"] == "process" and out["eta_target"]
    ctl.tick(150)
    deg.tick(150)
    s_c, s_d = ctl.store, deg.store
    assert s_d.faults.machine_value(ch, "cop") == pytest.approx(0.80)
    # At constant load the machine draws 1/0.8 as much and its COP falls 20 %.
    kw_c, kw_d = s_c._plant_power_by_name[ch], s_d._plant_power_by_name[ch]
    cop_c, cop_d = s_c._plant_cop_by_name[ch], s_d._plant_cop_by_name[ch]
    assert kw_d / kw_c == pytest.approx(1.25, rel=0.02)
    assert cop_d / cop_c == pytest.approx(0.80, rel=0.02)
    # The identity: the heat it delivers is unchanged (part load, no FLA cap).
    assert cop_d * kw_d == pytest.approx(cop_c * kw_c, rel=0.02)
    # And on the wire, after the engine's EMA has settled.
    w_cop, w_kw = _wire(deg, ch, "COP"), _wire(deg, ch, "Active_Power")
    assert w_cop / _wire(ctl, ch, "COP") == pytest.approx(0.80, rel=0.03)
    assert w_cop * w_kw == pytest.approx(_wire(ctl, ch, "COP") * _wire(ctl, ch, "Active_Power"),
                                         rel=0.03)
    # Target reached is a mark; the entry stays open (a worn machine stays worn).
    e = s_d.faults.ledger.get(out["id"])
    assert e["end"] is None and any(m["what"] == "target_reached" for m in e["marks"])


def test_cop_beyond_fla_books_capacity_loss(tmp_path, api_state):
    """At its FLA limit a worn chiller cannot make the tonnage: the unmet share
    is lost capacity, and COP x kW is what it DOES deliver."""
    from core.cooling_model import chiller_draw_w
    w, unmet = chiller_draw_w(40_000, 1.0, "chicago", efficiency=0.6)
    assert w == 40_000 and unmet > 0.0
    w0, unmet0 = chiller_draw_w(40_000, 0.5, "chicago")
    from core.cooling_model import chiller_electrical_w
    assert unmet0 == 0.0 and w0 == pytest.approx(chiller_electrical_w(40_000, 0.5, "chicago"))


# ── hx_approach, chiller condenser (S1-2) ────────────────────────────────────

def test_condenser_fouling_raises_approach_head_and_kw(pair):
    ctl, deg = pair
    ch = _lead_chiller(deg)
    app0 = _wire(deg, ch, "Cond_Approach_Temp")
    assert 0.9 <= app0 <= 2.1                       # design 2 K, load-scaled
    ev0 = _wire(deg, ch, "Evap_Approach_Temp")
    _degrade(deg, deg.h.name(ch), "hx_approach", 4.0, rate_per_h=3600.0)
    ctl.tick(60)
    deg.tick(60)
    assert _wire(deg, ch, "Cond_Approach_Temp") == pytest.approx(
        _wire(ctl, ch, "Cond_Approach_Temp") + 4.0, abs=0.15)
    assert _wire(deg, ch, "Evap_Approach_Temp") == pytest.approx(ev0, abs=0.15)
    assert deg.store._plant_auto_points[ch]["Cond_Pressure"] == pytest.approx(
        ctl.store._plant_auto_points[ch]["Cond_Pressure"] + 104.0, abs=1.0)
    kw_c, kw_d = ctl.store._plant_power_by_name[ch], deg.store._plant_power_by_name[ch]
    assert kw_d / kw_c == pytest.approx(1.0 + 0.027 * 4.0, rel=0.02)


def test_new_points_exist_and_are_appended(tmp_path):
    """Appended LAST: no existing BACnet object instance moves."""
    from core.bacnet_plant_generator import PLANT_SPEC, build_plant_object_tree
    from core.bacnet_object_model import OBJ_ANALOG_INPUT, UNIT_PASCALS
    ch = [p[0] for p in PLANT_SPEC["chiller"]["ai"]]
    assert ch[-2:] == ["Cond_Approach_Temp", "Evap_Approach_Temp"]
    assert ch.index("Run_Hours") == 12
    crah = PLANT_SPEC["crah"]["ai"]
    assert crah[-1][0] == "Filter_DP" and crah[-1][1] == UNIT_PASCALS == 53
    _tree, n2k = build_plant_object_tree("crah", 11.0)
    assert n2k["Filter_DP"] == (OBJ_ANALOG_INPUT, 11)
    from core.fault_points import metric_hint
    assert metric_hint("chiller", "Cond_Approach_Temp") == {"metric": "approach_temp",
                                                            "instance": "COND"}
    assert metric_hint("chiller", "Evap_Approach_Temp") == {"metric": "approach_temp",
                                                            "instance": "EVAP"}
    assert metric_hint("crah", "Filter_DP") == {"metric": "filter_diff_pressure",
                                                "instance": ""}


# ── tower_approach (S1-3) ────────────────────────────────────────────────────

def test_tower_fill_fouling_lifts_condenser_water_and_fan_speed(pair):
    ctl, deg = pair
    s = deg.store
    towers = [deg.h.made[f"CT{i}"] for i in range(1, 4)]
    for t in towers:
        _degrade(deg, t, "tower_approach", 3.0, rate_per_h=3600.0)
    ctl.tick(80)
    deg.tick(80)
    assert s._cond_base_c[DC] == pytest.approx(ctl.store._cond_base_c[DC] + 3.0, abs=0.05)
    run = sorted(s._tower_running_now[DC])
    assert run
    ct = run[0]
    oa_wb = _wire(deg, ct, "OA_Wet_Bulb_Temp")
    app_d = _wire(deg, ct, "Cond_Water_Out") - oa_wb
    app_c = _wire(ctl, ct, "Cond_Water_Out") - _wire(ctl, ct, "OA_Wet_Bulb_Temp")
    assert app_d - app_c == pytest.approx(3.0, abs=0.3)
    assert s._plant_speed_by_name[ct] >= ctl.store._plant_speed_by_name[ct]
    # Warmer condenser water costs compressor lift.
    ch = _lead_chiller(deg)
    assert s._plant_power_by_name[ch] > ctl.store._plant_power_by_name[ch]


# ── hx_approach, CDU (S1-4) ─────────────────────────────────────────────────

def test_cdu_plate_fouling_raises_approach_and_opens_the_valve(pair):
    ctl, deg = pair
    cdu = deg.h.made["CDU1"]
    a0 = _wire(deg, cdu.name, "Approach_Temp")
    assert 0.0 < a0 < 4.0
    _degrade(deg, cdu, "hx_approach", 5.0)
    ctl.tick(30)
    deg.tick(30)
    assert _wire(deg, cdu.name, "Approach_Temp") == pytest.approx(
        _wire(ctl, cdu.name, "Approach_Temp") + 5.0, abs=0.2)
    assert (_wire(deg, cdu.name, "Facility_CHW_Valve")
            > _wire(ctl, cdu.name, "Facility_CHW_Valve"))


# ── filter_loading, CRAH (S1-5) ──────────────────────────────────────────────

def test_crah_filter_loading_dp_fan_delivered_and_the_switch(pair):
    ctl, deg = pair
    s = deg.store
    crah = deg.h.made["CRAH1"]
    dp0 = _wire(deg, crah.name, "Filter_DP")
    fan0 = s._plant_speed_by_name[crah.name]
    assert dp0 > 0.0 and _wire(deg, crah.name, "Filter_Dirty") == 0.0
    _degrade(deg, crah, "filter_loading", 1.2, rate_per_h=3.6)        # 0.1 per 100 s
    seen, last = [], None
    for _ in range(14):
        deg.tick(100)
        L = s.faults.machine_value(crah.name, "filter_loading")
        dp = s._plant_auto_points[crah.name]["Filter_DP"]
        seen.append((L, dp, _wire(deg, crah.name, "Filter_Dirty"),
                     s._crah_delivered_frac(crah.name)))
        if last is not None:
            assert dp >= last * 0.97                           # monotonic (1 % dither)
        last = dp
    for L, dp, dirty, frac in seen:
        assert (dirty == 1.0) is (L >= 1.0 - 1e-9), (L, dirty)
        assert frac == pytest.approx(1.0)        # fan has headroom: airflow held
    # dP at the same airflow follows 60 Pa x (1 + 3L) x q^2.
    q = s._filter_q[crah.name]
    assert seen[-1][1] == pytest.approx(60.0 * (1 + 3 * 1.2) * q * q, rel=0.03)
    assert s._plant_speed_by_name[crah.name] > fan0


def test_crah_filter_saturated_fan_loses_airflow(tmp_path):
    """Past 100 % fan the unit cannot hold airflow: delivered falls as 1/s_req."""
    h = build_plant(tmp_path, crahs=1)
    s = h.store
    name = h.made["CRAH1"].name
    s.faults._machine[(name, "filter_loading")] = 1.5
    spd, w = s._degraded_drive(name, "crah", 11_000.0, 0.95, 0.0)
    assert spd == 1.0 and w == 11_000.0
    R = 1.0 + 60.0 * 3 * 1.5 / 250.0
    assert s._crah_airflow_frac[name] == pytest.approx(1.0 / (0.95 * R ** 0.5))


# ── filter_loading, CDU strainer (S1-5) ─────────────────────────────────────

def test_cdu_strainer_loading(pair):
    ctl, deg = pair
    s = deg.store
    cdu = deg.h.made["CDU1"]
    _degrade(deg, cdu, "filter_loading", 1.0, rate_per_h=3600.0)
    ctl.tick(40)
    deg.tick(40)
    assert _wire(deg, cdu.name, "Filter_DP") == pytest.approx(
        _wire(ctl, cdu.name, "Filter_DP") * 2.5, rel=0.05)
    assert s._plant_speed_by_name[cdu.name] > ctl.store._plant_speed_by_name[cdu.name]
    assert s._plant_auto_points[cdu.name].get("Alarm_LowFlow") == 1.0


# ── pump_derate (S1-6) ───────────────────────────────────────────────────────

def test_worn_impeller_speeds_up_to_hold_dp(pair):
    ctl, deg = pair
    s = deg.store
    p = [t["chwp"] for t in s._plant_trains_run[DC] if t.get("chwp")][0]
    _degrade(deg, deg.h.name(p), "pump_derate", 0.85)
    ctl.tick(30)
    deg.tick(30)
    spd_c, spd_d = ctl.store._plant_speed_by_name[p], s._plant_speed_by_name[p]
    assert spd_d == pytest.approx(min(1.0, spd_c * ((1 / 0.85 + 0.25) / 1.25) ** 0.5), rel=0.01)
    assert s._plant_power_by_name[p] > ctl.store._plant_power_by_name[p]
    # The loop holds its differential pressure (on the similarity line).
    dp_c = ctl.store._plant_auto_points[p]["Diff_Pressure"]
    dp_d = s._plant_auto_points[p]["Diff_Pressure"]
    assert dp_d == pytest.approx(dp_c, rel=0.08)


# ── the API ──────────────────────────────────────────────────────────────────

def test_refusals(pair):
    from fastapi import HTTPException
    _ctl, deg = pair
    crah, ch = deg.h.made["CRAH1"], deg.h.made["CHL1"]
    bad = [(crah, "cop", 0.8, 1.0), (ch, "cop", 0.3, 1.0), (ch, "cop", 0.8, 0.0),
           (ch, "nope", 0.8, 1.0), (crah, "filter_loading", 2.0, 1.0)]
    for dev, kind, tgt, rate in bad:
        with pytest.raises(HTTPException) as ei:
            _degrade(deg, dev, kind, tgt, rate)
        assert ei.value.status_code == 422, (kind, tgt, rate)
    _degrade(deg, ch, "cop", 0.9)
    with pytest.raises(HTTPException) as ei:
        _degrade(deg, ch, "cop", 0.8)
    assert ei.value.status_code == 409


def test_stop_keeps_the_machine_degraded_and_restore_cleans_it(pair):
    from fastapi import HTTPException
    from api.routers.faults import delete_fault, list_faults
    _ctl, deg = pair
    s = deg.store
    crah = deg.h.made["CRAH1"]
    out = _degrade(deg, crah, "filter_loading", 1.0, rate_per_h=30.0)
    deg.tick(60)                                           # half way: L = 0.5
    L = s.faults.machine_value(crah.name, "filter_loading")
    assert L == pytest.approx(0.5, abs=0.01)
    closed = delete_fault(out["id"])
    assert closed["end_reason"] == "deleted"
    deg.tick(20)
    assert s.faults.machine_value(crah.name, "filter_loading") == pytest.approx(L)
    held = s.faults.held_id(crah.name, "filter_loading")
    assert held and s.faults.ledger.get(held)["end"] is None
    assert any(a["id"] == held for a in list_faults()["active"])
    with pytest.raises(HTTPException) as ei:
        delete_fault(held)
    assert ei.value.status_code == 409
    # A new ramp continues from where the coil is.
    out2 = _degrade(deg, crah, "filter_loading", 1.0, rate_per_h=1800.0)
    assert out2["params"]["from"] == pytest.approx(L)
    assert s.faults.ledger.get(held)["end_reason"] == "superseded"
    closed = delete_fault(out2["id"], restore=True)
    assert closed["end_reason"] == "restore"
    assert s.faults.machine_value(crah.name, "filter_loading") == 0.0
    maint = s.faults.ledger.query(kind="maintenance")["entries"]
    assert maint and maint[0]["params"]["degradation"] == "filter_loading"


def test_a_paused_simulator_does_not_foul(pair):
    _ctl, deg = pair
    ch = deg.h.made["CHL1"]
    _degrade(deg, ch, "cop", 0.8, rate_per_h=36.0)
    deg.store._dt = 0.0
    deg.tick(20)
    assert deg.store.faults.machine_value(ch.name, "cop") == 1.0
    deg.store._dt = 1.0
    deg.tick(10)
    assert deg.store.faults.machine_value(ch.name, "cop") == pytest.approx(0.9)


# ── persistence and resume (S1-1, §7.3) ─────────────────────────────────────

def test_degradation_persists_across_a_restart(tmp_path, api_state):
    r = _rig(tmp_path / "a")
    api_state(r)
    r.store._faults_booted = True
    r.tick(5)
    ch, crah = r.h.made["CHL1"], r.h.made["CRAH1"]
    out = _degrade(r, ch, "hx_approach", 4.0, rate_per_h=3.6)         # 0.1 K per 100 s
    r.tick(100)
    o2 = _degrade(r, crah, "filter_loading", 1.0, rate_per_h=3600.0)
    r.tick(10)
    from api.routers.faults import delete_fault
    delete_fault(o2["id"])                                   # held residual
    v = r.store.faults.machine_value(ch.name, "hx_approach")
    assert v == pytest.approx(0.11)
    r.store._save_faults()
    data = json.loads(r.store._faults_state_path().read_text(encoding="utf-8"))
    assert data["degradations"][0]["value"] == pytest.approx(v)

    # A new process: a fresh store on the same ledger boots and resumes.
    r2 = _rig(tmp_path / "b")
    r2.store._boot_faults()
    assert r2.store.faults.machine_value(ch.name, "hx_approach") == pytest.approx(v)
    assert r2.store.faults.machine_value(crah.name, "filter_loading") == pytest.approx(1.0)
    e = r2.store.faults.ledger.get(out["id"])
    assert e["end"] is None and e["resumes"], "a resume event is ledgered"
    held = r2.store.faults.held_id(crah.name, "filter_loading")
    assert held and r2.store.faults.ledger.get(held)["end"] is None
    r2.tick(100)                                               # and it keeps fouling
    assert r2.store.faults.machine_value(ch.name, "hx_approach") > v

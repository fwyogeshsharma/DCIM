"""Published chiller COP must reconcile with the loop it serves.

COP is the one plant number a reader will instinctively multiply by something else:
COP x draw is the cooling the machine claims to be making. If that product does not
equal the heat on the evaporator header, the panel is telling two different stories
about the same plant, and both look plausible.

It did, for a while. COP came off the part-load curve at the raw PLR, and the draw
beside it was then rescaled by the per-DC normalisation that makes the plant sum
match the staged demand. One normalised number, one not: a header carrying 113 kW
was reported by a chiller claiming COP 4.08 on a 50.27 kW draw, which is 205 kW of
cooling. Nothing alarmed, because nothing checks.
"""
import pathlib
import tempfile

import pytest

from conftest import DC, build_plant

CP_WATER = 4.186


def _plant(servers=200, ticks=12, fault_at=None, fault=None):
    p = build_plant(pathlib.Path(tempfile.mkdtemp()), servers=servers,
                    installed_modules=6, probes=True)
    for i in range(ticks):
        if fault is not None and i == fault_at:
            fault(p)
        p.tick()
        for d in p.dm.get_all_devices():
            p.store._step_device(d)
            p.store._step_ext_state(d)
    return p


def _loop_kw(store, dc=DC):
    """Heat on the chilled-water header, from the published flow and ΔT."""
    return store._chw_flow_lps.get(dc, 0.0) * CP_WATER * store._chw_dt_c.get(dc, 0.0)


def _claimed_kw(store):
    """Cooling the chillers claim: Σ COP x published draw."""
    return sum(cop * store._plant_power_by_name.get(name, 0.0)
               for name, cop in store._plant_cop_by_name.items())


def test_cop_reconciles_with_the_header_at_light_load():
    s = _plant().store
    loop, claimed = _loop_kw(s), _claimed_kw(s)
    assert loop > 0
    assert claimed == pytest.approx(loop, rel=0.02), (
        f"chillers claim {claimed:.1f} kW of cooling on a {loop:.1f} kW header")


def test_cop_reconciles_with_the_header_with_several_chillers_running():
    """The share is per-machine, so the reconciliation has to survive more than one
    of them running — an error that cancels at N=1 would hide here."""
    s = _plant(servers=2000).store
    running = {n: c for n, c in s._plant_cop_by_name.items() if c > 0}
    assert len(running) > 1, "fixture did not stage up — test proves nothing"
    assert _claimed_kw(s) == pytest.approx(_loop_kw(s), rel=0.02)


def test_identical_chillers_on_one_header_report_identical_cop():
    """Sequenced to equal part-load, so equal machines carry equal duty. A share
    split any other way (per-unit draw, round-robin) breaks this."""
    s = _plant(servers=2000).store
    cops = sorted(c for c in s._plant_cop_by_name.values() if c > 0)
    assert len(cops) > 1
    assert cops[-1] == pytest.approx(cops[0], rel=0.01)


def test_cop_rises_with_load():
    """Direction check. At low load the fixed overheads dominate and the plant is
    genuinely inefficient; a COP that did not move with load would mean it is still
    coming from somewhere other than the live plant."""
    light = max(c for c in _plant().store._plant_cop_by_name.values())
    heavy = max(c for c in _plant(servers=2000).store._plant_cop_by_name.values())
    assert heavy > light


def test_a_staged_off_chiller_reports_zero_not_a_curve_value():
    s = _plant().store
    idle = [n for n in s._plant_cop_by_name if n in s._plant_standby_names]
    assert idle, "fixture staged everything on — test proves nothing"
    for n in idle:
        assert s._plant_cop_by_name[n] == 0.0


def test_a_faulted_chiller_reports_zero_and_its_load_moves_to_the_survivor():
    """The reading that used to hide a plant failure: a machine shed by its own
    protection kept drawing power AND kept a healthy COP. Delivering nothing has to
    read as nothing, and the header's heat has to reappear on whatever is left."""
    import core.device_state_store as store_mod

    def fault(p):
        lead = sorted(n for n, c in p.store._plant_cop_by_name.items() if c > 0)[0]
        store_mod._plant_state_cache[lead] = {"Alarm_HighPressure": 1.0}
        fault.lead = lead

    s = _plant(fault_at=6, fault=fault).store
    assert s._plant_cop_by_name[fault.lead] == 0.0
    assert _claimed_kw(s) == pytest.approx(_loop_kw(s), rel=0.02)


# ── The catalogued-SKU path ──────────────────────────────────────────────────
#
# build_plant's chillers are "chiller-1000t", which is in NEITHER catalog:
# cooling_capacity_w and nameplate_power_w both return 0 for it. So the fixture
# exercises the FALLBACK branch throughout — equal shares, and _run_cap_w == 0 sends
# plr down its own fallback too. The estate's real SKU (Carrier 19DV 800kW) is
# catalogued, so production takes the capacity-weighted branch instead.
#
# Both branches therefore need covering, or the suite proves the wrong one.
CATALOG_SKU = "Carrier 19DV 800kW"
CATALOG_CAP_KW = 800.0


def _plant_with_catalogued_chillers(servers=2000, ticks=12):
    from core.device_manager import DeviceType

    p = build_plant(pathlib.Path(tempfile.mkdtemp()), servers=servers,
                    installed_modules=6, probes=True)
    for d in p.dm.get_all_devices():
        if d.device_type == DeviceType.CHILLER:
            d.model_name = CATALOG_SKU
    for _ in range(ticks):
        p.tick()
        for d in p.dm.get_all_devices():
            p.store._step_device(d)
            p.store._step_ext_state(d)
    return p


def test_cop_reconciles_on_the_capacity_weighted_path():
    s = _plant_with_catalogued_chillers().store
    assert _claimed_kw(s) == pytest.approx(_loop_kw(s), rel=0.02)


def test_the_claim_never_exceeds_running_capacity():
    """The evaporator load is a DEMAND figure and does not shrink when a machine
    drops out. Allocating it uncapped would hand the survivor the whole load and
    RAISE its COP — a failure that reads as an efficiency gain."""
    from core.device_manager import DeviceType

    p = _plant_with_catalogued_chillers()
    s = p.store
    running = [d.name for d in p.dm.get_all_devices()
               if d.device_type == DeviceType.CHILLER
               and d.name not in s._plant_standby_names]
    assert running, "fixture staged everything off — test proves nothing"
    assert _claimed_kw(s) <= len(running) * CATALOG_CAP_KW * 1.02

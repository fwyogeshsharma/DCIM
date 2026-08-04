"""The fault-injection campaign, on the fixture path.

The live campaign (scratchpad `campaign.py` + `evidence.py`) sweeps 30 single
faults and 6 exhaustion scenarios against the running simulator through the API.
It costs about a day of wall clock, because `store._dt` is MEASURED elapsed time
and there is no acceleration knob — simulated time is real time by construction,
and the dt-invariance the model guarantees means ticking faster buys nothing. Held
for 240 s each and run at two loads, that is ~2.5 h of pure `time.sleep` per load
before the evidence pass doubles it.

The same scenarios run here in seconds, because `PlantFixture.tick()` leaves `_dt`
pinned at the configured interval: one tick is one simulated second at no
wall-clock cost.

WHAT THIS ASSERTS, AND WHY IT IS NOT THE CAMPAIGN'S NUMBERS. The campaign RECORDED
values; it had no expectations to fail against, which is why every one of its
findings needed a human to read a table. Pinning those numbers here would freeze
today's arithmetic — including the parts still wrong — so this asserts INVARIANTS
instead:

  * the injection actually took effect (the store can see the fault). The live
    campaign's own high-load run silently violated this: two scenarios reported
    `clean_baseline: false` and one was 31/31 fields IDENTICAL pre vs during, so
    it measured nothing at all and the report presented it as a result.
  * no fault drives the model outside its physical envelope
  * releasing the fault returns the plant to setpoint, except where a latching
    safety is supposed to hold it

WHAT DOES NOT PORT, and still needs the live probe: anything downstream of the
API — the EV2 metered branch behind `max(metered, model)` in get_power_summary
(this fixture has no metering), SNMP trap dispatch, and the endpoint wiring. That
is the half F9 hid in, so `live_probe.py` stays the tool for it.
"""
import math
import random

import pytest

from conftest import DC, build_plant

SEED = 20260804
TRAINS = 3
SERVERS = 60
MODULES = 6
CRAHS = 4
CDUS = 2

SETTLE_TICKS = 6
HOLD_TICKS = 60
RELEASE_TICKS = 40
STEP_EVERY = 10          # run the per-device pass every Nth tick, not every tick

CRAH1 = f"CRAH1-{DC}-HA-R1-01"

# Running-status point by device class, read off the leading name code — the same
# role-in-the-prefix idiom the store uses for header probes and valves.
_RUN_PT = {"CHL": "Chiller_Running", "CT": "Fan_Status", "CHWP": "Run_Status",
           "CWP": "Run_Status", "VCHW": "Status_Modulating",
           "VCW": "Status_Modulating", "CRAH": "Unit_Running", "CDU": "Unit_Running"}


def _running_point(dev):
    return _RUN_PT[dev.split("-")[0].rstrip("0123456789")]


# ── The sweep: one fault at a time, exactly the campaign's matrix ─────────────
# (device, faulted point or None for a silent stop, value, counts-as-loss, label).
#
# `counts` records whether the store is SUPPOSED to read this as a cooling loss.
# Three of the thirty deliberately do not, and encoding that here is the point:
#   * Alarm_HighCHWSupply and Alarm_HighReturnAir are _CAPACITY_ALARMS — they
#     announce a shortfall the thermal model has already booked, so counting one
#     as a fault would report a healthy-but-outmatched plant as broken.
#   * Filter_Dirty is not an Alarm_ point at all; a dirty filter is a maintenance
#     signal, not a loss of cooling.
# Asserting `_is_faulted` blindly across the matrix would have quietly demanded
# the opposite of all three.
PLANT_FAULTS = [
    (f"CHL1-{DC}-CP", "Alarm_HighPressure", 1.0, True, "high head pressure"),
    (f"CHL1-{DC}-CP", "Alarm_LowEvapTemp", 1.0, True, "low evaporator temp"),
    (f"CHL1-{DC}-CP", "Alarm_FlowLoss", 1.0, True, "evaporator flow loss"),
    (f"CHL1-{DC}-CP", "Alarm_HighCHWSupply", 1.0, False, "CHW off setpoint"),
    (f"CHL1-{DC}-CP", None, 0.0, True, "compressor trip (silent)"),
    (f"CT1-{DC}-RF", "Alarm_HighVibration", 1.0, True, "high fan vibration"),
    (f"CT1-{DC}-RF", "Alarm_LowBasin", 1.0, True, "low basin level"),
    (f"CT1-{DC}-RF", None, 0.0, True, "cell fan stop"),
    (f"CHWP1-{DC}-CP", "Alarm_Fault", 1.0, True, "CHW pump fault"),
    (f"CHWP1-{DC}-CP", "Alarm_LowFlow", 1.0, True, "CHW pump low flow"),
    (f"CHWP1-{DC}-CP", None, 0.0, True, "CHW pump stop"),
    (f"CWP1-{DC}-CP", "Alarm_Fault", 1.0, True, "CW pump fault"),
    (f"CWP1-{DC}-CP", "Alarm_LowFlow", 1.0, True, "CW pump low flow"),
    (f"CWP1-{DC}-CP", None, 0.0, True, "CW pump stop"),
    (f"VCHW-{DC}-CP", "Alarm_ActuatorFault", 1.0, True, "CHW valve stuck"),
    (f"VCHW-{DC}-CP", None, 0.0, True, "CHW valve stops modulating"),
    (f"VCW-{DC}-CP", "Alarm_ActuatorFault", 1.0, True, "CW valve stuck"),
    (f"VCW-{DC}-CP", None, 0.0, True, "CW valve stops modulating"),
    (CRAH1, "Alarm_HighTemp", 1.0, True, "CRAH high discharge"),
    (CRAH1, "Alarm_AirflowLoss", 1.0, True, "CRAH airflow loss"),
    (CRAH1, "Filter_Dirty", 1.0, False, "CRAH filter clogged"),
    (CRAH1, "Alarm_HighReturnAir", 1.0, False, "CRAH high return air"),
    (CRAH1, None, 0.0, True, "CRAH unit trip"),
]

# Header instruments told to lie. A failed element must not be able to drag the
# MODEL off its own physics — the loop is computed, and these read it.
PROBE_FAULTS = [
    (f"CHWS-{DC}-CP", "inlet_temp", 18.0, "CHW supply reads high"),
    (f"CHWS-{DC}-CP", "inlet_temp", 0.0, "CHW supply reads zero"),
    (f"CHWR-{DC}-CP", "inlet_temp", 24.0, "CHW return reads high"),
    (f"CWS-{DC}-CP", "inlet_temp", 40.0, "CW supply reads high"),
    (f"CWR-{DC}-CP", "inlet_temp", 45.0, "CW return reads high"),
    (f"CTB-{DC}-CP", "inlet_temp", 40.0, "basin reads high"),
    (f"FLOW-{DC}-CP", "airflow", 0.0, "flow meter reads zero"),
]

# ── The exhaustion scenarios, verbatim from the evidence pass ─────────────────
EXHAUSTION = [
    ("all-chillers", [(f"CHL{i}-{DC}-CP", "Chiller_Running", 0.0) for i in (1, 2, 3)],
     "silent stop of every chiller"),
    ("all-chwp", [(f"CHWP{i}-{DC}-CP", "Alarm_Fault", 1.0) for i in (1, 2, 3, 4)],
     "every CHW pump faulted — flow must fall, interlock must shed"),
    ("all-towers", [(f"CT{i}-{DC}-RF", "Fan_Status", 0.0) for i in (1, 2, 3)],
     "tower bank stopped — condenser runaway, makeup must stop"),
    ("all-cwp", [(f"CWP{i}-{DC}-CP", "Alarm_Fault", 1.0) for i in (1, 2, 3)],
     "every condenser pump faulted — rejection lost at the water side"),
    ("hall-crahs", [(f"CRAH{i}-{DC}-HA-R1-01", "Unit_Running", 0.0)
                    for i in range(1, CRAHS + 1)],
     "hall air side gone — plant itself stays healthy"),
    ("lead-chiller-stop", [(f"CHL1-{DC}-CP", "Chiller_Running", 0.0)],
     "single silent stop — must fail over"),
]


# ── Harness ──────────────────────────────────────────────────────────────────

def _plant(tmp_path):
    random.seed(SEED)
    p = build_plant(tmp_path, trains=TRAINS, servers=SERVERS,
                    installed_modules=MODULES, crahs=CRAHS, cdus=CDUS,
                    probes=True, valves=True, rack_probes=3)
    _run(p, SETTLE_TICKS)
    return p


def _step_all(p):
    for d in p.dm.get_all_devices():
        p.store._step_device(d)


def _run(p, ticks):
    """Advance the plant. The per-device pass is deliberately NOT run every tick —
    it is the expensive half and nothing asserted here needs per-tick resolution
    on a server field."""
    for i in range(ticks):
        p.tick()
        if i % STEP_EVERY == 0:
            _step_all(p)
    _step_all(p)


def _envelope(p):
    """Every physical bound the model claims for itself, checked at once."""
    from core.cooling_model import (CHW_SETPOINT_C, CHW_SUPPLY_RISE_MAX_C,
                                    CHW_MAX_DT_C, COND_MAX_RANGE_C)
    s = p.store
    loss = s._cool_loss_frac.get(DC, 0.0)
    assert 0.0 <= loss <= 1.0, f"cooling loss fraction out of range: {loss}"
    assert 0.0 <= s._chw_pen.get(DC, 0.0) <= s._COOL_MAX
    sup = s._chw_supply_c[DC]
    assert CHW_SETPOINT_C - 0.1 <= sup <= CHW_SETPOINT_C + CHW_SUPPLY_RISE_MAX_C + 0.1, (
        f"chilled water outside its modelled band: {sup}")
    assert s._chw_flow_lps[DC] >= 0.0
    assert 0.0 <= s._chw_dt_c[DC] <= CHW_MAX_DT_C + 0.1
    assert 0.0 <= s._cond_range_c.get(DC, 0.0) <= COND_MAX_RANGE_C + 0.1
    assert s._chw_return_c[DC] >= s._chw_supply_c[DC] - 0.1, "return below supply"
    assert s._cond_water_c.get(DC, 0.0) <= s._COND_MAX_C + 0.1
    for d in p.dm.get_all_devices():
        if d.device_type.value == "server":
            assert 15.0 - 0.1 <= d.inlet_temp <= 45.0 + 0.1, (
                f"{d.name} inlet {d.inlet_temp} outside the modelled clamp")
            assert 20.0 <= d.cpu_temp <= 95.0 + 0.1, f"{d.name} die {d.cpu_temp}"
            assert not math.isnan(d.cpu_temp)


def _apply(cache, dev, point, value):
    """Fault a unit the way the live override channel does: still commanded on,
    with the faulted point set. `point is None` means the fault IS a silent stop,
    so the running-status point carries the value instead."""
    rp = _running_point(dev)
    if point is None or point == rp:
        cache[dev] = {rp: value}
    else:
        cache[dev] = {rp: 1.0, point: value}


# ── The sweep ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dev,point,value,counts,label", PLANT_FAULTS,
                         ids=[f"{d.split('-')[0]}:{lb}" for d, _p, _v, _c, lb in PLANT_FAULTS])
def test_single_plant_fault(tmp_path, plant_cache, dev, point, value, counts, label):
    """Each fault must be aimed at a real machine, must leave the model inside its
    envelope, and must let go when released."""
    p = _plant(tmp_path)
    _envelope(p)
    # The matrix names must match the topology. A typo would silently fault
    # nothing and the whole case would pass while testing air — which is exactly
    # how the live campaign shipped two scenarios that measured an unfaulted
    # plant.
    assert any(d.name == dev for d in p.dm.get_all_devices()), f"{dev} not in plant"

    _apply(plant_cache, dev, point, value)
    _run(p, HOLD_TICKS)

    assert p.store._is_faulted(dev) is counts, (
        f"{label}: store reads faulted={p.store._is_faulted(dev)}, expected {counts}")
    _envelope(p)

    plant_cache.clear()
    _run(p, RELEASE_TICKS)
    _envelope(p)
    assert not p.store._is_faulted(dev)


@pytest.mark.parametrize("dev,metric,value,label", PROBE_FAULTS,
                         ids=[lb for _d, _m, _v, lb in PROBE_FAULTS])
def test_a_lying_header_instrument_cannot_move_the_model(tmp_path, plant_cache,
                                                         dev, metric, value, label):
    """A header thermowell reads the loop; it does not drive it. A failed element
    must not be able to pull the computed loop off its own physics, or the plant
    would be steerable from a broken sensor."""
    p = _plant(tmp_path)
    before = (p.store._chw_supply_c[DC], p.store._cond_water_c.get(DC, 0.0),
              p.store._cool_loss_frac.get(DC, 0.0))

    d = p.name(dev)
    p.store.device_overrides.setdefault(d.id, {})[metric] = value
    _run(p, HOLD_TICKS)

    after = (p.store._chw_supply_c[DC], p.store._cond_water_c.get(DC, 0.0),
             p.store._cool_loss_frac.get(DC, 0.0))
    assert after[0] == pytest.approx(before[0], abs=0.5), f"{label} moved CHW supply"
    assert after[1] == pytest.approx(before[1], abs=1.0), f"{label} moved condenser"
    assert after[2] == pytest.approx(before[2], abs=0.01), f"{label} moved the loss"
    _envelope(p)


# ── The exhaustion scenarios ─────────────────────────────────────────────────

@pytest.mark.parametrize("name,points,why", EXHAUSTION,
                         ids=[n for n, _p, _w in EXHAUSTION])
def test_exhaustion_scenario_stays_inside_the_envelope(tmp_path, plant_cache,
                                                       name, points, why):
    """Whole redundant sets removed. Every one must land, stay physical, and —
    apart from the latching high-pressure cutout, which is supposed to hold — let
    the plant recover when released."""
    p = _plant(tmp_path)
    for dev, pt, val in points:
        _apply(plant_cache, dev, pt, val)
    _run(p, HOLD_TICKS)

    assert any(p.store._is_faulted(dev) for dev, _pt, _v in points), (
        f"{name}: nothing was actually faulted — {why}")
    _envelope(p)

    plant_cache.clear()
    p.store._chiller_hp_lockout.clear()      # a latched cutout needs a manual reset
    _run(p, RELEASE_TICKS * 3)
    _envelope(p)
    assert p.store._chw_pen.get(DC, 0.0) < 1.0, (
        f"{name}: plant did not return to setpoint after release")


def test_partial_pumping_widens_delta_t_in_proportion(tmp_path, plant_cache):
    """The MIDDLE of the pump-derived flow curve, which the exhaustion scenarios
    never reach — they only ever produce full pumping or none.

    Found by deliberately removing the ΔT clamp and watching this file stay green:
    with the default fixture `_chw_pump_frac` is effectively binary (one train
    required, so surviving pumping is 1.0 or 0.0), and flow of exactly zero takes
    the other branch. Three trains staged on and two of the three chilled-water
    pumps lost puts the loop at a third of design flow, where Q = ṁ·cp·ΔT says the
    range must open by the same factor. Nothing else covers that."""
    random.seed(SEED)
    p = build_plant(tmp_path, trains=TRAINS, servers=1200,
                    installed_modules=MODULES, crahs=CRAHS)
    _run(p, SETTLE_TICKS)
    assert len(p.store._plant_trains_run[DC]) == TRAINS, "need every train staged on"
    dt_full = p.store._chw_dt_c[DC]

    # Two lead pumps and the header spare gone: one of the three required survives.
    for i in (1, 2, TRAINS + 1):
        _apply(plant_cache, f"CHWP{i}-{DC}-CP", "Alarm_Fault", 1.0)
    _run(p, HOLD_TICKS)

    frac = p.store._chw_pump_frac[DC]
    assert frac == pytest.approx(1.0 / TRAINS, abs=0.01), (
        f"expected a third of design pumping, got {frac}")
    assert p.store._chw_dt_c[DC] == pytest.approx(dt_full / frac, rel=0.15), (
        f"range must open as flow closes: {dt_full} → {p.store._chw_dt_c[DC]}")
    _envelope(p)


def test_the_air_side_going_dark_leaves_the_plant_healthy(tmp_path, plant_cache):
    """The scenario the campaign kept as its control. Losing every CRAH in a hall
    is an AIR-side failure: the room runs away, but the chillers, pumps and towers
    are all still doing their jobs and the water loop must say so."""
    p = _plant(tmp_path)
    for i in range(1, CRAHS + 1):
        _apply(plant_cache, f"CRAH{i}-{DC}-HA-R1-01", None, 0.0)
    _run(p, HOLD_TICKS)

    assert p.store._chw_supply_c[DC] == pytest.approx(7.0, abs=0.5), (
        "the plant is making good water — the failure is in the hall")
    assert p.store._cool_loss_frac.get(DC, 0.0) == 0.0
    assert p.store.cooling_degraded(DC) is False

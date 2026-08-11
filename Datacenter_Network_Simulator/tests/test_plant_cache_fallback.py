"""The mechanical branch must carry load whether or not BACnet is running.

Cooling plant is the only load class whose live watts come from a DIFFERENT
protocol than the boards that feed it. A chiller's draw is a BACnet present value;
the MCC and MPP that carry it publish over SNMP. So stopping the BACnet simulator
emptied `_plant_state_cache`, `_plant_watts` returned 0 for every unit, and
`_compute_power_flow` — which only admits a load when `w > 0` — put nothing into
the mechanical branch at all.

What that looked like live: four MCCs and eight MPPs reporting ENERGIZED at nominal
voltage with ZERO current, and PUE losing its numerator. Not an error state — a
plausible one. Nothing on the SNMP plane says "my source is gone", and a real MCC's
trip unit is a CT on the bus that reads load regardless of whether any BMS
integration is alive.

The fallback is the staged cooling model, which is computed every tick from live IT
heat and weather whatever BACnet is doing, and already carries the states the meters
would show (standby 0, unpowered 0, tripped at the auxiliary floor). These tests pin
the four cases that matter: cache wins when present, model carries when it is gone,
an unpowered unit still reads zero either way, and a cold start does not invent load.
"""
import pytest

from tests.conftest import DC, build_plant


def _wire_mcc(fx):
    """Hang the plant off a minimal, ENERGIZED electrical ladder; return the MCC.

    The fixture is a cooling-layer plant with no electrical side, and the defect was
    only ever visible from the board above the plant — that is where the missing
    watts were supposed to arrive.

    A bare MCC is not enough. _compute_energized walks down from the SOURCES, so a
    board with nothing upstream is dead by definition, `_active_parents` drops it
    from every plant unit's parent list, and the cascade delivers nothing — a zero
    at the board that looks exactly like the bug under test but means "no feed".
    Hence the full utility → switchgear → ATS → MCC chain, which is also the
    shortest path that exercises the real energization rules.
    """
    from tests.conftest import _device

    def mk(name, dtype, ip, model=""):
        d = _device(fx.dm, f"{name}-{DC}-UR", dtype, ip, 0, model=model,
                    room="UPS Room")
        # BOTH registries: _device only reaches the DeviceManager, while the power
        # cascade walks the TopologyEngine. An edge to an id the graph has no
        # device for creates a bare node that types as a leaf, and the board reads
        # zero for a third unrelated reason.
        fx.topo.add_device(d)
        return d

    util = mk("UTIL1", "utility_feed", "10.0.9.1")
    swgr = mk("SWGR1", "switchgear", "10.0.9.2")
    ats = mk("ATS1", "ats", "10.0.9.3")
    mcc = mk("MCC1", "mcc", "10.0.9.4", "Eaton Freedom 2100 MCC 1600A")
    for a, b in ((util, swgr), (swgr, ats), (ats, mcc)):
        fx.topo.add_link(a.id, b.id, None, None, layer="power")
    for d in _plant_units(fx):
        fx.topo.add_link(mcc.id, d.id, None, None, layer="power")
    return mcc


def _plant_units(fx):
    return [d for d in fx.dm.get_all_devices()
            if d.device_type.value in ("chiller", "pump", "cooling_tower", "crah")]


def _through(fx, dev):
    return fx.store._through_live.get(dev.id, 0.0)


def test_cache_wins_when_bacnet_is_publishing(tmp_path, plant_cache):
    """With BACnet up, the branch carries the METER's number, not the model's.

    The fallback must not quietly displace real telemetry — a faulted machine
    reporting its auxiliary draw over BACnet has to reach the board as that draw.
    """
    fx = build_plant(tmp_path, crahs=2)
    mcc = _wire_mcc(fx)
    fx.tick()                                   # fills _plant_power_by_name

    # A deliberately implausible reading, so a pass cannot be the model agreeing
    # with the meter by coincidence.
    for d in _plant_units(fx):
        plant_cache[d.name] = {"Active_Power": 7.0}       # kW
    fx.tick()

    expected = 7000.0 * len(_plant_units(fx))
    assert _through(fx, mcc) == pytest.approx(expected, rel=1e-6)


def test_branch_carries_load_with_bacnet_never_started(tmp_path, plant_cache):
    """THE REGRESSION. Cache empty for the whole run; the MCC must still read load.

    Two ticks because the model publishes at the END of the pass it is computed in,
    so the first tick is the cold start covered below and the second is the steady
    state an operator would ever actually look at.
    """
    fx = build_plant(tmp_path, crahs=2)
    mcc = _wire_mcc(fx)
    fx.tick()
    fx.tick()

    assert not plant_cache, "fixture must run with BACnet stopped"
    carried = _through(fx, mcc)
    assert carried > 0.0, "mechanical branch read zero with BACnet stopped"

    # And it is the model's number, unit for unit — not some floor or average.
    expected = sum(fx.power(d.name) for d in _plant_units(fx)) * 1000.0
    assert carried == pytest.approx(expected, rel=1e-6)


def test_unpowered_unit_reads_zero_from_either_source(tmp_path, plant_cache):
    """A unit whose MCC is dead draws nothing — the fallback must not resurrect it.

    This is the guard the fallback had to be threaded around: `unpowered` is checked
    BEFORE both sources, so a machine on a dead bus contributes 0 whether its last
    BACnet reading or the staged model says otherwise.
    """
    fx = build_plant(tmp_path, crahs=2)
    mcc = _wire_mcc(fx)
    fx.tick()
    fx.tick()
    live = _through(fx, mcc)
    assert live > 0.0

    dead = _plant_units(fx)[0]
    fx.store._plant_unpowered_names = {dead.name}
    plant_cache[dead.name] = {"Active_Power": 99.0}       # stale meter reading
    fx.tick()

    assert _through(fx, dead) == 0.0
    assert _through(fx, mcc) < live


def test_cold_start_invents_no_load(tmp_path, plant_cache):
    """One pass with neither source populated: zero, and no exception.

    The fallback reads a map that the SAME pass fills at its end, so the very first
    computation has nothing to draw on. That has to read as "nothing known yet"
    rather than raising or fabricating a figure.

    Called directly rather than through fx.tick(), which cannot show this: the tick
    chain runs _compute_cond_loop first, and that primes staging with a full extra
    _compute_power_flow(). By the time a tick's own pass runs, the model map is
    already populated — which is exactly why a real startup shows no zero-load blip
    on the mechanical boards, and why this case needs asking for on purpose.
    """
    fx = build_plant(tmp_path, crahs=2)
    mcc = _wire_mcc(fx)
    assert not fx.store._plant_power_by_name

    fx.store._compute_power_flow()

    assert _through(fx, mcc) == 0.0
    assert fx.store._plant_power_by_name, "the pass must still publish the model"


def test_first_tick_already_carries_load(tmp_path, plant_cache):
    """And the operator-visible first tick is NOT zero, for the reason above.

    Pins the priming behaviour the cold-start test works around: if that prime is
    ever removed, the mechanical branch gains a one-tick hole at startup — a
    transient that reads as a dead plant on whatever polls in that window.
    """
    fx = build_plant(tmp_path, crahs=2)
    mcc = _wire_mcc(fx)
    fx.tick()

    assert _through(fx, mcc) > 0.0

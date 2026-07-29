"""EV2 panel power quality vs load.

The meter's whole reason for reporting PF and %THD-i is that they move in OPPOSITE
directions with load: active-PFC server supplies improve power factor as they load
up, while current distortion is worst at light load because it is measured against
a shrinking fundamental. Both must therefore respond to load at the PANEL, not only
per branch.
"""
import pytest

from core.bacnet_telemetry import EV2TelemetryEngine, BRANCH_BREAKER_KW


def _settle(eng, circuit_kw, ticks=40):
    """Run the engine to steady state at a fixed branch loading.

    Passes BOTH live_kw and circuit_kw, exactly as BACnetController.tick does -
    the bug being guarded here lives on the live_kw path, so a test that omitted
    it would exercise the wrong branch and prove nothing.
    """
    vals = {}
    for _ in range(ticks):
        vals = eng.tick(30.0, live_kw=sum(circuit_kw), circuit_kw=list(circuit_kw))
    return vals


def _engine(branches, commissioned_kw):
    """A panel whose rated_kw was fixed at commissioning, as the real one is."""
    return EV2TelemetryEngine(circuits=42, rated_kw=commissioned_kw)


def test_panel_thd_falls_as_the_panel_loads_up():
    """The headline behaviour. A lightly loaded panel shows high current
    distortion; the same panel near its branch capacity shows the active-PFC
    floor."""
    light = _settle(_engine(12, 25.0), [0.6] * 12)
    heavy = _settle(_engine(12, 25.0), [BRANCH_BREAKER_KW * 0.95] * 12)
    assert light["Current_THD"] > heavy["Current_THD"] + 4.0
    assert heavy["Current_THD"] < 7.0


def test_panel_pf_climbs_as_the_panel_loads_up():
    light = _settle(_engine(12, 25.0), [0.6] * 12)
    heavy = _settle(_engine(12, 25.0), [BRANCH_BREAKER_KW * 0.95] * 12)
    assert heavy["Panel_PF"] > light["Panel_PF"] + 0.05


def test_power_quality_survives_the_fleet_outgrowing_the_meter():
    """The regression this guards.

    rated_kw is frozen when the meter is commissioned. On a growing fleet the live
    load overruns that figure within a few racks, so a load fraction measured
    against it pins at its clamp and %THD-i sticks at the full-load floor forever -
    the panel then reports a perfectly loaded supply while it is nearly idle. The
    fraction must be measured against the BREAKER capacity of the branches actually
    clamped, which is what the per-circuit curves already use.
    """
    # Commissioned tiny; now carrying 12 branches at a light 8 % of breaker.
    eng = _engine(12, 5.0)
    light = _settle(eng, [BRANCH_BREAKER_KW * 0.08] * 12)
    assert light["Current_THD"] > 9.0, (
        "panel THD saturated at the full-load floor despite a nearly idle panel")

    # Same commissioning figure, same branch count, now genuinely loaded.
    heavy = _settle(_engine(12, 5.0), [BRANCH_BREAKER_KW * 0.9] * 12)
    assert heavy["Current_THD"] < light["Current_THD"] - 4.0


def test_panel_and_branches_agree_on_load():
    """Panel and per-circuit curves must share one basis, or the mains contradicts
    the branches it is the sum of."""
    eng = _engine(12, 25.0)
    vals = _settle(eng, [BRANCH_BREAKER_KW * 0.25] * 12)
    branch_thd = [vals[f"Ckt{i:02d}_THD"] for i in range(1, 13)]
    mean_branch = sum(branch_thd) / len(branch_thd)
    assert vals["Current_THD"] == pytest.approx(mean_branch, abs=3.0)


def test_spare_breakers_do_not_dilute_the_load_fraction():
    """A 42-pole panel with 12 branches clamped is judged on those 12, not on 42 -
    otherwise every partly built panel would read permanently light."""
    twelve = _settle(_engine(12, 25.0), [BRANCH_BREAKER_KW * 0.9] * 12)
    thirty = _settle(_engine(30, 25.0), [BRANCH_BREAKER_KW * 0.9] * 30)
    assert twelve["Current_THD"] == pytest.approx(thirty["Current_THD"], abs=2.0)

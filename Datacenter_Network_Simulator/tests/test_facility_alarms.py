"""Facility electrical gear — switchgear, MCC, MPP, generator.

Every one of these is metered by the sim and every one of them loads up as the IT
fleet grows (the MCC and MPP directly: they carry the cooling plant, which tracks
IT heat). Until these rules existed the physics happened with nothing annunciating
it — the whole chain from the service entrance to the mechanical panelboards was
silent no matter how close to its rating it ran.
"""
import pytest

from conftest import DC


def _engine():
    from core.rule_engine import RuleEngine
    from core.trap_rules import DEFAULT_RULES

    eng = RuleEngine()
    eng.fired = []
    eng.set_enabled(True)
    eng.set_action_callback(lambda action: eng.fired.append(action.rule.rule_name))
    for r in DEFAULT_RULES:
        eng.add_rule(r)
    return eng


def _fire(eng, dtype, t=0.0, **kw):
    from core.fact_model import DeviceFact

    base = dict(device_id=f"{dtype.upper()}1-{DC}", device_type=dtype,
                model_name="", ip_address="10.9.9.9", timestamp=t)
    base.update(kw)
    eng.fired.clear()
    eng.evaluate_fact(DeviceFact(**base))
    return set(eng.fired)


def _fire_held(eng, dtype, seconds=120.0, step=15.0, **kw):
    """Some of these rules carry a dwell (a genset legitimately overshoots on load
    acceptance), and durations are measured against the fact timestamp."""
    fired, t = set(), 0.0
    while t <= seconds:
        fired |= _fire(eng, dtype, t=t, **kw)
        t += step
    return fired


# ── Load ladders ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dtype,alarm,critical", [
    ("switchgear", "SwitchgearBusOverload", "SwitchgearBusOverloadCritical"),
    ("mcc", "MCCOverload", "MCCOverloadCritical"),
    ("mpp", "MPPOverload", "MPPOverloadCritical"),
])
def test_load_ladder(dtype, alarm, critical):
    """A bus planned to 80 % for continuous load alarms at 85 % (out of planning
    headroom) and again at 95 % (about to trip a main). Below the planning limit it
    stays quiet — a fully loaded board is not a fault."""
    eng = _engine()
    assert not _fire(eng, dtype, elec_load_pct=60.0, elec_status="energized")

    fired = _fire(eng, dtype, elec_load_pct=88.0, elec_status="energized")
    assert alarm in fired and critical not in fired

    fired = _fire(eng, dtype, elec_load_pct=97.0, elec_status="energized")
    assert critical in fired


@pytest.mark.parametrize("dtype,alarm,recovery", [
    ("switchgear", "SwitchgearBusOverload", "SwitchgearBusLoadNormal"),
    ("mcc", "MCCOverload", "MCCLoadNormal"),
    ("mpp", "MPPOverload", "MPPLoadNormal"),
])
def test_load_clears_inside_the_alarm_point(dtype, alarm, recovery):
    """The clear sits below the alarm so a board riding its threshold cannot
    chatter alarm/clear every tick — the same convention the UPS and PDU rules use."""
    eng = _engine()
    assert alarm in _fire(eng, dtype, elec_load_pct=88.0, elec_status="energized")
    # Still above the clear: no recovery yet.
    assert recovery not in _fire(eng, dtype, elec_load_pct=83.0,
                                 elec_status="energized")
    assert recovery in _fire(eng, dtype, elec_load_pct=70.0,
                             elec_status="energized")


def test_generator_overload_needs_a_dwell():
    """A standby set legitimately overshoots when the ATS dumps a block load on it;
    what matters is whether it STAYS there, because past ~90 % the governor has no
    headroom left for the next block."""
    eng = _engine()
    assert not _fire(eng, "generator", elec_load_pct=95.0, elec_status="running")
    assert "GeneratorOverload" in _fire_held(eng, "generator", elec_load_pct=95.0,
                                             elec_status="running")


def test_generator_load_clears():
    eng = _engine()
    _fire_held(eng, "generator", elec_load_pct=95.0, elec_status="running")
    assert "GeneratorLoadNormal" in _fire(eng, "generator", t=999.0,
                                          elec_load_pct=60.0,
                                          elec_status="running")


def test_rules_do_not_cross_device_types():
    """Each board carries its own thresholds on the shared metric, so an MCC rule
    must not fire on a switchgear fact and vice versa."""
    eng = _engine()
    fired = _fire(eng, "mcc", elec_load_pct=97.0, elec_status="energized")
    assert not any(n.startswith(("Switchgear", "MPP", "Generator")) for n in fired)


# ── Health ───────────────────────────────────────────────────────────────────

def test_dead_bus_is_a_transition_not_a_state():
    """A board that is already dead at startup must not trap — otherwise every
    cold start floods the NMS, and the shipped topology boots with its generator
    paralleling boards dead by design (they energise when the gensets run)."""
    eng = _engine()
    assert "SwitchgearBusDead" not in _fire(eng, "switchgear", elec_status="dead",
                                            elec_load_pct=0.0)
    # Energised first, then lost: that IS the event.
    _fire(eng, "switchgear", elec_status="energized", elec_load_pct=40.0)
    assert "SwitchgearBusDead" in _fire(eng, "switchgear", elec_status="dead",
                                        elec_load_pct=0.0)


def test_dead_bus_recovers():
    eng = _engine()
    _fire(eng, "switchgear", elec_status="energized", elec_load_pct=40.0)
    _fire(eng, "switchgear", elec_status="dead", elec_load_pct=0.0)
    assert "SwitchgearBusEnergized" in _fire(eng, "switchgear",
                                             elec_status="energized",
                                             elec_load_pct=40.0)


def test_mcc_dead_bus_is_critical():
    """An MCC going dark is a cooling outage in waiting — the chilled-water loop's
    thermal mass buys about a minute, then the room starts warming."""
    eng = _engine()
    _fire(eng, "mcc", elec_status="energized", elec_load_pct=40.0)
    _fire(eng, "mcc", elec_status="dead", elec_load_pct=0.0)
    rule = eng.get_rule("MCCBusDead")
    assert rule.severity == "critical"


def test_generator_failure_to_start_traps():
    eng = _engine()
    _fire(eng, "generator", elec_status="standby", elec_load_pct=0.0)
    assert "GeneratorFailedToStart" in _fire(eng, "generator", elec_status="fault",
                                             elec_load_pct=0.0)
    assert "GeneratorRecovered" in _fire(eng, "generator", elec_status="standby",
                                         elec_load_pct=0.0)


# ── The metric actually reaches the fact ─────────────────────────────────────

def test_fact_normalises_each_board_onto_one_metric():
    """The gear reports load and health under per-type key prefixes; the fact has
    to normalise both so one rule per type can read them."""
    from core.device_state_store import DeviceStateStore

    keys = DeviceStateStore._ELEC_FACT_KEYS
    assert keys["switchgear"] == ("swgr_", "swgr_bus_status")
    assert keys["mcc"] == ("mcc_", "mcc_status")
    assert keys["mpp"] == ("mpp_", "mpp_status")
    assert keys["generator"] == ("gen_", "gen_status")
    # An ATS is a switch, not a meter, and a revenue meter has no rating to load
    # against — inventing a load % for either would be fiction.
    assert "ats" not in keys and "utility_feed" not in keys

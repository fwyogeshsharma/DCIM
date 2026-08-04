"""Cooling-plant regression gate.

Six representative fault scenarios from the fault-injection campaign, pinned so
that later work on the cooling model cannot quietly rewrite every thermal number
in the simulator with nothing to catch it.

WHAT IS PINNED, AND WHAT IS NOT. These are physical invariants and bounded
ranges, not snapshots of today's arithmetic. Snapshotting the current numbers
would pin the bugs along with the behaviour — several of the numbers this model
produces today are wrong, and the point of a gate is to protect the parts that
are right while the wrong parts get fixed.

So the file is in two halves:

  * The INVARIANTS below hold today and must keep holding. Redundancy absorbs a
    single fault; a total loss runs the room away; the evaporator and condenser
    sides are genuinely independent in both directions; losing rejection latches
    the high-pressure cutout.

  * The GATES at the bottom are marked xfail(strict) because they describe
    behaviour the model does NOT have yet. They are the acceptance criteria for
    the remediation phases, written before the fix so the fix has something to
    prove itself against. When one starts passing, delete its marker — strict
    xfail turns an unexpected pass into a failure precisely so this cannot be
    forgotten.

Determinism: the store's published points carry a random walk, and the suite is
run under pytest-randomly, which reseeds between tests. Everything asserted here
is derived from the thermal model rather than from a published point, so the
walk cannot reach it — but the seed is pinned anyway so a failure is
reproducible from the same command line.
"""
import random

import pytest

from conftest import DC, build_plant  # noqa: F401  (build_plant used by the knee tests)

SEED = 20260731

# Fixture plant sized so the arithmetic is representative rather than degenerate:
# three complete trains against a load that stages one of them, which is the
# staging depth the campaign's deep-dive phase ran at and the depth where a
# single machine carries the whole site.
TRAINS = 3
SERVERS = 200
MODULES = 6
CRAHS = 4

SETTLE_TICKS = 4      # let staging prime and the loop reach steady state
HOLD_TICKS = 90       # ≈ the campaign's 90 s observation window at a 1 s tick


# ── Harness ──────────────────────────────────────────────────────────────────

def _plant(tmp_path, cache, **kw):
    random.seed(SEED)
    cache.clear()
    p = build_plant(tmp_path, trains=TRAINS, servers=SERVERS,
                    installed_modules=MODULES, crahs=CRAHS, **kw)
    for _ in range(SETTLE_TICKS):
        p.tick()
    return p


def _hold(p, ticks=HOLD_TICKS):
    for _ in range(ticks):
        p.tick()


def _alarm(cache, name, running_point, alarm):
    """Fault a unit the way the plant override channel does: the unit is still
    commanded on and reporting run status, with a health alarm set."""
    cache[name] = {running_point: 1.0, alarm: 1.0}


def _stop(cache, name, running_point):
    """Stop a unit SILENTLY — run status reads 0 and no alarm is raised. This is
    the failure a real run-status proof timer exists to catch."""
    cache[name] = {running_point: 0.0}


def _lead_chillers(p):
    return [t["chiller"] for t in p.store._plant_trains_run[DC]]


def _pen(p):
    return p.store._chw_pen.get(DC, 0.0)


def _cond(p):
    return p.store._cond_water_c.get(DC, 0.0)


# ── 1. Baseline ──────────────────────────────────────────────────────────────

def test_healthy_plant_holds_setpoint(tmp_path, plant_cache):
    """Nothing faulted: no cooling loss, no penalty, chilled water on setpoint.

    The baseline every other scenario is measured against. A plant that drifts
    off setpoint while healthy would make every delta below meaningless."""
    from core.cooling_model import CHW_SETPOINT_C

    p = _plant(tmp_path, plant_cache)
    _hold(p, 20)
    assert p.store._cool_loss_frac[DC] == 0.0
    assert _pen(p) == 0.0
    assert p.store._chw_supply_c[DC] == CHW_SETPOINT_C
    assert p.store._tower_reject[DC] == 1.0


# ── 2. Redundancy absorbs a single fault ─────────────────────────────────────

def test_alarmed_lead_chiller_fails_over_and_costs_nothing(tmp_path, plant_cache):
    """The campaign's headline: the biggest machine in the building fails and the
    site does not notice, because a standby train is promoted within a tick.

    This is the behaviour that makes fault severity a property of the redundancy
    topology rather than of the machine, and it must survive any change to the
    staging code."""
    p = _plant(tmp_path, plant_cache)
    lead = _lead_chillers(p)[0]
    _alarm(plant_cache, lead, "Chiller_Running", "Alarm_HighPressure")
    _hold(p, 30)

    assert lead not in _lead_chillers(p), "alarmed chiller must not stay lead"
    assert _lead_chillers(p), "a standby train must have been promoted"
    assert _pen(p) < 0.5, f"failover should cost nothing, penalty was {_pen(p)}"


# ── 3. Total evaporator-side loss runs the room away ─────────────────────────

def test_total_chiller_loss_runs_the_room_away(tmp_path, plant_cache):
    """Every chiller alarmed. With no train left to promote the loss is total,
    the penalty leaves the bounded branch and integrates upward, and chilled
    water climbs well past setpoint.

    Bounded rather than exact: the phase-1 time-base fix preserves the 1 s tick
    rate, so this range holds across it, but pinning the exact figure would make
    the gate fail on a legitimate change to the integration constant."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    assert p.store._cool_loss_frac[DC] == pytest.approx(1.0, abs=0.01)
    assert 8.0 < _pen(p) < 20.0, f"expected a runaway, got {_pen(p)}"
    assert p.store._chw_supply_c[DC] > 15.0


def test_evaporator_loss_leaves_the_condenser_untouched(tmp_path, plant_cache):
    """The independence that the campaign called the model's best result. An
    evaporator-side failure cannot raise condensing pressure — there is no heat
    reaching the condenser to raise it — so the condenser loop must not move.

    Losing this would be the single most misleading regression possible here: it
    is the difference between a plant that models two loops and one that models a
    single lumped temperature."""
    p = _plant(tmp_path, plant_cache)
    cond_before = _cond(p)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    assert _cond(p) == pytest.approx(cond_before, abs=1.0)
    assert not p.store._chiller_hp_lockout, "no high-pressure trip on an evaporator loss"


# ── 4. Condenser-side loss is the mirror image ───────────────────────────────

def test_condenser_pump_loss_runs_the_condenser_away(tmp_path, plant_cache):
    """Lose every condenser pump and heat stops reaching the tower, so the
    condenser loop climbs — the clean mirror of the evaporator case above, and
    it must reach the loop through the same head-pressure path a stalled tower
    would use.

    Asserted on the loop temperature and the trip rather than on _tower_reject:
    that field holds the TOWER's rejection capability only. The condenser-pump
    term is folded into the local rejection figure after _tower_reject has been
    stored, deliberately, because the pumps are train members and their loss is
    already counted once in the per-train deficit."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    assert _cond(p) > 40.0, f"condenser should run away, got {_cond(p)}"
    assert p.store._chiller_hp_lockout, "lost condenser flow must reach the HP cutout"


def test_tower_bank_loss_latches_high_pressure_trips(tmp_path, plant_cache):
    """The deepest chain in the campaign, and it must hold end to end: fill area
    gone → condenser water climbs → chillers unload at the capacity limit → the
    high-pressure cutout latches.

    The latch is the part worth protecting. A trip that cleared itself when the
    tower fault cleared would make the whole lost-rejection scenario self-heal,
    which is the opposite of what a manual-reset cutout does."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CT{i}-{DC}-RF", "Fan_Status", "Alarm_HighVibration")
    _hold(p)

    assert _cond(p) > 40.0
    assert p.store._chiller_hp_lockout, "lost rejection must latch the HP cutout"

    # And it stays latched after the fault clears — cooling the loop is not a reset.
    plant_cache.clear()
    _hold(p, 30)
    assert p.store._chiller_hp_lockout, "HP lockout must not self-clear"


def test_severity_is_monotonic_across_the_tolerance_knee(tmp_path, plant_cache):
    """A worse failure must never read milder than a smaller one.

    The penalty switches branches at _COOL_TOL: below it a shortfall eases to a
    standing offset, above it the excess heat integrates. The two used to be
    discontinuous — the bounded side arrived at its offset within a time constant
    while the runaway side started from zero and needed tens of seconds to climb
    to the value the MILDER fault was already sitting at. On three staged trains,
    one chiller lost (L 0.340) read 6.089 K at 90 s and two lost (L 0.673) read
    6.055 K. An operator triaging by inlet temperature works the bigger outage
    second.

    Sized at 1200 servers deliberately: the default fixture stages ONE train, so
    the first two faults are covered by promotion and the loss never lands near
    the knee at all. The inversion only exists where the shortfall can straddle
    it, which is why no earlier test saw it."""
    seen = []
    for n in range(0, TRAINS + 1):
        random.seed(SEED)
        plant_cache.clear()
        p = build_plant(tmp_path / f"knee{n}", trains=TRAINS, servers=1200,
                        installed_modules=MODULES, crahs=CRAHS)
        for _ in range(SETTLE_TICKS):
            p.tick()
        assert len(p.store._plant_trains_run[DC]) == TRAINS, (
            "this test needs every train staged on, or the knee is never crossed")
        for i in range(1, n + 1):
            _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
        _hold(p)
        seen.append((p.store._cool_loss_frac[DC], _pen(p)))

    assert [l for l, _ in seen] == sorted(l for l, _ in seen), (
        f"cooling loss should grow with the number of faults: {seen}")
    for (l0, p0), (l1, p1) in zip(seen, seen[1:]):
        assert p1 > p0, (
            f"loss {l1} read milder than loss {l0}: {p1} K vs {p0} K")


def test_the_two_penalty_branches_meet_at_the_knee(tmp_path, plant_cache):
    """The floor the runaway branch is held to is the offset the plant carries AT
    the knee — not this shortfall's own bounded value.

    Flooring at loss × 18 also removes the inversion, but it extrapolates a
    bounded-regime expression to a total loss, floors the penalty at 18 K and
    roughly doubles the measured runaway (11.9 → 23.2 K at 90 s). That is a
    recalibration of the thermal model wearing a monotonicity fix as a disguise,
    so the rate is pinned here as well as the ordering."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    assert p.store._cool_loss_frac[DC] == pytest.approx(1.0, abs=0.01)
    assert 10.0 < _pen(p) < 17.0, (
        f"total loss should integrate at the calibrated rate, got {_pen(p)}")


def _valve_pen(tmp_path, plant_cache, tag, points):
    """Penalty after holding a VCHW fault described by *points*."""
    random.seed(SEED)
    plant_cache.clear()
    p = build_plant(tmp_path / tag, trains=TRAINS, servers=SERVERS,
                    installed_modules=MODULES, crahs=CRAHS, valves=True)
    for _ in range(SETTLE_TICKS):
        p.tick()
    plant_cache[f"VCHW-{DC}-CP"] = points
    _hold(p)
    return _pen(p)


def test_two_ways_of_losing_the_same_valve_cost_the_same(tmp_path, plant_cache):
    """A stuck actuator and one that has stopped modulating are the same physical
    state — the disc is no longer tracking command — so at the same travel they
    must cost the same.

    The campaign measured 0.3 K against 2.3 K for exactly this pair. Both reach
    the penalty through _is_faulted and both are now scored on travel, so the
    fault LABEL cannot change the answer; only the position can.

    Both injections carry position feedback, because a real actuator that has
    stopped modulating still reports where it is. Pinning this pair against a
    feedback-less injection would compare a known valve with an unknown one and
    call the difference an inversion."""
    at = {"Position": 45.0, "Commanded_Position": 65.0}
    stuck = _valve_pen(tmp_path, plant_cache, "stuck",
                       {**at, "Status_Modulating": 1.0, "Alarm_ActuatorFault": 1.0})
    frozen = _valve_pen(tmp_path, plant_cache, "frozen",
                        {**at, "Status_Modulating": 0.0})

    assert stuck > 0.1, f"a valve stuck below its command must cost cooling, got {stuck}"
    assert stuck == pytest.approx(frozen, abs=0.5), (
        f"stuck actuator {stuck} K vs stopped modulating {frozen} K")


def test_a_valve_stuck_open_is_not_a_cooling_loss(tmp_path, plant_cache):
    """Severity is WHERE the disc froze. A header valve is fail-in-place — the
    actuators used at that torque have no spring return — so a dead actuator that
    happens to be wide open still passes design flow. Scoring it the same as one
    stuck shut, which a bare fault bit does, makes a non-event and a catastrophe
    read alike and is what put the whole severity ranking in question.

    A valve stuck FURTHER OPEN than commanded is a pumping-energy penalty, not a
    cooling loss, and this model does not charge for it."""
    fault = {"Status_Modulating": 1.0, "Alarm_ActuatorFault": 1.0}
    wide = _valve_pen(tmp_path, plant_cache, "open",
                      {**fault, "Position": 95.0, "Commanded_Position": 65.0})
    part = _valve_pen(tmp_path, plant_cache, "part",
                      {**fault, "Position": 45.0, "Commanded_Position": 65.0})
    shut = _valve_pen(tmp_path, plant_cache, "shut",
                      {**fault, "Position": 0.0, "Commanded_Position": 65.0})

    assert wide < 0.1, f"a valve stuck open costs no cooling, got {wide}"
    assert part > wide, f"partly shut must beat wide open: {part} vs {wide}"
    assert shut > part, f"fully shut must beat partly shut: {shut} vs {part}"


def test_a_valve_with_no_position_feedback_reads_shut(tmp_path, plant_cache):
    """No feedback is not no loss. A modulating valve that has stopped reporting
    travel is unknown, and the conservative reading — the one a real BMS takes
    when the 4–20 mA feedback drops — is that it is shut. Anything else lets a
    valve disappear from the loss arithmetic by failing harder."""
    blind = _valve_pen(tmp_path, plant_cache, "blind", {"Status_Modulating": 0.0})
    shut = _valve_pen(tmp_path, plant_cache, "shut2",
                      {"Status_Modulating": 0.0, "Position": 0.0,
                       "Commanded_Position": 65.0})

    assert blind == pytest.approx(shut, abs=0.5), (
        f"lost feedback must read as shut: {blind} vs {shut}")


# ── 5. Ceiling ───────────────────────────────────────────────────────────────

def test_runaway_is_bounded_by_the_thermal_ceiling(tmp_path, plant_cache):
    """A runaway integrates upward but stops at equipment-limit territory rather
    than growing without bound, and the chilled-water supply stays capped below
    it — a starved plant makes warm water, not boiling water."""
    from core.cooling_model import CHW_SETPOINT_C, CHW_SUPPLY_RISE_MAX_C

    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p, 600)

    assert _pen(p) <= p.store._COOL_MAX
    assert (p.store._chw_supply_c[DC]
            <= CHW_SETPOINT_C + CHW_SUPPLY_RISE_MAX_C + 1e-6)


# ══ Acceptance gates for the remediation phases ══════════════════════════════
# Each of these describes behaviour the model does not have yet. Marked strict so
# that when the corresponding fix lands, an unexpected pass fails the suite and
# forces the marker to be removed rather than left behind as a lie.


def test_loop_flow_falls_when_the_pumps_are_gone(tmp_path, plant_cache):
    """Water is moved by pumps, so losing them must reduce flow.

    Today flow is Q/(cp·ΔT) from live IT heat, then split across the pumps still
    running — so faulting every pump RAISES the published figure. The campaign
    measured 7.8 → 14.9 l/s with all four chilled-water pumps in alarm."""
    p = _plant(tmp_path, plant_cache)
    before = p.store._chw_flow_lps[DC]
    for i in range(1, TRAINS + 2):          # trains + the header standby
        _alarm(plant_cache, f"CHWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p, 30)

    assert p.store._chw_flow_lps[DC] < before * 0.5, (
        f"flow must fall with the pumps: {before} → {p.store._chw_flow_lps[DC]}")


def test_a_stalled_loop_widens_delta_t(tmp_path, plant_cache):
    """ΔT is a RESULT of flow, not an input to it, and losing the pumps has to show
    up as a widening range.

    Flow used to be the demand figure Q/(cp·ΔT) with ΔT read off a duty curve. A
    demand figure cannot lose flow, so with every chilled-water pump in alarm the
    range still read ~4.6 K — a perfectly healthy-looking loop with nothing moving
    through it. Now the pumps set flow and the load sets ΔT, so a stalled header
    reads the saturated range a stagnant thermowell actually shows."""
    from core.cooling_model import CHW_DESIGN_DT_C, CHW_MAX_DT_C

    p = _plant(tmp_path, plant_cache)
    assert p.store._chw_dt_c[DC] == pytest.approx(CHW_DESIGN_DT_C, abs=0.2), (
        "a healthy loaded plant holds design range")
    for i in range(1, TRAINS + 2):          # trains + the header standby
        _alarm(plant_cache, f"CHWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    assert p.store._chw_flow_lps[DC] == 0.0, "no pumps, no flow"
    assert p.store._chw_dt_c[DC] == pytest.approx(CHW_MAX_DT_C, abs=0.1), (
        f"a stalled loop must read a saturated range, got {p.store._chw_dt_c[DC]}")
    assert p.store._chw_return_c[DC] > p.store._chw_supply_c[DC] + 10.0


def test_a_covered_pump_loss_does_not_move_the_loop(tmp_path, plant_cache):
    """Pump-derived flow must still respect N+1. The header spare covers one failed
    chilled-water pump, so the loop keeps its flow AND its design range — the
    failure costs redundancy, not cooling. Without this the inversion would read
    every single pump fault as a loop event."""
    from core.cooling_model import CHW_DESIGN_DT_C

    p = _plant(tmp_path, plant_cache)
    before = p.store._chw_flow_lps[DC]
    _alarm(plant_cache, f"CHWP1-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    assert p.store._chw_pump_frac[DC] == 1.0, "the spare should cover one loss"
    assert p.store._chw_flow_lps[DC] == pytest.approx(before, abs=0.2)
    assert p.store._chw_dt_c[DC] == pytest.approx(CHW_DESIGN_DT_C, abs=0.2)


def test_condenser_flow_falls_with_its_own_pumps(tmp_path, plant_cache):
    """The condenser flow meter has to lose flow when the pumps that make it stop.

    Its shape is still demand-led — the range there is set by the tower approach,
    not by a ΔP loop — but it is now capped by surviving condenser pumping, so
    rejection capability and the flow meter that ought to show the same loss are
    no longer derived independently."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    assert p.store._cw_pump_frac[DC] == 0.0
    for name, pts in _pump_points(p, "CWP").items():
        assert pts.get("Flow") == 0.0, f"{name} reports flow with every CW pump dead"


def _pump_points(p, prefix):
    return {d.name: p.auto_points(d.name) for d in p.dm.get_all_devices()
            if d.name.startswith(prefix)}


def test_a_stopped_pump_publishes_no_flow(tmp_path, plant_cache):
    """An impeller that is not turning moves no water, and its gauge set has to say
    so as one story.

    A faulted pump kept its share of the header flow because the split only
    excluded standby and unpowered units — so it advertised 5.5 l/s while its own
    Speed point, back-derived from a draw that had collapsed to the auxiliary
    floor, read 0 %. Flow, speed and differential all come off the same decision
    now, so they cannot disagree."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    pumps = _pump_points(p, "CWP")
    assert pumps, "fixture should carry condenser pumps"
    for name, pts in pumps.items():
        assert pts.get("Flow") == 0.0, f"{name} is faulted but reports flow {pts.get('Flow')}"
        assert pts.get("Speed") == 0.0, f"{name} reports speed {pts.get('Speed')}"
        assert pts.get("Diff_Pressure") == 0.0, (
            f"{name} holds {pts.get('Diff_Pressure')} kPa across a still impeller")


def test_a_header_spare_pump_is_never_left_walking(tmp_path, plant_cache):
    """The spare is a member of nobody's train, so publishing per-train left it
    with no override at all and the BACnet engine's own base curve went out on the
    wire — a faulted spare advertising 1.62 l/s while the header it feeds read 0.

    It only shows up once the spare has been pulled OUT of standby, which is
    exactly when every lead pump has failed and nothing is left to promote."""
    p = _plant(tmp_path, plant_cache)
    spare = f"CHWP{TRAINS + 1}-{DC}-CP"
    assert spare in _pump_points(p, "CHWP"), "fixture should carry a header spare"
    for i in range(1, TRAINS + 2):
        _alarm(plant_cache, f"CHWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p)

    assert p.store._chw_pump_frac[DC] == 0.0, "no chilled-water pumping should survive"
    for name, pts in _pump_points(p, "CHWP").items():
        assert pts.get("Flow") == 0.0, (
            f"{name} reports {pts.get('Flow')} l/s with every pump faulted")


def test_surviving_pumps_carry_the_whole_header_flow(tmp_path, plant_cache):
    """A dead pump's share is not its own — it belongs to whatever is still
    turning. Splitting the header across every pump PRESENT rather than every pump
    RUNNING understates each survivor and hides the duty a failure transfers."""
    p = _plant(tmp_path, plant_cache)
    _hold(p, 10)

    flowing = {n: pts["Flow"] for n, pts in _pump_points(p, "CHWP").items()
               if pts.get("Flow")}
    assert flowing, "some chilled-water pump should be turning"
    assert sum(flowing.values()) == pytest.approx(
        p.store._chw_flow_lps[DC], abs=0.6), (
        f"pump flows {flowing} should add up to the header {p.store._chw_flow_lps[DC]}")


def test_loss_of_evaporator_flow_sheds_the_chillers(tmp_path, plant_cache):
    """The flow switch every chiller is safety-interlocked to must be reachable
    from the model, and tripping it must actually stop the machine.

    An interlock that cannot trip is not an interlock. In the campaign
    CHWFlowLoss fired once in 38 scenarios — only when the meter was pinned by
    hand — and even then no chiller stopped."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 2):
        _alarm(plant_cache, f"CHWP{i}-{DC}-CP", "Run_Status", "Alarm_Fault")
    _hold(p, 60)

    assert p.store._chw_flow_interlock, "loss of evaporator flow must shed chillers"
    assert p.store._cool_loss_frac[DC] > 0.9


def test_reported_cooling_follows_the_surviving_plant(tmp_path, plant_cache):
    """The cooling figure PUE is built on has to fall when the plant stops running.

    It answered "what would a healthy plant draw for this load" — a demand figure
    — so during a total loss of chilled water it held flat while the branch meters
    showed the mechanical panel drop by a third. A DCIM integrating meters and one
    reading the headline disagreed by ~15 % during exactly the failure the
    simulator exists to rehearse.

    Asserted on cooling_model_kw() rather than on get_power_summary(), because the
    defect lives in the metered branch — max(metered, model) — and this fixture
    has no EV2 metering, so the summary falls through to the computed path where
    the figure was always real. Verify the end-to-end number with the campaign
    harness against a metered topology."""
    p = _plant(tmp_path, plant_cache)
    before = p.store.cooling_model_kw()
    assert before > 0.0, "fixture should model some cooling draw"
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    after = p.store.cooling_model_kw()
    assert after < before * 0.2, (
        f"cooling kW must track the surviving plant: {before} → {after}")


def test_metered_plant_draw_collapses_with_the_plant(tmp_path, plant_cache):
    """The METERED cooling branch has to fall with the plant, not just the model.

    get_power_summary takes max(metered, model), so scaling only the model left
    the meters winning every time: plant_power was normalised so the per-unit sum
    equalled the UNSCALED demand each tick, which put a dead chiller back at full
    draw. A total loss of chilled water read 132.9 → 140.3 kW with PUE falling
    1.672 → 1.651 — cooling rising while the plant was off, because the chassis-fan
    term was driving IT up underneath a mechanical figure that could not move.

    Asserted on the per-unit draws rather than get_power_summary() because that is
    what the meters integrate (_plant_watts → the power graph → the mech panel),
    and it holds whether or not a topology carries EV2 metering."""
    p = _plant(tmp_path, plant_cache)
    before = sum(p.store._plant_power_by_name.values())
    assert before > 0.0, "fixture should draw some plant power"
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    after = sum(p.store._plant_power_by_name.values())
    assert after < before * 0.75, (
        f"metered plant draw must follow the surviving plant: {before} → {after}")


def test_a_covered_fault_costs_the_panel_almost_nothing(tmp_path, plant_cache):
    """Staging and total failure must not read the same on the panel.

    One chiller down is covered: the BMS demotes it and promotes a standby, so the
    load moves to a machine that is still running and the mechanical panel barely
    notices. That redistribution is the reason the normalisation runs BEFORE the
    delivered-capacity collapse — it is correct here and only wrong when there is
    nothing left to carry the load."""
    p = _plant(tmp_path, plant_cache)
    before = sum(p.store._plant_power_by_name.values())
    _alarm(plant_cache, _lead_chillers(p)[0], "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    after = sum(p.store._plant_power_by_name.values())
    assert after > before * 0.75, (
        f"N+1 absorbed the fault; the panel should hold: {before} → {after}")


def test_a_tripped_chiller_is_not_disconnected(tmp_path, plant_cache):
    """A machine that has shut down still has a closed MCC bucket — controls, oil
    pump and crankcase heater stay energized — so its branch meter reads small,
    not zero. Only a unit whose MCC is genuinely dead reads nothing.

    Measured on a TOTAL loss, because a single fault is covered: the BMS demotes
    the alarmed unit and promotes a standby, and a staged-off machine really is at
    ~0. With every train faulted there is no standby left to promote, so the units
    stay commanded on and carry their auxiliaries."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    lead = _lead_chillers(p)[0]
    drawn = p.store._plant_power_by_name[lead]
    assert drawn > 0.0, "a tripped chiller is not disconnected"
    assert drawn < 20.0, f"a tripped chiller must fall to its auxiliaries, got {drawn}"


def test_cdu_coolant_follows_the_facility_loop(tmp_path, plant_cache):
    """A CDU rejects into the same chilled water the CRAHs use, so when that water
    goes warm the technology-cooling loop must follow it.

    It held 32.0 °C in every cascade, including one with all three chillers
    latched out and chilled water at 15–19 °C — while the die temperature on the
    servers it feeds DID take the penalty. The CDU point and the servers on its
    loop told different stories on the wire."""
    p = _plant(tmp_path, plant_cache, cdus=2)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    tcs = p.auto_points(f"CDU1-{DC}-HA-R1-01").get("TCS_Supply_Temp")
    assert tcs is not None, "the store must publish the CDU loop, not leave it walking"
    assert tcs > 34.0, f"coolant should follow the facility loop up, got {tcs}"


def test_starved_crah_drives_its_valve_open(tmp_path, plant_cache):
    """A CRAH holds discharge setpoint on its chilled-water valve. Feed the coil
    warm water and the controller drives that valve to 100 % and leaves it there.

    Measured at 40 → 46 % on a coil fed 19 °C water while the hall ran to
    42.7 °C — the position of a unit trimming for load, not one that has lost its
    cooling."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    valve = p.auto_points(f"CRAH1-{DC}-HA-R1-01").get("CHW_Valve")
    assert valve is not None, "the store must publish the valve, not leave it walking"
    assert valve > 95.0, f"a starved coil drives its valve open, got {valve}"


def test_tower_makeup_stops_on_a_dead_plant(tmp_path, plant_cache):
    """Makeup water is evaporation. With no chiller moving heat into the condenser
    loop there is nothing to evaporate, so consumption must collapse — it held at
    5.28 → 5.24 l/min with every chiller stopped."""
    p = _plant(tmp_path, plant_cache)
    cell = next(iter(p.store._tower_running_now[DC]))
    before = p.auto_points(cell)["Makeup_Flow"]
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    cell = next(iter(p.store._tower_running_now[DC]))
    after = p.auto_points(cell)["Makeup_Flow"]
    assert after < before * 0.5, f"makeup must follow real rejection: {before} → {after}"


def _step_all(p):
    """Run the per-device pass the ticker does after the cooling chain. The
    fixture's tick() covers the plant only; server and probe readings come from
    _step_device."""
    for d in p.dm.get_all_devices():
        p.store._step_device(d)


def test_crah_discharge_air_tracks_a_cooling_failure(tmp_path, plant_cache):
    """Discharge air is the CRAH's controlled variable and the first point a DCIM
    trends. Losing the coil's water is exactly what should raise it.

    It held 22.0 °C while its own return read 43.3 °C and server inlets 44.3 °C —
    a 21 K air-side rise across a coil being fed 19 °C water, which is physically
    impossible. The plant plane reported a healthy hall through a total
    failure."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)

    sa = p.auto_points(f"CRAH1-{DC}-HA-R1-01").get("Supply_Air_Temp")
    assert sa is not None, "the store must publish discharge air"
    assert sa > 28.0, f"discharge must follow the failure, got {sa}"


def test_the_cooling_penalty_is_counted_exactly_once(tmp_path, plant_cache):
    """The half of F3 that can silently go wrong.

    The room model holds the penalty privately and adds it to the inlet on the way
    out. Publishing it on the discharge point as well — without deleting that
    downstream addition — charges the room twice. Server inlet must land within a
    rack-height gradient of the discharge air feeding it, not a whole penalty
    above it."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)
    _step_all(p)

    sa = p.auto_points(f"CRAH1-{DC}-HA-R1-01")["Supply_Air_Temp"]
    inlets = [d.inlet_temp for d in p.dm.get_all_devices()
              if d.device_type.value == "server"]
    # +3 K is the full floor-to-top-of-rack gradient; +2 K of slack for noise.
    assert max(inlets) <= sa + 5.0, (
        f"inlet {max(inlets)} vs discharge {sa} — the penalty is being counted twice")


def test_rack_probes_see_what_the_servers_beside_them_see(tmp_path, plant_cache):
    """On a real floor these probes are the first alarm on a CRAH failure. Here
    they read 21.7–22.2 °C through every cascade while co-located servers read
    36–44 °C, which makes SensorAmbientTempHigh (>32 °C) and Critical (>38 °C)
    unreachable by physics."""
    p = _plant(tmp_path, plant_cache, rack_probes=3)
    for i in range(1, TRAINS + 1):
        _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
    _hold(p)
    _step_all(p)

    probes = [d.inlet_temp for d in p.dm.get_all_devices()
              if d.device_type.value == "sensor"]
    servers = [d.inlet_temp for d in p.dm.get_all_devices()
               if d.device_type.value == "server"]
    assert probes, "fixture should carry rack probes"
    avg_p, avg_s = sum(probes) / len(probes), sum(servers) / len(servers)
    assert abs(avg_p - avg_s) < 3.0, (
        f"probes {avg_p:.1f} °C vs servers {avg_s:.1f} °C in the same racks")
    assert max(probes) > 32.0, "the ambient alarms must be reachable"


# ── P5: coverage ─────────────────────────────────────────────────────────────

def test_cooling_plant_has_trap_coverage():
    """Sensors carry 35 rules, PDUs 30, UPSes 28 — and chillers, towers, pumps,
    valves, CRAHs and CDUs carried zero, while publishing live SNMP OIDs.

    All 54 traps in the campaign came from the six water probes. A cascade that
    latched three chillers out on high head pressure produced no chiller trap, and
    stopping seven CRAHs fired nothing at all. BACnet COV is the realistic primary
    path for plant alarms and it works; this is the SNMP plane, which the
    simulator otherwise populates for these same devices."""
    from core.trap_rules import DEFAULT_RULES

    covered = {}
    for r in DEFAULT_RULES:
        for t in (getattr(r, "device_types", None) or []):
            covered[t] = covered.get(t, 0) + 1
    for dtype in ("chiller", "cooling_tower", "pump", "valve", "crah"):
        assert covered.get(dtype), f"no trap rule targets {dtype}"
    # Every alarm rule needs its recovery pair, or a cleared plant fault leaves a
    # stuck alarm on the NMS.
    alarms = {r.rule_name for r in DEFAULT_RULES if not r.is_recovery}
    for r in DEFAULT_RULES:
        if r.is_recovery:
            assert r.recovery_of in alarms, f"{r.rule_name} recovers nothing"
    # OIDs must be unique within the enterprise plant arc.
    plant_oids = [r.trap_oid for r in DEFAULT_RULES
                  if r.trap_oid.startswith("1.3.6.1.4.1.99999.4.")]
    assert len(plant_oids) == len(set(plant_oids)), "duplicate plant trap OID"


def test_cooling_context_rebuilds_when_the_topology_arrives_later(tmp_path, plant_cache):
    """The cooling maps must follow the device inventory, not freeze at whatever
    it happened to be the first time something asked.

    This is the failure that invalidated a whole live fault campaign. A headless
    server starts with no topology; anything that touches the cooling context in
    that window — a poll of /plant/chiller-trips, say — cached it as {}. The
    upload that followed was ignored for the life of the process, so
    _compute_chw_penalty iterated an empty plant and the entire cooling model went
    inert. Nothing looked broken: the BACnet engine kept publishing plausible
    per-device values on top of a dead model, and only the staged-unit count gave
    it away (13 -> 4).

    Worst failure mode available here — a plant that looks like it is working."""
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    dm, topo = DeviceManager(), TopologyEngine()
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)

    # Someone asks before the topology exists.
    assert store._cooling_context()["plant_by_dc"] == {}

    # Topology arrives afterwards, exactly as an upload does.
    built = build_plant(tmp_path / "late", trains=TRAINS, servers=40,
                        installed_modules=MODULES, valves=True)
    for d in built.dm.get_all_devices():
        dm.add_device(d)
        topo.add_device(d)

    kinds = store._cooling_context()["plant_by_dc"].get(DC, {})
    assert kinds.get("chiller"), "the context must rebuild once devices exist"
    assert kinds.get("valve"), "and it must carry the header valves"


def test_a_capacity_alarm_never_becomes_a_cooling_loss(tmp_path, plant_cache):
    """A capacity alarm is the ANNOUNCEMENT of a shortfall the thermal model has
    already booked. Scoring it as lost capacity would double-charge it and close a
    positive feedback loop: shortfall raises the alarm, the alarm reads as lost
    capacity, which deepens the shortfall — and a plant at full load drives itself
    to the thermal ceiling on nothing but its own annunciation.

    END TO END, deliberately. The existing test in test_chw_loop asserts that
    _is_faulted and _is_alarmed both ignore capacity alarms, which is necessary
    but not sufficient — it says nothing about a path AROUND those two predicates.
    Re-running the fault campaign turned up exactly such a path in
    cooling_degraded, and no unit test caught it because none of them followed the
    alarm all the way to the penalty and the health flag."""
    p = _plant(tmp_path, plant_cache)
    lead = _lead_chillers(p)[0]
    plant_cache[lead] = {"Chiller_Running": 1.0, "Alarm_HighCHWSupply": 1.0}
    _hold(p)

    assert _pen(p) == 0.0, f"capacity alarm must not heat the room, got {_pen(p)}"
    assert p.store._cool_loss_frac[DC] == 0.0
    assert p.store.cooling_degraded(DC) is False, (
        "a machine outmatched by the load is not a plant that has failed")
    assert p.store._plant_status(p.name(lead)) == "ok", (
        "and it must not raise a plant trap either")


def test_capacity_alarms_stay_exempt_even_across_the_whole_plant(tmp_path, plant_cache):
    """The exemption has to hold at scale, and it has to stay DISCRIMINATING.

    Two failure modes sit either side of this: exempt too little and a fully
    loaded plant drives itself to the thermal ceiling on its own annunciation;
    exempt too much and a plant that has genuinely lost every chiller reports
    itself healthy. Both are checked here against the same fixture."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        plant_cache[f"CHL{i}-{DC}-CP"] = {"Chiller_Running": 1.0,
                                          "Alarm_HighCHWSupply": 1.0}
    _hold(p)
    assert _pen(p) == 0.0, "capacity alarms on every machine still cost nothing"
    assert p.store.cooling_degraded(DC) is False

    # Same machines, a genuine health alarm: now it IS short.
    plant_cache.clear()
    for i in range(1, TRAINS + 1):
        plant_cache[f"CHL{i}-{DC}-CP"] = {"Chiller_Running": 1.0,
                                          "Alarm_HighPressure": 1.0}
    _hold(p)
    assert _pen(p) > 1.0, "a real alarm on every chiller must heat the room"
    assert p.store.cooling_degraded(DC) is True


def test_a_healthy_plant_raises_no_plant_traps(tmp_path, plant_cache):
    """The N+1 spares must not annunciate for being spares.

    Caught by re-running the fault campaign against a live simulator: at baseline,
    with nothing faulted, PlantUnitStopped had fired four times — once for each
    cycled-off tower cell. A trap set that cries wolf on a healthy plant is worse
    than no trap set, because it teaches an operator to ignore it."""
    p = _plant(tmp_path, plant_cache)
    _hold(p, 20)

    standby = p.store._plant_standby_names
    assert standby, "fixture should stage something off"
    for d in p.dm.get_all_devices():
        if d.device_type.value in p.store._PLANT_FACT_TYPES:
            assert p.store._plant_status(d) == "ok", (
                f"{d.name} reports {p.store._plant_status(d)} on a healthy plant")


def test_staging_transitions_do_not_annunciate(tmp_path, plant_cache, monkeypatch):
    """A lead/lag handover is not a fault and must not raise a trap.

    Found by re-running the live campaign: a single chiller failover produced four
    PlantUnitStopped traps and four matching Cleared, one pair per train member.
    Units leave the standby set a tick or two before the BACnet plane publishes
    them as running, so each one is briefly "commanded on but reading stopped".

    Noise like that is worse than a missing trap — it is how an operator learns to
    ignore the alarm that matters."""
    import time as _t

    p = _plant(tmp_path, plant_cache)
    lead = _lead_chillers(p)[0]
    dev = p.name(lead)
    # A member mid-handover: commanded on (not standby) but still reading stopped.
    plant_cache[lead] = {"Chiller_Running": 0.0}
    p.store._plant_standby_names.discard(lead)
    assert p.store._plant_status(dev) == "ok", "a transition must stay quiet"

    # Held past the debounce, the same reading IS a stopped machine.
    base = _t.monotonic()
    monkeypatch.setattr(_t, "monotonic",
                        lambda: base + p.store._RUN_ALARM_S + 1.0)
    monkeypatch.setattr("core.device_state_store.time.monotonic",
                        lambda: base + p.store._RUN_ALARM_S + 1.0)
    p.store._plant_standby_names.discard(lead)
    assert p.store._plant_status(dev) == "stopped", (
        "a genuinely stopped machine must still annunciate")


def test_an_alarmed_unit_still_reports_its_alarm_once_staged_off(tmp_path, plant_cache):
    """The ordering trap behind the fix above.

    A unit that alarms is ranked unfit and staged off, so it lands in the standby
    set. If standby were tested first it would swallow the alarm, the trap would
    never fire, and — worse — the recovery could never fire either, leaving a stuck
    alarm on the NMS after the operator cleared it."""
    p = _plant(tmp_path, plant_cache)
    lead = _lead_chillers(p)[0]
    _alarm(plant_cache, lead, "Chiller_Running", "Alarm_HighPressure")
    _hold(p, 30)

    dev = p.name(lead)
    assert lead in p.store._plant_standby_names, "an alarmed unit gets staged off"
    assert p.store._plant_status(dev) == "hp_trip", "its alarm must still be reported"

    plant_cache.pop(lead, None)
    _hold(p, 5)
    assert p.store._plant_status(dev) == "ok", "clearing must land on ok so recovery fires"


def test_hot_inlet_costs_real_watts(tmp_path, plant_cache):
    """Chassis fan power is cube-law in speed and commonly adds 5–15 % to server
    draw at elevated inlet. It is the mechanism that makes a cooling event visible
    on the UPS.

    The campaign drove fans +925 rpm at 42.7 °C inlet with IT kW, PDU kW and UPS
    load flat within noise, in all 38 scenarios — the cooling failure never
    reached the power chain at all."""
    p = _plant(tmp_path, plant_cache)
    srv = next(d for d in p.dm.get_all_devices() if d.device_type.value == "server")
    srv.cpu_usage = 50.0
    srv.inlet_temp = 22.0
    cool = p.store._server_live_watts(srv)
    srv.inlet_temp = 43.0
    hot = p.store._server_live_watts(srv)

    assert hot > cool * 1.04, (
        f"a hot inlet must cost fan watts: {cool:.1f} W → {hot:.1f} W")


def test_server_throttles_instead_of_clamping(tmp_path, plant_cache):
    """A runaway needs an end state other than a clamp. Real silicon throttles
    well before its limit and the platform shuts down at it.

    The campaign never saw this because a 150 s window left the die ~80 % short of
    its target — the short window is a campaign limitation, but the absence of any
    protective response is the model gap."""
    p = _plant(tmp_path, plant_cache)
    srv = next(d for d in p.dm.get_all_devices() if d.device_type.value == "server")
    srv.cpu_usage = 90.0
    srv.cpu_temp = 92.0
    p.store._apply_thermal_protection(srv)
    assert srv.cpu_usage < 90.0, "above the throttle point the server must shed load"

    srv.cpu_temp = 96.0
    p.store._apply_thermal_protection(srv)
    assert srv.power_state == "Off", "at the critical limit the platform must shut down"


def _leaking_loop(tmp_path, plant_cache, tag, ticks=500, load=65.0):
    """A busy direct-to-chip loop with a fully-open leak on CDU1.

    Two CDUs, so the servers on the healthy one are the control: a leak that
    warmed the whole hall would be indistinguishable from the room-air path these
    tests exist to bypass.

    Load is PINNED on the leaking loop through the same device-override channel
    the live campaign drives, because the scenario only means anything on a busy
    loop. Direct-to-chip exists for dense, high-utilisation nodes; a leak on an
    idle loop legitimately should not trip anything, and the die model says so —
    a liquid die is 35 + 0.30 × cpu_usage, so the leak's +38 K only clears 90 °C
    once the node is actually working.

    65 % rather than something heroic: it lands the equilibrium near 92 °C, inside
    the throttle band and clear of the 95 °C shutdown, so the ramp is what gets
    exercised. Pinning higher parks the die against the shutdown boundary and the
    test turns on which side of a rounding step it lands.
    """
    random.seed(SEED)
    plant_cache.clear()
    p = build_plant(tmp_path / tag, trains=TRAINS, servers=40,
                    installed_modules=MODULES, crahs=CRAHS, cdus=2)
    for _ in range(SETTLE_TICKS):
        p.tick()
        _step_all(p)

    leaking = f"CDU1-{DC}-HA-R1-01"
    on_loop = p.store._cdu_loop_servers()[leaking]
    assert on_loop, "fixture should wire servers onto the CDU cold-plate loop"
    for d in p.dm.get_all_devices():
        if d.name in on_loop:
            # Both: the override suppresses the random walk (see _pin_value), the
            # field is what the die model actually reads. Setting only the override
            # leaves cpu_usage on its start value and the loop never gets busy.
            p.store.device_overrides.setdefault(d.id, {})["cpu_usage"] = load
            d.cpu_usage = load
    # Loop pressure below the intact 250 kPa sets leak intensity; 140 kPa is a
    # fully-open leak. The alarm on its own would only apply the 0.5 floor.
    plant_cache[leaking] = {"Alarm_Leak": 1.0, "TCS_Loop_Pressure": 140.0}
    # The die relaxes toward its target with _CPU_THERMAL_TAU_S = 150 s, so this
    # needs MINUTES of simulated time, not the 90 s the plant cases hold for. It
    # still runs in a fraction of a second — the live campaign needed a wall-clock
    # day for the same question only because the model integrates against real
    # elapsed time and has no acceleration knob.
    for _ in range(ticks):
        p.tick()
        _step_all(p)
    return p, on_loop


def test_a_cdu_leak_drives_the_die_into_thermal_protection(tmp_path, plant_cache):
    """The one mechanism in this model that can actually reach the throttle point.

    Two fault campaigns never exercised _apply_thermal_protection, and growing the
    fleet did not help: server inlet is hard-clamped at 45 °C and an air-cooled die
    is base + 0.9 × (inlet − 22), so the intake term saturates around +20.7 K
    however much IT load is added. The high-load run peaked at 76.7 °C — LOWER than
    the low-load run's 79.9 °C — because more servers is more heat, not a hotter
    inlet, once the clamp binds. The 90 °C path is unreachable by load alone.

    A direct-to-chip leak is the way in, and it is the honest one: the cold plate
    stops pulling heat out of the package, so the die climbs regardless of room
    air. That is how liquid-cooled servers actually cook."""
    p, on_loop = _leaking_loop(tmp_path, plant_cache, "leak")

    hot = [d for d in p.dm.get_all_devices() if d.name in on_loop]
    cool = [d for d in p.dm.get_all_devices()
            if d.device_type.value == "server" and d.name not in on_loop]
    peak = max(d.cpu_temp for d in hot)

    assert peak > p.store._CPU_THROTTLE_C, (
        f"a full leak must carry the die past {p.store._CPU_THROTTLE_C} °C, got {peak}")
    assert max(d.cpu_temp for d in cool) < p.store._CPU_THROTTLE_C, (
        "the leak must stay on its own loop — the healthy CDU's servers stay cool")


def test_a_hot_die_gets_a_protective_response(tmp_path, plant_cache):
    """Crossing the throttle point has to DO something.

    The die used to pin at the 95 °C clamp with no response at all, so a runaway
    had no end state: no capacity lost, no trap, nothing for an operator to
    rehearse. Throttling is progressive rather than a cliff, so the factor lands
    inside the ramp between the floor and full speed."""
    p, on_loop = _leaking_loop(tmp_path, plant_cache, "shed")

    throttled = {n: f for n, f in p.store._throttled.items() if n in on_loop}
    shutdown = {n for n in p.store._thermal_shutdown if n in on_loop}
    assert throttled or shutdown, "the leaking loop must show a protective response"
    for _n, factor in throttled.items():
        assert p.store._THROTTLE_FLOOR <= factor < 1.0, (
            f"{_n} throttle factor {factor} outside the ramp")
    for d in p.dm.get_all_devices():
        if d.name in shutdown:
            assert d.power_state == "Off", f"{d.name} tripped but still reads on"


def test_heating_rate_is_wall_clock_not_tick_count(tmp_path, plant_cache):
    """The same failure, held for the same number of SECONDS, must produce the
    same room temperature at any tick interval.

    Before the time-base fix it did not: at a 2 s tick the plant heated at half
    the rate, because the penalty added a fixed amount per tick while the
    condenser model beside it correctly scaled by elapsed time. A hall does not
    cool more slowly because the simulator is busy."""
    wall_s = 60

    def penalty_after(dt):
        p = _plant(tmp_path / f"dt{dt}", plant_cache, tick_interval=dt)
        for i in range(1, TRAINS + 1):
            _alarm(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running", "Alarm_FlowLoss")
        _hold(p, int(wall_s / dt))
        return _pen(p)

    fast, slow = penalty_after(1.0), penalty_after(2.0)
    assert slow == pytest.approx(fast, rel=0.05), (
        f"1 s tick reached {fast} K, 2 s tick reached {slow} K over the same "
        f"{wall_s} s of wall clock")


def test_silently_stopped_lead_fails_over_like_an_alarmed_one(tmp_path, plant_cache):
    """A machine that dies quietly must fail over exactly like one that
    complains.

    Today an ALARMED chiller is replaced within a tick and costs nothing, while
    the SAME machine merely stopped is left as lead and takes the plant down —
    the cooling-loss model counts it as gone while the staging model still ranks
    it fit. Trane Tracer, JCI Metasys and ASHRAE Guideline 36 all resolve this
    with a run-status proof timer: commanded on, status off, promote the
    standby."""
    p = _plant(tmp_path, plant_cache)
    lead = _lead_chillers(p)[0]
    _stop(plant_cache, lead, "Chiller_Running")
    _hold(p, 150)      # well past any realistic proof-timer dwell

    assert lead not in _lead_chillers(p), "a stopped lead must be replaced"
    assert _pen(p) < 0.5, f"failover should cost nothing, penalty was {_pen(p)}"


def test_shut_header_valve_is_not_a_no_op(tmp_path, plant_cache):
    """A shut chilled-water header valve is one of the few genuine single points
    of failure in a hydronic plant — it stops the loop dead. Today it moves
    nothing, because the point that reports it is not in the set the store reads
    as 'running'."""
    p = _plant(tmp_path, plant_cache, valves=True)
    _stop(plant_cache, f"VCHW-{DC}-CP", "Status_Modulating")
    _hold(p)

    assert p.store._cool_loss_frac[DC] > 0.0
    assert _pen(p) > 0.5, f"a shut header valve must cost cooling, got {_pen(p)}"


def test_condenser_valve_acts_on_the_condenser_loop(tmp_path, plant_cache):
    """The condenser-water valve sits in the rejection path, alongside the tower
    cells and the condenser pumps. Faulting it must warm the CONDENSER loop —
    today it throttles the chilled-water side instead and leaves the condenser
    untouched, which is the wrong loop entirely."""
    p = _plant(tmp_path, plant_cache, valves=True)
    cond_before = _cond(p)
    _alarm(plant_cache, f"VCW-{DC}-CP", "Status_Modulating", "Alarm_ActuatorFault")
    _hold(p)

    assert _cond(p) > cond_before + 1.0, (
        f"condenser valve fault must move the condenser loop; "
        f"{cond_before} → {_cond(p)}")


def test_degraded_reads_true_during_a_silent_total_loss(tmp_path, plant_cache):
    """The health predicate has to answer for the failure mode that has no alarm
    behind it. With every chiller stopped and no alarm raised, the plant is not
    delivering chilled water and must not report itself healthy."""
    p = _plant(tmp_path, plant_cache)
    for i in range(1, TRAINS + 1):
        _stop(plant_cache, f"CHL{i}-{DC}-CP", "Chiller_Running")
    _hold(p, 150)

    assert p.store.cooling_degraded(DC) is True

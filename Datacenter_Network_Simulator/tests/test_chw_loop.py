"""Evaporator-side (chilled-water) loop model.

The condenser side already had coverage (test_store_condenser / test_store_tower_bank).
This is the other half: what water the plant MAKES, how much of it moves, and what it
annunciates when the load outgrows it.
"""
import pytest

from conftest import DC, build_plant


def _settle(plant, n=3):
    for _ in range(n):
        plant.tick()


def _hold_shortfall(plant, penalty_c, ticks):
    """Hold the plant at a fixed cooling shortfall for *ticks* loop passes.

    The penalty is re-derived by _compute_chw_penalty on every full tick, so a test
    that just assigns it and calls tick() would have its value overwritten before
    the loop model ever saw it. Driving _compute_chw_loop directly pins the input
    and exercises exactly the thing under test."""
    for _ in range(ticks):
        plant.store._chw_pen[DC] = penalty_c
        plant.store._compute_chw_loop()


def test_supply_holds_setpoint_when_plant_keeps_up(plant):
    """CHW supply is a CONTROLLED variable. A plant with capacity to spare holds it
    on setpoint regardless of how the load moves — that is the whole point of a
    setpoint, and it is what makes a drift off it diagnostic."""
    from core.cooling_model import CHW_SETPOINT_C

    _settle(plant)
    assert plant.store._chw_supply_c[DC] == CHW_SETPOINT_C


def test_supply_rises_when_the_plant_is_short(plant):
    """Push the plant past its capacity and the supply temperature leaves setpoint.
    This is the single most diagnostic high-load symptom in a chiller plant."""
    from core.cooling_model import CHW_SETPOINT_C

    _settle(plant)
    _hold_shortfall(plant, 6.0, 1)        # the thermal model's booked shortfall
    assert plant.store._chw_supply_c[DC] > CHW_SETPOINT_C + 5.0


def test_supply_rise_is_capped(plant):
    """A starved plant makes warm water, not boiling water — the machines keep
    running and keep making SOME cooling. Without the cap, a runaway room penalty
    (which integrates to 28 °C) would report 35 °C 'chilled' water."""
    from core.cooling_model import CHW_SETPOINT_C, CHW_SUPPLY_RISE_MAX_C

    _settle(plant)
    _hold_shortfall(plant, 28.0, 1)
    assert (plant.store._chw_supply_c[DC]
            <= CHW_SETPOINT_C + CHW_SUPPLY_RISE_MAX_C + 1e-6)


def test_flow_tracks_load(tmp_path, plant_cache):
    """Flow is not a clock-driven walk: two plants at the same duty but different
    heat move different amounts of water."""
    small = build_plant(tmp_path / "s", servers=40, installed_modules=6)
    big = build_plant(tmp_path / "b", servers=400, installed_modules=6)
    _settle(small, 6)
    _settle(big, 6)
    assert big.store._chw_flow_lps[DC] > small.store._chw_flow_lps[DC] * 3


def test_delta_t_narrows_at_low_load(tmp_path, plant_cache):
    """Low-ΔT syndrome. Below the minimum-flow bypass the loop recirculates, so
    supply water mixes into the return and the MEASURED ΔT collapses — a real
    plant symptom, not a fault. At higher load the valves regain authority and ΔT
    comes back to design."""
    from core.cooling_model import CHW_DESIGN_DT_C

    # 600, not 400: at 400 the plant sits 0.014 of duty BELOW the minimum-flow
    # knee this test is about, so any change that moves live IT load by a couple of
    # percent flips the answer. A test balanced on a discontinuity measures the
    # discontinuity, not the behaviour.
    idle = build_plant(tmp_path / "i", servers=6, installed_modules=12)
    busy = build_plant(tmp_path / "b", servers=600, installed_modules=6)
    _settle(idle, 6)
    _settle(busy, 6)
    assert idle.store._chw_dt_c[DC] < busy.store._chw_dt_c[DC]
    assert busy.store._chw_dt_c[DC] == CHW_DESIGN_DT_C


def test_flow_conserves_heat(plant):
    """Q = ṁ·cp·ΔT must actually hold — the published flow and ΔT have to carry the
    live IT heat, or the loop is decorative."""
    from core.cooling_model import CP_WATER_KJ_KGK

    _settle(plant, 6)
    s = plant.store
    carried_kw = s._chw_flow_lps[DC] * CP_WATER_KJ_KGK * s._chw_dt_c[DC]
    # Both factors are published rounded to 2 dp, so allow the rounding error back.
    assert abs(carried_kw - s._it_live_by_dc[DC] / 1000.0) < 0.1


def test_running_chillers_publish_the_loop(plant):
    """The machines report the loop, not their own random walk."""
    _settle(plant, 6)
    s = plant.store
    lead = next(iter(plant.running_chillers()))
    pts = plant.auto_points(lead)
    # Points are published to 1 dp against a state kept at 2, so allow the
    # half-step; what matters is that the machine reports the loop, not a walk.
    assert pts["CHW_Supply_Temp"] == pytest.approx(s._chw_supply_c[DC], abs=0.05)
    assert pts["CHW_Return_Temp"] == pytest.approx(s._chw_return_c[DC], abs=0.05)
    assert pts["CHW_Flow"] > 0


def test_standby_chillers_publish_no_evaporator_points(plant):
    """A staged-off machine's evaporator is isolated behind its own stopped pump —
    it genuinely drifts, and the condenser model deliberately leaves it alone. If we
    published loop temperatures on it, a standby unit would advertise that it is
    making chilled water while stopped."""
    _settle(plant, 6)
    standby = plant.standby() & {t["chiller"] for t in plant.store._cooling_context()
                                 ["trains_by_dc"][DC]}
    assert standby, "fixture should stage at least one chiller off"
    for name in standby:
        assert "CHW_Flow" not in plant.auto_points(name)


def test_head_follows_the_affinity_law_on_the_similarity_line(tmp_path,
                                                              plant_cache):
    """Head ∝ speed² — but only along the pump's OWN similarity line, where flow
    scales with speed. That is what the affinity law actually claims, and stating
    it as "faster pump ⇒ more head" is wrong: a pump held at one speed while the
    valves open passes more water and develops LESS head, because it slides out
    along its curve. Comparing two plants' absolute head conflates the two.

    Pinned on the curve itself so it cannot be perturbed by whatever load the rest
    of the suite happens to leave behind."""
    from core.cooling_model import pump_head_frac

    for n in (0.35, 0.5, 0.75, 1.0):
        assert pump_head_frac(n, n) == pytest.approx(n * n), n

    # And at a FIXED speed, head must fall as the pump is asked to pass more.
    at_speed = [pump_head_frac(0.35, q) for q in (0.2, 0.35, 0.6, 1.0)]
    assert all(b < a for a, b in zip(at_speed, at_speed[1:])), at_speed

    # Live gauge set: whatever the load, the published head has to be the curve
    # evaluated at the published speed and flow — one operating point, not two.
    p = build_plant(tmp_path / "curve", servers=400, installed_modules=6)
    _settle(p, 8)
    tr = p.store._plant_trains_run[DC][0]
    pts = p.auto_points(tr["chwp"])
    assert 0.0 < pts["Diff_Pressure"] <= p.store._PUMP_DIFF_KPA * 1.25
    assert pts["Speed"] >= 35.0 - 0.05


def test_pump_discharge_equals_suction_plus_head(plant):
    """Internally consistent pressures — a gauge set that does not add up is worse
    than no gauge at all."""
    _settle(plant, 6)
    tr = plant.store._plant_trains_run[DC][0]
    pts = plant.auto_points(tr["chwp"])
    assert (pts["Discharge_Pressure"]
            == round(pts["Suction_Pressure"] + pts["Diff_Pressure"], 1))


def test_condenser_flow_exceeds_evaporator_flow(plant):
    """The condenser loop carries the IT heat PLUS the compressor work that moved
    it, which is exactly why a CW pump is always sized above its CHW counterpart."""
    _settle(plant, 6)
    tr = plant.store._plant_trains_run[DC][0]
    assert (plant.auto_points(tr["cwp"])["Flow"]
            > plant.auto_points(tr["chwp"])["Flow"])


def test_tower_makeup_tracks_rejection(tmp_path, plant_cache):
    """Makeup water is evaporation plus blowdown — reject more heat, consume more
    water. A tower whose makeup flow does not move with load is not modelling the
    only consumable the plant has."""
    idle = build_plant(tmp_path / "i", servers=6, installed_modules=12)
    busy = build_plant(tmp_path / "b", servers=400, installed_modules=6)
    _settle(idle, 6)
    _settle(busy, 6)

    def makeup(p):
        cell = next(iter(p.store._tower_running_now[DC]))
        return p.auto_points(cell)["Makeup_Flow"]

    assert makeup(busy) > makeup(idle)


# ── Load-driven annunciation ─────────────────────────────────────────────────

def test_high_chw_supply_needs_a_dwell(plant):
    """A stage change or a valve step briefly moves the leaving water. Real plant
    sequences carry a dwell for exactly that reason, so one tick off setpoint must
    NOT annunciate."""
    _settle(plant, 6)
    _hold_shortfall(plant, 6.0, 1)
    lead = next(iter(plant.running_chillers()))
    assert "Alarm_HighCHWSupply" not in plant.auto_points(lead)


def _dwell_ticks(plant):
    return int(plant.store._CHW_HIGH_S / plant.store._dt) + 2


def test_high_chw_supply_latches_after_the_dwell(plant):
    """Held off setpoint, it annunciates."""
    _settle(plant, 6)
    _hold_shortfall(plant, 6.0, _dwell_ticks(plant))
    lead = next(iter(plant.running_chillers()))
    assert plant.auto_points(lead)["Alarm_HighCHWSupply"] == 1.0


def test_high_chw_supply_clears_with_hysteresis(plant):
    """And comes back down — but only once the water is genuinely back, so a plant
    sitting on the deadband cannot chatter."""
    _settle(plant, 6)
    _hold_shortfall(plant, 6.0, _dwell_ticks(plant))
    lead = next(iter(plant.running_chillers()))
    assert lead in plant.store._chw_high_alarm

    _hold_shortfall(plant, 1.5, 1)     # inside the deadband, outside the clear
    assert lead in plant.store._chw_high_alarm, "must not clear on the deadband"

    _hold_shortfall(plant, 0.0, 1)
    assert lead not in plant.store._chw_high_alarm
    assert "Alarm_HighCHWSupply" not in plant.auto_points(lead)


def test_capacity_alarms_never_publish_a_zero(plant):
    """These points are merged OVER the operator's forced-alarm map, so writing an
    explicit 0.0 would stamp out a fault injected from the Limits tab. Absence is
    the clear."""
    from core.device_state_store import _CAPACITY_ALARMS

    _settle(plant, 6)
    for pts in plant.store._plant_auto_points.values():
        for key in _CAPACITY_ALARMS:
            assert pts.get(key, 1.0) != 0.0


def test_pump_low_flow_stays_a_fault(plant, plant_cache):
    """Alarm_LowFlow must NOT be exempted as a capacity alarm. On a pump it means a
    blocked strainer, a shut isolation valve or a failed impeller — a fault that
    genuinely costs cooling — so reusing it for 'healthy pump, too much load' would
    blind the store to the real thing."""
    from core.device_state_store import _CAPACITY_ALARMS

    assert "Alarm_LowFlow" not in _CAPACITY_ALARMS
    pump = f"CHWP1-{DC}-CP"
    plant_cache[pump] = {"Run_Status": 1.0, "Alarm_LowFlow": 1.0}
    assert plant.store._is_faulted(pump)

    # And the loop model does not raise it, at any load.
    _settle(plant, 6)
    for pts in plant.store._plant_auto_points.values():
        assert "Alarm_LowFlow" not in pts


def test_capacity_alarms_are_not_scored_as_lost_cooling(plant, plant_cache):
    """The critical one. A capacity alarm is the ANNOUNCEMENT of a shortfall the
    thermal model has already booked. Scoring it as lost capacity would both
    double-charge it and close a positive feedback loop — shortfall raises the
    alarm, the alarm is read as lost capacity, which deepens the shortfall — and a
    plant at full load would drive itself to the thermal ceiling on nothing but its
    own annunciation."""
    lead = f"CHL1-{DC}-CP"
    plant_cache[lead] = {"Chiller_Running": 1.0, "Alarm_HighCHWSupply": 1.0}
    assert not plant.store._is_faulted(lead)
    assert not plant.store._is_alarmed(lead)

    # A genuine health alarm on the same machine still counts.
    plant_cache[lead] = {"Chiller_Running": 1.0, "Alarm_HighPressure": 1.0}
    assert plant.store._is_faulted(lead)
    assert plant.store._is_alarmed(lead)


def _set_hall_air(plant, inlet_c, outlet_c):
    """Put every server in the hall at a chosen intake/exhaust pair, then run the
    air-side model against it. The full tick re-derives both from the thermal
    model, so pinning them and calling _compute_chw_loop directly is what isolates
    the return-air path."""
    for d in plant.dm.get_all_devices():
        if d.device_type.value == "server":
            d.inlet_temp, d.outlet_temp = inlet_c, outlet_c
    plant.store._compute_power_flow()
    plant.store._compute_chw_loop()


def test_crah_return_air_is_the_hot_aisle(tmp_path, plant_cache):
    """Return air is what the hot aisle hands back, so it sits between the
    cold-aisle intake and the rack exhaust — above the intake because it carries
    real heat, below the exhaust because bypass air dilutes it."""
    p = build_plant(tmp_path, servers=40, crahs=2)
    _settle(p, 6)
    _set_hall_air(p, 24.0, 40.0)
    ret = p.auto_points("CRAH1-DC1-HA-R1-01")["Return_Air_Temp"]
    assert 24.0 < ret < 40.0


def test_crah_return_air_tracks_the_load(tmp_path, plant_cache):
    """Widen the air-side ΔT — which is what a growing fleet does — and the return
    follows. A return-air point that does not move with load is decorative."""
    p = build_plant(tmp_path, servers=40, crahs=2)
    _settle(p, 6)
    _set_hall_air(p, 24.0, 34.0)
    cool = p.auto_points("CRAH1-DC1-HA-R1-01")["Return_Air_Temp"]
    _set_hall_air(p, 24.0, 46.0)
    hot = p.auto_points("CRAH1-DC1-HA-R1-01")["Return_Air_Temp"]
    assert hot > cool


def test_crah_return_air_is_not_published_without_a_measured_exhaust(tmp_path,
                                                                     plant_cache):
    """A hall whose servers report no exhaust warmer than their intake has no
    return-air temperature to publish. Restating the cold aisle as 'return air'
    would be a fabricated reading."""
    p = build_plant(tmp_path, servers=40, crahs=2)
    _settle(p, 6)
    _set_hall_air(p, 24.0, 0.0)
    assert "Return_Air_Temp" not in p.auto_points("CRAH1-DC1-HA-R1-01")


def test_crah_high_return_air_annunciates(tmp_path, plant_cache):
    """A hot aisle past the limit raises HighReturnAir — NOT HighTemp. The two mean
    opposite things: a high DISCHARGE means the unit has lost its ability to cool,
    a high RETURN means the unit is fine and the room feeding it is not. Only the
    second is a load symptom, and conflating them would also close a feedback loop
    (HighTemp warms the unit's own supply air, which warms the room)."""
    p = build_plant(tmp_path, servers=40, crahs=2)
    _settle(p, 6)
    _set_hall_air(p, 40.0, 58.0)
    pts = p.auto_points("CRAH1-DC1-HA-R1-01")
    assert pts["Return_Air_Temp"] >= p.store._CRAH_RETURN_ALARM_C
    assert pts["Alarm_HighReturnAir"] == 1.0
    assert "Alarm_HighTemp" not in pts


def test_pump_head_and_speed_come_from_one_number(tmp_path, plant_cache):
    """Head and Speed must be derived from the SAME value, not from two copies of
    the draw that are smoothed differently.

    They were both "from power", but the BACnet engine derived Speed from an
    EMA-smoothed copy while the loop model derived head from the raw target. At
    steady state they agreed, so the inconsistency only appeared during a load
    transient — and in one captured run head FELL while speed ROSE, which is
    impossible for a centrifugal pump. Publishing both from one number removes the
    class of bug rather than just the instance."""
    from core.cooling_model import (
        CHILLER_COP_RATED, CHW_DESIGN_DT_C, COND_DESIGN_RANGE_C, PLANT_MODULE_KW,
        pump_head_frac, water_flow_lps,
    )

    p = build_plant(tmp_path, servers=400, installed_modules=6)
    _settle(p, 6)
    trains = p.store._plant_trains_run[DC]
    tr = trains[0]
    # Design flow per pump is HARDWARE: the installed plant's design flow over the
    # number of trains built, not the number staged. Re-derived here rather than
    # read off the store, so this cross-checks the published gauge set instead of
    # restating it.
    n_all = len(p.store._cooling_context()["trains_by_dc"][DC])
    inst_kw = p.store._plant_installed_mods.get(DC, 1) * PLANT_MODULE_KW
    design = {
        tr['chwp']: water_flow_lps(inst_kw, CHW_DESIGN_DT_C) / n_all,
        tr['cwp']: water_flow_lps(inst_kw * (1.0 + 1.0 / CHILLER_COP_RATED),
                                  COND_DESIGN_RANGE_C) / n_all,
    }
    for name in (tr['chwp'], tr['cwp']):
        pts = p.auto_points(name)
        # Head is now the pump's operating POINT — speed and flow together, because
        # a centrifugal pump droops along its curve as it passes more water.
        implied = p.store._PUMP_DIFF_KPA * pump_head_frac(
            pts['Speed'] / 100.0, pts['Flow'] / design[name])
        # Tolerance is wider than the gauge's own precision on purpose: q_frac is
        # reconstructed from Flow as PUBLISHED (1 dp), and at design head that
        # rounding moves the curve result by a few tenths of a kPa.
        assert pts['Diff_Pressure'] == pytest.approx(implied, abs=0.6), name
        # And the drive frequency has to match the same speed.
        assert pts['VFD_Frequency'] == pytest.approx(pts['Speed'] / 2.0, abs=0.2), name


def test_pump_head_rises_monotonically_with_speed(tmp_path, plant_cache):
    """Whatever the load does, a pump turning faster must develop more head."""
    pts = []
    for n in (40, 200, 600):
        p = build_plant(tmp_path / f"n{n}", servers=n, installed_modules=6)
        _settle(p, 6)
        tr = p.store._plant_trains_run[DC][0]
        a = p.auto_points(tr['chwp'])
        pts.append((a['Speed'], a['Diff_Pressure']))
    pts.sort()
    assert all(b[1] >= a[1] for a, b in zip(pts, pts[1:])), pts


# ─────────────────────────────────────────────────────────────────────────────
#  Where published pump SPEED comes from
#
#  It used to be back-derived from the pump's metered draw. That draw is not the
#  pump's own curve value: the store normalises every running unit so each DC's
#  plant sums to cooling_electrical_w(). Inverting a normalised share answers
#  "what fraction of the plant's electrical bill is this machine", not "how fast
#  is it turning", and the two only agree while the staged unit count holds still.
#  Speed is now the drive's own commanded fraction, taken from the same thermal
#  duty that sets header flow.
# ─────────────────────────────────────────────────────────────────────────────
def _chwp(p):
    return p.auto_points(p.store._plant_trains_run[DC][0]["chwp"])


def test_a_running_pump_never_publishes_below_the_drive_floor(tmp_path, plant_cache):
    """A VFD has a turndown limit. Below it the pump is stopped, not slow — so a
    running pump reporting under PUMP_MIN_SPEED is a reading no drive can produce.
    The old back-derivation published 24.6 % against a 35 % floor."""
    from core.cooling_model import PUMP_MIN_SPEED
    for servers in (1, 6, 40):
        p = build_plant(tmp_path / f"idle{servers}", servers=servers,
                        installed_modules=12)
        _settle(p, 8)
        a = _chwp(p)
        assert a["Speed"] >= PUMP_MIN_SPEED * 100.0 - 0.05, (servers, a["Speed"])


def test_speed_tracks_the_water_not_the_electrical_share(tmp_path, plant_cache):
    """The regression in one assertion. A pump moving several times the water must
    turn faster — even though staging a second train cut its share of the plant's
    normalised electrical total, which is what the old derivation read."""
    idle = build_plant(tmp_path / "i", servers=6, installed_modules=6)
    busy = build_plant(tmp_path / "b", servers=600, installed_modules=6)
    _settle(idle, 8)
    _settle(busy, 8)
    i, b = _chwp(idle), _chwp(busy)
    assert b["Flow"] > i["Flow"]
    assert b["Speed"] > i["Speed"], (i, b)


def test_header_flow_and_speed_never_disagree(tmp_path, plant_cache):
    """Flow ∝ speed on a fixed system curve, so the two must move together across
    every load the plant sees.

    HEADER flow, not per-pump: two pumps in parallel on one ΔP setpoint each carry
    half, so per-pump flow halves at unchanged speed — that is the sharing, not a
    disagreement. What must never happen is the header moving more water while the
    drives report turning slower, which is precisely what the old power-derived
    speed published."""
    seen = []
    for servers, mods in ((6, 12), (40, 12), (200, 6), (600, 6)):
        p = build_plant(tmp_path / f"s{servers}m{mods}", servers=servers,
                        installed_modules=mods)
        _settle(p, 8)
        pumps = [t["chwp"] for t in p.store._plant_trains_run[DC] if t.get("chwp")]
        header = sum(p.auto_points(n).get("Flow", 0.0) for n in pumps)
        seen.append((round(header, 2), p.auto_points(pumps[0])["Speed"]))
    seen.sort()
    assert all(b[1] >= a[1] - 0.05 for a, b in zip(seen, seen[1:])), seen


def test_vfd_frequency_agrees_with_the_published_speed(tmp_path, plant_cache):
    """50 Hz mains at 100 %. A drive whose Hz and % disagree is one of them wrong."""
    p = build_plant(tmp_path / "f", servers=600, installed_modules=6)
    _settle(p, 8)
    a = _chwp(p)
    assert a["VFD_Frequency"] == pytest.approx(a["Speed"] * 0.5, abs=0.15)


def test_speed_and_operating_point_describe_one_machine(tmp_path, plant_cache):
    """Published speed and published flow must place the pump at the SAME point on
    its curve, because head is read off that point.

    Both are now derived from one hardware constant — the installed plant's design
    flow per train — so a pump's speed fraction and its flow fraction agree. When
    they did not, a pump published 50.9 % speed while passing 0.68 of design, and
    the head came off a curve position the drive was never at. In the worst case
    the two disagreed enough to put the operating point off the end of the curve
    entirely, and the published differential pressure was zero at a perfectly
    ordinary load.

    Above the turndown floor only: ON the floor the pump deliberately runs faster
    than the load needs, pushing minimum flow around the bypass, and speed
    exceeding the flow fraction there is the bypass working."""
    from core.cooling_model import (
        CHW_DESIGN_DT_C, PLANT_MODULE_KW, PUMP_MIN_SPEED, water_flow_lps,
    )
    checked = 0
    for servers, mods in ((200, 6), (400, 6), (600, 6), (600, 12), (900, 6)):
        p = build_plant(tmp_path / f"pt{servers}m{mods}", servers=servers,
                        installed_modules=mods)
        _settle(p, 8)
        n_all = len(p.store._cooling_context()["trains_by_dc"][DC])
        inst_kw = p.store._plant_installed_mods.get(DC, 1) * PLANT_MODULE_KW
        per_pump_design = water_flow_lps(inst_kw, CHW_DESIGN_DT_C) / n_all
        pts = p.auto_points(p.store._plant_trains_run[DC][0]["chwp"])
        speed = pts["Speed"] / 100.0
        if speed <= PUMP_MIN_SPEED + 1e-6:
            continue                       # on the bypass floor — see docstring
        assert speed == pytest.approx(pts["Flow"] / per_pump_design, abs=0.02), \
            (servers, mods, speed, pts["Flow"] / per_pump_design)
        assert pts["Diff_Pressure"] > 0.0, (servers, mods)
        checked += 1
    assert checked >= 3, "fixture should exercise pumps off the floor"


def test_the_same_heat_moves_the_same_water_whatever_is_installed(tmp_path,
                                                                  plant_cache):
    """Flow is set by the heat (Q = ṁ·cp·ΔT), so installed capacity cannot change
    how much water a given load moves — only how many pumps share it.

    This deliberately does NOT assert that the bigger plant turns slower. Its pumps
    are larger, but it also stages down to fewer trains, so the one that runs
    carries the whole header: twice the flow through a twice-as-big pump is the
    same fraction of design and therefore the same speed. Both effects are real and
    they cancel. What must hold is that per-pump speed is per-pump flow over the
    hardware's design flow — pinned by
    test_speed_and_operating_point_describe_one_machine."""
    small = build_plant(tmp_path / "small", servers=600, installed_modules=6)
    large = build_plant(tmp_path / "large", servers=600, installed_modules=12)
    _settle(small, 8)
    _settle(large, 8)

    def header(p):
        pumps = [t["chwp"] for t in p.store._plant_trains_run[DC] if t.get("chwp")]
        return sum(p.auto_points(n).get("Flow", 0.0) for n in pumps)

    assert header(large) == pytest.approx(header(small), rel=0.05)
    # And the bigger plant really did stage down, which is why the speeds match.
    assert (len(large.store._plant_trains_run[DC])
            < len(small.store._plant_trains_run[DC]))


# ─────────────────────────────────────────────────────────────────────────────
#  Condenser flow: a ΔT-setpoint loop, not a ΔP one
#
#  The two loops run different control strategies. Evaporator pumps ride a
#  differential-pressure setpoint, so their flow floors at the bypass and the
#  measured ΔT collapses at light load. Condenser pumps ride a condenser-ΔT
#  setpoint, so flow tracks the heat and the RANGE holds near design. Sizing the
#  condenser flow with the EVAPORATOR's bypass curve inverted that — the range
#  moved and the flow held, and since flow = Q/(cp·ΔT) a collapsing range
#  inflates flow. The published figure came out as reject_kw/duty, which is
#  nearly load-independent because both terms rise together.
# ─────────────────────────────────────────────────────────────────────────────
def _cw_flow(p):
    return sum(p.auto_points(t["cwp"]).get("Flow", 0.0)
               for t in p.store._plant_trains_run[DC] if t.get("cwp"))


def test_condenser_flow_rises_with_rejected_heat(tmp_path, plant_cache):
    """The defect in one assertion. Live, the DC rejecting 153.9 kW moved 12.6 l/s
    while the one rejecting 111.4 kW moved 14.2 — more heat, less water."""
    seen = []
    for servers in (40, 200, 600, 900):
        p = build_plant(tmp_path / f"cw{servers}", servers=servers,
                        installed_modules=6)
        _settle(p, 8)
        seen.append((p.store._it_live_by_dc[DC], _cw_flow(p)))
    assert all(b[1] >= a[1] for a, b in zip(seen, seen[1:])), seen
    assert seen[-1][1] > seen[0][1] * 2, "flow must actually track heat, not creep"


def test_condenser_range_holds_at_design_under_load(tmp_path, plant_cache):
    """What a ΔT-controlled loop is FOR. Once the pumps are off their floor they
    have authority, and holding the range is the whole control objective."""
    from core.cooling_model import COND_DESIGN_RANGE_C
    for servers in (200, 600, 900):
        p = build_plant(tmp_path / f"r{servers}", servers=servers,
                        installed_modules=6)
        _settle(p, 8)
        assert p.store._cond_range_c[DC] == pytest.approx(COND_DESIGN_RANGE_C,
                                                          abs=0.05), servers


def test_condenser_flow_always_exceeds_evaporator_flow(tmp_path, plant_cache):
    """Thermodynamics, at EVERY load — the condenser carries the IT heat plus the
    compressor work that moved it. This is what the old range-narrowing was really
    guarding, and it survives on the pump turndown floor instead: a lightly loaded
    plant floors both loops rather than distorting one loop's range."""
    for servers, mods in ((1, 12), (6, 12), (40, 6), (200, 6), (600, 6), (900, 6)):
        p = build_plant(tmp_path / f"x{servers}m{mods}", servers=servers,
                        installed_modules=mods)
        _settle(p, 8)
        assert _cw_flow(p) > p.store._chw_flow_lps[DC], (servers, mods)


def test_condenser_flow_does_not_track_the_electrical_duty(tmp_path, plant_cache):
    """Guards the specific wrong input. _plant_duty is cooling-electrical over
    running nameplate; dividing by it made flow ≈ reject/duty, so a plant whose
    load doubled published almost the same flow. Doubling the heat must move
    materially more water."""
    small = build_plant(tmp_path / "cwsmall", servers=300, installed_modules=6)
    big = build_plant(tmp_path / "cwbig", servers=600, installed_modules=6)
    _settle(small, 8)
    _settle(big, 8)
    ratio = _cw_flow(big) / max(1e-6, _cw_flow(small))
    assert ratio > 1.5, ratio

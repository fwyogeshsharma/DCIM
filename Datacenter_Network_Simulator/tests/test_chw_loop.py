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

    idle = build_plant(tmp_path / "i", servers=6, installed_modules=12)
    busy = build_plant(tmp_path / "b", servers=400, installed_modules=6)
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


def test_pump_head_follows_the_affinity_law(tmp_path, plant_cache):
    """Head ∝ speed², so a pump throttled back develops far less differential
    pressure. A pump reporting design head at 40 % speed is physically impossible."""
    idle = build_plant(tmp_path / "i", servers=6, installed_modules=12)
    busy = build_plant(tmp_path / "b", servers=400, installed_modules=6)
    _settle(idle, 6)
    _settle(busy, 6)

    def head(p):
        tr = p.store._plant_trains_run[DC][0]
        return p.auto_points(tr["chwp"])["Diff_Pressure"]

    assert head(busy) > head(idle)


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
    from core.cooling_model import pump_head_frac

    p = build_plant(tmp_path, servers=400, installed_modules=6)
    _settle(p, 6)
    tr = p.store._plant_trains_run[DC][0]
    for name in (tr['chwp'], tr['cwp']):
        pts = p.auto_points(name)
        implied = p.store._PUMP_DIFF_KPA * pump_head_frac(pts['Speed'] / 100.0)
        assert pts['Diff_Pressure'] == pytest.approx(implied, abs=0.2), name
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

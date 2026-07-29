"""Plant header instruments (CHWS / CHWR / CWS / CWR / CTB / FLOW).

These are DeviceType.SENSOR devices, like a rack air probe, but they are wired into
the water loops rather than the cold aisle. Two things have to hold: they must
report the LIVE loop, and the cold-aisle rules must not be applied to them.
"""
import pytest

from conftest import DC, build_plant


@pytest.fixture
def probed(tmp_path, plant_cache):
    p = build_plant(tmp_path, servers=200, installed_modules=6, probes=True)
    for _ in range(6):
        p.tick()
        for d in p.dm.get_all_devices():
            p.store._step_device(d)
            p.store._step_ext_state(d)
    return p


def _probe(p, code):
    return next(d for d in p.dm.get_all_devices() if d.name == f"{code}-{DC}-CP")


# ── Role dispatch ────────────────────────────────────────────────────────────

def test_role_comes_from_the_name_and_the_model(tmp_path, plant_cache):
    """Both are required. The model prefix says 'this is a plant instrument'; the
    leading name code says WHICH point. A rack probe must satisfy neither."""
    from core.device_manager import Device, DeviceType, Vendor
    from core.device_state_store import _probe_role

    def mk(name, model):
        return Device(name=name, device_type=DeviceType.SENSOR,
                      vendor=Vendor.VERTIV, ip_address="10.9.9.9", model_name=model)

    assert _probe_role(mk("CHWS-DC1-CP", "Plant CHW Supply Temp")) == "chw_supply"
    assert _probe_role(mk("FLOW-DC1-CP", "Plant CHW Flow Meter")) == "chw_flow"
    # Right model family, unknown point code → not a point we publish.
    assert _probe_role(mk("XYZ-DC1-CP", "Plant CHW Supply Temp")) is None
    # A rack air probe that happens to sit in a rack whose name starts with a code.
    assert _probe_role(mk("CHWS-DC1-CP", "Raritan DPX2-T3H1")) is None


# ── They read the loop ───────────────────────────────────────────────────────

def test_temperature_probes_report_their_loop(probed):
    """Each thermowell reads the header it is welded into — the same values the
    chillers and tower cells are publishing, not a room-air walk."""
    s = probed.store
    assert _probe(probed, "CHWS").inlet_temp == pytest.approx(
        s._chw_supply_c[DC], abs=0.2)
    assert _probe(probed, "CHWR").inlet_temp == pytest.approx(
        s._chw_return_c[DC], abs=0.2)
    assert _probe(probed, "CWS").inlet_temp == pytest.approx(
        s._cond_water_c[DC], abs=0.2)
    assert _probe(probed, "CWR").inlet_temp == pytest.approx(
        s._cond_water_c[DC] + s._COND_RANGE_C, abs=0.2)
    # The basin IS the cold well the condenser supply header draws from, which is
    # why the tower cells publish Basin_Temp and Cond_Water_Out as one value.
    assert _probe(probed, "CTB").inlet_temp == pytest.approx(
        s._cond_water_c[DC], abs=0.2)


def test_flow_meter_reports_loop_flow(probed):
    assert _probe(probed, "FLOW").airflow == pytest.approx(
        probed.store._chw_flow_lps[DC], rel=0.02)


def test_probes_move_with_load(tmp_path, plant_cache):
    """The whole point of the fix: a bigger fleet moves the meter."""
    def flow(servers):
        p = build_plant(tmp_path / f"n{servers}", servers=servers,
                        installed_modules=6, probes=True)
        for _ in range(6):
            p.tick()
            for d in p.dm.get_all_devices():
                p.store._step_device(d)
        return _probe(p, "FLOW").airflow

    assert flow(400) > flow(40) * 3


def test_probes_report_no_air_channels(probed):
    """A thermowell has no humidity and no dew point, and a flow meter has no
    temperature. Leaving the air-probe walk running on them would publish readings
    with no instrument behind them."""
    for code in ("CHWS", "CHWR", "CWS", "CWR", "CTB"):
        d = _probe(probed, code)
        assert d.humidity == 0.0 and d.dewpoint == 0.0
        assert d.airflow == 0.0
    flow = _probe(probed, "FLOW")
    assert flow.inlet_temp == 0.0 and flow.humidity == 0.0


def test_probes_are_exempt_from_the_quiet_baseline_scrub(probed):
    """The quiet-mode scrub exists to tame a random WALK. These readings are not
    walked — they are the computed loop — so clamping them would not suppress a
    phantom alarm, it would falsify a measurement: condenser return water sits
    above the 31.9 °C cold-aisle ceiling by design."""
    probed.store.autonomous_faults = False
    cwr = _probe(probed, "CWR")
    # A warm, humid site legitimately puts the condenser return here; the fixture's
    # Chicago weather is milder, so pin it rather than depend on the season.
    cwr.inlet_temp = 35.5
    probed.store._scrub_numeric_faults(cwr)
    assert cwr.inlet_temp == 35.5
    assert cwr.humidity == 0.0, "a thermowell must not be given a humidity reading"


def test_rack_probes_are_still_scrubbed(probed):
    """Surgical, again — the cold-aisle walk still gets tamed."""
    from core.device_manager import Device, DeviceType, Vendor

    rack = Device(name="SEN1-DC1-HA-R2-01", device_type=DeviceType.SENSOR,
                  vendor=Vendor.VERTIV, ip_address="10.7.7.7",
                  model_name="Vertiv Geist GTHD")
    rack.inlet_temp = 40.0
    probed.store._ext_states[rack.name] = {}
    probed.store._scrub_numeric_faults(rack)
    assert rack.inlet_temp == 31.9


def test_probes_serve_one_point_on_entity_sensor_mib(probed):
    """A header instrument is ONE point. Falling through to the generic probe
    tables would publish humidity and dew point beside it — a hygrometer that does
    not exist, reporting a value it cannot measure."""
    from core.snmprec_generator import SNMPRecGenerator, _ENTITY_SENSOR

    chws = SNMPRecGenerator._plant_probe_entries(_probe(probed, "CHWS"))
    assert chws is not None
    oids = {o for o, _t, _v in chws}
    assert oids == {f"{_ENTITY_SENSOR}.{i}.1" for i in range(1, 6)}
    types = dict((o, v) for o, _t, v in chws)
    assert types[f"{_ENTITY_SENSOR}.1.1"] == "8"      # entPhySensorType = celsius

    flow = SNMPRecGenerator._plant_probe_entries(_probe(probed, "FLOW"))
    assert dict((o, v) for o, _t, v in flow)[f"{_ENTITY_SENSOR}.1.1"] == "12"  # other

    # A rack air probe keeps its vendor probe tables.
    assert SNMPRecGenerator._plant_probe_entries(
        _rack_probe_device()) is None


def _rack_probe_device():
    from core.device_manager import Device, DeviceType, Vendor

    return Device(name="SEN1-DC1-HA-R2-01", device_type=DeviceType.SENSOR,
                  vendor=Vendor.VERTIV, ip_address="10.7.7.7",
                  model_name="Vertiv Geist GTHD")


def test_probe_readings_reach_ext_state(probed):
    """The DCIM/rule side reads water temperature as WATER, not as room ambient."""
    assert probed.store._ext_states["CHWS-DC1-CP"]["water_temp"] > 0
    assert probed.store._ext_states["CHWS-DC1-CP"]["probe_role"] == "chw_supply"
    assert "water_temp" not in probed.store._ext_states["FLOW-DC1-CP"]
    assert probed.store._ext_states["FLOW-DC1-CP"]["water_flow_lps"] > 0


# ── The cold-aisle rules must not touch them ─────────────────────────────────

def _engine():
    """A live rule engine with the shipped ruleset. It reports through an action
    callback rather than a return value, and no-ops entirely unless enabled, so
    both are wired here; fired rule names land in eng.fired."""
    from core.rule_engine import RuleEngine
    from core.trap_rules import DEFAULT_RULES

    eng = RuleEngine()
    eng.fired = []
    eng.set_enabled(True)
    eng.set_action_callback(lambda action: eng.fired.append(action.rule.rule_name))
    for r in DEFAULT_RULES:
        eng.add_rule(r)
    return eng


def _fire(eng, t=0.0, **kw):
    from core.fact_model import DeviceFact

    base = dict(device_id="P1", device_type="sensor", ip_address="10.9.9.9",
                timestamp=t)
    base.update(kw)
    eng.fired.clear()
    eng.evaluate_fact(DeviceFact(**base))
    return set(eng.fired)


def _fire_held(eng, seconds=400.0, step=30.0, **kw):
    """Feed the same fact across enough simulated time to satisfy a rule's dwell.
    Rule durations are measured against the fact's own timestamp, so a burst of
    same-instant facts would never clear a duration gate."""
    fired = set()
    t = 0.0
    while t <= seconds:
        fired |= _fire(eng, t=t, **kw)
        t += step
    return fired


def test_condenser_return_water_does_not_raise_a_room_alarm():
    """35 °C condenser return water is a HEALTHY design condition. Before the fix
    it looked exactly like a 35 °C hot aisle to the ambient rules, so a perfectly
    normal plant annunciated a room over-temperature on every tick."""
    eng = _engine()
    fired = _fire(eng, model_name="Plant CW Return Temp",
                  ambient_temp=35.0, water_temp=35.0)
    assert "SensorAmbientTempHigh" not in fired


def test_a_real_rack_probe_still_alarms():
    """The exclusion must be surgical — the cold aisle still gets its rules."""
    eng = _engine()
    fired = _fire(eng, model_name="Raritan DPX2-T3H1", ambient_temp=35.0)
    assert "SensorAmbientTempHigh" in fired


def test_prefix_exclusion_matches_the_whole_family():
    from core.rule_engine import _model_excluded

    assert _model_excluded("Plant CHW Supply Temp", ["Plant *"])
    assert _model_excluded("Plant CT Basin Temp", ["Plant *"])
    assert not _model_excluded("Raritan DPX2-T3H1", ["Plant *"])
    assert _model_excluded("Exactly This", ["Exactly This"])


def test_model_filters_survive_a_json_round_trip():
    """Both filters have to persist. A ruleset exported and re-imported without the
    exclusion would silently re-apply the cold-aisle rules to the water loops —
    and model_names was already being dropped before this change."""
    from core.rule_engine import Rule

    for r in _engine()._rules.values():
        again = Rule.from_dict(r.to_dict())
        assert again.model_names == r.model_names
        assert again.model_names_exclude == r.model_names_exclude


# ── The water rules DO fire ──────────────────────────────────────────────────

def test_chw_supply_off_setpoint_alarms():
    """The load symptom. Held above setpoint, the supply thermowell annunciates."""
    fired = _fire_held(_engine(), device_id="CHWS-DC1-CP",
                       model_name="Plant CHW Supply Temp", water_temp=12.0)
    assert "CHWSupplyTempHigh" in fired


def test_chw_supply_on_setpoint_is_quiet():
    fired = _fire_held(_engine(), device_id="CHWS-DC1-CP",
                       model_name="Plant CHW Supply Temp", water_temp=7.0)
    assert not fired


def test_chw_supply_high_needs_the_dwell():
    """One sample off setpoint is a valve step, not a plant that has run out of
    capacity. The rule must not annunciate on it."""
    eng = _engine()
    assert not _fire(eng, device_id="CHWS-DC1-CP",
                     model_name="Plant CHW Supply Temp", water_temp=12.0)


def test_condenser_water_alarms_at_the_head_pressure_limit():
    """The CW alarm point is where the chillers begin unloading on head pressure,
    not at any room-air number — a humid afternoon legitimately puts this loop in
    the low 30s."""
    quiet = _fire_held(_engine(), device_id="CWS-DC1-CP",
                       model_name="Plant CW Supply Temp", water_temp=30.0)
    assert not quiet
    hot = _fire_held(_engine(), device_id="CWS-DC1-CP",
                     model_name="Plant CW Supply Temp", water_temp=38.0)
    assert "CWSupplyTempHigh" in hot


def test_flow_alarm_is_loss_of_flow_not_low_load():
    """A quiet plant riding its minimum-flow bypass is not a fault. What the
    evaporator flow switch trips on is flow going away."""
    low = _fire_held(_engine(), device_id="FLOW-DC1-CP",
                     model_name="Plant CHW Flow Meter", water_flow_lps=6.0)
    assert not low
    lost = _fire_held(_engine(), device_id="FLOW-DC1-CP",
                      model_name="Plant CHW Flow Meter", water_flow_lps=0.1)
    assert "CHWFlowLoss" in lost

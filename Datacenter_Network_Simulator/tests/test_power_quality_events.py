"""Utility power-quality events reach the meters a real sag would reach.

A voltage sag or a frequency excursion is a property of a BUS. The EV2 meter
used to roll its own dice for both (and for a sensor fault), per meter, per
tick: ~50 uncorrelated MAJOR alarms a month on every meter, including meters on
UPS-output panels that no utility sag can reach. What must hold now:

* left alone, a meter never raises a low-side PQ alarm or a sensor fault;
* a bus sag supplied by the caller latches Alarm_Undervoltage on the debounce
  and clears after it, and an operator force still works;
* the store sends a utility sag to every utility-fed meter of a site on the
  same tick, and to none behind a UPS;
* DCIM_SIM_PQ_SAGS_PER_DAY=0 turns the events off.
"""
import json
import random
from pathlib import Path

import pytest

from core.bacnet_telemetry import EV2TelemetryEngine
from core.device_state_store import classify_ev2_supply

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"
LOW_SIDE = ("Alarm_Undervoltage", "Alarm_UnderFrequency", "Alarm_SensorFault")


def _engine():
    return EV2TelemetryEngine(circuits=12, frequency_hz=50.0, rated_kw=40.0)


# ── the meter ────────────────────────────────────────────────────────────────

def test_a_meter_left_alone_never_raises_a_low_side_alarm():
    random.seed(1234)
    eng = _engine()
    for _ in range(20_000):
        v = eng.tick(1.0, live_kw=25.0)
        for a in LOW_SIDE:
            assert v[a] == 0.0, a


def test_a_meter_left_alone_with_a_grid_frequency_never_alarms_either():
    random.seed(99)
    eng = _engine()
    for i in range(5_000):
        v = eng.tick(1.0, live_kw=25.0, grid_freq_hz=50.0 + 0.04 * ((i % 7) - 3) / 3)
        assert v["Alarm_UnderFrequency"] == 0.0
        assert abs(v["Line_Frequency"] - 50.0) < 0.05


def test_the_meter_reads_the_grid_frequency_it_is_given():
    eng = _engine()
    v = eng.tick(1.0, live_kw=25.0, grid_freq_hz=49.973)
    assert abs(v["Line_Frequency"] - 49.973) <= 0.0021


def _settle(eng, n=20):
    for _ in range(n):
        eng.tick(1.0, live_kw=25.0)


@pytest.mark.parametrize("depth", [0.80, 0.85, 0.88])
def test_a_bus_sag_held_for_the_debounce_latches_and_then_clears(depth):
    """The alarm derivation needs ALARM_DEBOUNCE consecutive ticks below 90 %,
    so ONE tick of sag cannot latch it; the store holds an event that long."""
    eng = _engine()
    _settle(eng)
    deb = EV2TelemetryEngine.ALARM_DEBOUNCE
    assert deb == 2
    # One tick alone is debounced away.
    v = eng.tick(1.0, live_kw=25.0, bus_sag=depth)
    assert v["Alarm_Undervoltage"] == 0.0
    assert all(v[f"Voltage_Ph{p}"] < 230 * 0.90 for p in "ABC")
    _settle(eng)
    # Held for the debounce: latches on the last tick of the event.
    for k in range(deb):
        v = eng.tick(1.0, live_kw=25.0, bus_sag=depth)
    assert v["Alarm_Undervoltage"] == 1.0
    # All three phases sag together (a bus event), within a volt or so.
    ph = [v[f"Voltage_Ph{p}"] for p in "ABC"]
    assert max(ph) - min(ph) < 3.0
    # ... and it clears within a few ticks of the bus recovering.
    cleared = None
    for k in range(6):
        v = eng.tick(1.0, live_kw=25.0)
        if v["Alarm_Undervoltage"] == 0.0:
            cleared = k
            break
    assert cleared is not None and cleared <= 2
    assert v["Alarm_VoltageImbalance"] == 0.0


def test_a_forced_undervoltage_drives_the_sag_it_reports():
    eng = _engine()
    _settle(eng)
    for _ in range(3):
        v = eng.tick(1.0, live_kw=25.0, forced={"Alarm_Undervoltage"})
    assert v["Alarm_Undervoltage"] == 1.0
    assert all(v[f"Voltage_Ph{p}"] < 230 * 0.90 for p in "ABC")
    for _ in range(5):
        v = eng.tick(1.0, live_kw=25.0)
    assert v["Alarm_Undervoltage"] == 0.0


def test_forced_underfrequency_and_sensor_fault_still_work():
    eng = _engine()
    _settle(eng)
    for _ in range(3):
        v = eng.tick(1.0, live_kw=25.0, grid_freq_hz=50.0,
                     forced={"Alarm_UnderFrequency", "Alarm_SensorFault"})
    assert v["Alarm_UnderFrequency"] == 1.0 and v["Line_Frequency"] < 49.0
    assert v["Alarm_SensorFault"] == 1.0 and v["Voltage_THD"] == 0.0
    v = eng.tick(1.0, live_kw=25.0, grid_freq_hz=50.0)
    assert v["Alarm_UnderFrequency"] == 0.0 and v["Alarm_SensorFault"] == 0.0


# ── the classification ───────────────────────────────────────────────────────

def test_classification_on_a_small_graph():
    id_type = {"U": "utility_feed", "S": "switchgear", "A": "ats", "G": "generator",
               "S2": "switchgear", "UPS": "ups", "R": "rpp", "M": "mcc"}
    parents = {"S": ["U"], "A": ["S", "S2"], "S2": ["G"], "UPS": ["A"],
               "R": ["UPS"], "M": ["A"], "U": [], "G": []}
    assert classify_ev2_supply("R", parents, id_type) == "ups"
    assert classify_ev2_supply("UPS", parents, id_type) == "ups"
    assert classify_ev2_supply("M", parents, id_type) == "utility"
    assert classify_ev2_supply("S", parents, id_type) == "utility"
    assert classify_ev2_supply("S2", parents, id_type) == "local"
    # A panel with a UPS-backed feed AND a raw utility feed sees the sag.
    parents2 = dict(parents, R=["UPS", "S"])
    assert classify_ev2_supply("R", parents2, id_type) == "utility"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    data = json.loads(TOPO.read_text(encoding="utf-8-sig"))
    dm, topo = DeviceManager(), TopologyEngine()
    topo.from_dict(data)
    for d in topo.get_all_devices():
        dm.add_device(d)
    store = DeviceStateStore(dm, topo, str(tmp_path_factory.mktemp("pq")), tick_interval=1.0)
    ctx = store._power_context()
    return store, ctx


def _by_name(ctx, cls=None):
    return {ctx["ev2_ip_name"][ip]: c for ip, c in ctx["ev2_supply"].items()
            if cls is None or c == cls}


def test_shipped_topology_rack_panels_are_behind_ups_and_the_boards_are_not(site):
    _, ctx = site
    sup = _by_name(ctx)
    assert sup, "no EV2 meters classified"
    for dc in ("DC1", "DC2"):
        for room in ("HA", "HB", "NR"):
            rack = [n for n in sup if f"-{dc}-{room}-" in n]
            assert rack and all(sup[n] == "ups" for n in rack), (dc, room)
        assert sup[f"EV21-{dc}-UR"] == "utility"
        assert sup[f"EV21-{dc}-MR"] == "utility" and sup[f"EV22-{dc}-MR"] == "utility"
        # The paralleling board is fed by the gensets only.
        assert sup[f"EV21-{dc}-GR"] == "local"
    # The network room meter the alarms were seen on can never see a utility sag.
    assert sup["EV21-DC2-NR-R2-01"] == "ups"


def _cities(store):
    from core.device_state_store import _dc_city_map
    return _dc_city_map(store._dm.get_all_devices(), blank_dc="?")


def _reset(store, rate, ledger=None):
    store._pq_sags_per_day = rate
    store._pq_events = {}
    store._ev2_sag_by_ip = {}


def test_all_exposed_meters_of_a_site_sag_on_the_same_tick(site):
    from core import fault_ledger

    store, ctx = site
    _reset(store, 1e12)                       # p(tick) == 1
    store._dt = 1.0
    store._step_power_quality(ctx, _cities(store))
    sag = store._ev2_sag_by_ip
    exposed = {ip for ip, c in ctx["ev2_supply"].items() if c == "utility"}
    assert set(sag) == exposed
    for dc in ("DC1", "DC2"):
        ips = [ip for ip in exposed if ctx["ev2_ip_dc"][ip] == dc]
        assert len({sag[ip] for ip in ips}) == 1          # one bus event, one depth
        assert 0.80 <= sag[ips[0]] <= 0.88
    # No meter behind a UPS is touched; all of them follow the grid frequency.
    for ip, c in ctx["ev2_supply"].items():
        if c == "ups":
            assert ip not in sag
            assert ip in store._ev2_freq_by_ip
    # The event is held for the debounce and then ends.
    for _ in range(EV2TelemetryEngine.ALARM_DEBOUNCE - 1):
        store._pq_sags_per_day = 0.0
        store._step_power_quality(ctx, _cities(store))
        assert set(store._ev2_sag_by_ip) == exposed
    store._step_power_quality(ctx, _cities(store))
    assert store._ev2_sag_by_ip == {}
    # Ledgered once per site, as context, opened and closed.
    ents = [e for e in fault_ledger.get_ledger().query(kind="context")["entries"]
            if (e.get("params") or {}).get("context") == "utility_sag"]
    assert sorted(e["params"]["datacenter"] for e in ents) == ["DC1", "DC2"]
    for e in ents:
        assert e["end"] is not None and e["end_reason"] == "cleared"
        assert e["params"]["meters"] and all(
            "-HA-" not in m and "-NR-" not in m for m in e["params"]["meters"])


def test_zero_rate_yields_no_events(site, monkeypatch):
    from core import device_state_store as dss

    monkeypatch.setenv("DCIM_SIM_PQ_SAGS_PER_DAY", "0")
    assert dss._pq_sags_per_day() == 0.0
    store, ctx = site
    _reset(store, dss._pq_sags_per_day())
    store._dt = 1.0
    for _ in range(5_000):
        store._step_power_quality(ctx, _cities(store))
        assert store._ev2_sag_by_ip == {}
    assert store._pq_events == {}


def test_the_rate_env_var_parses(monkeypatch):
    from core import device_state_store as dss

    monkeypatch.delenv("DCIM_SIM_PQ_SAGS_PER_DAY", raising=False)
    assert dss._pq_sags_per_day() == 0.1
    monkeypatch.setenv("DCIM_SIM_PQ_SAGS_PER_DAY", "2.5")
    assert dss._pq_sags_per_day() == 2.5
    monkeypatch.setenv("DCIM_SIM_PQ_SAGS_PER_DAY", "lots")
    assert dss._pq_sags_per_day() == 0.1


def test_a_ledger_failure_does_not_break_the_tick(site, monkeypatch):
    from core import fault_ledger

    class _Broken:
        def open(self, *a, **k):
            raise OSError("disk gone")

        def close(self, *a, **k):
            raise OSError("disk gone")

    monkeypatch.setattr(fault_ledger, "get_ledger", lambda: _Broken())
    store, ctx = site
    _reset(store, 1e12)
    store._dt = 1.0
    store._step_power_quality(ctx, _cities(store))
    assert store._ev2_sag_by_ip                   # the event still happened
    store._pq_sags_per_day = 0.0
    for _ in range(3):
        store._step_power_quality(ctx, _cities(store))
    assert store._ev2_sag_by_ip == {}

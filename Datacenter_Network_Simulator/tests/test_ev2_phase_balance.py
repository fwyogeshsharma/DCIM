"""EV2 phase currents follow how each branch is really wired.

The server-hall RPPs feed 3 network racks on single-phase strips (AP8941) and 5
compute racks on 3-phase 22 kW strips (AP8886). A 3-phase strip draws its load
over all three legs; booking each one onto a single leg in turn left phase C a
whole rack short and the panel reading 29 % current imbalance - and put 37 A
on a leg that a 32 A pole could not have carried.
"""
from core.bacnet_telemetry import EV2TelemetryEngine

# Server Hall A, RPP-A, as the live hall reads it: Ckt01-03 network, 04-08 compute.
HALL_KW = [0.17, 0.18, 0.21, 8.5, 8.7, 8.3, 4.9, 4.9]
HALL_POLES = [1, 1, 1, 3, 3, 3, 3, 3]


def _settle(poles, kw=HALL_KW, ticks=60):
    eng = EV2TelemetryEngine(circuits=42, rated_kw=40.0)
    vals = {}
    for _ in range(ticks):
        vals = eng.tick(30.0, live_kw=sum(kw), circuit_kw=list(kw), circuit_poles=poles)
    return vals


def _imbalance(v):
    ph = [v["Current_PhA"], v["Current_PhB"], v["Current_PhC"]]
    avg = sum(ph) / 3
    return max(abs(x - avg) for x in ph) / avg * 100


def test_three_phase_strips_keep_the_panel_balanced():
    v = _settle(HALL_POLES)
    assert _imbalance(v) < 5.0


def test_the_panel_total_still_sums_its_branches():
    v = _settle(HALL_POLES)
    assert abs(v["Panel_Total_kW"] - sum(HALL_KW)) < 0.05 * sum(HALL_KW)
    branches = sum(v[f"Ckt{i:02d}_kW"] for i in range(1, 9))
    assert abs(v["Panel_Total_kW"] - branches) < 0.01


def test_a_three_phase_branch_reads_its_per_leg_current():
    v = _settle(HALL_POLES)
    # 8.5 kW over three 230 V legs at PF ~0.9-0.99 is 12-14 A a leg, not 37 A on one.
    assert 11.0 < v["Ckt04_Current"] < 14.5
    # A single-phase branch still carries its whole load on its one leg.
    assert v["Ckt01_Current"] > 0.5


def test_without_pole_data_the_old_single_phase_booking_is_unchanged():
    # Absent poles (an older caller) = every branch single-phase, rotated A/B/C.
    v = _settle(None)
    assert _imbalance(v) > 20.0


# ── which branches are 3-phase ───────────────────────────────────────────────
from types import SimpleNamespace

from core.device_manager import DeviceType
from core.device_state_store import DeviceStateStore


def _store_with(devices):
    store = DeviceStateStore.__new__(DeviceStateStore)
    store._dm = SimpleNamespace(get_device=lambda nid: devices.get(nid))
    return store


def test_branch_poles_follow_the_pdu_sku_and_the_load_class():
    s = _store_with({
        "ap8886": SimpleNamespace(device_type=DeviceType.PDU, model_name="APC AP8886"),
        "ap8941": SimpleNamespace(device_type=DeviceType.PDU, model_name="APC AP8941"),
        "mystery": SimpleNamespace(device_type=DeviceType.PDU, model_name="Nobody 1"),
        "ats": SimpleNamespace(device_type=DeviceType.ATS, model_name="ASCO 7000"),
        "chiller": SimpleNamespace(device_type=DeviceType.CHILLER, model_name="Carrier 19DV"),
        "sensor": SimpleNamespace(device_type=DeviceType.SENSOR, model_name="Plant CHW Flow Meter"),
    })
    assert s._branch_poles("ap8886") == 3      # 22 kW 3-phase 400 V strip
    assert s._branch_poles("ap8941") == 1      # 30 A single-phase strip
    assert s._branch_poles("mystery") == 1     # unknown SKU: the common small strip
    assert s._branch_poles("ats") == 3
    assert s._branch_poles("chiller") == 3
    assert s._branch_poles("sensor") == 1      # an instrument, not plant
    assert s._branch_poles("gone") == 1

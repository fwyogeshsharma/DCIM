"""A tripped PDU breaker is a POWER event, not an annunciation.

The Simulate-Fault menu offered exactly one PDU Condition (Load High) and pushed
everything else into one-shot Events — including the breaker. But every PDU trap
rule in `core/trap_rules.py` is a raise/clear PAIR: state changes on the status
points, thresholds with hysteresis on the numeric ones. That is the signature of a
condition, and real intelligent PDUs (Raritan PX3, APC AP89xx, Vertiv Geist,
ServerTech) hold all of them as standing states over SNMP.

The breaker is the one that matters most, because it is the only PDU fault with a
consequence beyond the trap: the outlets go dead. A real branch breaker also
LATCHES — it stays open until somebody physically resets it — so nothing here may
self-heal.

Modelled at the whole-PDU level, i.e. the input breaker rather than one branch
pole: cords attach to the PDU in the power graph, so per-branch energization is not
representable. That overstates the blast radius of a branch trip and exactly
matches an input-breaker or feed loss.
"""
import pytest

from core.device_manager import Device, DeviceManager, DeviceType, Vendor
from core.device_state_store import DeviceStateStore
from core.topology_engine import TopologyEngine


def _dev(dm, name, dtype, ip, watts=0):
    d = Device(name=name, device_type=DeviceType(dtype), vendor=Vendor.SUPERMICRO,
               ip_address=ip, power_draw_w=watts)
    d.datacenter, d.room = "DC1", "Hall A"
    dm.add_device(d)
    return d


@pytest.fixture
def rack(tmp_path):
    """One PDU feeding two servers: one single-corded, one dual-corded off a second
    PDU. That split is the whole point — a trip must drop the first and not the
    second, which is what dual-cording is for."""
    dm, topo = DeviceManager(), TopologyEngine()
    made = {
        "PDUA": _dev(dm, "PDUA-DC1-HA-R1", "pdu", "10.0.1.1"),
        "PDUB": _dev(dm, "PDUB-DC1-HA-R1", "pdu", "10.0.1.2"),
        "SRV1": _dev(dm, "SRV01-DC1-HA-R1-01", "server", "10.1.0.1", 500),
        "SRV2": _dev(dm, "SRV02-DC1-HA-R1-02", "server", "10.1.0.2", 500),
    }
    for d in made.values():
        topo.add_device(d)
    topo.add_link(made["PDUA"].id, made["SRV1"].id, layer="power")   # single-corded
    topo.add_link(made["PDUA"].id, made["SRV2"].id, layer="power")   # A cord
    topo.add_link(made["PDUB"].id, made["SRV2"].id, layer="power")   # B cord
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    return store, made


def _energize(store):
    store._compute_power_flow()
    return store._energized


def test_a_healthy_pdu_feeds_everything(rack):
    store, made = rack
    en = _energize(store)
    for key in ("PDUA", "PDUB", "SRV1", "SRV2"):
        assert en.get(made[key].id, True), f"{key} should be live on a healthy rack"


def test_a_tripped_breaker_takes_the_strip_and_its_single_corded_load_dead(rack):
    """The consequence that makes this a condition rather than an event."""
    store, made = rack
    store.set_pdu_condition(made["PDUA"].id, "breaker_trip", True)
    en = _energize(store)

    assert en.get(made["PDUA"].id) is False, "the tripped strip must go dead"
    assert en.get(made["SRV1"].id) is False, (
        "a single-corded server on the tripped PDU must lose power")


def test_a_dual_corded_load_rides_the_trip(rack):
    """The behaviour worth rehearsing: 2N cording survives one strip."""
    store, made = rack
    store.set_pdu_condition(made["PDUA"].id, "breaker_trip", True)
    en = _energize(store)

    assert en.get(made["PDUB"].id, True), "the healthy strip is untouched"
    assert en.get(made["SRV2"].id, True), (
        "a dual-corded server must ride a single PDU trip on its other PSU")


def test_the_trip_latches_and_clears_only_on_command(rack):
    """A real branch breaker stays open until somebody resets it."""
    store, made = rack
    store.set_pdu_condition(made["PDUA"].id, "breaker_trip", True)
    for _ in range(50):
        _energize(store)
    assert store._energized.get(made["PDUA"].id) is False, "nothing may self-heal"
    assert store.get_pdu_conditions(made["PDUA"].id) == ["breaker_trip"]

    store.set_pdu_condition(made["PDUA"].id, "breaker_trip", False)
    en = _energize(store)
    assert en.get(made["PDUA"].id, True), "clearing must restore the strip"
    assert en.get(made["SRV1"].id, True)
    assert store.get_pdu_conditions(made["PDUA"].id) == []


def test_the_status_point_agrees_with_the_power(rack):
    """The published point and the energization must read the same state, so a DCIM
    polling pdu_breaker_status cannot disagree with what the outlets are doing.

    `_force_states_nominal` runs AFTER the ext-state walk and scrubs the PDU status
    points back to nominal every tick — that is what keeps the random walk from
    raising autonomous traps. The override-backed conditions survive it because
    `_apply_ext_overrides` re-pins them afterwards; a `pducond` has no such
    re-application, so the strip went dead while the point still read "ok" and the
    ok->tripped rule never fired. Caught live: load and outlet current fell to 0
    with breaker_status unchanged and no trap."""
    store, made = rack
    pdu = made["PDUA"]
    store.set_pdu_condition(pdu.id, "breaker_trip", True)
    for _ in range(3):
        store._step_device(pdu)
        store._step_ext_state(pdu)
        store._force_states_nominal(pdu)      # the pass that used to undo it
    from core.device_state_store import _ext_state_cache
    assert _ext_state_cache[pdu.name]["pdu_breaker_status"] == "tripped"

    store.set_pdu_condition(pdu.id, "breaker_trip", False)
    for _ in range(3):
        store._step_device(pdu)
        store._step_ext_state(pdu)
        store._force_states_nominal(pdu)
    assert _ext_state_cache[pdu.name]["pdu_breaker_status"] == "ok", (
        "clearing must let the scrub take the point back to nominal")


def test_every_pdu_condition_is_offered_as_a_condition():
    """The menu gap itself. Each PDU trap rule is a raise/clear pair, so each needs
    a Condition — one Load High entry left the rest as one-shot Events."""
    from api.routers.devices import FAULT_MAP

    pdu_faults = {k: v for k, v in FAULT_MAP.items() if "pdu" in v["types"]}
    assert len(pdu_faults) >= 12, (
        f"expected the full PDU condition set, got {sorted(pdu_faults)}")
    for key in ("pdu_breaker_trip", "pdu_ground_fault", "pdu_smoke",
                "pdu_outlet_fail", "pdu_volt_low", "pdu_pf_low"):
        assert key in pdu_faults, f"{key} missing from the Conditions group"
    # Breaker trip is the only one with a power consequence, so it is the only one
    # that may carry a pducond; the rest must pin their backing metric.
    assert pdu_faults["pdu_breaker_trip"].get("pducond") == "breaker_trip"
    assert all("override" in v or "metric" in v
               for k, v in pdu_faults.items() if k != "pdu_breaker_trip")

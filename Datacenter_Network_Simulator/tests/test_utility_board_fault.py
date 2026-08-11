"""A dead utility MAIN board must start the gensets, same as a grid outage.

A transfer switch senses voltage on its own normal terminals. It has no telemetry
from the utility and no idea why those terminals went dark — a grid outage, a
tripped main breaker on the board above it, a bus fault, an open service feeder
all read as one thing: under-voltage on normal. ASCO 7000 / Eaton ATC-900 all
behave this way, and NFPA 110 Level 1 Type 10 times the engine start from that
sensing point, not from anything the utility says.

The store used to drive its sequencer from the INJECTED utility-outage flag alone
(`_utility_failed[dc]`), while the energization walk separately honoured switchgear
faults. So a bus fault on SWGR1-DC1-UR killed both ATSes, dropped both UPSes to
battery and stopped the whole mechanical plant, while the sequencer sat in state
"utility" and two healthy gensets stayed in standby — a site blackout that the
emergency system exists to prevent, and one no real ATS would allow.

These tests pin the sensing point: the board's health, not the flag.
"""
import pytest

from tests.conftest import DC, build_plant, _device
from core.power_transfer import GEN_RUNNING, GEN_STANDBY


def _wire_electrical(fx):
    """utility → utility board → ATS → MCC → plant, plus gensets → paralleling board.

    The genset board is a SEPARATE bus (as it is on site), which is what makes the
    emergency source survive a fault on the utility board.
    """
    def mk(name, dtype, ip, room="UPS Room"):
        d = _device(fx.dm, f"{name}-{DC}-UR", dtype, ip, 0, room=room)
        fx.topo.add_device(d)                 # both registries — the cascade walks topo
        return d

    e = {
        "util":  mk("UTIL1", "utility_feed", "10.0.9.1"),
        "swgr":  mk("SWGR1", "switchgear", "10.0.9.2"),
        "gswgr": mk("SWGR2", "switchgear", "10.0.9.3", room="Generator Room"),
        "gen1":  mk("GEN1", "generator", "10.0.9.4", room="Generator Room"),
        "gen2":  mk("GEN2", "generator", "10.0.9.5", room="Generator Room"),
        "ats":   mk("ATS1", "ats", "10.0.9.6"),
        "mcc":   mk("MCC1", "mcc", "10.0.9.7"),
    }
    for a, b in (("util", "swgr"), ("gen1", "gswgr"), ("gen2", "gswgr"),
                 ("swgr", "ats"), ("gswgr", "ats"), ("ats", "mcc")):
        fx.topo.add_link(e[a].id, e[b].id, None, None, layer="power")
    for d in fx.dm.get_all_devices():
        if d.device_type.value in ("chiller", "pump", "cooling_tower", "crah"):
            fx.topo.add_link(e["mcc"].id, d.id, None, None, layer="power")
    return e


def _tick(fx, e):
    """One tick in the ticker's real order, including the two steps the cooling
    fixture skips: the transfer sequencer and the per-device electrical telemetry."""
    fx.store._compute_leak_heat()
    fx.store._compute_cond_loop()
    fx.store._compute_chw_penalty()
    fx.store._step_transfer()
    fx.store._compute_unpowered_loads()
    fx.store._compute_power_flow()
    fx.store._compute_chw_loop()
    for d in e.values():
        fx.store._step_ext_state(d)


def _ats_state(fx, dev):
    from core.device_state_store import _ext_state_cache
    return _ext_state_cache.get(dev.name, {})


def _settle(fx, e, seconds):
    for _ in range(int(seconds)):
        _tick(fx, e)


@pytest.fixture
def elec(tmp_path, plant_cache):
    fx = build_plant(tmp_path, crahs=2, tick_interval=1.0)
    e = _wire_electrical(fx)
    _tick(fx, e)
    return fx, e


def test_baseline_is_utility_with_gens_in_standby(elec):
    fx, _ = elec
    st = fx.store._transfer.status(DC)
    assert (st.state, st.source, st.gen_status) == ("utility", "normal", GEN_STANDBY)


def test_bus_fault_on_utility_board_starts_the_gensets(elec):
    """The fault the sequencer used to be blind to."""
    fx, e = elec
    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)

    # TDNE 3 s: still sensing, engines NOT yet cranking. A momentary sag must not
    # crank, which is the entire reason the delay exists.
    _settle(fx, e, 2)
    assert fx.store._transfer.status(DC).state == "sense"
    assert fx.store._transfer.status(DC).gen_status == GEN_STANDBY

    # NFPA 110 Type 10: emergency source available within 10 s, transfer at ~12 s.
    _settle(fx, e, 12)
    st = fx.store._transfer.status(DC)
    assert st.state == "emergency"
    assert st.source == "emergency"
    assert st.source_live
    assert st.gen_status == GEN_RUNNING
    assert st.gen_at_voltage


def test_breaker_trip_on_utility_board_starts_the_gensets(elec):
    """A tripped main is the same under-voltage at the ATS as a faulted bus."""
    fx, e = elec
    fx.store.set_swgr_condition(e["swgr"].id, "breaker_trip", True)
    _settle(fx, e, 14)
    assert fx.store._transfer.status(DC).gen_status == GEN_RUNNING


def test_mechanical_plant_comes_back_on_the_generator(elec):
    """The point of starting: the plant restarts in staged blocks, not never."""
    fx, e = elec
    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
    _settle(fx, e, 6)                                  # dead bus, engines cranking
    assert fx.store._transfer.status(DC).mech_blocks_on == 0
    assert fx.store._plant_unpowered_names

    _settle(fx, e, 40)                                 # past the +25 s third block
    assert fx.store._transfer.status(DC).mech_blocks_on == 3
    assert not fx.store._plant_unpowered_names


def test_ats_annunciates_the_lost_normal_source(elec):
    """The switch must not report a healthy normal source off a dead board."""
    fx, e = elec
    assert _ats_state(fx, e["ats"]).get("ats_normal_available") == "yes"

    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
    _settle(fx, e, 14)
    st = _ats_state(fx, e["ats"])
    assert st.get("ats_normal_available") == "no"
    assert st.get("ats_normal_voltage") == 0.0
    assert st.get("ats_position") == "emergency"
    assert "source_lost" in fx.store.get_ats_conditions(e["ats"].id)


def test_utility_meter_still_reads_healthy(elec):
    """The SERVICE is fine — only the board below it is dead. A revenue meter that
    reported a failed feed here would send an operator hunting the wrong fault."""
    from core.device_state_store import _ext_state_cache
    fx, e = elec
    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
    _settle(fx, e, 14)
    assert _ext_state_cache.get(e["util"].name, {}).get("util_status") == "normal"
    assert fx.store.get_electrical_status()[DC]["utility_ok"] is True
    assert fx.store.get_electrical_status()[DC]["normal_source_ok"] is False


def test_fault_on_the_generator_board_does_not_crank(elec):
    """The paralleling board is not the ATS's normal source. Faulting it costs the
    site its emergency source, not its utility — the engines have no reason to run."""
    fx, e = elec
    fx.store.set_swgr_condition(e["gswgr"].id, "bus_fault", True)
    _settle(fx, e, 14)
    st = fx.store._transfer.status(DC)
    assert (st.state, st.gen_status) == ("utility", GEN_STANDBY)


def test_clearing_the_fault_retransfers(elec):
    """Utility board back = normal source available again, so the ATS starts its
    TDEN. It stays on the genset until that expires (a flapping source must not
    beat the load up), so the assertion is the state machine leaving 'emergency'."""
    fx, e = elec
    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
    _settle(fx, e, 14)
    assert fx.store._transfer.status(DC).state == "emergency"

    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", False)
    _settle(fx, e, 2)
    st = fx.store._transfer.status(DC)
    assert st.state == "retransfer"
    assert st.source == "emergency"          # still carrying load on the genset


def test_failed_gensets_leave_the_site_dark(elec):
    """A board fault with no startable genset is still a blackout — the fix must not
    manufacture an emergency source that isn't there."""
    fx, e = elec
    for g in (e["gen1"], e["gen2"]):
        fx.store.set_gen_failed(g.id, True)
    fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
    _settle(fx, e, 20)
    st = fx.store._transfer.status(DC)
    assert not st.source_live
    assert st.gen_status == GEN_STANDBY
    assert st.mech_blocks_on == 0

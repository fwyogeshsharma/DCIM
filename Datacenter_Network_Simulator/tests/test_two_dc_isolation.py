"""Two sites, one store: the per-DC state must not leak between them.

The rest of the cooling suite builds ONE datacenter, and production runs two. That
gap is not cosmetic — nearly every cooling field on the store is keyed per DC
(`_chw_pen`, `_cool_loss_frac`, `_plant_trains_run`, `_run_proof_s`,
`_chw_pump_frac`, `_tower_reject`, …) and several passes run once PER SITE. Any of
those that walks a whole map instead of its own site's slice will corrupt the other
site, and a single-DC fixture cannot see it happen.

It already did. `_accrue_run_proof` expired every timer not in the DC it was called
for, so the second site's pass deleted the first site's run-proof timers on every
tick. `_run_unproven` then answered False forever on whichever site was not
processed last, and `cooling_degraded` reported a healthy plant through a total
silent loss of chilled water — on the live topology, while
`test_degraded_reads_true_during_a_silent_total_loss` passed in the single-DC
fixture. It took a live probe to find, which is exactly what this file is for.

The two sites are identical by construction, so any asymmetry these tests find is
the store leaking state, never the fixture favouring one.
"""
import random

import pytest

from conftest import build_two_dc_plant

SEED = 20260804
A, B = "DC1", "DC2"          # A is built first — the site the old bug corrupted
TRAINS = 3
SERVERS = 200
MODULES = 6
CRAHS = 4

SETTLE_TICKS = 4
HOLD_TICKS = 90


@pytest.fixture
def sites(tmp_path, plant_cache):
    random.seed(SEED)
    p = build_two_dc_plant(tmp_path, trains=TRAINS, servers=SERVERS,
                           installed_modules=MODULES, crahs=CRAHS)
    for _ in range(SETTLE_TICKS):
        p.tick()
    return p


def _hold(p, ticks=HOLD_TICKS):
    for _ in range(ticks):
        p.tick()


def _stop_chillers(cache, dc):
    """Silent stop: run status reads 0, no alarm behind it. The failure mode the
    run-status proof timer exists to catch, and the one the live probe used."""
    for i in range(1, TRAINS + 1):
        cache[f"CHL{i}-{dc}-CP"] = {"Chiller_Running": 0.0}


def test_both_sites_start_healthy_and_symmetric(sites):
    """Baseline. If the two sites do not agree when nothing is wrong, every
    asymmetry below would be meaningless."""
    s = sites.store
    for dc in (A, B):
        assert sites.running_chillers(dc), f"{dc} should have a train staged on"
        assert s.cooling_degraded(dc) is False
        assert s._cool_loss_frac.get(dc, 0.0) == 0.0
    assert s._chw_pen.get(A, 0.0) == pytest.approx(s._chw_pen.get(B, 0.0), abs=0.2)
    assert s._chw_flow_lps[A] == pytest.approx(s._chw_flow_lps[B], abs=0.2)


def test_a_silent_total_loss_is_seen_at_the_site_it_happened_to(sites, plant_cache):
    """The F14 regression, end to end through real ticks rather than by calling
    the predicate directly.

    Faults site A specifically, because A is built first and it was the site the
    old bug corrupted: B's pass ran second and wiped A's run-proof timers before
    anything could read them."""
    s = sites.store
    _stop_chillers(plant_cache, A)
    _hold(sites)

    assert s.cooling_degraded(A) is True, (
        "a total silent loss must register at the site it happened to")
    assert s.cooling_degraded(B) is False, (
        "the healthy site must not be dragged down with it")


def test_the_other_site_keeps_its_run_proof_timers(sites, plant_cache):
    """Directly on the mechanism, so a future refactor that reintroduces a global
    sweep fails here with a readable reason rather than as a mystery three tests
    away."""
    s = sites.store
    _stop_chillers(plant_cache, A)
    _hold(sites)

    silent_a = [n for n in s._run_proof_s if f"-{A}-" in n]
    assert silent_a, (
        "site A's commanded-but-silent chillers should hold a proof timer; an "
        "empty map means another site's pass expired them")
    assert all(s._run_unproven(n) for n in silent_a)
    assert not [n for n in s._run_proof_s if f"-{B}-" in n], (
        "site B is healthy and should hold no failure-to-start timers at all")


def test_a_failure_at_one_site_leaves_the_other_sites_loop_alone(sites, plant_cache):
    """Thermal and hydraulic state are per-DC too. A dead plant at A must not warm
    B's chilled water, shrink its flow, or spend its cooling capacity."""
    s = sites.store
    before = {"pen": s._chw_pen.get(B, 0.0), "flow": s._chw_flow_lps[B],
              "dt": s._chw_dt_c[B], "loss": s._cool_loss_frac.get(B, 0.0)}
    _stop_chillers(plant_cache, A)
    _hold(sites)

    assert s._chw_pen[A] > 1.0, "site A should be running away"
    assert s._chw_pen.get(B, 0.0) == pytest.approx(before["pen"], abs=0.2)
    assert s._chw_flow_lps[B] == pytest.approx(before["flow"], abs=0.3)
    assert s._chw_dt_c[B] == pytest.approx(before["dt"], abs=0.3)
    assert s._cool_loss_frac.get(B, 0.0) == pytest.approx(before["loss"], abs=0.01)


def test_a_failure_at_one_site_leaves_the_other_sites_plant_running(sites,
                                                                    plant_cache):
    """Staging, standby membership and per-unit draw are per-DC as well. B's
    machines must still be commanded, still turning and still drawing."""
    s = sites.store
    _stop_chillers(plant_cache, A)
    _hold(sites)

    assert sites.running_chillers(B), "site B must still have a train staged on"
    b_draw = sum(kw for n, kw in s._plant_power_by_name.items() if f"-{B}-" in n)
    a_draw = sum(kw for n, kw in s._plant_power_by_name.items() if f"-{A}-" in n)
    assert b_draw > 0.0, "site B's plant should still be drawing power"
    assert b_draw > a_draw, (
        f"the dead site should draw less than the healthy one: {a_draw} vs {b_draw}")


def test_faulting_the_second_site_is_symmetric(sites, plant_cache):
    """The same failure at B must read the same way it did at A. If only one
    direction works, some pass is still order-dependent — which is the shape of
    every bug this file exists to catch."""
    s = sites.store
    _stop_chillers(plant_cache, B)
    _hold(sites)

    assert s.cooling_degraded(B) is True
    assert s.cooling_degraded(A) is False
    assert s._chw_pen[B] > 1.0
    assert s._chw_pen.get(A, 0.0) < 1.0

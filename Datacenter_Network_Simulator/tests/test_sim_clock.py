"""Simulated time must track the wall clock.

The ticker sleeps `_tick_interval` and THEN does the tick's work, so the real period
is always interval + work. Billing every accumulator the configured constant made
simulated time run slow by the work fraction (~80 % of real, measured live), and the
error grew with the topology — so run-hours, stage timers, transfer sequencing, UPS
autonomy, energy registers and SNMP uptime all drifted by a load-dependent amount.
"""
import time
import types

import pytest

from core.device_state_store import DeviceStateStore as Store

INTERVAL = 0.05
WORK = 0.03          # 60 % of the interval again on top
TICKS = 30


@pytest.fixture
def clock():
    """Bare object carrying just the attributes _advance_clock touches."""
    o = types.SimpleNamespace()
    o._tick_interval = INTERVAL
    o._last_tick_t = None
    o._dt = INTERVAL
    o._DT_MAX_MULT = Store._DT_MAX_MULT
    return o


class TestTracksRealTime:
    def test_sim_time_matches_wall_time_under_load(self, clock):
        sim = 0.0
        start = time.monotonic()
        for _ in range(TICKS):
            time.sleep(INTERVAL)                 # the ticker's wait
            sim += Store._advance_clock(clock)   # what every accumulator is billed
            time.sleep(WORK)                     # the tick's work
        wall = time.monotonic() - start
        assert 0.95 < sim / wall < 1.02

    def test_the_old_fixed_interval_would_have_run_slow(self):
        """Pins the bug: the constant undercounts by the work fraction."""
        wall_per_tick = INTERVAL + WORK
        assert INTERVAL / wall_per_tick < 0.7

    def test_dt_is_published_on_the_instance(self, clock):
        Store._advance_clock(clock)
        assert clock._dt == pytest.approx(INTERVAL)


class TestEdgeCases:
    def test_first_tick_falls_back_to_the_cadence(self, clock):
        assert Store._advance_clock(clock) == INTERVAL

    def test_resume_from_pause_is_not_billed_for_the_pause(self, clock):
        Store._advance_clock(clock)
        time.sleep(0.15)
        clock._last_tick_t = None                # what _ticker_loop does while paused
        assert Store._advance_clock(clock) == INTERVAL

    def test_a_long_stall_is_clamped(self, clock):
        """A suspend or breakpoint must not expire every timer in one tick —
        a UPS would drain to empty instantly."""
        Store._advance_clock(clock)
        clock._last_tick_t -= 3600.0
        assert Store._advance_clock(clock) == INTERVAL * Store._DT_MAX_MULT

    def test_dt_is_never_negative(self, clock):
        Store._advance_clock(clock)
        clock._last_tick_t += 10.0               # clock coerced backwards
        assert Store._advance_clock(clock) == 0.0

    def test_clamp_scales_with_the_configured_interval(self, clock):
        clock._tick_interval = 2.0
        Store._advance_clock(clock)
        clock._last_tick_t -= 999.0
        assert Store._advance_clock(clock) == 2.0 * Store._DT_MAX_MULT

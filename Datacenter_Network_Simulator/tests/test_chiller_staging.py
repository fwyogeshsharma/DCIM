"""Chiller-plant staging: load hysteresis plus the anti-short-cycle timers.

The timers are the part that matters. Hysteresis alone lets a sawtooth IT load
bounce a stage every tick, which no real plant would allow and no compressor motor
would survive.
"""
import pytest

from core.cooling_model import (
    PLANT_MODULE_KW, STAGE_INTERVAL_S, STAGE_MIN_OFF_S, STAGE_MIN_ON_S,
    installed_modules_for, stage_modules,
)

M = PLANT_MODULE_KW
INSTALLED = 12
NEVER = 1e9          # timer long satisfied


class TestLoadHysteresis:
    """With no timers supplied the call stays purely load-driven (back-compat)."""

    def test_stages_up_above_the_up_fraction(self):
        assert stage_modules(500, INSTALLED, 4) == 5      # > 0.90 x 4 x 110

    def test_stages_down_below_the_down_fraction(self):
        assert stage_modules(150, INSTALLED, 4) == 3      # < 0.60 x 3 x 110

    def test_holds_inside_the_deadband(self):
        assert stage_modules(300, INSTALLED, 4) == 4

    def test_never_exceeds_installed(self):
        assert stage_modules(1e6, INSTALLED, INSTALLED) == INSTALLED

    def test_never_drops_below_min_on(self):
        assert stage_modules(0.0, INSTALLED, 1) == 1


class TestAntiShortCycleTimers:
    def test_min_off_blocks_a_restart(self):
        """A compressor that just stopped must rest before it can start again."""
        assert stage_modules(10_000, INSTALLED, 4,
                             since_up_s=NEVER, since_down_s=10.0) == 4

    def test_min_off_satisfied_allows_the_restart(self):
        assert stage_modules(10_000, INSTALLED, 4,
                             since_up_s=NEVER, since_down_s=STAGE_MIN_OFF_S + 1) == 5

    def test_min_off_is_not_bypassed_even_by_a_deficit(self):
        """Anti-recycle protects a motor. A real plant rides the deficit out."""
        deficit_load = 10_000                       # far past the running capacity
        assert stage_modules(deficit_load, INSTALLED, 4,
                             since_up_s=NEVER, since_down_s=1.0) == 4

    def test_settle_interval_blocks_a_non_urgent_stage_up(self):
        load = 420                                  # > 0.9*4*110, still < 4*110
        assert stage_modules(load, INSTALLED, 4,
                             since_up_s=10.0, since_down_s=NEVER) == 4

    def test_settle_interval_elapsed_allows_the_stage_up(self):
        assert stage_modules(420, INSTALLED, 4,
                             since_up_s=STAGE_INTERVAL_S + 1, since_down_s=NEVER) == 5

    def test_real_deficit_bypasses_the_settle_interval(self):
        load = 460                                  # past 4 x 110 -> genuine shortfall
        assert stage_modules(load, INSTALLED, 4,
                             since_up_s=10.0, since_down_s=NEVER) == 5

    def test_min_on_blocks_an_early_shed(self):
        assert stage_modules(50, INSTALLED, 4,
                             since_up_s=STAGE_MIN_ON_S - 1, since_down_s=NEVER) == 4

    def test_min_on_satisfied_allows_the_shed(self):
        assert stage_modules(50, INSTALLED, 4,
                             since_up_s=STAGE_MIN_ON_S + 1, since_down_s=NEVER) == 3

    def test_sheds_one_module_at_a_time(self):
        """A second shed waits out the interval rather than dumping the plant."""
        assert stage_modules(50, INSTALLED, 3,
                             since_up_s=NEVER, since_down_s=STAGE_INTERVAL_S - 1) == 3


class TestSawtoothRegression:
    """The bug the timers exist to prevent: a load oscillating across both
    thresholds used to flip a stage on literally every tick."""

    @staticmethod
    def _run(timed):
        on, t_up, t_dn, dt = 4, NEVER, NEVER, 5.0
        flips = 0
        for i in range(2000):                       # ~2.8 h of 5 s ticks
            load = 500 if (i // 6) % 2 == 0 else 150
            t_up += dt
            t_dn += dt
            kw = ({"since_up_s": t_up, "since_down_s": t_dn} if timed else {})
            nxt = stage_modules(load, INSTALLED, on, **kw)
            if nxt > on:
                t_up, flips = 0.0, flips + 1
            elif nxt < on:
                t_dn, flips = 0.0, flips + 1
            on = nxt
        return flips

    def test_untimed_sawtooth_flaps_on_every_load_reversal(self):
        """2000 ticks, load reverses every 6 -> a stage change on each reversal."""
        assert self._run(timed=False) >= 1000

    def test_timers_hold_the_stage_steady(self):
        assert self._run(timed=True) < 40

    def test_timers_cut_stage_changes_by_orders_of_magnitude(self):
        assert self._run(timed=False) > 20 * self._run(timed=True)


class TestPlantSizing:
    def test_installed_covers_design_load_with_margin(self):
        mods = installed_modules_for(1500)           # servers in one DC
        assert mods * M >= 1500 * 0.714              # design heat, kW

    def test_at_least_one_module(self):
        assert installed_modules_for(0) >= 1

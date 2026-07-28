"""Cooling-tower bank: cell cycling, fan-law power, approach and condenser water.

The bank is header equipment — it is NOT staged with a chiller train. Every healthy
cell runs and shares the airflow at low speed, except when sharing would push the
fans under their turndown, at which point the bank cycles cells off instead.
"""
import calendar

import pytest

from core.cooling_model import (
    COND_DESIGN_C, COND_MIN_C, FAN_MIN_SPEED, TOWER_APPROACH_C, TOWER_CREDIT_MAX,
    affinity_power_kw, ambient_c, cond_supply_c, cooling_electrical_w,
    tower_approach_c, tower_cell_demand, tower_cell_speed_frac, tower_cells_needed,
    tower_cells_running, tower_chiller_factor, wet_bulb_c,
)

CELLS = 6
CELL_KW = 45.0
JAN = calendar.timegm((2026, 1, 15, 3, 0, 0, 0, 0, 0))   # cold night
JUL = calendar.timegm((2026, 7, 15, 15, 0, 0, 0, 0, 0))  # hot afternoon


def bank(duty, cells=CELLS):
    """Resolve a duty fraction into (demand, needed, running, per-cell speed, kW)."""
    demand = tower_cell_demand(duty, cells)
    needed = tower_cells_needed(duty, cells)
    running = tower_cells_running(demand, cells)
    speed = tower_cell_speed_frac(demand, running)
    return demand, needed, running, speed, running * affinity_power_kw(CELL_KW, speed)


class TestAirflowConservation:
    """Whatever the bank does with cells and speeds, it must still deliver the
    airflow the load demands. The first cut of this model did not, and quietly
    under-rejected by 40 %."""

    @pytest.mark.parametrize("duty", [1.0, 0.8, 0.5, 0.3, 0.15, 0.05])
    def test_delivered_airflow_meets_demand(self, duty):
        demand, _needed, running, speed, _kw = bank(duty)
        # Capped by the bank's own size, and by the turndown floor at tiny loads.
        assert running * speed + 1e-9 >= min(demand, CELLS)

    @pytest.mark.parametrize("duty", [1.0, 0.8, 0.5, 0.3, 0.15, 0.05])
    def test_speed_stays_inside_the_vfd_envelope(self, duty):
        *_, speed, _kw = bank(duty)
        assert FAN_MIN_SPEED - 1e-9 <= speed <= 1.0

    @pytest.mark.parametrize("duty", [1.0, 0.8, 0.5, 0.3, 0.15, 0.05])
    def test_running_cells_within_the_bank(self, duty):
        _demand, _needed, running, _speed, _kw = bank(duty)
        assert 1 <= running <= CELLS


class TestDesignPoint:
    def test_full_duty_runs_every_cell_at_full_speed(self):
        _d, _n, running, speed, kw = bank(1.0)
        assert running == CELLS
        assert speed == pytest.approx(1.0)

    def test_full_duty_draws_bank_nameplate(self):
        """The PUE anchor: at design the bank must cost exactly its nameplate."""
        *_, kw = bank(1.0)
        assert kw == pytest.approx(CELLS * CELL_KW)


class TestSharingBeatsStaging:
    def test_sharing_across_the_bank_is_cheaper_than_1_to_1(self):
        """Same airflow, spread wider, costs (needed/running)^2 — the cube law."""
        demand, needed, running, speed, kw = bank(0.5)
        one_to_one_speed = tower_cell_speed_frac(demand, needed)
        one_to_one_kw = needed * affinity_power_kw(CELL_KW, one_to_one_speed)
        assert needed * one_to_one_speed == pytest.approx(running * speed)  # equal flow
        assert kw < one_to_one_kw


class TestCellCycling:
    def test_low_duty_cycles_cells_off_rather_than_idling_the_bank(self):
        _d, _n, running, _s, _kw = bank(0.15)
        assert running < CELLS

    def test_cycling_beats_pinning_the_whole_bank_at_the_floor(self):
        *_, kw = bank(0.15)
        pinned = CELLS * affinity_power_kw(CELL_KW, FAN_MIN_SPEED)
        assert kw < pinned

    def test_tiny_duty_keeps_at_least_one_cell(self):
        _d, _n, running, _s, _kw = bank(0.001)
        assert running == 1

    def test_no_cells_available_runs_nothing(self):
        assert tower_cells_running(demand=3.0, cells_available=0) == 0


class TestRedundancyAccounting:
    def test_surplus_cells_are_not_required_capacity(self):
        """Losing a cell above `needed` costs efficiency, not rejection."""
        needed = tower_cells_needed(0.5, CELLS)
        assert needed < CELLS
        assert min(1.0, (CELLS - 1) / needed) == 1.0

    def test_losing_a_needed_cell_does_cost_rejection(self):
        needed = tower_cells_needed(0.5, CELLS)
        assert min(1.0, (needed - 1) / needed) < 1.0


class TestApproachAndCredit:
    def test_extra_area_lowers_the_approach(self):
        assert tower_approach_c(3, CELLS) < tower_approach_c(3, 3)

    def test_exactly_needed_cells_sit_at_design_approach(self):
        assert tower_approach_c(3, 3) == pytest.approx(TOWER_APPROACH_C)

    def test_no_credit_when_only_the_needed_cells_run(self):
        assert tower_chiller_factor(3, 3) == 1.0

    def test_credit_is_a_saving_and_is_capped(self):
        factor = tower_chiller_factor(1, CELLS)
        assert 1.0 - TOWER_CREDIT_MAX <= factor < 1.0

    def test_credit_reaches_the_dc_total_and_so_pue(self):
        """Applied to the variable term only — it is a compressor saving, not a
        change to the pump/fan floor."""
        it_w, design_w = 1_000_000.0, 1_200_000.0
        base = cooling_electrical_w(it_w, design_w, "chicago")
        credited = cooling_electrical_w(it_w, design_w, "chicago",
                                        cond_factor=tower_chiller_factor(1, CELLS))
        assert credited < base


class TestCondenserWater:
    def test_a_tower_cannot_beat_the_wet_bulb(self):
        for city in ("chicago", "phoenix", "singapore", "dublin"):
            assert cond_supply_c(city, 3, CELLS, JUL) >= wet_bulb_c(city, JUL)

    def test_wet_bulb_is_below_dry_bulb(self):
        assert wet_bulb_c("phoenix", JUL) < ambient_c("phoenix", JUL)

    def test_arid_sites_reject_more_cheaply_than_humid_ones(self):
        """Same dry-bulb story, different wet bulb — this is why site matters."""
        assert wet_bulb_c("phoenix", JUL) < wet_bulb_c("singapore", JUL)

    def test_more_cells_make_colder_water(self):
        assert cond_supply_c("chicago", 3, CELLS, JUL) < cond_supply_c("chicago", 3, 3, JUL)

    def test_cold_night_is_colder_than_hot_afternoon(self):
        assert cond_supply_c("chicago", 3, 3, JAN) < cond_supply_c("chicago", 3, 3, JUL)

    def test_never_below_the_chiller_minimum(self):
        """Below ~15 C entering water a centrifugal loses the head it needs, so
        tower control holds the minimum instead of chasing the wet bulb."""
        assert cond_supply_c("ashburn", 3, CELLS, JAN) >= COND_MIN_C

    def test_design_point_matches_the_published_base(self):
        """26.5 C design wet bulb + 4 C approach = the 30.5 C point base."""
        assert COND_DESIGN_C == pytest.approx(26.5 + TOWER_APPROACH_C)

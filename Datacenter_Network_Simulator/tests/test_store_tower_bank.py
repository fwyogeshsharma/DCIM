"""Store-level tower bank: cells are header equipment, not train members.

Every healthy cell is available; the bank runs as many as it can keep above the fan
turndown and cycles the rest off. A cycled-off cell is staged off by the BMS — so it
must read stopped and draw nothing — but it stays AVAILABLE, because it can start on
demand. Conflating those two put cells in and out of the bank every tick.
"""
import pytest

from tests.conftest import DC, build_plant

CELLS = ["CT1-DC1-RF", "CT2-DC1-RF", "CT3-DC1-RF"]


def running_cells(plant):
    return set(plant.store._tower_running_now.get(DC, set()))


class TestBankStaging:
    def test_cells_are_never_staged_with_a_train(self, plant):
        """Two of three trains are on standby; their cells must not follow."""
        plant.tick()
        assert plant.running_chillers() == {"CHL1-DC1-CP"}
        assert len(running_cells(plant)) >= 1

    def test_light_load_cycles_surplus_cells_off(self, plant):
        plant.tick()
        assert len(running_cells(plant)) < len(CELLS)

    def test_cycled_off_cells_are_reported_standby(self, plant):
        """So they read Fan_Status 0 and fade on the canvas, like any staged-off unit."""
        plant.tick()
        idle = set(CELLS) - running_cells(plant)
        assert idle
        assert idle <= plant.standby()

    def test_running_cell_is_not_standby(self, plant):
        plant.tick()
        assert not (running_cells(plant) & plant.standby())

    def test_idle_cells_draw_nothing(self, plant):
        plant.tick()
        for c in set(CELLS) - running_cells(plant):
            assert plant.power(c) == 0.0

    def test_running_cell_draws_power(self, plant):
        plant.tick()
        assert all(plant.power(c) > 0 for c in running_cells(plant))

    def test_heavy_load_runs_more_cells(self, tmp_path):
        light = build_plant(tmp_path / "l", servers=4, installed_modules=6)
        heavy = build_plant(tmp_path / "h", servers=600, installed_modules=6)
        light.tick()
        heavy.tick()
        assert len(running_cells(heavy)) > len(running_cells(light))


class TestBankStability:
    def test_the_running_set_does_not_churn_between_ticks(self, plant):
        """The chatter bug: ranking on raw run-hours moved the lead cell every tick
        and smeared one cell's duty across the whole bank."""
        plant.tick()
        first = running_cells(plant)
        seen = set()
        for _ in range(20):
            plant.tick()
            seen.add(frozenset(running_cells(plant)))
        assert seen == {frozenset(first)}

    def test_cell_hands_over_after_a_rotation_period(self, plant):
        plant.tick()
        lead = next(iter(running_cells(plant)))
        plant.store._tower_run_hours[lead] = plant.store._TOWER_ROTATE_H + 1
        plant.tick()
        assert lead not in running_cells(plant)

    def test_run_hours_accrue_only_for_running_cells(self, plant):
        for _ in range(5):
            plant.tick()
        hours = plant.store._tower_run_hours
        for c in running_cells(plant):
            assert hours.get(c, 0) > 0
        for c in set(CELLS) - running_cells(plant):
            assert hours.get(c, 0) == 0


class TestRejectionCapacity:
    def test_a_cycled_off_cell_still_counts_as_capacity(self, plant):
        """Idle is not lost. Counting cycled-off cells as lost rejection drove a
        false cooling-degraded state."""
        plant.tick()
        plant.tick()
        assert plant.store._tower_reject[DC] == 1.0
        assert not plant.store.cooling_degraded(DC)

    def test_losing_a_surplus_cell_costs_no_rejection(self, plant, plant_cache):
        plant.tick()
        idle = sorted(set(CELLS) - running_cells(plant))
        plant_cache[idle[0]] = {"Alarm_HighVibration": 1.0}
        plant.tick()
        plant.tick()
        assert plant.store._tower_reject[DC] == 1.0

    def test_losing_the_whole_bank_loses_rejection(self, plant):
        plant.store._plant_unpowered_names = set(CELLS)
        plant.tick()
        plant.tick()
        assert plant.store._tower_reject[DC] == 0.0
        assert plant.store.cooling_degraded(DC)

    def test_an_alarmed_cell_is_dropped_from_the_bank(self, plant, plant_cache):
        plant.tick()
        plant_cache[CELLS[0]] = {"Alarm_HighVibration": 1.0}
        plant.tick()
        assert CELLS[0] not in running_cells(plant)

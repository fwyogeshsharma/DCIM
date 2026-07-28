"""Store-level staging: how many modules run, which trains carry them, and which
units end up on standby. Exercises _compute_power_flow against a real topology
rather than the pure helpers it calls.
"""
import pytest

from tests.conftest import DC, build_plant


def load_watts(plant):
    return sum(d.power_draw_w for d in plant.dm.get_all_devices()
               if d.device_type.value == "server")


class TestModuleStaging:
    def test_light_load_runs_the_minimum(self, tmp_path):
        p = build_plant(tmp_path, servers=4, installed_modules=12)
        p.tick()
        assert p.stage_on() == 1

    def test_heavy_load_stages_more_modules_on(self, tmp_path):
        """Timers gate how FAST it climbs, so drive several ticks."""
        p = build_plant(tmp_path, servers=400, installed_modules=12)
        for _ in range(40):
            p.store._plant_stage_since[DC] = (1e9, 1e9)   # timers already satisfied
            p.tick()
        assert p.stage_on() > 1

    def test_staging_never_exceeds_installed(self, tmp_path):
        p = build_plant(tmp_path, servers=4000, installed_modules=3)
        for _ in range(30):
            p.store._plant_stage_since[DC] = (1e9, 1e9)
            p.tick()
        assert p.stage_on() == 3

    def test_overload_beyond_the_installed_plant_is_recorded(self, tmp_path):
        p = build_plant(tmp_path, servers=4000, installed_modules=1)
        p.tick()
        assert p.store._plant_overload_kw[DC] > 0

    def test_no_overload_when_the_plant_covers_the_load(self, plant):
        plant.tick()
        assert plant.store._plant_overload_kw[DC] == 0


class TestTrainSelection:
    def test_one_train_carries_a_light_load(self, plant):
        plant.tick()
        assert plant.running_chillers() == {"CHL1-DC1-CP"}

    def test_idle_trains_go_to_standby_whole(self, plant):
        plant.tick()
        sb = plant.standby()
        for i in (2, 3):
            assert {f"CHL{i}-DC1-CP", f"CHWP{i}-DC1-CP", f"CWP{i}-DC1-CP"} <= sb

    def test_the_running_train_is_not_on_standby(self, plant):
        plant.tick()
        sb = plant.standby()
        assert not ({"CHL1-DC1-CP", "CHWP1-DC1-CP", "CWP1-DC1-CP"} & sb)

    def test_header_spare_pump_idles_while_the_lead_pump_is_healthy(self, plant):
        plant.tick()
        assert "CHWP4-DC1-CP" in plant.standby()

    def test_lead_is_deterministic_across_builds(self, tmp_path):
        a, b = build_plant(tmp_path / "a"), build_plant(tmp_path / "b")
        a.tick()
        b.tick()
        assert a.running_chillers() == b.running_chillers() == {"CHL1-DC1-CP"}


class TestFailover:
    def test_unpowered_train_is_passed_over_for_a_healthy_one(self, plant):
        plant.store._plant_unpowered_names = {"CHL1-DC1-CP"}
        plant.tick()
        assert "CHL1-DC1-CP" not in plant.running_chillers()
        assert plant.running_chillers() == {"CHL2-DC1-CP"}

    def test_alarmed_train_is_passed_over(self, plant, plant_cache):
        plant_cache["CHL1-DC1-CP"] = {"Alarm_HighPressure": 1.0}
        plant.tick()
        assert "CHL1-DC1-CP" not in plant.running_chillers()

    def test_a_locked_out_chiller_is_passed_over(self, plant):
        plant.store._chiller_hp_lockout.add("CHL1-DC1-CP")
        plant.tick()
        assert "CHL1-DC1-CP" not in plant.running_chillers()

    def test_least_switching_keeps_the_standby_that_took_over(self, plant):
        """A recovered chiller must NOT displace the unit now carrying the load —
        that would be a pointless second compressor start."""
        plant.store._plant_unpowered_names = {"CHL1-DC1-CP"}
        plant.tick()
        assert plant.running_chillers() == {"CHL2-DC1-CP"}
        plant.store._plant_unpowered_names = set()          # CHL1 comes back
        plant.tick()
        assert plant.running_chillers() == {"CHL2-DC1-CP"}

    def test_a_pump_fault_takes_its_whole_train_out(self, plant, plant_cache):
        """Trains stage as a unit — a chiller with no pump moves no water."""
        plant_cache["CHWP1-DC1-CP"] = {"Alarm_Fault": 1.0}
        plant.tick()
        assert "CHL1-DC1-CP" not in plant.running_chillers()


class TestLeadRotation:
    def test_lead_hands_over_after_a_full_rotation_period(self, plant):
        plant.tick()
        assert plant.running_chillers() == {"CHL1-DC1-CP"}
        # Age the lead past one rotation period; peers stay at zero.
        plant.store._train_run_hours["CHL1-DC1-CP"] = plant.store._TRAIN_ROTATE_H + 1
        plant.tick()
        assert plant.running_chillers() == {"CHL2-DC1-CP"}

    def test_lead_holds_inside_a_rotation_period(self, plant):
        plant.tick()
        plant.store._train_run_hours["CHL1-DC1-CP"] = plant.store._TRAIN_ROTATE_H * 0.9
        plant.tick()
        assert plant.running_chillers() == {"CHL1-DC1-CP"}

    def test_run_hours_accrue_only_for_the_lead(self, plant):
        for _ in range(5):
            plant.tick()
        hours = plant.store._train_run_hours
        assert hours.get("CHL1-DC1-CP", 0) > 0
        assert hours.get("CHL2-DC1-CP", 0) == 0
        assert hours.get("CHL3-DC1-CP", 0) == 0

"""Condenser loop: the water temperature the bank can actually hold, the points it
publishes, and the head-pressure protection that keys off it.

The loop base is wet bulb + achieved approach, not a fixed constant, so it moves with
the weather and with how many cells share the load. Everything downstream — chiller
points, tower points, the HP thresholds — has to agree with it.
"""
import pytest

from core.cooling_model import COND_MIN_C, cond_supply_c
from tests.conftest import CITY, DC, build_plant


def auto(plant, name):
    """Synthetic BACnet points the store published for a device this tick."""
    return plant.store._plant_auto_points.get(name, {})


class TestLoopTemperature:
    def test_base_is_wet_bulb_plus_approach(self, plant):
        plant.tick()
        plant.tick()
        needed, running = plant.store._tower_cells[DC]
        assert plant.store._cond_base_c[DC] == pytest.approx(
            round(cond_supply_c(CITY, needed, running), 2), abs=0.5)

    def test_loop_settles_at_the_base_when_rejection_is_whole(self, plant):
        for _ in range(60):
            plant.tick()
        assert plant.store._cond_water_c[DC] == pytest.approx(
            plant.store._cond_base_c[DC], abs=0.3)

    def test_loop_never_reads_below_the_chiller_minimum(self, plant):
        plant.tick()
        plant.tick()
        assert plant.store._cond_water_c[DC] >= COND_MIN_C

    def test_losing_rejection_heats_the_loop(self, plant):
        for _ in range(5):
            plant.tick()
        settled = plant.store._cond_water_c[DC]
        plant.store._plant_unpowered_names = {"CT1-DC1-RF", "CT2-DC1-RF", "CT3-DC1-RF"}
        for _ in range(30):
            plant.tick()
        assert plant.store._cond_water_c[DC] > settled + 5


class TestPublishedPoints:
    def test_running_chiller_carries_the_design_range(self, plant):
        plant.tick()
        pts = auto(plant, "CHL1-DC1-CP")
        rng = pts["Cond_Return_Temp"] - pts["Cond_Supply_Temp"]
        assert rng == pytest.approx(plant.store._COND_RANGE_C, abs=0.2)

    def test_standby_chiller_sits_at_loop_temperature_with_no_range(self, plant):
        """An idle machine's barrel is loop water — it must not invent its own 33 C."""
        plant.tick()
        plant.tick()
        pts = auto(plant, "CHL2-DC1-CP")
        assert pts["Cond_Supply_Temp"] == pts["Cond_Return_Temp"]
        assert pts["Cond_Supply_Temp"] == pytest.approx(
            plant.store._cond_water_c[DC], abs=0.1)

    def test_standby_chiller_pressure_is_the_idle_fit(self, plant):
        """No compressor, no lift — the refrigerant sits at loop saturation."""
        plant.tick()
        plant.tick()
        assert auto(plant, "CHL2-DC1-CP")["Cond_Pressure"] < \
            auto(plant, "CHL1-DC1-CP")["Cond_Pressure"]

    def test_no_startup_transient_on_the_very_first_tick(self, plant):
        """Regression: standby plant used to publish RUNNING values for one tick.

        The cooling passes run ahead of _compute_power_flow, so on the first tick no
        staging decision existed and the standby set was empty — every chiller came
        out with a live condenser range and running head pressure, and every cell
        reported its fan turning. Staging is now primed before the first consumer
        reads it, so tick one already looks like tick two.
        """
        plant.tick()
        pts = auto(plant, "CHL2-DC1-CP")
        assert pts["Cond_Return_Temp"] == pts["Cond_Supply_Temp"]

    def test_staging_is_primed_before_the_first_cooling_pass(self, plant):
        """The mechanism, asserted directly: the cond loop must never run against an
        empty staging decision."""
        assert not plant.store._plant_stage_on
        plant.store._compute_cond_loop()
        assert plant.store._plant_stage_on
        assert plant.store._plant_standby_names

    def test_priming_happens_once(self, plant):
        """Idempotent — a later tick must not re-prime and clobber live staging."""
        plant.tick()
        plant.store._plant_stage_on[DC] = 99
        plant.store._ensure_staging_primed()
        assert plant.store._plant_stage_on[DC] == 99

    def test_tower_outlet_equals_the_chiller_supply(self, plant):
        """One number across the whole loop — the bank sets it, the chiller reads it."""
        plant.tick()
        plant.tick()
        lead_cell = next(iter(plant.store._tower_running_now[DC]))
        assert auto(plant, lead_cell)["Cond_Water_Out"] == pytest.approx(
            auto(plant, "CHL1-DC1-CP")["Cond_Supply_Temp"], abs=0.1)

    def test_running_cell_shows_the_range_across_it(self, plant):
        """The range published on a cell is the LIVE one the loop is carrying.

        It used to be pinned at the design constant here and at every other
        publication site, which made this assertion compare the constant with
        itself and hid the case that matters: a condenser loop whose pumps have
        stopped still advertised a healthy 5 K range while the chillers behind it
        latched out on head pressure. The default fixture is lightly loaded, where
        the VFD pumps are already on their turndown floor and the range narrows —
        so the live figure is the only thing worth asserting against."""
        plant.tick()
        plant.tick()
        lead_cell = next(iter(plant.store._tower_running_now[DC]))
        pts = auto(plant, lead_cell)
        live = plant.store._cond_range_c[DC]
        assert 0.0 < live <= plant.store._COND_RANGE_C + 0.2, (
            f"a healthy loop should not exceed design range, got {live}")
        assert pts["Cond_Water_In"] - pts["Cond_Water_Out"] == pytest.approx(
            live, abs=0.2)

    def test_a_loaded_plant_holds_the_design_range(self, tmp_path, plant_cache):
        """With the pumps off their turndown floor the ΔT control loop has
        authority and holds the condenser range at design."""
        from conftest import build_plant

        p = build_plant(tmp_path, servers=600, installed_modules=6)
        for _ in range(6):
            p.tick()
        assert p.store._cond_range_c[DC] == pytest.approx(
            p.store._COND_RANGE_C, abs=0.3)

    def test_a_stalled_condenser_loop_widens_its_range(self, tmp_path, plant_cache):
        """Lose the condenser pumps and the ΔT control loop is out of authority, so
        the range widens to the saturated reading a stagnant header shows. Pinned at
        design it claimed the loop was carrying its heat away normally."""
        from core.cooling_model import COND_MAX_RANGE_C
        from conftest import build_plant

        p = build_plant(tmp_path, servers=600, installed_modules=6)
        for _ in range(6):
            p.tick()
        for i in range(1, 4):
            plant_cache[f"CWP{i}-{DC}-CP"] = {"Run_Status": 1.0, "Alarm_Fault": 1.0}
        for _ in range(30):
            p.tick()

        assert p.store._cw_pump_frac[DC] == 0.0
        assert p.store._cond_range_c[DC] == pytest.approx(COND_MAX_RANGE_C, abs=0.1)

    def test_cycled_off_cell_shows_no_range(self, plant):
        """A valved-out cell with a stopped fan rejects nothing."""
        plant.tick()
        plant.tick()
        idle = sorted(set(["CT1-DC1-RF", "CT2-DC1-RF", "CT3-DC1-RF"])
                      - plant.store._tower_running_now[DC])
        pts = auto(plant, idle[0])
        assert pts["Cond_Water_In"] == pts["Cond_Water_Out"]


class TestHeadPressureProtection:
    @staticmethod
    def _kill_rejection(plant, ticks=400):
        plant.tick()
        plant.store._plant_unpowered_names = {"CT1-DC1-RF", "CT2-DC1-RF", "CT3-DC1-RF"}
        for _ in range(ticks):
            plant.tick()

    def test_lost_rejection_eventually_trips_the_chiller(self, plant):
        self._kill_rejection(plant)
        assert plant.store.get_chiller_trips()

    def test_the_trip_latches(self, plant):
        """Clearing the fault cools the loop but must not restart the machine —
        that is what a manual-reset HP cutout does."""
        self._kill_rejection(plant)
        tripped = plant.store.get_chiller_trips()
        plant.store._plant_unpowered_names = set()
        for _ in range(60):
            plant.tick()
        assert plant.store.get_chiller_trips() == tripped

    def test_reset_is_refused_while_the_loop_is_hot(self, plant):
        self._kill_rejection(plant)
        name = plant.store.get_chiller_trips()[0]
        msg = plant.store.reset_chiller_trip(name)
        assert "condenser water still" in msg
        assert name in plant.store.get_chiller_trips()

    def test_reset_holds_once_the_loop_has_cooled(self, plant):
        self._kill_rejection(plant)
        name = plant.store.get_chiller_trips()[0]
        plant.store._plant_unpowered_names = set()
        for _ in range(400):
            plant.tick()
        assert plant.store.reset_chiller_trip(name) == "reset"
        assert name not in plant.store.get_chiller_trips()

    def test_resetting_an_untripped_chiller_is_a_no_op(self, plant):
        plant.tick()
        assert plant.store.reset_chiller_trip("CHL1-DC1-CP") == "not tripped"

    def test_thresholds_scale_with_a_hot_site(self, tmp_path, monkeypatch):
        """A humid site legitimately holds warm condenser water; a fixed 36 C limit
        would leave it in permanent capacity limit."""
        import core.cooling_model as cm

        p = build_plant(tmp_path)
        monkeypatch.setattr(cm, "wet_bulb_c", lambda city, now=None: 31.0)
        p.tick()
        p.tick()
        base = p.store._cond_base_c[DC]
        assert base > p.store._COND_LIMIT_C - p.store._COND_LIMIT_MARGIN_C
        assert not p.store._chiller_derate, "healthy plant must not derate at its own base"

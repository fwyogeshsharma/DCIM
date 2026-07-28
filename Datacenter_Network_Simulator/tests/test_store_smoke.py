"""Does the fixture plant actually drive the store? Everything else depends on it.

The per-tick cooling chain is wrapped in try/except-and-log, so a broken fixture
produces empty results rather than an exception. These tests fail loudly instead.
"""
import logging

import pytest


def test_trains_are_discovered_from_the_cooling_edges(plant):
    ctx = plant.store._cooling_context()
    trains = ctx["trains_by_dc"]["DC1"]
    assert len(trains) == plant.trains
    for i, tr in enumerate(trains, start=1):
        assert tr["chiller"] == f"CHL{i}-DC1-CP"
        assert tr["chwp"] == f"CHWP{i}-DC1-CP"
        assert tr["cwp"] == f"CWP{i}-DC1-CP"
        assert tr["tower"] == f"CT{i}-DC1-RF"
        assert tr["complete"]


def test_tower_is_not_a_train_member(plant):
    """The bank is header equipment — it must not stage with a train."""
    tr = plant.store._cooling_context()["trains_by_dc"]["DC1"][0]
    assert "CT1-DC1-RF" not in tr["members"]
    assert set(tr["members"]) == {"CHL1-DC1-CP", "CHWP1-DC1-CP", "CWP1-DC1-CP"}


def test_header_spare_pump_is_identified(plant):
    assert plant.store._cooling_context()["spare_chwp"]["DC1"] == ["CHWP4-DC1-CP"]


def test_city_is_carried_for_the_weather_model(plant):
    assert plant.store._cooling_context()["city_by_dc"]["DC1"] == "chicago"


def test_a_tick_produces_plant_state(plant, caplog):
    """Guards the whole file: if the tick silently failed, everything below is
    vacuous."""
    with caplog.at_level(logging.ERROR):
        plant.tick()
    assert not [r for r in caplog.records if "error" in r.message.lower()], caplog.text
    assert plant.stage_on() is not None
    assert plant.store._plant_power_by_name, "no plant power computed"
    assert plant.store._plant_trains_run["DC1"], "no train staged on"

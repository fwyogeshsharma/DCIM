"""S1-9 (doc 32 §6.3, §14): the weather override.

Weather is the largest legitimate driver of plant efficiency, and CH-2 / CT-1
regress on wet bulb so that it is not flagged. Testing that gating needs the
sky to move on purpose, through the same functions every consumer already
reads.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from core import cooling_model as cm
from test_degradation import _rig
from test_fault_publish_truth import api_state  # noqa: F401  (fixture)
from test_plant_override_ramp import _wire

CITY = "chicago"


@pytest.fixture(autouse=True)
def _clean():
    cm._WEATHER.clear()
    yield
    cm._WEATHER.clear()


def test_offsets_and_the_wet_bulb_clamp():
    now = 1_790_000_000.0
    d0, w0 = cm.ambient_c(CITY, now), cm.wet_bulb_c(CITY, now)
    cm.set_weather_override(CITY, 8.0, 6.0, now + 3600)
    assert cm.ambient_c(CITY, now) == pytest.approx(d0 + 8.0)
    assert cm.wet_bulb_c(CITY, now) == pytest.approx(w0 + 6.0)
    # Dry and wet together (the API's default for an omitted wet offset).
    cm.set_weather_override(CITY, 5.0, 5.0, now + 3600)
    assert cm.wet_bulb_c(CITY, now) == pytest.approx(w0 + 5.0)
    # A wet-bulb offset alone is humidity, and wet bulb never beats dry bulb.
    cm.set_weather_override(CITY, 0.0, 20.0, now + 3600)
    assert cm.ambient_c(CITY, now) == pytest.approx(d0)
    assert cm.wet_bulb_c(CITY, now) == pytest.approx(d0)
    # Other cities are untouched; expiry honours `now`.
    assert cm.weather_override("new york", now) is None
    assert cm.weather_override(CITY, now + 3601) is None
    assert [c for c, _w in cm.expired_weather(now + 3601)] == [CITY]
    assert cm.weather_override(CITY, now) is None


def test_the_api_moves_the_tower_oa_points_and_expiry_is_ledgered(tmp_path, api_state):
    from api.routers.faults import WeatherBody, get_weather, set_weather
    r = _rig(tmp_path)
    api_state(r)
    r.tick(10)
    ct = r.h.made["CT1"].name
    dry0, wet0 = _wire(r, ct, "OA_Dry_Bulb_Temp"), _wire(r, ct, "OA_Wet_Bulb_Temp")
    chl = [t["chiller"] for t in r.store._plant_trains_run["DC1"]][0]
    kw0 = r.store._plant_power_by_name[chl]
    out = set_weather(WeatherBody(datacenter="DC1", dry_bulb_offset_c=8.0,
                                  wet_bulb_offset_c=6.0, duration_s=3.0,
                                  campaign="heatwave/t"))
    assert out["kind"] == "weather" and out["city"] == CITY
    r.tick(2)
    assert _wire(r, ct, "OA_Dry_Bulb_Temp") == pytest.approx(dry0 + 8.0, abs=0.15)
    assert _wire(r, ct, "OA_Wet_Bulb_Temp") == pytest.approx(wet0 + 6.0, abs=0.15)
    assert _wire(r, ct, "OA_Wet_Bulb_Temp") <= _wire(r, ct, "OA_Dry_Bulb_Temp")
    # Hotter, more humid air costs the chillers (ambient factor + condenser water).
    assert r.store._plant_power_by_name[chl] > kw0
    w = {x["datacenter"]: x for x in get_weather()["weather"]}
    assert w["DC1"]["override"]["dry_bulb_offset_c"] == 8.0
    time.sleep(3.2)
    r.tick(1)
    e = r.store.faults.ledger.get(out["id"])
    assert e["end_reason"] == "expired" and e["expected_kind"] == "none"
    assert e["campaign"] == "heatwave/t"
    assert get_weather()["weather"][0]["override"] is None


def test_refusals_and_clear(tmp_path, api_state):
    from api.routers.faults import WeatherBody, clear_weather, set_weather
    r = _rig(tmp_path)
    api_state(r)
    for body in (WeatherBody(datacenter="DC1", dry_bulb_offset_c=8.0),            # no until
                 WeatherBody(datacenter="DC1", dry_bulb_offset_c=40.0, duration_s=60),
                 WeatherBody(datacenter="DC1", duration_s=60)):                  # no-op
        with pytest.raises(HTTPException) as ei:
            set_weather(body)
        assert ei.value.status_code == 422
    with pytest.raises(HTTPException) as ei:
        set_weather(WeatherBody(datacenter="DC9", dry_bulb_offset_c=1.0, duration_s=60))
    assert ei.value.status_code == 404
    # Default scope is every site.
    out = set_weather(WeatherBody(dry_bulb_offset_c=3.0, duration_s=600))
    assert [e["city"] for e in out["entries"]] == [CITY]
    got = clear_weather()
    assert got["cleared"][0]["end_reason"] == "cleared"
    assert cm.weather_override(CITY) is None

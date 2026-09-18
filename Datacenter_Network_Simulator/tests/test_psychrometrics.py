"""Moist air, because half the ASHRAE envelope is written in it.

The recommended band is 18-27 C dry bulb AND -9..+15 C dew point AND RH at or
below 60 %. A hall can hold 22 C all year and be outside the envelope on
moisture alone, so the dew point a probe publishes has to be a real
measurement rather than a field approximation.
"""
import pytest

from core.psychrometrics import dew_point_c, relative_humidity_pct


def _old_rule(t, rh):
    """What this used to be: a linear fit, good above ~50 % RH and not below."""
    return t - (100.0 - rh) / 5.0


def test_dew_point_round_trips_through_relative_humidity():
    """The two directions are one relation. A probe that publishes T and RH and
    one that publishes T and dew point must describe the same air."""
    for t in (5.0, 18.0, 22.0, 27.0, 35.0):
        for rh in (8.0, 25.0, 47.0, 60.0, 80.0, 100.0):
            dp = dew_point_c(t, rh)
            assert relative_humidity_pct(t, dp) == pytest.approx(rh, abs=0.05)


def test_dew_point_never_exceeds_the_dry_bulb():
    """Air cannot condense above its own temperature; at saturation the two
    meet exactly."""
    for t in (-5.0, 0.0, 18.0, 27.0, 40.0):
        for rh in (1.0, 30.0, 99.0):
            assert dew_point_c(t, rh) < t
        assert dew_point_c(t, 100.0) == pytest.approx(t, abs=0.01)


def test_the_old_rule_of_thumb_missed_the_dry_end_entirely():
    """Why this module exists.

    The approximation errs toward the DRY end, which is the direction ASHRAE's
    dew-point FLOOR lives in (-9 C recommended, -12 C allowable). A hall at
    18 C and 8 % RH is at -16.8 C dew point - outside even the allowable
    envelope, a floor building static charge - and the old fit called it -0.4,
    comfortably in band. A compliance figure built on it would have cleared a
    genuinely non-compliant room and had no way of knowing.
    """
    assert dew_point_c(18.0, 8.0) == pytest.approx(-16.8, abs=0.1)
    assert _old_rule(18.0, 8.0) == pytest.approx(-0.4, abs=0.1)

    # And it is wrong by a kelvin even in the middle of a normal hall.
    assert dew_point_c(23.2, 47.0) == pytest.approx(11.3, abs=0.1)
    assert _old_rule(23.2, 47.0) == pytest.approx(12.6, abs=0.1)


def test_the_two_moisture_limits_bind_at_different_points():
    """ASHRAE caps moisture twice, at 60 % RH and at 15 C dew point, and which
    one bites depends on the temperature. A hall at 27 C and 60 % RH passes the
    humidity limit and fails the dew-point one - so a page that checks only RH
    would call it compliant.
    """
    assert dew_point_c(27.0, 60.0) > 15.0        # at the top of the band, DP binds
    assert dew_point_c(20.0, 60.0) < 15.0        # cooler, the same RH is fine


def test_zero_humidity_does_not_explode():
    """A probe reading 0 % is a fault, not air. The metrics tick must not throw
    on it, and the value it returns must still be below the dry bulb."""
    assert dew_point_c(22.0, 0.0) < 22.0
    assert dew_point_c(22.0, -5.0) < 22.0

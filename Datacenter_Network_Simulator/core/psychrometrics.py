"""Moist-air relations the thermal model needs, done properly.

Dew point is half of the ASHRAE TC 9.9 envelope. The recommended band is not
just 18-27 C: it is 18-27 C dry bulb AND a dew point between -9 C and +15 C
AND relative humidity at or below 60 %, because moisture is what condenses on
a cold coil and what lets a floor build static charge. A hall can sit at 22 C
all year and still be outside the envelope on both moisture counts.

This module exists because the simulator computed dew point with the field
rule of thumb:

    dew_point = dry_bulb - (100 - rh) / 5

which is a linear fit good to about a kelvin between 50 % and 100 % RH and
nothing like right below that. At 23.2 C and 47 % it reads 12.6 C where the
real figure is 11.3; at 35 % it reads 10.2 against 7.3. The error grows in
exactly the direction that matters - dry air - and the DP FLOOR (-9 C
recommended, -12 C allowable) is the limit a dry hall breaches. A compliance
figure built on that approximation would clear a floor that was genuinely out
of band and would have no way of knowing.

Magnus-Tetens with the Sonntag coefficients instead: better than 0.1 K over
-45..60 C, which covers every air this simulator makes, including a chiller
hall in winter.
"""
from __future__ import annotations

import math

#: Sonntag (1990) coefficients for saturation vapour pressure over water.
#: `B` is dimensionless, `C` is in kelvin. Over ICE the pair differs, but a
#: datacentre never sees sub-zero intake air and the water fit is the one
#: every psychrometric chart in the industry is drawn from.
_MAGNUS_B = 17.62
_MAGNUS_C = 243.12

#: Below this the logarithm runs away: RH of 0 has no dew point at all (air
#: with no water in it never saturates), and a probe reading 0 % is a fault
#: rather than a measurement. Clamped rather than raised, because the caller
#: is a metrics tick that must not throw.
_RH_FLOOR_PCT = 0.5


def dew_point_c(dry_bulb_c: float, rh_pct: float) -> float:
    """Dew point (°C) from dry-bulb temperature (°C) and relative humidity (%).

    The temperature the air would have to be cooled to, at constant pressure,
    for the water in it to start condensing. Always at or below the dry bulb,
    and equal to it only at saturation.
    """
    rh = max(_RH_FLOOR_PCT, min(100.0, float(rh_pct)))
    t = float(dry_bulb_c)
    gamma = math.log(rh / 100.0) + (_MAGNUS_B * t) / (_MAGNUS_C + t)
    return (_MAGNUS_C * gamma) / (_MAGNUS_B - gamma)


def relative_humidity_pct(dry_bulb_c: float, dew_point_c_: float) -> float:
    """Relative humidity (%) from dry bulb and dew point, the inverse of above.

    Here because a probe that publishes dew point and temperature but no RH is
    a real part (several Vertiv and Geist heads do exactly that), and the
    moisture leg of the envelope is written in both units.
    """
    t = float(dry_bulb_c)
    d = min(float(dew_point_c_), t)
    num = math.exp((_MAGNUS_B * d) / (_MAGNUS_C + d))
    den = math.exp((_MAGNUS_B * t) / (_MAGNUS_C + t))
    return max(0.0, min(100.0, 100.0 * num / den))

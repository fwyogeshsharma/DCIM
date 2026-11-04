"""What an IT box draws, in one place.

A server's (or switch's) draw is a compute share - an idle floor plus a
CPU-driven part of nameplate - plus the RISE in chassis fan power as the
intake warms. Two parts of the simulator need that number and they must
agree to the watt:

  * the power chain (DeviceStateStore._server_live_watts) books it through the
    cords into the PDUs, the UPS and the facility meters;
  * the device REPORTS it - Redfish PowerConsumedWatts, and the PSU-input
    watts a switch lists in ENTITY-SENSOR-MIB.

They used to be two formulas. The chain gained the fan term (so a cooling
failure shows on the UPS, as it does on a real floor) and the reporting side
never did, so whenever an intake passed 25 C the PDU metered power that none
of the boxes plugged into it admitted to drawing. A BMC's PowerConsumedWatts
is the PSU input; the fans are inside it.
"""

from __future__ import annotations

from typing import Any

#: Fan power as a fraction of nameplate: the floor a cool intake needs (already
#: inside the compute curve) and full speed.
FAN_BASE_FRAC = 0.04
FAN_FULL_FRAC = 0.16
#: Intake temperatures between which the fans ramp from floor to full.
FAN_RAMP_LO_C = 25.0
FAN_RAMP_HI_C = 40.0
#: The intake assumed when a device reports none.
DEFAULT_INLET_C = 22.0


def fan_speed_frac(device: Any, default_inlet_c: float = DEFAULT_INLET_C) -> float:
    """Fan drive speed 0..1 from intake temperature."""
    inlet = float(getattr(device, "inlet_temp", None) or default_inlet_c)
    span = FAN_RAMP_HI_C - FAN_RAMP_LO_C
    return max(0.0, min(1.0, (inlet - FAN_RAMP_LO_C) / span))


def live_watts(device: Any, default_inlet_c: float = DEFAULT_INLET_C) -> float:
    """The draw, W: 0 if powered off or with no nameplate."""
    if getattr(device, "power_state", "On") == "Off":
        return 0.0
    nominal = float(getattr(device, "power_draw_w", 0) or 0)
    if nominal <= 0:
        return 0.0
    load = max(0.0, min(1.0, float(getattr(device, "cpu_usage", 0) or 0) / 100.0))
    compute_w = nominal * (0.55 + 0.45 * load)
    spd = fan_speed_frac(device, default_inlet_c)
    fan_w = nominal * (FAN_FULL_FRAC - FAN_BASE_FRAC) * (spd ** 3)
    return compute_w + fan_w

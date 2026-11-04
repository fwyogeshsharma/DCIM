"""What a box reports drawing is what the power chain books for it.

A BMC's PowerConsumedWatts and a switch's PSU-input watts are measured at the
PSU input, so the chassis fans are inside them. The chain (DeviceStateStore.
_server_live_watts) has long added the fan rise on a hot intake; the reported
side did not, so a PDU above a warm rack metered power that no server plugged
into it admitted to drawing.
"""

from __future__ import annotations

from types import SimpleNamespace

from core import power_draw
from core.redfish_data_generator import _live_watts


def _box(inlet: float, cpu: float = 50.0, nominal: int = 800, state: str = "On"):
    return SimpleNamespace(power_draw_w=nominal, cpu_usage=cpu, inlet_temp=inlet,
                           power_state=state)


def test_a_cool_intake_has_no_fan_rise():
    box = _box(inlet=22.0)
    assert power_draw.live_watts(box) == 800 * (0.55 + 0.45 * 0.5)


def test_a_hot_intake_adds_the_fan_rise_to_what_the_bmc_reports():
    cool, hot = _box(inlet=22.0), _box(inlet=37.0)
    assert _live_watts(hot) > _live_watts(cool)
    rise = 800 * (power_draw.FAN_FULL_FRAC - power_draw.FAN_BASE_FRAC) * (12 / 15) ** 3
    assert abs(_live_watts(hot) - (_live_watts(cool) + rise)) <= 1


def test_reported_and_booked_are_the_same_number():
    for inlet in (20.0, 26.0, 33.0, 41.0):
        box = _box(inlet=inlet, cpu=73.0)
        assert _live_watts(box) == round(power_draw.live_watts(box))


def test_the_state_store_books_the_shared_formula():
    from core.device_state_store import DeviceStateStore
    box = _box(inlet=35.0, cpu=40.0)
    booked = DeviceStateStore._server_live_watts(None, box)
    assert booked == power_draw.live_watts(box, 22.0)


def test_a_powered_off_box_draws_and_reports_nothing():
    box = _box(inlet=35.0, state="Off")
    assert power_draw.live_watts(box) == 0.0 and _live_watts(box) == 0

"""The BMC reports the chassis it is bolted to, not a copy it took once.

Two bugs, one after the other, both of which reached a running simulator.

The first was the design: RedfishDevice copied `device.power_state` at
construction, so pulling both cords set the Device to Off while the BMC went on
answering On - and the simulator's own API, which reads the BMC's copy, called
a de-energised server a running one.

The second was the repair. The property went in immediately above

    @property
    def member_id(self) -> str:

and split the decorator from its function, so `member_id` became a plain method
and the doubled `@property` above the getter made `rdev.power_state` a property
object. Every /api/redfish/status request then died with

    TypeError: 'property' object is not callable

The suite did not notice, because nothing here had ever read either attribute.
That is the gap these tests close: they are cheap, and they are about the two
places where an accessor stops being an accessor.
"""

from __future__ import annotations

import pytest

from core.device_manager import Device, DeviceType, Vendor
from simulator.redfish_device import RedfishDevice


@pytest.fixture
def bmc():
    device = Device(name="SRV01-DC1-HA-R1-01", device_type=DeviceType.SERVER,
                    vendor=Vendor.DELL, model_name="Dell PowerEdge R750",
                    ip_address="10.2.2.1")
    return RedfishDevice(device, "admin", "password"), device


def test_power_state_follows_the_chassis(bmc):
    """Whatever turns the box off - a thermal trip, or losing every cord - the
    BMC has to say so. It is bolted to that chassis."""
    rdev, device = bmc
    assert rdev.power_state == "On"

    device.power_state = "Off"
    assert rdev.power_state == "Off", "the BMC is reporting a stale copy"


def test_a_redfish_action_still_reaches_the_chassis(bmc):
    """The read-through must not make the BMC read-only: a Redfish power action
    is how an operator turns a server off, and it has to move the Device that
    every other plane reads."""
    rdev, device = bmc
    rdev.power_state = "Off"
    assert device.power_state == "Off"
    rdev.power_state = "On"
    assert device.power_state == "On"


def test_member_id_is_still_a_property(bmc):
    """The one the insertion broke.

    A plain method here answers every status request with a bound method where
    a string belongs, and JSON serialisation dies on it.
    """
    rdev, _device = bmc
    assert isinstance(rdev.member_id, str)
    assert rdev.member_id, "member_id must resolve to an id, not a callable"


def test_the_status_summary_serialises(bmc):
    """The exact shape the REST layer builds, which is where the TypeError
    surfaced - after the change had been committed, pushed and restarted."""
    import json

    rdev, _device = bmc
    summary = {"device": rdev.device.name,
               "member_id": rdev.member_id,
               "power_state": rdev.power_state,
               "sessions": rdev.session_list()}
    assert json.dumps(summary)

"""The plant override channel is keyed by device NAME, not by bind IP.

This is the prerequisite for moving field gear (valves, pump VFDs, header
instruments) behind a BACnet/IP router or a Modbus gateway: once a device stops
being an IP node it has no bind address to key on, and an override keyed on one
would silently never match.

That failure is not hypothetical. api/routers/bacnet._device_name records the
previous outage: resolving to ip_address keyed every plant device under "", one
colliding never-matched bucket, so plant fault injection did nothing and never
showed ACTIVE. These tests exist so the identity cannot drift back.
"""
from __future__ import annotations

from core.device_manager import Device, DeviceType, Vendor


def _valve(name="VCHW1-DC1-CP", mgmt="", ip=""):
    return Device(name=name, device_type=DeviceType.VALVE, vendor=Vendor.BELIMO,
                  model_name="Belimo PR..A-BAC", ip_address=ip, mgmt_ip=mgmt)


class _DM:
    def __init__(self, devices):
        self._by_id = {d.id: d for d in devices}

    def get_device(self, device_id):
        return self._by_id.get(device_id)


def test_override_key_is_the_device_name(monkeypatch):
    from api.routers import bacnet
    dev = _valve(mgmt="10.52.14.31")

    class _S:
        device_manager = _DM([dev])
    monkeypatch.setattr(bacnet, "_state", lambda: _S())
    assert bacnet._device_name(dev.id) == "VCHW1-DC1-CP"


def test_a_device_with_no_address_still_resolves(monkeypatch):
    """The whole point: field gear behind a router owns no IP, and the override
    must still reach it."""
    from api.routers import bacnet
    dev = _valve(mgmt="", ip="")

    class _S:
        device_manager = _DM([dev])
    monkeypatch.setattr(bacnet, "_state", lambda: _S())
    assert bacnet._device_name(dev.id) == "VCHW1-DC1-CP"


def test_addressless_devices_do_not_collide(monkeypatch):
    """The exact shape of the documented outage: two devices with no address must
    not collapse into one bucket."""
    from api.routers import bacnet
    a = _valve("VCHW1-DC1-CP")
    b = _valve("VCW1-DC1-CP")

    class _S:
        device_manager = _DM([a, b])
    monkeypatch.setattr(bacnet, "_state", lambda: _S())
    assert bacnet._device_name(a.id) != bacnet._device_name(b.id)


def test_unknown_device_returns_none(monkeypatch):
    from api.routers import bacnet

    class _S:
        device_manager = _DM([])
    monkeypatch.setattr(bacnet, "_state", lambda: _S())
    assert bacnet._device_name("nope") is None


def test_the_controller_matches_overrides_on_the_same_key():
    """The store writes the override and the BACnet controller reads it. If the
    two ever disagree about the key, injection goes quiet with nothing to see."""
    import inspect
    from simulator import bacnet_controller
    src = inspect.getsource(bacnet_controller.BACnetController.tick)
    assert 'get(getattr(dev, "device_name", "")' in src, (
        "controller must look overrides up by device_name")
    assert 'get(getattr(dev, "device_ip"' not in src, (
        "controller is still keying overrides on the bind IP")

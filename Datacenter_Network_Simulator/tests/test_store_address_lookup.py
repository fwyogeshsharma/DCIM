"""A device answers on every address it owns, not just its production one.

`DeviceStateStore.get_metrics(ip)` is what gives gNMI (and anything else that
asks the store) the LIVE value of a metric instead of the value its dataset was
generated with. It resolves the caller's IP to a device, and it used to do that
against `ip_address` alone.

Everything that manages this fleet the way a real NMS does talks to the OOB
management plane - that is what the plane is for - so every one of those calls
missed. `GNMIServicer._overlay` treats a miss as "no store" and falls back to
jittering the static dataset by +/-10, which is indistinguishable from live
data at a glance and is not live at all: a switch whose generated CPU landed at
92% reported 82-100% for days while the ticker walked the real figure down to
50, and the NMS watching it raised a CPU alert that no recovery could ever
clear.

SNMP never showed the bug because snmpsim is file-backed and those files are
named by the management IP already - which is exactly why this is worth a test
rather than a comment: the two planes disagreed for weeks and both looked fine.
"""

import pytest

from core.device_manager import Device, DeviceType, Vendor


class _FakeDM:
    def __init__(self, devices):
        self._devices = devices

    def get_all_devices(self):
        return self._devices


@pytest.fixture
def store():
    from core.device_state_store import DeviceStateStore

    dev = Device(
        name="SP1-DC2-HA-R1-01",
        device_type=DeviceType.SWITCH,
        vendor=Vendor.ARISTA_NETWORKS,
        ip_address="10.50.21.14",
    )
    dev.mgmt_ip = "10.51.21.19"
    dev.cpu_usage = 57

    s = DeviceStateStore.__new__(DeviceStateStore)   # no ticker, no threads
    s._dm = _FakeDM([dev])
    s._boot_times = {}
    return s, dev


def test_the_production_address_resolves(store):
    s, dev = store
    assert s._find_device("10.50.21.14") is dev


def test_the_management_address_resolves(store):
    """The one that was missing, and the one every poller actually uses."""
    s, dev = store
    assert s._find_device("10.51.21.19") is dev


def test_an_unknown_address_is_still_a_miss(store):
    """The fallback exists for genuinely unknown targets; do not break it."""
    s, _ = store
    assert s._find_device("10.99.99.99") is None


def test_live_metrics_reach_a_caller_on_the_management_plane(store):
    """The behaviour the whole thing is for: gNMI gets 57, not the template.

    If this returns None the servicer serves the dataset's birth value with
    jitter, which is the failure mode that produced a permanent CPU alert on
    eight switches that were never busy.
    """
    s, dev = store
    metrics = s.get_metrics("10.51.21.19")
    assert metrics is not None
    assert metrics["cpu_usage"] == dev.cpu_usage == 57


def test_a_device_with_no_management_ip_is_unaffected(store):
    """Most facility gear has one address. An empty mgmt_ip must not match ''."""
    s, dev = store
    dev.mgmt_ip = ""
    assert s._find_device("10.50.21.14") is dev
    assert s._find_device("") is None or s._find_device("") is dev

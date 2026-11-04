"""The IT and electrical degradations the DCIM's learned baselines are built to
catch (platform docs/35 step 4): battery ageing, a dried-out CPU thermal
interface, a worn fan, a failing optic.

Each moves the MACHINE - truth - so every protocol shows it, and each moves
only the device it is put on: what a whole room does is not this device's
fault, and the platform's detectors are judged on exactly that difference.
"""

from __future__ import annotations

import random

import pytest

from core.fault_injection import DEGRADATION_KINDS
from test_degradation import _degrade, _rig  # noqa: F401  (rig helpers)
from test_fault_publish_truth import api_state  # noqa: F401  (fixture)

SETTLE = 60


def test_the_four_kinds_are_declared_for_the_right_equipment():
    assert DEGRADATION_KINDS["battery_aging"]["types"] == ("ups",)
    assert DEGRADATION_KINDS["thermal_paste"]["types"] == ("server",)
    assert DEGRADATION_KINDS["fan_wear"]["types"] == ("server",)
    assert set(DEGRADATION_KINDS["link_errors"]["types"]) == {"switch", "router"}
    for k in ("battery_aging", "thermal_paste", "fan_wear", "link_errors"):
        assert DEGRADATION_KINDS[k]["healthy"] == 0.0


@pytest.fixture
def servers(tmp_path, api_state):
    ctl = _rig(tmp_path / "ctl", servers=8)
    ctl.tick(SETTLE)
    deg = _rig(tmp_path / "deg", servers=8)
    api_state(deg)
    deg.tick(SETTLE)
    return ctl, deg


def _server(r, i=0):
    from core.device_manager import DeviceType
    return sorted((d for d in r.h.dm.get_all_devices() if d.device_type == DeviceType.SERVER),
                  key=lambda d: d.name)[i]


def test_dried_paste_runs_the_cpu_hotter_and_only_on_that_server(servers):
    ctl, deg = servers
    srv = _server(deg)
    _degrade(deg, srv, "thermal_paste", 12.0)                       # 12 K, reached at once
    ctl.tick(900)                                                   # the die's time constant
    deg.tick(900)
    hot, cool = _server(deg).cpu_temp, _server(ctl).cpu_temp
    assert hot - cool == pytest.approx(12.0, abs=1.5)
    # A neighbour on the same intake and load is untouched.
    assert _server(deg, 1).cpu_temp == pytest.approx(_server(ctl, 1).cpu_temp, abs=0.5)


def test_a_worn_fan_runs_faster_than_its_siblings_on_the_wire(servers):
    from core.redfish_data_generator import _fans
    ctl, deg = servers
    srv = _server(deg)
    _degrade(deg, srv, "fan_wear", 0.3)
    deg.tick(5)
    ctl.tick(5)
    worn = deg.call(_fans, _server(deg))
    calm = ctl.call(_fans, _server(ctl))
    assert worn[0]["Reading"] > calm[0]["Reading"] * 1.25         # Fan 1, 30 % fast
    for i in (1, 2, 3):                                             # the others as they were
        assert abs(worn[i]["Reading"] - calm[i]["Reading"]) < 400


# ── UPS and switch: a small store of their own ────────────────────────────────

def _small_store(tmp_path):
    from core.device_manager import (Device, DeviceManager, DeviceType, Interface, Vendor)
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine
    random.seed(7)
    dm, topo = DeviceManager(), TopologyEngine()
    ups = Device(name="UPSA-DC1-UR", device_type=DeviceType.UPS, vendor=Vendor.VERTIV,
                 model_name="Vertiv Liebert EXL S1 1200kVA", ip_address="10.0.9.1")
    sw = Device(name="SW01-DC1-HA-R1-01", device_type=DeviceType.SWITCH,
                vendor=Vendor.CISCO_SYSTEMS, model_name="Cisco Nexus 9364C",
                ip_address="10.0.9.2", power_draw_w=500)
    peer = Device(name="SW02-DC1-HA-R1-02", device_type=DeviceType.SWITCH,
                  vendor=Vendor.CISCO_SYSTEMS, model_name="Cisco Nexus 9364C",
                  ip_address="10.0.9.3", power_draw_w=500)
    sw.interfaces = [Interface(index=1, name="mgmt0", connected_to_device=peer.id),
                     Interface(index=2, name="eth1/1", connected_to_device=peer.id),
                     Interface(index=3, name="eth1/2", connected_to_device=peer.id)]
    for d in (ups, sw, peer):
        dm.add_device(d)
        topo.add_device(d) if hasattr(topo, "add_device") else None
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    return store, ups, sw


def _machine(store, name, kind, value):
    """Put a degradation straight at its target (the API path is tested above)."""
    from core.fault_injection import Degradation
    store.faults.add_degradation(Degradation(id=f"t-{name}-{kind}", device=name, device_id=name,
                                             kind=kind, rate_per_h=1e9, target=value))
    store.faults.step_degradations(1.0)


def test_an_ageing_battery_runs_hot_and_loses_health(tmp_path):
    store, ups, _ = _small_store(tmp_path / "a")
    for _ in range(1200):                                          # settle on float first
        store._tick()
    base = store._ext_states[ups.name]["ups_battery_temp_c"]
    _machine(store, ups.name, "battery_aging", 6.0)
    for _ in range(1200):                                          # the string has mass
        store._tick()
    st = store._ext_states[ups.name]
    assert st["ups_battery_temp_c"] - base == pytest.approx(6.0, abs=1.0)
    assert st["ups_battery_health"] <= 100.0 - 3.0 * 6.0 + 0.1


def test_a_failing_optic_puts_its_errors_on_one_data_port(tmp_path):
    store, _, sw = _small_store(tmp_path / "b")
    for _ in range(5):
        store._tick()
    before = {i.name: i.in_errors for i in sw.interfaces}
    _machine(store, sw.name, "link_errors", 50.0)
    for _ in range(60):
        store._tick()
    gained = {i.name: i.in_errors - before[i.name] for i in sw.interfaces}
    assert gained["eth1/1"] >= 50 * 60 * 0.9                       # ~50 errors a second
    assert gained["eth1/2"] < 60 and gained["mgmt0"] < 60          # background trickle only


def test_fans_ramp_rather_than_jump(servers):
    """A BMC ramps its fans. The speed moves smoothly tick to tick, so a poll a
    minute apart sees a ramp, not the +1,300 / -600 rpm steps a CPU burst used
    to produce (which the DCIM's spike check rightly read as a faulty tach)."""
    _ctl, deg = servers
    srv = _server(deg)
    speeds = []
    for _ in range(120):
        deg.tick(1)
        speeds.append(_server(deg).fan_rpm)
    steps = [abs(b - a) for a, b in zip(speeds, speeds[1:], strict=False)]
    from core.device_manager import fan_rpm_range
    lo, hi = fan_rpm_range(srv.model_name or "")
    assert max(steps) < 0.08 * (hi - lo) + 60, (max(steps), lo, hi)

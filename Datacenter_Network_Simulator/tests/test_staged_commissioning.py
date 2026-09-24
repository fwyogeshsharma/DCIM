"""A server is requested, bought, delivered, racked, built and only then accepted.

The fleet engine used to collapse that into one instant: `_add_server` created a
device and commissioned it in the same breath, so the estate only ever contained
finished hardware. Every state a DCIM spends most of its time looking at was
reachable only by hand, which made the alarm-shelving the DCIM has for exactly
those states impossible to exercise.

What is pinned here is the ORDER, because it is the part the obvious design gets
wrong. Redfish power-on is the FIRST thing that happens to a racked machine, not
the last: you cannot flash firmware, soak it, or PXE an operating system onto a
chassis that is off. And what puts a machine into service is acceptance - an
administrative gate - by which time it has been powered for days.
"""
from __future__ import annotations

import pytest

import core.device_state_store as dss
from core import lifecycle as lc
from core.device_manager import Device, DeviceType, Vendor
from core.fleet_lifecycle import DaySummary, FleetLifecycleEngine


class _DM:
    def __init__(self):
        self.devices = {}

    def add(self, d):
        self.devices[d.id] = d
        return d

    def get_all_devices(self):
        return list(self.devices.values())

    def remove_device(self, did):
        self.devices.pop(did, None)


class _Topo:
    def __init__(self):
        self.removed = []

    def remove_device(self, did):
        self.removed.append(did)


class _IPM:
    def __init__(self):
        self.released = []

    def release(self, ip):
        self.released.append(ip)


class _State:
    def __init__(self):
        self.device_manager = _DM()
        self.topology = _Topo()
        self.ip_manager = _IPM()
        self.selected_adapter = ""
        self.state_store = None
        self.executor = None
        self.snmpsim = None
        self.redfish = self.gnmi = self.bacnet = self.modbus = None
        self.notified = []

    def notify_ui(self, what):
        self.notified.append(what)


@pytest.fixture
def eng(monkeypatch):
    e = FleetLifecycleEngine(_State(), log_cb=lambda *_: None)
    # The protocol side is proved elsewhere; what matters here is WHEN it is
    # asked to bring a device up, and with what on the device.
    e.commissioned = []
    e.decommissioned = []
    monkeypatch.setattr(e, "_bind_ip", lambda ip: None)
    monkeypatch.setattr(e, "_gen_datasets", lambda d: None)
    real_commission = e._commission
    monkeypatch.setattr(e, "_commission", lambda d: (
        e.commissioned.append((d.name, lc.state_of(d),
                               bool(getattr(d, "os_deployed", True)))),
        real_commission(d))[1])
    monkeypatch.setattr(e, "_decommission_net",
                        lambda d: e.decommissioned.append(d.name))
    monkeypatch.setattr(dss, "_lifecycle_offline_cache", set())
    return e


def _srv(eng, name="SRV-NEW", state="planned", os_deployed=False, power="Off"):
    d = Device(name=name, device_type=DeviceType.SERVER, vendor=Vendor.SUPERMICRO,
               ip_address="10.50.1.10", mgmt_ip="10.51.1.10", lifecycle=state)
    d.os_deployed = os_deployed
    d.power_state = power
    eng.s.device_manager.add(d)
    eng._stage_day[d.id] = eng.day
    return d


def _days(eng, n):
    """Run n sim-days of pipeline only, and return every promotion."""
    out = []
    for _ in range(n):
        eng.day += 1
        summ = DaySummary(day=eng.day)
        eng._advance_pipeline(summ)
        out.extend(summ.promoted)
    return out


# ----------------------------------------------------------------- the arc

def test_a_server_walks_the_whole_path_in_order(eng):
    """The sequence, end to end, with nothing skipped and nothing out of order."""
    c = eng.cfg
    d = _srv(eng)

    seen = []

    def state():
        return (lc.state_of(d), bool(d.os_deployed), d.power_state,
                lc.on_wire(d), lc.os_agent_up(d))

    seen.append(("day 0", state()))
    for _ in range(c.procurement_lead_days + c.dock_to_rack_days
                   + c.burn_in_days + c.acceptance_days + 2):
        _days(eng, 1)
        cur = state()
        if not seen or seen[-1][1] != cur:
            seen.append((f"day {eng.day}", cur))

    path = [s[1][0] for s in seen]
    assert path == ["planned", "in_stock", "installed", "installed", "in_service"]

    # planned: nothing, and drawing nothing - the rack unit is reserved, not used.
    assert seen[0][1] == ("planned", False, "Off", False, False)
    # delivered: on a dock. Still nothing.
    assert seen[1][1] == ("in_stock", False, "Off", False, False)
    # racked: POWERED, and the BMC is the only thing answering.
    assert seen[2][1] == ("installed", False, "On", True, False)
    # imaged: the production NIC joins. Still not accepted.
    assert seen[3][1] == ("installed", True, "On", True, True)
    # accepted.
    assert seen[4][1] == ("in_service", True, "On", True, True)


def test_power_comes_on_before_the_os_not_after(eng):
    """The inversion this whole model exists to get right.

    A four-step plan that ends "turn it on with Redfish, then it is in service"
    has the order backwards twice: the chassis must be powered to be flashed,
    soaked and imaged at all, and what makes it in-service is acceptance, days
    later.
    """
    c = eng.cfg
    d = _srv(eng)
    _days(eng, c.procurement_lead_days + c.dock_to_rack_days)

    assert lc.state_of(d) == "installed"
    assert d.power_state == "On", "racked hardware is energised, not waiting"
    assert not d.os_deployed, "and has no OS on it yet"
    # Which is exactly when an OS may be laid down, and not before.
    assert lc.can_deploy_os(d)[0]


def test_an_unpowered_box_cannot_be_imaged(eng):
    """You cannot PXE a chassis that is off. The gate says so rather than
    silently producing a machine with an OS and no power."""
    d = _srv(eng, state="installed", power="Off")
    ok, why = lc.can_deploy_os(d)

    assert not ok
    assert "power it on over Redfish" in why


# ------------------------------------------------- reserved is not consumed

def test_ordered_hardware_reserves_its_slot_without_drawing(eng):
    """The distinction that keeps capacity honest.

    A `planned` server holds its rack unit, its power budget and its addresses
    from the day the PO is approved - two projects must not be sold one slot. It
    must not also show up as LOAD, or every PDU, UPS and PUE figure counts
    hardware that is still at the vendor.
    """
    d = _srv(eng)

    assert d.power_state == "Off"
    assert lc.power_state_for(d) == "Off"
    # The existing cascade reads 0 W for a chassis that is off, which is why no
    # second mechanism is needed - see DeviceStateStore._live_device_watts.
    assert d.power_draw_w > 0, "the nameplate is still there to be reserved against"


def test_burn_in_draws_real_power(eng):
    """A soak is a full-power test and SHOULD show on the UPS. A commissioning
    window that was invisible to the power chain would be modelling the paperwork
    rather than the work."""
    c = eng.cfg
    d = _srv(eng)
    _days(eng, c.procurement_lead_days + c.dock_to_rack_days)

    assert lc.state_of(d) == "installed"
    assert d.power_state == "On"


# ------------------------------------------------------ what gets asked when

def test_nothing_is_commissioned_before_it_is_racked(eng):
    """`planned` and `in_stock` must never reach the protocol servers. The engine
    calls _commission on the way in regardless, and the lifecycle gate inside it
    is what makes that safe - so this checks the gate is actually load-bearing."""
    c = eng.cfg
    d = _srv(eng)
    _days(eng, c.procurement_lead_days)

    assert lc.state_of(d) == "in_stock"
    # It was asked for, and refused by the gate rather than by the caller.
    assert all(state != "in_service" for _n, state, _o in eng.commissioned)
    assert not any(lc.on_wire(x) for x in [d])


def test_the_machine_is_recommissioned_when_its_os_lands(eng):
    """Not only on the first stage. What a device SERVES changes: `installed`
    with no OS writes a BMC dataset only, and the same box once imaged needs its
    OS dataset too. Only a regeneration does that."""
    c = eng.cfg
    d = _srv(eng)
    _days(eng, c.procurement_lead_days + c.dock_to_rack_days)
    before = len(eng.commissioned)

    _days(eng, c.burn_in_days)

    assert d.os_deployed
    assert len(eng.commissioned) > before, (
        "the OS deploy did not regenerate; the production NIC would have no "
        "dataset to answer from")


# -------------------------------------------------------------- leaving

def test_a_decommissioned_box_goes_dark_but_keeps_its_rack_unit(eng):
    """Drained and powered down, still bolted in. That is what a DCIM sees on a
    real floor between the change and somebody walking to the rack - and the slot
    is not free for reuse until the box is actually out."""
    d = _srv(eng, state="in_service", os_deployed=True, power="On")
    summ = DaySummary(day=eng.day)

    eng.cfg.decommission_lambda = 1
    eng._decommission(summ)
    # _decommission keeps a floor of 4 servers, so drive the move directly.
    if lc.state_of(d) == "in_service":
        eng._set_stage(d, summ, "decommissioned", note="drained")

    assert lc.state_of(d) == "decommissioned"
    assert d.power_state == "Off"
    assert not lc.on_wire(d)
    assert d.id in eng.s.device_manager.devices, "still racked"
    assert d.name in eng.decommissioned, "and off every protocol plane"


def test_it_is_unracked_once_it_has_drained(eng):
    d = _srv(eng, state="decommissioned", power="Off")

    _days(eng, eng.cfg.decom_drain_days)

    assert d.id not in eng.s.device_manager.devices
    assert d.id in eng.s.topology.removed
    assert "10.50.1.10" in eng.s.ip_manager.released


def test_only_live_machines_are_picked_for_decommissioning(eng):
    """Retiring hardware that has not arrived is not a thing. Before this the
    candidate pool was every server, so a box on a dock could be decommissioned
    on the day it was ordered."""
    for i in range(8):
        _srv(eng, name=f"SRV-P{i}", state="planned")
    summ = DaySummary(day=eng.day)
    eng.cfg.decommission_lambda = 3

    eng._decommission(summ)

    assert summ.promoted == []


# ------------------------------------------------------------- the scheduler

def test_a_hand_moved_device_joins_the_pipeline(eng):
    """Derived from the devices, not from a registry of in-flight work.

    Somebody who moves a machine to `installed` through the API gets it carried
    the rest of the way, and nothing can be stranded by a registry that lost an
    entry.
    """
    d = _srv(eng, state="installed", os_deployed=False, power="On")
    del eng._stage_day[d.id]

    _days(eng, eng.cfg.burn_in_days + eng.cfg.acceptance_days + 1)

    assert lc.state_of(d) == "in_service"


def test_an_unknown_stage_day_restarts_the_wait(eng):
    """A restart loses the sim clock, so a device found mid-commissioning with no
    entry serves its wait from now. Treating it as infinitely old would complete
    every in-flight commissioning the moment the simulator came up."""
    d = _srv(eng, state="planned")
    del eng._stage_day[d.id]

    _days(eng, 1)
    assert lc.state_of(d) == "planned", "the wait restarted rather than elapsed"
    # Stamped as of the day it was noticed, so the full lead time still has to
    # elapse from here.
    assert eng._stage_day[d.id] == eng.day
    _days(eng, eng.cfg.procurement_lead_days - 1)
    assert lc.state_of(d) == "planned", "it completed early; the stamp was ignored"
    _days(eng, 1)
    assert lc.state_of(d) == "in_stock"


def test_maintenance_is_not_moved_by_the_scheduler(eng):
    """Somebody parked it deliberately. The day loop does not decide when work
    is finished."""
    d = _srv(eng, state="maintenance", os_deployed=True, power="On")

    _days(eng, 30)

    assert lc.state_of(d) == "maintenance"


def test_turning_staging_off_restores_the_old_behaviour(eng):
    """One switch back to provision-straight-to-live, because an existing demo or
    test that wants a finished estate should not have to wait five weeks of sim
    time for one."""
    eng.cfg.staged_commissioning = False
    d = _srv(eng, state="planned")

    _days(eng, 60)

    assert lc.state_of(d) == "planned", "the pipeline is off, so nothing moves it"


def test_the_census_separates_racked_from_built(eng):
    """"310 servers" hides that 12 are boxes on a dock. The panel needs the
    breakdown, and `installed` has to be split by whether an OS is on it - the
    two halves look completely different to a poller."""
    _srv(eng, name="A", state="planned")
    _srv(eng, name="B", state="in_stock")
    _srv(eng, name="C", state="installed", os_deployed=False, power="On")
    _srv(eng, name="D", state="installed", os_deployed=True, power="On")
    _srv(eng, name="E", state="in_service", os_deployed=True, power="On")

    census = eng.status()["commissioning"]

    assert census == {"planned": 1, "in_stock": 1, "installed (no OS)": 1,
                      "installed": 1, "in_service": 1}

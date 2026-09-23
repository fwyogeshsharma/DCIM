"""What a lifecycle state does to what answers a poll.

The field is worthless on its own - a string in an export nothing reads. What is
worth protecting is the consequence: a device that has not been racked yet
answers NOTHING, and a server that has been racked but not built answers on its
BMC and nowhere else. That second case is the one a DCIM most often gets wrong,
and it is the one this simulator could not previously produce at all.

The realism claim being pinned, because it is the part somebody will want to
change later without reading why: `installed` silences a SERVER's production NIC
and does NOT silence a switch's agent. A server racked this morning has its
iDRAC on the OOB switch and no operating system; a switch has been through ZTP
and is configured, and only its cut-over is pending. Making both silent would
invent a difference that does not exist on real hardware.
"""
from __future__ import annotations

import pytest

import core.device_state_store as dss
from core import lifecycle as lc
from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator


def _dev(dtype="server", state="in_service", ip="10.50.1.10", mgmt="10.51.1.10"):
    return Device(
        name=f"{dtype}-{state}",
        device_type=DeviceType(dtype),
        vendor=Vendor.SUPERMICRO,
        ip_address=ip,
        mgmt_ip=mgmt,
        lifecycle=state,
    )


# ------------------------------------------------------------------- the field

def test_a_topology_written_before_the_field_loads_as_a_live_estate():
    """Every existing topology file has no `lifecycle` key, and every device in
    it was live. Defaulting to anything else would take a working estate dark on
    upgrade."""
    d = _dev()
    raw = {k: v for k, v in d.to_dict().items() if k != "lifecycle"}

    assert Device.from_dict(raw).lifecycle == "in_service"
    assert lc.DEFAULT == "in_service"


def test_the_state_survives_an_export_import_round_trip():
    """The DCIM reads the export; so does a saved topology. A field that did not
    round-trip would reset every device to live on restart."""
    d = _dev(state="installed")

    assert d.to_dict()["lifecycle"] == "installed"
    assert Device.from_dict(d.to_dict()).lifecycle == "installed"


def test_an_unknown_state_is_coerced_rather_than_raising():
    """A hand-edited topology with a typo must still load. Taking the whole file
    down over one field means an operator cannot open the simulator to fix it."""
    assert _dev(state="bogus").lifecycle == "in_service"
    assert lc.normalise("  INSTALLED ") == "installed"
    assert not lc.is_valid("bogus")


def test_the_states_match_the_dcim_exactly():
    """A state the DCIM can hold and the simulator cannot is a state the pair
    cannot be tested against each other in."""
    assert lc.STATES == ("planned", "in_stock", "installed", "in_service",
                         "maintenance", "decommissioned", "retired")


# ------------------------------------------------------------ what answers

@pytest.mark.parametrize("state", ["planned", "in_stock", "decommissioned",
                                   "retired"])
def test_hardware_that_is_not_there_answers_nothing(state):
    """Not built, boxed on a shelf, or gone. No agent, no BMC, no ICMP."""
    for dtype in ("server", "switch", "pdu"):
        d = _dev(dtype, state)
        assert not lc.on_wire(d)
        assert lc.is_offline(d)
        assert not lc.os_agent_up(d)
        assert not lc.bmc_up(d)
        assert SNMPRecGenerator.snmp_bind_ips(d) == []


@pytest.mark.parametrize("state", ["in_service", "maintenance"])
def test_planned_work_does_not_silence_anything(state):
    """`maintenance` reports exactly as `in_service` does.

    Real gear does not stop answering because a ticket says somebody is working
    on it - which is the whole reason alarm shelving is a separate idea from
    reachability. A device that went quiet during planned work would be
    indistinguishable from one that died during it.
    """
    d = _dev("server", state)
    assert lc.on_wire(d) and lc.os_agent_up(d) and lc.bmc_up(d)
    assert SNMPRecGenerator.snmp_bind_ips(d) == ["10.50.1.10", "10.51.1.10"]


def test_a_racked_but_unbuilt_server_answers_on_its_bmc_and_nowhere_else():
    """The commissioning state, and the one a DCIM mis-reads as half a fault.

    You rack it, patch the iDRAC into the OOB switch and power it on. The
    controller is up; there is no operating system, so nothing is listening on
    the production NIC. Redfish and BMC SNMP answer, the OS agent times out, and
    both facts are correct at the same time.
    """
    d = _dev("server", "installed")

    assert lc.on_wire(d)
    assert lc.bmc_up(d)
    assert not lc.os_agent_up(d)
    # The mgmt IP only - which is the BMC's, not the OS agent's.
    assert SNMPRecGenerator.snmp_bind_ips(d) == ["10.51.1.10"]
    assert SNMPRecGenerator.bmc_address(d) == "10.51.1.10"


def test_installed_does_not_silence_a_switch():
    """A switch in `installed` has been through ZTP: it is configured, reachable
    and waiting on a cut-over, so on the wire it is indistinguishable from
    in_service. The difference is a DCIM fact - shelve its alarms - not silence,
    and inventing the silence would model hardware that does not behave that
    way."""
    d = _dev("switch", "installed")

    assert lc.on_wire(d)
    assert lc.os_agent_up(d), "a switch's NOS agent runs before acceptance"
    assert SNMPRecGenerator.snmp_bind_ips(d) == ["10.51.1.10"]


# --------------------------------------------------------- the dataset on disk

@pytest.fixture
def gen(tmp_path):
    return SNMPRecGenerator(str(tmp_path))


def _topo(*devices):
    """A topology holding just these devices - enough for generate_device, which
    walks neighbours to build the LLDP tables."""
    from core.topology_engine import TopologyEngine

    t = TopologyEngine()
    for d in devices:
        t.add_device(d)
    return t


def _files(gen):
    return {p.name for p in gen.output_dir.glob("*.snmprec")}


def test_an_unbuilt_server_gets_a_bmc_dataset_and_no_os_dataset(gen, plant_cache):
    """snmpsim serves files. The OS dataset existing IS the OS agent answering,
    so a machine with no OS must not have one - a dataset of plausible zeros
    would have a DCIM inventorying an operating system, an uptime and a CPU load
    for a box that has not been built."""
    d = _dev("server", "installed")

    assert gen.generate_device(d, None) == ""
    assert _files(gen) == {"10.51.1.10.snmprec"}


def test_moving_back_to_installed_takes_the_os_dataset_off_disk(gen, plant_cache):
    """Removed, not merely left unwritten.

    snmpsim serves whatever is on disk, so a device pulled back for rework would
    otherwise keep answering from the file it had while it was in service - the
    same trap that once had a de-energised server reporting an uptime of zero.
    """
    d = _dev("server", "in_service")
    gen.generate_device(d, _topo(d))
    assert "10.50.1.10.snmprec" in _files(gen)

    d.lifecycle = "installed"
    gen.generate_device(d, _topo(d))

    assert "10.50.1.10.snmprec" not in _files(gen)
    assert "10.51.1.10.snmprec" in _files(gen)


def test_the_guard_is_in_generate_device_not_only_in_the_loop(gen, plant_cache):
    """`generate_all` is not the only caller: the fleet engine's hot-commission
    path calls `generate_device` directly, so a gate in the loop would have
    covered a full regeneration and missed every device added while the simulator
    was running."""
    src = (SNMPRecGenerator.generate_device.__doc__ or "")
    assert "hot-commission" in src

    d = _dev("server", "planned")
    # Off the wire entirely: even the BMC has nothing to answer with.
    gen.generate_device(d, None)
    assert _files(gen) == set()


# ------------------------------------------------- one question, two causes

def test_power_and_lifecycle_are_two_causes_of_one_silence(monkeypatch):
    """Consumers ask `_is_off_wire`, not `_is_unpowered`.

    A device with no live cord is dark; so is one that has not been racked. Both
    look identical to a poller, every consumer wants the same answer, and none of
    them should have to know there are two reasons - which is what went wrong in
    the DCIM's shelving for the same shape of problem.
    """
    monkeypatch.setattr(dss, "_unpowered_cache", {"dead-cords"})
    monkeypatch.setattr(dss, "_lifecycle_offline_cache", {"not-racked"})

    assert dss._is_unpowered("dead-cords")
    assert not dss._is_unpowered("not-racked")
    assert dss._is_lifecycle_offline("not-racked")

    for name in ("dead-cords", "not-racked"):
        assert dss._is_off_wire(name)
    assert not dss._is_off_wire("healthy")


def test_the_dataset_gate_reads_the_combined_question(gen, monkeypatch,
                                                      plant_cache):
    """Either cause must remove the dataset, or snmpsim answers for a box that
    cannot."""
    d = _dev("server", "in_service")
    gen.generate_device(d, _topo(d))
    assert "10.50.1.10.snmprec" in _files(gen)

    monkeypatch.setattr(dss, "_lifecycle_offline_cache", {d.name})
    assert gen._is_dark(d)


def test_a_boxed_spare_sends_no_traps(monkeypatch):
    """A trap from a device on a shelf is as wrong as one from a de-energised
    chassis, and for the same reason: nothing in there is running the agent that
    would have sent it."""
    from core import trap_engine

    d = _dev("server", "in_stock")
    monkeypatch.setattr(dss, "_lifecycle_offline_cache", {d.name})

    assert trap_engine._is_dark(d)


def test_the_offline_set_is_derived_from_the_devices():
    """Four things change a lifecycle - the fleet engine, add-device, a topology
    load and the transition endpoint - and a cache four writers must remember to
    update is a cache that is wrong. It is re-derived on the tick instead."""
    devs = [_dev("server", "in_service"), _dev("switch", "planned"),
            _dev("pdu", "retired")]

    assert lc.offline_names(devs) == {"switch-planned", "pdu-retired"}


# ----------------------------------------------------- nothing is brought up

class _FakeState:
    """Just enough AppState for the commission path. Every protocol server is
    absent, so `_commission` skips them and what is left to observe is whether it
    tried to bind an address and write a dataset at all."""

    def __init__(self):
        self.selected_adapter = ""
        self.topology = None
        self.device_manager = None
        self.notified = []
        # The protocol servers the info payload consults. Absent = not running,
        # which is what `_device_to_info` reads them for.
        self.redfish = None
        self.gnmi = None
        self.bacnet = None
        self.modbus = None
        self.snmp = None

    def notify_ui(self, what):
        self.notified.append(what)


@pytest.fixture
def engine():
    from core.fleet_lifecycle import FleetLifecycleEngine

    return FleetLifecycleEngine(_FakeState(), log_cb=lambda *_: None)


@pytest.mark.parametrize("state", ["planned", "in_stock", "decommissioned",
                                   "retired"])
def test_commissioning_an_absent_device_does_nothing_at_all(engine, monkeypatch,
                                                            state):
    """The gate is at the top of `_commission`, which is the one place every
    device passes through on its way to being live - rather than in each of the
    five protocol branches below it, where the next protocol added would miss
    it."""
    calls = []
    monkeypatch.setattr(engine, "_bind_ip", lambda ip: calls.append(("bind", ip)))
    monkeypatch.setattr(engine, "_gen_datasets", lambda d: calls.append(("gen",)))

    engine.commission_device(_dev("server", state))

    assert calls == [], f"{state} hardware was brought onto the wire"


@pytest.mark.parametrize("state", ["installed", "in_service", "maintenance"])
def test_commissioning_a_present_device_binds_and_generates(engine, monkeypatch,
                                                            state):
    calls = []
    monkeypatch.setattr(engine, "_bind_ip", lambda ip: calls.append(("bind", ip)))
    monkeypatch.setattr(engine, "_gen_datasets", lambda d: calls.append(("gen",)))

    engine.commission_device(_dev("server", state))

    assert ("gen",) in calls
    assert ("bind", "10.50.1.10") in calls
    # And the BMC's address, which is what a racked-but-unbuilt server answers on.
    assert ("bind", "10.51.1.10") in calls


# ----------------------------------------------------------- the transition

def test_a_transition_republishes_the_offline_set_before_touching_the_planes(
        monkeypatch):
    """Order matters, and getting it wrong is silent.

    The dataset generator reads the off-wire set through the store's module
    cache. A regeneration that ran before the set was republished would write a
    dataset for the device this very call has just taken off the wire - and
    snmpsim serves whatever is on disk, so it would answer for it indefinitely.
    """
    from api.routers import devices as router

    order = []

    dev = _dev("server", "in_service")

    class _DM:
        def get_device(self, _id):
            return dev

        def get_all_devices(self):
            return [dev]

    class _Eng:
        def commission_device(self, d):
            order.append(("commission", set(dss._lifecycle_offline_cache)))

        def _decommission_net(self, d):
            order.append(("decommission", set(dss._lifecycle_offline_cache)))

    state = _FakeState()
    state.device_manager = _DM()
    state.fleet_engine = _Eng()
    monkeypatch.setattr(router, "_state", lambda: state)
    monkeypatch.setattr(dss, "_lifecycle_offline_cache", set())

    from api.models.schemas import LifecycleRequest

    info = router.set_lifecycle("any-id", LifecycleRequest(to_state="in_stock"))

    assert info.lifecycle == "in_stock"
    assert info.on_wire is False
    assert info.snmp_ips == [], "a boxed spare advertises no agent"
    # Taken off the wire, and the set already said so when it happened.
    assert order == [("decommission", {dev.name})]
    assert "sync_devices" in state.notified


def test_a_transition_back_onto_the_wire_recommissions(monkeypatch):
    """Re-commissioned even when the device was ALREADY on the wire, because the
    datasets differ between states: in_service -> installed keeps the BMC dataset
    and must lose the OS one, and only a regeneration does that."""
    from api.routers import devices as router
    from api.models.schemas import LifecycleRequest

    calls = []
    dev = _dev("server", "in_service")

    class _DM:
        def get_device(self, _id):
            return dev

        def get_all_devices(self):
            return [dev]

    class _Eng:
        def commission_device(self, d):
            calls.append("commission")

        def _decommission_net(self, d):
            calls.append("decommission")

    state = _FakeState()
    state.device_manager = _DM()
    state.fleet_engine = _Eng()
    monkeypatch.setattr(router, "_state", lambda: state)
    monkeypatch.setattr(dss, "_lifecycle_offline_cache", set())

    info = router.set_lifecycle("any-id", LifecycleRequest(to_state="installed"))

    assert calls == ["commission"]
    assert info.on_wire is True
    assert info.snmp_ips == ["10.51.1.10"], "BMC only; there is no OS yet"


def test_an_unknown_state_is_refused_rather_than_silently_ignored(monkeypatch):
    """A typo that left the device live while the caller believed it had been
    moved is worse than an error."""
    from fastapi import HTTPException

    from api.routers import devices as router
    from api.models.schemas import LifecycleRequest

    dev = _dev("server", "in_service")

    class _DM:
        def get_device(self, _id):
            return dev

        def get_all_devices(self):
            return [dev]

    state = _FakeState()
    state.device_manager = _DM()
    monkeypatch.setattr(router, "_state", lambda: state)

    with pytest.raises(HTTPException) as exc:
        router.set_lifecycle("any-id", LifecycleRequest(to_state="decomissioned"))

    assert exc.value.status_code == 422
    assert "in_service" in str(exc.value.detail), "the error lists what IS valid"
    assert dev.lifecycle == "in_service", "and the device was not moved"

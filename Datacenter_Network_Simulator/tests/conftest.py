"""Shared fixtures. Puts the repo root on sys.path so `core.*` imports work when
pytest is run from anywhere."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def plant_cache():
    """The module-level BACnet present-value cache the store reads plant health from.

    Yields a dict to seed as {device_name: {point: value}} and clears it afterwards,
    so tests that fake alarm/run states cannot leak into one another.
    """
    from core.device_state_store import _plant_state_cache

    _plant_state_cache.clear()
    yield _plant_state_cache
    _plant_state_cache.clear()


# ── Fixture topology ─────────────────────────────────────────────────────────
# A minimal but STRUCTURALLY REAL chiller plant: N complete cooling trains
# (chiller + its evaporator pump + its condenser pump + its tower cell), a header
# standby CHW pump, and enough servers to make a load. Wired on the `cooling` layer
# exactly the way the shipped topology is, because _build_trains discovers trains by
# walking those edges — a fixture that hand-assembled the trains instead would test
# nothing about the discovery.

CITY = "chicago"
DC = "DC1"
ROOM = "Hall A"
CHILLER_W = 120_000      # nameplate electrical, per unit
PUMP_W = 15_000
TOWER_W = 45_000
CRAH_W = 11_000
CDU_W = 8_000
SERVER_W = 700


def _device(dm, name, dtype, ip, watts, model="", room=ROOM, dc=DC):
    from core.device_manager import Device, DeviceType, Vendor

    d = Device(
        name=name,
        device_type=DeviceType(dtype),
        vendor=Vendor.SUPERMICRO,
        ip_address=ip,
        model_name=model,
        power_draw_w=watts,
    )
    d.datacenter = dc
    d.datacenter_city = CITY
    d.room = room
    dm.add_device(d)
    return d


class PlantFixture:
    """Handle on the built plant: the store plus name→device lookup."""

    def __init__(self, store, dm, topo, trains, made=None):
        self.store = store
        self.dm = dm
        self.topo = topo
        self.trains = trains
        self.made = made or {}
        self.dcs = [DC]

    def name(self, n):
        return next(d for d in self.dm.get_all_devices() if d.name == n)

    def tick(self):
        """Run the per-tick cooling chain in the order the real ticker does."""
        self.store._compute_leak_heat()
        self.store._compute_cond_loop()
        self.store._compute_chw_penalty()
        self.store._compute_power_flow()
        self.store._compute_chw_loop()

    def auto_points(self, n):
        """Synthetic BACnet points the store publishes for device *n* this tick."""
        # Keyed by device NAME — the identity that survives field gear
        # losing its address when it moves behind a router/gateway.
        return self.store._plant_auto_points.get(n, {})

    def stage_on(self, dc=DC):
        return self.store._plant_stage_on.get(dc)

    def standby(self):
        return set(self.store._plant_standby_names)

    def running_chillers(self, dc=DC):
        return {t["chiller"] for t in self.store._plant_trains_run.get(dc, [])}

    def power(self, n):
        return self.store._plant_power_by_name.get(n, 0.0)


def build_plant(tmp_path, trains=3, servers=40, installed_modules=6, crahs=0,
                probes=False, valves=False, cdus=0, rack_probes=0,
                tick_interval=1.0):
    """Assemble a one-DC plant and return a PlantFixture.

    `installed_modules` is forced rather than derived, so a test can put the plant
    at a chosen duty without having to conjure thousands of servers.

    `crahs` defaults to 0 — the air side is only needed by the tests that exercise
    it, and adding CRAHs changes the plant's nameplate sum (and therefore its duty
    fraction), which the staging/rotation tests are calibrated against. `probes`
    likewise adds the plant's header instruments only where they are under test.

    `valves` adds the two header control valves. Off by default for the same
    reason as the others: they are header equipment common to every train, so
    they change the cooling-loss arithmetic for every test that does not need
    them.

    `tick_interval` seeds the store's dt. Only the time-base tests vary it —
    everything else wants the 1 s tick the rest of the suite is calibrated on.
    """
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    dm, topo = DeviceManager(), TopologyEngine()
    made = _build_dc(dm, topo, DC, 0, trains, servers, crahs, probes, valves,
                     cdus, rack_probes)

    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=tick_interval)
    store._plant_installed_mods[DC] = installed_modules
    return PlantFixture(store, dm, topo, trains, made)


def _build_dc(dm, topo, dc, net, trains, servers, crahs=0, probes=False,
              valves=False, cdus=0, rack_probes=0):
    """Devices + cooling-layer wiring for ONE datacenter, into a SHARED dm/topo.

    *net* is the second IP octet, so two sites in one store never collide — the
    store keys published plant points by device name (_plant_auto_points)
    and duplicate addresses would silently merge one site's telemetry into the
    other's, which is precisely the kind of cross-DC bleed these fixtures exist to
    catch. Device NAMES already carry the site segment, so they are unique anyway.
    """
    made = {}

    # BACnet/IP <-> MS/TP router. Pump VFD cards (Grundfos CIM 300) and header
    # valve actuators (Belimo "-BAC") sit on its RS-485 trunk and own NO address,
    # exactly as the shipped topology models them. A fixture that gave them IPs
    # would leave this harness — the one that has already caught real bugs —
    # testing a world the simulator no longer has.
    mstp_net = 2000 + (net % 100)
    # .9.x belongs to the rack probes below — the router takes its own octet.
    router_ip = f"10.{net}.8.1"
    router = _device(dm, f"BRTR1-{dc}-CP", "bacnet_router", "", 12,
                     model="LOYTEC LINX-151", room="Central Plant", dc=dc)
    router.mgmt_ip = router_ip
    router.mstp_net = mstp_net
    router.mstp_children = []
    made["BRTR"] = router

    def _mstp(device, mac):
        """Put a field device on the trunk: no address, no port, MAC only.

        Goes through attach_to_mstp_trunk rather than setting the fields, because
        the portless rule runs in __post_init__ and has already fired by now —
        assigning mstp_mac by hand leaves the device holding an Ethernet port it
        cannot have.
        """
        device.attach_to_mstp_trunk(mstp_net, mac, router_ip)
        router.mstp_children.append(device.name)
        return device

    # MS/TP master MACs. Separate bases per class so a pump and a valve can never
    # land on the same address (mirrors tools/migrate_mstp_field_devices.py).
    _PUMP_MAC, _VALVE_MAC = 1, 20

    for i in range(1, trains + 1):
        made[f"CHL{i}"] = _device(dm, f"CHL{i}-{dc}-CP", "chiller",
                                  f"10.{net}.1.{i}", CHILLER_W,
                                  model="chiller-1000t", dc=dc)
        made[f"CHWP{i}"] = _mstp(_device(dm, f"CHWP{i}-{dc}-CP", "pump",
                                         "", PUMP_W, dc=dc), _PUMP_MAC + i - 1)
        made[f"CWP{i}"] = _mstp(_device(dm, f"CWP{i}-{dc}-CP", "pump",
                                        "", PUMP_W, dc=dc),
                                _PUMP_MAC + trains + i)
        made[f"CT{i}"] = _device(dm, f"CT{i}-{dc}-RF", "cooling_tower",
                                 f"10.{net}.4.{i}", TOWER_W, room="Roof", dc=dc)
    # Header standby CHW pump — index beyond the trains, so _build_trains leaves it
    # unclaimed and reports it as the N+1 spare.
    spare = trains + 1
    made[f"CHWP{spare}"] = _mstp(_device(dm, f"CHWP{spare}-{dc}-CP", "pump",
                                         "", PUMP_W, dc=dc),
                                 _PUMP_MAC + spare - 1)

    if probes:
        # Plant header instruments. Named with the role code leading, and carrying
        # the "Plant …" model names, exactly as the shipped topology does — the
        # store reads the role off both, so a fixture that shortcut either would
        # test nothing about the dispatch.
        #
        # They carry NO address, and hang off a Modbus gateway by unit id, which
        # is how the shipped topology models them: a thermowell is an RTD in a
        # pipe, not a network node. Giving them IPs here would leave this harness
        # — the one that has already caught two real bugs — modelling a world the
        # simulator no longer has.
        gw_ip = f"10.{net}.6.1"
        gateway = _device(dm, f"MBGW1-{dc}-CP", "modbus_gateway", "", 15,
                          model="Moxa MGate MB3480", room="Central Plant", dc=dc)
        gateway.mgmt_ip = gw_ip
        gateway.modbus_role = "gateway"
        gateway.modbus_children = []
        made["MBGW"] = gateway

        for j, (code, model) in enumerate((
                ("CHWS", "Plant CHW Supply Temp"),
                ("CHWR", "Plant CHW Return Temp"),
                ("FLOW", "Plant CHW Flow Meter"),
                ("CWS", "Plant CW Supply Temp"),
                ("CWR", "Plant CW Return Temp"),
                ("CTB", "Plant CT Basin Temp")), start=1):
            probe = _device(dm, f"{code}-{dc}-CP", "sensor", "", 0, model=model,
                            room="Central Plant", dc=dc)
            probe.modbus_role = "rtu_slave"
            probe.modbus_unit_id = j
            probe.modbus_gateway_ip = gw_ip
            gateway.modbus_children.append(probe.name)
            made[code] = probe

    if valves:
        # Header control valves. The leading name segment carries the loop the
        # valve sits in — VCHW on the evaporator side, VCW on the condenser side —
        # which is the same role-in-the-prefix idiom the header probes use, and
        # which the store reads to decide which loop an actuator fault throttles.
        for j, code in enumerate(("VCHW", "VCW"), start=1):
            made[code] = _mstp(_device(dm, f"{code}-{dc}-CP", "valve",
                                       "", 0, room="Central Plant", dc=dc),
                               _VALVE_MAC + j - 1)

    for i in range(1, crahs + 1):
        made[f"CRAH{i}"] = _device(dm, f"CRAH{i}-{dc}-HA-R1-01", "crah",
                                   f"10.{net}.5.{i}", CRAH_W, dc=dc)

    for i in range(1, cdus + 1):
        made[f"CDU{i}"] = _device(dm, f"CDU{i}-{dc}-HA-R1-01", "cdu",
                                  f"10.{net}.8.{i}", CDU_W, dc=dc)

    # RACK environmental probes — the DPX2-style cold-aisle sensors, distinct from
    # the plant header instruments `probes=` adds. No "Plant …" model name, so the
    # store leaves them on the ambient path rather than publishing a header reading
    # into them.
    if rack_probes:
        # A DPX2 plugs into a PX2's RJ-12 sensor port and is read through that
        # PDU's agent — it has no address of its own. The rack PDU therefore has
        # to exist before the probes do, and it is what carries them.
        pdu_ip = f"10.{net}.9.1"
        rack_pdu = _device(dm, f"PDUA-{dc}-HA-R1-01", "pdu", "", 0,
                           model="Raritan PX2-5170CR", dc=dc)
        rack_pdu.mgmt_ip = pdu_ip
        rack_pdu.sensor_children = []
        made["PDUA"] = rack_pdu

        slot = 1
        for i in range(1, rack_probes + 1):
            probe = _device(dm, f"SNS{i}-{dc}-HA-R1-01", "sensor", "", 0,
                            model="DPX2-T2H1", dc=dc)
            # attach_to_sensor_port, not a bare field assignment: the portless
            # rule runs in __post_init__ and has already fired by now, so setting
            # host_pdu_ip by hand leaves the probe holding an Ethernet port.
            probe.attach_to_sensor_port(pdu_ip, slot)
            rack_pdu.sensor_children.append(probe.name)
            slot += 2                      # a T2H1 occupies two slots (temp + humidity)
            made[f"SNS{i}"] = probe

    for i in range(1, servers + 1):
        made[f"SRV{i}"] = _device(dm, f"SRV{i:02d}-{dc}-HA-R1-01", "server",
                                  f"10.{net + 1}.0.{i}", SERVER_W, dc=dc)

    for d in made.values():
        topo.add_device(d)

    # Cooling-layer wiring: each chiller to its own pumps and cell, plus the spare
    # CHW pump hung off train 1's header (mirrors the shipped topology).
    for i in range(1, trains + 1):
        c = made[f"CHL{i}"].id
        for peer in (f"CHWP{i}", f"CWP{i}", f"CT{i}"):
            topo.add_link(c, made[peer].id, layer="cooling")
    topo.add_link(made["CHL1"].id, made[f"CHWP{spare}"].id, layer="cooling")

    # Cold-plate loops: each CDU takes a slice of the servers. The store discovers
    # cdu_by_server by walking these cooling-layer links, so wiring them is what
    # makes a CDU a real loop rather than an unattached box.
    for i in range(1, cdus + 1):
        for j in range(i, servers + 1, max(1, cdus)):
            topo.add_link(made[f"CDU{i}"].id, made[f"SRV{j}"].id, layer="cooling")
    return made


def build_two_dc_plant(tmp_path, dcs=(DC, "DC2"), trains=3, servers=40,
                       installed_modules=6, crahs=0, tick_interval=1.0):
    """Two complete, independent sites sharing ONE store — the shape production
    actually runs, and the one the rest of this suite cannot see.

    Almost every cooling field on the store is keyed per DC (`_chw_pen`,
    `_cool_loss_frac`, `_plant_trains_run`, `_run_proof_s`, `_chw_pump_frac`, …),
    so a pass that walks one of those maps globally while running once PER SITE
    will corrupt whichever site it does not belong to. That is not hypothetical:
    `_accrue_run_proof` expired every other DC's run-proof timers on each tick, so
    `cooling_degraded` answered "healthy" through a total silent loss of chilled
    water on the live two-site topology while the single-DC gate stayed green.

    Sites are identical by construction, so any asymmetry a test finds is the
    store leaking state between them rather than the fixture favouring one.
    """
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    dm, topo = DeviceManager(), TopologyEngine()
    made = {}
    for k, dc in enumerate(dcs):
        # Ten apart, so a site's server block (net + 1) never lands on the next
        # site's plant block.
        made[dc] = _build_dc(dm, topo, dc, k * 10, trains, servers, crahs)

    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=tick_interval)
    for dc in dcs:
        store._plant_installed_mods[dc] = installed_modules
    fx = PlantFixture(store, dm, topo, trains, made)
    fx.dcs = list(dcs)
    return fx


@pytest.fixture
def plant(tmp_path, plant_cache):
    """Default fixture plant: 3 trains, 40 servers, 6 installed modules.

    Depends on plant_cache so the BACnet present-value cache is clean — the store
    reads plant health from it, and a stale entry from another test would change
    which trains are eligible.
    """
    return build_plant(tmp_path)

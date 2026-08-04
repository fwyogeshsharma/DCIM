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


def _device(dm, name, dtype, ip, watts, model="", room=ROOM):
    from core.device_manager import Device, DeviceType, Vendor

    d = Device(
        name=name,
        device_type=DeviceType(dtype),
        vendor=Vendor.SUPERMICRO,
        ip_address=ip,
        model_name=model,
        power_draw_w=watts,
    )
    d.datacenter = DC
    d.datacenter_city = CITY
    d.room = room
    dm.add_device(d)
    return d


class PlantFixture:
    """Handle on the built plant: the store plus name→device lookup."""

    def __init__(self, store, dm, topo, trains):
        self.store = store
        self.dm = dm
        self.topo = topo
        self.trains = trains

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
        ip = self.store._plant_ip_by_name.get(n, "")
        return self.store._plant_auto_points.get(ip, {})

    def stage_on(self, dc=DC):
        return self.store._plant_stage_on.get(dc)

    def standby(self):
        return set(self.store._plant_standby_names)

    def running_chillers(self):
        return {t["chiller"] for t in self.store._plant_trains_run.get(DC, [])}

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
    made = {}

    for i in range(1, trains + 1):
        made[f"CHL{i}"] = _device(dm, f"CHL{i}-{DC}-CP", "chiller",
                                  f"10.0.1.{i}", CHILLER_W, model="chiller-1000t")
        made[f"CHWP{i}"] = _device(dm, f"CHWP{i}-{DC}-CP", "pump", f"10.0.2.{i}", PUMP_W)
        made[f"CWP{i}"] = _device(dm, f"CWP{i}-{DC}-CP", "pump", f"10.0.3.{i}", PUMP_W)
        made[f"CT{i}"] = _device(dm, f"CT{i}-{DC}-RF", "cooling_tower",
                                 f"10.0.4.{i}", TOWER_W, room="Roof")
    # Header standby CHW pump — index beyond the trains, so _build_trains leaves it
    # unclaimed and reports it as the N+1 spare.
    spare = trains + 1
    made[f"CHWP{spare}"] = _device(dm, f"CHWP{spare}-{DC}-CP", "pump",
                                   f"10.0.2.{spare}", PUMP_W)

    if probes:
        # Plant header instruments. Named with the role code leading, and carrying
        # the "Plant …" model names, exactly as the shipped topology does — the
        # store reads the role off both, so a fixture that shortcut either would
        # test nothing about the dispatch.
        for j, (code, model) in enumerate((
                ("CHWS", "Plant CHW Supply Temp"),
                ("CHWR", "Plant CHW Return Temp"),
                ("FLOW", "Plant CHW Flow Meter"),
                ("CWS", "Plant CW Supply Temp"),
                ("CWR", "Plant CW Return Temp"),
                ("CTB", "Plant CT Basin Temp")), start=1):
            made[code] = _device(dm, f"{code}-{DC}-CP", "sensor", f"10.0.6.{j}", 0,
                                 model=model, room="Central Plant")

    if valves:
        # Header control valves. The leading name segment carries the loop the
        # valve sits in — VCHW on the evaporator side, VCW on the condenser side —
        # which is the same role-in-the-prefix idiom the header probes use, and
        # which the store reads to decide which loop an actuator fault throttles.
        for j, code in enumerate(("VCHW", "VCW"), start=1):
            made[code] = _device(dm, f"{code}-{DC}-CP", "valve", f"10.0.7.{j}", 0,
                                 room="Central Plant")

    for i in range(1, crahs + 1):
        made[f"CRAH{i}"] = _device(dm, f"CRAH{i}-{DC}-HA-R1-01", "crah",
                                   f"10.0.5.{i}", CRAH_W)

    for i in range(1, cdus + 1):
        made[f"CDU{i}"] = _device(dm, f"CDU{i}-{DC}-HA-R1-01", "cdu",
                                  f"10.0.8.{i}", CDU_W)

    # RACK environmental probes — the DPX2-style cold-aisle sensors, distinct from
    # the plant header instruments `probes=` adds. No "Plant …" model name, so the
    # store leaves them on the ambient path rather than publishing a header reading
    # into them.
    for i in range(1, rack_probes + 1):
        made[f"SNS{i}"] = _device(dm, f"SNS{i}-{DC}-HA-R1-01", "sensor",
                                  f"10.0.9.{i}", 0, model="DPX2-T2H1")

    for i in range(1, servers + 1):
        made[f"SRV{i}"] = _device(dm, f"SRV{i:02d}-{DC}-HA-R1-01", "server",
                                  f"10.1.0.{i}", SERVER_W)

    for d in dm.get_all_devices():
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

    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=tick_interval)
    store._plant_installed_mods[DC] = installed_modules
    return PlantFixture(store, dm, topo, trains)


@pytest.fixture
def plant(tmp_path, plant_cache):
    """Default fixture plant: 3 trains, 40 servers, 6 installed modules.

    Depends on plant_cache so the BACnet present-value cache is clean — the store
    reads plant health from it, and a stale entry from another test would change
    which trains are eligible.
    """
    return build_plant(tmp_path)

"""The CDU cold-plate loop map must survive a topology arriving late.

The ticker calls `_compute_leak_heat` — and therefore `_cdu_loop_servers()` — on
every tick from the moment the store starts, which on a headless server is well
before any topology is uploaded. That map used to cache UNCONDITIONALLY, so it
froze at {} for the life of the process, and nothing on the upload path rebuilt it
(the only caller of `invalidate_cooling_context` is a device edit).

Two silent consequences, both measured on the live app before the fix:

  * a full CDU coolant leak forced on all six DC1 CDUs moved the hottest die
    66.7 -> 67.5 C over ten minutes. `_leak_heat` was empty, and that leak is the
    ONLY mechanism in the model that can reach the 90 C throttle — server inlet is
    clamped at 45 C, so load alone saturates well short of it.
  * `_liquid_cooled_servers()` is derived from the same map, so every direct-to-chip
    server read as AIR-cooled: die = 38 + 0.45*usage instead of 35 + 0.30*usage.
    The fleet die mean sat on the air curve (60.5 C at 49.5 % load, where the air
    formula predicts 60.3 and the liquid one 49.9).

`_cooling_context` already had exactly this bug and was fixed by keying on the
device inventory; its docstring says the failure "looks like a working plant".
These two caches were left unconditional. Same fix, same reason.
"""
import random

from conftest import DC, build_plant


def test_the_loop_map_rebuilds_when_the_topology_arrives_late(tmp_path, plant_cache):
    """Ask BEFORE the plant exists, then ask again after. A run-lifetime cache
    answers {} forever; an inventory-keyed one rebuilds."""
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    empty_store = DeviceStateStore(DeviceManager(), TopologyEngine(), str(tmp_path))
    assert empty_store._cdu_loop_servers() == {}, "nothing wired yet"
    assert empty_store._liquid_cooled_servers() == set()

    # The real plant shows up later, exactly as /topology/upload delivers it.
    random.seed(20260804)
    plant_cache.clear()
    p = build_plant(tmp_path / "late", trains=3, servers=40,
                    installed_modules=6, crahs=2, cdus=2)

    loops = p.store._cdu_loop_servers()
    assert loops, "the loop map must rebuild once the cooling edges exist"
    assert p.store._liquid_cooled_servers(), "DLC servers must be discoverable"


def test_a_stale_loop_map_does_not_outlive_its_inventory(tmp_path, plant_cache):
    """Prime the caches, then move the inventory. Both must rebuild — the liquid
    set is derived from the loop map, so checking its own cache first would return
    the stale answer forever."""
    random.seed(20260804)
    plant_cache.clear()
    p = build_plant(tmp_path / "grow", trains=3, servers=40,
                    installed_modules=6, crahs=2, cdus=2)

    before_loops = dict(p.store._cdu_loop_servers())
    before_liquid = set(p.store._liquid_cooled_servers())
    assert before_loops and before_liquid

    # Force the signature to move the way a fleet add would.
    p.store._cdu_loop_servers_cache = {"STALE-CDU": {"STALE-SRV"}}
    p.store._liquid_servers_cache = {"STALE-SRV"}
    p.store._cdu_loop_sig = -999

    assert p.store._cdu_loop_servers() == before_loops, "loop map must rebuild"
    assert p.store._liquid_cooled_servers() == before_liquid, (
        "the liquid set is derived from the loop map and must rebuild with it")


def test_a_leak_reaches_the_die_on_a_late_arriving_plant(tmp_path, plant_cache):
    """End to end: the failure the live probe hit. A leak on a plant whose caches
    were primed empty must still heat the servers on that loop."""
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    # Prime the same global cache path the running app poisons on boot.
    DeviceStateStore(DeviceManager(), TopologyEngine(), str(tmp_path))._cdu_loop_servers()

    random.seed(20260804)
    plant_cache.clear()
    p = build_plant(tmp_path / "leak", trains=3, servers=40,
                    installed_modules=6, crahs=2, cdus=2)
    for _ in range(4):
        p.tick()

    cdu = f"CDU1-{DC}-HA-R1-01"
    on_loop = p.store._cdu_loop_servers()[cdu]
    assert on_loop, "the leaking CDU must have servers on its loop"

    plant_cache[cdu] = {"Alarm_Leak": 1.0, "TCS_Loop_Pressure": 140.0}
    p.tick()
    assert p.store._leak_heat, "a forced leak must produce leak heat"
    assert max(p.store._leak_heat.values()) == 1.0, "a fully open leak is full severity"

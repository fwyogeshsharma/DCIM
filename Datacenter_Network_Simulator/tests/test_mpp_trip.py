"""S1-11 (doc 32 §13.2 rpp-trip-crah): a mechanical panel's main breaker trips.

CRAHs are fed from MECHANICAL distribution (MCC -> MPP), not from IT RPPs, so
the realistic power-chain root for "half a hall's CRAHs drop at once" is the
MPP. There was no injection path for it: the REST link-break refuses the power
layer and FAULT_MAP had no MPP/MCC entry. The trip de-energises the panel's
loads through the store's existing energization, the same path a utility loss
takes, and is ledgered as ``electrical``.

Run against the SHIPPED topology, because the point is which units that one
panel actually feeds.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"
PANEL = "MPPA-DC1-HA"


@pytest.fixture
def site(tmp_path, monkeypatch, plant_cache):
    from api.state import AppState
    from core.device_manager import DeviceManager
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine

    data = json.loads(TOPO.read_text(encoding="utf-8-sig"))
    dm, topo = DeviceManager(), TopologyEngine()
    topo.from_dict(data)
    for d in topo.get_all_devices():
        dm.add_device(d)
    store = DeviceStateStore(dm, topo, str(tmp_path), tick_interval=1.0)
    store._dt = 1.0
    holder = SimpleNamespace(state_store=store, device_manager=dm, topology=topo,
                             notify_ui=lambda *a: None)
    monkeypatch.setattr(AppState, "get", classmethod(lambda cls: holder))
    return store, dm


def _ha_crahs(dm):
    return {d.name for d in dm.get_all_devices()
            if d.device_type.value == "crah" and "-DC1-HA-" in d.name}


def _fed_by(store, dm, panel_name):
    ctx = store._power_context()
    pid = next(d.id for d in dm.get_all_devices() if d.name == panel_name)
    seen, stack = set(), [pid]
    while stack:
        for c in ctx["children"].get(stack.pop(), []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return pid, {store._device_name(n) for n in seen} & _ha_crahs(dm)


def test_the_panel_trip_unpowers_exactly_its_crahs_and_clears(site):
    from api.routers.devices import FaultRequest, get_device_faults, set_device_fault
    store, dm = site
    pid, fed = _fed_by(store, dm, PANEL)
    ha = _ha_crahs(dm)
    assert fed and fed < ha, (fed, ha)            # half the hall, not all of it
    assert len(fed) == 4 and len(ha) == 7         # doc 32 §13.2: R9-01/03/05/07
    store._step_transfer()
    assert not (store._plant_unpowered_names & ha)

    out = set_device_fault(pid, FaultRequest(fault="mpp_breaker_trip", action="start"))
    assert out.ok
    assert "mpp_breaker_trip" in get_device_faults(pid)["active"]
    store._step_transfer()
    assert store._plant_unpowered_names & ha == fed
    en = store._compute_energized(store._power_context())
    assert en[pid] is False
    # Ledgered as an electrical injection.
    from core.fault_ledger import get_ledger
    e = get_ledger().query(kind="electrical")["entries"][0]
    assert e["device"] == PANEL and e["end"] is None and e["expected_kind"] == "process"

    set_device_fault(pid, FaultRequest(fault="mpp_breaker_trip", action="clear"))
    store._step_transfer()
    assert not (store._plant_unpowered_names & ha)
    assert get_ledger().get(e["id"])["end_reason"] == "cleared"


def test_an_unpowered_crah_delivers_nothing_at_once(site):
    """No ride-through on the air side: the 60 s chilled-water thermal-mass
    grace applies to the water plant (train members), while a CRAH with a dead
    panel stops moving air immediately and the room books the loss."""
    store, dm = site
    pid, fed = _fed_by(store, dm, PANEL)
    store.set_panel_condition(pid, "breaker_trip", True)
    store._step_transfer()
    for n in fed:
        assert store._crah_delivered_frac(n) == 0.0

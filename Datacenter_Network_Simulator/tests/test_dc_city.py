"""S0-2 (doc 32 §9.1, §14): the DC → city map is a majority vote, from ONE helper.

DC2 weather was New York only because a New York device came first in insertion
order; ten DC2 sensors carried "Chicago". A re-save that reordered nodes would
have moved the whole site's wet bulb by ~3 K.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TOPO = ROOT / "topologies" / "dual_dc_enterprise.json"


def _d(name, dc, city):
    return SimpleNamespace(name=name, datacenter=dc, datacenter_city=city)


def test_first_device_wrong_still_yields_majority():
    from core.device_state_store import _dc_city_map
    devs = [_d("SEN1-DC2-HA-R2-01", "DC2", "Chicago")]
    devs += [_d(f"SRV{i}-DC2", "DC2", "New York") for i in range(5)]
    devs += [_d("SRV1-DC1", "DC1", "Chicago")]
    assert _dc_city_map(devs) == {"DC2": "New York", "DC1": "Chicago"}


def test_blank_dc_is_skipped_or_keyed():
    from core.device_state_store import _dc_city_map
    devs = [_d("X", "", "Chicago"), _d("Y", "DC1", "Chicago")]
    assert _dc_city_map(devs) == {"DC1": "Chicago"}
    assert _dc_city_map(devs, blank_dc="?") == {"?": "Chicago", "DC1": "Chicago"}


def test_missing_cities_do_not_vote():
    from core.device_state_store import _dc_city_map
    devs = [_d("A", "DC1", ""), _d("B", "DC1", None), _d("C", "DC1", "Chicago")]
    assert _dc_city_map(devs) == {"DC1": "Chicago"}
    assert _dc_city_map([_d("A", "DC9", "")]) == {"DC9": None}


def test_dissenters_are_logged(caplog):
    import core.device_state_store as dss
    dss._dc_city_dissent_seen = None
    devs = [_d("BAD", "DC2", "Chicago"), _d("A", "DC2", "New York"), _d("B", "DC2", "New York")]
    with caplog.at_level("WARNING", logger=dss.log.name):
        dss._dc_city_map(devs)
        dss._dc_city_map(devs)            # same set: logged once only
    hits = [r for r in caplog.records if "datacenter_city disagrees" in r.getMessage()]
    assert len(hits) == 1 and "BAD" in hits[0].getMessage()


def test_both_derivations_use_the_helper():
    """Two copies of a derivation is how one stayed buggy."""
    from core.device_state_store import DeviceStateStore
    for meth in (DeviceStateStore._cooling_context, DeviceStateStore._compute_power_flow):
        src = inspect.getsource(meth)
        assert "_dc_city_map(" in src, meth.__name__
        assert 'getattr(d, "datacenter_city", None)' not in src, meth.__name__


def test_shipped_topology_has_zero_dissenters():
    from tools.fix_dc_city_labels import plan
    doc = json.loads(TOPO.read_text(encoding="utf-8"))
    assert plan(doc) == []
    cities = {}
    for n in doc["nodes"]:
        dv = n["device"]
        if dv.get("datacenter") and dv.get("datacenter_city"):
            cities.setdefault(dv["datacenter"], set()).add(dv["datacenter_city"])
    assert cities.get("DC1") == {"Chicago"}
    assert cities.get("DC2") == {"New York"}


def test_fix_tool_is_dry_run_and_idempotent(tmp_path, capsys):
    from tools.fix_dc_city_labels import main
    doc = {"nodes": [{"device": {"name": "A", "datacenter": "DC2", "datacenter_city": "New York"}},
                     {"device": {"name": "B", "datacenter": "DC2", "datacenter_city": "New York"}},
                     {"device": {"name": "SEN1", "datacenter": "DC2", "datacenter_city": "Chicago",
                                 "other": "Chicago"}}]}
    p = tmp_path / "t.json"
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    p.write_bytes(text.replace("\n", "\r\n").encode())
    assert main([str(p)]) == 0
    assert p.read_bytes() == text.replace("\n", "\r\n").encode()      # dry run
    assert main([str(p), "--write"]) == 0
    after = p.read_bytes().decode()
    assert "\r\n" in after                                              # endings kept
    got = json.loads(after)
    assert got["nodes"][2]["device"]["datacenter_city"] == "New York"
    assert got["nodes"][2]["device"]["other"] == "Chicago"              # only the city field
    before = p.read_bytes()
    assert main([str(p), "--write"]) == 0
    assert p.read_bytes() == before                                     # idempotent

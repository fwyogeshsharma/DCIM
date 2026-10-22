"""S3-1 / S3-2 (doc 32 §13, §14): the campaign library and its runner, offline.

The runner is NEVER run against live here. These pin the safety contract
(a dry run makes no network call at all; fire needs --allow-fire; every
injection is cleared and every enabled detector restored in a finally), the
library's schema and scenario names (doc 33's scorer reads the same file), and
the S3-2 calibration write-back.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from tools import live_campaign_anomaly as lca

#: doc 32 §13.2-13.5, verbatim. Doc 33's harness joins on these names.
DOC32_NAMES = [
    "all-chillers", "all-chwp", "all-towers", "tower-cell-partial", "all-cwp", "hall-crahs",
    "crah-single-trip", "crah-coil-starve", "lead-chiller-stop", "valve-actuator-stuck",
    "valve-feedback-stuck", "chiller-short-cycle", "pump-proof-lost", "pump-shaft-coupling",
    "cdu-leak", "cdu-condensation", "utility-flap", "pdu-imbalance", "valve-hunting",
    "rpp-trip-crah",
    "chiller-cop-decay", "chiller-condenser-fouling", "tower-fill-fouling",
    "crah-filter-loading", "cdu-strainer-loading", "cdu-hx-fouling", "chwp-impeller-wear",
    "pump-bearing-heat", "tower-fan-imbalance", "tower-basin-leak", "cdu-slow-leak",
    "crah-setpoint-raise", "chw-reset",
    "crah-supply-bias", "crah-supply-bias-control", "rack-probe-drift", "pdu-probe-stuck",
    "chw-supply-noisy", "crah-return-dropout", "chw-flow-meter-bias", "bmc-inlet-bias",
    "tower-wetbulb-bias", "cdu-filter-dp-drift", "ups-battery-probe-drift",
    "clean-soak-7d", "heatwave", "fire-pre-discharge", "maintenance-crah", "sim-restart",
]


@pytest.fixture
def lib():
    return lca.load_library()


def test_the_library_validates_and_carries_exactly_the_doc_names(lib):
    assert lca.validate(lib) == []
    assert [s["name"] for s in lib["scenarios"]] == DOC32_NAMES
    by = {s["name"]: s for s in lib["scenarios"]}
    # Doc 29 seeded state: enable-for-run and pending flags.
    assert by["tower-cell-partial"]["enable_for_run"] == ["CT-2"]
    assert by["utility-flap"]["enable_for_run"] == ["AT-1"]
    assert by["tower-basin-leak"]["enable_for_run"] == ["CT-3"]
    assert by["crah-filter-loading"]["pending"] == "point_availability"
    assert by["chiller-condenser-fouling"]["pending"] == "point_availability"
    assert by["pump-shaft-coupling"]["pending"] == "s4"
    assert by["fire-pre-discharge"]["requires"]["allow_fire"] is True
    assert by["all-towers"]["expected_misses"] == ["CH-5"]
    assert by["valve-hunting"]["must_not"] == ["VA-1"]
    # Offline twins named in the library exist.
    import importlib
    tfc = importlib.import_module("test_fault_campaign")
    for s in lib["scenarios"]:
        if s["offline_twin"]:
            path, fn = s["offline_twin"].split("::")
            assert path == "tests/test_fault_campaign.py" and hasattr(tfc, fn), s["name"]


def test_a_dry_run_makes_no_network_call(monkeypatch, capsys):
    def boom(*a, **kw):
        raise AssertionError("a dry run touched the network")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert lca.main(DOC32_NAMES) == 0
    out = capsys.readouterr().out
    assert "DRY RUN - nothing was sent" in out
    assert "would be REFUSED: fire scenario without --allow-fire" in out
    assert lca.main(["--list"]) == 0


def test_fire_is_refused_without_allow_fire_before_any_call(monkeypatch, capsys):
    def boom(*a, **kw):
        raise AssertionError("refused fire still touched the network")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert lca.main(["fire-pre-discharge", "--send"]) == 3
    assert "REFUSED" in capsys.readouterr().out


def test_variant_merge(lib):
    s = lca.with_variant(lca.scenario(lib, "chiller-cop-decay"), "realistic")
    assert s["injections"][0]["body"]["rate_per_h"] == 0.0006
    assert s["injections"][0]["body"]["target"] == 0.80
    assert s["expected"][0]["window_s"]["min"] == 2 * 24 * 3600
    with pytest.raises(KeyError):
        lca.with_variant(lca.scenario(lib, "all-chillers"), "realistic")


def test_calibration_write_back(lib, tmp_path):
    measured = {"scenarios": {"crah-filter-loading": {"CR-4": {"delays_s": [30000, 26000, 41000]}},
                              "nope": {"X": {"delays_s": [1]}}}}
    new, changes = lca.apply_calibration(lib, measured)
    w = lca.scenario(new, "crah-filter-loading")["expected"][0]["window_s"]
    assert w == {"min": 26000, "max": 41000, "median": 30000, "calibrated": True, "runs": 3,
                 "design": {"min": 21600, "max": 72000}}
    assert lca.validate(new) == []
    assert lca.scenario(lib, "crah-filter-loading")["expected"][0]["window_s"]["calibrated"] is False
    assert any(n == "nope" for n, _d, _w in changes)
    # The CLI is dry unless --write.
    p = tmp_path / "lib.json"
    p.write_text(json.dumps(lib), encoding="utf-8")
    m = tmp_path / "m.json"
    m.write_text(json.dumps(measured), encoding="utf-8")
    assert lca.main(["--library", str(p), "--calibrate", str(m)]) == 0
    assert json.loads(p.read_text(encoding="utf-8")) == lib
    assert lca.main(["--library", str(p), "--calibrate", str(m), "--write"]) == 0
    assert lca.scenario(json.loads(p.read_text(encoding="utf-8")),
                        "crah-filter-loading")["expected"][0]["window_s"]["calibrated"]


def test_target_resolution_is_by_type_and_position():
    devs = [{"id": "c2", "name": "CHL2-DC1-CP", "device_type": "chiller", "datacenter": "DC1"},
            {"id": "c1", "name": "CHL1-DC1-CP", "device_type": "chiller", "datacenter": "DC1"},
            {"id": "c3", "name": "CHL1-DC2-CP", "device_type": "chiller", "datacenter": "DC2"},
            {"id": "s1", "name": "CHWS-DC1-CP", "device_type": "sensor", "datacenter": "DC1"}]
    plant = {"CHL1-DC1-CP": {"Chiller_Running": 0.0}, "CHL2-DC1-CP": {"Chiller_Running": 1.0}}
    T = {"device_type": "chiller", "datacenter": "DC1"}
    assert [d["id"] for d in lca.resolve_targets(dict(T, pick="all"), devs, plant)] == ["c1", "c2"]
    assert [d["id"] for d in lca.resolve_targets(dict(T, pick="first"), devs, plant)] == ["c1"]
    assert [d["id"] for d in lca.resolve_targets(dict(T, pick="lead"), devs, plant)] == ["c2"]
    assert [d["id"] for d in lca.resolve_targets(dict(T, pick="non_lead"), devs, plant)] == ["c1"]


# ── the runner against a recording fake (never the live stack) ──────────────

class FakeSim:
    def __init__(self):
        self.calls = []
        self.active = []
        self.devs = [{"id": "x1", "name": "CRAH1-DC1-HA-R9-01", "device_type": "crah",
                      "datacenter": "DC1"}]

    def get(self, path):
        self.calls.append(("GET", path))
        if path == "/devices":
            return self.devs
        if path.startswith("/bacnet/plant/metrics"):
            return [{"name": "CRAH1-DC1-HA-R9-01", "values": {"Unit_Running": 1.0}}]
        if path == "/faults":
            return {"active": self.active}
        return {}

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path == "/faults/degradation":
            self.active.append({"device": "CRAH1-DC1-HA-R9-01", "kind": "degradation"})
            return {"id": "flt_deg"}
        if path == "/faults/context":
            return {"id": "flt_ctx"}
        return {"ok": True}

    def delete(self, path):
        self.calls.append(("DELETE", path))
        return {}


class FakePlatform:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        return {"enabled": False}

    def patch(self, path, body):
        self.calls.append(("PATCH", path, body))


def test_runner_enables_injects_verifies_and_restores_everything(lib):
    sim, plat = FakeSim(), FakePlatform()
    t = [0.0]

    def clock():
        return t[0]

    def sleep(s):
        t[0] += max(s, 1.0)

    scn = lca.with_variant(lca.scenario(lib, "crah-filter-loading"), None)
    scn["enable_for_run"] = ["CR-4"]                    # exercise enable/restore
    r = lca.Runner(sim, plat, hold_scale=1e-4, sleep=sleep, clock=clock,
                   log=lambda *a: None, check_clean=False)
    res = r.run(scn, run_id="t1")
    assert res["campaign"] == "crah-filter-loading/t1" and res["all_landed"]
    posts = [c for c in sim.calls if c[0] == "POST" and c[1] == "/faults/degradation"]
    assert posts[0][2]["campaign"] == "crah-filter-loading/t1" and posts[0][2]["device"] == "x1"
    assert ("DELETE", "/faults/flt_deg?restore=true") in sim.calls
    assert plat.calls[1] == ("PATCH", "/api/v1/anomalies/detectors/CR-4", {"enabled": True})
    assert plat.calls[-1] == ("PATCH", "/api/v1/anomalies/detectors/CR-4", {"enabled": False})
    ctx = [c for c in sim.calls if c[0] == "POST" and c[1] == "/faults/context"]
    assert ctx[0][2]["params"]["detector"] == "CR-4" and ctx[-1][2]["close_id"] == "flt_ctx"
    assert sim.calls[0] == ("GET", "/air/influence")           # spatial gate first


def test_runner_clears_even_when_the_hold_is_interrupted(lib):
    sim = FakeSim()

    def sleep(s):
        raise KeyboardInterrupt

    scn = lca.scenario(lib, "crah-filter-loading")
    r = lca.Runner(sim, None, sleep=sleep, log=lambda *a: None, check_clean=False)
    with pytest.raises(KeyboardInterrupt):
        r.run(scn, run_id="t2")
    assert ("DELETE", "/faults/flt_deg?restore=true") in sim.calls


def test_runner_gates(lib):
    r = lca.Runner(FakeSim(), None, log=lambda *a: None, check_clean=False)
    for name in ("fire-pre-discharge", "pump-shaft-coupling", "sim-restart", "utility-flap"):
        with pytest.raises(lca.Refused):
            r.run(lca.scenario(lib, name))

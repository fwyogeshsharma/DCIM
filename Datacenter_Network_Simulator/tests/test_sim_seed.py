"""S0-1 (doc 32 §9.3, §14): stable hashing + DCIM_SIM_SEED.

Every restart used to step some probes by up to 0.6 K, because per-instrument
offsets came from the per-process salted builtin hash(). These tests run the
derivations in two SUBPROCESSES with different PYTHONHASHSEED values, so a
regression to hash() cannot hide behind one interpreter's salt.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
import json, sys
sys.path.insert(0, __ROOT__)
from core.sim_seed import stable_hash, stable_seed, GLOBAL_SEED
from core.bacnet_plant_generator import PlantTelemetryEngine
names = ["CRAH1-DC1-HA-R9-01", "CHL1-DC1-CP", "SEN1-DC2-HA-R2-01"]
out = {"global": GLOBAL_SEED, "engines": {}, "walk": {}, "offsets": {}}
for n in names:
    e = PlantTelemetryEngine("crah", rated_kw=11.0, seed=stable_seed(n))
    out["engines"][n] = dict(e._values)
    out["walk"][n] = [e._rng.random() for _ in range(5)]
    # The per-instrument offset formulas the store uses (room-air probe, UPS
    # battery probe, ASD background).
    out["offsets"][n] = [((stable_hash(n) % 13) - 6) / 10.0,
                         ((stable_hash(n) % 5) - 2) / 10.0,
                         (stable_hash(n) % 5) / 1000.0]
print(json.dumps(out, sort_keys=True))
"""


def _run(hashseed: str, sim_seed: str | None) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env.pop("DCIM_SIM_SEED", None)
    if sim_seed is not None:
        env["DCIM_SIM_SEED"] = sim_seed
    res = subprocess.run([sys.executable, "-c", _PROBE.replace("__ROOT__", repr(str(ROOT)))], env=env,
                         capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_same_seed_different_hashseed_is_identical():
    a = _run("1", "42")
    b = _run("12345", "42")
    assert a["global"] == 42
    assert a == b


def test_per_instrument_offsets_ignore_the_global_seed():
    """An instrument's calibration offset belongs to the instrument: it must
    survive restarts AND a seed change. Noise processes follow the seed."""
    a = _run("7", "42")
    b = _run("7", "43")
    assert a["offsets"] == b["offsets"]
    assert a["walk"] != b["walk"]


def test_unset_seed_still_stable_offsets():
    a = _run("1", None)
    b = _run("2", None)
    assert a["global"] == 0
    assert a["offsets"] == b["offsets"]
    assert a["engines"] == b["engines"]


def test_stable_hash_is_crc32():
    import zlib
    from core.sim_seed import stable_hash
    assert stable_hash("CHL1-DC1-CP") == zlib.crc32(b"CHL1-DC1-CP")


def test_no_builtin_hash_left_at_the_seed_sites():
    """The call sites doc 32 §9.3 names must not regress to builtin hash()."""
    import re
    from simulator import bacnet_controller
    from core import device_state_store, snmprec_generator, trap_engine
    pat = re.compile(r"(?<![_\w.])hash\(")
    for mod in (bacnet_controller, device_state_store, snmprec_generator, trap_engine):
        src = inspect.getsource(mod)
        hits = [ln for ln in src.splitlines()
                if pat.search(ln) and not ln.lstrip().startswith("#")]
        assert not hits, (mod.__name__, hits)


def test_engine_walk_noise_uses_its_own_rng():
    """The walk noise must not draw from the global stream, or thread
    interleaving would make a seeded run unrepeatable."""
    from core import bacnet_plant_generator as bpg
    src = inspect.getsource(bpg.PlantTelemetryEngine.tick)
    assert "random.uniform" not in src
    assert "self._rng.uniform" in src


def test_seed_global_random_only_when_set(monkeypatch):
    import random
    from core import sim_seed
    monkeypatch.setattr(sim_seed, "SEED_SET", False)
    state = random.getstate()
    assert sim_seed.seed_global_random() is False
    assert random.getstate() == state
    monkeypatch.setattr(sim_seed, "SEED_SET", True)
    monkeypatch.setattr(sim_seed, "GLOBAL_SEED", 42)
    assert sim_seed.seed_global_random() is True
    x = random.random()
    random.seed(42)
    assert random.random() == x

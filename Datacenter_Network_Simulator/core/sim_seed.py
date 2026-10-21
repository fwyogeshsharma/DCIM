"""Stable hashing and the simulator's global seed (doc 32 §9.3, S0-1).

Python salts ``str.__hash__`` per process, so every ``hash(name)`` in the
simulator used to change on each restart. Two things depended on it:

* the plant engines' initial walk offsets (``seed=hash(name)``), and
* per-instrument calibration offsets (room-air probe ±0.6 K, UPS battery probe
  ±0.2 K, ASD background, a Dell service tag …).

The second is a ground-truth defect: every restart silently stepped some probes
by up to 0.6 K, which an L0 bias detector would rightly flag and a ledger that
recorded nothing would score as a false positive.

Two rules:

* ``stable_hash(s)`` — a property of the NAME. Used for per-instrument offsets,
  which belong to the instrument and must survive restarts *and* seed changes.
* ``stable_seed(s)`` — ``stable_hash(s) ^ GLOBAL_SEED``. Used for noise
  processes (plant walk RNGs, fault RNGs), so a campaign re-run with the same
  ``DCIM_SIM_SEED`` gets the same per-device noise sequences.

Honest limit: a live run is statistically repeatable, not bit-exact. The
ambient model reads the wall clock, ``dt`` jitters, and other threads share the
global ``random`` stream.
"""
from __future__ import annotations

import os
import random
import zlib


def _read_seed() -> int:
    raw = os.environ.get("DCIM_SIM_SEED", "").strip()
    if not raw:
        return 0
    try:
        return int(raw, 0) & 0xFFFFFFFF
    except ValueError:
        return stable_hash(raw)


def stable_hash(s: str) -> int:
    """Process-independent 32-bit hash of *s* (CRC-32 of its UTF-8 bytes)."""
    return zlib.crc32(str(s).encode("utf-8")) & 0xFFFFFFFF


GLOBAL_SEED: int = _read_seed()
SEED_SET: bool = bool(os.environ.get("DCIM_SIM_SEED", "").strip())


def stable_seed(name: str) -> int:
    """Per-name RNG seed that follows ``DCIM_SIM_SEED``."""
    return (stable_hash(name) ^ GLOBAL_SEED) & 0xFFFFFFFF


def seed_global_random() -> bool:
    """Seed the global ``random`` module once at process start, only when
    ``DCIM_SIM_SEED`` is set. Unset keeps today's behaviour. Returns whether it
    seeded."""
    if SEED_SET:
        random.seed(GLOBAL_SEED)
        return True
    return False

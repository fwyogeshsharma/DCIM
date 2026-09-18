"""A device with no power sends no traps.

An in-row CDU on two tripped strips sent "plant unit stopped" thirty seconds
after losing its last cord. Its controller runs on the power that went; the
notification could not have left the box.
"""
import asyncio
import types

import core.device_state_store as dss
from core import trap_engine as te


def test_a_dark_device_is_dark_to_the_trap_path():
    dev = types.SimpleNamespace(name="CDU1-DARK")
    dss._unpowered_cache.add(dev.name)
    try:
        assert te._is_dark(dev)
    finally:
        dss._unpowered_cache.discard(dev.name)
    assert not te._is_dark(dev)


def test_the_raw_sender_returns_before_building_anything_for_a_dark_device():
    """Returns before the first pysnmp import - nothing is built, nothing sent."""
    dev = types.SimpleNamespace(name="CDU1-DARK")
    engine = te.TrapEngine.__new__(te.TrapEngine)
    dss._unpowered_cache.add(dev.name)
    try:
        # Would raise on the missing engine state if it went past the guard.
        asyncio.run(engine._send_raw_trap_async(dev, "1.3.6.1.4.1.9999.0.1", {}))
    finally:
        dss._unpowered_cache.discard(dev.name)

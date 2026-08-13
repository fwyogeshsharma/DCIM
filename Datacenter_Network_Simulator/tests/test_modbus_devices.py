"""Per-device coverage for every simulated Modbus device type.

test_modbus.py pins the protocol (framing, exceptions, gateway routing) using one
or two devices. This file pins the DEVICES: every type in MODBUS_MAPS is stood up
as a live server and every point in its map is read over the wire and checked
against the ext state it renders.

The synthetic ext is derived FROM the map rather than hand-written, so a point
added to a map is covered here the moment it exists — a hand-written fixture
would silently leave it untested, which is how a map grows a point nobody ever
reads back.
"""
from __future__ import annotations

import socket
import struct

import pytest

import core.device_state_store as dss
from core.modbus_register_map import (
    MODBUS_DEVICE_TYPES, MODBUS_MAPS, REG_SPACES,
    SPACE_DISCRETE, SPACE_HOLDING, SPACE_INPUT,
    decode_registers, get_map,
)
from simulator.modbus_controller import ModbusController

HOST = "127.0.0.1"


# ─────────────────────────────────────────────────────────────────────────────
#  Synthetic ext, derived from each map
# ─────────────────────────────────────────────────────────────────────────────
def _safe_value(point) -> float:
    """A value that survives this point's dtype and scale without saturating.

    Saturation would make the round-trip assertion pass against a clamped value
    rather than the real one, hiding a scaling error.
    """
    scale = point.scale or 1
    if point.dtype == "f32":
        return round(120.0 + (point.addr % 40) * 1.5, 2)
    if point.dtype in ("s16", "s32"):
        limit = 32000 if point.dtype == "s16" else 2_000_000
    else:
        limit = 65000 if point.dtype == "u16" else 4_000_000
    raw = min(limit, 1000 + (point.addr % 50) * 37)
    val = raw / scale
    # Round to the precision the scale can actually carry, or the round-trip
    # loses digits the encoder was never able to represent.
    decimals = max(0, len(str(int(scale))) - 1)
    return round(val, decimals)


def _synth_ext(device_type: str) -> dict:
    """Build an ext dict covering every key this map reads."""
    mm = get_map(device_type)
    ext: dict = {}
    for space, points in mm.points.items():
        for p in points:
            if p.presence_of:
                ext.setdefault(p.presence_of, 0.0)
                continue
            if not p.key:
                continue
            if p.enum:
                ext[p.key] = sorted(p.enum)[0]
            elif space == SPACE_DISCRETE and isinstance(p.truthy, str):
                ext[p.key] = p.truthy            # assert the bit goes TRUE
            elif space == SPACE_DISCRETE and p.truthy is None:
                ext[p.key] = 1.0
            elif space in REG_SPACES:
                ext[p.key] = _safe_value(p)
    return ext


# ─────────────────────────────────────────────────────────────────────────────
#  Live server per device type
# ─────────────────────────────────────────────────────────────────────────────
def _free_port() -> int:
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Client:
    def __init__(self, port):
        self.port = port
        self.txn = 0

    def _req(self, pdu, unit=1):
        self.txn = (self.txn + 1) & 0xFFFF
        frame = struct.pack(">HHHB", self.txn, 0, len(pdu) + 1, unit) + pdu
        s = socket.create_connection((HOST, self.port), timeout=5)
        try:
            s.sendall(frame)
            head = s.recv(7)
            ln = struct.unpack(">H", head[4:6])[0]
            body = b""
            while len(body) < ln - 1:
                chunk = s.recv(ln - 1 - len(body))
                if not chunk:
                    break
                body += chunk
            return body
        finally:
            s.close()

    def read_regs(self, space, addr, count, unit=1):
        fc = 3 if space == SPACE_HOLDING else 4
        b = self._req(struct.pack(">BHH", fc, addr, count), unit)
        if b[0] & 0x80:
            raise AssertionError(f"exception 0x{b[1]:02X} reading {space}[{addr}]")
        return list(struct.unpack(">" + "H" * count, b[2:]))

    def read_bit(self, addr, unit=1):
        b = self._req(struct.pack(">BHH", 2, addr, 1), unit)
        if b[0] & 0x80:
            raise AssertionError(f"exception 0x{b[1]:02X} reading discrete[{addr}]")
        return b[2] & 1


@pytest.fixture(params=sorted(MODBUS_DEVICE_TYPES))
def served(request):
    """One live Modbus server per device type, with ext covering its whole map."""
    dtype = request.param
    name = f"TEST-{dtype.upper()}"
    dss._ext_state_cache[name] = _synth_ext(dtype)
    ctrl = ModbusController()
    ctrl.set_log_callback(lambda m, l="info": None)
    port = _free_port()
    assert ctrl.start([{"ip": HOST, "name": name, "device_type": dtype,
                        "unit_id": 1}], port=port), f"{dtype} failed to start"
    ctrl.tick(1.0)
    yield dtype, name, _Client(port), ctrl
    ctrl.stop()
    dss._ext_state_cache.pop(name, None)


# ─────────────────────────────────────────────────────────────────────────────
#  Every device type
# ─────────────────────────────────────────────────────────────────────────────
def test_device_serves_its_map(served):
    dtype, _name, client, _ctrl = served
    mm = get_map(dtype)
    for space in (SPACE_INPUT, SPACE_HOLDING):
        for p in mm.points.get(space, []):
            regs = client.read_regs(space, p.addr, p.width)
            assert len(regs) == p.width, f"{dtype}/{p.name}"


def test_every_measurement_round_trips(served):
    """Decoded value must equal the ext value it renders — this is what catches a
    wrong scale or a wrong word order on a specific point."""
    dtype, name, client, _ctrl = served
    mm = get_map(dtype)
    ext = dss._ext_state_cache[name]
    for p in mm.points.get(SPACE_INPUT, []):
        if p.enum or not p.key:
            continue
        regs = client.read_regs(SPACE_INPUT, p.addr, p.width)
        got = decode_registers(p, regs, mm.word_order)
        want = float(ext[p.key])
        tol = max(abs(want) * 1e-4, 1.0 / (p.scale or 1))
        assert abs(got - want) <= tol, (
            f"{dtype}/{p.name} @ {p.addr:#06x}: got {got}, ext has {want}")


def test_enum_points_decode_to_the_published_state(served):
    dtype, name, client, _ctrl = served
    mm = get_map(dtype)
    ext = dss._ext_state_cache[name]
    for p in mm.points.get(SPACE_HOLDING, []):
        if not p.enum:
            continue
        raw = client.read_regs(SPACE_HOLDING, p.addr, 1)[0]
        assert raw == p.enum[ext[p.key]], f"{dtype}/{p.name}"


def test_status_bits_follow_their_ext_string(served):
    """A discrete keyed on a status string reads 1 only when that exact string is
    published, and 0 otherwise.

    Several bits legitimately share one key — On_Battery and Low_Battery both read
    ups_status — so at most one of a shared group can be set at a time. Asserting
    both sides is what proves the bit tracks the VALUE rather than merely the
    presence of the key.
    """
    dtype, name, client, _ctrl = served
    mm = get_map(dtype)
    ext = dss._ext_state_cache[name]
    checked = 0
    for p in mm.points.get(SPACE_DISCRETE, []):
        if p.presence_of or not isinstance(p.truthy, str):
            continue
        expect = 1 if str(ext.get(p.key)) == p.truthy else 0
        assert client.read_bit(p.addr) == expect, (
            f"{dtype}/{p.name}: ext[{p.key}]={ext.get(p.key)!r}, "
            f"truthy={p.truthy!r}, expected {expect}")
        checked += 1
    if checked:
        assert any(client.read_bit(p.addr) == 1
                   for p in mm.points[SPACE_DISCRETE]
                   if isinstance(p.truthy, str)), (
            f"{dtype}: no status bit ever asserts — the mapping cannot be exercised")


def test_validity_bit_reflects_publication(served):
    dtype, name, client, ctrl = served
    mm = get_map(dtype)
    dv = next(p for p in mm.points[SPACE_DISCRETE] if p.presence_of)
    assert client.read_bit(dv.addr) == 1, f"{dtype}: valid while published"
    slave = ctrl.get_slave(name)
    slave.apply_ext({})
    assert client.read_bit(dv.addr) == 0, f"{dtype}: must go invalid when unpublished"


def test_reading_past_the_map_is_refused(served):
    """Every device must bound its own map — a read off the end has to be
    exception 02, not a zero that looks like a reading."""
    dtype, _name, client, _ctrl = served
    mm = get_map(dtype)
    last = max(p.addr + p.width for p in mm.points[SPACE_INPUT])
    with pytest.raises(AssertionError, match="exception 0x02"):
        client.read_regs(SPACE_INPUT, last + 8, 1)


def test_values_track_the_store(served):
    """A device must re-render on tick, not serve the image it started with."""
    dtype, name, client, ctrl = served
    mm = get_map(dtype)
    p = next((x for x in mm.points[SPACE_INPUT]
              if x.key and not x.enum and x.dtype != "f32"), None)
    if p is None:
        pytest.skip(f"{dtype} has no scaled integer point")
    ext = dss._ext_state_cache[name]
    ext[p.key] = round(float(ext[p.key]) / 2.0, 2)
    ctrl.tick(1.0)
    got = decode_registers(p, client.read_regs(SPACE_INPUT, p.addr, p.width),
                           mm.word_order)
    assert abs(got - float(ext[p.key])) <= max(abs(got) * 1e-3, 1.0 / (p.scale or 1))


# ─────────────────────────────────────────────────────────────────────────────
#  Coverage of the set itself
# ─────────────────────────────────────────────────────────────────────────────
def test_the_served_set_is_exactly_the_documented_one():
    """CRAH/CDU/PDU are deliberately excluded (already on SNMP+BACnet); RPP has no
    comms and its branches are metered by the EV2s; chiller/pump/tower/valve carry
    one comm card, not two. If this set changes, that reasoning changed with it."""
    assert MODBUS_DEVICE_TYPES == {
        "utility_feed", "switchgear", "mcc", "mpp", "generator", "ats", "ups"}


def test_every_type_has_a_distinct_identity():
    ids = {dt: get_map(dt).map_id for dt in MODBUS_DEVICE_TYPES}
    assert len(set(ids.values())) == len(ids), f"duplicate map_id: {ids}"


def test_both_word_orders_are_represented():
    """The heterogeneity is the point — if every map agreed, the plane would stop
    exercising the decoding bug it exists to expose."""
    orders = {get_map(dt).word_order for dt in MODBUS_DEVICE_TYPES}
    assert orders == {"big", "swap"}


def test_every_map_reads_keys_the_store_actually_writes():
    """A point keyed on a typo renders as its default forever and looks alive."""
    import inspect
    import re
    src = inspect.getsource(dss)
    written = set(re.findall(r'st\[\"(\w+)\"\]', src))
    written |= set(re.findall(r'\.get\(\"(\w+)\"', src))
    missing = []
    for dt in MODBUS_DEVICE_TYPES:
        for points in get_map(dt).points.values():
            for p in points:
                key = p.key or p.presence_of
                if key and key not in written:
                    missing.append(f"{dt}/{p.name} -> ext['{key}']")
    assert not missing, "map keys never written by the store: " + ", ".join(missing)


def test_ups_operating_mode_tracks_the_real_state():
    """Regression: Operating_Mode was keyed on ext['ups_operating_mode'], which the
    store never wrote — it was derived inside _publish_facts instead. The register
    therefore served enum default 'online' forever, including on a UPS running on
    battery or bypass. A point reporting a plausible constant is worse than one
    reporting nothing, because it is trended as real.
    """
    from simulator.modbus_device import ModbusSlave
    mm = get_map("ups")
    point = next(p for p in mm.points[SPACE_HOLDING] if p.name == "Operating_Mode")
    slave = ModbusSlave("UPS-X", "ups", mm, unit_id=1)

    def mode_for(ext):
        slave.apply_ext(ext)
        row = next(r for r in slave.snapshot() if r["name"] == "Operating_Mode")
        return row["value"]

    assert mode_for({"ups_operating_mode": "online"}) == "online"
    assert mode_for({"ups_operating_mode": "battery"}) == "battery"
    assert mode_for({"ups_operating_mode": "bypass"}) == "bypass"
    # Every mode the map declares must be reachable, or the enum is decorative.
    for name in point.enum:
        assert mode_for({"ups_operating_mode": name}) == name


def test_the_store_publishes_operating_mode_it_does_not_only_derive_it():
    """The derivation must live where every plane can read it. If it moves back
    inside _publish_facts, the Modbus register goes constant again with nothing
    to show for it."""
    import inspect
    src = inspect.getsource(dss)
    assert 'st["ups_operating_mode"]' in src, (
        "the store no longer publishes ups_operating_mode into ext")

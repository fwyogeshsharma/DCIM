"""Modbus/TCP plane — encoding, framing, exception semantics, gateway routing.

The encoding tests matter more than they look: per-vendor word order and scaling
are the single most common cause of a working-but-wrong Modbus integration, and
they are deliberately heterogeneous across the maps in this simulator.
"""
from __future__ import annotations

import socket
import struct
import time

import pytest

import core.device_state_store as dss
from core.modbus_register_map import (
    MODBUS_MAPS, WORD_BIG, WORD_SWAP,
    decode_registers, encode_point, get_map,
)
from simulator.modbus_controller import ModbusController

HOST = "127.0.0.1"


def _point(device_type: str, space: str, name: str):
    mm = get_map(device_type)
    return mm, next(p for p in mm.points[space] if p.name == name)


# ─────────────────────────────────────────────────────────────────────────────
#  Encoding
# ─────────────────────────────────────────────────────────────────────────────
def test_f32_round_trip():
    mm, p = _point("utility_feed", "input", "Active_Power")
    assert round(decode_registers(p, encode_point(p, 812.5, mm.word_order),
                                  mm.word_order), 2) == 812.5


def test_word_order_actually_differs():
    """If these ever encode identically the heterogeneity is gone and the maps
    stop exercising the bug they exist to exercise."""
    _, p = _point("switchgear", "input", "Energy_Delivered")
    assert encode_point(p, 123456.7, WORD_SWAP) != encode_point(p, 123456.7, WORD_BIG)


def test_u32_swapped_round_trip():
    _, p = _point("switchgear", "input", "Energy_Delivered")
    regs = encode_point(p, 123456.7, WORD_SWAP)
    assert round(decode_registers(p, regs, WORD_SWAP), 1) == 123456.7


def test_wrong_word_order_is_wrong_by_65536ish():
    """Decoding a swapped map as big-endian must produce garbage, not a near
    miss — that is what makes the bug findable in a real integration."""
    _, p = _point("switchgear", "input", "Energy_Delivered")
    regs = encode_point(p, 1000.0, WORD_SWAP)
    assert abs(decode_registers(p, regs, WORD_BIG) - 1000.0) > 1000.0


def test_signed_16bit_negative_power_factor():
    _, p = _point("switchgear", "input", "Power_Factor")
    assert round(decode_registers(p, encode_point(p, -0.87, WORD_SWAP),
                                  WORD_SWAP), 2) == -0.87


def test_out_of_range_saturates_not_wraps():
    """A wrapped value looks like a live reading and gets trended as one; a
    pegged value reads as an obvious out-of-range."""
    _, p = _point("switchgear", "input", "Power_Factor")
    assert encode_point(p, 9999.0, WORD_SWAP)[0] == 32767


def test_every_map_declares_identity():
    for dtype, mm in MODBUS_MAPS.items():
        assert mm.map_id.startswith("SIM-"), (
            f"{dtype}: map_id must mark these as simulator addresses, "
            f"not the vendor's published map")
        assert mm.vendor and mm.product


def test_no_map_has_overlapping_addresses():
    from core.modbus_register_map import covered_addresses, REG_SPACES
    for dtype, mm in MODBUS_MAPS.items():
        for space in mm.points:
            seen = set()
            for p in mm.points[space]:
                width = p.width if space in REG_SPACES else 1
                for off in range(width):
                    a = p.addr + off
                    assert a not in seen, f"{dtype}/{space}: address {a} claimed twice"
                    seen.add(a)
            assert seen == set(covered_addresses(mm, space))


# ─────────────────────────────────────────────────────────────────────────────
#  Live server
# ─────────────────────────────────────────────────────────────────────────────
_SWGR_EXT = {
    "swgr_voltage": 415.2, "swgr_current": 1180.0, "swgr_frequency": 50.0,
    "swgr_power_factor": -0.87, "swgr_load_pct": 62.5, "swgr_kw": 812,
    "swgr_energy_kwh": 123456.7, "swgr_bus_status": "energized",
    "swgr_breaker_status": "closed", "swgr_source": "utility",
    "swgr_va": 239.6, "swgr_vb": 240.1, "swgr_vc": 239.9,
    "swgr_ia": 1180.0, "swgr_ib": 1175.0, "swgr_ic": 1190.0,
    "swgr_phase_imbalance": 0.6, "swgr_kvar": 210, "swgr_kva": 840,
}


def _free_port() -> int:
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Client:
    def __init__(self, port: int):
        self.port = port
        self.txn = 0

    def req(self, pdu: bytes, unit: int = 1) -> bytes:
        self.txn += 1
        frame = struct.pack(">HHHB", self.txn, 0, len(pdu) + 1, unit) + pdu
        s = socket.create_connection((HOST, self.port), timeout=5)
        try:
            s.sendall(frame)
            head = s.recv(7)
            ln = struct.unpack(">H", head[4:6])[0]
            return s.recv(ln - 1)
        finally:
            s.close()

    def read_input(self, addr: int, count: int, unit: int = 1) -> bytes:
        return self.req(struct.pack(">BHH", 4, addr, count), unit)


@pytest.fixture
def server():
    dss._ext_state_cache["SWGR-TEST"] = dict(_SWGR_EXT)
    ctrl = ModbusController()
    port = _free_port()
    assert ctrl.start([{"ip": HOST, "name": "SWGR-TEST",
                        "device_type": "switchgear", "unit_id": 1}], port=port)
    ctrl.tick(1.0)
    yield ctrl, _Client(port)
    ctrl.stop()
    dss._ext_state_cache.pop("SWGR-TEST", None)


def test_is_ready_requires_a_bound_socket(server):
    ctrl, _ = server
    assert ctrl.is_ready()
    ctrl.stop()
    assert not ctrl.is_ready()


def test_read_input_registers_match_ext(server):
    _, c = server
    body = c.read_input(0x0000, 5)
    v = struct.unpack(">" + "H" * 5, body[2:])
    assert v[0] == 4152                                        # 415.2 V x10
    assert v[1] == 1180                                        # A
    assert v[2] == 500                                         # 50.0 Hz x10
    assert struct.unpack(">h", struct.pack(">H", v[3]))[0] == -87   # PF x100
    assert v[4] == 625                                         # 62.5 % x10


def test_32bit_accumulator_uses_the_maps_word_order(server):
    _, c = server
    w = struct.unpack(">HH", c.read_input(0x0030, 2)[2:])
    assert ((w[1] << 16) | w[0]) / 10.0 == 123456.7            # swapped


def test_discrete_inputs_pack_lsb_first(server):
    _, c = server
    body = c.req(struct.pack(">BHH", 2, 0, 3))
    assert body[2] & 0b111 == 0b011      # energized + closed, source not generator


def test_gap_in_sparse_map_is_exception_02_not_zero(server):
    _, c = server
    body = c.read_input(0x0007, 2)
    assert (body[0], body[1]) == (0x84, 0x02)


def test_over_125_registers_is_exception_03(server):
    _, c = server
    body = c.read_input(0x0000, 200)
    assert (body[0], body[1]) == (0x84, 0x03)


def test_unknown_function_is_exception_01(server):
    _, c = server
    body = c.req(struct.pack(">BHH", 0x63, 0, 1))
    assert (body[0], body[1]) == (0xE3, 0x01)


def test_write_to_a_measurement_is_refused(server):
    _, c = server
    body = c.req(struct.pack(">BHH", 6, 0x0000, 1234))
    assert body[0] & 0x80


def test_read_device_id_carries_the_sim_map_id(server):
    _, c = server
    assert b"SIM-EATON-PXG-MAGNUM-v1" in c.req(bytes([0x2B, 0x0E, 0x01, 0x00]))


def test_split_frame_reassembles(server):
    _, c = server
    pdu = struct.pack(">BHH", 4, 0x0000, 1)
    frame = struct.pack(">HHHB", 99, 0, len(pdu) + 1, 1) + pdu
    s = socket.create_connection((HOST, c.port), timeout=5)
    try:
        s.sendall(frame[:4])
        time.sleep(0.05)
        s.sendall(frame[4:])
        head = s.recv(7)
        body = s.recv(struct.unpack(">H", head[4:6])[0] - 1)
    finally:
        s.close()
    assert struct.unpack(">H", body[2:4])[0] == 4152


def test_pipelined_frames_get_one_reply_each(server):
    _, c = server
    pdu = struct.pack(">BHH", 4, 0x0000, 1)
    f1 = struct.pack(">HHHB", 101, 0, len(pdu) + 1, 1) + pdu
    f2 = struct.pack(">HHHB", 102, 0, len(pdu) + 1, 1) + pdu
    s = socket.create_connection((HOST, c.port), timeout=5)
    try:
        s.sendall(f1 + f2)
        got = b""
        while len(got) < 2 * (7 + 4):
            chunk = s.recv(64)
            if not chunk:
                break
            got += chunk
    finally:
        s.close()
    assert len(got) == 2 * (7 + 4)


def test_values_track_the_store_between_ticks(server):
    ctrl, c = server
    dss._ext_state_cache["SWGR-TEST"]["swgr_voltage"] = 402.0
    ctrl.tick(1.0)
    assert struct.unpack(">H", c.read_input(0x0000, 1)[2:4])[0] == 4020


# ─────────────────────────────────────────────────────────────────────────────
#  Gateway / RTU trunk
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def trunk():
    for name, v in (("MPP-A", 400.0), ("MPP-B", 398.0)):
        dss._ext_state_cache[name] = {
            "mpp_voltage": v, "mpp_status": "energized", "mpp_current": 88.0,
            "mpp_frequency": 50.0, "mpp_power_factor": 0.97, "mpp_load_pct": 55.0}
    ctrl = ModbusController()
    port = _free_port()
    assert ctrl.start([
        {"ip": HOST, "name": "MBGW-TEST", "device_type": "", "role": "gateway"},
        {"ip": "", "name": "MPP-A", "device_type": "mpp", "unit_id": 3,
         "role": "rtu_slave", "gateway_ip": HOST},
        {"ip": "", "name": "MPP-B", "device_type": "mpp", "unit_id": 4,
         "role": "rtu_slave", "gateway_ip": HOST},
    ], port=port)
    ctrl.tick(1.0)
    yield ctrl, _Client(port)
    ctrl.stop()
    dss._ext_state_cache.pop("MPP-A", None)
    dss._ext_state_cache.pop("MPP-B", None)


def test_unit_id_selects_the_slave_behind_the_gateway(trunk):
    _, c = trunk
    assert struct.unpack(">H", c.read_input(0x0000, 1, unit=3)[2:4])[0] == 4000
    assert struct.unpack(">H", c.read_input(0x0000, 1, unit=4)[2:4])[0] == 3980


def test_unknown_unit_is_gateway_path_unavailable(trunk):
    _, c = trunk
    body = c.read_input(0x0000, 1, unit=77)
    assert (body[0], body[1]) == (0x84, 0x0A)


def test_dead_rtu_slave_answers_0x0b_over_a_live_socket(trunk):
    """The distinguishing Modbus failure: the network path is fine, the field
    device is not. No other plane in this simulator can express that."""
    ctrl, c = trunk
    ctrl.tick(1.0, unpowered_names={"MPP-B"})
    body = c.read_input(0x0000, 1, unit=4)
    assert (body[0], body[1]) == (0x84, 0x0B)
    # ...and its neighbour on the same trunk is unaffected.
    assert struct.unpack(">H", c.read_input(0x0000, 1, unit=3)[2:4])[0] == 4000


def test_rs485_trunk_serialises_transactions(trunk):
    """An 18-slave trunk cannot be polled in under a couple of seconds, and a
    simulator that answers instantly teaches the opposite lesson."""
    _, c = trunk
    t0 = time.monotonic()
    for _ in range(6):
        c.read_input(0x0000, 1, unit=3)
    assert time.monotonic() - t0 >= 0.25


def test_an_ip_is_a_device_or_a_gateway_never_both():
    ctrl = ModbusController()
    port = _free_port()
    ctrl.start([{"ip": HOST, "name": "X", "device_type": "mpp", "unit_id": 1},
                {"ip": HOST, "name": "GW", "device_type": "", "role": "gateway"}],
               port=port)
    try:
        assert len(ctrl._gateways) == 0
    finally:
        ctrl.stop()

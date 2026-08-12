"""One simulated Modbus slave — register banks plus PDU request handling.

A slave holds no telemetry of its own. Each tick the controller calls
`apply_ext()` with the device's live `ext` dict from DeviceStateStore, and the
slave re-renders its banks through the ModbusMap. Between ticks the banks are
static, which is exactly how a real device behaves: the register image is only
as fresh as the last acquisition scan.

Framing is not this class's problem. `handle_pdu()` takes a bare PDU (function
code + data, no MBAP header, no RTU CRC) and returns a bare PDU, so the same
slave serves a TCP connection and an RTU trunk behind a gateway unchanged.
"""
from __future__ import annotations

import logging
import struct
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.modbus_register_map import (
    ModbusMap, ModbusPoint,
    SPACE_COIL, SPACE_DISCRETE, SPACE_HOLDING, SPACE_INPUT,
    BIT_SPACES, REG_SPACES,
    covered_addresses, decode_registers, encode_bit, encode_point,
)

log = logging.getLogger(__name__)

# ── Function codes ───────────────────────────────────────────────────────────
FC_READ_COILS            = 0x01
FC_READ_DISCRETE_INPUTS  = 0x02
FC_READ_HOLDING          = 0x03
FC_READ_INPUT            = 0x04
FC_WRITE_SINGLE_COIL     = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_COILS  = 0x0F
FC_WRITE_MULTIPLE_REGS   = 0x10
FC_READ_DEVICE_ID        = 0x2B      # MEI, sub-function 0x0E

# ── Exception codes ──────────────────────────────────────────────────────────
EX_ILLEGAL_FUNCTION   = 0x01
EX_ILLEGAL_ADDRESS    = 0x02
EX_ILLEGAL_VALUE      = 0x03
EX_SLAVE_FAILURE      = 0x04
EX_GATEWAY_PATH       = 0x0A   # gateway could not route to the unit id
EX_GATEWAY_NO_RESPONSE= 0x0B   # unit exists on the trunk but did not answer

# Protocol limits. Exceeding them is exception 03, not a truncated answer — a
# master that asks for 200 registers has a bug, and quietly returning 125 hides
# it until the missing 75 matter.
MAX_READ_REGISTERS = 125
MAX_READ_BITS      = 2000
MAX_WRITE_REGISTERS = 123
MAX_WRITE_BITS      = 1968


class ModbusSlave:
    """A single addressable unit: one native-TCP device, or one RTU slave."""

    def __init__(self, name: str, device_type: str, mmap: ModbusMap,
                 unit_id: int = 1, ip: str = "",
                 write_cb: Optional[Callable[[str, str, float], bool]] = None):
        self.name        = name
        self.device_type = device_type
        self.map         = mmap
        self.unit_id     = unit_id
        self.ip          = ip
        # write_cb(device_name, write_action, engineering_value) -> accepted?
        self._write_cb   = write_cb

        # Powered/reachable state, driven by the controller from the store's
        # unpowered set. A dead device does not answer — see the controller for
        # how that presents differently on TCP vs behind a gateway.
        self.online = True
        # Local/Remote interlock. Real gear refuses remote commands when the
        # front panel is in Local, and most sites run the Modbus plane read-only
        # regardless — so this starts closed and the operator must arm it.
        self.write_enabled = mmap.write_enabled

        self._lock = threading.RLock()

        # Rendered banks
        self._regs: Dict[str, Dict[int, int]] = {SPACE_INPUT: {}, SPACE_HOLDING: {}}
        self._bits: Dict[str, Dict[int, int]] = {SPACE_COIL: {}, SPACE_DISCRETE: {}}

        # {space: {addr: (point, word_offset)}} — the authority on what exists.
        self._cover: Dict[str, Dict[int, Tuple[ModbusPoint, int]]] = {
            sp: covered_addresses(mmap, sp)
            for sp in (SPACE_INPUT, SPACE_HOLDING, SPACE_COIL, SPACE_DISCRETE)
        }

        # Counters a real gateway/slave keeps and an integrator actually uses.
        self.stats = {"requests": 0, "exceptions": 0, "writes": 0,
                      "last_exception": 0}

        self.apply_ext({})

    # ─────────────────────────────────────────────────────────────────────
    #  Telemetry refresh
    # ─────────────────────────────────────────────────────────────────────
    def apply_ext(self, ext: Dict[str, Any]) -> None:
        """Re-render every bank from the device's live ext dict."""
        wo = self.map.word_order
        regs: Dict[str, Dict[int, int]] = {SPACE_INPUT: {}, SPACE_HOLDING: {}}
        bits: Dict[str, Dict[int, int]] = {SPACE_COIL: {}, SPACE_DISCRETE: {}}

        for space, points in self.map.points.items():
            for p in points:
                raw_val = ext.get(p.key, p.default) if p.key else p.default
                if space in REG_SPACES:
                    words = encode_point(p, raw_val, wo)
                    for off, w in enumerate(words):
                        regs[space][p.addr + off] = w & 0xFFFF
                else:
                    # A coil with no key is a command point: it has no telemetry
                    # to render, so it keeps whatever was last written to it.
                    if space == SPACE_COIL and not p.key:
                        bits[space][p.addr] = self._bits.get(space, {}).get(p.addr, 0)
                    else:
                        bits[space][p.addr] = encode_bit(p, raw_val)

        with self._lock:
            self._regs, self._bits = regs, bits

    def set_online(self, online: bool) -> None:
        with self._lock:
            self.online = bool(online)

    def accept_unit(self, unit: int) -> bool:
        """Does this slave answer for the unit id in the MBAP header?

        Real native-TCP gear is split on this. Most cards ignore the unit id
        entirely (there is nothing behind them to disambiguate), some insist on
        their configured value, and 0xFF is the spec's "not used" placeholder.
        `accept_any_unit` carries the per-vendor choice — the CAT map sets it
        False, so an integration that assumes unit 1 everywhere gets caught.
        """
        if self.map.accept_any_unit:
            return True
        return int(unit) in (self.unit_id, 0xFF, 0x00)

    # ─────────────────────────────────────────────────────────────────────
    #  Request handling
    # ─────────────────────────────────────────────────────────────────────
    def handle_pdu(self, pdu: bytes) -> bytes:
        """Bare PDU in, bare PDU out. Never raises — a malformed request is an
        exception response, because that is what a master must be able to parse."""
        if not pdu:
            return b""
        with self._lock:
            self.stats["requests"] += 1
        func = pdu[0]

        try:
            if func in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
                return self._read_bits(func, pdu)
            if func in (FC_READ_HOLDING, FC_READ_INPUT):
                return self._read_registers(func, pdu)
            if func == FC_WRITE_SINGLE_COIL:
                return self._write_single_coil(pdu)
            if func == FC_WRITE_SINGLE_REGISTER:
                return self._write_single_register(pdu)
            if func == FC_WRITE_MULTIPLE_COILS:
                return self._write_multiple_coils(pdu)
            if func == FC_WRITE_MULTIPLE_REGS:
                return self._write_multiple_registers(pdu)
            if func == FC_READ_DEVICE_ID:
                return self._read_device_id(pdu)
            return self._exception(func, EX_ILLEGAL_FUNCTION)
        except struct.error:
            # Short/garbled payload. 03 (illegal data value) is the closest the
            # spec offers for "your request did not parse".
            return self._exception(func, EX_ILLEGAL_VALUE)
        except Exception:
            log.exception("[Modbus] %s: handler error on FC%02X", self.name, func)
            return self._exception(func, EX_SLAVE_FAILURE)

    def _exception(self, func: int, code: int) -> bytes:
        with self._lock:
            self.stats["exceptions"] += 1
            self.stats["last_exception"] = code
        return bytes([func | 0x80, code])

    # ── Reads ────────────────────────────────────────────────────────────
    def _read_registers(self, func: int, pdu: bytes) -> bytes:
        addr, count = struct.unpack(">HH", pdu[1:5])
        if not (1 <= count <= MAX_READ_REGISTERS):
            return self._exception(func, EX_ILLEGAL_VALUE)

        space = SPACE_HOLDING if func == FC_READ_HOLDING else SPACE_INPUT
        cover = self._cover[space]
        with self._lock:
            bank = dict(self._regs[space])

        # Every address in the span must exist. A sparse map means a blind scan
        # hits a gap, and the master needs to learn that from an exception.
        out: List[int] = []
        for a in range(addr, addr + count):
            if a not in cover:
                return self._exception(func, EX_ILLEGAL_ADDRESS)
            out.append(bank.get(a, 0))

        body = b"".join(struct.pack(">H", w) for w in out)
        return bytes([func, len(body)]) + body

    def _read_bits(self, func: int, pdu: bytes) -> bytes:
        addr, count = struct.unpack(">HH", pdu[1:5])
        if not (1 <= count <= MAX_READ_BITS):
            return self._exception(func, EX_ILLEGAL_VALUE)

        space = SPACE_COIL if func == FC_READ_COILS else SPACE_DISCRETE
        cover = self._cover[space]
        with self._lock:
            bank = dict(self._bits[space])

        vals: List[int] = []
        for a in range(addr, addr + count):
            if a not in cover:
                return self._exception(func, EX_ILLEGAL_ADDRESS)
            vals.append(1 if bank.get(a, 0) else 0)

        # Packed LSB-first, low address in bit 0 of the first byte.
        nbytes = (count + 7) // 8
        packed = bytearray(nbytes)
        for i, v in enumerate(vals):
            if v:
                packed[i // 8] |= 1 << (i % 8)
        return bytes([func, nbytes]) + bytes(packed)

    # ── Writes ───────────────────────────────────────────────────────────
    def _write_guard(self, func: int, space: str, addr: int) -> Optional[bytes]:
        """Shared refusal path. Returns an exception PDU, or None to proceed."""
        cover = self._cover[space]
        if addr not in cover:
            return self._exception(func, EX_ILLEGAL_ADDRESS)
        point, off = cover[addr]
        if not point.writable:
            # The address exists but is a measurement. 02 is the conventional
            # answer for "not writable here" — a read-only register is, from the
            # master's side, simply not a valid write address.
            return self._exception(func, EX_ILLEGAL_ADDRESS)
        if off != 0:
            return self._exception(func, EX_ILLEGAL_ADDRESS)
        if not self.write_enabled:
            # Local mode / read-only plane. Real gear answers 04 here: the
            # request was well-formed and the device declined it.
            return self._exception(func, EX_SLAVE_FAILURE)
        return None

    def _commit_write(self, point: ModbusPoint, value: float) -> bool:
        """Hand the write to the override channel. Never touch `ext`.

        The store rewrites `ext` every tick, so a value poked in here would be
        gone within a second and the master would see its own write evaporate —
        which is worse than refusing it. The write_cb routes into the same
        override map the API and the Simulate-Fault menu use.
        """
        if not self._write_cb or not point.write_action:
            return False
        try:
            ok = bool(self._write_cb(self.name, point.write_action, value))
        except Exception:
            log.exception("[Modbus] %s: write callback failed", self.name)
            return False
        if ok:
            with self._lock:
                self.stats["writes"] += 1
        return ok

    def _write_single_coil(self, pdu: bytes) -> bytes:
        addr, raw = struct.unpack(">HH", pdu[1:5])
        # The spec allows exactly these two values, and nothing else.
        if raw not in (0x0000, 0xFF00):
            return self._exception(FC_WRITE_SINGLE_COIL, EX_ILLEGAL_VALUE)
        bad = self._write_guard(FC_WRITE_SINGLE_COIL, SPACE_COIL, addr)
        if bad:
            return bad

        point, _ = self._cover[SPACE_COIL][addr]
        val = 1 if raw == 0xFF00 else 0
        if not self._commit_write(point, float(val)):
            return self._exception(FC_WRITE_SINGLE_COIL, EX_SLAVE_FAILURE)
        with self._lock:
            self._bits[SPACE_COIL][addr] = val
        return pdu[:5]                     # echo request

    def _write_single_register(self, pdu: bytes) -> bytes:
        addr, raw = struct.unpack(">HH", pdu[1:5])
        bad = self._write_guard(FC_WRITE_SINGLE_REGISTER, SPACE_HOLDING, addr)
        if bad:
            return bad

        point, _ = self._cover[SPACE_HOLDING][addr]
        if point.width != 1:
            # A 32-bit point cannot be written one word at a time: the device
            # would act on half a value. Real gear requires FC16 for these.
            return self._exception(FC_WRITE_SINGLE_REGISTER, EX_ILLEGAL_ADDRESS)
        val = decode_registers(point, [raw], self.map.word_order)
        if not self._commit_write(point, val):
            return self._exception(FC_WRITE_SINGLE_REGISTER, EX_SLAVE_FAILURE)
        with self._lock:
            self._regs[SPACE_HOLDING][addr] = raw & 0xFFFF
        return pdu[:5]

    def _write_multiple_coils(self, pdu: bytes) -> bytes:
        addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
        if not (1 <= count <= MAX_WRITE_BITS) or nbytes != (count + 7) // 8:
            return self._exception(FC_WRITE_MULTIPLE_COILS, EX_ILLEGAL_VALUE)
        data = pdu[6:6 + nbytes]

        # Validate the whole span before committing any of it — a partially
        # applied multi-write leaves the device in a state the master never asked
        # for and cannot infer from the exception.
        for i in range(count):
            bad = self._write_guard(FC_WRITE_MULTIPLE_COILS, SPACE_COIL, addr + i)
            if bad:
                return bad
        for i in range(count):
            point, _ = self._cover[SPACE_COIL][addr + i]
            val = (data[i // 8] >> (i % 8)) & 1
            if not self._commit_write(point, float(val)):
                return self._exception(FC_WRITE_MULTIPLE_COILS, EX_SLAVE_FAILURE)
            with self._lock:
                self._bits[SPACE_COIL][addr + i] = val
        return struct.pack(">BHH", FC_WRITE_MULTIPLE_COILS, addr, count)

    def _write_multiple_registers(self, pdu: bytes) -> bytes:
        addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
        if not (1 <= count <= MAX_WRITE_REGISTERS) or nbytes != count * 2:
            return self._exception(FC_WRITE_MULTIPLE_REGS, EX_ILLEGAL_VALUE)
        words = list(struct.unpack(">" + "H" * count, pdu[6:6 + nbytes]))

        cover = self._cover[SPACE_HOLDING]
        # Walk point-by-point rather than address-by-address, so a 32-bit point
        # is validated and committed as one value.
        i = 0
        planned: List[Tuple[ModbusPoint, int, List[int]]] = []
        while i < count:
            a = addr + i
            bad = self._write_guard(FC_WRITE_MULTIPLE_REGS, SPACE_HOLDING, a)
            if bad:
                return bad
            point, _ = cover[a]
            if i + point.width > count:
                # The span stops halfway through a 32-bit value.
                return self._exception(FC_WRITE_MULTIPLE_REGS, EX_ILLEGAL_VALUE)
            planned.append((point, a, words[i:i + point.width]))
            i += point.width

        for point, a, chunk in planned:
            val = decode_registers(point, chunk, self.map.word_order)
            if not self._commit_write(point, val):
                return self._exception(FC_WRITE_MULTIPLE_REGS, EX_SLAVE_FAILURE)
            with self._lock:
                for off, w in enumerate(chunk):
                    self._regs[SPACE_HOLDING][a + off] = w & 0xFFFF
        return struct.pack(">BHH", FC_WRITE_MULTIPLE_REGS, addr, count)

    # ── FC43/14 Read Device Identification ───────────────────────────────
    def _read_device_id(self, pdu: bytes) -> bytes:
        """The only self-description Modbus has, and it is optional and thin.

        Three strings: vendor, product code, revision. This simulator puts the
        map_id in the revision slot, so a client that bothers to ask is told in
        band that these addresses are the simulator's and not the vendor's.
        """
        if len(pdu) < 4 or pdu[1] != 0x0E:
            return self._exception(FC_READ_DEVICE_ID, EX_ILLEGAL_FUNCTION)
        read_code = pdu[2]
        if read_code not in (0x01, 0x02, 0x03, 0x04):
            return self._exception(FC_READ_DEVICE_ID, EX_ILLEGAL_VALUE)

        objects = [
            (0x00, self.map.vendor.encode("ascii", "replace")),
            (0x01, self.map.product.encode("ascii", "replace")),
            (0x02, self.map.map_id.encode("ascii", "replace")),
        ]
        if read_code == 0x04:                       # single specific object
            want = pdu[3]
            objects = [o for o in objects if o[0] == want]
            if not objects:
                return self._exception(FC_READ_DEVICE_ID, EX_ILLEGAL_ADDRESS)

        body = bytearray([FC_READ_DEVICE_ID, 0x0E, read_code,
                          0x01,               # conformity: basic, stream only
                          0x00,               # more follows: no
                          0x00,               # next object id
                          len(objects)])
        for oid, val in objects:
            body += bytes([oid, len(val)]) + val
        return bytes(body)

    # ─────────────────────────────────────────────────────────────────────
    #  Introspection (API / register browser)
    # ─────────────────────────────────────────────────────────────────────
    def snapshot(self) -> List[dict]:
        """Every point with its raw words and decoded engineering value."""
        wo = self.map.word_order
        rows: List[dict] = []
        with self._lock:
            regs = {k: dict(v) for k, v in self._regs.items()}
            bits = {k: dict(v) for k, v in self._bits.items()}

        for space, points in self.map.points.items():
            for p in points:
                if space in REG_SPACES:
                    words = [regs[space].get(p.addr + o, 0) for o in range(p.width)]
                    scaled: Any
                    if p.enum:
                        inv = {v: k for k, v in p.enum.items()}
                        scaled = inv.get(words[0], words[0])
                    else:
                        scaled = round(decode_registers(p, words, wo), 3)
                    rows.append({"space": space, "addr": p.addr, "name": p.name,
                                 "dtype": p.dtype, "raw": words, "value": scaled,
                                 "units": p.units, "key": p.key,
                                 "writable": p.writable})
                else:
                    rows.append({"space": space, "addr": p.addr, "name": p.name,
                                 "dtype": "bit", "raw": [bits[space].get(p.addr, 0)],
                                 "value": bits[space].get(p.addr, 0),
                                 "units": "", "key": p.key,
                                 "writable": p.writable})
        rows.sort(key=lambda r: (r["space"], r["addr"]))
        return rows

    def summary(self) -> dict:
        with self._lock:
            stats = dict(self.stats)
        return {"name": self.name, "ip": self.ip, "unit_id": self.unit_id,
                "device_type": self.device_type, "map_id": self.map.map_id,
                "vendor": self.map.vendor, "product": self.map.product,
                "word_order": self.map.word_order, "online": self.online,
                "write_enabled": self.write_enabled, "stats": stats}

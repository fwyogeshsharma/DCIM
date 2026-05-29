"""
BACnet/IP Object Model — constants, dataclasses, and codec helpers.

Pure-Python BACnet tag encoding/decoding.  No external library required.
Used by bacnet_ev2_generator, bacnet_device, and bacnet_controller.

BACnet tag wire format (first byte):
  bits 7-4  tag number (0-14; 15 = extended, next byte is real tag)
  bit  3    class: 0 = application, 1 = context
  bits 2-0  length / special:
              0-4  actual octet count
              5    extended length (next byte; 254/255 = 2/4 more bytes)
              6    opening tag  (context only)
              7    closing tag  (context only)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────
#  BACnet Object Type constants
# ─────────────────────────────────────────────────────────────────

OBJ_ANALOG_INPUT   = 0
OBJ_ANALOG_OUTPUT  = 1
OBJ_ANALOG_VALUE   = 2
OBJ_BINARY_INPUT   = 3
OBJ_BINARY_OUTPUT  = 4
OBJ_BINARY_VALUE   = 5
OBJ_DEVICE         = 8

# ─────────────────────────────────────────────────────────────────
#  BACnet Property Identifier constants
# ─────────────────────────────────────────────────────────────────

PROP_ACTIVE_COV_SUBSCRIPTIONS      = 152
PROP_APPLICATION_SOFTWARE_VERSION  = 12
PROP_COV_INCREMENT                 = 22
PROP_DESCRIPTION                   = 28
PROP_FIRMWARE_REVISION             = 44
PROP_MAX_APDU_LENGTH_ACCEPTED      = 62
PROP_MAX_PRES_VALUE                = 65
PROP_MIN_PRES_VALUE                = 69
PROP_MODEL_NAME                    = 70
PROP_OBJECT_IDENTIFIER             = 75
PROP_OBJECT_LIST                   = 76
PROP_OBJECT_NAME                   = 77
PROP_OBJECT_TYPE                   = 79
PROP_OUT_OF_SERVICE                = 81
PROP_PRESENT_VALUE                 = 85
PROP_PROTOCOL_REVISION             = 139
PROP_PROTOCOL_VERSION              = 98
PROP_RELIABILITY                   = 103
PROP_RESOLUTION                    = 106
PROP_SEGMENTATION_SUPPORTED        = 107
PROP_STATUS_FLAGS                  = 111
PROP_SYSTEM_STATUS                 = 112
PROP_UNITS                         = 117
PROP_VENDOR_IDENTIFIER             = 120
PROP_VENDOR_NAME                   = 121
PROP_ALL                           = 8     # pseudo-property: read all

# ─────────────────────────────────────────────────────────────────
#  BACnet Engineering Units
# ─────────────────────────────────────────────────────────────────

UNIT_AMPERES        = 3
UNIT_VOLTS          = 5
UNIT_WATTS          = 47
UNIT_KILOWATTS      = 48
UNIT_WATT_HOURS     = 18
UNIT_KILOWATT_HOURS = 19
UNIT_HERTZ          = 27
UNIT_PERCENT        = 98
UNIT_NO_UNITS       = 95

# ─────────────────────────────────────────────────────────────────
#  BACnet Reliability
# ─────────────────────────────────────────────────────────────────

RELIABILITY_NO_FAULT_DETECTED = 0
RELIABILITY_NO_SENSOR         = 1
RELIABILITY_OVER_RANGE        = 2
RELIABILITY_UNDER_RANGE       = 3
RELIABILITY_COMM_FAILURE      = 12

# ─────────────────────────────────────────────────────────────────
#  BACnet Segmentation
# ─────────────────────────────────────────────────────────────────

SEGMENTATION_BOTH            = 0
SEGMENTATION_TRANSMIT        = 1
SEGMENTATION_RECEIVE         = 2
SEGMENTATION_NO_SEGMENTATION = 3

# ─────────────────────────────────────────────────────────────────
#  BACnet Device Status
# ─────────────────────────────────────────────────────────────────

DEVICE_STATUS_OPERATIONAL             = 0
DEVICE_STATUS_OPERATIONAL_READ_ONLY   = 1
DEVICE_STATUS_DOWNLOAD_REQUIRED       = 2
DEVICE_STATUS_DOWNLOAD_IN_PROGRESS    = 3
DEVICE_STATUS_NON_OPERATIONAL         = 4
DEVICE_STATUS_BACKUP_IN_PROGRESS      = 5

# ─────────────────────────────────────────────────────────────────
#  BACnet PDU / APDU types
# ─────────────────────────────────────────────────────────────────

PDU_CONFIRMED_REQUEST   = 0
PDU_UNCONFIRMED_REQUEST = 1
PDU_SIMPLE_ACK          = 2
PDU_COMPLEX_ACK         = 3
PDU_SEGMENT_ACK         = 4
PDU_ERROR               = 5
PDU_REJECT              = 6
PDU_ABORT               = 7

# ─────────────────────────────────────────────────────────────────
#  BACnet Service choices
# ─────────────────────────────────────────────────────────────────

SVC_I_AM                            = 0   # unconfirmed
SVC_UNCONFIRMED_COV_NOTIFICATION    = 2   # unconfirmed
SVC_WHO_IS                          = 8   # unconfirmed
SVC_SUBSCRIBE_COV                   = 5   # confirmed
SVC_READ_PROPERTY                   = 12  # confirmed
SVC_READ_PROPERTY_MULTIPLE          = 14  # confirmed

# ─────────────────────────────────────────────────────────────────
#  BACnet Error classes / codes
# ─────────────────────────────────────────────────────────────────

ERR_CLASS_OBJECT   = 1
ERR_CLASS_PROPERTY = 2
ERR_CODE_UNKNOWN_OBJECT           = 31
ERR_CODE_UNKNOWN_PROPERTY         = 32
ERR_CODE_UNSUPPORTED_OBJECT_TYPE  = 36
ERR_CODE_VALUE_OUT_OF_RANGE       = 37

# ─────────────────────────────────────────────────────────────────
#  BVLL function codes
# ─────────────────────────────────────────────────────────────────

BVLL_TYPE               = 0x81
BVLC_RESULT             = 0x00
BVLC_ORIGINAL_UNICAST   = 0x0A
BVLC_ORIGINAL_BROADCAST = 0x0B
BVLC_FORWARDED_NPDU     = 0x04

# ─────────────────────────────────────────────────────────────────
#  Verdigris-specific constants
# ─────────────────────────────────────────────────────────────────

VERDIGRIS_VENDOR_ID         = 9999   # non-official; use until registered
VERDIGRIS_VENDOR_NAME       = "Verdigris Technologies"
VERDIGRIS_MODEL_NAME        = "EV2"
VERDIGRIS_FIRMWARE_REVISION = "2.4.1"
VERDIGRIS_APP_SW_VERSION    = "EV2-SIM-1.0"
BACNET_PORT                 = 47808   # 0xBAC0
MAX_APDU_LENGTH             = 1476    # Ethernet-safe (1500 - 28 header bytes)


# ─────────────────────────────────────────────────────────────────
#  BACnet Object dataclass
# ─────────────────────────────────────────────────────────────────

@dataclass
class BACnetObject:
    """
    Single BACnet object with all required and optional properties.

    object_type: OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT, etc.
    instance:    object instance number
    present_value: float for AI, bool for BI
    """
    object_type:   int
    instance:      int
    name:          str
    description:   str
    units:         int       # engineering units enum; UNIT_NO_UNITS for BI
    present_value: float = 0.0   # float for AI; 0.0/1.0 for BI (bool stored as float)
    status_flags:  int   = 0     # 4-bit: in_alarm|fault|overridden|out_of_service
    reliability:   int   = RELIABILITY_NO_FAULT_DETECTED
    out_of_service: bool = False
    min_pres_value: Optional[float] = None
    max_pres_value: Optional[float] = None
    resolution:    Optional[float] = None
    cov_increment: float = 0.1
    # Minimum seconds between COV notifications (0 = no minimum, for alarms)
    min_cov_interval_sec: float = 0.0
    # internal: last value at which COV was notified
    _cov_last_value: float = field(default=0.0, repr=False, compare=False)
    # internal: monotonic timestamp of last COV notification sent
    _cov_last_ts:    float = field(default=0.0, repr=False, compare=False)

    # Hysteresis factor — require 1.15× threshold to fire, prevents
    # near-threshold oscillation chatter
    _COV_HYSTERESIS: float = field(default=1.15, repr=False, compare=False,
                                   init=False)

    @property
    def object_id(self) -> Tuple[int, int]:
        return (self.object_type, self.instance)

    @property
    def is_binary(self) -> bool:
        return self.object_type in (OBJ_BINARY_INPUT, OBJ_BINARY_OUTPUT,
                                    OBJ_BINARY_VALUE)

    def should_send_cov(self) -> bool:
        """
        Return True when a COV notification should be dispatched.

        Gates (all must pass):
          1. Minimum interval — skip if last notification was too recent.
          2. Threshold + hysteresis — delta must exceed cov_increment × 1.15.
             Binary objects skip hysteresis (any state flip = notify).
        """
        import time as _time
        if self.min_cov_interval_sec > 0.0:
            if (_time.monotonic() - self._cov_last_ts) < self.min_cov_interval_sec:
                return False
        delta = abs(self.present_value - self._cov_last_value)
        if self.is_binary:
            # Binary: notify on any state change (0→1 or 1→0)
            return delta >= 0.5
        return delta >= (self.cov_increment * self._COV_HYSTERESIS)

    # Keep old name as alias so any external callers don't break
    def cov_threshold_exceeded(self) -> bool:
        return self.should_send_cov()

    def mark_cov_sent(self):
        import time as _time
        self._cov_last_value = self.present_value
        self._cov_last_ts    = _time.monotonic()


# ─────────────────────────────────────────────────────────────────
#  BACnet tag ENCODING helpers
# ─────────────────────────────────────────────────────────────────

def _enc_length(n: int) -> bytes:
    """Encode a tag length for the extended-length case (len_enc = 5)."""
    if n <= 253:
        return bytes([n])
    if n <= 0xFFFF:
        return bytes([254, n >> 8, n & 0xFF])
    return bytes([255, (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])


def enc_app_null() -> bytes:
    return b'\x00'


def enc_app_bool(val: bool) -> bytes:
    return bytes([0x11, 0x01 if val else 0x00])


def enc_app_uint(val: int) -> bytes:
    """Application-tagged unsigned integer, minimum encoding."""
    if val == 0:
        return bytes([0x21, 0x00])
    n = (val.bit_length() + 7) // 8
    n = min(n, 4)
    return bytes([(2 << 4) | n]) + val.to_bytes(n, 'big')


def enc_app_real(val: float) -> bytes:
    """Application-tagged 32-bit IEEE 754 real."""
    return b'\x44' + struct.pack('>f', val)


def enc_app_enum(val: int) -> bytes:
    """Application-tagged enumerated."""
    n = max(1, (val.bit_length() + 7) // 8)
    n = min(n, 4)
    return bytes([(9 << 4) | n]) + val.to_bytes(n, 'big')


def enc_app_charstr(s: str) -> bytes:
    """Application-tagged character string (UTF-8 encoding indicator 0x00)."""
    raw = b'\x00' + s.encode('utf-8')
    n = len(raw)
    if n <= 4:
        tag = bytes([(7 << 4) | n])
    else:
        tag = bytes([(7 << 4) | 5]) + _enc_length(n)
    return tag + raw


def enc_app_bitstr(flags: int, num_bits: int = 4) -> bytes:
    """
    Application-tagged bit string.

    BACnet status flags: 4 bits, MSB-first.
    flags bits 3..0 map to the high nibble of the byte.
      bit3 (MSB) = in-alarm
      bit2       = fault
      bit1       = overridden
      bit0 (LSB) = out-of-service
    """
    unused = (8 - (num_bits % 8)) % 8
    byte_count = (num_bits + 7) // 8
    bits_byte = (flags << (8 - num_bits)) & 0xFF
    data = bytes([unused]) + bits_byte.to_bytes(byte_count, 'big')
    n = len(data)
    tag = bytes([(8 << 4) | n])
    return tag + data


def enc_app_oid(obj_type: int, instance: int) -> bytes:
    """Application-tagged BACnetObjectIdentifier (4 bytes)."""
    val = ((obj_type & 0x3FF) << 22) | (instance & 0x3FFFFF)
    return b'\xC4' + val.to_bytes(4, 'big')


def enc_ctx_uint(ctx: int, val: int) -> bytes:
    """Context-tagged unsigned integer."""
    if val == 0:
        data = b'\x00'
    else:
        n = (val.bit_length() + 7) // 8
        data = val.to_bytes(n, 'big')
    n = len(data)
    if n <= 4:
        return bytes([(ctx << 4) | 0x08 | n]) + data
    return bytes([(ctx << 4) | 0x08 | 5]) + _enc_length(n) + data


def enc_ctx_oid(ctx: int, obj_type: int, instance: int) -> bytes:
    """Context-tagged BACnetObjectIdentifier (4 bytes)."""
    val = ((obj_type & 0x3FF) << 22) | (instance & 0x3FFFFF)
    return bytes([(ctx << 4) | 0x08 | 4]) + val.to_bytes(4, 'big')


def enc_ctx_open(ctx: int) -> bytes:
    """Opening context tag."""
    return bytes([(ctx << 4) | 0x0E])


def enc_ctx_close(ctx: int) -> bytes:
    """Closing context tag."""
    return bytes([(ctx << 4) | 0x0F])


# ─────────────────────────────────────────────────────────────────
#  NPDU builder
# ─────────────────────────────────────────────────────────────────

def build_npdu(apdu: bytes, expects_reply: bool = False) -> bytes:
    """Wrap APDU in NPDU (no routing, no source specifier)."""
    ctrl = 0x04 if expects_reply else 0x00
    return bytes([0x01, ctrl]) + apdu


def build_bvll(npdu_data: bytes, broadcast: bool = False) -> bytes:
    """Wrap NPDU data in a BVLL packet."""
    func = BVLC_ORIGINAL_BROADCAST if broadcast else BVLC_ORIGINAL_UNICAST
    length = 4 + len(npdu_data)
    return bytes([BVLL_TYPE, func, length >> 8, length & 0xFF]) + npdu_data


# ─────────────────────────────────────────────────────────────────
#  BVLL / NPDU DECODING helpers
# ─────────────────────────────────────────────────────────────────

def parse_bvll(data: bytes):
    """Parse BVLL header.  Returns (bvlc_function, payload) or (None, None)."""
    if len(data) < 4 or data[0] != BVLL_TYPE:
        return None, None
    func   = data[1]
    length = (data[2] << 8) | data[3]
    if length > len(data):
        return None, None
    # FORWARDED_NPDU has an extra 6-byte B/IP address before the NPDU
    if func == BVLC_FORWARDED_NPDU:
        return func, data[10:length]
    return func, data[4:length]


def parse_npdu(data: bytes):
    """Parse NPDU.  Returns (apdu_data) or None if network-layer message."""
    if len(data) < 2 or data[0] != 0x01:
        return None
    ctrl = data[1]
    if ctrl & 0x80:   # network layer message — not an APDU
        return None
    pos = 2
    if ctrl & 0x20:   # DNET present
        if pos + 2 >= len(data):
            return None
        dlen = data[pos + 2]
        pos += 3 + dlen + 1   # DNET(2) + DLEN(1) + DADR(dlen) + hop-count(1)
    if ctrl & 0x08:   # SNET present
        if pos + 2 >= len(data):
            return None
        slen = data[pos + 2]
        pos += 3 + slen         # SNET(2) + SLEN(1) + SADR(slen)
    return data[pos:]


def parse_apdu(data: bytes):
    """
    APDU parser — handles both request and response PDU types.

    Returns dict with at least:
      type     : 'confirmed' | 'unconfirmed' |
                 'simple_ack' | 'complex_ack' |
                 'error' | 'reject' | 'abort'
      service  : int   (0 for reject/abort)
      data     : bytes (payload after header)
    Confirmed requests and response types also carry:
      invoke_id: int
    Returns None on parse error.
    """
    if not data:
        return None
    pdu_type = (data[0] >> 4) & 0x0F

    if pdu_type == PDU_UNCONFIRMED_REQUEST:
        if len(data) < 2:
            return None
        return {'type': 'unconfirmed', 'service': data[1], 'data': data[2:]}

    if pdu_type == PDU_CONFIRMED_REQUEST:
        # Byte 0: PDU-type(4) | SEG(1) | MOR(1) | SA(1) | reserved(1)
        # Byte 1: MAX-SEGS(4) | MAX-RESP(4)
        # Byte 2: Invoke-ID
        # [Byte 3: Sequence-Number if segmented]
        # [Byte 4: Window-Size if segmented]
        # Next  : Service-Choice
        if len(data) < 4:
            return None
        segmented = bool(data[0] & 0x08)
        pos = 3
        if segmented:
            pos += 2   # skip sequence + window
        if pos >= len(data):
            return None
        return {
            'type': 'confirmed',
            'invoke_id': data[2],
            'service': data[pos],
            'data': data[pos + 1:],
        }

    # ── Response PDU types ────────────────────────────────────────
    # These are returned by devices to clients (ComplexAck, SimpleAck,
    # Error, Reject, Abort).  The simulator itself never *receives* them,
    # but test clients and any future client code need to parse them.

    if pdu_type == PDU_SIMPLE_ACK:
        # Byte 0: 0x20, Byte 1: invoke-id, Byte 2: service-ack-choice
        if len(data) < 3:
            return None
        return {'type': 'simple_ack', 'invoke_id': data[1],
                'service': data[2], 'data': b''}

    if pdu_type == PDU_COMPLEX_ACK:
        # Byte 0: 0x30 | flags, Byte 1: invoke-id,
        # Byte 2: service-ack-choice, Byte 3+: value
        if len(data) < 3:
            return None
        return {'type': 'complex_ack', 'invoke_id': data[1],
                'service': data[2], 'data': data[3:]}

    if pdu_type == PDU_ERROR:
        # Byte 0: 0x50, Byte 1: invoke-id,
        # Byte 2: service-choice, Byte 3+: error-class + error-code
        if len(data) < 3:
            return None
        return {'type': 'error', 'invoke_id': data[1],
                'service': data[2], 'data': data[3:]}

    if pdu_type == PDU_REJECT:
        # Byte 0: 0x60, Byte 1: invoke-id, Byte 2: reject-reason
        if len(data) < 2:
            return None
        return {'type': 'reject', 'invoke_id': data[1],
                'service': 0, 'data': data[2:]}

    if pdu_type == PDU_ABORT:
        # Byte 0: 0x70 | server-bit, Byte 1: invoke-id,
        # Byte 2: abort-reason
        if len(data) < 2:
            return None
        return {'type': 'abort', 'invoke_id': data[1],
                'service': 0, 'data': data[2:]}

    return None


# ─────────────────────────────────────────────────────────────────
#  Tag / value DECODING helpers (for parsing inbound requests)
# ─────────────────────────────────────────────────────────────────

def _decode_tag_hdr(data: bytes, pos: int):
    """
    Parse one tag header at data[pos].

    Returns (tag_num, is_context, is_opening, is_closing, value_len, new_pos)
    or raises IndexError on truncation.
    """
    b = data[pos]
    tag_raw = (b >> 4) & 0x0F
    is_ctx  = bool(b & 0x08)
    len_enc = b & 0x07
    pos += 1

    if tag_raw == 0x0F:        # extended tag number
        tag_num = data[pos]; pos += 1
    else:
        tag_num = tag_raw

    if is_ctx and len_enc == 6:
        return tag_num, True, True, False, 0, pos
    if is_ctx and len_enc == 7:
        return tag_num, True, False, True, 0, pos

    if len_enc == 5:           # extended length
        ext = data[pos]; pos += 1
        if ext == 254:
            vlen = (data[pos] << 8) | data[pos + 1]; pos += 2
        elif ext == 255:
            vlen = int.from_bytes(data[pos:pos + 4], 'big'); pos += 4
        else:
            vlen = ext
    else:
        vlen = len_enc

    return tag_num, is_ctx, False, False, vlen, pos


def decode_uint(data: bytes, pos: int, length: int) -> int:
    """Read an unsigned int from data[pos:pos+length]."""
    return int.from_bytes(data[pos:pos + length], 'big')


def decode_oid_bytes(raw4: bytes) -> Tuple[int, int]:
    """Decode a 4-byte object-identifier into (object_type, instance)."""
    val = int.from_bytes(raw4, 'big')
    return (val >> 22) & 0x3FF, val & 0x3FFFFF


def decode_whois(data: bytes):
    """
    Parse Who-Is payload.

    Returns (low, high) device instance range, or (None, None) for global.
    """
    if not data:
        return None, None
    try:
        pos = 0
        _tn, _ic, _io, _ic2, vlen, pos = _decode_tag_hdr(data, pos)
        low  = decode_uint(data, pos, vlen);  pos += vlen
        _tn, _ic, _io, _ic2, vlen, pos = _decode_tag_hdr(data, pos)
        high = decode_uint(data, pos, vlen)
        return low, high
    except Exception:
        return None, None


def decode_read_property(data: bytes):
    """
    Parse ReadProperty request payload.

    Returns (obj_type, obj_instance, prop_id, array_index) or None.
    array_index is None if not specified.
    """
    try:
        pos = 0
        # context[0]: object-identifier (4 bytes)
        _tn, _ic, _io, _ico, vlen, pos = _decode_tag_hdr(data, pos)
        obj_type, obj_inst = decode_oid_bytes(data[pos:pos + 4])
        pos += 4
        # context[1]: property-identifier
        _tn, _ic, _io, _ico, vlen, pos = _decode_tag_hdr(data, pos)
        prop_id = decode_uint(data, pos, vlen); pos += vlen
        # optional context[2]: array-index
        array_index = None
        if pos < len(data):
            _tn, _ic, _io, _ico, vlen, pos2 = _decode_tag_hdr(data, pos)
            if not (_io or _ico):
                array_index = decode_uint(data, pos2, vlen)
        return obj_type, obj_inst, prop_id, array_index
    except Exception:
        return None


def decode_read_property_multiple(data: bytes):
    """
    Parse ReadPropertyMultiple request payload.

    Returns list of {obj_type, obj_inst, props: [(prop_id, array_index), ...]}
    """
    results = []
    pos = 0
    try:
        while pos < len(data):
            # context[0]: object-identifier
            _tn, _ic, _io, _ico, vlen, pos = _decode_tag_hdr(data, pos)
            if vlen < 4:
                break
            obj_type, obj_inst = decode_oid_bytes(data[pos:pos + 4])
            pos += 4

            # context[1] opening
            _tn, _ic, is_open, _ico, _vl, pos = _decode_tag_hdr(data, pos)
            if not is_open:
                break

            props = []
            while pos < len(data):
                _tn, _ic, _io, is_close, _vl, _ = _decode_tag_hdr(data, pos)
                if is_close:
                    pos = _ ; break

                # context[0]: property-identifier
                _tn, _ic, _io2, _ico2, vlen2, pos = _decode_tag_hdr(data, pos)
                prop_id = decode_uint(data, pos, vlen2); pos += vlen2

                # optional context[1]: array-index
                array_index = None
                if pos < len(data):
                    _tn2, _ic2, _io2, _ico2, vlen3, pos3 = _decode_tag_hdr(data, pos)
                    if _tn2 == 1 and _ic2 and not _io2 and not _ico2:
                        array_index = decode_uint(data, pos3, vlen3)
                        pos = pos3 + vlen3

                props.append((prop_id, array_index))

            results.append({'obj_type': obj_type, 'obj_inst': obj_inst, 'props': props})
    except Exception:
        pass
    return results


def decode_subscribe_cov(data: bytes):
    """
    Parse SubscribeCOV request payload.

    Returns dict with:
      process_id, obj_type, obj_inst, confirmed, lifetime
    confirmed/lifetime are None → cancel subscription.
    """
    try:
        pos = 0
        # context[0]: subscriber-process-id
        _tn, _ic, _io, _ico, vlen, pos = _decode_tag_hdr(data, pos)
        process_id = decode_uint(data, pos, vlen); pos += vlen
        # context[1]: monitored-object-id
        _tn, _ic, _io, _ico, vlen, pos = _decode_tag_hdr(data, pos)
        obj_type, obj_inst = decode_oid_bytes(data[pos:pos + 4]); pos += 4
        # optional context[2]: issue-confirmed-notifications
        confirmed = False
        lifetime  = 0
        if pos < len(data):
            _tn, _ic, _io, _ico, vlen, pos2 = _decode_tag_hdr(data, pos)
            if not (_io or _ico):
                confirmed = bool(decode_uint(data, pos2, vlen)); pos = pos2 + vlen
        if pos < len(data):
            _tn, _ic, _io, _ico, vlen, pos2 = _decode_tag_hdr(data, pos)
            if not (_io or _ico):
                lifetime = decode_uint(data, pos2, vlen)
        return {
            'process_id': process_id,
            'obj_type':   obj_type,
            'obj_inst':   obj_inst,
            'confirmed':  confirmed,
            'lifetime':   lifetime,
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
#  APDU builders
# ─────────────────────────────────────────────────────────────────

def build_iam(device_instance: int, max_apdu: int = MAX_APDU_LENGTH) -> bytes:
    """Build an I-Am unconfirmed broadcast packet (full BVLL frame)."""
    apdu = bytes([
        (PDU_UNCONFIRMED_REQUEST << 4),  # PDU type
        SVC_I_AM,                         # service choice
    ])
    apdu += enc_app_oid(OBJ_DEVICE, device_instance)
    apdu += enc_app_uint(max_apdu)
    apdu += enc_app_enum(SEGMENTATION_NO_SEGMENTATION)
    apdu += enc_app_uint(VERDIGRIS_VENDOR_ID)
    return build_bvll(build_npdu(apdu), broadcast=True)


def build_iam_unicast(device_instance: int, max_apdu: int = MAX_APDU_LENGTH) -> bytes:
    """Build an I-Am packet as unicast (for directed Who-Is)."""
    apdu = bytes([
        (PDU_UNCONFIRMED_REQUEST << 4),
        SVC_I_AM,
    ])
    apdu += enc_app_oid(OBJ_DEVICE, device_instance)
    apdu += enc_app_uint(max_apdu)
    apdu += enc_app_enum(SEGMENTATION_NO_SEGMENTATION)
    apdu += enc_app_uint(VERDIGRIS_VENDOR_ID)
    return build_bvll(build_npdu(apdu), broadcast=False)


def build_simple_ack(invoke_id: int, service: int) -> bytes:
    """Build a SimpleAck response (e.g. for SubscribeCOV)."""
    apdu = bytes([
        (PDU_SIMPLE_ACK << 4),
        invoke_id,
        service,
    ])
    return build_bvll(build_npdu(apdu))


def build_error(invoke_id: int, service: int,
                err_class: int, err_code: int) -> bytes:
    """Build a BACnet Error response."""
    apdu = bytes([(PDU_ERROR << 4), invoke_id, service])
    apdu += enc_app_enum(err_class)
    apdu += enc_app_enum(err_code)
    return build_bvll(build_npdu(apdu))


def build_reject(invoke_id: int, reason: int = 9) -> bytes:
    """Build a BACnet Reject response (reason 9 = unrecognised-service)."""
    apdu = bytes([(PDU_REJECT << 4), invoke_id, reason])
    return build_bvll(build_npdu(apdu))


def encode_property_value(obj: BACnetObject, prop_id: int) -> Optional[bytes]:
    """
    Encode a single property of *obj* as application-tagged bytes.

    Returns None if the property is not supported.
    The returned bytes are the raw value bytes (no context wrapping).
    The caller wraps in context[3] for ReadProperty ACK.
    """
    if prop_id == PROP_OBJECT_IDENTIFIER:
        return enc_app_oid(obj.object_type, obj.instance)

    if prop_id == PROP_OBJECT_NAME:
        return enc_app_charstr(obj.name)

    if prop_id == PROP_OBJECT_TYPE:
        return enc_app_enum(obj.object_type)

    if prop_id == PROP_DESCRIPTION:
        return enc_app_charstr(obj.description)

    if prop_id == PROP_PRESENT_VALUE:
        if obj.is_binary:
            return enc_app_enum(1 if obj.present_value else 0)
        return enc_app_real(float(obj.present_value))

    if prop_id == PROP_UNITS:
        return enc_app_enum(obj.units)

    if prop_id == PROP_STATUS_FLAGS:
        return enc_app_bitstr(obj.status_flags, 4)

    if prop_id == PROP_RELIABILITY:
        return enc_app_enum(obj.reliability)

    if prop_id == PROP_OUT_OF_SERVICE:
        return enc_app_bool(obj.out_of_service)

    if prop_id == PROP_COV_INCREMENT:
        return enc_app_real(obj.cov_increment)

    if prop_id == PROP_MIN_PRES_VALUE and obj.min_pres_value is not None:
        return enc_app_real(obj.min_pres_value)

    if prop_id == PROP_MAX_PRES_VALUE and obj.max_pres_value is not None:
        return enc_app_real(obj.max_pres_value)

    if prop_id == PROP_RESOLUTION and obj.resolution is not None:
        return enc_app_real(obj.resolution)

    return None   # property not supported


def encode_device_property_value(
    prop_id: int,
    device_instance: int,
    device_name: str,
    object_list: List[BACnetObject],
    array_index: Optional[int] = None,
) -> Optional[bytes]:
    """
    Encode device-object-only properties.

    Returns None for unsupported properties.
    """
    if prop_id == PROP_OBJECT_IDENTIFIER:
        return enc_app_oid(OBJ_DEVICE, device_instance)

    if prop_id == PROP_OBJECT_NAME:
        return enc_app_charstr(device_name)

    if prop_id == PROP_OBJECT_TYPE:
        return enc_app_enum(OBJ_DEVICE)

    if prop_id == PROP_DESCRIPTION:
        return enc_app_charstr(f"Verdigris EV2 Energy Intelligence Platform — {device_name}")

    if prop_id == PROP_VENDOR_NAME:
        return enc_app_charstr(VERDIGRIS_VENDOR_NAME)

    if prop_id == PROP_VENDOR_IDENTIFIER:
        return enc_app_uint(VERDIGRIS_VENDOR_ID)

    if prop_id == PROP_MODEL_NAME:
        return enc_app_charstr(VERDIGRIS_MODEL_NAME)

    if prop_id == PROP_FIRMWARE_REVISION:
        return enc_app_charstr(VERDIGRIS_FIRMWARE_REVISION)

    if prop_id == PROP_APPLICATION_SOFTWARE_VERSION:
        return enc_app_charstr(VERDIGRIS_APP_SW_VERSION)

    if prop_id == PROP_PROTOCOL_VERSION:
        return enc_app_uint(1)

    if prop_id == PROP_PROTOCOL_REVISION:
        return enc_app_uint(19)

    if prop_id == PROP_MAX_APDU_LENGTH_ACCEPTED:
        return enc_app_uint(MAX_APDU_LENGTH)

    if prop_id == PROP_SEGMENTATION_SUPPORTED:
        return enc_app_enum(SEGMENTATION_NO_SEGMENTATION)

    if prop_id == PROP_SYSTEM_STATUS:
        return enc_app_enum(DEVICE_STATUS_OPERATIONAL)

    if prop_id == PROP_OBJECT_LIST:
        # BACnet Array property (clause 12.11 / Annex K):
        #   array_index=None → whole array (all OIDs concatenated)
        #   array_index=0    → count (Unsigned)
        #   array_index=n    → nth element (1-based OID)
        all_oids: List[BACnetObject] = []   # build unified list
        # element 0 in the logical array is the device object itself
        _device_oid_bytes = enc_app_oid(OBJ_DEVICE, device_instance)
        _obj_oids_bytes   = [enc_app_oid(o.object_type, o.instance)
                             for o in object_list]
        total = 1 + len(object_list)        # device obj + all other objects

        if array_index == 0:
            return enc_app_uint(total)

        if array_index is not None:
            # 1-based index into the array
            idx = array_index
            if idx == 1:
                return _device_oid_bytes
            if 2 <= idx <= total:
                return _obj_oids_bytes[idx - 2]
            return None   # out of range → caller sends error

        # No index → return full array
        return _device_oid_bytes + b''.join(_obj_oids_bytes)

    if prop_id == PROP_STATUS_FLAGS:
        return enc_app_bitstr(0, 4)

    if prop_id == PROP_RELIABILITY:
        return enc_app_enum(RELIABILITY_NO_FAULT_DETECTED)

    if prop_id == PROP_OUT_OF_SERVICE:
        return enc_app_bool(False)

    return None

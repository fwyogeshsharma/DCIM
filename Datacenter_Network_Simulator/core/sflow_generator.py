"""
sFlow v5 datagram builder (RFC 3176 / sFlow v5 specification).

Generates binary UDP payloads containing:
  Counter samples — interface counters from DeviceStateStore metrics dict
  Flow samples    — synthetic sampled packet headers (topology-based src/dst IPs)

No external libraries required — struct, socket, ipaddress, random (all stdlib).
"""
from __future__ import annotations

import ipaddress
import random
import struct
from typing import List, Tuple

# ── sFlow v5 constants ────────────────────────────────────────────────────────

_VERSION           = 5
_ADDR_IPV4         = 1

# Top-level sample envelope types (enterprise=0)
_SAMPLE_FLOW       = 1    # flow_sample
_SAMPLE_COUNTER    = 2    # counter_sample

# Counter sub-record formats (enterprise=0)
_CTR_GENERIC_IFACE = 1    # if_counters (RFC 2863)

# Flow sub-record formats (enterprise=0)
_FLOW_RAW_HEADER   = 1    # sampled_header

# sampled_header protocol field
_PROTO_ETHERNET    = 1

# IANA ifType
_IFTYPE_ETHERNET   = 6    # ethernetCsmacd

# ifStatus bitmask: bit0=adminUp, bit1=operUp
_IF_STATUS_UP      = 0b11

# Port profiles: device_type → [(src_port, dst_port), ...]
_PORT_PROFILES: dict = {
    "router":        [(179, 179), (22, 22), (161, 161), (443, 8443)],
    "switch":        [(22, 22),   (161, 161), (80, 80)],
    "server":        [(80, 49152), (443, 49153), (22, 49154), (3306, 49155)],
    "firewall":      [(443, 443),  (80, 80),  (22, 22)],
    "load_balancer": [(80, 80),    (443, 443), (8080, 8080)],
}

# Protocol choices per device type (6=TCP, 17=UDP, 89=OSPF)
_PROTO_CHOICES: dict = {
    "router":        [6, 6, 17, 89],
    "switch":        [6, 17],
    "server":        [6, 6, 6, 17],
    "firewall":      [6, 17],
    "load_balancer": [6, 6, 17],
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pack_ipv4(addr: str) -> bytes:
    try:
        return ipaddress.IPv4Address(addr).packed
    except Exception:
        return b'\x00\x00\x00\x00'


def _xdr_opaque(data: bytes) -> bytes:
    """XDR variable-length opaque: 4-byte length then data padded to 4 bytes."""
    pad = (4 - len(data) % 4) % 4
    return struct.pack("!I", len(data)) + data + b'\x00' * pad


def _make_record(enterprise: int, fmt: int, data: bytes) -> bytes:
    """Wrap a sub-record in its (enterprise<<12|format, length, data) envelope."""
    return struct.pack("!II", (enterprise << 12) | fmt, len(data)) + data


def _make_sample(sample_type: int, data: bytes) -> bytes:
    """Wrap a sample record in its (type, length, data) top-level envelope."""
    return struct.pack("!II", sample_type, len(data)) + data


# ── Public API ────────────────────────────────────────────────────────────────

class SFlowGenerator:
    """Build sFlow v5 binary UDP datagrams from live metrics dicts."""

    # ── Datagram builders ─────────────────────────────────────────────────────

    def build_counter_datagram(
        self,
        agent_ip: str,
        sub_agent_id: int,
        sequence_num: int,
        uptime_ms: int,
        iface_metrics: List[Tuple[int, int, dict]],
    ) -> bytes:
        """
        One counter sample per interface.

        iface_metrics: list of (if_index, if_speed_bps, stats_dict)
        stats_dict keys: in_octets, out_octets, in_unicast_pkts, out_unicast_pkts,
                         in_errors, out_errors, in_discards, out_discards
        """
        samples = [
            _make_sample(_SAMPLE_COUNTER,
                         self._counter_sample_data(seq, idx, speed, stats))
            for seq, (idx, speed, stats) in enumerate(iface_metrics, start=1)
        ]
        return self._header(agent_ip, sub_agent_id, sequence_num,
                            uptime_ms, len(samples)) + b"".join(samples)

    def build_flow_datagram(
        self,
        agent_ip: str,
        sub_agent_id: int,
        sequence_num: int,
        uptime_ms: int,
        flow_entries: List[Tuple],
        sample_rate: int = 1000,
    ) -> bytes:
        """
        One flow sample per entry.

        flow_entries: list of (if_index, src_ip, dst_ip, src_port, dst_port, protocol)
        """
        samples = [
            _make_sample(_SAMPLE_FLOW,
                         self._flow_sample_data(seq, *entry, sample_rate))
            for seq, entry in enumerate(flow_entries, start=1)
        ]
        return self._header(agent_ip, sub_agent_id, sequence_num,
                            uptime_ms, len(samples)) + b"".join(samples)

    # ── sFlow v5 datagram header ──────────────────────────────────────────────

    @staticmethod
    def _header(agent_ip: str, sub_agent_id: int,
                seq: int, uptime_ms: int, num_samples: int) -> bytes:
        return (
            struct.pack("!I", _VERSION)
            + struct.pack("!I", _ADDR_IPV4)
            + _pack_ipv4(agent_ip)
            + struct.pack("!IIII", sub_agent_id, seq, uptime_ms, num_samples)
        )

    # ── Counter sample ────────────────────────────────────────────────────────

    @staticmethod
    def _counter_sample_data(seq: int, if_index: int,
                              if_speed: int, stats: dict) -> bytes:
        in_oct   = stats.get("in_octets",       0)
        out_oct  = stats.get("out_octets",      0)
        in_pkts  = stats.get("in_unicast_pkts",  max(1, in_oct  // 1400))
        out_pkts = stats.get("out_unicast_pkts", max(1, out_oct // 1400))
        in_err   = stats.get("in_errors",        0)
        out_err  = stats.get("out_errors",       0)
        in_disc  = stats.get("in_discards",      0)
        out_disc = stats.get("out_discards",     0)

        M = 0xFFFFFFFFFFFFFFFF  # 64-bit mask
        m = 0xFFFFFFFF          # 32-bit mask

        # Generic interface counters block — 88 bytes (sFlow v5 if_counters)
        iface_data = (
            struct.pack("!II", if_index, _IFTYPE_ETHERNET)
            + struct.pack("!Q", min(if_speed, M))        # ifSpeed
            + struct.pack("!II", 1, _IF_STATUS_UP)       # ifDirection, ifStatus
            + struct.pack("!Q", in_oct & M)              # ifInOctets
            + struct.pack("!IIIII",
                in_pkts  & m, 0, 0,
                in_disc  & m,
                in_err   & m,
              )
            + struct.pack("!I", 0)                       # ifInUnknownProtos
            + struct.pack("!Q", out_oct & M)             # ifOutOctets
            + struct.pack("!IIIII",
                out_pkts & m, 0, 0,
                out_disc & m,
                out_err  & m,
              )
            + struct.pack("!I", 0)                       # ifPromiscuousMode
        )
        counter_rec = _make_record(0, _CTR_GENERIC_IFACE, iface_data)

        return struct.pack("!III",
            seq,
            if_index,   # source_id = (type=0 << 30) | if_index
            1,          # num counter records
        ) + counter_rec

    # ── Flow sample ───────────────────────────────────────────────────────────

    @staticmethod
    def _flow_sample_data(seq: int, if_index: int,
                           src_ip: str, dst_ip: str,
                           src_port: int, dst_port: int,
                           protocol: int,
                           sample_rate: int) -> bytes:
        frame_size = random.randint(64, 1514)

        # Synthetic Ethernet + IP + ports (38 bytes)
        eth = (
            bytes([0x00, 0x1a, 0x2b, 0x3c, 0x4d, 0x5e])
            + bytes([0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff])
            + b'\x08\x00'
        )
        ip_hdr = struct.pack("!BBHHHBBH4s4s",
            0x45, 0,
            min(frame_size - 14, 65535),
            random.randint(1, 65535),
            0x4000, 64,
            protocol, 0,
            _pack_ipv4(src_ip),
            _pack_ipv4(dst_ip),
        )
        transport = (struct.pack("!HH", src_port, dst_port)
                     if protocol in (6, 17) else b'\x00\x00\x00\x00')
        raw = eth + ip_hdr + transport

        header_data = (
            struct.pack("!III", _PROTO_ETHERNET, frame_size, 0)
            + _xdr_opaque(raw)
        )
        flow_rec = _make_record(0, _FLOW_RAW_HEADER, header_data)

        sample_data = struct.pack("!IIIIIII",
            seq,
            if_index,           # source_id
            sample_rate,
            sample_rate * seq,  # sample_pool (synthetic)
            0,                  # drops
            if_index,           # input interface
            0x40000000,         # output = multiple/unknown
        )
        sample_data += struct.pack("!I", 1)   # num flow records
        sample_data += flow_rec
        return sample_data


def pick_flow_ports(device_type: str) -> Tuple[int, int]:
    """Return (src_port, dst_port) appropriate for a device type."""
    profiles = _PORT_PROFILES.get(device_type, [(80, 49152)])
    return random.choice(profiles)


def pick_protocol(device_type: str) -> int:
    """Return IP protocol number appropriate for a device type."""
    return random.choice(_PROTO_CHOICES.get(device_type, [6]))
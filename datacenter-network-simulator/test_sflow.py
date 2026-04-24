"""
sFlow v5 Test Receiver
======================
Listens on UDP, decodes sFlow v5 datagrams, and shows a live aggregated
summary updated every N seconds.

Usage:
    python test_sflow.py                          # summary every 30s
    python test_sflow.py --interval 10            # summary every 10s
    python test_sflow.py --verbose                # also print raw datagrams
    python test_sflow.py --port 6343
    python test_sflow.py --host 127.0.0.1 --port 6343
    python test_sflow.py --counters-only          # suppress flow output in verbose mode
    python test_sflow.py --flows-only             # suppress counter output in verbose mode
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import struct
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

# ── sFlow v5 constants ────────────────────────────────────────────────────────

_SAMPLE_FLOW    = 1
_SAMPLE_COUNTER = 2
_CTR_GENERIC_IFACE = 1
_FLOW_RAW_HEADER   = 1
_PROTO_NAMES = {1: "Ethernet", 11: "IPv4", 12: "IPv6"}
_IP_PROTO    = {1: "ICMP", 6: "TCP", 17: "UDP", 89: "OSPF"}
_PORT_NAMES  = {
    22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    80: "HTTP", 123: "NTP", 161: "SNMP", 179: "BGP",
    443: "HTTPS", 514: "Syslog", 3306: "MySQL",
    5432: "PostgreSQL", 6379: "Redis", 8080: "HTTP-alt",
    8443: "HTTPS-alt",
}

# ── ANSI colours ──────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()

def _c(code, text):  return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text
def _green(t):       return _c("32", t)
def _cyan(t):        return _c("36", t)
def _yellow(t):      return _c("33", t)
def _red(t):         return _c("31", t)
def _blue(t):        return _c("34", t)
def _bold(t):        return _c("1",  t)
def _dim(t):         return _c("2",  t)
def _magenta(t):     return _c("35", t)

def _bar(ratio: float, width: int = 20) -> str:
    filled = int(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    if ratio > 0.7:   return _red(bar)
    if ratio > 0.4:   return _yellow(bar)
    return _green(bar)

def _fmt_bytes(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f} GB"
    if n >= 1_000_000:     return f"{n/1_000_000:.1f} MB"
    if n >= 1_000:         return f"{n/1_000:.1f} KB"
    return f"{n} B"

def _fmt_rate(bps: float) -> str:
    if bps >= 1_000_000_000: return f"{bps/1_000_000_000:.1f} Gbps"
    if bps >= 1_000_000:     return f"{bps/1_000_000:.1f} Mbps"
    if bps >= 1_000:         return f"{bps/1_000:.1f} Kbps"
    return f"{bps:.0f} bps"


# ── Aggregator ────────────────────────────────────────────────────────────────

class Aggregator:
    """Thread-safe accumulator for one time window."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        # Flow matrix: (src_ip, dst_ip) → [flow_count, estimated_bytes]
        self.flow_pairs:    dict = defaultdict(lambda: [0, 0])
        # Port usage: dst_port → flow_count
        self.port_counts:   dict = defaultdict(int)
        # Protocol usage: proto_name → flow_count
        self.proto_counts:  dict = defaultdict(int)
        # Per-device flow count (for anomaly detection)
        self.device_flows:  dict = defaultdict(int)
        # Counter bandwidth: agent_ip → {"in_bytes": int, "out_bytes": int}
        # We store the max seen in the window (counter values are cumulative)
        self.agent_counters: dict = defaultdict(lambda: {"in": 0, "out": 0})
        # Previous window counters for delta calculation
        self.prev_counters:  dict = {}
        # Totals
        self.dgrams   = 0
        self.t_start  = time.monotonic()

    def record_flow(self, src_ip: str, dst_ip: str,
                    dst_port: int, proto: str, frame_len: int):
        with self._lock:
            key = (src_ip, dst_ip)
            self.flow_pairs[key][0] += 1
            self.flow_pairs[key][1] += frame_len
            self.port_counts[dst_port] += 1
            self.proto_counts[proto]   += 1
            self.device_flows[src_ip]  += 1

    def record_counter(self, agent_ip: str, if_index: int,
                        in_bytes: int, out_bytes: int):
        with self._lock:
            cur = self.agent_counters[agent_ip]
            cur["in"]  += in_bytes
            cur["out"] += out_bytes

    def record_datagram(self):
        with self._lock:
            self.dgrams += 1

    def snapshot_and_reset(self) -> dict:
        """Return a copy of current window data then reset for next window."""
        with self._lock:
            snap = {
                "flow_pairs":    dict(self.flow_pairs),
                "port_counts":   dict(self.port_counts),
                "proto_counts":  dict(self.proto_counts),
                "device_flows":  dict(self.device_flows),
                "agent_counters": dict(self.agent_counters),
                "dgrams":        self.dgrams,
                "elapsed":       time.monotonic() - self.t_start,
            }
            self._reset()
            return snap


# ── Binary decoder ────────────────────────────────────────────────────────────

class _Buf:
    def __init__(self, data: bytes):
        self._d   = data
        self._pos = 0

    def remaining(self): return len(self._d) - self._pos
    def u32(self):
        v, = struct.unpack_from("!I", self._d, self._pos); self._pos += 4; return v
    def u64(self):
        v, = struct.unpack_from("!Q", self._d, self._pos); self._pos += 8; return v
    def ipv4(self):
        raw = self._d[self._pos:self._pos+4]; self._pos += 4
        try:    return str(ipaddress.IPv4Address(raw))
        except: return "0.0.0.0"
    def raw(self, n):
        out = self._d[self._pos:self._pos+n]; self._pos += n; return out
    def slice(self, n):
        out = _Buf(self._d[self._pos:self._pos+n]); self._pos += n; return out


def decode_datagram(data: bytes) -> dict | None:
    try:
        buf = _Buf(data)
        if buf.u32() != 5: return None          # version must be 5
        addr_type = buf.u32()
        agent_ip  = buf.ipv4() if addr_type == 1 else buf.raw(16).hex()
        buf.u32()                                # sub_agent_id
        seq       = buf.u32()
        buf.u32()                                # uptime_ms
        n_samples = buf.u32()
        samples   = []
        for _ in range(n_samples):
            if buf.remaining() < 8: break
            stype = buf.u32(); slen = buf.u32()
            if buf.remaining() < slen: break
            sbuf  = buf.slice(slen)
            s     = _decode_sample(stype, sbuf)
            if s: samples.append(s)
        return {"agent_ip": agent_ip, "seq": seq, "samples": samples}
    except Exception:
        return None


def _decode_sample(stype, buf):
    if stype == _SAMPLE_COUNTER: return _decode_counter(buf)
    if stype == _SAMPLE_FLOW:    return _decode_flow(buf)
    return None

def _decode_counter(buf):
    seq = buf.u32(); source_id = buf.u32(); n = buf.u32()
    records = []
    for _ in range(n):
        if buf.remaining() < 8: break
        dfmt = buf.u32(); rlen = buf.u32()
        rbuf = buf.slice(rlen)
        r = _decode_ctr_record(dfmt & 0xFFF, dfmt >> 12, rbuf)
        if r: records.append(r)
    return {"type": "counter", "seq": seq, "source_id": source_id, "records": records}

def _decode_ctr_record(fmt, ent, buf):
    if ent == 0 and fmt == _CTR_GENERIC_IFACE:
        if_index  = buf.u32(); buf.u32()         # ifType
        buf.u64()                                 # ifSpeed
        buf.u32()                                 # ifDirection
        if_status = buf.u32()
        in_oct    = buf.u64(); in_up = buf.u32()
        buf.u32(); buf.u32()
        in_disc = buf.u32(); in_err = buf.u32(); buf.u32()
        out_oct   = buf.u64(); out_up = buf.u32()
        buf.u32(); buf.u32()
        out_disc= buf.u32(); out_err= buf.u32()
        return {
            "kind": "generic", "if_index": if_index,
            "admin_up": bool(if_status & 1), "oper_up": bool(if_status & 2),
            "in_octets": in_oct,  "out_octets": out_oct,
            "in_pkts":   in_up,   "out_pkts":   out_up,
            "in_errors": in_err,  "out_errors":  out_err,
            "in_discards": in_disc, "out_discards": out_disc,
        }
    return None

def _decode_flow(buf):
    seq = buf.u32(); source_id = buf.u32()
    rate = buf.u32(); pool = buf.u32(); drops = buf.u32()
    in_if = buf.u32(); buf.u32(); n = buf.u32()
    records = []
    for _ in range(n):
        if buf.remaining() < 8: break
        dfmt = buf.u32(); rlen = buf.u32()
        rbuf = buf.slice(rlen)
        r = _decode_flow_record(dfmt & 0xFFF, dfmt >> 12, rbuf)
        if r: records.append(r)
    return {"type": "flow", "seq": seq, "sample_rate": rate,
            "in_iface": in_if, "records": records}

def _decode_flow_record(fmt, ent, buf):
    if ent == 0 and fmt == _FLOW_RAW_HEADER:
        proto     = buf.u32()
        frame_len = buf.u32()
        buf.u32()                       # stripped
        hdr_len   = buf.u32()
        pad = (4 - hdr_len % 4) % 4
        header = buf.raw(hdr_len + pad)[:hdr_len]
        parsed = _parse_eth(header)
        return {"kind": "raw", "protocol": _PROTO_NAMES.get(proto, str(proto)),
                "frame_len": frame_len, **parsed}
    return None

def _parse_eth(data):
    if len(data) < 14: return {}
    etype = struct.unpack("!H", data[12:14])[0]
    result = {}
    if etype == 0x0800 and len(data) >= 34:
        ip = data[14:]
        proto  = ip[9]
        result["ip_src"]   = socket.inet_ntoa(ip[12:16])
        result["ip_dst"]   = socket.inet_ntoa(ip[16:20])
        result["ip_proto"] = _IP_PROTO.get(proto, str(proto))
        if proto in (6, 17) and len(ip) >= 24:
            sp, dp = struct.unpack("!HH", ip[20:24])
            result["src_port"] = sp
            result["dst_port"] = dp
    return result


# ── Summary printer ───────────────────────────────────────────────────────────

_TOP_N = 10

def print_summary(snap: dict, interval: int):
    elapsed  = max(snap["elapsed"], 0.001)
    now      = datetime.now().strftime("%H:%M:%S")
    dgrams   = snap["dgrams"]
    flows    = snap["flow_pairs"]
    ports    = snap["port_counts"]
    protos   = snap["proto_counts"]
    dev_flow = snap["device_flows"]
    counters = snap["agent_counters"]

    sep = _dim("─" * 70)
    print(f"\n{sep}")
    print(_bold(f"  sFlow Summary  [{now}]  window={interval}s  datagrams={dgrams}"))
    print(sep)

    # ── Bandwidth from counter samples ────────────────────────────────────────
    if counters:
        print(_bold("\n  Bandwidth (counter samples)"))
        total_in = sum(v["in"]  for v in counters.values())
        total_out= sum(v["out"] for v in counters.values())
        top_bw = sorted(counters.items(),
                        key=lambda x: x[1]["in"] + x[1]["out"], reverse=True)[:_TOP_N]
        max_bw = max((v["in"]+v["out"]) for v in counters.values()) or 1
        for ip, v in top_bw:
            ratio = (v["in"] + v["out"]) / max_bw
            rate_in  = _fmt_rate(v["in"]  * 8 / elapsed)
            rate_out = _fmt_rate(v["out"] * 8 / elapsed)
            print(f"  {_cyan(ip):20s}  {_bar(ratio)}  ↓{rate_in}  ↑{rate_out}")
        print(f"  {_dim('Total'):20s}  {'':20s}  "
              f"↓{_fmt_rate(total_in*8/elapsed)}  ↑{_fmt_rate(total_out*8/elapsed)}")

    # ── Top conversations (who talks to whom) ─────────────────────────────────
    if flows:
        print(_bold("\n  Top Conversations (flow samples)"))
        top_flows = sorted(flows.items(), key=lambda x: x[1][1], reverse=True)[:_TOP_N]
        max_bytes = max(v[1] for v in flows.values()) or 1
        for (src, dst), (count, est_bytes) in top_flows:
            ratio = est_bytes / max_bytes
            print(f"  {_yellow(src):18s} → {_yellow(dst):18s}  "
                  f"{_bar(ratio, 12)}  {count:4d} flows  ~{_fmt_bytes(est_bytes)}")

    # ── Top destination ports ─────────────────────────────────────────────────
    if ports:
        print(_bold("\n  Top Destination Ports"))
        top_ports = sorted(ports.items(), key=lambda x: x[1], reverse=True)[:_TOP_N]
        total_flows = sum(ports.values()) or 1
        max_p = top_ports[0][1]
        for port, count in top_ports:
            name  = _PORT_NAMES.get(port, "")
            label = f"{port}" + (f" ({name})" if name else "")
            ratio = count / max_p
            pct   = count * 100 / total_flows
            print(f"  {label:22s}  {_bar(ratio, 16)}  {count:5d} flows  {pct:4.1f}%")

    # ── Protocol breakdown ────────────────────────────────────────────────────
    if protos:
        print(_bold("\n  Protocol Breakdown"))
        total = sum(protos.values()) or 1
        for proto, count in sorted(protos.items(), key=lambda x: x[1], reverse=True):
            pct = count * 100 / total
            print(f"  {proto:8s}  {_bar(pct/100, 16)}  {count:5d} flows  {pct:4.1f}%")

    # ── Per-device flow count + anomaly detection ─────────────────────────────
    if dev_flow:
        print(_bold("\n  Flow Rate per Device"))
        values    = list(dev_flow.values())
        mean      = sum(values) / len(values)
        threshold = mean * 3.0      # flag devices with >3× mean flow count
        top_dev   = sorted(dev_flow.items(), key=lambda x: x[1], reverse=True)[:_TOP_N]
        max_dv    = top_dev[0][1] if top_dev else 1
        any_anomaly = False
        for ip, count in top_dev:
            ratio    = count / max_dv
            rate_str = f"{count/elapsed:.1f} flows/s"
            anomaly  = count > threshold and count > mean + 5
            flag     = _red("  ⚠ ANOMALY") if anomaly else ""
            if anomaly: any_anomaly = True
            print(f"  {_cyan(ip):20s}  {_bar(ratio)}  {count:5d} flows  {rate_str}{flag}")
        if any_anomaly:
            print(_red(f"\n  ⚠  Anomaly threshold: {threshold:.0f} flows "
                       f"(mean={mean:.0f}, 3× rule). Possible scan or DDoS source."))

    print(f"\n{sep}\n")


# ── Verbose datagram printer ──────────────────────────────────────────────────

def print_datagram(dgram: dict, show_counters: bool, show_flows: bool):
    ts    = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    agent = dgram.get("agent_ip", "?")
    seq   = dgram.get("seq", 0)
    n     = len(dgram.get("samples", []))
    print(_bold(f"[{ts}]") + f" agent={_cyan(agent)}  seq={seq}  samples={n}")
    for s in dgram.get("samples", []):
        if s["type"] == "counter" and show_counters:
            _print_counter(s)
        elif s["type"] == "flow" and show_flows:
            _print_flow(s)
    print()

def _print_counter(s):
    print(f"  {_green('COUNTER')} seq={s['seq']}  source_id={s['source_id']}")
    for r in s.get("records", []):
        if r.get("kind") == "generic":
            a = _green("up") if r["admin_up"] else _yellow("down")
            o = _green("up") if r["oper_up"]  else _yellow("down")
            print(f"    ifIndex={r['if_index']}  admin={a}  oper={o}")
            print(f"      IN:  {r['in_octets']:>15,} bytes  {r['in_pkts']:>10,} pkts"
                  f"  err={r['in_errors']}  disc={r['in_discards']}")
            print(f"      OUT: {r['out_octets']:>15,} bytes  {r['out_pkts']:>10,} pkts"
                  f"  err={r['out_errors']}  disc={r['out_discards']}")

def _print_flow(s):
    print(f"  {_blue('FLOW')}    seq={s['seq']}  rate=1:{s['sample_rate']}")
    for r in s.get("records", []):
        if r.get("kind") == "raw":
            src   = r.get("ip_src", "?")
            dst   = r.get("ip_dst", "?")
            proto = r.get("ip_proto", r.get("protocol", "?"))
            sp    = r.get("src_port", "")
            dp    = r.get("dst_port", "")
            ports = f"  {sp}→{dp}" if sp else ""
            print(f"    {r['protocol']}  {_yellow(src)}→{_yellow(dst)}"
                  f"  proto={proto}{ports}  frame={r['frame_len']}B")


# ── Main loop ─────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="sFlow v5 test receiver for the Datacenter Network Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host",          default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=6343,      help="UDP port (default: 6343)")
    p.add_argument("--interval",type=int,default=30,      help="Summary interval in seconds (default: 30)")
    p.add_argument("--verbose",  action="store_true",     help="Also print individual datagrams")
    p.add_argument("--counters-only", action="store_true",help="Verbose: show only counter samples")
    p.add_argument("--flows-only",    action="store_true",help="Verbose: show only flow samples")
    return p


def main():
    args = build_parser().parse_args()
    show_counters = not args.flows_only
    show_flows    = not args.counters_only

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.host, args.port))
    except OSError as e:
        print(f"ERROR: cannot bind {args.host}:{args.port} — {e}", file=sys.stderr)
        sys.exit(1)

    agg = Aggregator()

    # ── Summary timer thread ──────────────────────────────────────────────────
    def _summary_loop():
        while True:
            time.sleep(args.interval)
            snap = agg.snapshot_and_reset()
            if snap["dgrams"] > 0:
                print_summary(snap, args.interval)

    t = threading.Thread(target=_summary_loop, daemon=True, name="summary-timer")
    t.start()

    # ── Receive loop ──────────────────────────────────────────────────────────
    print(_bold(f"sFlow v5 receiver listening on {args.host}:{args.port}"))
    print(_dim(f"Summary every {args.interval}s"
               + ("  |  verbose mode" if args.verbose else "") + "\n"))

    dgram_count = 0
    try:
        while True:
            data, _ = sock.recvfrom(65535)
            dgram   = decode_datagram(data)
            if not dgram:
                continue

            dgram_count += 1
            agg.record_datagram()

            # Feed aggregator
            for s in dgram.get("samples", []):
                if s["type"] == "counter":
                    for r in s.get("records", []):
                        if r.get("kind") == "generic":
                            agg.record_counter(
                                dgram["agent_ip"],
                                r["if_index"],
                                r["in_octets"],
                                r["out_octets"],
                            )
                elif s["type"] == "flow":
                    for r in s.get("records", []):
                        if r.get("kind") == "raw" and "ip_src" in r:
                            agg.record_flow(
                                r["ip_src"], r["ip_dst"],
                                r.get("dst_port", 0),
                                r.get("ip_proto", r.get("protocol", "?")),
                                r["frame_len"],
                            )

            # Verbose output
            if args.verbose:
                print_datagram(dgram, show_counters, show_flows)

    except KeyboardInterrupt:
        print(f"\nStopped. {dgram_count} datagram(s) received.")
        # Print final summary
        snap = agg.snapshot_and_reset()
        if snap["dgrams"] > 0:
            print_summary(snap, args.interval)
    finally:
        sock.close()


if __name__ == "__main__":
    main()

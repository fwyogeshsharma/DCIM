"""
gNMI Test Client — exercises all four gNMI RPCs against the simulator.

Two connection modes
--------------------
PROXY (single endpoint, routes by --target)
  Host : localhost   Port : 50051  (default)
  Use --target <device-ip> to address a specific device.
  The proxy must be enabled in the gNMI panel before use.

DIRECT (per-device gRPC server, no proxy needed)
  Host : <device-ip>  Port : 57400
  Connect straight to the device — omit --target entirely.
  Requires the device IP to be bound to an adapter in the simulator.

────────────────────────────────────────────────────────────
PROXY examples  (--host localhost --port 50051, defaults)
────────────────────────────────────────────────────────────

# Capabilities
python test_gNMI.py capabilities

# Get all devices (all loaded targets)
python test_gNMI.py get

# Get a specific device
python test_gNMI.py get --target 10.1.0.1 --path /interfaces
python test_gNMI.py get --target 10.1.0.1 --path /interfaces/interface[name=eth0]
python test_gNMI.py get --target 10.1.0.1 --path /system --path /lldp

# Subscribe ONCE
python test_gNMI.py subscribe --mode once --target 10.1.0.1

# Subscribe STREAM (every 5 s)
python test_gNMI.py subscribe --mode stream --target 10.1.0.1 --path /interfaces --interval 5

# Subscribe POLL
python test_gNMI.py subscribe --mode poll --target 10.1.0.1 --count 3 --poll-interval 2

# Set (accepted but discarded by the simulator)
python test_gNMI.py set --target 10.1.0.1 --path /system/config/hostname --value '"new-hostname"'

────────────────────────────────────────────────────────────
DIRECT examples  (--host <device-ip> --port 57400, no --target)
────────────────────────────────────────────────────────────

# Capabilities
python test_gNMI.py --host 10.1.0.1 --port 57400 capabilities

# Get all data from the device
python test_gNMI.py --host 10.1.0.1 --port 57400 get

# Get specific paths
python test_gNMI.py --host 10.1.0.1 --port 57400 get --path /interfaces
python test_gNMI.py --host 10.1.0.1 --port 57400 get --path /interfaces/interface[name=eth0]
python test_gNMI.py --host 10.1.0.1 --port 57400 get --path /system --path /lldp

# Subscribe ONCE
python test_gNMI.py --host 10.1.0.1 --port 57400 subscribe --mode once

# Subscribe STREAM (every 5 s)
python test_gNMI.py --host 10.1.0.1 --port 57400 subscribe --mode stream --path /interfaces --interval 5

# Subscribe POLL
python test_gNMI.py --host 10.1.0.1 --port 57400 subscribe --mode poll --count 3 --poll-interval 2

# Set
python test_gNMI.py --host 10.1.0.1 --port 57400 set --path /system/config/hostname --value '"new-hostname"'
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

# ── Add compiled proto stubs to path ─────────────────────────────────────────

_HERE = Path(__file__).parent
_COMPILED = _HERE / "proto" / "compiled"
if str(_COMPILED) not in sys.path:
    sys.path.insert(0, str(_COMPILED))

try:
    import gnmi_pb2
    import gnmi_pb2_grpc
except ImportError as e:
    print(f"ERROR: Cannot import gNMI proto stubs from {_COMPILED}")
    print(f"  {e}")
    print("  Run:  python compile_protos.py   (or:  pip install grpcio-tools)")
    sys.exit(1)

try:
    import grpc
except ImportError:
    print("ERROR: grpcio not installed.  Run:  pip install grpcio")
    sys.exit(1)


# ── Path string → gnmi_pb2.Path ───────────────────────────────────────────────

def _parse_path(path_str: str) -> gnmi_pb2.Path:
    """
    Convert a path string like  /interfaces/interface[name=eth0]/config
    into a gnmi_pb2.Path with PathElems.
    """
    if not path_str or path_str == "/":
        return gnmi_pb2.Path()

    parts = [p for p in path_str.split("/") if p]
    elems = []
    for part in parts:
        # Check for key predicates: name[key1=val1][key2=val2]
        m = re.match(r'^([^\[]+)((?:\[[^\]]+\])+)?$', part)
        if not m:
            elems.append(gnmi_pb2.PathElem(name=part))
            continue
        name = m.group(1)
        keys = {}
        if m.group(2):
            for kv in re.findall(r'\[([^\]]+)\]', m.group(2)):
                k, _, v = kv.partition("=")
                keys[k] = v
        elems.append(gnmi_pb2.PathElem(name=name, key=keys))
    return gnmi_pb2.Path(elem=elems)


def _path_to_str(path: gnmi_pb2.Path) -> str:
    """Convert a gnmi_pb2.Path back to a human-readable string."""
    if not path.elem:
        return "/"
    parts = []
    for elem in path.elem:
        s = elem.name
        for k, v in elem.key.items():
            s += f"[{k}={v}]"
        parts.append(s)
    prefix = f"[{path.target}]" if path.target else ""
    return prefix + "/" + "/".join(parts)


# ── Value decoding ────────────────────────────────────────────────────────────

def _decode_value(tv: gnmi_pb2.TypedValue):
    """Return a Python object from a TypedValue."""
    kind = tv.WhichOneof("value")
    if kind in ("json_val", "json_ietf_val"):
        raw = getattr(tv, kind)
        try:
            return json.loads(raw)
        except Exception:
            return raw.decode("utf-8", errors="replace")
    if kind == "string_val":
        return tv.string_val
    if kind == "int_val":
        return tv.int_val
    if kind == "uint_val":
        return tv.uint_val
    if kind == "bool_val":
        return tv.bool_val
    if kind == "float_val":
        return tv.float_val
    if kind == "bytes_val":
        return f"<bytes len={len(tv.bytes_val)}>"
    return f"<{kind}>"


def _pprint(obj, indent: int = 2):
    """Pretty-print a Python object."""
    print(json.dumps(obj, indent=indent, default=str, ensure_ascii=False))


# ── Channel helper ────────────────────────────────────────────────────────────

def _channel(host: str, port: int) -> grpc.Channel:
    return grpc.insecure_channel(f"{host}:{port}")


# ── RPC helpers ───────────────────────────────────────────────────────────────

def cmd_capabilities(stub, args):
    print(f"→ Capabilities  [{args.host}:{args.port}]")
    resp = stub.Capabilities(gnmi_pb2.CapabilityRequest())
    print(f"\ngNMI version : {resp.gNMI_version}")
    print(f"Encodings    : {[gnmi_pb2.Encoding.Name(e) for e in resp.supported_encodings]}")
    print(f"\nSupported models ({len(resp.supported_models)}):")
    for m in resp.supported_models:
        print(f"  {m.name:<45}  v{m.version:<10}  {m.organization}")


def cmd_get(stub, args):
    paths = args.path or []
    target = args.target or ""
    print(f"→ Get  target={target or '(all)'}  paths={paths or ['/']}  [{args.host}:{args.port}]")

    prefix = gnmi_pb2.Path(target=target) if target else gnmi_pb2.Path()
    gnmi_paths = [_parse_path(p) for p in paths] if paths else [gnmi_pb2.Path()]

    req = gnmi_pb2.GetRequest(
        prefix=prefix,
        path=gnmi_paths,
        type=gnmi_pb2.GetRequest.ALL,
        encoding=gnmi_pb2.Encoding.Value("JSON_IETF"),
    )
    resp = stub.Get(req)

    if not resp.notification:
        print("  (no data returned)")
        return

    for notif in resp.notification:
        tgt = notif.prefix.target if notif.prefix.ByteSize() > 0 else "(unknown)"
        ts_s = notif.timestamp / 1e9
        print(f"\n── Target: {tgt}  (ts={ts_s:.3f}) ──")
        for upd in notif.update:
            path_str = _path_to_str(upd.path)
            val = _decode_value(upd.val)
            print(f"\n  Path: {path_str}")
            if isinstance(val, (dict, list)):
                _pprint(val)
            else:
                print(f"  Value: {val}")


def cmd_subscribe(stub, args):
    mode_map = {
        "once":   gnmi_pb2.SubscriptionList.ONCE,
        "stream": gnmi_pb2.SubscriptionList.STREAM,
        "poll":   gnmi_pb2.SubscriptionList.POLL,
    }
    mode_str = (args.mode or "once").lower()
    mode = mode_map.get(mode_str, gnmi_pb2.SubscriptionList.ONCE)

    paths = args.path or ["/"]
    target = args.target or ""
    interval_ns = int((args.interval or 30) * 1e9)

    print(f"→ Subscribe  mode={mode_str.upper()}  target={target or '(all)'}  "
          f"paths={paths}  [{args.host}:{args.port}]")
    if mode_str == "stream":
        print(f"  interval={args.interval or 30}s   (Ctrl-C to stop)")
    if mode_str == "poll":
        print(f"  polls={args.count or 3}  poll-interval={args.poll_interval or 2}s")

    prefix = gnmi_pb2.Path(target=target) if target else gnmi_pb2.Path()
    subscriptions = [
        gnmi_pb2.Subscription(
            path=_parse_path(p),
            mode=gnmi_pb2.SubscriptionMode.Value("SAMPLE"),
            sample_interval=interval_ns,
        )
        for p in paths
    ]
    sub_list = gnmi_pb2.SubscriptionList(
        prefix=prefix,
        subscription=subscriptions,
        mode=mode,
        encoding=gnmi_pb2.Encoding.Value("JSON_IETF"),
    )

    def _request_iter():
        yield gnmi_pb2.SubscribeRequest(subscribe=sub_list)
        if mode == gnmi_pb2.SubscriptionList.POLL:
            count = args.count or 3
            poll_delay = args.poll_interval or 2
            for i in range(count):
                time.sleep(poll_delay)
                print(f"\n  [poll #{i + 1}]")
                yield gnmi_pb2.SubscribeRequest(poll=gnmi_pb2.Poll())

    msg_count = 0
    try:
        for resp in stub.Subscribe(_request_iter()):
            if resp.HasField("sync_response") and resp.sync_response:
                print("  ── sync_response (snapshot complete) ──")
                if mode_str == "once":
                    break
                continue

            if resp.HasField("update"):
                notif = resp.update
                tgt = notif.prefix.target if notif.prefix.ByteSize() > 0 else "(unknown)"
                ts_s = notif.timestamp / 1e9
                msg_count += 1
                print(f"\n[msg #{msg_count}] target={tgt}  ts={ts_s:.3f}")
                for upd in notif.update:
                    path_str = _path_to_str(upd.path)
                    val = _decode_value(upd.val)
                    print(f"  Path: {path_str}")
                    if isinstance(val, (dict, list)):
                        # For stream mode print a compact summary to avoid flooding
                        if mode_str == "stream" and isinstance(val, dict):
                            keys = list(val.keys())[:6]
                            preview = {k: val[k] for k in keys}
                            if len(val) > 6:
                                preview["..."] = f"({len(val) - 6} more keys)"
                            _pprint(preview)
                        else:
                            _pprint(val)
                    else:
                        print(f"  Value: {val}")
    except KeyboardInterrupt:
        print("\n  (interrupted)")
    except grpc.RpcError as e:
        print(f"\nRPC error: {e.code()} — {e.details()}")


def cmd_set(stub, args):
    target = args.target or ""
    path_str = args.path[0] if args.path else "/"
    raw_value = args.value or "null"

    print(f"→ Set  target={target}  path={path_str}  value={raw_value}  [{args.host}:{args.port}]")

    try:
        val_obj = json.loads(raw_value)
    except json.JSONDecodeError:
        val_obj = raw_value  # treat as plain string

    encoded = json.dumps(val_obj).encode("utf-8")
    tv = gnmi_pb2.TypedValue(json_ietf_val=encoded)
    upd = gnmi_pb2.Update(path=_parse_path(path_str), val=tv)

    prefix = gnmi_pb2.Path(target=target) if target else gnmi_pb2.Path()
    req = gnmi_pb2.SetRequest(prefix=prefix, update=[upd])

    resp = stub.Set(req)
    ts_s = resp.timestamp / 1e9
    print(f"\n  SetResponse  ts={ts_s:.3f}")
    for r in resp.response:
        op_name = gnmi_pb2.UpdateResult.Operation.Name(r.op)
        print(f"  {op_name:<10} {_path_to_str(r.path)}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="gNMI test client for the Datacenter Network Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--host", default="localhost", help="gNMI server host (default: localhost)")
    p.add_argument("--port", type=int, default=50051,  help="gNMI server port (default: 50051)")

    sub = p.add_subparsers(dest="command", required=True)

    # capabilities
    sub.add_parser("capabilities", aliases=["cap", "caps"],
                   help="Fetch server capabilities")

    # get
    g = sub.add_parser("get", help="Get data for one or more paths")
    g.add_argument("--target", "-t", help="Device IP / target name")
    g.add_argument("--path",   "-p", action="append",
                   help="gNMI path (may repeat). e.g. /interfaces")

    # subscribe
    s = sub.add_parser("subscribe", aliases=["sub"],
                       help="Subscribe to telemetry updates")
    s.add_argument("--target",   "-t", help="Device IP / target name")
    s.add_argument("--path",     "-p", action="append",
                   help="gNMI path (may repeat)")
    s.add_argument("--mode",     "-m", default="once",
                   choices=["once", "stream", "poll"],
                   help="Subscription mode (default: once)")
    s.add_argument("--interval", "-i", type=float, default=30,
                   help="STREAM sample interval in seconds (default: 30)")
    s.add_argument("--count",    "-n", type=int, default=3,
                   help="POLL: number of polls to send (default: 3)")
    s.add_argument("--poll-interval", type=float, default=2,
                   help="POLL: seconds between polls (default: 2)")

    # set
    w = sub.add_parser("set", help="Set a value (simulator acks but discards)")
    w.add_argument("--target", "-t", required=True, help="Device IP / target name")
    w.add_argument("--path",   "-p", required=True, action="append",
                   help="gNMI path to update")
    w.add_argument("--value",  "-v", required=True,
                   help='JSON-encoded value, e.g.  \'"new-hostname"\'  or  \'{"mtu":9000}\'')

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    channel = _channel(args.host, args.port)
    stub = gnmi_pb2_grpc.gNMIStub(channel)

    cmd = args.command
    try:
        if cmd in ("capabilities", "cap", "caps"):
            cmd_capabilities(stub, args)
        elif cmd == "get":
            cmd_get(stub, args)
        elif cmd in ("subscribe", "sub"):
            cmd_subscribe(stub, args)
        elif cmd == "set":
            cmd_set(stub, args)
        else:
            parser.print_help()
    except grpc.RpcError as e:
        print(f"\nRPC error [{e.code()}]: {e.details()}")
        print(f"Is the gNMI server running on {args.host}:{args.port}?")
        sys.exit(1)
    finally:
        channel.close()


if __name__ == "__main__":
    main()
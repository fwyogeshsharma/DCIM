"""
gNMI gRPC Server — serves simulated OpenConfig data for switches and routers.

Routing:  The gNMI 'target' field in the path prefix maps to a device IP.
          e.g.  target="10.1.0.2"  →  loads  datasets/10.1.0.2.gnmi.json

Supported RPCs:
    Capabilities — returns supported OpenConfig models + gNMI version
    Get          — returns JSON-IETF encoded data for requested path(s)
    Subscribe    — ONCE (snapshot), POLL (on-demand), STREAM (periodic telemetry)
    Set          — accepted but discarded (simulated ack)
"""
from __future__ import annotations

import copy
import json
import os
import random
import sys
import threading
import time
import logging
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

log = logging.getLogger(__name__)


# ── Lazy proto import ─────────────────────────────────────────────────────────

def _import_stubs():
    """Import compiled gNMI proto stubs, adding compiled/ dir to sys.path."""
    compiled = str(Path(__file__).parent.parent / "proto" / "compiled")
    if compiled not in sys.path:
        sys.path.insert(0, compiled)
    import gnmi_pb2
    import gnmi_pb2_grpc
    return gnmi_pb2, gnmi_pb2_grpc


# ── OpenConfig model catalogue ────────────────────────────────────────────────

_COMMON_MODELS = [
    ("openconfig-interfaces",       "OpenConfig working group", "2.4.3"),
    ("openconfig-lldp",             "OpenConfig working group", "0.2.1"),
    ("openconfig-system",           "OpenConfig working group", "0.11.1"),
    ("openconfig-network-instance", "OpenConfig working group", "0.13.0"),
]
_SWITCH_MODELS = [
    ("openconfig-vlan",             "OpenConfig working group", "3.2.0"),
]
_ROUTER_MODELS = [
    ("openconfig-bgp",              "OpenConfig working group", "7.0.0"),
    ("openconfig-ospfv2",           "OpenConfig working group", "0.4.1"),
    ("openconfig-aft",              "OpenConfig working group", "1.0.0"),
    ("openconfig-if-ip",            "OpenConfig working group", "3.1.0"),
]

_GNMI_VERSION = "0.9.1"


# ── Path extraction helpers ───────────────────────────────────────────────────

def _strip_prefix(name: str) -> str:
    """Strip 'module:' prefix from a path element name."""
    return name.split(":", 1)[1] if ":" in name else name


def _extract_path(data: dict, elems: list) -> Optional[object]:
    """
    Traverse *data* following gNMI path elements.
    Handles module-prefixed names and list key matching.
    Returns the sub-tree at the given path, or None if not found.
    """
    current = data
    for elem in elems:
        name = _strip_prefix(elem.name)
        keys = {k: v for k, v in elem.key.items()}

        if not isinstance(current, dict):
            return None

        # Try plain name, then try stripping prefixes from dict keys
        if name in current:
            val = current[name]
        else:
            match = None
            for k in current:
                if _strip_prefix(k) == name:
                    match = k
                    break
            if match is None:
                return None
            val = current[match]

        if keys and isinstance(val, list):
            # Find first list element that matches all keys
            found = None
            for item in val:
                if not isinstance(item, dict):
                    continue
                if all(str(item.get(k)) == v or str(item.get(_strip_prefix(k))) == v
                       for k, v in keys.items()):
                    found = item
                    break
            if found is None:
                return None
            current = found
        else:
            current = val

    return current


# ── Live-value injection ──────────────────────────────────────────────────────

def _inject_live_values(subtree):
    """
    Return a deep copy of *subtree* with dynamic telemetry fields updated:
      - system state  : uptime recomputed from boot-time
      - memory state  : reserved/free/utilized varied ±5 %
      - cpu state     : instant/avg/min/max varied ±10 pp
      - if counters   : in/out octets and packets incremented
    """
    if not isinstance(subtree, (dict, list)):
        return subtree
    node = copy.deepcopy(subtree)
    _walk_inject(node)
    return node


def _walk_inject(node):
    if isinstance(node, list):
        for item in node:
            _walk_inject(item)
        return
    if not isinstance(node, dict):
        return

    # ── System state: recompute uptime from the fixed boot-time ──────────
    if "uptime" in node and "boot-time" in node:
        try:
            boot_ns = int(node["boot-time"])
            node["uptime"] = max(0, (int(time.time() * 1e9) - boot_ns) // 10_000_000)
        except (ValueError, TypeError):
            pass

    # ── Memory state: vary usage ±5 % of total physical ──────────────────
    if {"physical", "reserved", "free", "utilized"}.issubset(node):
        try:
            physical = int(node["physical"])
            base_used = int(node["reserved"])
            swing = max(1, physical // 20)          # 5 % of total
            used = max(0, min(physical, base_used + random.randint(-swing, swing)))
            node["reserved"]  = str(used)
            node["free"]      = str(physical - used)
            node["utilized"]  = round(used / max(1, physical) * 100, 1)
        except (ValueError, TypeError):
            pass

    # ── CPU state: integer instant/avg/min/max (no alarm-status) ────────
    if {"instant", "avg", "min", "max"}.issubset(node) and "alarm-status" not in node:
        try:
            base    = int(node["instant"])
            instant = max(0, min(100, base + random.randint(-10, 10)))
            node["instant"] = instant
            node["avg"]     = max(0, min(100, base + random.randint(-5, 5)))
            node["min"]     = max(0, instant - random.randint(0, 10))
            node["max"]     = min(100, instant + random.randint(0, 10))
        except (ValueError, TypeError):
            pass

    # ── Temperature: float instant/avg/min/max WITH alarm-status ─────────
    if {"instant", "avg", "min", "max", "alarm-status"}.issubset(node):
        try:
            base      = float(node["instant"])
            instant   = round(max(18.0, min(99.0, base + random.uniform(-1.0, 1.0))), 1)
            threshold = float(node.get("alarm-threshold", 75))
            node["instant"]      = instant
            node["avg"]          = round(max(18.0, min(99.0, base + random.uniform(-0.5, 0.5))), 1)
            node["min"]          = round(max(18.0, instant - random.uniform(1, 5)), 1)
            node["max"]          = round(min(99.0, instant + random.uniform(1, 5)), 1)
            node["alarm-status"] = instant >= threshold
        except (ValueError, TypeError):
            pass

    # ── Interface counters: add random increments each tick ──────────────
    if "in-octets" in node and "out-octets" in node:
        try:
            node["in-octets"]        = str(int(node["in-octets"])  + random.randint(5_000, 150_000))
            node["out-octets"]       = str(int(node["out-octets"]) + random.randint(5_000, 150_000))
            node["in-unicast-pkts"]  = str(int(node.get("in-unicast-pkts",  "0")) + random.randint(3, 100))
            node["out-unicast-pkts"] = str(int(node.get("out-unicast-pkts", "0")) + random.randint(3, 100))
            node["in-errors"]        = str(int(node.get("in-errors",  "0")) + random.randint(0, 2))
            node["out-errors"]       = str(int(node.get("out-errors", "0")) + random.randint(0, 1))
            node["in-discards"]      = str(int(node.get("in-discards",  "0")) + random.randint(0, 2))
            node["out-discards"]     = str(int(node.get("out-discards", "0")) + random.randint(0, 3))
        except (ValueError, TypeError):
            pass

    for v in node.values():
        _walk_inject(v)


# ── Store-aware value application ────────────────────────────────────────────

def _apply_store_metrics(subtree, metrics: dict):
    """
    Deep-copy *subtree* and overlay live values sourced from the shared
    DeviceStateStore rather than generating fresh random numbers each call.
    Both SNMP and gNMI will therefore return the same values for the same tick.
    """
    if not isinstance(subtree, (dict, list)):
        return subtree
    node = copy.deepcopy(subtree)
    _walk_apply(node, metrics)
    return node


def _walk_apply(node, metrics: dict, _iface: str = ""):
    """Recursively overlay store metrics onto *node* (mutates in-place)."""
    if isinstance(node, list):
        for item in node:
            # Track interface name so counter lookups can find the right entry.
            name = item.get("name", _iface) if isinstance(item, dict) else _iface
            _walk_apply(item, metrics, name)
        return
    if not isinstance(node, dict):
        return

    # Inherit interface name from this node if present.
    if isinstance(node.get("name"), str):
        _iface = node["name"]

    # ── system/state: uptime (real-time from cached boot-time) ───────────
    if "uptime" in node and "boot-time" in node:
        boot_ns = metrics.get("boot_time_ns", int(node.get("boot-time", 0)))
        node["boot-time"] = boot_ns
        node["uptime"]    = max(0, (int(time.time() * 1e9) - boot_ns) // 10_000_000)

    # ── system/memory/state ──────────────────────────────────────────────
    if {"physical", "reserved", "free", "utilized"}.issubset(node):
        total = metrics.get("memory_total", int(node.get("physical", 0) or 0))
        used  = metrics.get("memory_used",  int(node.get("reserved",  0) or 0))
        node["physical"]  = str(total)
        node["reserved"]  = str(used)
        node["free"]      = str(max(0, total - used))
        node["utilized"]  = round(used / max(1, total) * 100, 1)

    # ── system/cpus/cpu/state/total (integer, no alarm-status) ──────────
    if {"instant", "avg", "min", "max"}.issubset(node) and "alarm-status" not in node:
        cpu = int(metrics.get("cpu_usage", node.get("instant", 0)))
        node["instant"] = cpu
        node["avg"]     = max(0, min(100, cpu + random.randint(-2, 2)))
        node["min"]     = max(0, cpu - random.randint(0, 5))
        node["max"]     = min(100, cpu + random.randint(0, 5))

    # ── platform temperature (float instant/avg/min/max WITH alarm-status)
    if {"instant", "avg", "min", "max", "alarm-status"}.issubset(node):
        # Use component name context (_iface tracks "CPU" or "CHASSIS")
        temp      = float(metrics.get("cpu_temp" if _iface == "CPU" else "inlet_temp",
                                      node.get("instant", 45.0)))
        threshold = float(node.get("alarm-threshold", 75))
        node["instant"]      = round(temp + random.uniform(-0.5, 0.5), 1)
        node["avg"]          = round(temp, 1)
        node["min"]          = round(max(18.0, temp - random.uniform(2, 8)), 1)
        node["max"]          = round(min(99.0, temp + random.uniform(1, 6)), 1)
        node["alarm-status"] = node["instant"] >= threshold

    # ── interface/state/counters ─────────────────────────────────────────
    if "in-octets" in node and "out-octets" in node:
        iface_data = metrics.get("interfaces", {}).get(_iface, {})
        if iface_data:
            node["in-octets"]        = str(iface_data.get("in_octets",        int(node["in-octets"])))
            node["out-octets"]       = str(iface_data.get("out_octets",       int(node["out-octets"])))
            node["in-unicast-pkts"]  = str(iface_data.get("in_unicast_pkts",  int(node.get("in-unicast-pkts",  "0"))))
            node["out-unicast-pkts"] = str(iface_data.get("out_unicast_pkts", int(node.get("out-unicast-pkts", "0"))))
            node["in-errors"]        = str(iface_data.get("in_errors",        int(node.get("in-errors",        "0"))))
            node["out-errors"]       = str(iface_data.get("out_errors",       int(node.get("out-errors",        "0"))))
            node["in-discards"]      = str(iface_data.get("in_discards",      int(node.get("in-discards",      "0"))))
            node["out-discards"]     = str(iface_data.get("out_discards",     int(node.get("out-discards",     "0"))))
        else:
            # Interface not in store (unknown name) — fall back to random increment.
            node["in-octets"]  = str(int(node["in-octets"])  + random.randint(5_000, 150_000))
            node["out-octets"] = str(int(node["out-octets"]) + random.randint(5_000, 150_000))

    for v in node.values():
        if isinstance(v, (dict, list)):
            _walk_apply(v, metrics, _iface)


# ── Path → string helper ─────────────────────────────────────────────────────

def _peer_str(raw: str) -> str:
    """Parse the gRPC context.peer() string into a clean host:port string.

    gRPC formats it as ``ipv4:1.2.3.4:5678`` or ``ipv6:%5B::1%5D:5678``
    (brackets URL-encoded).  We strip the protocol prefix and URL-decode.
    """
    import urllib.parse
    if raw.startswith(("ipv4:", "ipv6:")):
        raw = raw.split(":", 1)[1]
    return urllib.parse.unquote(raw)


def _path_elem_str(path) -> str:
    """Convert a gnmi_pb2.Path to a human-readable string like '/interfaces/interface[name=eth0]'."""
    try:
        if not path or not path.elem:
            return "/"
        parts = []
        for elem in path.elem:
            s = elem.name
            if elem.key:
                keys = ",".join(f"{k}={v}" for k, v in sorted(elem.key.items()))
                s = f"{s}[{keys}]"
            parts.append(s)
        return "/" + "/".join(parts)
    except Exception:
        return "/"


# ── gNMI Servicer ─────────────────────────────────────────────────────────────

class GNMIServicer:
    """
    Implements the four gNMI RPCs backed by pre-generated .gnmi.json files.
    """

    def __init__(self, datasets_dir: str):
        self._datasets_dir = str(Path(datasets_dir).resolve())
        # {ip: dict} — in-memory cache of device data
        self._data: Dict[str, dict] = {}
        self._lock = threading.RLock()
        # Optional shared state store — when set, live metrics are read from
        # here instead of using random injection, so SNMP and gNMI agree.
        self._store = None
        # Connected Subscribe clients: {context_id: info_dict}
        self._clients: Dict[int, dict] = {}
        self._clients_lock = threading.Lock()

    def set_state_store(self, store):
        """Attach the shared DeviceStateStore.  Pass None to detach."""
        self._store = store

    def get_clients(self) -> list:
        """Return a snapshot list of currently connected Subscribe clients."""
        with self._clients_lock:
            return list(self._clients.values())

    # ------------------------------------------------------------------ #
    #  Data management                                                     #
    # ------------------------------------------------------------------ #

    def load_all(self):
        """Load every *.gnmi.json file in the datasets directory."""
        loaded = 0
        for p in Path(self._datasets_dir).glob("*.gnmi.json"):
            ip = p.stem.replace(".gnmi", "")  # strip extension parts
            # stem of "10.1.0.2.gnmi.json" is "10.1.0.2.gnmi" → strip ".gnmi"
            if ip.endswith(".gnmi"):
                ip = ip[:-5]
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                with self._lock:
                    self._data[ip] = data
                loaded += 1
            except Exception as exc:
                log.warning("Could not load %s: %s", p, exc)
        log.info("gNMI servicer loaded %d device(s)", loaded)
        return loaded

    def load_device(self, ip: str) -> bool:
        """Load a single device's .gnmi.json file (used by per-device servers)."""
        path = Path(self._datasets_dir) / f"{ip}.gnmi.json"
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._data[ip] = data
            return True
        except Exception as exc:
            log.warning("Could not load %s: %s", path, exc)
            return False

    def reload_device(self, ip: str) -> bool:
        """Hot-reload a single device's data file."""
        path = Path(self._datasets_dir) / f"{ip}.gnmi.json"
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._data[ip] = data
            return True
        except Exception as exc:
            log.warning("Hot-reload failed for %s: %s", ip, exc)
            return False

    def get_targets(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def target_counts(self) -> dict:
        """Return {'switch': n, 'router': n} counts."""
        counts = {"switch": 0, "router": 0, "other": 0}
        with self._lock:
            for data in self._data.values():
                dt = data.get("device_type", "other")
                counts[dt] = counts.get(dt, 0) + 1
        return counts

    def _get_data(self, target: str) -> Optional[dict]:
        with self._lock:
            return self._data.get(target)

    # ------------------------------------------------------------------ #
    #  gNMI RPCs                                                          #
    # ------------------------------------------------------------------ #

    def Capabilities(self, request, context):
        gnmi_pb2, _ = _import_stubs()
        models = []
        for name, org, ver in _COMMON_MODELS + _SWITCH_MODELS + _ROUTER_MODELS:
            models.append(gnmi_pb2.ModelData(name=name, organization=org, version=ver))
        return gnmi_pb2.CapabilityResponse(
            supported_models=models,
            supported_encodings=[
                gnmi_pb2.Encoding.Value("JSON_IETF"),
                gnmi_pb2.Encoding.Value("JSON"),
            ],
            gNMI_version=_GNMI_VERSION,
        )

    def Get(self, request, context):
        gnmi_pb2, _ = _import_stubs()
        prefix  = request.prefix
        target  = prefix.target if prefix.ByteSize() > 0 else ""
        ts      = int(time.time() * 1e9)
        notifications = []

        # If no explicit target try to serve from any loaded data
        targets_to_query = [target] if target else list(self._data.keys())

        for tgt in targets_to_query:
            data = self._get_data(tgt)
            if data is None:
                continue
            updates = []
            paths = list(request.path) if request.path else [gnmi_pb2.Path()]
            for path in paths:
                subtree = _extract_path(data, list(path.elem)) if path.elem else data
                if subtree is None:
                    continue
                subtree = self._overlay(subtree, tgt)
                encoded = json.dumps(subtree, ensure_ascii=False).encode("utf-8")
                tv = gnmi_pb2.TypedValue(json_ietf_val=encoded)
                updates.append(gnmi_pb2.Update(path=path, val=tv))

            if updates:
                pfx = gnmi_pb2.Path(target=tgt)
                notifications.append(gnmi_pb2.Notification(
                    timestamp=ts, prefix=pfx, update=updates
                ))

        return gnmi_pb2.GetResponse(notification=notifications)

    def Set(self, request, context):
        """Accept Set but discard — simulated acknowledgement."""
        gnmi_pb2, _ = _import_stubs()
        ts = int(time.time() * 1e9)
        results = []
        for upd in list(request.update) + list(request.replace):
            results.append(gnmi_pb2.UpdateResult(
                path=upd.path,
                op=gnmi_pb2.UpdateResult.Operation.Value("UPDATE"),
            ))
        for path in request.delete:
            results.append(gnmi_pb2.UpdateResult(
                path=path,
                op=gnmi_pb2.UpdateResult.Operation.Value("DELETE"),
            ))
        return gnmi_pb2.SetResponse(
            prefix=request.prefix,
            response=results,
            timestamp=ts,
        )

    def Subscribe(self, request_iterator, context) -> Iterator:
        """
        Bidirectional streaming Subscribe RPC.

        Modes
        -----
        ONCE  — send full snapshot + sync marker, then close.
        POLL  — send snapshot + sync on each Poll trigger from client.
        STREAM — send initial snapshot + sync, then push periodic updates.
        """
        gnmi_pb2, _ = _import_stubs()

        try:
            first_req = next(request_iterator)
        except StopIteration:
            return

        if first_req.WhichOneof("request") != "subscribe":
            return

        sub_list = first_req.subscribe
        target   = sub_list.prefix.target if sub_list.prefix.ByteSize() > 0 else ""
        mode     = sub_list.mode  # STREAM=0, ONCE=1, POLL=2

        # ── Register client (skip if this call was forwarded by the proxy) ──
        # The proxy tracks the real client itself and sends x-gnmi-proxy=1
        # to prevent duplicate entries in the Connected Clients table.
        _is_proxy_forward = any(
            k == "x-gnmi-proxy"
            for k, _v in (context.invocation_metadata() or [])
        )

        _MODE_NAMES = {0: "STREAM", 1: "ONCE", 2: "POLL"}
        peer = _peer_str(context.peer() or "")

        paths = [_path_elem_str(sub.path) for sub in sub_list.subscription] or ["/"]
        client_info = {
            "peer":         peer or "unknown",
            "mode":         _MODE_NAMES.get(mode, str(mode)),
            "target":       target or "(all)",
            "paths":        paths,
            "connected_at": time.time(),
            "push_count":   0,
        }
        cid = id(context)
        if not _is_proxy_forward:
            with self._clients_lock:
                self._clients[cid] = client_info

        try:
            # ── Initial snapshot ──────────────────────────────────────────
            yield from self._snapshot_responses(sub_list, target, gnmi_pb2)
            yield gnmi_pb2.SubscribeResponse(sync_response=True)
            client_info["push_count"] += 1

            if mode == gnmi_pb2.SubscriptionList.ONCE:
                return

            if mode == gnmi_pb2.SubscriptionList.POLL:
                for req in request_iterator:
                    if not context.is_active():
                        break
                    if req.WhichOneof("request") == "poll":
                        yield from self._snapshot_responses(sub_list, target, gnmi_pb2)
                        yield gnmi_pb2.SubscribeResponse(sync_response=True)
                        client_info["push_count"] += 1
                return

            # ── STREAM mode ───────────────────────────────────────────────
            interval = 30.0
            for sub in sub_list.subscription:
                if sub.sample_interval > 0:
                    interval = sub.sample_interval / 1e9

            tick    = 0.5
            elapsed = 0.0
            while context.is_active():
                time.sleep(tick)
                elapsed += tick
                if elapsed >= interval:
                    elapsed = 0.0
                    if not context.is_active():
                        break
                    yield from self._snapshot_responses(sub_list, target, gnmi_pb2)
                    client_info["push_count"] += 1

        finally:
            # ── Deregister client (disconnect / error / ONCE done) ────────
            if not _is_proxy_forward:
                with self._clients_lock:
                    self._clients.pop(cid, None)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _overlay(self, subtree, ip: str):
        """
        Apply live metrics to *subtree*.
        Uses the shared DeviceStateStore when attached (giving SNMP/gNMI the
        same values), otherwise falls back to per-request random injection.
        """
        if self._store is not None:
            metrics = self._store.get_metrics(ip)
            if metrics is not None:
                return _apply_store_metrics(subtree, metrics)
        return _inject_live_values(subtree)

    def _snapshot_responses(self, sub_list, target: str, gnmi_pb2) -> Iterator:
        """
        Build SubscribeResponse(update=Notification) messages for all
        requested paths and the given target.
        """
        ts = int(time.time() * 1e9)
        targets = [target] if target else list(self._data.keys())

        for tgt in targets:
            data = self._get_data(tgt)
            if data is None:
                continue

            paths = [sub.path for sub in sub_list.subscription] \
                    if sub_list.subscription else [gnmi_pb2.Path()]

            updates = []
            for path in paths:
                subtree = _extract_path(data, list(path.elem)) if path.elem else data
                if subtree is None:
                    continue
                subtree = self._overlay(subtree, tgt)
                encoded = json.dumps(subtree, ensure_ascii=False).encode("utf-8")
                tv = gnmi_pb2.TypedValue(json_ietf_val=encoded)
                updates.append(gnmi_pb2.Update(path=path, val=tv))

            if updates:
                pfx = gnmi_pb2.Path(target=tgt)
                notification = gnmi_pb2.Notification(
                    timestamp=ts, prefix=pfx, update=updates
                )
                yield gnmi_pb2.SubscribeResponse(update=notification)


# ── gRPC server wrapper ───────────────────────────────────────────────────────

class GNMIServer:
    """
    Thin wrapper that wires GNMIServicer into a grpc.Server.
    Runs in the calling thread — use GNMIController to run in background.
    """

    def __init__(self, servicer, port: int = 50051,
                 max_workers: int = 10):
        self._servicer   = servicer
        self._port       = port
        self._max_workers = max_workers
        self._server     = None

    def start(self, bind_address: str = None) -> bool:
        """
        Start the gRPC server.

        *bind_address* overrides the default ``[::]:{port}`` binding.
        Pass e.g. ``"10.1.0.2:57400"`` for a per-device server that only
        listens on a specific virtual IP.
        """
        self.last_error: str = ""
        try:
            import grpc
            from concurrent import futures
            _, gnmi_pb2_grpc = _import_stubs()

            class _Svc(gnmi_pb2_grpc.gNMIServicer):
                def __init__(self, svc):
                    self._svc = svc
                def Capabilities(self, req, ctx):
                    return self._svc.Capabilities(req, ctx)
                def Get(self, req, ctx):
                    return self._svc.Get(req, ctx)
                def Set(self, req, ctx):
                    return self._svc.Set(req, ctx)
                def Subscribe(self, req_iter, ctx):
                    yield from self._svc.Subscribe(req_iter, ctx)

            self._server = grpc.server(
                futures.ThreadPoolExecutor(max_workers=self._max_workers),
                options=[
                    ("grpc.max_send_message_length",    50 * 1024 * 1024),
                    ("grpc.max_receive_message_length",  4 * 1024 * 1024),
                ],
            )
            gnmi_pb2_grpc.add_gNMIServicer_to_server(_Svc(self._servicer), self._server)
            addr = bind_address or f"[::]:{self._port}"
            bound = self._server.add_insecure_port(addr)
            if bound == 0:
                raise OSError(
                    f"add_insecure_port({addr!r}) returned 0 — "
                    "address unavailable or port already in use"
                )
            self._server.start()
            log.info("gNMI server listening on %s (insecure)", addr)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            log.debug("gNMI server start failed: %s", exc)
            return False

    def stop(self, grace: float = 2.0):
        if self._server:
            self._server.stop(grace)
            self._server = None
            log.info("gNMI server stopped.")

    def wait_for_termination(self):
        if self._server:
            self._server.wait_for_termination()


# ── Proxy servicer ────────────────────────────────────────────────────────────

class GNMIProxyServicer:
    """
    Proxy gNMI servicer for the aggregated endpoint (default port 50051).

    Routing logic
    -------------
    • Request carries ``prefix.target = "10.1.0.2"``  →  forwarded via gRPC
      to the dedicated per-device server at ``10.1.0.2:{device_port}``.
    • No target, or target has no registered per-device server  →  served
      locally from the shared GNMIServicer (all-device fallback).

    This mirrors a real gNMI Gateway/Proxy: clients can either connect
    directly to a device (``10.1.0.2:57400``) or use the proxy endpoint
    (``<host>:50051``) and set the target field to address any device.
    """

    def __init__(self, local_servicer: "GNMIServicer"):
        self._local = local_servicer
        # ip → grpc.Channel to the per-device server
        self._channels: Dict[str, object] = {}
        self._lock = threading.Lock()
        # Clients routed through the proxy to per-device servers
        self._proxy_clients: Dict[int, dict] = {}
        self._proxy_clients_lock = threading.Lock()

    def get_proxy_clients(self) -> list:
        """Return clients currently routed through the proxy."""
        with self._proxy_clients_lock:
            return list(self._proxy_clients.values())

    # ------------------------------------------------------------------ #
    #  Channel management                                                  #
    # ------------------------------------------------------------------ #

    def add_device(self, ip: str, port: int = 57400):
        """Register a per-device server so the proxy can route to it."""
        import grpc
        with self._lock:
            if ip not in self._channels:
                self._channels[ip] = grpc.insecure_channel(
                    f"{ip}:{port}",
                    options=[("grpc.enable_retries", 0)],
                )

    def remove_device(self, ip: str):
        with self._lock:
            ch = self._channels.pop(ip, None)
        if ch:
            try:
                ch.close()
            except Exception:
                pass

    def close_all(self):
        with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
        for ch in channels:
            try:
                ch.close()
            except Exception:
                pass

    def _stub(self, ip: str):
        """Return a gNMIStub for *ip*, or None if not a registered device."""
        with self._lock:
            ch = self._channels.get(ip)
        if ch is None:
            return None
        _, gnmi_pb2_grpc = _import_stubs()
        return gnmi_pb2_grpc.gNMIStub(ch)

    # ------------------------------------------------------------------ #
    #  gNMI RPCs                                                          #
    # ------------------------------------------------------------------ #

    def Capabilities(self, request, context):
        return self._local.Capabilities(request, context)

    def Set(self, request, context):
        return self._local.Set(request, context)

    def Get(self, request, context):
        target = request.prefix.target if request.prefix.ByteSize() > 0 else ""
        stub = self._stub(target) if target else None
        if stub:
            try:
                return stub.Get(request)
            except Exception as exc:
                log.warning("[Proxy] Get → %s failed (%s); serving locally", target, exc)
        return self._local.Get(request, context)

    def Subscribe(self, request_iterator, context):
        import itertools

        try:
            first_req = next(request_iterator)
        except StopIteration:
            return

        # Determine target / mode / paths from the first SubscribeRequest
        target = ""
        mode   = 0
        paths  = ["/"]
        if first_req.WhichOneof("request") == "subscribe":
            sub    = first_req.subscribe
            target = sub.prefix.target if sub.prefix.ByteSize() > 0 else ""
            mode   = sub.mode
            paths  = [_path_elem_str(s.path) for s in sub.subscription] or ["/"]

        stub = self._stub(target) if target else None
        # Rebuild the full request iterator (first_req already consumed)
        full_iter = itertools.chain([first_req], request_iterator)

        if stub:
            _MODE_NAMES = {0: "STREAM", 1: "ONCE", 2: "POLL"}
            peer = _peer_str(context.peer() or "")
            client_info = {
                "peer":         peer or "unknown",
                "mode":         _MODE_NAMES.get(mode, str(mode)),
                "target":       target or "(all)",
                "paths":        paths,
                "connected_at": time.time(),
                "push_count":   0,
            }
            cid = id(context)
            with self._proxy_clients_lock:
                self._proxy_clients[cid] = client_info
            try:
                # Tell per-device server this is a proxy forward so it skips
                # registering the same connection in its own client tracker.
                metadata = [("x-gnmi-proxy", "1")]
                for response in stub.Subscribe(full_iter, metadata=metadata):
                    if not context.is_active():
                        break
                    client_info["push_count"] += 1
                    yield response
                return
            except Exception as exc:
                log.warning("[Proxy] Subscribe → %s failed: %s", target, exc)
                return
            finally:
                with self._proxy_clients_lock:
                    self._proxy_clients.pop(cid, None)

        # No per-device server — serve from the shared local servicer
        yield from self._local.Subscribe(full_iter, context)

    def get_clients(self) -> list:
        """Return clients routed through the proxy + local-servicer clients."""
        clients = self.get_proxy_clients()
        clients.extend(self._local.get_clients())
        return clients
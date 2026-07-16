"""Which topology a directory of generated datasets was built from.

Datasets (SNMP .snmprec, gNMI .gnmi.json) are named by IP, so a directory can be
COMPLETE — every expected filename present — while every file inside is stale.
Re-seat a switch's ports or resize a device and the names never change. The
fingerprint is the missing half: it answers "were these built from THIS topology?"
so a restart can adopt existing datasets instead of rebuilding them, without ever
adopting datasets that describe a topology you no longer have.

Lives here rather than on a generator because it describes the TOPOLOGY, and both
the SNMP and gNMI generators stamp the same value into their own directories.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.topology_engine import TopologyEngine

# Deliberately a dotfile with no extension: the dataset globs (*.snmprec,
# *.gnmi.json) must not match it, or snmpsim would serve it as an agent and the
# orphan reaper would delete it.
FINGERPRINT_FILE = ".topology_fingerprint"


def compute(topology: "TopologyEngine") -> str:
    """Hash everything that decides dataset CONTENT.

    Covers what the generators actually read — identity, model, addresses, the
    full port list, and every ethernet termination (which drives ifTable, LLDP,
    the MAC table and gNMI's interface/neighbour state).

    EXCLUDES live telemetry (octets, cpu, uptime) and link broken-ness: the state
    store patches those into the datasets on every tick, so folding them in would
    mark a directory stale seconds after it was written and make the check
    worthless.
    """
    h = hashlib.sha256()
    devs = []
    for d in topology.get_all_devices():
        ifaces = tuple(
            (i.index, i.name, i.speed, getattr(i, "role", "data"))
            for i in getattr(d, "interfaces", [])
        )
        devs.append((
            d.name, d.device_type.value, d.vendor.value, d.model_name or "",
            d.ip_address or "", getattr(d, "mgmt_ip", "") or "",
            getattr(d, "sys_location", "") or "", ifaces,
        ))
    for t in sorted(devs, key=repr):
        h.update(repr(t).encode())
    edges = []
    for u, v, e in topology.get_links():
        edges.append((
            e.get("src_node", u), e.get("dst_node", v),
            e.get("layer", "production"), e.get("src_iface"), e.get("dst_iface"),
        ))
    for t in sorted(edges, key=repr):
        h.update(repr(t).encode())
    return h.hexdigest()[:16]


def write(output_dir, topology: "TopologyEngine") -> str:
    fp = compute(topology)
    try:
        (Path(output_dir) / FINGERPRINT_FILE).write_text(fp, encoding="utf-8")
    except OSError:
        pass          # datasets remain usable; reconcile just won't adopt them
    return fp


def read(output_dir) -> Optional[str]:
    """The topology these datasets were built from, or None if unknown.

    None means "written before fingerprinting existed, or hand-managed" — callers
    must treat that as unverifiable, NOT as a match.
    """
    try:
        v = (Path(output_dir) / FINGERPRINT_FILE).read_text(encoding="utf-8").strip()
        return v or None
    except OSError:
        return None

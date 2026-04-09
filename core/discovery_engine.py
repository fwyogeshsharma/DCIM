"""
SNMP-based topology discovery engine.
Walks LLDP-MIB on each simulated device and reconstructs the topology from
the live SNMP responses, then compares with the actual configured topology.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional, Set


# LLDP remote table OIDs (must match lldp_generator.py)
LLDP_REM_SYS_NAME    = "1.0.8802.1.1.2.1.4.1.1.9"   # neighbor hostname
LLDP_REM_CHASSIS_ID  = "1.0.8802.1.1.2.1.4.1.1.5"   # neighbor IP
LLDP_REM_PORT_ID     = "1.0.8802.1.1.2.1.4.1.1.7"   # remote port name


@dataclass
class DiscoveredLink:
    local_device: str
    local_ip: str
    local_port: int       # 0-based interface index
    remote_device: str
    remote_ip: str
    remote_port: str      # interface name on remote side
    in_actual: bool = True


@dataclass
class DiscoveryResult:
    discovered_links: List[DiscoveredLink] = field(default_factory=list)
    matched: List[Tuple[str, str]] = field(default_factory=list)
    missing: List[Tuple[str, str]] = field(default_factory=list)
    extra: List[DiscoveredLink] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    devices_scanned: int = 0


class DiscoveryEngine:
    """Queries SNMPSim via pysnmp to discover topology from LLDP neighbor tables."""

    def __init__(self, host: str = "127.0.0.1", port: int = 161):
        self.host = host
        self.port = port

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def discover(self, topology, progress_cb=None, link_cb=None,
                 device_scanned_cb=None) -> DiscoveryResult:
        """
        Walk LLDP on every device in the topology and compare the discovered
        adjacencies against the configured topology.

        progress_cb(current, total, message) — optional progress callback.
        link_cb(src_device_id, dst_device_id)  — called for each unique link
            the moment it is confirmed via SNMP (may be called from a non-main
            thread; use a Qt signal as the callback for thread safety).
        device_scanned_cb(device_id) — called once per device immediately after
            its LLDP walk completes (even if no links were found or an error
            occurred).  Use this to drive per-device progress animations.
        """
        result = DiscoveryResult()
        devices = topology.get_all_devices()
        result.devices_scanned = len(devices)

        ip_to_dev = {d.ip_address: d for d in devices}
        discovered_edges: Set[frozenset] = set()

        for i, device in enumerate(devices):
            if progress_cb:
                progress_cb(i, len(devices),
                            f"Scanning {device.name} ({device.ip_address})…")
            try:
                links = self._walk_device_lldp(device, ip_to_dev)
                for link in links:
                    result.discovered_links.append(link)
                    remote_dev = ip_to_dev.get(link.remote_ip)
                    local_dev  = ip_to_dev.get(link.local_ip)
                    if remote_dev and local_dev:
                        edge = frozenset([local_dev.id, remote_dev.id])
                        if edge not in discovered_edges:
                            discovered_edges.add(edge)
                            if link_cb:
                                link_cb(local_dev.id, remote_dev.id)
            except Exception as exc:
                result.errors.append(
                    f"{device.name} ({device.ip_address}): [{type(exc).__name__}] {exc}"
                )
            finally:
                if device_scanned_cb:
                    device_scanned_cb(device.id)

        if progress_cb:
            progress_cb(len(devices), len(devices), "Comparing with configured topology…")

        # Compare against configured topology
        actual_edges: Set[frozenset] = set(
            frozenset([u, v]) for u, v, _ in topology.get_links()
        )

        for edge in actual_edges:
            if edge in discovered_edges:
                result.matched.append(tuple(edge))
            else:
                result.missing.append(tuple(edge))

        for link in result.discovered_links:
            remote_dev = ip_to_dev.get(link.remote_ip)
            local_dev  = ip_to_dev.get(link.local_ip)
            if local_dev and remote_dev:
                link.in_actual = (frozenset([local_dev.id, remote_dev.id]) in actual_edges)
            else:
                link.in_actual = False
                result.extra.append(link)

        return result

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #

    def _walk_device_lldp(self, device, ip_to_dev: dict) -> List[DiscoveredLink]:
        community = device.ip_address  # community = IP in this simulator
        host = device.ip_address       # SNMPSim listens on each device's own IP

        raw_names  = self._walk_oid(community, LLDP_REM_SYS_NAME,   host)
        raw_ips    = self._walk_oid(community, LLDP_REM_CHASSIS_ID,  host)
        raw_ports  = self._walk_oid(community, LLDP_REM_PORT_ID,     host)

        def to_suffix_map(walk_result, base_oid):
            d: Dict[str, str] = {}
            prefix = base_oid + "."
            for oid, val in walk_result:
                if oid.startswith(prefix):
                    d[oid[len(prefix):]] = val
            return d

        names = to_suffix_map(raw_names,  LLDP_REM_SYS_NAME)
        ips   = to_suffix_map(raw_ips,    LLDP_REM_CHASSIS_ID)
        ports = to_suffix_map(raw_ports,  LLDP_REM_PORT_ID)

        links: List[DiscoveredLink] = []
        for suffix, neighbor_name in names.items():
            # suffix format: "{timeMark}.{localPort}.{remoteIdx}"
            parts = suffix.split(".")
            local_port = int(parts[1]) if len(parts) >= 3 else 0
            remote_ip  = ips.get(suffix, "unknown")
            remote_port = ports.get(suffix, "?")

            links.append(DiscoveredLink(
                local_device=device.name,
                local_ip=device.ip_address,
                local_port=local_port,
                remote_device=neighbor_name,
                remote_ip=remote_ip,
                remote_port=remote_port,
            ))

        return links

    def _walk_oid(self, community: str, oid: str, host: str = None) -> List[Tuple[str, str]]:
        import asyncio
        import sys

        target = host or self.host

        async def _run() -> List[Tuple[str, str]]:
            try:
                from pysnmp.hlapi.asyncio import (
                    SnmpEngine, CommunityData, UdpTransportTarget,
                    ContextData, ObjectType, ObjectIdentity, walkCmd,
                )
            except ImportError:
                raise RuntimeError(
                    "pysnmp is not installed. Run: "
                    "pip install --force-reinstall pysnmp-lextudio"
                )

            results: List[Tuple[str, str]] = []
            snmp_engine = SnmpEngine()
            try:
                async for err_ind, err_status, _err_idx, var_binds in walkCmd(
                    snmp_engine,
                    CommunityData(community),
                    UdpTransportTarget((target, self.port), timeout=2, retries=1),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                    lexicographicMode=False,
                ):
                    if err_ind or err_status:
                        break
                    for vb in var_binds:
                        results.append((str(vb[0]), str(vb[1])))
            finally:
                try:
                    snmp_engine.closeDispatcher()
                except Exception:
                    pass
            return results

        # On Windows, SelectorEventLoop does not support UDP datagrams;
        # ProactorEventLoop must be used explicitly in non-main threads.
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
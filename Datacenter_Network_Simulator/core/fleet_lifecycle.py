"""
Fleet Lifecycle Engine — simulates day-by-day IT fleet churn.

Real datacenters are not static: compute is provisioned and decommissioned
continuously (capacity expansion, refresh cycles, RMA swaps). DCIM platforms
track this as asset lifecycle. This engine models that organic change on a
*compressed* clock — one "day" every N minutes — by adding and removing
**servers** (and, when a rack fills, the ToR switch + PDU that a new rack needs).

Design choices (locked with the user):
  • Cadence    : compressed sim-day, configurable minutes/day, off until started.
  • Scope      : servers only, plus ToR-switch + PDU when expanding a new rack.
                 Core/aggregation/power/cooling are never churned.
  • Growth     : net-positive and lumpy — quiet days, normal days, burst days
                 (a burst ≈ a rack being filled). Capped per rack/row and overall.
  • Persistence: in-memory only. The on-disk topology JSON is never rewritten;
                 a restart reverts to the original fleet.

Coherence rules — a provisioned server must be *valid*, never a dangling node:
  • lands in a rack that has free U **and** a ToR switch **and** a PDU,
  • gets a free IP from the production pool,
  • uplinks to that rack's ToR (production layer) and draws from its PDU (power),
  • new device templates (vendor/model/interface_count) are cloned from an
    existing peer so the fleet stays internally consistent.

Operational note (same limitation as the manual "Add Device"): a brand-new IP is
not bound by a *running* SNMPSim/gNMI server until the next bind cycle — the
device is live in the topology, metrics tick, and REST immediately; live SNMP
polling on its fresh IP needs a rebind/restart.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.device_manager import Device, DeviceType, Vendor

if TYPE_CHECKING:
    from api.state import AppState

# Rack geometry: servers fill from the bottom; ToR + PDU sit at the top.
_RACK_UNITS = 42
_TOR_UNIT = 42
_PDU_UNIT = 41
_FIRST_SERVER_UNIT = 1


@dataclass
class DaySummary:
    day: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    expanded_racks: list[str] = field(default_factory=list)
    total_servers: int = 0


@dataclass
class FleetConfig:
    minutes_per_day: float = 5.0       # wall-clock minutes that equal one sim-day
    provision_lambda: int = 3          # avg servers provisioned on a normal day
    decommission_lambda: int = 1       # avg servers decommissioned (net-positive)
    max_servers_per_rack: int = 20
    max_racks_per_row: int = 12
    max_total_servers: int = 600


class FleetLifecycleEngine:
    """Background sim-day scheduler that churns the server fleet in-memory."""

    def __init__(self, app_state: "AppState", log_cb=None):
        self.s = app_state
        self._log = log_cb or (lambda *a, **k: None)
        self.cfg = FleetConfig()
        self.enabled = False
        self.day = 0
        self.history: list[DaySummary] = []
        self._seq = 0                  # monotonic suffix for generated names
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.enabled:
            return
        if self.s.device_manager is None or self.s.topology is None:
            raise RuntimeError("Topology not loaded")
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fleet-lifecycle", daemon=True)
        self._thread.start()
        self._log(f"[Fleet] lifecycle started — 1 day every {self.cfg.minutes_per_day} min")

    def stop(self) -> None:
        self.enabled = False
        self._stop.set()
        self._log("[Fleet] lifecycle stopped")

    def _run(self) -> None:
        # Interruptible sleep so a stop/interval change takes effect promptly.
        while not self._stop.is_set():
            if self._stop.wait(timeout=max(1.0, self.cfg.minutes_per_day * 60.0)):
                break
            try:
                self.advance_day()
            except Exception as e:  # never let one bad day kill the loop
                self._log(f"[Fleet] day error: {e}")

    # ── one sim-day ──────────────────────────────────────────────────────────

    def advance_day(self) -> DaySummary:
        """Apply one day of churn: decommission a few, provision more."""
        with self._lock:
            self.day += 1
            summ = DaySummary(day=self.day)
            self._decommission(summ)
            self._provision(summ)
            summ.total_servers = len(self._servers())
            self.history.append(summ)
            self.history = self.history[-60:]
            if self.s is not None:
                self.s.notify_ui("sync_devices")
            self._log(f"[Fleet] day {self.day}: +{len(summ.added)} -{len(summ.removed)} "
                      f"(servers={summ.total_servers})")
            return summ

    # ── churn counts (lumpy, net-positive) ───────────────────────────────────

    @staticmethod
    def _lumpy(lam: int) -> int:
        r = random.random()
        if r < 0.35:                       # quiet day — nothing happens
            return 0
        if r < 0.85:                       # normal day
            return max(1, lam)
        return lam + random.randint(1, 3)  # burst — e.g. a rack being filled

    # ── decommission ─────────────────────────────────────────────────────────

    def _decommission(self, summ: DaySummary) -> None:
        servers = self._servers()
        # Keep a floor so the fleet never fully drains during a demo.
        n = min(self._lumpy(self.cfg.decommission_lambda), max(0, len(servers) - 4))
        for dev in random.sample(servers, n) if n > 0 else []:
            try:
                ip = dev.ip_address
                self._decommission_net(dev)             # stop BMC/gNMI, unbind IP
                self.s.device_manager.remove_device(dev.id)
                self.s.topology.remove_device(dev.id)   # also drops incident links
                if self.s.ip_manager and ip:
                    self.s.ip_manager.release(ip)
                summ.removed.append(dev.name)
            except Exception as e:
                self._log(f"[Fleet] decom {dev.name} failed: {e}")

    # ── provision ────────────────────────────────────────────────────────────

    def _provision(self, summ: DaySummary) -> None:
        want = self._lumpy(self.cfg.provision_lambda)
        for _ in range(want):
            if len(self._servers()) >= self.cfg.max_total_servers:
                self._log("[Fleet] total-server cap reached — provisioning paused")
                break
            rack = self._rack_with_space()
            if rack is None:
                rack = self._expand_rack(summ)
                if rack is None:
                    self._log("[Fleet] no capacity to expand — provisioning paused")
                    break
            name = self._add_server(rack)
            if name:
                summ.added.append(name)

    # ── placement helpers ────────────────────────────────────────────────────

    def _servers(self) -> list[Device]:
        return [d for d in self.s.device_manager.get_all_devices()
                if d.device_type == DeviceType.SERVER]

    def _by_type(self, t: DeviceType) -> list[Device]:
        return [d for d in self.s.device_manager.get_all_devices() if d.device_type == t]

    @staticmethod
    def _rack_key(d: Device) -> tuple:
        return (d.datacenter or "", d.rack_row or 0, d.rack_num or 0)

    def _rack_devices(self, key: tuple) -> list[Device]:
        return [d for d in self.s.device_manager.get_all_devices() if self._rack_key(d) == key]

    def _find_in_rack(self, key: tuple, t: DeviceType) -> Optional[Device]:
        for d in self._rack_devices(key):
            if d.device_type == t:
                return d
        return None

    def _rack_with_space(self) -> Optional[dict]:
        """A populated rack that has free U, a ToR switch and a PDU. Returns the
        rack context (key + ToR + PDU + a server template) or None."""
        racks: dict[tuple, int] = {}
        for srv in self._servers():
            racks[self._rack_key(srv)] = racks.get(self._rack_key(srv), 0) + 1
        # Least-full first, so racks fill evenly. A rack's ToR/PDU are found by
        # following an existing peer server's real uplink — robust whether the
        # ToR is per-rack or per-row.
        for key, count in sorted(racks.items(), key=lambda kv: kv[1]):
            if count >= self.cfg.max_servers_per_rack:
                continue
            tmpl = self._find_in_rack(key, DeviceType.SERVER)
            if tmpl is None:
                continue
            tor = self._neighbor(tmpl, "production", (DeviceType.SWITCH,))
            pdu = self._neighbor(tmpl, "power", (DeviceType.PDU, DeviceType.FLOOR_PDU))
            if tor:
                return {"key": key, "tor": tor, "pdu": pdu, "server_tmpl": tmpl}
        return None

    def _neighbor(self, dev: Device, layer: str, types: tuple) -> Optional[Device]:
        """First neighbour of *dev* on *layer* whose type is in *types*."""
        try:
            adj = self.s.topology.graph[dev.id]
        except Exception:
            return None
        for nbr, edges in adj.items():
            if any(ed.get("layer") == layer for ed in edges.values()):
                d = self.s.device_manager.get_device(nbr)
                if d and d.device_type in types:
                    return d
        return None

    def _expand_rack(self, summ: DaySummary) -> Optional[dict]:
        """Provision a new rack (ToR switch + PDU) in a row with spare rack slots,
        then return it as a placement target. The new ToR uplinks to the same
        upstream switch an existing ToR uses (aggregation), so it isn't dangling."""
        tor_tmpl = self._template_switch()
        pdu_tmpl = self._by_type(DeviceType.PDU) or self._by_type(DeviceType.FLOOR_PDU)
        srv_tmpl = self._servers()
        if not (tor_tmpl and pdu_tmpl and srv_tmpl):
            return None   # nothing to clone from — cannot expand coherently
        pdu_tmpl = pdu_tmpl[0]
        srv_tmpl = srv_tmpl[0]

        # Pick a row with fewer than max_racks_per_row racks.
        rows: dict[tuple, set] = {}
        for d in self.s.device_manager.get_all_devices():
            if d.datacenter and d.rack_row:
                rows.setdefault((d.datacenter, d.rack_row), set()).add(d.rack_num or 0)
        target_row = None
        for (dc, row), racknums in sorted(rows.items()):
            if len(racknums) < self.cfg.max_racks_per_row:
                target_row, used = (dc, row), racknums
                break
        if target_row is None:
            return None
        dc, row = target_row
        new_num = (max(used) if used else 0) + 1

        upstream = self._uplink_for(tor_tmpl)
        tor = self._clone(tor_tmpl, dc, row, new_num, _TOR_UNIT, prefix="tor")
        if tor is None:
            return None
        if upstream is not None:
            self.s.topology.add_link(tor.id, upstream.id, layer="production")
        pdu = self._clone(pdu_tmpl, dc, row, new_num, _PDU_UNIT, prefix="pdu")

        # New ToR + PDU also answer SNMP/gNMI once commissioned.
        self._commission(tor)
        if pdu is not None:
            self._commission(pdu)

        summ.expanded_racks.append(f"{dc}:R{row}:RACK{new_num}")
        self._log(f"[Fleet] expanded new rack {dc}:R{row}:RACK{new_num} (ToR+PDU)")
        return {"key": (dc, row, new_num), "tor": tor, "pdu": pdu, "server_tmpl": srv_tmpl}

    def _template_switch(self) -> Optional[Device]:
        # Prefer a switch that already has servers (a real ToR) over a spine.
        switches = self._by_type(DeviceType.SWITCH)
        for sw in switches:
            key = self._rack_key(sw)
            if any(d.device_type == DeviceType.SERVER for d in self._rack_devices(key)):
                return sw
        return switches[0] if switches else None

    def _uplink_for(self, tor: Device) -> Optional[Device]:
        """The aggregation/spine device a ToR connects up to — reused for new ToRs."""
        try:
            g = self.s.topology.graph
            for nbr in g.neighbors(tor.id):
                dev = self.s.device_manager.get_device(nbr)
                if dev and dev.device_type in (DeviceType.SWITCH, DeviceType.ROUTER) and dev.id != tor.id:
                    return dev
        except Exception:
            pass
        return None

    # ── device creation ──────────────────────────────────────────────────────

    def _alloc_ip(self) -> Optional[str]:
        used = {d.ip_address for d in self.s.device_manager.get_all_devices()}
        if self.s.ip_manager is None:
            return None
        for _ in range(10000):
            try:
                ip = self.s.ip_manager.next_ip()
            except Exception:
                return None
            if ip not in used:
                return ip
        return None

    def _unique_name(self, base: str) -> str:
        names = {d.name for d in self.s.device_manager.get_all_devices()}
        while True:
            self._seq += 1
            cand = f"{base}-{self._seq:04d}"
            if cand not in names:
                return cand

    def _next_free_unit(self, key: tuple) -> int:
        used = {d.rack_unit for d in self._rack_devices(key) if d.rack_unit}
        u = _FIRST_SERVER_UNIT
        while u in used and u < _PDU_UNIT:
            u += 1
        return u

    def _add_server(self, rack: dict) -> Optional[str]:
        tmpl: Device = rack["server_tmpl"]
        dc, row, num = rack["key"]
        unit = self._next_free_unit(rack["key"])
        dev = self._clone(tmpl, dc, row, num, unit, prefix="srv")
        if dev is None:
            return None
        # Wire it in: production uplink to the rack ToR, power feed from the PDU.
        try:
            self.s.topology.add_link(dev.id, rack["tor"].id, layer="production")
            if rack.get("pdu"):
                self.s.topology.add_link(dev.id, rack["pdu"].id, layer="power")
        except Exception as e:
            self._log(f"[Fleet] wiring {dev.name} failed: {e}")
        self._commission(dev)   # bring it online on SNMP/gNMI/Redfish
        return dev.name

    def _clone(self, tmpl: Device, dc: str, row: int, num: int, unit: int,
               prefix: str) -> Optional[Device]:
        """Create a new device cloned from *tmpl*'s vendor/model/port profile,
        placed at the given rack location, and register it in manager+topology."""
        ip = self._alloc_ip()
        if ip is None:
            self._log("[Fleet] IP pool exhausted")
            return None
        base = f"{dc or 'dc'}-{prefix}".lower().replace(" ", "-")
        name = self._unique_name(base)
        loc = ", ".join(p for p in [dc, f"Row {row}", f"Rack {num}", f"U{unit}"] if p)
        try:
            dev = Device(
                name=name,
                device_type=tmpl.device_type,
                vendor=tmpl.vendor,
                ip_address=ip,
                model_name=tmpl.model_name,
                snmp_port=getattr(tmpl, "snmp_port", 161),
                gnmi_port=getattr(tmpl, "gnmi_port", 57400),
                interface_count=getattr(tmpl, "interface_count", 8),
                metrics_enabled=True,
                country=getattr(tmpl, "country", ""),
                datacenter_city=getattr(tmpl, "datacenter_city", ""),
                datacenter=dc,
                room=getattr(tmpl, "room", ""),
                floor=getattr(tmpl, "floor", ""),
                rack_row=row,
                rack_num=num,
                rack_unit=unit,
                sys_location_override=loc,
            )
            self.s.device_manager.add_device(dev)
            self.s.topology.add_device(dev, x=0.0, y=0.0)
            if self.s.ip_manager:
                self.s.ip_manager.reserve(ip)
            return dev
        except Exception as e:
            self._log(f"[Fleet] create {name} failed: {e}")
            if self.s.ip_manager:
                self.s.ip_manager.release(ip)
            return None

    # ── hot-commission (make a churned device answer on the live protocols) ───

    def _commission(self, device: Device) -> None:
        """Bring a freshly-provisioned device online on the running protocol
        servers: host-bind its IP, generate SNMP+gNMI datasets, and hot-add it to
        gNMI/Redfish. Best-effort and fully guarded — never breaks the sim day.

        SNMP: snmpsim is a wildcard 0.0.0.0:161 subprocess that routes by
        community (= device IP) to <ip>.snmprec in its data-dir. Writing the file
        + host-binding the IP makes the device pollable without a restart, as long
        as snmpsim resolves recordings from the dir per request."""
        self._bind_ip(device.ip_address)
        self._gen_datasets(device)
        g = getattr(self.s, "gnmi", None)
        if g is not None and getattr(g, "_running", False):
            try: g.add_device(device)
            except Exception as e: self._log(f"[Fleet] gNMI commission {device.name}: {e}")
        r = getattr(self.s, "redfish", None)
        if (device.device_type == DeviceType.SERVER and r is not None
                and getattr(r, "_running", False)):
            try: r.add_device(device)
            except Exception as e: self._log(f"[Fleet] Redfish commission {device.name}: {e}")

    def _decommission_net(self, device: Device) -> None:
        """Undo _commission for a device about to be removed."""
        bind_ip = getattr(device, "mgmt_ip", "") or device.ip_address
        g = getattr(self.s, "gnmi", None)
        if g is not None:
            try: g.remove_device(bind_ip)
            except Exception: pass
        r = getattr(self.s, "redfish", None)
        if r is not None:
            try: r.remove_device(bind_ip)
            except Exception: pass
        self._unbind_ip(device.ip_address)

    def _gen_datasets(self, device: Device) -> None:
        try:
            from core.snmprec_generator import SNMPRecGenerator
            SNMPRecGenerator(getattr(self.s, "snmp_datasets_dir", "datasets/snmp")) \
                .generate_device(device, self.s.topology)
        except Exception as e:
            self._log(f"[Fleet] SNMP dataset {device.name}: {e}")
        try:
            from core.gnmi_data_generator import GNMIDataGenerator
            GNMIDataGenerator(getattr(self.s, "gnmi_datasets_dir", "datasets/gnmi")) \
                .generate_device(device, self.s.topology)
        except Exception as e:
            self._log(f"[Fleet] gNMI dataset {device.name}: {e}")

    def _bind_ip(self, ip: str) -> None:
        iface = getattr(self.s, "selected_adapter", "")
        if not iface or not ip:
            return
        try:
            from core import ip_binder
            if not ip_binder.is_admin():
                self._log("[Fleet] not admin — skipping host IP bind (SNMP/Redfish "
                          "poll on new IPs needs a manual rebind)")
                return
            ip_binder.add_ip(iface, ip, getattr(self.s, "subnet_mask", "255.255.255.0"))
        except Exception as e:
            self._log(f"[Fleet] bind {ip}: {e}")

    def _unbind_ip(self, ip: str) -> None:
        iface = getattr(self.s, "selected_adapter", "")
        if not iface or not ip:
            return
        try:
            from core import ip_binder
            if ip_binder.is_admin():
                ip_binder.remove_ip(iface, ip)
        except Exception:
            pass

    # ── status ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "day": self.day,
            "config": {
                "minutes_per_day": self.cfg.minutes_per_day,
                "provision_lambda": self.cfg.provision_lambda,
                "decommission_lambda": self.cfg.decommission_lambda,
                "max_servers_per_rack": self.cfg.max_servers_per_rack,
                "max_racks_per_row": self.cfg.max_racks_per_row,
                "max_total_servers": self.cfg.max_total_servers,
            },
            "total_servers": len(self._servers()) if self.s.device_manager else 0,
            "history": [
                {"day": h.day, "added": h.added, "removed": h.removed,
                 "expanded_racks": h.expanded_racks, "total_servers": h.total_servers}
                for h in self.history[-30:]
            ],
        }

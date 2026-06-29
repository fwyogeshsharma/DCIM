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
from core.rack_capacity import (
    leaf_interface_groups, leaf_port_roles, rack_server_capacity,
    TOR_A_UNIT, TOR_B_UNIT, PDU_UNIT, FIRST_SERVER_UNIT, LAST_SERVER_UNIT,
)

if TYPE_CHECKING:
    from api.state import AppState

# Rack geometry (shared contract — see core/rack_capacity.py):
#   U42 = ToR-A (leaf)   U41 = reserved for future MLAG peer leaf (empty)
#   PDUs = 0U vertical    servers fill U1..U40 from the bottom.


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
    # Per-rack server ceiling = min(leaf downlink ports, power_cap). power_cap is
    # the realistic ~10-15 kW binding limit; flip-invariant for dual-homing.
    power_cap: int = 22
    # Growth policy (locked with the user): fill the racks that already exist in
    # the current halls first; never enlarge an existing hall's footprint. Only
    # once every existing rack is at capacity does the fleet open a *new* hall
    # (room), and a new hall is itself capped at a fixed grid of
    # max_racks_per_row x compute_rows_per_room racks before the next hall opens.
    max_racks_per_row: int = 5         # racks per compute row in a NEW (fleet) hall
    compute_rows_per_room: int = 2     # compute rows per NEW hall -> grid = rows x width
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
        # Halls (rooms) that existed when the engine first provisioned — their
        # footprint is frozen: the fleet fills their racks but never adds a rack
        # to them. Snapshotted lazily on the first provision so it captures the
        # loaded topology, not anything the fleet later creates.
        self._frozen_rooms: Optional[set] = None
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
        self._snapshot_existing_rooms()
        want = self._lumpy(self.cfg.provision_lambda)
        for _ in range(want):
            if len(self._servers()) >= self.cfg.max_total_servers:
                self._log("[Fleet] total-server cap reached — provisioning paused")
                break
            # Placement order: (1) an existing rack with free U, (2) the next rack
            # in a fleet hall that still has grid space, (3) a brand-new hall.
            # Existing (curated) halls are never enlarged — their footprint is
            # frozen; capacity beyond them comes from new halls only.
            rack = self._rack_with_space()
            if rack is None:
                rack = self._expand_capacity(summ)
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

    @staticmethod
    def _phys_row_key(d: Device) -> tuple:
        """Physically-unique row id. A DC can have several halls/floors that
        reuse row numbers, so include floor + room — otherwise two different
        physical rows collapse into one and a new rack gets mislocated."""
        return (d.datacenter or "", str(d.floor or ""), d.room or "", d.rack_row or 0)

    @staticmethod
    def _room_key(d: Device) -> tuple:
        """Physically-unique hall (room) id: (dc, floor, room)."""
        return (d.datacenter or "", str(d.floor or ""), d.room or "")

    def _snapshot_existing_rooms(self) -> None:
        """Freeze the set of halls that already host servers, once. These curated
        halls are filled but never enlarged; new capacity goes to new halls."""
        if self._frozen_rooms is None:
            self._frozen_rooms = {self._room_key(d) for d in self._servers()}

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
        # ToR is per-rack or per-row. Capacity is bound by the leaf's server-
        # facing downlink ports (or the power cap), not a flat number.
        for key, count in sorted(racks.items(), key=lambda kv: kv[1]):
            tmpl = self._find_in_rack(key, DeviceType.SERVER)
            if tmpl is None:
                continue
            tor = self._neighbor(tmpl, "production", (DeviceType.SWITCH,))
            if tor is None:
                continue
            if count >= self._rack_cap(tor):
                continue
            pdu = self._neighbor(tmpl, "power", (DeviceType.PDU, DeviceType.FLOOR_PDU))
            return {"key": key, "tor": tor, "pdu": pdu, "server_tmpl": tmpl}
        return None

    def _rack_cap(self, tor: Device) -> int:
        """Server ceiling for a rack fronted by leaf *tor*: min(downlink ports,
        power cap). Flip-invariant across single/dual-homing."""
        downlink, _ = leaf_port_roles(getattr(tor, "model_name", "") or "",
                                      getattr(tor, "interface_count", 54))
        return rack_server_capacity(downlink, self.cfg.power_cap)

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

    # ── capacity expansion (new racks live in NEW halls, never curated ones) ──

    def _expand_capacity(self, summ: DaySummary) -> Optional[dict]:
        """Add a compute rack without enlarging any existing hall. First try to
        grow a fleet-created hall that still has grid space; if none has room (or
        none exists yet), open a brand-new hall. Returns a placement target."""
        rack = self._add_rack_to_fleet_room(summ)
        if rack is not None:
            return rack
        return self._open_new_room(summ)

    def _fleet_rooms(self) -> dict[tuple, list[Device]]:
        """Halls the fleet itself created (i.e. not in the frozen snapshot),
        mapped roomkey -> its server devices."""
        frozen = self._frozen_rooms or set()
        rooms: dict[tuple, list[Device]] = {}
        for srv in self._servers():
            rk = self._room_key(srv)
            if rk in frozen:
                continue
            rooms.setdefault(rk, []).append(srv)
        return rooms

    def _add_rack_to_fleet_room(self, summ: DaySummary) -> Optional[dict]:
        """If a fleet-created hall still has space in its fixed grid
        (max_racks_per_row x compute_rows_per_room), add the next rack to it."""
        width = max(1, self.cfg.max_racks_per_row)
        grid = width * max(1, self.cfg.compute_rows_per_room)
        # Most-full fleet hall first, so one hall fills before the next is touched.
        for rk, servers in sorted(self._fleet_rooms().items(),
                                  key=lambda kv: (-len(kv[1]), tuple(map(str, kv[0])))):
            racks = {(s.rack_row or 0, s.rack_num or 0) for s in servers}
            if len(racks) >= grid:
                continue
            tmpl = servers[0]
            tor_tmpl = self._neighbor(tmpl, "production", (DeviceType.SWITCH,))
            if tor_tmpl is None:
                continue
            dc, floor, room = rk
            base_row = min(s.rack_row or 0 for s in servers)
            idx = len(racks)                       # next grid slot, fill in order
            new_row = base_row + idx // width
            new_num = idx % width + 1
            return self._build_rack(dc, floor, room, new_row, new_num,
                                    tor_tmpl, tmpl, summ)
        return None

    def _open_new_room(self, summ: DaySummary) -> Optional[dict]:
        """Commission a brand-new server hall in the busiest DC and seed it with
        its first compute rack. The hall gets the next free floor and the next
        'Server Hall <letter>'; its rows use DC-global-unique numbers so a rack's
        (dc, row, num) key never collides with another hall's."""
        dc = self._busiest_dc()
        if dc is None:
            return None
        srv_tmpl = next((s for s in self._servers() if (s.datacenter or "") == dc), None)
        if srv_tmpl is None:
            return None
        tor_tmpl = self._neighbor(srv_tmpl, "production", (DeviceType.SWITCH,))
        if tor_tmpl is None:
            return None
        floor = self._next_floor(dc)
        room = self._next_hall_name(dc)
        base_row = self._next_dc_row(dc)
        return self._build_rack(dc, floor, room, base_row, 1, tor_tmpl, srv_tmpl, summ)

    def _busiest_dc(self) -> Optional[str]:
        counts: dict[str, int] = {}
        for s in self._servers():
            counts[s.datacenter or ""] = counts.get(s.datacenter or "", 0) + 1
        if not counts:
            return None
        return max(sorted(counts), key=lambda d: counts[d])

    def _next_floor(self, dc: str) -> str:
        """Next numeric floor above the DC's existing numeric floors (G/Roof are
        ignored). Halls live on numbered floors, one hall per floor."""
        nums = []
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "") != dc:
                continue
            f = str(d.floor or "").strip()
            if f.isdigit():
                nums.append(int(f))
        return str((max(nums) if nums else 0) + 1)

    def _next_hall_name(self, dc: str) -> str:
        """Next 'Server Hall <letter>' after the highest existing one in the DC."""
        letters = []
        prefix = "Server Hall "
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "") != dc:
                continue
            room = d.room or ""
            if room.startswith(prefix) and len(room) == len(prefix) + 1:
                letters.append(room[-1].upper())
        nxt = chr(ord(max(letters)) + 1) if letters else "A"
        return f"{prefix}{nxt}"

    def _next_dc_row(self, dc: str) -> int:
        """One past the highest rack_row anywhere in the DC, so new-hall rows are
        globally unique within the DC (keeps (dc, row, num) rack keys distinct)."""
        rows = [d.rack_row or 0 for d in self.s.device_manager.get_all_devices()
                if (d.datacenter or "") == dc]
        return (max(rows) if rows else 0) + 1

    def _build_rack(self, dc: str, floor: str, room: str, row: int, num: int,
                    tor_tmpl: Device, srv_tmpl: Device,
                    summ: DaySummary) -> Optional[dict]:
        """Materialise a new compute rack (ToR leaf + rack PDU) at the given hall
        location and return it as a placement target. The ToR clones *tor_tmpl*
        (same DC vendor/model/spine) and is born MLAG-ready; the PDU clones the
        DC's power kit. Both are commissioned onto the live protocol servers."""
        pdu_tmpl = self._dc_pdu(dc)
        upstream = self._uplink_for(tor_tmpl)
        tor = self._clone(tor_tmpl, dc, row, num, TOR_A_UNIT, prefix="tor",
                          floor=floor, room=room)
        if tor is None:
            return None
        # New leaf is born MLAG-ready: realistic 48x25G+6x100G port-roles and a
        # reserved U41 peer slot, so the future dual-homing flip is non-disruptive.
        self.s.device_manager.update_device(
            tor.id,
            interface_groups=leaf_interface_groups(tor.model_name or "",
                                                   tor.interface_count),
            mlag_ready=True,
            mlag_peer_unit=TOR_B_UNIT,
        )
        if upstream is not None:
            self.s.topology.add_link(tor.id, upstream.id, layer="production")
        # Rack PDU is a 0U vertical side-rail mount (no RU consumed).
        pdu = (self._clone(pdu_tmpl, dc, row, num, PDU_UNIT, prefix="pdu",
                           floor=floor, room=room) if pdu_tmpl else None)

        # New ToR + PDU also answer SNMP/gNMI once commissioned.
        self._commission(tor)
        if pdu is not None:
            self._commission(pdu)

        summ.expanded_racks.append(f"{dc}:{room}:R{row}:RACK{num}")
        self._log(f"[Fleet] new rack {dc}/F{floor}/{room} R{row} RACK{num} (ToR+PDU)")
        return {"key": (dc, row, num), "floor": floor, "room": room,
                "tor": tor, "pdu": pdu, "server_tmpl": srv_tmpl}

    def _dc_pdu(self, dc: str) -> Optional[Device]:
        """A rack PDU in *dc* to clone the new rack's PDU from (vendor-consistent
        with that DC's power kit). Falls back to any PDU if the DC has none."""
        for d in self.s.device_manager.get_all_devices():
            if d.datacenter == dc and d.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU):
                return d
        pd = self._by_type(DeviceType.PDU) or self._by_type(DeviceType.FLOOR_PDU)
        return pd[0] if pd else None

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
        # Servers occupy U1..U40; U41 (MLAG peer slot) and U42 (ToR) stay clear.
        used = {d.rack_unit for d in self._rack_devices(key) if d.rack_unit}
        u = FIRST_SERVER_UNIT
        while u in used and u < LAST_SERVER_UNIT:
            u += 1
        return u

    def _add_server(self, rack: dict) -> Optional[str]:
        tmpl: Device = rack["server_tmpl"]
        dc, row, num = rack["key"]
        unit = self._next_free_unit(rack["key"])
        # New halls/racks carry an explicit floor+room; filling an existing rack
        # leaves them None so the server inherits its same-rack template's hall.
        dev = self._clone(tmpl, dc, row, num, unit, prefix="srv",
                          floor=rack.get("floor"), room=rack.get("room"))
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
               prefix: str, floor: Optional[str] = None,
               room: Optional[str] = None) -> Optional[Device]:
        """Create a new device cloned from *tmpl*'s vendor/model/port profile,
        placed at the given rack location, and register it in manager+topology.
        *floor*/*room* override the template's hall (used when the rack lives in a
        new hall); left None they inherit the template's floor/room."""
        ip = self._alloc_ip()
        if ip is None:
            self._log("[Fleet] IP pool exhausted")
            return None
        base = f"{dc or 'dc'}-{prefix}".lower().replace(" ", "-")
        name = self._unique_name(base)
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
                room=room if room is not None else getattr(tmpl, "room", ""),
                floor=floor if floor is not None else getattr(tmpl, "floor", ""),
                rack_row=row,
                rack_num=num,
                rack_unit=unit,
                # No sys_location_override: let Device.sys_location compute the
                # full country/city/DC/floor/room/rack string so fleet-added
                # devices match the format of curated peers.
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
                "power_cap": self.cfg.power_cap,
                "max_racks_per_row": self.cfg.max_racks_per_row,
                "compute_rows_per_room": self.cfg.compute_rows_per_room,
                "max_total_servers": self.cfg.max_total_servers,
            },
            "total_servers": len(self._servers()) if self.s.device_manager else 0,
            "history": [
                {"day": h.day, "added": h.added, "removed": h.removed,
                 "expanded_racks": h.expanded_racks, "total_servers": h.total_servers}
                for h in self.history[-30:]
            ],
        }

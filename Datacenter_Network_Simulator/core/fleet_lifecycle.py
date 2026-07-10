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
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from core.device_manager import Device, DeviceType, Vendor
from core.rack_capacity import (
    leaf_interface_groups, leaf_port_roles, rack_has_power_headroom,
    RACK_POWER_BUDGET_W_DEFAULT,
    TOR_A_UNIT, TOR_B_UNIT, PDU_UNIT, FIRST_SERVER_UNIT, LAST_SERVER_UNIT,
    SERVER_U_HEIGHT,
)
from core import hall_geometry as geo

if TYPE_CHECKING:
    from api.state import AppState

# ── Unified device-naming scheme (see tools/rename_devices.py) ────────────────
# Names lead with a type CODE, then location:
#   rack rooms:     <CODE>-<DC>-<ROOM>-R<row>-<rack:02d>
#   facility rooms: <CODE>-<DC>-<ROOM>
_ROOM_CODE = {
    "Server Hall A": "HA", "Server Hall B": "HB", "Central Plant": "CP",
    "Network Room": "NR", "UPS Room": "UR", "Generator Room": "GR",
    "Mechanical Room": "MR", "Roof": "RF",
}
# clone-prefix → leading type code (pdu*/rpp* carry an A/B side, handled inline)
_PREFIX_CODE = {"srv": "SRV", "tor": "LF", "spine": "SP", "ev2": "EV2",
                "crah": "CRAH", "sen": "SEN"}


def _room_code(room: Optional[str]) -> str:
    r = room or ""
    m = re.match(r"Server Hall (\w)", r)          # Server Hall A/B/C… → HA/HB/HC
    if m:
        return "H" + m.group(1).upper()
    return _ROOM_CODE.get(r, re.sub(r"[^A-Za-z]", "", r).upper()[:2] or "XX")


def _is_rack_room(room: Optional[str]) -> bool:
    r = room or ""
    return r.startswith("Server Hall") or r == "Network Room"

# Resource-safety ceiling on the fleet server count. This is NOT the facility
# limit (that is ~470 — the installed cooling plant; see FleetConfig below), but
# a hard guard on host resources: every commissioned server is a Redfish BMC
# socket and every new leaf a gNMI gRPC server (eventfds), so the process fd/
# thread count scales with the fleet. Even with the startup fd bump
# (core.resource_limits.raise_fd_limit), let the count run unbounded and the
# process eventually starves its own API listener (Errno 24). 5000 sits well
# under a 65536-fd budget while still allowing large demo fleets. The real fix
# for hyperscale counts is a single wildcard listener that demuxes by dest IP
# (as snmpsim does) — until then this ceiling stands.
MAX_TOTAL_SERVERS_HARD_CAP = 5000

# Rack geometry (shared contract — see core/rack_capacity.py):
#   U42 = ToR-A (leaf)   U41 = reserved for future MLAG peer leaf (empty)
#   PDUs = 0U vertical    servers fill U1..U40 from the bottom.


@dataclass
class DaySummary:
    day: int
    # added/removed hold per-device info dicts (name, vendor, mgmt_ip, ip) so the
    # UI Activity log can expand a day and show what actually churned.
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    expanded_racks: list[str] = field(default_factory=list)
    total_servers: int = 0


@dataclass
class FleetConfig:
    minutes_per_day: float = 5.0       # wall-clock minutes that equal one sim-day
    provision_lambda: int = 3          # avg servers provisioned on a normal day
    decommission_lambda: int = 1       # avg servers decommissioned (net-positive)
    # Per-rack fill is bound by TWO real limits, whichever binds first:
    #   1. leaf downlink ports — physical, flip-invariant for dual-homing.
    #   2. rack_power_budget_w — summed nameplate draw of the rack's kit must stay
    #      within its provisioned power budget (17.6 kW usable = a 22 kW rack PDU
    #      at the NEC 80% derate, the A/B single-feed ceiling). The usual binding
    #      limit in an enterprise hall (power/thermal, not ports).
    rack_power_budget_w: int = RACK_POWER_BUDGET_W_DEFAULT
    # Growth policy: each hall holds up to compute_rows_per_room x max_racks_per_row
    # compute racks. The fleet fills the racks already in a hall, then adds racks
    # up to that grid (curated racks count toward it), and only once every hall is
    # full opens a brand-new hall — matching the enlarged halls' headroom.
    max_racks_per_row: int = 5         # compute racks per row in a hall's grid
    compute_rows_per_room: int = 3     # compute rows per hall -> grid = rows x width
    # Sized to the STAGED cooling plant's ultimate capacity. The plant is installed
    # for this cap and sequences chiller modules on as load grows (see
    # core/cooling_model.stage_modules + DeviceStateStore._installed_modules), so the
    # fleet can grow to 3000 servers at design PUE (~1.5) instead of overloading a
    # frozen plant. Keep this in step with _PLANT_DESIGN_SERVER_CAP. User-editable in
    # the Fleet panel (bounded by the resource-safety hard cap).
    max_total_servers: int = 3000


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
        # Halls whose full CRAH complement has been installed (sized to ultimate rack
        # load + N+1), so we top a hall up to its cooling capacity exactly once.
        self._crah_ensured: set = set()
        # Halls the fleet has opened, so they're counted/filled like curated ones.
        self._fleet_halls: set = set()
        # Stable, DC-globally-unique rack_row label per (hall, virtual-row) the
        # fleet fills — keeps (dc, row, num) rack keys from colliding across halls.
        self._row_labels: dict = {}
        self._row_label_seq = 1000
        # Topology-graph layout for fleet nodes: each DC gets its own band (the
        # curated x-range, below the curated nodes) so DC1/DC2 fleet growth never
        # overlaps the other DC. Bounds snapshotted per DC; placed counter tiles.
        self._dc_bounds_cache: dict = {}
        self._dc_placed: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Latch so the "mgmt /22 base full — spilling to overflow" note is logged
        # once per session, not once per device past the cliff.
        self._mgmt_overflow_warned = False

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
            # Snapshot EVERY device id before the day so ACTIVITY's +/- reflects the
            # true device delta, not just servers: provisioning a rack/hall also adds
            # leaf/OOB/RPP/EV2/CRAH/PDU infra, and decommission drops a server's kit.
            # A full-device id diff below counts them all (the "srv" figure stays the
            # server subtotal).
            before = {d.id: d for d in self.s.device_manager.get_all_devices()}
            self._decommission(summ)
            self._provision(summ)
            after = {d.id: d for d in self.s.device_manager.get_all_devices()}
            summ.added   = [self._dev_info(after[i])  for i in (after.keys()  - before.keys())]
            summ.removed = [self._dev_info(before[i]) for i in (before.keys() - after.keys())]
            summ.total_servers = len(self._servers())
            self.history.append(summ)
            self.history = self.history[-60:]
            # Power graph changed (devices + power edges added/removed) — drop the
            # cached cascade so the new IT load ripples up to the PDU/UPS/RPP/EV2
            # meters on the next tick instead of being summed against a stale tree.
            ss = getattr(self.s, "state_store", None)
            if ss is not None:
                try: ss.invalidate_power_context()
                except Exception as e: self._log(f"[Fleet] power ctx invalidate: {e}")
            if self.s is not None:
                self.s.notify_ui("sync_devices")
            # SNMP agents for churned devices only become pollable after snmpsim
            # re-indexes its data-dir. Bounce it ONCE per changed day — async (off
            # the sim-day thread) and coalesced inside reload_snmp, never per
            # device. gNMI/Redfish were already hot-added live in _commission.
            if summ.added or summ.removed or summ.expanded_racks:
                # The power/topology graph gained or lost nodes+edges — tell the UI
                # to rebuild the topology scene (Qt) / refetch the graph (web), not
                # just the device list. Without this the churned devices show in the
                # device table but never appear/disappear on the live topology.
                if self.s is not None:
                    self.s.notify_ui("rebuild_topology_scene")
                ex = getattr(self.s, "executor", None)
                if ex is not None and getattr(self.s, "snmpsim", None) is not None:
                    try:
                        ex.submit(self.s.reload_snmp, self._log)
                    except Exception as e:
                        self._log(f"[Fleet] snmp reload submit: {e}")
            self._log(f"[Fleet] day {self.day}: +{len(summ.added)} -{len(summ.removed)} "
                      f"(servers={summ.total_servers})")
            return summ

    # ── churn counts (lumpy, net-positive) ───────────────────────────────────

    @staticmethod
    def _lumpy(lam: int) -> int:
        if lam <= 0:                       # rate of 0 = disabled, never any events
            return 0
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
                # (Counted by advance_day's full-device diff, not here.)
            except Exception as e:
                self._log(f"[Fleet] decom {dev.name} failed: {e}")

    # ── provision ────────────────────────────────────────────────────────────

    def _provision(self, summ: DaySummary) -> None:
        # Bring every populated hall up to its full CRAH complement (curated halls
        # the fleet packs beyond their original 4 CRAHs, plus any fleet halls). Once
        # per hall — cheap after the first pass.
        self._ensure_all_hall_crahs()
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
            dev = self._add_server(rack)
            # (Added devices — server + any new rack/hall infra — are counted by
            # advance_day's full-device diff, not appended here.)
            if dev is None:
                continue

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

    @staticmethod
    def _dev_info(dev: Device) -> dict:
        """Compact device descriptor for the Activity log (name/type/vendor/IPs)."""
        return {
            "name": dev.name,
            "device_type": getattr(dev.device_type, "value", str(dev.device_type or "")),
            "vendor": getattr(dev.vendor, "value", str(dev.vendor or "")),
            "mgmt_ip": getattr(dev, "mgmt_ip", "") or "",
            "ip": getattr(dev, "ip_address", "") or "",
        }

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
        racks: dict[tuple, int] = {}          # rack -> server count
        racks_w: dict[tuple, float] = {}      # rack -> summed nameplate watts (all kit)
        for srv in self._servers():
            racks[self._rack_key(srv)] = racks.get(self._rack_key(srv), 0) + 1
        # Rack power is the summed nameplate draw of ALL its kit (servers + ToR),
        # not just servers — PDUs are 0U infra and read 0, so they don't inflate.
        for d in self.s.device_manager.get_all_devices():
            k = self._rack_key(d)
            if k in racks:                    # only racks that already hold servers
                racks_w[k] = racks_w.get(k, 0.0) + float(getattr(d, "power_draw_w", 0) or 0)
        # Least-full first, so racks fill evenly. A rack's ToR/PDU are found by
        # following an existing peer server's real uplink — robust whether the
        # ToR is per-rack or per-row. A rack has room only if it clears BOTH the
        # physical downlink-port limit AND the power budget (summed watts + the
        # next server's nameplate draw must stay within the per-rack budget).
        for key, count in sorted(racks.items(), key=lambda kv: kv[1]):
            tmpl = self._find_in_rack(key, DeviceType.SERVER)
            if tmpl is None:
                continue
            tor = self._neighbor(tmpl, "production", (DeviceType.SWITCH,))
            if tor is None:
                continue
            if count >= self._port_cap(tor):
                continue                       # leaf downlink ports exhausted
            if self._next_free_unit(key) is None:
                continue                       # rack U-space full (2U servers, ~20 max)
            pdus = self._neighbors(tmpl, "power", (DeviceType.PDU, DeviceType.FLOOR_PDU))
            add_w = float(getattr(tmpl, "power_draw_w", 0) or 0)
            if not rack_has_power_headroom(racks_w.get(key, 0.0), add_w,
                                           self._rack_budget_w(pdus)):
                continue                       # would exceed the rack power budget
            return {"key": key, "tor": tor, "pdus": pdus, "server_tmpl": tmpl}
        return None

    def _rack_budget_w(self, pdus: list) -> int:
        """Usable per-rack power budget: the operator's configured budget, capped
        by what a SINGLE rack PDU can actually deliver on A/B failover (its real
        nameplate × 0.8 NEC continuous-load derate). The PDU is the physical
        ceiling — you cannot provision a rack past what one feed carries when its
        pair fails — while the configured budget models a smaller branch-circuit
        or per-rack cooling limit BELOW that ceiling. Falls back to the configured
        budget when the rack's PDUs carry no rating."""
        cap = int(self.cfg.rack_power_budget_w)
        rated = [int(getattr(p, "rated_power_w", 0) or 0) for p in pdus]
        rated = [r for r in rated if r > 0]
        if rated:
            cap = min(cap, int(min(rated) * 0.8))
        return cap

    def _port_cap(self, tor: Device) -> int:
        """Physical per-rack server ceiling: the leaf's server-facing downlink
        ports. Flip-invariant across single/dual-homing (a dual-homed server uses
        one downlink on each of the two leaves, so the count is unchanged). This
        is a hard co-limit alongside the power budget."""
        downlink, _ = leaf_port_roles(getattr(tor, "model_name", "") or "",
                                      getattr(tor, "interface_count", 54))
        return max(0, downlink)

    def _neighbor(self, dev: Device, layer: str, types: tuple) -> Optional[Device]:
        """First neighbour of *dev* on *layer* whose type is in *types*."""
        nb = self._neighbors(dev, layer, types)
        return nb[0] if nb else None

    def _neighbors(self, dev: Device, layer: str, types: tuple) -> list:
        """All neighbours of *dev* on *layer* whose type is in *types*."""
        out = []
        try:
            adj = self.s.topology.graph[dev.id]
        except Exception:
            return out
        for nbr, edges in adj.items():
            if any(ed.get("layer") == layer for ed in edges.values()):
                d = self.s.device_manager.get_device(nbr)
                if d and d.device_type in types:
                    out.append(d)
        return out

    # ── fabric port-capacity (spines fixed per pod, OOB stacks per hall) ──────
    def _is_spine(self, d: Optional[Device]) -> bool:
        # Names lead with a type code (e.g. 'SP1-DC1-HA-R1-01'); the role is the
        # first '-'-segment.
        return (d is not None and d.device_type == DeviceType.SWITCH
                and (d.name or "").split("-", 1)[0].upper().startswith("SP"))

    def _is_leaf(self, d: Optional[Device]) -> bool:
        return (d is not None and d.device_type == DeviceType.SWITCH
                and not (d.name or "").split("-", 1)[0].upper().startswith("SP"))

    def _spine_downlink_cap(self, spine: Device) -> int:
        """Leaf-facing downlink ports on a spine — the max leaves (hence racks) a
        pod can hold before a new pod/hall is needed."""
        return max(8, int(getattr(spine, "interface_count", 32) or 32))

    def _oob_port_cap(self, oob: Device) -> int:
        """Management ports on an OOB switch — max managed uplinks before another
        OOB must be stacked into the hall."""
        return max(8, int(getattr(oob, "interface_count", 48) or 48))

    def _leaves_on(self, dev: Device) -> int:
        """How many leaf switches are attached to *dev* (spine downlinks / OOB ports
        in use)."""
        try:
            return sum(1 for nbr in self.s.topology.graph.neighbors(dev.id)
                       if self._is_leaf(self.s.device_manager.get_device(nbr)))
        except Exception:
            return 0

    def _spines_have_room(self, spines: list) -> bool:
        """Clos rule: a new leaf uplinks to EVERY spine, so it needs a free downlink
        on every one. Full fabric → the pod (hall) is full."""
        return bool(spines) and all(
            self._leaves_on(sp) < self._spine_downlink_cap(sp) for sp in spines)

    def _link_layer(self, a: str, b: str) -> Optional[str]:
        try:
            for ed in self.s.topology.graph[a][b].values():
                if ed.get("layer"):
                    return ed["layer"]
        except Exception:
            pass
        return None

    def _hall_oobs(self, rk: tuple) -> list:
        """Every OOB switch that manages a leaf in hall *rk* (deduped)."""
        seen: dict = {}
        for s in self._servers():
            if self._room_key(s) != rk:
                continue
            leaf = self._neighbor(s, "production", (DeviceType.SWITCH,))
            if leaf is None:
                continue
            for o in self._neighbors(leaf, "management", (DeviceType.OOB_SWITCH,)):
                seen[o.id] = o
        return list(seen.values())

    def _clone_fabric_node(self, tmpl: Device, rk: tuple, prefix: str,
                           num: int = 1, fx: Optional[float] = None,
                           fy: Optional[float] = None) -> Optional[Device]:
        """Clone a shared fabric node (spine / OOB) into hall *rk* and replicate its
        UPSTREAM links (to the core / management aggregation) — but NOT its downstream
        leaves/servers/PDUs. Used to give a new hall its own pod fabric and to stack
        an extra OOB when a hall's management ports are exhausted. *num*/*fx*/*fy*
        place it on the hall's floor grid (network/back row) so the floor-plan draws
        it inside the right hall; without them it would inherit the source hall's
        coordinates and render in the wrong room."""
        dc, floor, room = rk
        new = self._clone(tmpl, dc, self._row_label(rk, prefix), num,
                          int(getattr(tmpl, "rack_unit", 1) or 1),
                          prefix=prefix, floor=floor, room=room, fx=fx, fy=fy)
        if new is None:
            return None
        _down = (DeviceType.SERVER, DeviceType.PDU, DeviceType.FLOOR_PDU)
        try:
            for nbr in list(self.s.topology.graph.neighbors(tmpl.id)):
                d = self.s.device_manager.get_device(nbr)
                if d is None or self._is_leaf(d) or d.device_type in _down:
                    continue                      # skip downstream; keep uplinks only
                self.s.topology.add_link(new.id, nbr,
                                         layer=self._link_layer(tmpl.id, nbr) or "production")
        except Exception as e:
            self._log(f"[Fleet] fabric clone uplink {new.name}: {e}")
        return new

    def _oob_for_new_leaf(self, rk: tuple, infra: dict) -> Optional[Device]:
        """An OOB switch in hall *rk* with a free management port; stack a new OOB
        into the hall (cloned from the existing one) when they are all full."""
        oobs = self._hall_oobs(rk) or ([infra["oob"]] if infra.get("oob") else [])
        for o in oobs:
            if self._leaves_on(o) < self._oob_port_cap(o):
                return o
        tmpl = oobs[0] if oobs else infra.get("oob")
        if tmpl is None:
            return None
        # Next free slot on the hall's back network row (past RPPs + spines + OOBs).
        slot = 3 + len(infra.get("spines") or []) + len(oobs)
        new = self._clone_fabric_node(tmpl, rk, "oob", num=slot,
                                      fx=geo.rack_x(slot),
                                      fy=getattr(tmpl, "floor_y", None))
        if new is not None:
            self._commission(new)
            self._log(f"[Fleet] OOB ports exhausted — stacked {new.name} in "
                      f"{rk[0]}/{rk[2]}")
        return new

    # ── capacity expansion (fill each hall's grid, then open a new hall) ──────
    #
    # The static floor-plan (tools/enlarge_halls.py) sizes the rooms; the fleet
    # is in-memory only, so it works in a LOGICAL grid, not floor coordinates.
    # Each hall holds up to compute_rows_per_room x max_racks_per_row compute
    # racks. Curated racks count toward that cap; the fleet fills the remainder,
    # then opens a new hall. Fleet racks get DC-globally-unique synthetic row
    # labels (>= 1000) so their (dc, row, num) keys never collide with curated
    # rows or with another hall's.

    _FLEET_ROW_BASE = 1000
    _HALL_SENSORS   = 3      # environmental probes (temp/RH/airflow) per new hall
    _DESIGN_RACK_KW = 12.0   # design per-rack IT the hall's CRAHs are sized to

    def _expand_capacity(self, summ: DaySummary) -> Optional[dict]:
        """Add a compute rack. First fill a hall (curated or fleet) that still has
        grid space; only when every hall is full open a brand-new hall."""
        rack = self._fill_hall_grid(summ)
        if rack is not None:
            return rack
        return self._open_new_hall(summ)

    def _row_label(self, rk: tuple, vrow) -> int:
        """Stable, globally-unique rack_row label for a (hall, virtual-row)."""
        key = (rk, vrow)
        if key not in self._row_labels:
            self._row_label_seq += 1
            self._row_labels[key] = self._row_label_seq
        return self._row_labels[key]

    def _hall_back_y(self, rk: tuple) -> Optional[float]:
        """Back-most curated compute-row floor_y in hall *rk* — the row behind
        which the fleet lays its new rows. None for a fresh fleet hall."""
        ys = [d.floor_y for d in self._servers()
              if self._room_key(d) == rk and d.floor_y is not None
              and (d.rack_row or 0) < self._FLEET_ROW_BASE]
        return max(ys) if ys else None

    def _hall_extent(self, rk: tuple) -> Optional[dict]:
        """The floor-plan room extent (width/depth/racks_per_row/rows/aisles) for
        hall *rk*, or None when the topology carries no floor-plan block."""
        dc, _floor, room = rk
        fp = getattr(self.s.topology, "floorplan", None)
        if not isinstance(fp, dict):
            return None
        return (fp.get("rooms") or {}).get(f"{dc}/{room}")

    def _hall_has_local_spine(self, rk: tuple) -> bool:
        """True if a spine switch physically lives in hall *rk* (a network hall /
        pod), False for a compute ANNEX that shares the DC's spines in another
        hall. Determines whether row 1 is reserved for the network/MDA."""
        return any(self._is_spine(d) and self._room_key(d) == rk
                   for d in self.s.device_manager.get_all_devices())

    def _hall_grid(self, rk: tuple):
        """(racks_per_row, compute_rows, first_compute_row, n_rows) for hall *rk*,
        derived from its PHYSICAL floor-plan extent so a hall fills to its real
        rack capacity before a new hall opens — instead of a fixed config box.
        The back row (row n_rows) is the CRAH perimeter. A NETWORK hall reserves
        the front row (row 1) for spines/OOB, so compute occupies rows 2..n_rows-1.
        A compute ANNEX (no local spine — shares the DC's fabric) has no network
        row, so compute fills from row 1 (rows 1..n_rows-1). Falls back to the
        config grid for a hall with no extent."""
        ext = self._hall_extent(rk)
        rows = ext.get("rows") if ext else None
        # Row capacity is the LARGER of the stored racks_per_row and what the hall's
        # physical width actually fits — so a hall whose width_m outgrew its stored
        # racks_per_row (the curated extents disagreed: an 8.4 m hall stored 9 but
        # fits 13) packs the real floor width instead of leaving a dead strip, while
        # a hall already sized tight is never shrunk below its authored count. Falls
        # back to the stored value when the hall carries no width.
        w = ext.get("width_m") if ext else None
        stored = (ext.get("racks_per_row") if ext else None) or 0
        rpr = max(stored, geo.racks_for_width(w) if w else 0) or None
        if ext and rpr and rows:
            rpr = max(1, int(rpr))
            if not self._hall_has_local_spine(rk):        # compute annex
                n_rows = max(2, len(rows)); compute_rows = max(1, n_rows - 1); first = 1
            else:
                n_rows = max(3, len(rows)); compute_rows = max(1, n_rows - 2); first = 2
            # Keep a hall's compute grid UNDER one 42-pole RPP per side (_RPP_POLES):
            # each rack draws one branch PDU per side, so ≤ 41 racks means rpp_a/rpp_b
            # each carry < 42 branches — the hall is powered by its OWN single RPP
            # pair, never a shared or spilled panel across halls. When the grid fills
            # the fleet opens a NEW hall (with its own RPPs) instead of overflowing.
            rpr = max(1, min(rpr, (self._RPP_POLES - 1) // compute_rows))
            return rpr, compute_rows, first, n_rows
        cr = max(1, self.cfg.compute_rows_per_room)
        return max(1, self.cfg.max_racks_per_row), cr, 1, cr + 1

    def _rack_coords(self, rk: tuple, vrow: int, num: int,
                     first_row: int = 1, n_rows: Optional[int] = None):
        """Floor-plan coords for fleet rack (vrow, num) in hall *rk*: laid on the
        empty rows behind the curated compute (curated hall) or from *first_row*
        (fresh fleet hall). Returns None when the row would reach the back-wall
        CRAH perimeter (row n_rows) — the hall is then full and a new one opens."""
        back = self._hall_back_y(rk)
        fy = round((back + geo.ROW_PITCH * (vrow + 1)) if back is not None
                   else geo.row_y(first_row + vrow), 4)
        if n_rows is not None and fy >= geo.row_y(n_rows) - 1e-6:
            return None                                # would hit the CRAH back row
        fx = geo.rack_x(num)
        i = max(1, int(round((fy - geo.row_y(1)) / geo.ROW_PITCH)) + 1)
        hot, cold, facing = geo.row_aisles(i)
        return fx, fy, hot, cold, facing

    def _hall_compute_racks(self) -> dict:
        """roomkey -> set of (rack_row, rack_num) racks that hold servers."""
        from collections import defaultdict
        racks: dict = defaultdict(set)
        for s in self._servers():
            racks[self._room_key(s)].add((s.rack_row or 0, s.rack_num or 0))
        return racks

    def _hall_infra(self, rk: tuple) -> Optional[dict]:
        """Resolve a hall's shared kit for cloning a new compute rack into it:
        a server + leaf template, both rack-PDU feeds (RPP A/B), the OOB switch
        and the spine set. None if the hall isn't a wired compute hall."""
        servers = [s for s in self._servers() if self._room_key(s) == rk]
        if not servers:
            return None
        srv_tmpl = servers[0]
        leaf = self._neighbor(srv_tmpl, "production", (DeviceType.SWITCH,))
        if leaf is None:
            return None
        oob = self._neighbor(leaf, "management", (DeviceType.OOB_SWITCH,))
        spines = []
        try:
            for nbr in self.s.topology.graph.neighbors(leaf.id):
                d = self.s.device_manager.get_device(nbr)
                if d and self._is_spine(d):
                    spines.append(d)
        except Exception:
            pass
        rpps = [d for d in self.s.device_manager.get_all_devices()
                if d.device_type == DeviceType.RPP and self._room_key(d) == rk]
        # A/B feed side is in the leading type code (e.g. 'RPPA-DC1-HA-R1-04').
        rpp_a = next((r for r in rpps if r.name and "A" in r.name.split("-", 1)[0]), None)
        rpp_b = next((r for r in rpps if r.name and "B" in r.name.split("-", 1)[0]), None)
        if rpp_a is None and rpps:
            rpp_a = rpps[0]
        if rpp_b is None and len(rpps) > 1:
            rpp_b = rpps[1]
        # Hall cooling/env: the CRAHs that air-cool this hall and a sensor to
        # clone. A CRAH's chilled-water loop hangs off the DC-wide CHW headers,
        # NOT the chiller directly: it draws cold water from the CHW SUPPLY header
        # (SENS-<dc>-CHWS) and dumps warm water into the CHW RETURN header
        # (SENS-<dc>-CHWR) — the same two nodes the curated CRAHs connect to. The
        # chiller/pumps already sit behind those headers, so this is where a new
        # CRAH joins the loop.
        _dc = srv_tmpl.datacenter or ""
        crahs = [d for d in self.s.device_manager.get_all_devices()
                 if d.device_type == DeviceType.CRAH and self._room_key(d) == rk]
        sensor_tmpl = next((d for d in self.s.device_manager.get_all_devices()
                            if d.device_type == DeviceType.SENSOR
                            and self._room_key(d) == rk), None)

        def _chw_header(code: str):
            # Header sensors lead with their role code (e.g. 'CHWS-DC1-CP').
            return next((d for d in self.s.device_manager.get_all_devices()
                         if d.device_type == DeviceType.SENSOR
                         and (d.datacenter or "") == _dc
                         and (d.name or "").upper().split("-", 1)[0] == code), None)
        chw_supply = _chw_header("CHWS")
        chw_return = _chw_header("CHWR")
        return {"srv_tmpl": srv_tmpl, "leaf_tmpl": leaf, "oob": oob,
                "spines": spines, "rpp_a": rpp_a, "rpp_b": rpp_b,
                "pdu_tmpl": self._dc_pdu(_dc),
                "crahs": crahs, "sensor_tmpl": sensor_tmpl,
                "chw_supply": chw_supply, "chw_return": chw_return}

    @staticmethod
    def _yk(y) -> float:
        """Row-position key: round floor_y so curated and fleet racks on the same
        physical row hash to one slot regardless of tiny float differences."""
        return round(float(y), 2)

    def _next_compute_slot(self, rk: tuple):
        """The next free compute slot in hall *rk*, scanned ROW-MAJOR: fill every
        free rack in a compute row (front to back) BEFORE opening the next row —
        so freed in-row gaps (e.g. the slots vacated by moving RPPs to the network
        row) fill first, like a real hall packs a row before starting the next.
        Returns (rack_row_label, rack_num, coords) or None when the compute grid
        is full. Placing into a curated row uses that row's real rack_row; a new
        row behind gets a stable synthetic label."""
        rpr, comp_rows, _first_row, n_rows = self._hall_grid(rk)
        servers = [s for s in self._servers() if self._room_key(s) == rk]
        if not servers:
            return None
        # Occupied physical slots — ANY rack-occupying device (server, leaf, and
        # crucially RPP/EV2 racks so a compute rack is never placed on top of a
        # power rack), keyed by (row, num).
        occ = {(self._yk(d.floor_y), d.rack_num or 0)
               for d in self.s.device_manager.get_all_devices()
               if self._room_key(d) == rk and d.floor_y is not None
               and (d.rack_num or 0) >= 1}
        # Curated compute rows first (their real rack_row + floor_y), front to back.
        cur_rows: dict = {}
        for s in servers:
            if (s.rack_row or 0) < self._FLEET_ROW_BASE and s.floor_y is not None:
                cur_rows.setdefault(self._yk(s.floor_y), s.rack_row)
        rows: list = sorted(cur_rows.items())        # [(fy, rack_row)]
        # Then extend behind the back-most curated row with new fleet rows, up to
        # the hall's compute-row count (stop before the CRAH back wall).
        back = max(cur_rows) if cur_rows else None
        if back is not None:
            for k in range(1, comp_rows - len(rows) + 1):
                fy = round(back + geo.ROW_PITCH * k, 4)
                if fy >= geo.row_y(n_rows) - 1e-6:
                    break                            # would hit the CRAH perimeter
                rows.append((fy, None))              # synthetic label assigned on use
        for fy, rack_row in rows:
            for num in range(1, rpr + 1):
                if (self._yk(fy), num) in occ:
                    continue                         # slot taken
                i = max(1, int(round((fy - geo.row_y(1)) / geo.ROW_PITCH)) + 1)
                hot, cold, facing = geo.row_aisles(i)
                if rack_row is None:                 # new row behind curated compute
                    rack_row = self._row_label(rk, ("y", self._yk(fy)))
                return rack_row, num, (geo.rack_x(num), round(fy, 4), hot, cold, facing)
        return None

    def _fill_hall_grid(self, summ: DaySummary) -> Optional[dict]:
        """Add the next compute rack to a hall that's still under its grid cap.
        Most-occupied hall first, so one hall fills before the next is touched.
        Racks fill ROW-MAJOR (each compute row packs full before the next opens),
        so freed in-row gaps are used before a new row is started. The cap
        (racks_per_row x compute_rows) and row width come from each hall's PHYSICAL
        extent, so a hall fills to its real floor capacity before a new hall opens."""
        racks = self._hall_compute_racks()
        for rk in sorted(racks, key=lambda k: (-len(racks[k]), tuple(map(str, k)))):
            rpr, comp_rows, _first_row, _n_rows = self._hall_grid(rk)
            if len(racks[rk]) >= rpr * comp_rows:
                continue                              # hall full to its cap
            infra = self._hall_infra(rk)
            if infra is None:
                continue
            # Fabric limit: a new rack means a new leaf, which needs a free downlink
            # on every spine. If the pod's spine fabric is full, this hall is done —
            # skip it so a new hall (its own pod) is opened instead.
            if not self._spines_have_room(infra.get("spines")):
                continue
            slot = self._next_compute_slot(rk)
            if slot is None:
                continue                              # grid full (hit CRAH wall)
            rack_row, num, coords = slot
            return self._build_compute_rack(rk, rack_row, num, infra, summ, 0, coords)
        return None

    # ── CRAH provisioning (install a hall's full complement, sized to load) ──────
    def _hall_crah_target(self, rk: tuple) -> int:
        """How many CRAHs a hall needs for its ULTIMATE rack load (grid capacity ×
        design rack kW) at N+1 — the count installed up front so the hall is cooled
        for capacity, not just its current occupancy."""
        from core.cooling_model import crah_count_for
        rpr, comp_rows, _first, _n = self._hall_grid(rk)
        ult_it_kw = max(1, rpr) * max(1, comp_rows) * self._DESIGN_RACK_KW
        return crah_count_for(ult_it_kw)

    def _crah_perimeter_positions(self, ext: dict, rpr: int, n_rows: int,
                                  target: int) -> list:
        """(fx, fy) for *target* CRAHs lined along the hall's BACK wall (behind
        the last IT row), evenly spread across the width — the curated Hall A
        layout. The front wall can't hold CRAHs (Row 1 sits there in a network
        hall, and a unit off the front wall pokes past it); the long side walls
        are blocked by full-width rack rows. Halls are wide enough that all
        `target` units fit one back wall. Kept in lock-step with
        tools/seed_hall_crahs.perimeter_positions()."""
        width = float(ext.get("width_m") or (rpr * geo.RACK_PITCH + 2 * geo.rack_x(1)))
        back_y = round(geo.row_y(n_rows), 4)            # back wall, from geometry
        return [(round(width * (j + 0.5) / target, 4), back_y) for j in range(target)]

    def _ensure_hall_crahs(self, rk: tuple, infra: Optional[dict] = None) -> list:
        """Install the hall's FULL CRAH complement (top up to _hall_crah_target),
        spread along the back wall and wired into the CHW loop like the curated
        CRAHs. Idempotent — adds only the shortfall. All CRAHs run (VFD-modulated on
        each hall's own inlet temp); none are staged off, so coverage is even and the
        many-slow-fans cube-law keeps part-load fan energy low.

        The curated halls now SEED their full complement in the topology JSON (see
        tools/seed_hall_crahs.py), so for them len(existing) >= target and this is a
        no-op. It still runs for halls the fleet OPENS at runtime, which cannot be
        pre-seeded — those get their complement built here on first fill."""
        dc, floor, room = rk
        infra = infra or self._hall_infra(rk)
        if infra is None:
            return []
        existing = [d for d in self.s.device_manager.get_all_devices()
                    if d.device_type == DeviceType.CRAH and self._room_key(d) == rk]
        target = self._hall_crah_target(rk)
        if len(existing) >= target:
            return existing
        tmpls = (infra.get("crahs") or existing
                 or self._by_type(DeviceType.CRAH))
        if not tmpls:
            return existing
        rpr, _cr, _first, n_rows = self._hall_grid(rk)
        ext = self._hall_extent(rk) or {}
        # Distribute the complement across the two free END walls (front + back),
        # matching tools/seed_hall_crahs.py — the long side walls are blocked by
        # full-width rack rows, so front+back is the realizable perimeter and it
        # halves per-wall density vs. lining one wall.
        positions = self._crah_perimeter_positions(ext, rpr, n_rows, target)
        chw_supply = infra.get("chw_supply")
        chw_return = infra.get("chw_return")
        # CRAHs are bulk mechanical loads, fed from the hall's own Mechanical Power
        # Panel — a panelboard in the room, downstream of an MCC and upstream of no
        # UPS. Without this the unit is unpowered. Resolved by ROOM so it survives
        # renames. New CRAHs alternate across the hall's A/B panels so a lost source
        # thins the air side evenly instead of killing one end of the room. Falls
        # back to the MCCs for a topology built before the hall panels existed.
        mccs = sorted((d for d in self.s.device_manager.get_all_devices()
                       if d.device_type == DeviceType.MPP and (d.datacenter or "") == dc
                       and (d.room or "") == room),
                      key=lambda d: d.name or "")
        if not mccs:
            mccs = sorted((d for d in self.s.device_manager.get_all_devices()
                           if d.device_type == DeviceType.MCC and (d.datacenter or "") == dc
                           and (d.room or "") == "Mechanical Room"),
                          key=lambda d: d.name or "")
        unit = int(getattr(tmpls[0], "rack_unit", 1) or 1)
        added = list(existing)
        for i in range(len(existing), target):
            cx, cy = positions[i]
            c = self._clone(tmpls[i % len(tmpls)], dc, self._row_label(rk, "crah"),
                            200 + i, unit, prefix="crah", floor=floor, room=room,
                            fx=cx, fy=cy)
            if c is None:
                continue
            try:
                if chw_supply is not None:
                    self.s.topology.add_link(chw_supply.id, c.id, layer="cooling")
                if chw_return is not None:
                    self.s.topology.add_link(c.id, chw_return.id, layer="cooling")
                if mccs:
                    self.s.topology.add_link(mccs[i % len(mccs)].id, c.id, layer="power")
            except Exception:
                pass
            self._commission(c)
            added.append(c)
        if added != existing:
            self._log(f"[Fleet] hall {dc}/{room} CRAHs {len(existing)}→{len(added)} "
                      f"(sized to ~{self._hall_crah_target(rk)} for capacity)")
        return added

    def _ensure_all_hall_crahs(self) -> None:
        """Top every hall that holds servers up to its full CRAH complement, once."""
        for rk in {self._room_key(d) for d in self._servers()}:
            if rk in self._crah_ensured:
                continue
            try:
                self._ensure_hall_crahs(rk)
            except Exception as e:
                self._log(f"[Fleet] CRAH provision {rk}: {e}")
            self._crah_ensured.add(rk)

    def _open_new_hall(self, summ: DaySummary) -> Optional[dict]:
        """Commission a brand-new server hall in the busiest DC, built to look
        like the curated halls: it CLONES the source hall's floor-plan extent (so
        it is the same physical size/shape), lays its power + pod-network gear on
        the front row, spreads its CRAHs along the back wall, and puts the first
        compute rack in the first middle row. Subsequent provisions fill the
        middle-row grid via _fill_hall_grid — the extent-derived cap fills the
        hall to capacity before another new hall opens."""
        dc = self._busiest_dc()
        if dc is None:
            return None
        src_rk = self._busiest_hall(dc)
        if src_rk is None:
            return None
        infra = self._hall_infra(src_rk)
        if infra is None or infra["rpp_a"] is None:
            return None
        floor = self._next_floor(dc)
        room = self._next_hall_name(dc)
        rk = (dc, floor, room)
        # Clone the SOURCE curated hall's extent so the new hall matches its
        # physical dimensions/rows/aisles (not a config-sized box). Fall back to
        # a config-derived extent only if the source hall has none.
        src_ext = self._hall_extent(src_rk)
        if src_ext:
            self._register_hall_extent_copy(dc, room, src_ext)
        else:
            self._register_hall_extent(dc, room, back_rows=2)
        rpr, comp_rows, first_row, n_rows = self._hall_grid(rk)
        ext = self._hall_extent(rk) or {}
        width_m = float(ext.get("width_m") or (rpr * geo.RACK_PITCH + 2 * geo.rack_x(1)))
        chw_supply = infra.get("chw_supply")
        chw_return = infra.get("chw_return")
        new_infra = dict(infra)

        # ── Front row (row 1): power + pod network, like the curated halls ──
        # Fresh RPP pair (fed by the source RPPs' UPS so hall load still reaches
        # the UPS/generator) at the near wall, then the pod's OWN spine set + OOB
        # (own fabric = meaningful spine/OOB port caps) across the rest of the row.
        front_y = round(geo.row_y(1), 4)
        new_infra["rpp_a"] = self._clone_rpp(infra["rpp_a"], rk, "A", front_y)
        new_infra["rpp_b"] = self._clone_rpp(infra["rpp_b"], rk, "B", front_y) if infra["rpp_b"] else None
        # Every RPP gets its OWN EV2-42 meter (like the curated halls), so a new
        # hall's power is monitored from day one — not only spill panels.
        for _rpp in (new_infra["rpp_a"], new_infra["rpp_b"]):
            if _rpp is not None:
                self._provision_ev2_for_rpp(_rpp, rk)
        fabric = [(sp, "sp") for sp in (infra.get("spines") or [])]
        if infra.get("oob") is not None:
            fabric.append((infra["oob"], "oob"))
        new_spines, new_oob = [], None
        for i, (tmpl, pfx) in enumerate(fabric):
            fx = geo.rack_x(min(3 + i, rpr))          # cols 1-2 are the RPP pair
            c = self._clone_fabric_node(tmpl, rk, pfx, num=100 + i, fx=fx, fy=front_y)
            if c is None:
                continue
            self._commission(c)
            if pfx == "sp":
                new_spines.append(c)
            else:
                new_oob = c
        if new_spines:
            new_infra["spines"] = new_spines
        if new_oob is not None:
            new_infra["oob"] = new_oob

        # ── Back wall (row n_rows): install the hall's FULL CRAH complement, sized
        # to its ULTIMATE rack load (N+1) — not a fixed clone count — spread along
        # the back wall and wired into the CHW loop. All run VFD-modulated.
        new_crahs = self._ensure_hall_crahs(rk, infra=infra)
        self._crah_ensured.add(rk)

        # Environmental probes spread across the first cold aisle.
        sen_tmpl = infra.get("sensor_tmpl")
        if sen_tmpl is not None:
            sy = round(0.6 + geo.ROW_PITCH, 4)
            for i in range(self._HALL_SENSORS):
                sx = round(width_m * (i + 0.5) / max(1, self._HALL_SENSORS), 4)
                c = self._clone(sen_tmpl, dc, self._row_label(rk, "sen"), 300 + i,
                                int(getattr(sen_tmpl, "rack_unit", 1) or 1),
                                prefix="sen", floor=floor, room=room, fx=sx, fy=sy)
                if c is not None:
                    self._commission(c)

        self._fleet_halls.add(rk)
        self._log(f"[Fleet] opened new hall {dc}/F{floor}/{room} "
                  f"(grid {rpr}x{comp_rows}, {len(new_crahs)} back-wall CRAH; "
                  f"cloned from {src_rk[2]})")
        coords = self._rack_coords(rk, 0, 1, first_row, n_rows)
        return self._build_compute_rack(rk, self._row_label(rk, 0), 1, new_infra, summ, 0, coords)

    def _register_hall_extent_copy(self, dc: str, room: str, src_ext: dict) -> None:
        """Register a fleet hall's floor-plan extent as a COPY of the source
        curated hall's, so the new hall has identical physical dimensions, rows
        and aisles — only the datacenter/room labels change."""
        fp = getattr(self.s.topology, "floorplan", None)
        if not isinstance(fp, dict):
            return
        ext = {k: (list(v) if isinstance(v, list) else v) for k, v in src_ext.items()}
        ext.update({"datacenter": dc, "room": room,
                    "class": "white_space", "containment": "cold_aisle"})
        fp.setdefault("rooms", {})[f"{dc}/{room}"] = ext

    def _register_hall_extent(self, dc: str, room: str, back_rows: int = 1,
                              side_lanes: int = 0) -> None:
        """Add a floorplan room extent for a fleet-created hall so the static
        floor-plan (and Save Topology export) draws the room box + aisles, not just
        loose racks. Grid = compute_rows_per_room compute rows + *back_rows* rows for
        the RPP and the pod's network gear (spines/OOB), matching where
        _build_compute_rack / _clone_rpp / the fabric clones place them.
        *side_lanes* widens the room by that many rack columns for the perimeter
        CRAH lanes on the side walls. No-op if the topology carries no floorplan
        block."""
        fp = getattr(self.s.topology, "floorplan", None)
        if not isinstance(fp, dict):
            return
        n_rows = max(1, self.cfg.compute_rows_per_room) + max(1, back_rows)
        ext = geo.hall_extent(n_rows, max(1, self.cfg.max_racks_per_row) + side_lanes)
        ext.update({"datacenter": dc, "room": room,
                    "class": "white_space", "containment": "cold_aisle"})
        fp.setdefault("rooms", {})[f"{dc}/{room}"] = ext

    def _busiest_dc(self) -> Optional[str]:
        counts: dict[str, int] = {}
        for s in self._servers():
            counts[s.datacenter or ""] = counts.get(s.datacenter or "", 0) + 1
        if not counts:
            return None
        return max(sorted(counts), key=lambda d: counts[d])

    def _busiest_hall(self, dc: str) -> Optional[tuple]:
        counts: dict = {}
        for s in self._servers():
            if (s.datacenter or "") == dc:
                counts[self._room_key(s)] = counts.get(self._room_key(s), 0) + 1
        if not counts:
            return None
        return max(sorted(counts), key=lambda k: counts[k])

    def _next_floor(self, dc: str) -> str:
        nums = []
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "") != dc:
                continue
            f = str(d.floor or "").strip()
            if f.isdigit():
                nums.append(int(f))
        return str((max(nums) if nums else 0) + 1)

    def _next_hall_name(self, dc: str) -> str:
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

    def _clone_rpp(self, tmpl: Device, rk: tuple, side: str,
                   y: Optional[float] = None) -> Optional[Device]:
        """Clone a Remote Power Panel into hall *rk* and feed it from the same UPS
        the template draws from, so downstream rack load reaches the UPS."""
        dc, floor, room = rk
        ups = self._neighbor(tmpl, "power", (DeviceType.UPS,))
        rpp = self._clone(tmpl, dc, self._row_label(rk, "rpp"),
                          1 if side == "A" else 2, PDU_UNIT,
                          prefix=f"rpp{side.lower()}", floor=floor, room=room,
                          fx=geo.rack_x(1 if side == "A" else 2), fy=y)
        if rpp is None:
            return None
        if ups is not None:
            try:
                self.s.topology.add_link(ups.id, rpp.id, layer="power")
            except Exception:
                pass
        self._commission(rpp)
        return rpp

    # ── RPP pole capacity + spill ────────────────────────────────────────────
    # A real RPP is a panelboard with a FIXED pole count — you cannot clamp more
    # branch PDUs onto it than it has poles (and its EV2 has one CT per pole). When
    # a panel fills, the datacenter adds ANOTHER RPP (+ its own EV2-42) rather than
    # overloading one. _RPP_POLES matches the curated 42-circuit RPP class.
    _RPP_POLES = 42

    @staticmethod
    def _rpp_side(rpp: Device) -> str:
        """A- or B-side of a dual-corded feed, from the RPP name (curated
        'RPP-IT-DC1-A1' / fleet 'DC1-RPPA…')."""
        nm = (rpp.name or "").upper()
        if "RPPB" in nm or "-B" in nm:
            return "B"
        return "A"

    def _pdus_on_rpp(self, rpp: Device) -> int:
        """Branch rack-PDUs already fed from this RPP over the power layer."""
        n = 0
        try:
            for nbr in self.s.topology.graph.neighbors(rpp.id):
                d = self.s.device_manager.get_device(nbr)
                if d and d.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU):
                    n += 1
        except Exception:
            pass
        return n

    def _side_rpps(self, rk: tuple, side: str, primary: Device) -> list:
        """Every RPP on *side* in hall *rk* (primary first, then any spills)."""
        out = [primary]
        for d in self.s.device_manager.get_all_devices():
            if (d.device_type == DeviceType.RPP and d.id != primary.id
                    and self._room_key(d) == rk and self._rpp_side(d) == side):
                out.append(d)
        return out

    def _rpp_with_capacity(self, rk: tuple, side: str, rpp: Device) -> Device:
        """Return an RPP on *side* that still has a free pole. If every existing
        panel is full (>= _RPP_POLES branch PDUs), provision a spill RPP + EV2-42
        and return that — the physical 'add another panel' path."""
        for r in self._side_rpps(rk, side, rpp):
            if self._pdus_on_rpp(r) < self._RPP_POLES:
                return r
        return self._spill_rpp(rk, side, rpp) or rpp

    def _spill_rpp(self, rk: tuple, side: str, tmpl_rpp: Device) -> Optional[Device]:
        """Clone a new RPP on *side* (fed from the same UPS as the full panel),
        provision its EV2-42 meter, and return it. Co-located with the template in
        the power room (0U panels — no compute-grid placement to collide with)."""
        dc, floor, _room = rk
        ups = self._neighbor(tmpl_rpp, "power", (DeviceType.UPS,))
        # Co-locate the spill with its TEMPLATE panel (same row AND rack), not a
        # hardcoded rack 1/2 — a curated hall's IT RPP sits at e.g. rack 4 (A) / 9
        # (B) in the network row, so stamping the spill at rack 1/2 dropped a 0U
        # panel onto the front-of-row compute/spine grid cells and the floorplan
        # then drew Rack 1/Rack 2 as RPP. Inheriting the template's rack_num keeps
        # the spill stacked with the panel it relieves, off the compute racks.
        rpp = self._clone(tmpl_rpp, dc, getattr(tmpl_rpp, "rack_row", 1) or 1,
                          getattr(tmpl_rpp, "rack_num", 1) or 1, PDU_UNIT,
                          prefix=f"rpp{side.lower()}", floor=floor,
                          room=getattr(tmpl_rpp, "room", None),
                          fx=getattr(tmpl_rpp, "floor_x", None),
                          fy=getattr(tmpl_rpp, "floor_y", None))
        if rpp is None:
            return None
        if ups is not None:
            try:
                self.s.topology.add_link(ups.id, rpp.id, layer="power")
            except Exception:
                pass
        self._commission(rpp)
        self._provision_ev2_for_rpp(rpp, rk)
        self._log(f"[Fleet] RPP {tmpl_rpp.name} full ({self._RPP_POLES} poles) — "
                  f"spilled {rpp.name} (+EV2) in {dc}/{getattr(tmpl_rpp,'room','')}")
        return rpp

    def _provision_ev2_for_rpp(self, rpp: Device, rk: tuple) -> Optional[Device]:
        """Clone an EV2-42 meter, clamp it onto *rpp* over the power layer (one CT
        per branch, exactly as curated add_ev2_monitors wires it), size the model to
        the panel's poles, and commission it (hot-adds to a running BACnet BMS)."""
        tmpl = next((d for d in self.s.device_manager.get_all_devices()
                     if d.device_type == DeviceType.ENERGY_MONITOR), None)
        if tmpl is None:
            self._log("[Fleet] no EV2 template to clone — spill RPP has no meter")
            return None
        dc, floor, _room = rk
        ev2 = self._clone(tmpl, dc, getattr(rpp, "rack_row", 1) or 1,
                          getattr(rpp, "rack_num", 1) or 1, 0,
                          prefix="ev2", floor=floor,
                          room=getattr(rpp, "room", None),
                          fx=getattr(rpp, "floor_x", None),
                          fy=getattr(rpp, "floor_y", None))
        if ev2 is None:
            return None
        try:
            self.s.topology.add_link(ev2.id, rpp.id, layer="power")
        except Exception:
            pass
        try:
            self.s.device_manager.update_device(
                ev2.id, model_name=f"Verdigris EV2-{self._RPP_POLES}")
        except Exception:
            pass
        self._commission(ev2)
        return ev2

    def _build_compute_rack(self, rk: tuple, row: int, num: int, infra: dict,
                            summ: DaySummary, vrow: int,
                            coords: Optional[tuple] = None) -> Optional[dict]:
        """Materialise a compute rack (leaf + dual rack PDUs) in hall *rk*, fully
        wired: leaf → every spine + the OOB switch; each PDU → an RPP feed so
        server load reaches the UPS. Placed on the floor grid at *coords*
        (fx, fy, hot, cold, facing) — precomputed by the caller from the hall's
        extent; falls back to _rack_coords when not supplied."""
        dc, floor, room = rk
        fx, fy, hot, cold, facing = coords or self._rack_coords(rk, vrow, num)
        leaf = self._clone(infra["leaf_tmpl"], dc, row, num, TOR_A_UNIT,
                           prefix="tor", floor=floor, room=room,
                           fx=fx, fy=fy, hot=hot, cold=cold, facing=facing)
        if leaf is None:
            return None
        self.s.device_manager.update_device(
            leaf.id,
            interface_groups=leaf_interface_groups(leaf.model_name or "",
                                                   leaf.interface_count),
            mlag_ready=True, mlag_peer_unit=TOR_B_UNIT)
        try:
            for sp in infra.get("spines") or []:
                self.s.topology.add_link(leaf.id, sp.id, layer="production")
            # OOB with a free port (stacks a new OOB into the hall if full).
            oob = self._oob_for_new_leaf(rk, infra)
            if oob is not None:
                self.s.topology.add_link(leaf.id, oob.id, layer="management")
        except Exception as e:
            self._log(f"[Fleet] leaf uplink {leaf.name}: {e}")

        pdus = []
        for side, rpp in (("A", infra.get("rpp_a")), ("B", infra.get("rpp_b"))):
            if infra.get("pdu_tmpl") is None:
                break
            pdu = self._clone(infra["pdu_tmpl"], dc, row, num, PDU_UNIT,
                              prefix=f"pdu{side.lower()}", floor=floor, room=room,
                              fx=fx, fy=fy, hot=hot, cold=cold, facing=facing)
            if pdu is None:
                continue
            # The cloned PDU inherits the curated DC rack-PDU's real nameplate
            # rating (via _clone → the SKU catalog, e.g. 22 kW for an AP8865), so
            # fleet racks and curated racks agree on PDU capacity. That fixed
            # nameplate is what the live power model measures load% against — no
            # per-rack override needed (the old budget÷0.8 override made fleet
            # racks disagree with curated ones and inverted the budget→PDU link).
            if rpp is not None:                       # PDU drinks from the RPP feed
                # Honour the panel's pole limit: if this RPP is full, spill onto a
                # fresh RPP (+its own EV2-42) instead of overloading it.
                rpp = self._rpp_with_capacity(rk, side, rpp)
                try:
                    self.s.topology.add_link(rpp.id, pdu.id, layer="power")
                except Exception:
                    pass
            self._commission(pdu)
            pdus.append(pdu)

        self._commission(leaf)
        summ.expanded_racks.append(f"{dc}:{room}:R{row}:RACK{num}")
        self._log(f"[Fleet] new rack {dc}/F{floor}/{room} R{row} RACK{num} (leaf+{len(pdus)}PDU)")
        return {"key": (dc, row, num), "floor": floor, "room": room,
                "tor": leaf, "pdus": pdus, "server_tmpl": infra["srv_tmpl"],
                "fx": fx, "fy": fy, "hot": hot, "cold": cold, "facing": facing}

    def _dc_pdu(self, dc: str) -> Optional[Device]:
        """A rack PDU in *dc* to clone the new COMPUTE rack's PDU from. Must be a
        compute-rack PDU (one feeding a server), not a lightly-rated network/OOB-
        rack PDU — otherwise the fleet clones an undersized PDU (e.g. an 8.6 kW
        AP8941) and every fleet rack inherits that low nameplate, collapsing the
        per-rack power budget. Picks the highest-rated compute-rack PDU in the DC;
        falls back to any PDU if the DC has no compute rack yet."""
        pdu_types = (DeviceType.PDU, DeviceType.FLOOR_PDU)
        server_racks = {self._rack_key(d) for d in self._servers()
                        if (d.datacenter or "") == dc}
        cands = [d for d in self.s.device_manager.get_all_devices()
                 if d.device_type in pdu_types and (d.datacenter or "") == dc
                 and self._rack_key(d) in server_racks]
        if cands:
            return max(cands, key=lambda p: int(getattr(p, "rated_power_w", 0) or 0))
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "") == dc and d.device_type in pdu_types:
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

    def _dc_bounds(self, dc: str) -> tuple:
        """(x0, x1, y_bottom) of *dc*'s CURATED nodes on the topology canvas,
        snapshotted once. Defines the band the fleet lays its nodes into so each
        DC stays in its own column and never overlaps the other."""
        if dc in self._dc_bounds_cache:
            return self._dc_bounds_cache[dc]
        # X-band: from the DC's SERVER cluster only — facility/power nodes can sit
        # at odd coords (x=0) and would bleed one DC's band into the other.
        # Y-floor: the LOWEST point of ANY curated node in the DC (CDU, OOB,
        # sensors, RPP/PDU all sit below the servers), so the fleet grid starts
        # clear of them instead of growing down into them.
        xs, all_y = [], []
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "") != dc:
                continue
            x, y = self.s.topology.get_position(d.id)
            all_y.append(y)
            if d.device_type == DeviceType.SERVER:
                xs.append(x)
        x0, x1 = (min(xs), max(xs)) if xs else (0.0, 1200.0)
        y_floor = max(all_y) if all_y else 1200.0
        b = (x0, x1, y_floor)
        self._dc_bounds_cache[dc] = b
        return b

    # Clear vertical gap between the lowest curated node and the first fleet row.
    _FLEET_Y_GAP = 220.0

    def _fleet_pos(self, dc: str) -> tuple:
        """Next canvas slot for a fleet node in *dc*'s band: a grid spanning the
        DC's server x-width, starting a clear gap BELOW every curated node and
        growing down. Monotone, so nodes never overlap each other, the curated
        CDU/OOB/power rows, or the other DC."""
        x0, x1, y_floor = self._dc_bounds(dc)
        step = 46.0
        cols = max(1, int(max(200.0, x1 - x0) // step))
        n = self._dc_placed.get(dc, 0)
        self._dc_placed[dc] = n + 1
        return (x0 + (n % cols) * step, y_floor + self._FLEET_Y_GAP + (n // cols) * 38.0)

    def _used_ips(self) -> set:
        """Every production + management IP currently in use, so a new device
        never collides regardless of which pool it draws from."""
        used: set = set()
        for d in self.s.device_manager.get_all_devices():
            if d.ip_address:
                used.add(d.ip_address)
            m = getattr(d, "mgmt_ip", "")
            if m:
                used.add(m)
        return used

    @staticmethod
    def _first_free_host(net, used: set) -> str:
        """Lowest host address in *net* not already in *used*. '' if the subnet is
        fully consumed."""
        for host in net.hosts():
            s = str(host)
            if s not in used:
                return s
        return ""

    @classmethod
    def _alloc_in_subnet(cls, ref_ip: str, prefix: int, used: set) -> str:
        """Lowest free host in the subnet that *ref_ip* belongs to (e.g. the prod
        10.50.0.0/16 or a DC's mgmt 192.168.4.0/22). Derives the network from a
        template peer so fleet devices share the curated addressing — not a
        mis-seeded global pool. '' if ref_ip is blank or the subnet is full."""
        if not ref_ip:
            return ""
        import ipaddress
        try:
            net = ipaddress.ip_network(f"{ref_ip}/{prefix}", strict=False)
        except ValueError:
            return ""
        return cls._first_free_host(net, used)

    # Fleet mgmt addressing. Each DC's curated mgmt pool is a /22 (1022 hosts:
    # e.g. DC1 192.168.0.0/22, DC2 192.168.4.0/22) — undersized for a fleet that
    # grows past ~1000 devices. When a DC's base /22 fills, spill into overflow
    # /22 blocks higher in the enclosing /16. Blocks are striped by lane so two
    # DCs' overflow pools never interleave into the same /22.
    _MGMT_PREFIX = 22
    _MGMT_LANES  = 8          # supports up to 8 curated DC mgmt bases (idx 0..7)

    @classmethod
    def _alloc_mgmt(cls, ref_ip: str, used: set) -> tuple:
        """Allocate a mgmt IP for a fleet device from the same /22 as its template
        peer; on exhaustion, spill into that DC's overflow lane in free space
        higher in the /16. Returns (ip, note): note is 'primary' (base /22),
        'overflow' (spilled block), or '' (ref_ip blank / /16 fully consumed)."""
        if not ref_ip:
            return "", ""
        import ipaddress
        try:
            base = ipaddress.ip_network(f"{ref_ip}/{cls._MGMT_PREFIX}", strict=False)
            sup  = ipaddress.ip_network(f"{ref_ip}/16", strict=False)
        except ValueError:
            return "", ""
        # 1) Base /22 — the curated pool this DC's peers already use.
        ip = cls._first_free_host(base, used)
        if ip:
            return ip, "primary"
        # 2) Overflow. Stripe /22 blocks in the /16 by lane: the first _MGMT_LANES
        #    blocks (idx 0..7) are reserved as DC bases; overflow starts past them.
        block   = 1 << (32 - cls._MGMT_PREFIX)          # hosts per /22 = 1024
        blocks  = 1 << (cls._MGMT_PREFIX - 16)          # /22 blocks per /16 = 64
        sup_a   = int(sup.network_address)
        lane    = ((int(base.network_address) - sup_a) // block) % cls._MGMT_LANES
        idx     = cls._MGMT_LANES + lane
        while idx < blocks:
            net = ipaddress.ip_network((sup_a + idx * block, cls._MGMT_PREFIX))
            ip  = cls._first_free_host(net, used)
            if ip:
                return ip, "overflow"
            idx += cls._MGMT_LANES
        return "", ""      # /16 fully consumed — genuinely out of mgmt space

    def _unique_name(self, base: str) -> str:
        names = {d.name for d in self.s.device_manager.get_all_devices()}
        while True:
            self._seq += 1
            cand = f"{base}-{self._seq:04d}"
            if cand not in names:
                return cand

    def _next_srv_name(self, dc: str) -> str:
        """Curated server-name style: DC2-SRV027 — DC + 'SRV' + a zero-padded
        number continuing past the highest existing one (no dash before it)."""
        prefix = f"{dc}-SRV"
        names, mx = set(), 0
        for d in self.s.device_manager.get_all_devices():
            nm = d.name or ""
            names.add(nm)
            if nm.startswith(prefix):
                tail = nm[len(prefix):].lstrip("-")   # tolerate old DC2-SRV-0021
                if tail.isdigit():
                    mx = max(mx, int(tail))
        n = mx + 1
        while f"{dc}-SRV{n:03d}" in names:
            n += 1
        return f"{dc}-SRV{n:03d}"

    def _seq_name(self, tmpl_name: str, dc: str) -> Optional[str]:
        """Continue a curated device's numbering so a fleet clone reads like its
        peers. Splits *tmpl_name* into a stem + trailing counter ('DC1-LF01' ->
        stem 'DC1-LF', width 2), rewrites the DC token to *dc* (so a cross-DC
        fallback template still lands in the right DC), then returns the stem +
        (highest existing counter with that stem + 1), zero-padded to the
        template's width. Returns None when the name has no continuable counter —
        a singleton like 'RPP-MECH-DC1' or 'EV2-COOL-DC1' whose only digits are
        the DC id — so the caller can fall back."""
        m = re.match(r"^(.*?)(\d+)$", tmpl_name or "")
        if not m:
            return None
        stem, num = m.group(1), m.group(2)
        if dc and re.search(r"DC\d+", stem):
            stem = re.sub(r"DC\d+", dc, stem, count=1)
        if stem.endswith("DC"):        # the trailing number WAS the DC ordinal
            return None
        width = len(num)
        pat = re.compile(r"^" + re.escape(stem) + r"0*(\d+)$")
        mx, names = 0, set()
        for d in self.s.device_manager.get_all_devices():
            nm = d.name or ""
            names.add(nm)
            mm = pat.match(nm)
            if mm:
                mx = max(mx, int(mm.group(1)))
        n = mx + 1
        cand = f"{stem}{n:0{width}d}"
        while cand in names:
            n += 1
            cand = f"{stem}{n:0{width}d}"
        return cand

    @staticmethod
    def _hall_code(room: Optional[str]) -> str:
        """DCIM hall abbreviation used in rack-PDU names: 'Server Hall C' -> 'SHC'
        (matches the curated 'PDU-DC1-SHA-…' style). Falls back to the room's word
        initials for any non-'Server Hall' room."""
        r = (room or "").strip()
        parts = r.split()
        if r.lower().startswith("server hall ") and len(parts) >= 3:
            return "SH" + parts[-1].upper()
        letters = "".join(w[0] for w in re.findall(r"[A-Za-z]+", r))[:4].upper()
        return letters or "HALL"

    def _row_rank(self, dc: str, floor, room: Optional[str], row: int) -> int:
        """1-based physical row index of rack_row *row* within hall (dc, floor,
        room), ordered front-to-back. Curated rows (small ints) rank ahead of
        fleet rows (synthetic >= _FLEET_ROW_BASE), matching the fleet-lays-behind-
        curated floor layout — so a fleet rack's PDU name carries a sensible R#."""
        key = (dc or "", str(floor or ""), room or "")
        rows = {int(row)}
        for d in self.s.device_manager.get_all_devices():
            if (d.datacenter or "", str(d.floor or ""), d.room or "") == key:
                rr = d.rack_row
                if rr is not None:
                    rows.add(int(rr))
        return sorted(rows).index(int(row)) + 1

    def _pdu_name(self, dc: str, floor, room: Optional[str], row: int,
                  num: int, side: str) -> str:
        """Curated rack-PDU name for a fleet PDU: PDU-<DC>-<HALL>-R<row>-<rack>-<A|B>
        (e.g. 'PDU-DC2-SHC-R1-3-A'), where the row is the rack's physical row rank
        in the hall and the rack ordinal is its position within that row — the same
        location encoding the curated 'PDU-DC1-SHA-R1-5-A' names use."""
        hall = self._hall_code(room)
        rrank = self._row_rank(dc, floor, room, row)
        names = {d.name for d in self.s.device_manager.get_all_devices()}
        n = int(num)
        while f"PDU-{dc}-{hall}-R{rrank}-{n}-{side}" in names:
            n += 1
        return f"PDU-{dc}-{hall}-R{rrank}-{n}-{side}"

    def _next_free_unit(self, key: tuple) -> Optional[int]:
        """Next free rack unit for a SERVER_U_HEIGHT-U server, on the curated 2U
        cadence (servers sit on odd units 1,3,5… and occupy U..U+1). Returns None
        when the rack is space-full — all U1..U40 slots taken — so a rack fills to
        its real physical U capacity (~20 servers) instead of the old 1U cadence
        that packed to the power cap and stranded rack U. U41/U42 stay clear for
        the ToR pair."""
        used = {d.rack_unit for d in self._rack_devices(key) if d.rack_unit}
        u = FIRST_SERVER_UNIT
        while u <= LAST_SERVER_UNIT - (SERVER_U_HEIGHT - 1):
            if u not in used:
                return u
            u += SERVER_U_HEIGHT
        return None

    def _add_server(self, rack: dict) -> Optional[Device]:
        tmpl: Device = rack["server_tmpl"]
        dc, row, num = rack["key"]
        unit = self._next_free_unit(rack["key"])
        if unit is None:
            return None                       # rack U-space full (all 2U slots taken)
        # New halls/racks carry an explicit floor+room; filling an existing rack
        # leaves them None so the server inherits its same-rack template's hall.
        dev = self._clone(tmpl, dc, row, num, unit, prefix="srv",
                          floor=rack.get("floor"), room=rack.get("room"),
                          fx=rack.get("fx"), fy=rack.get("fy"), hot=rack.get("hot"),
                          cold=rack.get("cold"), facing=rack.get("facing"))
        if dev is None:
            return None
        # Wire it in: production uplink to the rack ToR, dual A/B power feeds from
        # the rack PDUs — the power-layer edges are what the live load cascade
        # follows up to the RPP/UPS, so this is what makes new IT load show on the
        # upstream power meters.
        pdus = rack.get("pdus") or ([rack["pdu"]] if rack.get("pdu") else [])
        try:
            self.s.topology.add_link(dev.id, rack["tor"].id, layer="production")
            for p in pdus:
                self.s.topology.add_link(dev.id, p.id, layer="power")
        except Exception as e:
            self._log(f"[Fleet] wiring {dev.name} failed: {e}")
        # Record the A/B feed ids too (DCIM/Redfish power-source view + redundancy
        # split); the cascade itself runs off the edges above.
        upd = {}
        if len(pdus) >= 1:
            upd["power_source_a"] = pdus[0].id
        if len(pdus) >= 2:
            upd["power_source_b"] = pdus[1].id
        if upd:
            self.s.device_manager.update_device(dev.id, **upd)
        self._commission(dev)   # bring it online on SNMP/gNMI/Redfish
        return dev

    def _scheme_name(self, dc: str, room: Optional[str], row, num,
                     code: str, sided: bool = False, pad: int = 1) -> str:
        """A device name in the unified scheme. *code* is the leading type token
        (e.g. 'SRV', 'LF', 'PDUA', 'CRAH'). Rack-room devices get an R<row>-<rack>
        location suffix; facility devices just <DC>-<ROOM>. A *sided* code (PDUA/
        RPPB) is used bare when free (one per rack per side); everything else gets
        the next free per-rack index, zero-padded to *pad* (servers → 2)."""
        rc = _room_code(room)
        loc = (f"{dc}-{rc}-R{int(row or 0)}-{int(num or 0):02d}"
               if _is_rack_room(room) else f"{dc}-{rc}")
        used = {d.name for d in self.s.device_manager.get_all_devices()}
        if sided:
            nm = f"{code}-{loc}"
            if nm not in used:
                return nm
        i = 1
        while True:
            nm = f"{code}{i:0{pad}d}-{loc}"
            if nm not in used:
                return nm
            i += 1

    def _clone(self, tmpl: Device, dc: str, row: int, num: int, unit: int,
               prefix: str, floor: Optional[str] = None,
               room: Optional[str] = None, fx: Optional[float] = None,
               fy: Optional[float] = None, hot: Optional[str] = None,
               cold: Optional[str] = None, facing: Optional[str] = None
               ) -> Optional[Device]:
        """Create a new device cloned from *tmpl*'s vendor/model/port profile,
        placed at the given rack location, and register it in manager+topology.
        *floor*/*room* override the template's hall (used when the rack lives in a
        new hall); *fx/fy/hot/cold/facing* set the floor-plan coordinates so the
        device renders in the right spot. Any left None inherits the template's
        value (so filling an existing rack copies its peers' placement)."""
        # Allocate from the SAME subnets as the template peer so fleet devices
        # share the curated addressing: prod in the production /16 (e.g.
        # 10.50.0.0/16), mgmt in the DC's mgmt /22 (e.g. 192.168.4.0/22). A
        # device with no prod IP (a 0U PDU) keeps it blank, like its curated peer.
        used = self._used_ips()
        ip = self._alloc_in_subnet(getattr(tmpl, "ip_address", "") or "", 16, used)
        if getattr(tmpl, "ip_address", "") and not ip:
            self._log("[Fleet] prod IP subnet exhausted")
            return None
        if ip:
            used.add(ip)
        tmpl_mgmt = getattr(tmpl, "mgmt_ip", "") or ""
        mgmt_ip, mnote = self._alloc_mgmt(tmpl_mgmt, used)
        if tmpl_mgmt and not mgmt_ip:
            # /16 fully consumed — surface it instead of silently shipping a device
            # with no mgmt IP (which then loses its Redfish/gNMI OOB bind).
            self._log("[Fleet] mgmt IP space exhausted — new device has no mgmt IP")
        elif mnote == "overflow" and not self._mgmt_overflow_warned:
            self._mgmt_overflow_warned = True
            self._log("[Fleet] mgmt base /22 full — spilling into overflow blocks")
        # Name in the unified scheme so a fleet device is indistinguishable from a
        # curated peer: <CODE>-<DC>-<ROOM>-R<row>-<rack> (see _scheme_name).
        dcn = dc or "DC"
        if prefix.startswith("pdu") or prefix.startswith("rpp"):
            base = "PDU" if prefix.startswith("pdu") else "RPP"
            code = base + ("B" if prefix.endswith("b") else "A")
            name = self._scheme_name(dcn, room, row, num, code, sided=True)
        else:
            code = _PREFIX_CODE.get(prefix, (prefix.upper() or "DEV").replace(" ", "-"))
            name = self._scheme_name(dcn, room, row, num, code,
                                     pad=(2 if prefix == "srv" else 1))
        try:
            dev = Device(
                name=name,
                device_type=tmpl.device_type,
                vendor=tmpl.vendor,
                ip_address=ip,
                mgmt_ip=mgmt_ip,
                model_name=tmpl.model_name,
                snmp_port=getattr(tmpl, "snmp_port", 161),
                gnmi_port=getattr(tmpl, "gnmi_port", 57400),
                interface_count=getattr(tmpl, "interface_count", 8),
                # Inherit the rack peer's nameplate draw so the fleet server's
                # watts match its rack profile and feed the power-budget cap +
                # the live power cascade consistently. (0 lets Device fill a
                # type/model default in __post_init__.)
                power_draw_w=int(getattr(tmpl, "power_draw_w", 0) or 0),
                # Inherit the template's rating. A hall feed (RPP) is rated for the
                # whole hall, so a cloned RPP must NOT re-derive from the one rack it
                # opens with (that would peg it at overload immediately) — it copies
                # the curated hall RPP's frozen nameplate. A rack PDU copies the
                # curated rack PDU's real SKU nameplate (e.g. 22 kW), so fleet racks
                # match curated racks. Servers carry 0 (they are loads, not feeds).
                rated_power_w=int(getattr(tmpl, "rated_power_w", 0) or 0),
                metrics_enabled=True,
                country=getattr(tmpl, "country", ""),
                datacenter_city=getattr(tmpl, "datacenter_city", ""),
                datacenter=dc,
                room=room if room is not None else getattr(tmpl, "room", ""),
                floor=floor if floor is not None else getattr(tmpl, "floor", ""),
                rack_row=row,
                rack_num=num,
                rack_unit=unit,
                floor_x=fx if fx is not None else getattr(tmpl, "floor_x", None),
                floor_y=fy if fy is not None else getattr(tmpl, "floor_y", None),
                hot_aisle=hot if hot is not None else getattr(tmpl, "hot_aisle", ""),
                cold_aisle=cold if cold is not None else getattr(tmpl, "cold_aisle", ""),
                rack_facing=facing if facing is not None else getattr(tmpl, "rack_facing", ""),
                # No sys_location_override: let Device.sys_location compute the
                # full country/city/DC/floor/room/rack string so fleet-added
                # devices match the format of curated peers.
            )
            # Canvas position: a per-DC grid below that DC's curated nodes, so
            # fleet nodes never pile on the origin, never overlap each other, and
            # DC1/DC2 fleet growth stays in separate bands. Computed BEFORE the
            # device is registered so the (still position-less) new node can't
            # poison the band bounds. Cosmetic graph layout only — floor placement
            # lives in floor_x/floor_y.
            px, py = self._fleet_pos(dc)
            self.s.device_manager.add_device(dev)
            self.s.topology.add_device(dev, x=px, y=py)
            if self.s.ip_manager and ip:
                self.s.ip_manager.reserve(ip)
            return dev
        except Exception as e:
            self._log(f"[Fleet] create {name} failed: {e}")
            if self.s.ip_manager and ip:
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
        # The Redfish BMC and the gNMI target both live on the device's MGMT IP
        # (_bmc_ip / gNMI bind_ip = mgmt_ip or ip_address). Bind it too, else the
        # per-IP hot-add socket bind below fails on an unbound address and the
        # device never joins the running server — so the panel's Active count
        # never grows. Curated devices' mgmt IPs were bound by the bind panel;
        # fleet devices need theirs bound here.
        mgmt = getattr(device, "mgmt_ip", "") or ""
        if mgmt and mgmt != device.ip_address:
            self._bind_ip(mgmt)
        self._gen_datasets(device)
        # gNMI targets are the network fabric only (switches + routers), the same
        # set api/routers/gnmi.py starts with. Gating here keeps servers/CRAHs/
        # PDUs out of the gNMI target list (they'd otherwise register now that the
        # dataset key is fixed to the mgmt IP).
        g = getattr(self.s, "gnmi", None)
        if (device.device_type in self._GNMI_TYPES and g is not None
                and getattr(g, "_running", False)):
            try: g.add_device(device)
            except Exception as e: self._log(f"[Fleet] gNMI commission {device.name}: {e}")
        r = getattr(self.s, "redfish", None)
        if (device.device_type == DeviceType.SERVER and r is not None
                and getattr(r, "_running", False)):
            try: r.add_device(device)
            except Exception as e: self._log(f"[Fleet] Redfish commission {device.name}: {e}")
        # Chiller-plant gear (a fleet hall's CRAHs, etc.) joins the running BACnet
        # BMS. Same IP resolution as api/routers/bacnet.py: prod IP if present,
        # else the mgmt IP (both bound above). Keeps the BACnet panel's Active
        # Devices count in step with fleet-added cooling.
        b = getattr(self.s, "bacnet", None)
        if (device.device_type in self._BACNET_PLANT_TYPES and b is not None
                and getattr(b, "_running", False)):
            try:
                b.add_plant_device(device.ip_address or mgmt, device.device_type.value,
                                   name=device.name,
                                   rated_kw=(getattr(device, "power_draw_w", 0) or 0) / 1000.0)
            except Exception as e:
                self._log(f"[Fleet] BACnet commission {device.name}: {e}")
        # An EV2 energy meter (a fleet-provisioned RPP's panel meter) joins the
        # running BACnet BMS the same way. active_circuits self-corrects from the
        # live branch map, so the meter starts empty and fills as PDUs wire on.
        if (device.device_type == DeviceType.ENERGY_MONITOR and b is not None
                and getattr(b, "_running", False)):
            try:
                b.add_ev2_device(device.ip_address or mgmt, name=device.name,
                                 circuits=self._RPP_POLES)
            except Exception as e:
                self._log(f"[Fleet] BACnet EV2 commission {device.name}: {e}")

    # Chiller-plant device types exposed over BACnet (mirror api/routers/bacnet.py).
    _BACNET_PLANT_TYPES = {DeviceType.CHILLER, DeviceType.PUMP,
                           DeviceType.COOLING_TOWER, DeviceType.VALVE,
                           DeviceType.CRAH, DeviceType.CDU}
    # Network fabric exposed over gNMI (mirror api/routers/gnmi.py's start filter).
    _GNMI_TYPES = {DeviceType.SWITCH, DeviceType.ROUTER}

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
        b = getattr(self.s, "bacnet", None)
        if b is not None and device.device_type in self._BACNET_PLANT_TYPES:
            try: b.remove_plant_device(device.ip_address or bind_ip)
            except Exception: pass
        if b is not None and device.device_type == DeviceType.ENERGY_MONITOR:
            try: b.remove_ev2_device(device.ip_address or bind_ip)
            except Exception: pass
        self._unbind_ip(device.ip_address)
        if bind_ip and bind_ip != device.ip_address:
            self._unbind_ip(bind_ip)                 # the mgmt IP bound in _commission

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
        # Whole-fleet composition by device type in one pass. The fleet doesn't
        # only add servers — a new rack brings a leaf (+ its MLAG-ready peer),
        # dual rack PDUs; a new hall/pod brings spines, an OOB switch, RPPs, and
        # (perimeter cooling) CRAHs + environmental sensors. Surface all of them
        # so the panel reflects the real fleet, not just the server count.
        devs = self.s.device_manager.get_all_devices() if self.s.device_manager else []
        device_counts: dict = {}
        rack_pdu_w = 0
        for d in devs:
            k = d.device_type.value
            device_counts[k] = device_counts.get(k, 0) + 1
            # Representative COMPUTE-rack-PDU nameplate (the largest rack PDU) — the
            # per-rack budget knob governs compute racks, whose PDUs are the big
            # 3-phase units (network/OOB racks carry small PDUs and aren't budget-
            # provisioned). This is the physical ceiling on the budget: one PDU
            # carries the rack on A/B failover. Lets the panel show "budget X —
            # PDU delivers Y". (Per-rack enforcement uses each rack's own PDUs.)
            if d.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU):
                rack_pdu_w = max(rack_pdu_w, int(getattr(d, "rated_power_w", 0) or 0))
        # Usable ceiling for the per-rack budget = single PDU × 0.8 (NEC derate).
        budget_cap_w = int(rack_pdu_w * 0.8) if rack_pdu_w else 0
        effective_budget_w = (min(self.cfg.rack_power_budget_w, budget_cap_w)
                              if budget_cap_w else self.cfg.rack_power_budget_w)
        return {
            "enabled": self.enabled,
            "day": self.day,
            "config": {
                "minutes_per_day": self.cfg.minutes_per_day,
                "provision_lambda": self.cfg.provision_lambda,
                "decommission_lambda": self.cfg.decommission_lambda,
                "rack_power_budget_w": self.cfg.rack_power_budget_w,
                # Panel hint: the configured budget is capped at what one rack PDU
                # delivers on A/B failover. effective = the value the fleet
                # actually enforces per rack (see _rack_budget_w).
                "rack_pdu_capacity_w": rack_pdu_w,
                "rack_power_budget_cap_w": budget_cap_w,
                "rack_power_budget_effective_w": effective_budget_w,
                "max_racks_per_row": self.cfg.max_racks_per_row,
                "compute_rows_per_room": self.cfg.compute_rows_per_room,
                "max_total_servers": self.cfg.max_total_servers,
            },
            "total_servers": device_counts.get(DeviceType.SERVER.value, 0),
            "total_devices": len(devs),
            "device_counts": device_counts,
            "history": [
                {"day": h.day, "added": h.added, "removed": h.removed,
                 "expanded_racks": h.expanded_racks, "total_servers": h.total_servers}
                for h in self.history[-30:]
            ],
        }

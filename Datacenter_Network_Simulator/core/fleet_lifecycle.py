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
    leaf_interface_groups, leaf_port_roles, rack_has_power_headroom,
    RACK_POWER_BUDGET_W_DEFAULT,
    TOR_A_UNIT, TOR_B_UNIT, PDU_UNIT, FIRST_SERVER_UNIT, LAST_SERVER_UNIT,
)
from core import hall_geometry as geo

if TYPE_CHECKING:
    from api.state import AppState

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
    #      within its provisioned power budget (~15 kW usable). This is the usual
    #      binding limit in an enterprise hall (power/thermal, not ports).
    rack_power_budget_w: int = RACK_POWER_BUDGET_W_DEFAULT
    # Growth policy: each hall holds up to compute_rows_per_room x max_racks_per_row
    # compute racks. The fleet fills the racks already in a hall, then adds racks
    # up to that grid (curated racks count toward it), and only once every hall is
    # full opens a brand-new hall — matching the enlarged halls' headroom.
    max_racks_per_row: int = 5         # compute racks per row in a hall's grid
    compute_rows_per_room: int = 3     # compute rows per hall -> grid = rows x width
    # Default sized to the installed cooling plant, which Fleet lifecycle never
    # grows: ~104 kW of plant cools ~221 kW IT (÷0.47) ≈ 470 servers at typical
    # CPU, so a full fleet sits at design PUE (~1.47). User-editable in the Fleet
    # panel — raise it only if the topology's cooling plant is scaled up too.
    max_total_servers: int = 470


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
        # Halls the fleet has opened, so they're counted/filled like curated ones.
        self._fleet_halls: set = set()
        # Stable, DC-globally-unique rack_row label per (hall, virtual-row) the
        # fleet fills — keeps (dc, row, num) rack keys from colliding across halls.
        self._row_labels: dict = {}
        self._row_label_seq = 1000
        # Per fleet-hall compute-grid x-offset (columns). A new hall reserves a
        # CRAH lane on each side wall (perimeter cooling), so its compute racks
        # shift one column right; _rack_coords/_clone_rpp read this so later
        # _fill_hall_grid racks land in the same offset grid. Curated halls (not
        # in this map) stay at offset 0.
        self._hall_x_off: dict = {}
        # Topology-graph layout for fleet nodes: each DC gets its own band (the
        # curated x-range, below the curated nodes) so DC1/DC2 fleet growth never
        # overlaps the other DC. Bounds snapshotted per DC; placed counter tiles.
        self._dc_bounds_cache: dict = {}
        self._dc_placed: dict = {}
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
                summ.removed.append(self._dev_info(dev))
            except Exception as e:
                self._log(f"[Fleet] decom {dev.name} failed: {e}")

    # ── provision ────────────────────────────────────────────────────────────

    def _provision(self, summ: DaySummary) -> None:
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
            if dev is not None:
                summ.added.append(self._dev_info(dev))

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
        """Compact device descriptor for the Activity log (name/vendor/IPs)."""
        return {
            "name": dev.name,
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
            add_w = float(getattr(tmpl, "power_draw_w", 0) or 0)
            if not rack_has_power_headroom(racks_w.get(key, 0.0), add_w,
                                           self.cfg.rack_power_budget_w):
                continue                       # would exceed the rack power budget
            pdus = self._neighbors(tmpl, "power", (DeviceType.PDU, DeviceType.FLOOR_PDU))
            return {"key": key, "tor": tor, "pdus": pdus, "server_tmpl": tmpl}
        return None

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
        return (d is not None and d.device_type == DeviceType.SWITCH
                and "-SP" in (d.name or ""))

    def _is_leaf(self, d: Optional[Device]) -> bool:
        return (d is not None and d.device_type == DeviceType.SWITCH
                and "-SP" not in (d.name or ""))

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

    def _rack_coords(self, rk: tuple, vrow: int, num: int):
        """Floor-plan coords for fleet rack (vrow, num) in hall *rk*: behind the
        curated compute (or from the front in a fresh hall), on the shared grid."""
        back = self._hall_back_y(rk)
        fy = round((back + geo.ROW_PITCH * (vrow + 1)) if back is not None
                   else geo.row_y(1) + geo.ROW_PITCH * vrow, 4)
        fx = geo.rack_x(num + self._hall_x_off.get(rk, 0))
        i = max(1, int(round((fy - 1.8) / geo.ROW_PITCH)) + 1)
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
                if d and d.device_type == DeviceType.SWITCH and "-SP" in (d.name or ""):
                    spines.append(d)
        except Exception:
            pass
        rpps = [d for d in self.s.device_manager.get_all_devices()
                if d.device_type == DeviceType.RPP and self._room_key(d) == rk]
        rpp_a = next((r for r in rpps if r.name and "A" in r.name.rsplit("-", 1)[-1]), None)
        rpp_b = next((r for r in rpps if r.name and "B" in r.name.rsplit("-", 1)[-1]), None)
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

        def _chw_header(suffix: str):
            return next((d for d in self.s.device_manager.get_all_devices()
                         if d.device_type == DeviceType.SENSOR
                         and (d.datacenter or "") == _dc
                         and (d.name or "").upper().endswith(suffix)), None)
        chw_supply = _chw_header("CHWS")
        chw_return = _chw_header("CHWR")
        return {"srv_tmpl": srv_tmpl, "leaf_tmpl": leaf, "oob": oob,
                "spines": spines, "rpp_a": rpp_a, "rpp_b": rpp_b,
                "pdu_tmpl": self._dc_pdu(_dc),
                "crahs": crahs, "sensor_tmpl": sensor_tmpl,
                "chw_supply": chw_supply, "chw_return": chw_return}

    def _fill_hall_grid(self, summ: DaySummary) -> Optional[dict]:
        """Add the next compute rack to a hall that's still under its grid cap.
        Most-occupied hall first, so one hall fills before the next is touched."""
        width = max(1, self.cfg.max_racks_per_row)
        grid = width * max(1, self.cfg.compute_rows_per_room)
        racks = self._hall_compute_racks()
        for rk in sorted(racks, key=lambda k: (-len(racks[k]), tuple(map(str, k)))):
            used = racks[rk]
            if len(used) >= grid:
                continue                              # hall full to its cap
            curated = sum(1 for (rr, _n) in used if rr < self._FLEET_ROW_BASE)
            fleet_used = {(rr, rn) for (rr, rn) in used if rr >= self._FLEET_ROW_BASE}
            if len(fleet_used) >= max(0, grid - curated):
                continue                              # no fleet room left in cap
            infra = self._hall_infra(rk)
            if infra is None:
                continue
            # Fabric limit: a new rack means a new leaf, which needs a free downlink
            # on every spine. If the pod's spine fabric is full, this hall is done —
            # skip it so a new hall (its own pod) is opened instead.
            if not self._spines_have_room(infra.get("spines")):
                continue
            idx = len(fleet_used)                     # next fleet slot, in order
            vrow = idx // width
            row = self._row_label(rk, vrow)
            num = idx % width + 1
            return self._build_compute_rack(rk, row, num, infra, summ, vrow)
        return None

    def _open_new_hall(self, summ: DaySummary) -> Optional[dict]:
        """Commission a brand-new server hall in the busiest DC: a fresh RPP pair
        (fed from the DC UPS) plus the first compute rack. Subsequent provisions
        fill its grid via _fill_hall_grid."""
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
        # Reserve a CRAH lane on each side wall (perimeter cooling): compute
        # racks shift one column right, into cols 2..W+1, leaving col 1 (left
        # wall) and col W+2 (right wall) for the air handlers. Recorded per-hall
        # so later _fill_hall_grid racks land in the same offset grid.
        self._hall_x_off[rk] = 1
        W = max(1, self.cfg.max_racks_per_row)
        R = max(1, self.cfg.compute_rows_per_room)
        # Fresh RPP pair fed by the same UPS units the source RPPs draw from, so
        # the new hall's load still reaches the UPS/generator in the power model.
        # RPP sits at the back wall, one row behind the last compute row.
        back_y = round(geo.row_y(1) + geo.ROW_PITCH * R, 4)
        new_infra = dict(infra)
        new_infra["rpp_a"] = self._clone_rpp(infra["rpp_a"], rk, "A", back_y)
        new_infra["rpp_b"] = self._clone_rpp(infra["rpp_b"], rk, "B", back_y) if infra["rpp_b"] else None
        chw_supply = infra.get("chw_supply")
        chw_return = infra.get("chw_return")

        # ── Perimeter cooling: CRAHs on the side walls, sensors in the aisles ──
        # Real halls line the perimeter with downflow CRAHs at the hot-aisle
        # ends, not stacked in deep rows behind compute (that stretched the room
        # into a thin strip). Each CRAH rides a side wall (right, then left) at a
        # compute-row depth; environmental probes sit in the cold aisles. CRAHs
        # and sensors reuse existing rows/aisles, so they add width, not depth —
        # the hall stays wide-and-shallow like the curated halls. CRAHs join the
        # shared, pre-installed CHW loop at the DC's supply/return headers — no new
        # chillers.
        xL, xR = geo.rack_x(1), geo.rack_x(W + 2)
        crah_slots = []
        for r in range(1, R + 1):
            y = geo.row_y(r)
            crah_slots.append((xR, y))              # right wall
            crah_slots.append((xL, y))              # left wall
        cx = geo.rack_x(1 + (W + 1) // 2)           # aisle-centre column
        sen_slots = [(cx, round(0.6 + geo.ROW_PITCH * j, 4)) for j in range(1, R + 2)]
        new_crahs = []
        for i, crah_tmpl in enumerate(infra.get("crahs") or []):
            fx, fy = crah_slots[i % len(crah_slots)] if crah_slots else (xR, geo.row_y(1))
            c = self._clone(crah_tmpl, dc, self._row_label(rk, "crah"), 200 + i,
                            int(getattr(crah_tmpl, "rack_unit", 1) or 1),
                            prefix="crah", floor=floor, room=room, fx=fx, fy=fy)
            if c is None:
                continue
            # Join the CHW loop with the SAME two directional cooling edges the
            # curated CRAHs use, so the viewer colours/animates them correctly:
            #   • supply (cold): CHWS header → CRAH   (chilled water in)
            #   • return (hot):  CRAH → CHWR header   (warm water out)
            # One edge, or the wrong direction, mis-colours the link and leaves the
            # supply/return pair incomplete.
            try:
                if chw_supply is not None:
                    self.s.topology.add_link(chw_supply.id, c.id, layer="cooling")
                if chw_return is not None:
                    self.s.topology.add_link(c.id, chw_return.id, layer="cooling")
            except Exception:
                pass
            self._commission(c)
            new_crahs.append(c)
        sen_tmpl = infra.get("sensor_tmpl")
        if sen_tmpl is not None:
            for i in range(self._HALL_SENSORS):
                fx, fy = sen_slots[i % len(sen_slots)] if sen_slots else (cx, geo.row_y(1))
                c = self._clone(sen_tmpl, dc, self._row_label(rk, "sen"), 300 + i,
                                int(getattr(sen_tmpl, "rack_unit", 1) or 1),
                                prefix="sen", floor=floor, room=room, fx=fx, fy=fy)
                if c is not None:
                    self._commission(c)

        # A new hall is a new POD: give it its OWN spine set + OOB (cloned from
        # the source hall, upstream core/mgmt links replicated) instead of cross-
        # wiring its leaves into another hall's fabric — this is what makes the
        # spine/OOB port caps meaningful. Lay the fabric on ONE network row
        # behind the RPP back wall, across the compute columns (offset grid) so
        # it never spills past the side CRAH lanes.
        net_y0 = round(back_y + geo.ROW_PITCH, 4)
        fabric = [(sp, "sp") for sp in (infra.get("spines") or [])]
        if infra.get("oob") is not None:
            fabric.append((infra["oob"], "oob"))
        new_spines, new_oob = [], None
        for i, (tmpl, pfx) in enumerate(fabric):
            fx = geo.rack_x(2 + i % W)              # compute columns (offset grid)
            fy = round(net_y0 + geo.ROW_PITCH * (i // W), 4)
            c = self._clone_fabric_node(tmpl, rk, pfx, num=100 + i, fx=fx, fy=fy)
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
        net_rows = (len(fabric) + W - 1) // W if fabric else 0

        self._log(f"[Fleet] new pod: {len(new_spines)} spine(s), OOB, "
                  f"{len(new_crahs)} CRAH, {self._HALL_SENSORS} sensor(s)")
        self._fleet_halls.add(rk)
        # Rows deep = compute rows + 1 RPP row + the network rows; +2 side lanes
        # wide for the perimeter CRAHs.
        self._register_hall_extent(dc, room, back_rows=1 + net_rows, side_lanes=2)
        self._log(f"[Fleet] opened new hall {dc}/F{floor}/{room} "
                  f"(grid {R}x{W}, perimeter CRAH)")
        return self._build_compute_rack(rk, self._row_label(rk, 0), 1, new_infra, summ, 0)

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
        off = self._hall_x_off.get(rk, 0)
        rpp = self._clone(tmpl, dc, self._row_label(rk, "rpp"),
                          1 if side == "A" else 2, PDU_UNIT,
                          prefix=f"rpp{side.lower()}", floor=floor, room=room,
                          fx=geo.rack_x((1 if side == "A" else 2) + off), fy=y)
        if rpp is None:
            return None
        if ups is not None:
            try:
                self.s.topology.add_link(ups.id, rpp.id, layer="power")
            except Exception:
                pass
        self._commission(rpp)
        return rpp

    def _build_compute_rack(self, rk: tuple, row: int, num: int, infra: dict,
                            summ: DaySummary, vrow: int) -> Optional[dict]:
        """Materialise a compute rack (leaf + dual rack PDUs) in hall *rk*, fully
        wired: leaf → every spine + the OOB switch; each PDU → an RPP feed so
        server load reaches the UPS. Placed on the floor grid at virtual row
        *vrow*. Returns the placement target for servers."""
        dc, floor, room = rk
        fx, fy, hot, cold, facing = self._rack_coords(rk, vrow, num)
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
            # A rack PDU is sized to the rack's power FEED, not whatever load
            # happens to exist the instant it's created. Without this the fixed-
            # rating model would freeze it near-empty (only the ToR under it) and
            # then read a false 1000 %+ overload as the rack fills. Rate it to the
            # rack power budget with breaker headroom (÷0.8), and full budget per
            # side so an A/B PDU can carry the whole rack on failover.
            self.s.device_manager.update_device(
                pdu.id, rated_power_w=int(self.cfg.rack_power_budget_w / 0.8))
            if rpp is not None:                       # PDU drinks from the RPP feed
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
    def _alloc_in_subnet(ref_ip: str, prefix: int, used: set) -> str:
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
        for host in net.hosts():
            s = str(host)
            if s not in used:
                return s
        return ""

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

    def _next_free_unit(self, key: tuple) -> int:
        # Servers occupy U1..U40; U41 (MLAG peer slot) and U42 (ToR) stay clear.
        used = {d.rack_unit for d in self._rack_devices(key) if d.rack_unit}
        u = FIRST_SERVER_UNIT
        while u in used and u < LAST_SERVER_UNIT:
            u += 1
        return u

    def _add_server(self, rack: dict) -> Optional[Device]:
        tmpl: Device = rack["server_tmpl"]
        dc, row, num = rack["key"]
        unit = self._next_free_unit(rack["key"])
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
        mgmt_ip = self._alloc_in_subnet(getattr(tmpl, "mgmt_ip", "") or "", 22, used)
        # Match the curated naming style: servers are DC2-SRV027 (continue the
        # sequence, no dash); other fleet kit keeps DC2-TOR-0007 style.
        if prefix == "srv":
            name = self._next_srv_name(dc or "DC")
        else:
            base = f"{(dc or 'DC')}-{prefix.upper()}".replace(" ", "-")
            name = self._unique_name(base)
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
                # Inherit the template's install rating. A hall feed (RPP) is rated
                # for the whole hall, so a cloned RPP must NOT re-derive from the one
                # rack it opens with (that would peg it at overload immediately) — it
                # copies the curated hall RPP's frozen nameplate. Rack PDUs/servers
                # carry no rating (0) and derive fresh, which is correct per-rack.
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
        g = getattr(self.s, "gnmi", None)
        if g is not None and getattr(g, "_running", False):
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

    # Chiller-plant device types exposed over BACnet (mirror api/routers/bacnet.py).
    _BACNET_PLANT_TYPES = {DeviceType.CHILLER, DeviceType.PUMP,
                           DeviceType.COOLING_TOWER, DeviceType.VALVE,
                           DeviceType.CRAH, DeviceType.CDU}

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
        for d in devs:
            k = d.device_type.value
            device_counts[k] = device_counts.get(k, 0) + 1
        return {
            "enabled": self.enabled,
            "day": self.day,
            "config": {
                "minutes_per_day": self.cfg.minutes_per_day,
                "provision_lambda": self.cfg.provision_lambda,
                "decommission_lambda": self.cfg.decommission_lambda,
                "rack_power_budget_w": self.cfg.rack_power_budget_w,
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

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

from core.device_manager import Device, DeviceType, Vendor, Interface, InterfaceRole
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

# Names of a device's DEDICATED out-of-band management / BMC port — the console
# (switch/router) or lights-out controller (server) lands here, never on a data
# port. Kept in lock-step with tools/add_network_mgmt_port.py + tools/set_server_ports.py.
_MGMT_PORT_NAMES = {"mgmt0", "management", "mgmt", "management1", "fxp0", "em0",
                    "idrac", "ilo", "xcc", "ipmi", "imm", "cimc", "bmc"}


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

# Metres reserved at each end of the CRAH back wall for a mechanical power panel
# (MPP). The CRAH lineup is inset by this, so the two MPPs stand in the end bays
# flanking the CRAHs they feed. Shared with tools/seed_hall_crahs +
# tools/add_hall_mech_panels + tools/inset_crah_wall (keep them in step).
CRAH_END_RESERVE = 0.7

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
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Latch so the "mgmt /22 base full — spilling to overflow" note is logged
        # once per session, not once per device past the cliff.
        self._mgmt_overflow_warned = False
        # Per-DC latch for the "OOB core downlinks exhausted" warning, so the
        # management-aggregation ceiling is surfaced once per DC, not per endpoint.
        self._oob_core_warned: dict = {}

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
            self._settle_after_change(summ)
            self._log(f"[Fleet] day {self.day}: +{len(summ.added)} -{len(summ.removed)} "
                      f"(servers={summ.total_servers})")
            return summ

    def _settle_after_change(self, summ: DaySummary) -> None:
        """After a graph mutation (day churn OR a manual provision), refresh the
        derived power model and the live UIs. Caller must already hold self._lock.

        Drops the cached power cascade so new IT load ripples up to the
        PDU/UPS/RPP/EV2 meters instead of being summed against a stale tree, tells
        the UI to resync the device list + rebuild the topology scene (Qt) / refetch
        the graph (web), and bounces snmpsim ONCE (async, coalesced) so the new
        devices' SNMP agents become pollable. gNMI/Redfish were already hot-added
        live in _commission."""
        ss = getattr(self.s, "state_store", None)
        if ss is not None:
            try: ss.invalidate_power_context()
            except Exception as e: self._log(f"[Fleet] power ctx invalidate: {e}")
        if self.s is not None:
            self.s.notify_ui("sync_devices")
        if summ.added or summ.removed or summ.expanded_racks:
            if self.s is not None:
                self.s.notify_ui("rebuild_topology_scene")
            ex = getattr(self.s, "executor", None)
            if ex is not None and getattr(self.s, "snmpsim", None) is not None:
                try:
                    ex.submit(self.s.reload_snmp, self._log)
                except Exception as e:
                    self._log(f"[Fleet] snmp reload submit: {e}")

    # ── user-driven provisioning (manual capacity, off the day scheduler) ─────
    def provision_rack(self, dc: str, room: Optional[str] = None) -> Optional[dict]:
        """Add ONE empty compute rack (leaf + A/B rack PDUs, wired to the pod
        fabric + RPP feeds) to a hall in *dc* that still has grid space and
        fabric/power headroom — the SAME fill path day-churn uses, so every cap
        (spine downlinks, OOB ports, grid, RPP poles) is honoured. *room* targets a
        specific hall; None auto-picks the busiest hall in the DC. Returns the new
        rack's context dict, or None when the target hall (or, auto, no hall in the
        DC) has room. Hot-commissions the rack's gear onto the live sims."""
        with self._lock:
            summ = DaySummary(day=self.day)
            rack = self._fill_hall_grid(summ, dc=dc, room=room)
            if rack is None:
                return None
            summ.total_servers = len(self._servers())
            self._settle_after_change(summ)
            self._log(f"[Fleet] manual provision rack in {dc}: "
                      f"{summ.expanded_racks[-1] if summ.expanded_racks else '?'}")
            return rack

    def provision_hall(self, dc: str) -> Optional[dict]:
        """Open a brand-new server hall in *dc* — its own pod fabric (spines+OOB),
        RPP pair + EV2 meters, back-wall CRAH complement and sensors, cloned from
        the DC's busiest hall — and place its first compute rack. Returns that
        rack's context dict, or None when *dc* has no hall to clone from (RPP/infra
        missing). Hot-commissions all the new gear onto the live sims."""
        with self._lock:
            summ = DaySummary(day=self.day)
            rack = self._open_new_hall(summ, dc=dc)
            if rack is None:
                return None
            summ.total_servers = len(self._servers())
            self._settle_after_change(summ)
            self._log(f"[Fleet] manual provision NEW hall in {dc}")
            return rack

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
        is a hard co-limit alongside the power budget.

        Ports inside the downlink range that already carry a spine uplink or the
        peer-link are subtracted: leaf_port_roles splits by a real chassis layout
        (a 93180YC-FX's 48×25G before its 6×100G), but this topology's spines land on
        ports 0-3 — the FRONT of that range. Counting them would promise 48 server
        slots on a leaf that physically has 44 left, and fill the rack past its own
        fabric ports."""
        downlink, _ = leaf_port_roles(getattr(tor, "model_name", "") or "",
                                      getattr(tor, "interface_count", 54))
        try:
            fabric = self.s.topology.fabric_ifaces(tor.id)
        except Exception:
            fabric = set()
        return max(0, downlink - sum(1 for i in fabric if i < downlink))

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

    _OOB_UPLINK_RESERVE = 4   # ports kept for the OOB-core uplinks + the peer link

    def _oob_port_cap(self, oob: Device) -> int:
        """Usable access ports on an OOB switch: its real port count minus a small
        reserve for the uplinks to the OOB cores and the peer link. Endpoints past
        this stack a new OOB into the hall."""
        ports = int(getattr(oob, "interface_count", 48) or 48)
        return max(8, ports - self._OOB_UPLINK_RESERVE)

    # Devices that consume one access port on an OOB / OOBM switch. IT endpoints
    # (server BMC, rack PDU, switch console) and facility endpoints (CRAH, sensor,
    # EV2 meter, CDU, busway). Every OOB-family switch (OOB/OOBM/OOBC/BMSC) is
    # oob_switch, so uplinks and peers are excluded by simply not listing that type.
    _MGMT_ENDPOINT_TYPES = (
        DeviceType.SERVER, DeviceType.PDU, DeviceType.FLOOR_PDU, DeviceType.SWITCH,
        DeviceType.CRAH, DeviceType.SENSOR, DeviceType.ENERGY_MONITOR,
        DeviceType.CDU, DeviceType.MPP, DeviceType.MCC,
    )

    def _mgmt_endpoints_on(self, sw: Device) -> int:
        """Managed endpoints consuming an access port on an OOB or OOBM switch — the
        true port fill. Uplinks to the OOB cores / BMS controllers and peer links are
        OOB_SWITCH neighbours (not in _MGMT_ENDPOINT_TYPES) and don't count. This —
        not the leaf count — is what the stacking decision measures, so an access OOB
        tracks the SERVER count (a rack of ~20 BMCs fills a 48-port OOB in ~2 racks)
        and a BMS OOBM tracks the facility-device count the same way."""
        try:
            adj = self.s.topology.graph[sw.id]
        except Exception:
            return 0
        n = 0
        for nbr, edges in adj.items():
            if not any(ed.get("layer") == "management" for ed in edges.values()):
                continue
            d = self.s.device_manager.get_device(nbr)
            if d and d.device_type in self._MGMT_ENDPOINT_TYPES:
                n += 1
        return n

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
        """Every IT access OOB switch physically in hall *rk*. Filters on the name
        role being exactly 'OOB' so the BMS OOB (OOBM) and the OOB cores (OOBC) are
        excluded. Room-based rather than leaf-derived, so it also finds a freshly
        stacked OOB that so far carries only server BMCs and no leaf yet."""
        out = []
        for d in self._by_type(DeviceType.OOB_SWITCH):
            if self._room_key(d) != rk:
                continue
            letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
            if letters.upper() == "OOB":
                out.append(d)
        return out

    _SWITCH_U_PITCH = 2      # RU between stacked management switches
    _RACK_U = 42

    def _switch_stack_slot(self, rk: tuple, anchors: list):
        """(rack_row, rack_num, rack_unit) for one more management switch, U-STACKED
        into a network rack that already holds *anchors* (the hall's OOB/OOBM
        switches). Fills a rack's U-space top-down at 2U pitch before spilling to a
        fresh rack beside it — so a hall's switches share a rack like the curated
        network row (all OOBs in one rack), NOT one floor rack each."""
        racks: dict = {}
        for a in anchors:
            racks.setdefault((a.rack_row or 1, a.rack_num or 1), True)
        alldev = self.s.device_manager.get_all_devices()
        for (row, num) in sorted(racks):
            used = {d.rack_unit for d in alldev
                    if self._room_key(d) == rk and (d.rack_row or 0) == row
                    and (d.rack_num or 0) == num and d.rack_unit}
            for u in range(self._RACK_U, 0, -self._SWITCH_U_PITCH):
                if u not in used:
                    return row, num, u
        # every anchor rack is U-full — open a fresh network rack beside them.
        base = anchors[0] if anchors else None
        row = (base.rack_row if base else 1) or 1
        num = max((a.rack_num or 1) for a in anchors) + 1 if anchors else 1
        return row, num, self._RACK_U

    _OOB_CORE_UPLINK_RESERVE = 6   # core ports kept for perimeter FW / WAN + peer

    def _oob_cores(self, dc: str) -> list:
        """The OOB core switches (OOBC*) in datacenter *dc* — the Network-Room
        aggregation every hall access OOB uplinks to."""
        out = []
        for d in self._by_type(DeviceType.OOB_SWITCH):
            if (d.datacenter or "") != dc:
                continue
            letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
            if letters.upper() == "OOBC":
                out.append(d)
        return out

    def _oob_core_downlink_cap(self, core: Device) -> int:
        """Access-OOB uplink ports on an OOB core: real port count minus a reserve
        for the core's own upstream (perimeter firewalls / OOB WAN) and the
        peer-core link."""
        ports = int(getattr(core, "interface_count", 24) or 24)
        return max(4, ports - self._OOB_CORE_UPLINK_RESERVE)

    def _oob_core_load(self, core: Device) -> int:
        """Access OOB switches (role 'OOB') currently uplinked to this core. The
        peer core, upstream firewalls and WAN routers are not access downlinks and
        don't count."""
        try:
            adj = self.s.topology.graph[core.id]
        except Exception:
            return 0
        n = 0
        for nbr, edges in adj.items():
            if not any(ed.get("layer") == "management" for ed in edges.values()):
                continue
            d = self.s.device_manager.get_device(nbr)
            if d is None:
                continue
            letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
            if letters.upper() == "OOB":
                n += 1
        return n

    def _oob_cores_have_room(self, dc: str) -> bool:
        """True if every OOB core in *dc* still has a free access-downlink port. A
        new access OOB dual-homes to BOTH cores, so it needs room on each. With no
        modelled cores there is nothing to gate on."""
        cores = self._oob_cores(dc)
        if not cores:
            return True
        return all(self._oob_core_load(c) < self._oob_core_downlink_cap(c)
                   for c in cores)

    def _clone_fabric_node(self, tmpl: Device, rk: tuple, prefix: str,
                           num: int = 1, fx: Optional[float] = None,
                           fy: Optional[float] = None, row: Optional[int] = None,
                           unit: Optional[int] = None) -> Optional[Device]:
        """Clone a shared fabric node (spine / OOB) into hall *rk* and replicate its
        UPSTREAM links (to the core / management aggregation) — but NOT its downstream
        leaves/servers/PDUs. Used to give a new hall its own pod fabric and to stack
        an extra OOB when a hall's management ports are exhausted. *num*/*fx*/*fy*
        place it on the hall's floor grid (network/back row) so the floor-plan draws
        it inside the right hall; without them it would inherit the source hall's
        coordinates and render in the wrong room. *row*/*unit* override the rack_row
        and rack_unit — used to U-STACK a stacked switch into an existing network
        rack (a shared MDA rack holds many 1-2U switches) instead of consuming a
        whole floor rack each."""
        dc, floor, room = rk
        new = self._clone(tmpl, dc,
                          row if row is not None else self._row_label(rk, prefix), num,
                          unit if unit is not None else int(getattr(tmpl, "rack_unit", 1) or 1),
                          prefix=prefix, floor=floor, room=room, fx=fx, fy=fy)
        if new is None:
            return None
        _down = (DeviceType.SERVER, DeviceType.PDU, DeviceType.FLOOR_PDU)
        try:
            for nbr in list(self.s.topology.graph.neighbors(tmpl.id)):
                d = self.s.device_manager.get_device(nbr)
                if d is None or d.device_type in _down:
                    continue                      # skip downstream servers/PDUs
                # Skip DOWNSTREAM in-hall fabric — both leaf ToRs AND spine consoles
                # live in a Server Hall — but KEEP the UPSTREAM cores/aggregation,
                # which sit in the Network Room. Gate on room + switch type:
                #   * a core switch (COR) reads as a non-SP "leaf" via _is_leaf, so
                #     without the room gate the cloned SPINE loses its COR1/COR2
                #     uplink and the new pod is islanded from the DC core;
                #   * a source-hall SPINE is NOT a leaf, so without covering all
                #     Server-Hall switches the cloned OOB inherits the SOURCE hall's
                #     spine consoles — a spurious cross-hall management link.
                if (d.device_type == DeviceType.SWITCH
                        and (getattr(d, "room", "") or "").startswith("Server Hall")):
                    continue
                self.s.topology.add_link(new.id, nbr,
                                         layer=self._link_layer(tmpl.id, nbr) or "production")
        except Exception as e:
            self._log(f"[Fleet] fabric clone uplink {new.name}: {e}")
        return new

    def _oob_port_for(self, rk: tuple, infra: Optional[dict] = None) -> Optional[Device]:
        """A hall OOB switch with a free management port for ONE more endpoint — a
        server BMC (iDRAC/iLO), a managed PDU, or a switch console. When every hall
        OOB is full it clones a new one into the hall. Because the fill is measured
        by real endpoints (_mgmt_endpoints_on), each server's BMC consumes a port, so
        the OOB count grows with the server count, not just the leaf/rack count.

        *infra* is optional: it supplies a clone template and spine count when the
        caller has one; both fall back to what's physically in the hall otherwise."""
        infra = infra or {}
        oobs = self._hall_oobs(rk) or ([infra["oob"]] if infra.get("oob") else [])
        for o in oobs:
            if self._mgmt_endpoints_on(o) < self._oob_port_cap(o):
                return o
        tmpl = oobs[0] if oobs else infra.get("oob")
        if tmpl is None:
            return None
        # Stacking a new access OOB dual-homes it into BOTH OOB cores, consuming a
        # downlink on each. If the cores are out of downlinks, the DC's management
        # aggregation is full — don't stack past it. Oversubscribe the least-loaded
        # existing OOB and surface the ceiling once per DC: the real call an
        # operator faces until a second OOB core pair is added.
        dc = rk[0]
        if oobs and not self._oob_cores_have_room(dc):
            victim = min(oobs, key=self._mgmt_endpoints_on)
            if not self._oob_core_warned.get(dc):
                self._oob_core_warned[dc] = True
                self._log(f"[Fleet] OOB core downlinks exhausted in {dc} — cannot "
                          f"stack another access OOB; endpoints now oversubscribing "
                          f"{victim.name}. Add a second OOB core pair to grow the "
                          f"management plane.")
            return victim
        # U-stack the new OOB into the hall's existing network rack (like the
        # curated row: all OOBs share one rack), NOT a fresh floor rack each.
        anchors = oobs or ([tmpl] if tmpl else [])
        anchor = anchors[0] if anchors else tmpl
        row, num, unit = self._switch_stack_slot(rk, anchors)
        new = self._clone_fabric_node(tmpl, rk, "oob", num=num,
                                      fx=getattr(anchor, "floor_x", None),
                                      fy=getattr(anchor, "floor_y", None),
                                      row=row, unit=unit)
        if new is not None:
            self._wire_network_power(new, rk, infra)   # cord to network-row PDUs
            self._commission(new)
            self._log(f"[Fleet] OOB management ports full — stacked {new.name} in "
                      f"{rk[0]}/{rk[2]}")
        return new

    # ── BMS OOB (OOBM): the facility/OT management plane ──────────────────────
    # A separate plane from the IT access OOB. Hall CRAHs, sensors, EV2 meters and
    # CDUs answer BACnet/Modbus on it, and it uplinks to the BMS controllers (BMSC),
    # NOT to the OOB cores. Mirrors the curated OOBM wiring.
    def _hall_oobms(self, rk: tuple) -> list:
        """The BMS OOB switch(es) (role 'OOBM') physically in hall *rk*."""
        out = []
        for d in self._by_type(DeviceType.OOB_SWITCH):
            if self._room_key(d) != rk:
                continue
            letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
            if letters.upper() == "OOBM":
                out.append(d)
        return out

    def _any_oobm(self, dc: str) -> Optional[Device]:
        """A clone-template OOBM in *dc* when the hall has none yet. Prefers a hall
        OOBM (rack room) over the central-plant one, so the clone inherits a
        hall-shaped port count and placement."""
        cands = []
        for d in self._by_type(DeviceType.OOB_SWITCH):
            if (d.datacenter or "") != dc:
                continue
            letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
            if letters.upper() == "OOBM":
                cands.append(d)
        cands.sort(key=lambda d: 0 if _is_rack_room(d.room) else 1)
        return cands[0] if cands else None

    def _clone_bms_oob(self, tmpl: Device, rk: tuple, num: int,
                       row: Optional[int] = None, unit: Optional[int] = None,
                       fx: Optional[float] = None,
                       fy: Optional[float] = None) -> Optional[Device]:
        """Clone a hall BMS OOB (OOBM) into hall *rk*, keeping ONLY its uplinks to the
        BMS controllers (BMSC). Unlike _clone_fabric_node this must NOT replicate the
        template's facility neighbours (CRAH/sensor/…), which belong to the source
        hall — only the OT-plane uplink is shared. *row*/*unit* U-stack it into an
        existing network rack instead of taking a whole floor rack."""
        dc, floor, room = rk
        new = self._clone(tmpl, dc,
                          row if row is not None else self._row_label(rk, "oobm"), num,
                          unit if unit is not None else int(getattr(tmpl, "rack_unit", 1) or 1),
                          prefix="oobm", floor=floor, room=room,
                          fx=fx if fx is not None else geo.rack_x(num),
                          fy=fy if fy is not None else getattr(tmpl, "floor_y", None))
        if new is None:
            return None
        try:
            for nbr in list(self.s.topology.graph.neighbors(tmpl.id)):
                d = self.s.device_manager.get_device(nbr)
                if d is None:
                    continue
                letters = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha())
                if letters.upper() == "BMSC":
                    self.s.topology.add_link(new.id, nbr,
                        layer=self._link_layer(tmpl.id, nbr) or "management")
        except Exception as e:
            self._log(f"[Fleet] BMS-OOB uplink {new.name}: {e}")
        return new

    def _oobm_port_for(self, rk: tuple, infra: Optional[dict] = None) -> Optional[Device]:
        """A hall BMS OOB (OOBM) with a free port for one facility endpoint (CRAH,
        sensor, EV2 meter, CDU). Creates the hall's OOBM on first use — cloning from
        the source hall's OOBM (infra['oobm']) or any DC OOBM — and stacks a new one
        when the plane fills, so OOBM count tracks the facility-device count."""
        infra = infra or {}
        oobms = self._hall_oobms(rk)
        for o in oobms:
            if self._mgmt_endpoints_on(o) < self._oob_port_cap(o):
                return o
        tmpl = oobms[0] if oobms else (infra.get("oobm") or self._any_oobm(rk[0]))
        if tmpl is None:
            return None
        # U-stack the OOBM into the hall's network rack. Anchor on existing OOBMs,
        # or (first OOBM in the hall) on the access OOBs — curated OOBM shares the
        # OOB rack (e.g. R1-03). Never a whole floor rack of its own.
        anchors = oobms or self._hall_oobs(rk) or ([tmpl] if tmpl else [])
        anchor = anchors[0] if anchors else tmpl
        row, num, unit = self._switch_stack_slot(rk, anchors)
        new = self._clone_bms_oob(tmpl, rk, num, row=row, unit=unit,
                                  fx=getattr(anchor, "floor_x", None),
                                  fy=getattr(anchor, "floor_y", None))
        if new is not None:
            self._wire_network_power(new, rk, infra)   # cord to network-row PDUs
            self._commission(new)
            self._log(f"[Fleet] BMS-OOB {'ports full — stacked' if oobms else 'created'} "
                      f"{new.name} in {rk[0]}/{rk[2]}")
        return new

    def _mgmt_port_iface(self, dev: Device) -> Optional[int]:
        """List-index of *dev*'s dedicated OOB management / BMC port (mgmt0 / iLO /
        iDRAC / management / ...) so a console/BMC edge lands there, not on a data
        port. None when the device has no such named port (caller then auto-picks)."""
        for i, itf in enumerate(getattr(dev, "interfaces", None) or []):
            if (getattr(itf, "name", "") or "").strip().lower() in _MGMT_PORT_NAMES:
                return i
        return None

    def _wire_facility_mgmt(self, dev: Device, rk: tuple,
                            infra: Optional[dict] = None) -> None:
        """Put a fleet facility device (CRAH / sensor / EV2 meter / CDU) onto the
        hall's BMS OOB (OOBM), like the curated facility gear — it then answers
        BACnet/Modbus out-of-band. Creates or stacks the OOBM as needed."""
        try:
            oobm = self._oobm_port_for(rk, infra)
            if oobm is not None:
                self.s.topology.add_link(dev.id, oobm.id, layer="management")
        except Exception as e:
            self._log(f"[Fleet] BMS mgmt link {dev.name} failed: {e}")

    # ── network-row power: cord network gear to ITS OWN rack's A/B PDUs ────────
    # Real network gear is powered by the PDU pair in the SAME rack (a curated
    # network rack, e.g. R1-03, has its own PDUA/PDUB fed from the hall RPP → UPS).
    # So a stacked OOB U-stacked into that rack draws from that rack's PDUs — never
    # a shared off-rack pair. Only a brand-new fleet network rack (a new hall's
    # spine/OOB rack) has no PDUs yet; then one A/B pair is created IN that rack.
    def _rack_pdus(self, rk: tuple, row, num):
        """The (PDUA, PDUB) already in rack (row, num) of hall *rk*, or (None, None)."""
        a = b = None
        for d in self._by_type(DeviceType.PDU):
            if (self._room_key(d) == rk and (d.rack_row or 0) == (row or 0)
                    and (d.rack_num or 0) == (num or 0)):
                code = "".join(c for c in (d.name or "").split("-", 1)[0] if c.isalpha()).upper()
                if code == "PDUA" and a is None:
                    a = d
                elif code == "PDUB" and b is None:
                    b = d
        return a, b

    def _ensure_rack_pdus(self, rk: tuple, row, num, infra: Optional[dict],
                          fx=None, fy=None):
        """A/B rack PDUs in rack (row, num) — the pair already there (curated network
        rack) or a fresh pair created IN that rack, fed from the hall RPP. Rack-local,
        like real power distribution."""
        a, b = self._rack_pdus(rk, row, num)
        if a and b:
            return a, b
        infra = infra or self._hall_infra(rk) or {}
        tmpl = infra.get("pdu_tmpl")
        rpp_a, rpp_b = infra.get("rpp_a"), infra.get("rpp_b")
        if tmpl is None or rpp_a is None:
            return a, b
        dc, floor, room = rk
        for side, rpp, have in (("A", rpp_a, a), ("B", rpp_b or rpp_a, b)):
            if have is not None:
                continue
            pdu = self._clone(tmpl, dc, row, num, PDU_UNIT,
                              prefix=f"pdu{side.lower()}", floor=floor, room=room,
                              fx=fx, fy=fy)
            if pdu is None:
                continue
            try:
                self.s.topology.add_link(rpp.id, pdu.id, layer="power")
            except Exception:
                pass
            # Managed rack PDU onto the hall's access OOB (SNMP/Modbus over the OOB
            # plane), like every curated network-rack PDU — but only once THIS hall
            # actually has an OOB. A brand-new hall creates its spine/OOB-rack PDUs
            # BEFORE its OOB is stood up, so those get mgmt'd in a later pass (see
            # _open_new_hall); an existing hall (OOB stack) links immediately.
            try:
                if self._hall_oobs(rk):
                    poob = self._oob_port_for(rk, infra)
                    if poob is not None and self._room_key(poob) == rk:
                        self.s.topology.add_link(pdu.id, poob.id, layer="management")
            except Exception as e:
                self._log(f"[Fleet] network PDU mgmt link {pdu.name}: {e}")
            self._commission(pdu)
            if side == "A":
                a = pdu
            else:
                b = pdu
        return a, b

    def _wire_network_power(self, dev: Device, rk: tuple,
                            infra: Optional[dict] = None) -> None:
        """Dual-cord a network device (spine / access OOB / BMS OOB) to the A/B PDU
        pair IN ITS OWN RACK, like the curated network racks — never an off-rack
        pair. Creates the rack's PDUs only if the rack has none (fresh fleet rack)."""
        a, b = self._ensure_rack_pdus(rk, dev.rack_row, dev.rack_num, infra,
                                      fx=getattr(dev, "floor_x", None),
                                      fy=getattr(dev, "floor_y", None))
        if a is None and b is None:
            return
        upd = {}
        for pdu, key in ((a, "power_source_a"), (b, "power_source_b")):
            if pdu is None:
                continue
            try:
                self.s.topology.add_link(dev.id, pdu.id, layer="power")
                upd[key] = pdu.id
            except Exception:
                pass
        if upd:
            try:
                self.s.device_manager.update_device(dev.id, **upd)
            except Exception:
                pass

    def _wire_sensor_power(self, dev: Device, rk: tuple) -> None:
        """Environmental probes mount in the cold aisle, not a rack, and draw a
        single feed from a nearby rack PDU-B — like the curated hall sensors. Cords
        to an existing hall PDU-B (no per-sensor PDU is created)."""
        pdub = next((d for d in self._by_type(DeviceType.PDU)
                     if self._room_key(d) == rk
                     and "".join(c for c in (d.name or "").split("-", 1)[0]
                                 if c.isalpha()).upper() == "PDUB"), None)
        if pdub is None:
            return
        try:
            self.s.topology.add_link(dev.id, pdub.id, layer="power")
            self.s.device_manager.update_device(dev.id, power_source_b=pdub.id)
        except Exception:
            pass

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
                "oobm": next(iter(self._hall_oobms(rk)), None),
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

    def _fill_hall_grid(self, summ: DaySummary, dc: Optional[str] = None,
                        room: Optional[str] = None) -> Optional[dict]:
        """Add the next compute rack to a hall that's still under its grid cap.
        Most-occupied hall first, so one hall fills before the next is touched.
        Racks fill ROW-MAJOR (each compute row packs full before the next opens),
        so freed in-row gaps are used before a new row is started. The cap
        (racks_per_row x compute_rows) and row width come from each hall's PHYSICAL
        extent, so a hall fills to its real floor capacity before a new hall opens.

        *dc* scopes the search to one datacenter's halls and *room* to a single
        hall within it (both used by the manual provision action, so the operator
        can target a specific hall); None spans all DCs (day churn)."""
        racks = self._hall_compute_racks()
        if dc is not None:
            racks = {rk: v for rk, v in racks.items() if rk[0] == dc}
        if room is not None:
            racks = {rk: v for rk, v in racks.items() if rk[2] == room}
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
        # Inset the lineup by CRAH_END_RESERVE at each end so the two mechanical
        # power panels (MPP) can stand in the wall's end bays, flanking the CRAHs
        # they feed. Kept in lock-step with tools/seed_hall_crahs.perimeter_positions.
        end = CRAH_END_RESERVE
        usable = max(1.0, width - 2 * end)
        return [(round(end + usable * (j + 0.5) / target, 4), back_y)
                for j in range(target)]

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
            self._wire_facility_mgmt(c, rk, infra)   # CRAH onto the hall BMS OOB
            self._commission(c)
            added.append(c)
        if added != existing:
            self._log(f"[Fleet] hall {dc}/{room} CRAHs {len(existing)}→{len(added)} "
                      f"(sized to ~{self._hall_crah_target(rk)} for capacity)")
        return added

    def _ensure_hall_mpps(self, rk: tuple, infra: Optional[dict] = None) -> list:
        """Install the hall's A/B Mechanical Power Panels (MPPA/MPPB) — the
        panelboards its CRAHs hang off, like the curated halls
        (tools/add_hall_mech_panels.py). Each panel is fed from the DC's same-side
        MCC (NOT UPS-backed — it inherits the mechanical bus and bus tie from the
        MCC above it) and stands in an end bay of the CRAH back wall; its mgmt lands
        on the hall's BMS OOB. Idempotent.

        MUST run BEFORE _ensure_hall_crahs so the new CRAHs cord to these panels
        (resolved by room) instead of home-running 400 A across the site to the
        plant MCCs."""
        dc, floor, room = rk
        existing = [d for d in self.s.device_manager.get_all_devices()
                    if d.device_type == DeviceType.MPP and self._room_key(d) == rk]
        if existing:
            return existing
        # The DC's two Motor Control Centers, A-side then B-side (each MPP inherits
        # its source from the same-side MCC).
        mccs = sorted((d for d in self.s.device_manager.get_all_devices()
                       if d.device_type == DeviceType.MCC and (d.datacenter or "") == dc),
                      key=lambda d: d.name or "")
        if len(mccs) < 2:
            self._log(f"[Fleet] {dc}/{room}: <2 MCCs — hall MPPs skipped")
            return []
        tmpl = next((d for d in self.s.device_manager.get_all_devices()
                     if d.device_type == DeviceType.MPP), None)
        if tmpl is None:
            self._log(f"[Fleet] {dc}/{room}: no MPP template to clone — skipped")
            return []
        rpr, _cr, _first, n_rows = self._hall_grid(rk)
        ext = self._hall_extent(rk) or {}
        width_m = float(ext.get("width_m") or (rpr * geo.RACK_PITCH + 2 * geo.rack_x(1)))
        back_y = round(geo.row_y(n_rows), 4)             # CRAH back wall
        # CRAHs will take rack_num 200..200+target-1 (see _ensure_hall_crahs); the
        # panels stand in the end bays just past them.
        target = self._hall_crah_target(rk)
        row = self._row_label(rk, "crah")                # share the back-wall row label
        made = []
        for side, mcc, num, fx in (("a", mccs[0], 200 + target, 0.3),
                                   ("b", mccs[1], 200 + target + 1, round(width_m - 0.3, 3))):
            c = self._clone(tmpl, dc, row, num, 0, prefix=f"mpp{side}",
                            floor=floor, room=room, fx=fx, fy=back_y)
            if c is None:
                continue
            try:
                self.s.topology.add_link(mcc.id, c.id, layer="power")
            except Exception as e:
                self._log(f"[Fleet] MPP feed {c.name}: {e}")
            self._wire_facility_mgmt(c, rk, infra)       # panel onto the hall BMS OOB
            self._commission(c)
            made.append(c)
        if made:
            self._log(f"[Fleet] {dc}/{room}: +{len(made)} MPP "
                      f"(fed from {mccs[0].name}/{mccs[1].name}, mgmt on BMS OOB)")
        return made

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

    def _open_new_hall(self, summ: DaySummary,
                       dc: Optional[str] = None) -> Optional[dict]:
        """Commission a brand-new server hall in *dc* (the busiest DC when None),
        built to look like the curated halls: it CLONES the source hall's floor-plan
        extent (so it is the same physical size/shape), lays its power + pod-network
        gear on the front row, spreads its CRAHs along the back wall, and puts the
        first compute rack in the first middle row. Subsequent provisions fill the
        middle-row grid via _fill_hall_grid — the extent-derived cap fills the
        hall to capacity before another new hall opens."""
        dc = dc or self._busiest_dc()
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

        # ── Front row (row 1): pod network then power, laid out like the curated
        # halls — spines (2/rack) in cols 1-2, OOB in col 3, then the RPP pair
        # FLANKING the row: RPPA in col 4 (just past the network gear), RPPB at the
        # far end (last column). Both fed by the source RPPs' UPS so hall load still
        # reaches the UPS/generator. (Previously the RPPs sat in cols 1-2 and pushed
        # the network gear right, so a fleet/manual hall did not match curated.)
        front_y = round(geo.row_y(1), 4)
        # RPPB flanks the far end of the row at the PHYSICAL column count
        # (racks_per_row from the extent), NOT the compute-grid `rpr`: _hall_grid is
        # called here BEFORE the spines exist, so it can't see a local spine and
        # mis-reads the hall as a compute annex — which shrinks compute_rows and, via
        # the RPP-pole cap, `rpr` (e.g. 10 instead of 13). The compute grid fills
        # correctly (3x13) once the spines are up; only this placement needed the
        # true physical width.
        last_col = int(ext.get("racks_per_row") or rpr)
        new_infra["rpp_a"] = self._clone_rpp(infra["rpp_a"], rk, "A", front_y, col=4)
        new_infra["rpp_b"] = self._clone_rpp(infra["rpp_b"], rk, "B", front_y, col=last_col) if infra["rpp_b"] else None
        # Every RPP gets its OWN EV2-42 meter (like the curated halls), so a new
        # hall's power is monitored from day one — not only spill panels.
        for _rpp in (new_infra["rpp_a"], new_infra["rpp_b"]):
            if _rpp is not None:
                self._provision_ev2_for_rpp(_rpp, rk)
        # Pack the front-row network gear like the curated halls: TWO spines per
        # rack (U42/U41) in cols 1-2, then the OOB switch in its own rack (col 3) —
        # NOT one rack per spine, which spread a 4-spine pod across four cabinets.
        # The RPP pair flanks this gear (cols 4 and last), matching curated.
        spines = list(infra.get("spines") or [])
        new_spines, new_oob = [], None
        col = 1
        for p in range(0, len(spines), 2):
            fx = geo.rack_x(min(col, rpr))
            for j, tmpl in enumerate(spines[p:p + 2]):
                c = self._clone_fabric_node(tmpl, rk, "sp", num=100 + col,
                                            fx=fx, fy=front_y, unit=42 - j)
                if c is None:
                    continue
                self._wire_network_power(c, rk, new_infra)   # cord to network-row PDUs
                self._commission(c)
                new_spines.append(c)
            col += 1
        if infra.get("oob") is not None:                     # OOB in its own rack
            fx = geo.rack_x(min(col, rpr))
            c = self._clone_fabric_node(infra["oob"], rk, "oob", num=100 + col,
                                        fx=fx, fy=front_y)
            if c is not None:
                self._wire_network_power(c, rk, new_infra)
                self._commission(c)
                new_oob = c
        if new_spines:
            new_infra["spines"] = new_spines
        if new_oob is not None:
            new_infra["oob"] = new_oob
            # _clone_fabric_node copied each new spine's console link from the
            # TEMPLATE spine, so it points at the SOURCE hall's access OOB. Re-home
            # it onto THIS hall's own OOB so the pod is self-contained and its port
            # load counts against the right switch.
            for sp in new_spines:
                try:
                    for nbr in list(self.s.topology.graph.neighbors(sp.id)):
                        d = self.s.device_manager.get_device(nbr)
                        if (d and d.device_type == DeviceType.OOB_SWITCH
                                and d.id != new_oob.id and self._room_key(d) != rk):
                            self.s.topology.remove_link(sp.id, d.id, "management")
                    self.s.topology.add_link(sp.id, new_oob.id,
                                             src_iface=self._mgmt_port_iface(sp),
                                             layer="management")   # spine console on its mgmt0
                except Exception as e:
                    self._log(f"[Fleet] spine console re-home {sp.name}: {e}")

            # The spine/OOB-rack PDUs were created (in _wire_network_power ->
            # _ensure_rack_pdus) before this hall had an OOB, so they carry no mgmt
            # link yet. Land them on the hall access OOB now, like the curated
            # network-rack PDUs. Idempotent: skips any already on an OOB.
            for pdu in self._by_type(DeviceType.PDU):
                if self._room_key(pdu) != rk:
                    continue
                if self._neighbors(pdu, "management", (DeviceType.OOB_SWITCH,)):
                    continue
                poob = self._oob_port_for(rk, new_infra)
                if poob is not None:
                    try:
                        self.s.topology.add_link(pdu.id, poob.id, layer="management")
                    except Exception as e:
                        self._log(f"[Fleet] network PDU mgmt {pdu.name}: {e}")

        # ── Back wall: the hall's own A/B mechanical power panels FIRST, so the
        # CRAHs below cord to them (resolved by room) instead of home-running to the
        # plant MCCs — same as the curated halls (tools/add_hall_mech_panels.py).
        self._ensure_hall_mpps(rk, new_infra)

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
                    self._wire_facility_mgmt(c, rk, new_infra)   # sensor onto BMS OOB
                    self._wire_sensor_power(c, rk)               # sensor to a rack PDU-B
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
                   y: Optional[float] = None, col: Optional[int] = None) -> Optional[Device]:
        """Clone a Remote Power Panel into hall *rk* and feed it from the same UPS
        the template draws from, so downstream rack load reaches the UPS. *col* is
        the row-1 rack column the panel stands in — the curated halls flank the row
        (RPPA near the network gear, RPPB at the far end); defaults to 1/2 for A/B."""
        dc, floor, room = rk
        num = col if col is not None else (1 if side == "A" else 2)
        ups = self._neighbor(tmpl, "power", (DeviceType.UPS,))
        rpp = self._clone(tmpl, dc, self._row_label(rk, "rpp"),
                          num, PDU_UNIT,
                          prefix=f"rpp{side.lower()}", floor=floor, room=room,
                          fx=geo.rack_x(num), fy=y)
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
        self._wire_facility_mgmt(ev2, rk)   # EV2 meter onto the hall BMS OOB
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
            # Leaf console onto an OOB with a free port (stacks a new OOB if full).
            oob = self._oob_port_for(rk, infra)
            if oob is not None:
                self.s.topology.add_link(leaf.id, oob.id,
                                         src_iface=self._mgmt_port_iface(leaf),
                                         layer="management")   # leaf console on its mgmt0
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
            # Managed rack PDU onto a hall OOB management port (SNMP/Modbus over the
            # OOB plane), like the curated rack PDUs. Consumes a port too, so it
            # feeds the same stacking accounting — ~2 per rack.
            try:
                poob = self._oob_port_for(rk, infra)
                if poob is not None:
                    self.s.topology.add_link(pdu.id, poob.id, layer="management")
            except Exception as e:
                self._log(f"[Fleet] PDU mgmt link {pdu.name}: {e}")
            self._commission(pdu)
            pdus.append(pdu)

        # Cord the leaf to its OWN rack's A/B PDUs, like a curated ToR — otherwise
        # the switch draws no power and its load never reaches the PDU/RPP/UPS.
        if pdus:
            upd = {}
            for pdu, key in zip(pdus, ("power_source_a", "power_source_b")):
                try:
                    self.s.topology.add_link(leaf.id, pdu.id, layer="power")
                    upd[key] = pdu.id
                except Exception:
                    pass
            if upd:
                try:
                    self.s.device_manager.update_device(leaf.id, **upd)
                except Exception:
                    pass
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

    def _canvas_pos(self, dev: Device) -> tuple:
        """Canvas slot for a fleet-added device, from the SAME rules the batch
        layout uses (core/canvas_layout). The fleet used to lay its nodes on a
        private grid dumped below the curated ones, so a hall grown at runtime
        looked nothing like the same hall laid out by tools/layout_canvas.py.

        place_one() returns the canonical coordinate plus any nodes that must shift
        to seat it — a role whose sub-rows are full needs a new one, which pushes
        the rows below it down. Appending to a row with a free column moves nothing,
        which is the overwhelmingly common case. The moves are applied here so the
        hall stays inside its room rectangle instead of spilling out of it.

        O(n log n) in the device count, run once per created device. Fine for the
        handful a churn tick makes; it would be the wrong shape for a bulk load."""
        from core.canvas_layout import place_one
        try:
            topo = self.s.topology
            existing = []
            for d in topo.get_all_devices():
                x, y = topo.get_position(d.id)
                existing.append((d.name, d.datacenter or "", d.room or "", x, y))
            pos, moves = place_one(dev.name, dev.datacenter or "",
                                   dev.room or "", existing)
            if moves:
                id_of = {d.name: d.id for d in topo.get_all_devices()}
                for name, (mx, my) in moves.items():
                    if name in id_of:
                        topo.set_position(id_of[name], mx, my)
                self._log(f"[Fleet] canvas: {dev.name} opened a new row; "
                          f"shifted {len(moves)} node(s) to keep the layout canonical")
            return pos
        except Exception:
            self._log("[Fleet] canvas placement failed; parking at the origin")
            return (0.0, 0.0)

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
            self.s.topology.add_link(dev.id, rack["tor"].id,
                                     src_iface=0, layer="production")   # data NIC 0 -> ToR
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
        # Server BMC (iDRAC/iLO/XCC) onto a hall OOB management port, like a real
        # server that answers Redfish/IPMI out-of-band. This is what ties the OOB
        # switch count to the server count: each BMC eats one management port, so
        # the plane fills from compute and stacks a new OOB as servers grow — not
        # only when leaf/rack count crosses a threshold.
        try:
            tor = rack.get("tor")
            if tor is not None:
                rk = self._room_key(tor)
                leaf_oob = self._neighbor(tor, "management", (DeviceType.OOB_SWITCH,))
                oob = self._oob_port_for(rk, {"oob": leaf_oob})
                if oob is not None:
                    self.s.topology.add_link(dev.id, oob.id,
                                             src_iface=self._mgmt_port_iface(dev),
                                             layer="management")   # BMC (iLO/iDRAC) port -> OOB
        except Exception as e:
            self._log(f"[Fleet] BMC mgmt link {dev.name} failed: {e}")
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
        # curated peer: <CODE>-<DC>-<ROOM>-R<row>-<rack> (see _scheme_name). When
        # filling an existing rack the caller passes room=None so the room FIELD
        # inherits the template's hall — resolve that SAME hall here for the NAME
        # too, else it falls through to the "XX" room-code and drops the R#-##
        # location suffix (SRV05-DC1-XX instead of SRV05-DC1-HA-R2-01).
        dcn = dc or "DC"
        eff_room = room if room is not None else getattr(tmpl, "room", "")
        if prefix.startswith(("pdu", "rpp", "mpp")):
            base = ("PDU" if prefix.startswith("pdu")
                    else "RPP" if prefix.startswith("rpp") else "MPP")
            code = base + ("B" if prefix.endswith("b") else "A")
            name = self._scheme_name(dcn, eff_room, row, num, code, sided=True)
        else:
            code = _PREFIX_CODE.get(prefix, (prefix.upper() or "DEV").replace(" ", "-"))
            name = self._scheme_name(dcn, eff_room, row, num, code,
                                     pad=(2 if prefix == "srv" else 1))
        # Copy the template's PORT LAYOUT verbatim — the named ports (incl. the
        # dedicated mgmt/BMC port: mgmt0 / iLO / iDRAC / management / ...), their
        # speeds and the mixed-speed groups — so a fleet clone is indistinguishable
        # from its curated peer down to the console/BMC port. Fresh MAC + cleared
        # connections per interface (edges are (re)wired by the caller). Without this
        # the clone regenerates GENERIC ports and loses the named mgmt port, so its
        # console/BMC edge falls back onto a data port (the very collision the
        # curated topology was fixed for).
        cloned_ifaces = [Interface(index=itf.index, name=itf.name, speed=itf.speed,
                                   oper_status=1,
                                   role=getattr(itf, "role", InterfaceRole.DATA.value))
                         for itf in (getattr(tmpl, "interfaces", None) or [])]
        cloned_groups = [dict(g) for g in (getattr(tmpl, "interface_groups", None) or [])]
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
                interface_groups=cloned_groups,
                interfaces=cloned_ifaces,
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
                room=eff_room,   # same hall the name was built from (see above)
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
            # Canvas position from the shared layout rules (core/canvas_layout),
            # the same ones tools/layout_canvas.py applies in batch. Computed
            # BEFORE the device is registered, so the still-unplaced new node
            # cannot see itself. Cosmetic graph layout only — floor placement
            # lives in floor_x/floor_y.
            px, py = self._canvas_pos(dev)
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

    def place_device(self, device: Device) -> tuple:
        """Public entry point: give a manually-added device the SAME physical
        placement fleet computes — floor_x/floor_y + hot/cold aisle + facing from
        the rack grid (core/hall_geometry), and the canvas position from the shared
        layout (which also nudges peers to keep the hall canonical). Returns the
        canvas (x, y). A device without full rack coords (row/num) gets no floor
        placement and is parked at the canvas origin. Guarded — never raises."""
        try:
            row = int(getattr(device, "rack_row", 0) or 0)
            num = int(getattr(device, "rack_num", 0) or 0)
            if row > 0 and num > 0:
                sl = geo.slot(row, num)
                device.floor_x = sl.floor_x
                device.floor_y = sl.floor_y
                device.hot_aisle = sl.hot_aisle
                device.cold_aisle = sl.cold_aisle
                device.rack_facing = sl.rack_facing
        except Exception:
            self._log(f"[Fleet] floor placement {getattr(device,'name','?')} failed")
        return self._canvas_pos(device)

    def commission_device(self, device: Device) -> None:
        """Public entry point: bring a device online on the running protocol servers
        (SNMP/gNMI/Redfish/BACnet), the same path fleet churn uses. Called by the
        manual Add-Device API so a hand-added device answers immediately, without a
        regenerate + restart. Best-effort and guarded internally."""
        self._commission(device)

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

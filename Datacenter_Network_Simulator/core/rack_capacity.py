"""
Rack capacity + dual-homing (MLAG) forward-compat contract.

Single source of truth for (a) how many servers a compute rack can hold and
(b) the reserved geometry/ports that let a SECOND top-of-rack leaf be added
later for MLAG/vPC redundancy **without moving any existing server**. Imported
by the topology generator, the floor-plan exporter, and the fleet-lifecycle
engine so all three agree on the same numbers.

Why these values reflect real datacenter operations
----------------------------------------------------
* A leaf/ToR faceplate splits into many low-speed *downlink* (server-facing)
  ports plus a few high-speed *uplink* ports to the spines. A 48x25G + 6x100G
  leaf (Cisco Nexus 93180YC-FX, Dell S5248F-ON) has 48 server ports and 6
  uplinks. These are physically different cages (SFP28 vs QSFP28) — not one
  interchangeable pool.
* Per-rack server count is bounded by ONE leaf's downlinks (48), never 2x.
  This is the crucial property: a redundant (MLAG) server uses one downlink on
  EACH of the two leaves, so adding the second leaf does not raise the server
  count a rack can hold. The capacity is therefore **flip-invariant** —
  identical before and after dual-homing is adopted.
* In an enterprise hall a rack is power/thermal-bound (~10-15 kW) long before
  48 1U servers, so a power cap (~22) is usually the binding limit, not ports.
* Adding the 2nd leaf later needs a free RU and a couple of spare uplink ports
  for the inter-leaf MLAG peer-link. Both are reserved now so the future flip
  is non-disruptive: drop ToR-B into the reserved U, add the per-server second
  link, done.

Today the fleet is single-homed; nothing here turns dual-homing ON. It only
sizes and reserves so the eventual flip changes no capacity or geometry code.
"""
from __future__ import annotations

# ── Rack geometry (RU positions) ──────────────────────────────────────────────
TOR_A_UNIT = 42            # primary ToR / leaf (active today)
TOR_B_UNIT = 41            # RESERVED for the future MLAG peer leaf — keep empty
PDU_UNIT = 0               # rack PDUs are 0U vertical side-rail mounts (no RU)
FIRST_SERVER_UNIT = 1
LAST_SERVER_UNIT = 40      # U41/U42 reserved for the ToR pair
# In-rack CDU (4U) sits at the TOP of the server area, directly under the reserved
# ToR pair — U37-40. High placement keeps the coolant hoses short to the manifold
# and out of the way of server rails, and matches where the curated CDUs were
# re-seated to (tools/reseat_cdu_below_tor_reserve.py). It consumes server U, so a
# liquid rack holds correspondingly fewer machines.
CDU_UNIT = 37
# Modeled rack-server form factor. The curated topology uses 2U 2-socket servers
# (~650 W), placed on a 2U cadence (odd units 1,3,5…), so U1..U40 holds
# 40 / 2 = 20 servers. The fleet engine steps placement by this height so its
# racks match curated ones — space-bound at ~20 servers with power headroom,
# rather than the old 1U cadence that stranded rack U at the power cap.
SERVER_U_HEIGHT = 2
SERVERS_PER_RACK_BY_U = (LAST_SERVER_UNIT - FIRST_SERVER_UNIT + 1) // SERVER_U_HEIGHT

# ── Capacity knobs ────────────────────────────────────────────────────────────
POWER_CAP_DEFAULT = 22     # legacy server-COUNT proxy; still used by the static
                           # floor-plan exporter for a coarse per-rack capacity
                           # column. The live fleet engine no longer fills by
                           # count — it sums device nameplate watts vs the budget
                           # below (see rack_has_power_headroom).
RACK_POWER_BUDGET_W_DEFAULT = 17600   # usable per-rack power budget (W), 17.6 kW.
                           # = a 22 kW 3-phase rack PDU derated to the NEC 80%
                           # continuous-load rule (22000 × 0.8). This is what ONE
                           # PDU delivers, i.e. the A/B-failover ceiling, so the
                           # rack fills to its full single-feed capacity. The rack
                           # fills until summed nameplate draw of its kit would
                           # exceed it. (Fleet caps per rack at the actual PDU
                           # rating × 0.8 too — see FleetLifecycleEngine._rack_budget_w.)
MLAG_PEERLINK_PORTS = 2    # uplink ports held back for the inter-leaf peer-link

# (downlink_ports, uplink_ports) per known leaf model. Downlink = server-facing.
_LEAF_PORT_ROLES = {
    "Cisco Nexus 93180YC-FX": (48, 6),
    "Dell S5248F-ON":         (48, 6),
}
# default uplink count when a model is unknown (typical 1RU 25G leaf)
_DEFAULT_UPLINKS = 6


# Rack height (U) of the NON-server gear that gets racked, by type. Coarser than the
# per-SKU server catalog on purpose: these types have one representative chassis each in
# this sim, so a type answer is honest where a SKU catalog would be invented precision.
#
# Seeded from the 3D floor-plan viewer's own DEV_U table (webui/public/
# floorplan_viewer.html), which has drawn these heights correctly since before the
# backend modelled height at all — a 2U PA-5220 firewall and a 4U CoolIT CHx80 CDU are
# real. The two must agree: the viewer now reads the u_height this produces, so a
# divergence here silently redraws the estate.
#
# Absent = 1U. That covers the 1RU network gear and, harmlessly, the 0U side-rail PDUs
# and aisle-mounted sensors, which carry rack_unit 0 and never span anything.
_TYPE_U_HEIGHT = {
    "firewall": 2,      # PA-5220 and friends are 2U NGFW appliances
    "cdu":      4,      # CoolIT CHx80 — in-rack coolant distribution
}


def device_u_height(device_type, model_name: str = "") -> int:
    """Rack units a device's body occupies — from its SKU when we know it.

    Height belongs to the MODEL: a DL360 is 1U, a DL380 2U, a DL560 4U. A server whose
    SKU is not in MODEL_U_HEIGHT falls back to SERVER_U_HEIGHT. Non-servers come from
    _TYPE_U_HEIGHT, defaulting to 1U.

    Occupancy is a SPAN, not a point: a 2U server at U1 fills U1 AND U2, so anything
    asking "is this U free" must walk the whole body. Reading only each device's own
    rack_unit left every even U looking free — and a 4U CDU at U38 looking like it left
    room for a 2U server at U39.

    Accepts a DeviceType or a plain string. device_models is imported lazily: it
    imports device_manager, so a module-level import here would risk a cycle."""
    dt = getattr(device_type, "value", device_type)
    if dt != "server":
        return _TYPE_U_HEIGHT.get(dt, 1)
    if model_name:
        try:
            from core.device_models import MODEL_U_HEIGHT
        except Exception:
            return SERVER_U_HEIGHT
        h = MODEL_U_HEIGHT.get(model_name)
        if h:
            return h
    return SERVER_U_HEIGHT


def leaf_port_roles(model_name: str, interface_count: int = 54) -> tuple[int, int]:
    """Return (downlink_ports, uplink_ports) for a leaf switch.

    Known models use their real split; unknown models fall back to reserving
    _DEFAULT_UPLINKS high-speed uplinks and treating the rest as downlinks."""
    if model_name in _LEAF_PORT_ROLES:
        return _LEAF_PORT_ROLES[model_name]
    uplinks = min(_DEFAULT_UPLINKS, interface_count)
    return (interface_count - uplinks, uplinks)


def rack_server_capacity(downlink_ports: int,
                         power_cap: int = POWER_CAP_DEFAULT) -> int:
    """Max servers per rack — the binding minimum of server-facing ports and
    the power/thermal budget. Flip-invariant across single/dual-homing."""
    return max(0, min(downlink_ports, power_cap))


# ── Air-side thermal budget ───────────────────────────────────────────────────
#
# A rack has TWO independent ceilings and the lower one binds. The electrical budget
# above is what the PDU can deliver; this is what the room's air can carry away.
#
# Air cooling is limited by the airflow reaching the cabinet and the allowable rise
# across it: Q = ṁ·cp·ΔT. A perimeter-CRAH hall with hot/cold-aisle containment
# lands around 10-15 kW per rack; open-aisle halls are lower (5-8 kW), and pushing
# air past ~25-30 kW per rack stops being practical at all — which is precisely why
# direct-to-chip cooling exists.
#
# 15 kW sits in that contained-perimeter band and, being BELOW the 17.6 kW electrical
# budget, is the constraint that actually binds in a dense rack. That is the honest
# ordering: these halls run out of cooling before they run out of amps.
#
# Not lower: the curated all-air compute racks already run 12.6-12.8 kW, which is a
# perfectly buildable contained rack. A 12 kW budget would have retroactively declared
# a third of the estate illegal and refused every further server in it — a budget that
# calls the existing plant impossible is measuring the wrong thing.
#
# A design parameter, not a measurement — a hall with in-row coolers or a taller ΔT
# would carry more. Callers may pass their own budget.
RACK_AIR_BUDGET_W_DEFAULT = 15000

# Fraction of a direct-to-chip server's heat that still leaves via AIR. Cold plates
# capture the CPU/GPU load into the coolant loop; the residual (VRMs, DIMMs, drives,
# PSUs) is air-cooled. THE one definition — core.device_state_store imports it for
# the exhaust-ΔT and fan curves, so the thermal model and the capacity model cannot
# disagree about how much heat a liquid server puts in the room.
DTC_AIR_FRACTION = 0.30

# The IT gear whose heat a RACK's air budget actually has to carry. An allow-list,
# not a deny-list, and that direction matters: the first cut excluded cdu/pdu/sensor
# and so happily charged a Central Plant "rack" with 564 kW of chiller and the roof
# with 90 kW of cooling tower. Plant equipment is not in a cabinet — its heat goes to
# the condenser loop and outdoors — so a per-rack air budget is meaningless there.
#
# Excluded IT kit and why:
#   cdu   — pump work goes into the coolant it is pumping and leaves via the facility
#           water loop, not the cabinet's air.
#   pdu   — a strip dissipates only its own I²R losses (~1-2 %), already inside the
#           connected kit's nameplate for capacity purposes.
#   sensor— milliwatts.
_AIR_LOAD_TYPES = frozenset({
    "server", "switch", "router", "firewall", "load_balancer", "oob_switch",
})


def device_air_load_w(device_type, draw_w: float, liquid_cooled: bool = False) -> float:
    """Watts this device rejects into the ROOM AIR (not its total draw).

    An air-cooled server, a switch and anything else with fans rejects all of it. A
    direct-to-chip server rejects only DTC_AIR_FRACTION — that is the entire point of
    plumbing it, and counting its full draw against the air budget would make a
    liquid rack look as air-hungry as an all-air one."""
    dt = getattr(device_type, "value", device_type)
    if dt not in _AIR_LOAD_TYPES:
        return 0.0
    w = max(0.0, float(draw_w or 0.0))
    return w * (DTC_AIR_FRACTION if (dt == "server" and liquid_cooled) else 1.0)


def rack_has_air_headroom(current_air_w: float, add_air_w: float,
                          budget_w: int = RACK_AIR_BUDGET_W_DEFAULT) -> bool:
    """True if the rack's air-cooled heat stays inside what the room can carry.

    The co-limit to rack_has_power_headroom: a hybrid rack can hold FEWER air-cooled
    servers than an all-air one, because the liquid machines beside them still dump
    their residual air fraction into the same cabinet. Not a ban on mixing — a budget
    that mixing consumes."""
    return (current_air_w + add_air_w) <= budget_w


def rack_has_power_headroom(current_w: float, add_w: float,
                            budget_w: int = RACK_POWER_BUDGET_W_DEFAULT) -> bool:
    """True if adding a device drawing *add_w* watts keeps the rack's summed
    nameplate draw within its provisioned power budget.

    Models how a rack is really filled: capacity planning sums the design/
    nameplate power of installed kit (servers + ToR) and stops before the budget
    (PDU/branch-circuit rating and per-rack cooling) is exceeded — it does NOT
    fill to a flat server count. Port count is a separate physical co-limit."""
    return (current_w + add_w) <= budget_w


def usable_uplinks(uplink_ports: int) -> int:
    """Uplinks available to the spines after reserving the MLAG peer-link."""
    return max(0, uplink_ports - MLAG_PEERLINK_PORTS)


def leaf_interface_groups(model_name: str, interface_count: int = 54) -> list[dict]:
    """Realistic two-group interface layout for a leaf: a 25G downlink group +
    a 100G uplink group. Returned as plain str/int dicts so it round-trips
    through Device(interface_groups=...) and topology JSON unchanged."""
    downlink, uplink = leaf_port_roles(model_name, interface_count)
    groups = []
    if downlink:
        groups.append({"iface_type": "25 Gigabit Ethernet (25 Gbps)",
                       "count": downlink})
    if uplink:
        groups.append({"iface_type": "100 Gigabit Ethernet (100 Gbps)",
                       "count": uplink})
    return groups
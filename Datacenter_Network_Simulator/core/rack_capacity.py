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
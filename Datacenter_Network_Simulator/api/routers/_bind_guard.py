"""Shared pre-start guard: a simulator binds to host IPs, it does not create them.

Address assignment belongs to the Binding panel — that panel is this simulator's
IPAM / interface-config step. Real agents work the same way: snmpd does not give
the host an address, it binds one that DHCP or static config already put on the
interface, and fails EADDRNOTAVAIL when it isn't there. So every simulator checks
its OWN address set before starting and refuses on a gap, instead of silently
reconfiguring the host NIC as a side effect of pressing "Start".

The check is per-simulator, NEVER global. A partial bind is legitimate — gNMI needs
46 of ~1000 topology addresses — so a global "is everything bound" test would refuse
starts that would work perfectly.

Runtime commissioning is a different thing and is deliberately left alone: when the
fleet engine hot-adds a device to an already-running simulator there is no panel
interaction to hang a bind off, so AppState.reload_snmp_datasets() still binds those
IPs itself.
"""
from __future__ import annotations

from typing import Iterable, List

from fastapi import HTTPException

# How many addresses to name before collapsing the rest into a count.
_MAX_SHOWN = 8


def bound_set(s) -> set:
    """Every IP this app has aliased on the host.

    One list, because an IP alias has no owner: which simulator's start happened
    to bind it says nothing about whether a socket can bind it now.
    """
    return set(s.bound_ips)


def unbound(s, ips: Iterable[str]) -> List[str]:
    """Which of *ips* the host does not currently carry — deduped, input order."""
    have = bound_set(s)
    seen: set = set()
    out: List[str] = []
    for ip in ips:
        if ip and ip not in have and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def require_bound(s, ips: Iterable[str], what: str) -> None:
    """Raise 400 naming the actual gap, or return quietly.

    The message lists real addresses: "bind IPs first" on its own leaves the user
    to work out which of a thousand addresses is missing.
    """
    wanted = [ip for ip in ips if ip]
    if not wanted:
        return
    missing = unbound(s, wanted)
    if not missing:
        return

    shown = ", ".join(missing[:_MAX_SHOWN])
    if len(missing) > _MAX_SHOWN:
        shown += f", … (+{len(missing) - _MAX_SHOWN} more)"
    # detail is a {"user_message": ...} object, not a bare string, because the
    # web client shows ONLY messages an endpoint opted in this way and drops
    # everything else to the console (see webui/src/api/client.ts). A bare
    # string here would reach the operator as "Some of the information provided
    # is invalid" — which tells them nothing about which IPs to go bind.
    raise HTTPException(
        status_code=400,
        detail={
            "user_message": (
                f"{len(missing)} of {len(set(wanted))} {what} are not bound to a "
                f"host interface: {shown}. Bind them from the Binding panel, "
                f"then start."
            ),
            "unbound_count": len(missing),
            "unbound_ips": missing[:_MAX_SHOWN],
        },
    )

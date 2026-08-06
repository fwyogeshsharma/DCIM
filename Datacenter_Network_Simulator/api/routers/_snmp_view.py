"""What a UI should say about a device's SNMP reachability.

One authority for every surface that prints an SNMP port (device list, canvas
tooltip, device-info modal). Two fields drift constantly and printing the wrong
one sends an operator polling a closed socket:

  * device.snmp_port is the CONFIGURED intent, a static field defaulting to 161.
  * the simulator serves on whatever port was chosen at start — 1611 is the normal
    choice, because 161 is privileged and needs root.

And "has a port" is not the same as "has an agent": BACnet/Modbus plant gear
(chiller, pump, cooling tower, valve) and passive panels (RPP) carry no SNMP card
at all, yet still hold snmp_port=161 in the record.
"""
from __future__ import annotations

import time

# Live serving state, memoised for a beat: these run once per device, and a
# 659-device payload would otherwise rebuild the 900+ endpoint set that many
# times. Short enough that a Start/Stop shows up on the next poll.
_SERVING_TTL = 2.0
_serving_cache: tuple[float, int | None, set] = (0.0, None, set())


def snmp_serving() -> tuple[int | None, set]:
    """(port, ips) actually being served by the SNMP simulator right now.

    get_active_endpoints() is already gated on is_ready() — it returns [] unless a
    socket is really bound — so an IP in this set means "an agent answers here",
    not "we intended to serve it".
    """
    global _serving_cache
    now = time.monotonic()
    ts, port, ips = _serving_cache
    if now - ts < _SERVING_TTL:
        return port, ips
    port, ips = None, set()
    try:
        from api.state import AppState
        sim = getattr(AppState.get(), "snmpsim", None)
        if sim is not None:
            eps = sim.get_active_endpoints()
            if eps:
                port = sim.get_port()
                ips = {e.rsplit(":", 1)[0] for e in eps}
    except Exception:
        port, ips = None, set()
    _serving_cache = (now, port, ips)
    return port, ips


def snmp_facts(device) -> tuple[bool, int | None]:
    """(has an SNMP agent, port it is served on now).

    snmp_bind_ips() is the authority on the first half — the same function the
    binder and the dataset reaper use, so this cannot drift from what actually
    gets generated. It returns [] for the types that have no agent.
    """
    from core.snmprec_generator import SNMPRecGenerator as _Gen
    try:
        ips = _Gen.snmp_bind_ips(device)
    except Exception:
        ips = []
    if not ips:
        return False, None
    port, live = snmp_serving()
    if port is None or not any(ip in live for ip in ips):
        return True, None
    return True, port

"""Which IPs a device's SNMP agent(s) would be served on — the STATIC half of
what a UI needs to talk about SNMP reachability.

Deliberately no live state here. Whether a socket is bound changes on every
Start/Stop, while /devices and /topology/graph are fetched on topology change
only — embedding a port in them freezes it at page-load time, which is exactly
how a running 1611 sim ends up rendered as "161 (not serving)". The live half
comes from /snmp/status, which the UI already polls; the UI joins the two.

What is static and belongs here:
  * has an SNMP agent at all — BACnet/Modbus plant gear (chiller, pump, cooling
    tower, valve) and passive panels (RPP) carry no SNMP card, yet still hold
    snmp_port=161 in the record.
  * the addresses that agent answers on (OS/NOS agent, plus the BMC for servers).
"""
from __future__ import annotations


def snmp_agent_ips(device) -> list[str]:
    """Addresses snmpsim serves for this device — [] when it has no agent.

    snmp_bind_ips() is the authority: the same function the binder and the dataset
    reaper use, so this cannot drift from what actually gets generated.
    """
    from core.snmprec_generator import SNMPRecGenerator as _Gen
    try:
        return list(_Gen.snmp_bind_ips(device))
    except Exception:
        return []

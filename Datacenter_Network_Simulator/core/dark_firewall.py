"""A device with no power is not on the network.

When every cord into a box is dead, nothing in it answers: not the OS agent,
not the BMC, not the NOS, not ICMP. The protocol servers each learned to go
quiet for a dark device - Redfish refuses, gNMI returns UNAVAILABLE, BACnet and
Modbus stop replying - but SNMP could not. One snmpsim process serves the whole
estate from its data directory, and the only lever it offers is the dataset
file. Deleting a dark device's file on the tick and re-creating it on restore
wedged snmpsim: its index fell to 586 of 892 datasets and it stopped answering
every community in both sites.

So the silence is made where a real outage makes it - on the wire. While a
device is dark its addresses are dropped by the host firewall, in a chain this
module owns; when power returns the drops are removed. snmpsim, the bindings
and every protocol socket are left exactly as they were, and a caller sees
what it would see of a powered-off box: every request times out, ICMP
included.

Linux only, and only as root with iptables installed. Anywhere else this logs
once and does nothing - the per-protocol checks still silence everything but
SNMP.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Callable, Iterable, Optional

log = logging.getLogger(__name__)

#: The chain this module owns. Nothing else writes to it, so it can be flushed
#: wholesale at start - which is what keeps a simulator that crashed mid-outage
#: from leaving devices unreachable for ever.
CHAIN = "DCIM_DARK"

Runner = Callable[[list], "subprocess.CompletedProcess"]


def _iptables(args: list) -> subprocess.CompletedProcess:
    # -w: wait for the xtables lock rather than failing when another tool holds
    # it, which on a busy host is the ordinary case, not an error.
    return subprocess.run(["iptables", "-w", *args], capture_output=True,
                          text=True, timeout=10)


class DarkFirewall:
    def __init__(self, runner: Optional[Runner] = None,
                 available: Optional[bool] = None):
        self._run = runner or _iptables
        self._available = available
        self._ready: Optional[bool] = None
        self._dropped: set = set()

    @property
    def dropped(self) -> set:
        return set(self._dropped)

    def _can_run(self) -> bool:
        if self._available is not None:
            return self._available
        return (sys.platform.startswith("linux")
                and hasattr(os, "geteuid") and os.geteuid() == 0
                and shutil.which("iptables") is not None)

    def _ok(self, args: list) -> bool:
        try:
            return self._run(args).returncode == 0
        except Exception as exc:          # timeout, missing binary mid-run
            log.warning("[DarkFirewall] iptables %s failed: %s", " ".join(args), exc)
            return False

    def _ensure(self) -> bool:
        """Create the chain, empty it, and hook it into INPUT - once."""
        if self._ready is not None:
            return self._ready
        if not self._can_run():
            log.info("[DarkFirewall] iptables unavailable (needs Linux, root and "
                     "the iptables package): dark devices will still answer SNMP")
            self._ready = False
            return False
        self._ok(["-N", CHAIN])                    # fails harmlessly if it exists
        if not self._ok(["-F", CHAIN]):
            log.warning("[DarkFirewall] cannot manage chain %s; disabled", CHAIN)
            self._ready = False
            return False
        # INPUT, because a local caller (the collector on the same host) reaches
        # a locally-bound device address through the loopback path, and every
        # locally delivered packet passes INPUT. Inserted first so no earlier
        # ACCEPT can let a dark box answer.
        if not self._ok(["-C", "INPUT", "-j", CHAIN]):
            self._ok(["-I", "INPUT", "1", "-j", CHAIN])
        self._dropped.clear()
        self._ready = True
        return True

    def sync(self, ips: Iterable[str]) -> None:
        """Make the dropped set exactly `ips`."""
        if not self._ensure():
            return
        want = {ip for ip in ips if ip}
        for ip in sorted(want - self._dropped):
            if self._ok(["-A", CHAIN, "-d", ip, "-j", "DROP"]):
                self._dropped.add(ip)
        for ip in sorted(self._dropped - want):
            # Removed from the set whether or not the delete worked: a rule
            # that is already gone is the state we wanted.
            self._ok(["-D", CHAIN, "-d", ip, "-j", "DROP"])
            self._dropped.discard(ip)

    def clear(self) -> None:
        """Drop nothing. Called on stop, so a stopped simulator leaves no trace."""
        if self._ready:
            self._ok(["-F", CHAIN])
        self._dropped.clear()

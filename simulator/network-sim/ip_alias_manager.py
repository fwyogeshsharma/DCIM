"""
Linux IP Alias Manager
Replaces the Windows netsh-based ip_binder.py for Docker/Linux containers.
Requires NET_ADMIN capability (cap_add: [NET_ADMIN] in docker-compose).
"""
from __future__ import annotations

import logging
import subprocess
from typing import List, Set

log = logging.getLogger(__name__)


class IPAliasManager:
    def __init__(self, interface: str = "lo"):
        self._interface = interface
        self._bound: Set[str] = set()

    def bind_ip(self, ip: str) -> bool:
        if ip in self._bound:
            return True
        try:
            subprocess.run(
                ["ip", "addr", "add", f"{ip}/32", "dev", self._interface],
                check=True, capture_output=True
            )
            self._bound.add(ip)
            log.debug("Bound %s on %s", ip, self._interface)
            return True
        except subprocess.CalledProcessError as e:
            # EEXIST is fine — already bound
            if b"RTNETLINK answers: File exists" in e.stderr:
                self._bound.add(ip)
                return True
            log.warning("Failed to bind %s: %s", ip, e.stderr.decode())
            return False

    def unbind_ip(self, ip: str) -> bool:
        if ip not in self._bound:
            return True
        try:
            subprocess.run(
                ["ip", "addr", "del", f"{ip}/32", "dev", self._interface],
                check=True, capture_output=True
            )
            self._bound.discard(ip)
            return True
        except subprocess.CalledProcessError as e:
            log.warning("Failed to unbind %s: %s", ip, e.stderr.decode())
            return False

    def bind_all(self, ips: List[str]) -> int:
        bound = 0
        for ip in ips:
            if self.bind_ip(ip):
                bound += 1
        log.info("Bound %d/%d IPs on %s", bound, len(ips), self._interface)
        return bound

    def cleanup(self):
        for ip in list(self._bound):
            self.unbind_ip(ip)
        log.info("Released all IP aliases")

"""
IP Address Manager - Assigns IPs from a pool.
"""
import ipaddress
from typing import Set


class IPManager:
    def __init__(self, subnet: str = "192.168.1.0/24", start_offset: int = 10):
        self.network = ipaddress.IPv4Network(subnet, strict=False)
        self._hosts = list(self.network.hosts())
        self._index = start_offset
        self._assigned: Set[str] = set()

    def next_ip(self) -> str:
        while self._index < len(self._hosts):
            ip = str(self._hosts[self._index])
            self._index += 1
            if ip not in self._assigned:
                self._assigned.add(ip)
                return ip
        raise RuntimeError("IP address pool exhausted")

    def reserve(self, ip: str):
        self._assigned.add(ip)

    def release(self, ip: str):
        self._assigned.discard(ip)

    def reset(self):
        self._index = 10
        self._assigned.clear()

    def is_available(self, ip: str) -> bool:
        try:
            addr = ipaddress.IPv4Address(ip)
            return addr in self.network and ip not in self._assigned
        except ValueError:
            return False

    def get_assigned(self):
        return list(self._assigned)

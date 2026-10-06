"""A v3 network can be narrowed to device types.

An IT-OOB subnet holds the switches, routers, firewalls and load balancers AND
the server BMCs. A real site moves the network gear to SNMPv3 first; the BMCs
beside it on the same subnet stay v2c, some for years. Scoping by CIDR alone
could not say that.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import snmp_v3
from core.device_manager import DeviceType

NET = {"cidr": "10.51.0.0/20", "site": "DC1", "user": "dcim-poll",
       "auth_proto": "SHA256", "auth_key": "authpass123", "priv_proto": "AES128",
       "priv_key": "privpass123"}
GEAR = ["switch", "oob_switch", "router", "firewall", "load_balancer"]


def _dev(ip, dtype, vendor="Cisco Systems"):
    return SimpleNamespace(ip=ip, device_type=DeviceType(dtype), vendor=vendor)


@pytest.fixture
def scoped(monkeypatch):
    cfg = {"enabled": True, "networks": [snmp_v3.validate_network({**NET, "device_types": GEAR})]}
    monkeypatch.setattr(snmp_v3, "load", lambda: cfg)


def test_the_gear_speaks_v3_and_the_bmcs_beside_it_do_not(scoped):
    devices = [_dev("10.51.11.10", "switch"), _dev("10.51.11.11", "firewall", "Palo Alto Networks"),
               _dev("10.51.11.40", "server", "Dell Technologies")]
    got = snmp_v3.agents(devices, lambda d: [d.ip])
    assert set(got) == {"10.51.11.10", "10.51.11.11"}


def test_the_trap_engine_asks_the_same_question(scoped):
    assert snmp_v3.agent_for("10.51.11.10", "Cisco Systems", "switch") is not None
    assert snmp_v3.agent_for("10.51.11.40", "Dell Technologies", "server") is None
    # A sender whose type is not known gets v2c, not a guess.
    assert snmp_v3.agent_for("10.51.11.10", "Cisco Systems") is None


def test_a_network_without_types_still_covers_everything(monkeypatch):
    cfg = {"enabled": True, "networks": [snmp_v3.validate_network(NET)]}
    monkeypatch.setattr(snmp_v3, "load", lambda: cfg)
    assert "device_types" not in cfg["networks"][0]
    assert snmp_v3.agent_for("10.51.11.40", "Dell Technologies", "server") is not None
    assert snmp_v3.agent_for("10.51.11.40", "Dell Technologies") is not None


def test_a_misspelt_type_is_refused_not_ignored():
    with pytest.raises(snmp_v3.V3ConfigError, match="swich"):
        snmp_v3.validate_network({**NET, "device_types": ["switch", "swich"]})

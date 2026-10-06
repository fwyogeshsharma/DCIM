"""SNMPv3 (USM) agents for the devices a site has moved off v2c.

How real estates do it, which this models:

- A site migrates to v3 by device class, not all at once. Facility gear on the
  BMS network (PDU, UPS, ATS and CRAH cards) and network kit usually go first
  under security baselines; server BMCs and OS agents often stay on v2c, read-only
  on an isolated management network, for years - and some cannot move at all (the
  Microsoft SNMP service is v1/v2c only). So v3 here is scoped by network (CIDR),
  optionally narrowed to device types: on the IT-OOB network the switches,
  routers, firewalls and load balancers move while the BMCs beside them on the
  same subnet stay v2c. A device in scope keeps answering v2c too - a card
  mid-migration.
- Every agent has its OWN engine ID (RFC 3411 SnmpEngineID). Pollers discover it
  and USM localises keys to it, so a passphrase stolen from one PDU's traffic is
  not a key to its neighbour. Format 1 (IPv4): the vendor's IANA enterprise number
  with the high bit set, then 0x01, then the address. Vendors whose number is not
  listed below get net-snmp's (8072) - an embedded card running net-snmp, which is
  what most of them are.
- One poll user per site, with DIFFERENT passphrases per site, so one leaked
  credential does not open the other datacenter. SHA-256 auth and AES-128 privacy
  by default; MD5/DES are what security policies now reject.

snmpsim serves this with one engine per device bound to the device's own address
(see simulator/snmpsim_controller.py), because it picks a device's data by v3
context name and a real agent is polled with the empty context. That costs about
1 MB and a quarter of a second of agent start-up per device (measured), which is
why the scope is a set of networks rather than "everything".
"""
from __future__ import annotations

import ipaddress
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core import sim_settings

log = logging.getLogger(__name__)

SETTINGS_KEY = "snmp_v3"

AUTH_PROTOCOLS = ("SHA", "SHA224", "SHA256", "SHA384", "SHA512", "MD5")
PRIV_PROTOCOLS = ("AES", "AES128", "AES192", "AES256", "DES")

#: IANA private enterprise numbers for the vendors whose cards speak SNMP here.
#: Only numbers known to be right; anything else is an embedded net-snmp (8072).
#: Checked against IANA's enterprise-numbers registry, 2026-10-06. A vendor's
#: number here and its sysObjectID arc (core.device_manager.VENDOR_SYSOID) are the
#: same registration, and tests/test_snmp_v3_identity.py holds them together.
ENTERPRISE = {
    "APC by Schneider Electric": 318,     # American Power Conversion Corp.
    # Schneider Electric's own number. It was 318, which is APC's: the ION9000
    # meter's engine ID said APC while its sysObjectID (3833) said Schneider.
    "Schneider Electric": 3833,
    "Vertiv (Liebert)": 476,
    "Eaton": 534,
    "Raritan": 13742,
    "Server Technology": 1718,
    "Cisco Systems": 9,
    "Juniper Networks": 2636,
    "Arista Networks": 30065,
    "Hewlett Packard Enterprise": 11,
    "Dell Technologies": 674,
    "Lenovo": 19046,
    "Supermicro": 10876,
    "IBM": 2,
    "Palo Alto Networks": 25461,
    "F5 Networks": 3375,
    "Huawei Technologies": 2011,
    "Extreme Networks": 1916,
    "CoolIT Systems": 30518,              # CoolIT Systems Inc.
    "Loytec": 42036,                      # LOYTEC electronics GmbH
    "Moxa": 8691,                         # Moxa Technologies Co., Ltd.
    # Not here, because IANA has no number for them: ASCO Power Technologies,
    # Verdigris Technologies. Caterpillar Inc. holds 19209, but a CAT EMCP reaches
    # a network through a gateway, and that gateway's agent is what answers.
}
NET_SNMP = 8072


@dataclass
class V3User:
    name: str
    auth_proto: str
    auth_key: str
    priv_proto: str
    priv_key: str


@dataclass
class V3Agent:
    ip: str
    engine_id: str          # hex, no 0x - the form snmpsim's --v3-engine-id takes
    users: List[V3User] = field(default_factory=list)


def engine_id(vendor: str, ip: str) -> str:
    """RFC 3411 SnmpEngineID, format 1 (IPv4): 4-octet enterprise with the
    high bit set, 0x01, then the 4 address octets."""
    ent = ENTERPRISE.get(vendor, NET_SNMP)
    return f"{0x80000000 | ent:08x}01{ipaddress.IPv4Address(ip).packed.hex()}"


def _secret() -> str:
    # 24 URL-safe characters: well past USM's 8-character minimum, and free of
    # the quoting hazards a passphrase with spaces or '#' brings to CLI args.
    return secrets.token_urlsafe(18)


def default_networks() -> List[Dict[str, Any]]:
    """The BMS plane of each site, with its own user and passphrases."""
    return [
        {"cidr": "10.52.0.0/20", "site": "DC1", "user": "dcim-poll",
         "auth_proto": "SHA256", "auth_key": _secret(),
         "priv_proto": "AES128", "priv_key": _secret()},
        {"cidr": "10.52.16.0/20", "site": "DC2", "user": "dcim-poll",
         "auth_proto": "SHA256", "auth_key": _secret(),
         "priv_proto": "AES128", "priv_key": _secret()},
    ]


class V3ConfigError(ValueError):
    """A v3 setting that cannot be served, with a message fit for the operator."""


def validate_network(n: Dict[str, Any]) -> Dict[str, Any]:
    try:
        cidr = str(ipaddress.IPv4Network(str(n.get("cidr", "")), strict=False))
    except ValueError:
        raise V3ConfigError(f"not an IPv4 network: {n.get('cidr')!r}") from None
    user = str(n.get("user") or "").strip()
    if not user or len(user) > 32:
        raise V3ConfigError(f"{cidr}: user must be 1-32 characters")
    auth = str(n.get("auth_proto") or "").upper()
    priv = str(n.get("priv_proto") or "").upper()
    if auth not in AUTH_PROTOCOLS:
        raise V3ConfigError(f"{cidr}: auth_proto must be one of {', '.join(AUTH_PROTOCOLS)}")
    if priv not in PRIV_PROTOCOLS:
        raise V3ConfigError(f"{cidr}: priv_proto must be one of {', '.join(PRIV_PROTOCOLS)}")
    notify = str(n.get("notify") or "trap").lower()
    if notify not in ("trap", "inform"):
        raise V3ConfigError(f"{cidr}: notify must be trap or inform")
    out = {"cidr": cidr, "site": str(n.get("site") or ""), "user": user,
           "auth_proto": auth, "priv_proto": priv, "notify": notify}
    # Only these device types speak v3 here; absent or empty means every SNMP
    # agent in the network. Checked against the real type names, so a typo
    # cannot quietly leave a whole class on v2c.
    types = n.get("device_types") or []
    if types:
        from core.device_manager import DeviceType
        known = {t.value for t in DeviceType}
        bad = [t for t in types if str(t) not in known]
        if bad:
            raise V3ConfigError(f"{cidr}: unknown device type(s) {', '.join(map(str, bad))}")
        out["device_types"] = sorted({str(t) for t in types})
    for k in ("auth_key", "priv_key"):
        v = str(n.get(k) or "")
        if len(v) < 8:
            # RFC 3414 passphrase minimum; net-snmp and every card enforce it.
            raise V3ConfigError(f"{cidr}: {k} must be at least 8 characters")
        if any(c.isspace() for c in v):
            raise V3ConfigError(f"{cidr}: {k} must not contain whitespace")
        out[k] = v
    return out


def load() -> Dict[str, Any]:
    cfg = sim_settings.get(SETTINGS_KEY, None) or {}
    return {"enabled": bool(cfg.get("enabled")), "networks": list(cfg.get("networks") or [])}


def save(enabled: bool, networks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Persist the v3 configuration. Enabling with no networks configured
    creates the default per-site BMS networks with fresh passphrases."""
    cur = load()
    nets = cur["networks"] if networks is None else networks
    if enabled and not nets:
        nets = default_networks()
    clean = [validate_network(n) for n in nets]
    seen: List[ipaddress.IPv4Network] = []
    for n in clean:
        net = ipaddress.IPv4Network(n["cidr"])
        if any(net.overlaps(o) for o in seen):
            raise V3ConfigError(f"{n['cidr']} overlaps another v3 network")
        seen.append(net)
    cfg = {"enabled": bool(enabled), "networks": clean}
    sim_settings.set(SETTINGS_KEY, cfg)
    return cfg


def redacted(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"enabled": cfg["enabled"],
            "networks": [{k: ("********" if k in ("auth_key", "priv_key") else v)
                          for k, v in n.items()} for n in cfg["networks"]]}


def _type_of(device) -> str:
    t = getattr(device, "device_type", None)
    return str(getattr(t, "value", t) or "")


def _in_scope(n: Dict[str, Any], device_type: Optional[str]) -> bool:
    """Does network entry `n` cover a device of this type? A network with no
    device_types covers every type; one with a list covers only those - and a
    device whose type is unknown, not any of them."""
    types = n.get("device_types")
    return not types or (device_type or "") in types


def agents(devices, bind_ips) -> Dict[str, V3Agent]:
    """ip -> V3Agent for every SNMP address inside an enabled v3 network.

    `devices` is the topology's device list and `bind_ips(device)` the
    authority on which SNMP addresses a device has right now (lifecycle-aware),
    passed in rather than imported so this stays free of the generator."""
    cfg = load()
    if not cfg["enabled"] or not cfg["networks"]:
        return {}
    nets = [(ipaddress.IPv4Network(n["cidr"]), n) for n in cfg["networks"]]
    out: Dict[str, V3Agent] = {}
    for d in devices:
        vendor = getattr(getattr(d, "vendor", None), "value", None) or str(getattr(d, "vendor", "") or "")
        for ip in bind_ips(d):
            try:
                addr = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            for net, n in nets:
                if addr in net:
                    if not _in_scope(n, _type_of(d)):
                        break       # networks never overlap: no other entry covers it
                    out[ip] = V3Agent(ip=ip, engine_id=engine_id(vendor, ip), users=[V3User(
                        name=n["user"], auth_proto=n["auth_proto"], auth_key=n["auth_key"],
                        priv_proto=n["priv_proto"], priv_key=n["priv_key"])])
                    break
    return out


def agent_for(ip: str, vendor: str, device_type: Optional[str] = None) -> Optional[tuple]:
    """(V3Agent, notify) for one sending address, or None if it is not in an
    enabled v3 network - the trap engine's question, answered without the
    topology. `notify` is "trap" (the agent is authoritative: its own engine
    ID, the common case on PDU and UPS cards) or "inform" (the receiver is
    authoritative and acknowledges, as cards that support it can be set)."""
    cfg = load()
    if not cfg["enabled"] or not ip:
        return None
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return None
    for n in cfg["networks"]:
        if addr in ipaddress.IPv4Network(n["cidr"]):
            if not _in_scope(n, device_type):
                return None
            return (V3Agent(ip=ip, engine_id=engine_id(vendor, ip), users=[V3User(
                name=n["user"], auth_proto=n["auth_proto"], auth_key=n["auth_key"],
                priv_proto=n["priv_proto"], priv_key=n["priv_key"])]),
                    n.get("notify", "trap"))
    return None

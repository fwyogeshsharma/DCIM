"""An agent's engine ID and its sysObjectID name the same vendor.

Both carry an IANA private enterprise number: the engine ID (RFC 3411 format 1)
in its first four octets, sysObjectID as the arc under 1.3.6.1.4.1. A real card
gets both from one registration. Here they came from two tables kept by hand,
and drifted: Schneider's ION9000 said APC (318) in its engine ID and Schneider
(3833) in its sysObjectID. The DCIM names a v3 device it can read nothing else
from by the engine ID's number, so the two disagreeing is a wrong vendor.
"""
from __future__ import annotations

import pytest

from core.device_manager import VENDOR_SYSOID
from core.snmp_v3 import ENTERPRISE, NET_SNMP, engine_id


def _arc_pen(oid: str) -> int:
    parts = oid.lstrip(".").split(".")
    assert parts[:6] == ["1", "3", "6", "1", "4", "1"], oid
    return int(parts[6])


@pytest.mark.parametrize("vendor", sorted(str(getattr(v, "value", v)) for v in VENDOR_SYSOID))
def test_a_vendors_engine_id_and_sysobjectid_agree(vendor):
    arc = next(o for v, o in VENDOR_SYSOID.items() if str(getattr(v, "value", v)) == vendor)
    pen = _arc_pen(arc)
    assert ENTERPRISE.get(vendor) == pen, (
        f"{vendor}: sysObjectID arc {pen}, engine ID {ENTERPRISE.get(vendor, NET_SNMP)}")


def test_engine_id_is_rfc3411_format_1():
    # high bit + 4-octet PEN, format 0x01 (IPv4), then the address
    assert engine_id("CoolIT Systems", "10.52.11.10") == "800077360" + "1" + "0a340b0a"
    assert engine_id("Schneider Electric", "10.52.11.9").startswith("80000ef9")   # 3833
    assert engine_id("ASCO Power Technologies", "10.52.11.9").startswith("80001f88")  # 8072


def test_no_number_here_is_one_iana_gave_someone_else():
    # Found in the registry, 2026-10-06: the numbers these vendors were once
    # given here belong to others.
    assert 57628 not in ENTERPRISE.values()     # Oka Skog AB, not Verdigris
    assert 33818 not in ENTERPRISE.values()     # an individual, not ASCO
    assert ENTERPRISE["Schneider Electric"] == 3833 and ENTERPRISE["APC by Schneider Electric"] == 318

"""One chassis, one serial, every plane.

This exists because the planes disagreed. SNMP served ``sha1(id)[:7]`` through
ENTITY-MIB while Redfish served ``SN-<id[:8]>`` for the same machine, so a DCIM
polling both read two serials off one server and had every reason to file it as
two assets. Real hardware burns one serial in at manufacture and reports it
identically over entPhysicalSerialNum, Redfish and IPMI FRU.

The agreement is the invariant. Everything else here protects the properties a
DCIM reconciles on: stability across restarts, uniqueness across the estate, and
an explicit value never being overwritten.
"""

from __future__ import annotations

import json
import re

import pytest

import core.redfish_data_generator as redfish
from core.device_manager import Device, DeviceType, Vendor, device_serial
from core.snmprec_generator import SNMPRecGenerator
from core.topology_engine import TopologyEngine

TOPOLOGY = "topologies/dual_dc_enterprise.json"


def _device(**kw) -> Device:
    base = dict(name="SRV-DC1-HA-R1-01", device_type=DeviceType.SERVER,
                vendor=Vendor.DELL, ip_address="10.50.1.10",
                model_name="PowerEdge R640")
    base.update(kw)
    return Device(**base)


# ------------------------------------------------------------- the invariant

@pytest.mark.parametrize("vendor", [
    Vendor.DELL, Vendor.HPE, Vendor.CISCO_SYSTEMS, Vendor.APC, Vendor.LENOVO,
])
def test_snmp_and_redfish_report_the_same_serial(vendor):
    """The bug this module exists to prevent, stated as a test."""
    device = _device(vendor=vendor, id="fixedid1")

    assert SNMPRecGenerator._entity_serial(device) == device.serial_number
    assert device_serial(device) == device.serial_number


def test_redfish_system_and_chassis_agree():
    """A System and a Chassis are two views of one physical unit.

    A BMC that reported different serials on the two would be a firmware bug;
    reproducing it in a simulator teaches a collector something false.
    """
    device = _device(id="fixedid1")

    system = redfish.computer_system(device)
    chassis = redfish.chassis(device)

    assert system["SerialNumber"] == device.serial_number
    assert chassis["SerialNumber"] == device.serial_number
    # The old derivation is gone, not merely shadowed.
    payload = json.dumps({"s": system, "c": chassis})
    assert not re.search(r'"SerialNumber":\s*"SN-', payload)


# ---------------------------------------------------------------- properties

def test_serial_is_stable_for_a_given_device_id():
    """Reconciliation is only testable if the same device yields the same
    serial across a restart and a re-export."""
    assert _device(id="abc12345").serial_number == _device(
        name="renamed-since", id="abc12345").serial_number


def test_an_explicit_serial_is_never_overwritten():
    """A serial is a physical fact somebody may have recorded off the sticker.

    The simulator fills one when it does not know; it does not get to replace
    one it was given.
    """
    assert _device(serial_number="REALSERIAL").serial_number == "REALSERIAL"


def test_a_rename_does_not_change_the_serial():
    """Serial follows the chassis, name follows the role.

    Devices get renamed when a rack is re-purposed. If the serial moved with the
    name, every rename would look like a hardware swap to the DCIM.
    """
    before = _device(id="abc12345", name="SRV-DC1-HA-R1-01").serial_number
    after = _device(id="abc12345", name="SRV-DC2-HB-R9-42").serial_number
    assert before == after


@pytest.mark.parametrize("vendor,pattern", [
    # Dell Service Tag: 7 alphanumerics.
    (Vendor.DELL, r"^[A-Z0-9]{7}$"),
    # HPE: two letters, three digits, five alphanumerics.
    (Vendor.HPE, r"^[A-Z]{2}[0-9]{3}[A-Z0-9]{5}$"),
    # Cisco: 3-letter site, 2-digit year, 2-digit week, 4-char sequence.
    (Vendor.CISCO_SYSTEMS, r"^(FOC|FDO|JAE|SAL|TAE)[0-9]{4}[A-Z0-9]{4}$"),
    (Vendor.APC, r"^[A-Z0-9]{12}$"),
])
def test_serial_matches_the_vendor_format(vendor, pattern):
    """Vendor tooling matches on the shape, so the simulator has to have one."""
    assert re.match(pattern, _device(vendor=vendor, id="abc12345").serial_number)


def test_serial_omits_the_characters_that_get_misread():
    """I/O/0/1 are excluded the way real service tags exclude them.

    A serial is read off a sticker by a person under a rack. A charset where
    those pairs collide is how an asset gets filed against the wrong machine.
    """
    for i in range(400):
        serial = _device(id=f"seed{i:04d}").serial_number
        assert not (set(serial) & set("IO01")), serial


# ------------------------------------------------------------------- estate

def test_the_real_estate_has_no_duplicate_serials():
    """The unique index in migration 0044 builds, or it does not.

    664 devices over a 32-character alphabet collide with probability ~1e-5, so
    this is cheap insurance rather than a formality - and it is the check
    docs/23 phase 2 requires before the migration runs.
    """
    engine = TopologyEngine()
    engine.from_dict(json.load(open(TOPOLOGY, encoding="utf-8")))
    devices = [n["device"] for n in engine.to_dict()["nodes"]]

    serials = [d["serial_number"] for d in devices]
    assert len(devices) > 600, "topology looks truncated; do not trust this check"
    assert all(serials), "every device must carry a serial"
    assert len(set(serials)) == len(serials)


def test_export_round_trips_the_serial_unchanged():
    """Export -> import -> export must not move a serial.

    The DCIM reconciles on this string. If it changed on a re-export, every
    import would look like the whole estate had been replaced.
    """
    engine = TopologyEngine()
    engine.from_dict(json.load(open(TOPOLOGY, encoding="utf-8")))
    first = engine.to_dict()

    again = TopologyEngine()
    again.from_dict(first)

    def by_name(dump):
        return {n["device"]["name"]: n["device"]["serial_number"]
                for n in dump["nodes"]}

    assert by_name(again.to_dict()) == by_name(first)

"""A Raritan strip publishes its branch breakers, as an APC strip does its banks.

A 32 A single-phase PX3-5503U feeds receptacles rated at 10 and 16 A, so it
carries two 16 A overcurrent protectors (C1, C2). PDU2-MIB publishes them in
overCurrentProtectorConfigurationTable [pduId][ocp] (label, rated current as a
DisplayString) and measures them in measurementsOverCurrentProtectorSensorTable
[pduId][ocp][sensorType]. Before this the strip published one trip-state row at
ocp 1 whatever it carried, so a DCIM's per-bank check could not see the racks
it feeds.
"""

from __future__ import annotations

from core import vendor_oids
from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator, _raritan_ocp_count

R = vendor_oids.RARITAN
ST = vendor_oids.RARITAN_SENSOR_TYPE


def _strip(model: str) -> Device:
    return Device(name="PDUA-DC1-NR-R1-01", device_type=DeviceType.PDU,
                  vendor=Vendor.RARITAN, model_name=model, ip_address="10.0.1.9")


def _rows(dev: Device) -> dict[str, str]:
    gen = SNMPRecGenerator.__new__(SNMPRecGenerator)
    return {oid: val for oid, _t, val in gen._pdu_entries(dev, None)}


def test_a_32a_single_phase_strip_publishes_two_16a_protectors():
    rows = _rows(_strip("Raritan PX3-5503U"))
    assert rows[f"{R['ocpLabel']}.1.1"] == "C1"
    assert rows[f"{R['ocpLabel']}.1.2"] == "C2"
    assert rows[f"{R['ocpRatedCurrent']}.1.1"] == "16A"
    assert rows[f"{R['ocpRatedCurrent']}.1.2"] == "16A"
    for o in (1, 2):
        assert f"{R['ocpValue']}.1.{o}.{ST['current']}" in rows
        assert rows[f"{R['ocpDecimals']}.1.{o}.{ST['current']}"] == "3"
        assert f"{R['ocpState']}.1.{o}.{ST['trip']}" in rows
    assert not [o for o in rows if o.startswith(f"{R['ocpLabel']}.1.3")]


def test_a_strip_without_branch_breakers_publishes_no_protector():
    """A 16 A 208 V PX3-5161R is protected by its inlet breaker upstream; its
    overcurrent-protector tables are empty, as on the real unit."""
    dev = _strip("Raritan PX3-5161R")
    assert _raritan_ocp_count(dev) == 0
    rows = _rows(dev)
    assert not [o for o in rows if o.startswith(R["ocpValue"] + ".")
                or o.startswith(R["ocpState"] + ".") or o.startswith(R["ocpLabel"] + ".")]


def test_the_oids_are_the_pdu2_mib_ones():
    """Checked against PDU2-MIB on mibs.observium.org, 2026-10-28."""
    assert R["ocpValue"] == "1.3.6.1.4.1.13742.6.5.3.3.1.4"
    assert R["ocpLabel"] == "1.3.6.1.4.1.13742.6.3.4.3.1.2"
    assert R["ocpRatedCurrent"] == "1.3.6.1.4.1.13742.6.3.4.3.1.5"
    assert R["ocpDecimals"] == "1.3.6.1.4.1.13742.6.3.4.4.1.7"

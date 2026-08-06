"""Vendor OID resolution — the wire identity of a trap depends on WHO sends it.

These lock down the two failure modes that matter: a trap going out on the
placeholder PEN when the vendor has a real MIB, and a trap going out under a
real vendor's PEN for a device class that has no SNMP agent at all.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import vendor_oids as vo
from core.trap_definitions import TrapType, TRAP_DEFINITIONS
from core.trap_engine import TrapEngine


def _default(tt: TrapType) -> str:
    return TRAP_DEFINITIONS[tt].oid


@pytest.mark.parametrize("vendor,key,pen", [
    ("APC by Schneider Electric", "apc", 318),
    ("Raritan", "raritan", 13742),
    ("Server Technology", "raritan", 13742),   # PRO3X ships the same PDU2-MIB
    ("Vertiv (Liebert)", "liebert", 476),
    ("Cisco Systems", "cisco", 9),
    ("Dell Technologies", "dell", 674),
    ("Hewlett Packard Enterprise", "hpe", 232),
    ("Lenovo", "lenovo", 19046),
    ("Supermicro", "supermicro", 10876),
    ("Eaton", "eaton", 534),
    ("F5 Networks", "f5", 3375),
])
def test_vendor_key_and_pen(vendor, key, pen):
    assert vo.vendor_key(vendor) == key
    assert vo.vendor_pen(vendor) == pen


def test_unknown_vendor_has_no_key():
    assert vo.vendor_key("Acme Widgets") == ""
    assert vo.vendor_pen("Acme Widgets") is None
    assert vo.vendor_key(None) == ""


@pytest.mark.parametrize("vendor,dtype,trap,expected", [
    # The same physical event, two vendors, two entirely different OIDs.
    ("APC by Schneider Electric", "pdu", TrapType.PDU_LOAD_CRITICAL,
     "1.3.6.1.4.1.318.0.276"),                       # rPDUOverload
    ("Raritan", "pdu", TrapType.PDU_LOAD_CRITICAL,
     "1.3.6.1.4.1.13742.6.0.61"),                    # inletSensorStateChange
    ("Raritan", "pdu", TrapType.PDU_BREAKER_TRIPPED,
     "1.3.6.1.4.1.13742.6.0.65"),                    # overCurrentProtectorSensorStateChange
    ("Vertiv (Liebert)", "ups", TrapType.UPS_OUTPUT_OVERLOAD,
     "1.3.6.1.4.1.476.1.42.3.3.0.7"),                # lgpEventOutputOverload
    ("Cisco Systems", "switch", TrapType.CPU_HIGH,
     "1.3.6.1.4.1.9.9.109.2.0.1"),                   # cpmCPURisingThreshold
    ("Dell Technologies", "server", TrapType.SERVER_POWER_OFF,
     "1.3.6.1.4.1.674.10892.5.3.2.4.0.8579"),        # alertSystemPowerStateChangeInformation
    ("Hewlett Packard Enterprise", "server", TrapType.CPU_TEMP_CRITICAL,
     "1.3.6.1.4.1.232.0.6003"),                      # cpqHeThermalTempFailed
    ("Lenovo", "server", TrapType.SERVER_POWER_ON,
     "1.3.6.1.4.1.19046.11.1.158.5.0.24"),           # lenovoSpTrapPonS
])
def test_trap_oid_is_vendor_specific(vendor, dtype, trap, expected):
    assert vo.trap_oid(trap, vendor, dtype, _default(trap)) == expected


def test_supermicro_uses_ipmi_pet():
    """Supermicro/IBM BMCs have no notification MIB — they send PET."""
    oid = vo.trap_oid(TrapType.SERVER_POWER_OFF, "Supermicro", "server",
                      _default(TrapType.SERVER_POWER_OFF))
    # <pet>.1.1.0.<(sensor_type << 16) | (event_type << 8) | offset>
    assert oid == f"1.3.6.1.4.1.3183.1.1.0.{(0x09 << 16) | (0x6F << 8)}"


@pytest.mark.parametrize("dtype", sorted(vo.NON_SNMP_DEVICE_TYPES))
def test_bacnet_and_modbus_gear_never_claims_a_vendor_pen(dtype):
    """Plant/electrical field devices have no SNMP agent in production.

    Their vendor string must not pull them onto a real vendor tree — a Modbus
    genset that suddenly trapped as an Eaton ePDU would be a fabrication.
    """
    default = _default(TrapType.TEMPERATURE_ALERT)
    for vendor in ("Eaton", "Vertiv (Liebert)", "APC by Schneider Electric"):
        assert vo.trap_oid(TrapType.TEMPERATURE_ALERT, vendor, dtype, default) == default


def test_crah_is_not_treated_as_non_snmp():
    """A Liebert iCOM CRAH with a Unity card is a real SNMP agent."""
    assert "crah" not in vo.NON_SNMP_DEVICE_TYPES
    assert vo.trap_oid(TrapType.TEMPERATURE_ALERT, "Vertiv (Liebert)", "crah",
                       _default(TrapType.TEMPERATURE_ALERT)).startswith("1.3.6.1.4.1.476.")


def test_unmapped_trap_falls_back_to_synthetic():
    """No verified OID → keep the obviously-fake tree, never invent a leaf."""
    default = _default(TrapType.RACK_FAILURE)
    got = vo.trap_oid(TrapType.RACK_FAILURE, "APC by Schneider Electric", "pdu", default)
    assert got == default
    assert vo.is_synthetic(got)


def test_vendor_varbinds_follow_the_vendor_mib():
    """A vendor trap OID carrying simulator varbinds is still undecodable."""
    apc = SimpleNamespace(name="PDUA-DC1-CP", vendor="APC by Schneider Electric",
                          device_type="pdu", pdu_outlet_current=12.4)
    vbs = TrapEngine._vendor_varbinds(apc, TrapType.PDU_LOAD_CRITICAL, metric_value=97)
    oids = [o.prettyPrint() for o, _ in vbs]
    assert "1.3.6.1.4.1.318.1.1.12.1.1" in oids          # rPDUIdentName
    assert "1.3.6.1.4.1.318.1.1.12.2.3.1.1.2" in oids    # rPDULoadStatusLoad
    # tenths of an amp, per the MIB's units
    load = dict(zip(oids, [v for _, v in vbs]))["1.3.6.1.4.1.318.1.1.12.2.3.1.1.2"]
    assert int(load) == 124

    rar = SimpleNamespace(name="PDUB", vendor="Raritan", device_type="pdu",
                          pdu_outlet_current=9.8)
    vbs = TrapEngine._vendor_varbinds(rar, TrapType.PDU_BREAKER_TRIPPED)
    vals = {o.prettyPrint(): v for o, v in vbs}
    assert int(vals["1.3.6.1.4.1.13742.6.0.0.10"]) == vo.RARITAN_SENSOR_TYPE["trip"]
    assert int(vals["1.3.6.1.4.1.13742.6.5.1.3.1.3"]) == vo.RARITAN_SENSOR_STATE["open"]

    chiller = SimpleNamespace(name="CH01", vendor="Carrier", device_type="chiller")
    assert TrapEngine._vendor_varbinds(chiller, TrapType.TEMPERATURE_ALERT) is None


def test_pet_record_is_47_bytes_with_spec_offsets():
    rec = TrapEngine._pet_record(sensor_type=0x01, event_type=0x01, offset=0x01,
                                 severity=0x10)
    assert len(rec) == 47
    assert rec[26] == 0x10      # event severity
    assert rec[33] == 0x01      # sensor type
    assert rec[32] == 0x01      # event offset

"""Real vendor OIDs — IANA Private Enterprise Numbers and MIB-verified trap OIDs.

WHY THIS EXISTS
---------------
Every enterprise trap used to be sent under ``1.3.6.1.4.1.99999``, a placeholder
PEN that belongs to nobody. A device whose sysDescr says "APC Rack PDU 2G" but
whose overload trap arrives on 99999.6.4 is not something any NMS can process:
load PowerNet-MIB and the alarm still decodes as an unknown numeric OID, so the
event lands in the catch-all bucket instead of the PDU's alarm class.

Real gear keys its traps off the VENDOR, not off the alarm's meaning. An
over-current on an APC rPDU is ``rPDUOverload`` (318.0.276); the same physical
event on a Raritan PX is ``overCurrentProtectorSensorStateChange``
(13742.6.0.65) carrying a sensor-state varbind. So the OID for a TrapType can
only be resolved once the sending device is known — which is what
:func:`trap_oid` does.

POLICY (deliberate, see the "unmapped" note below)
--------------------------------------------------
Only OIDs *verified against the vendor's published MIB* appear here. Everything
else keeps its ``1.3.6.1.4.1.99999`` OID. That is on purpose: inventing a leaf
under a real PEN is WORSE than an obviously synthetic tree, because an NMS with
the genuine MIB loaded will either fail to resolve it or resolve it to some
unrelated object, i.e. a wrong decode instead of an obviously unknown one.

Deliberately left on the synthetic tree:
  * Chillers, cooling towers, pumps, valves, gensets — these speak BACnet/IP or
    Modbus in production and have no SNMP agent at all. Their SNMP trees exist
    only so the simulator can expose them uniformly; keeping the fake PEN is the
    honest signal that a real NMS would never poll them this way.
  * ASCO transfer switches and Caterpillar gensets — SNMP is normally a gateway
    (ASCO Connect / EMCP Modbus-to-SNMP), not a native vendor MIB we can cite.
  * Palo Alto firewalls, Dell switches (OS10) — PEN known (25461 / 674) but the
    specific notification leaves were not verified for this pass.
  * ``core/snmp_set_agent.py``'s 99999.3/.4 management + asset tree: that is the
    SIMULATOR's own control interface, not a device MIB, so a synthetic PEN is
    correct there and it is left alone.

SOURCES
-------
OIDs below were resolved from the vendors' published MIB modules (PowerNet-MIB
v4.5.8, Raritan PDU2-MIB 4.3.0, LIEBERT-GP-NOTIFICATIONS-MIB, CISCO-ENVMON-MIB,
CISCO-PROCESS-MIB, IDRAC-MIB-SMIv2, CPQHLTH-MIB, LENOVO-XCC-ALERT-MIB,
EATON-EPDU-MIB, F5-BIGIP-COMMON-MIB), plus the IPMI Platform Event Trap Format
Specification v1.0 for PET (PEN 3183).
"""
from __future__ import annotations

from core.trap_definitions import TrapType

# The placeholder PEN. Anything still under this tree is simulator-private and
# is NOT claiming to be a real vendor object.
SYNTHETIC_PEN = 99999
SYNTHETIC_BASE = f"1.3.6.1.4.1.{SYNTHETIC_PEN}"

# ── IANA Private Enterprise Numbers ───────────────────────────────────────────
# Keyed by the normalised vendor key returned by vendor_key().
VENDOR_PEN: dict[str, int] = {
    "ibm":        2,        # IBM
    "cisco":      9,        # Cisco Systems
    "hpe":        232,      # Hewlett-Packard (compaq arc, still the HPE tree)
    "apc":        318,      # American Power Conversion (APC by Schneider Electric)
    "eaton":      534,      # Eaton
    "dell":       674,      # Dell Inc.
    "liebert":    476,      # Emerson / Liebert — Vertiv's tree
    "f5":         3375,     # F5 Networks
    "pet":        3183,     # "wired_for_management" — IPMI Platform Event Traps
    "schneider":  3833,     # Schneider Electric (PowerLogic)
    "raritan":    13742,    # Raritan (now Legrand; also Server Technology PRO3X)
    "lenovo":     19046,    # Lenovo
    "supermicro": 10876,    # Super Micro Computer
    "paloalto":   25461,    # Palo Alto Networks
}

# Vendor-string fragments → key. Matched case-insensitively as substrings so the
# Vendor enum's display strings ("APC by Schneider Electric", "Vertiv (Liebert)")
# resolve without having to mirror the enum here.
_VENDOR_MATCH: tuple[tuple[str, str], ...] = (
    ("apc",                 "apc"),
    ("raritan",             "raritan"),
    ("server technology",   "raritan"),      # PRO3X ships the same PDU2-MIB
    ("vertiv",              "liebert"),
    ("liebert",             "liebert"),
    ("emerson",             "liebert"),
    ("cisco",               "cisco"),
    ("dell",                "dell"),
    ("hewlett",             "hpe"),
    ("hpe",                 "hpe"),
    ("lenovo",              "lenovo"),
    ("supermicro",          "supermicro"),
    ("super micro",         "supermicro"),
    ("ibm",                 "ibm"),
    ("eaton",               "eaton"),
    ("f5",                  "f5"),
    ("palo alto",           "paloalto"),
    ("schneider",           "schneider"),
)


def vendor_key(vendor) -> str:
    """Normalise a Vendor enum / display string to a registry key ('' if none)."""
    if vendor is None:
        return ""
    name = getattr(vendor, "value", vendor)
    if not isinstance(name, str):
        return ""
    low = name.lower()
    for frag, key in _VENDOR_MATCH:
        if frag in low:
            return key
    return ""


def vendor_pen(vendor) -> int | None:
    """IANA PEN for a device vendor, or None when it isn't in the registry."""
    return VENDOR_PEN.get(vendor_key(vendor))


# ── Verified varbind objects, by vendor ───────────────────────────────────────
# APC PowerNet-MIB
APC = {
    "identName":        "1.3.6.1.4.1.318.1.1.12.1.1",        # rPDUIdentName
    "identSerial":      "1.3.6.1.4.1.318.1.1.12.1.6",        # rPDUIdentSerialNumber
    "loadStatusLoad":   "1.3.6.1.4.1.318.1.1.12.2.3.1.1.2",  # rPDULoadStatusLoad (0.1 A)
    "loadStatusState":  "1.3.6.1.4.1.318.1.1.12.2.3.1.1.3",  # rPDULoadStatusLoadState
    "bankNumber":       "1.3.6.1.4.1.318.1.1.12.2.3.1.1.5",  # rPDULoadStatusBankNumber
    "phaseNumber":      "1.3.6.1.4.1.318.1.1.12.2.3.1.1.4",  # rPDULoadStatusPhaseNumber
    "trapArgs":         "1.3.6.1.4.1.318.2.3.3",             # mtrapargsString
    "probeTemp":        "1.3.6.1.4.1.318.1.1.10.3.13.1.1.3", # emsProbeStatusProbeTemperature
    "probeHumidity":    "1.3.6.1.4.1.318.1.1.10.3.13.1.1.6", # emsProbeStatusProbeHumidity
    # rPDU2 status tables — what a modern NMS actually polls on a Rack PDU 2G
    "rpdu2Power":       "1.3.6.1.4.1.318.1.1.26.4.3.1.5",    # rPDU2DeviceStatusPower (0.01 kW)
    "rpdu2LoadState":   "1.3.6.1.4.1.318.1.1.26.4.3.1.4",    # rPDU2DeviceStatusLoadState
    "rpdu2Energy":      "1.3.6.1.4.1.318.1.1.26.4.3.1.9",    # rPDU2DeviceStatusEnergy (0.1 kWh)
    "rpdu2ApparentPwr": "1.3.6.1.4.1.318.1.1.26.4.3.1.16",   # rPDU2DeviceStatusApparentPower
    "rpdu2PowerFactor": "1.3.6.1.4.1.318.1.1.26.4.3.1.17",   # rPDU2DeviceStatusPowerFactor (0.01)
    "rpdu2PhaseCurrent":"1.3.6.1.4.1.318.1.1.26.6.3.1.5",    # rPDU2PhaseStatusCurrent (0.1 A)
    "rpdu2PhaseVoltage":"1.3.6.1.4.1.318.1.1.26.6.3.1.6",    # rPDU2PhaseStatusVoltage (V)
    "rpdu2PhaseState":  "1.3.6.1.4.1.318.1.1.26.6.3.1.4",    # rPDU2PhaseStatusLoadState
    "rpdu2BankState":   "1.3.6.1.4.1.318.1.1.26.8.3.1.4",    # rPDU2BankStatusLoadState
    "rpdu2BankCurrent": "1.3.6.1.4.1.318.1.1.26.8.3.1.5",    # rPDU2BankStatusCurrent (0.1 A)
    "rpdu2OutletState": "1.3.6.1.4.1.318.1.1.26.9.2.3.1.5",  # rPDU2OutletSwitchedStatusState
    "rpdu2SensorTempC": "1.3.6.1.4.1.318.1.1.26.10.2.2.1.8", # rPDU2SensorTempHumidityStatusTempC
    "rpdu2SensorHumid": "1.3.6.1.4.1.318.1.1.26.10.2.2.1.10",# ...StatusRelativeHumidity
}

# Raritan PDU2-MIB. Raritan does not define one trap per condition: every
# threshold crossing is a *SensorStateChange carrying the sensor type, the new
# state and the old state, which is how a Raritan-aware NMS classifies it.
RARITAN = {
    "pduName":          "1.3.6.1.4.1.13742.6.3.2.2.1.13",    # pduName
    "pduSerial":        "1.3.6.1.4.1.13742.6.3.2.1.1.4",     # pduSerialNumber
    "typeOfSensor":     "1.3.6.1.4.1.13742.6.0.0.10",        # typeOfSensor
    "oldSensorState":   "1.3.6.1.4.1.13742.6.0.0.2",         # oldSensorState
    "inletValue":       "1.3.6.1.4.1.13742.6.5.2.3.1.4",     # measurementsInletSensorValue
    "inletState":       "1.3.6.1.4.1.13742.6.5.2.3.1.3",     # measurementsInletSensorState
    "outletValue":      "1.3.6.1.4.1.13742.6.5.4.3.1.4",     # measurementsOutletSensorValue
    "outletState":      "1.3.6.1.4.1.13742.6.5.4.3.1.3",     # measurementsOutletSensorState
    "externalValue":    "1.3.6.1.4.1.13742.6.5.5.3.1.4",     # measurementsExternalSensorValue
    "externalState":    "1.3.6.1.4.1.13742.6.5.5.3.1.3",     # measurementsExternalSensorState
    "unitValue":        "1.3.6.1.4.1.13742.6.5.1.3.1.4",     # measurementsUnitSensorValue
    "unitState":        "1.3.6.1.4.1.13742.6.5.1.3.1.3",     # measurementsUnitSensorState
    "ocpValue":         "1.3.6.1.4.1.13742.6.5.3.3.1.4",     # measurementsOverCurrentProtectorSensorValue
    "ocpState":         "1.3.6.1.4.1.13742.6.5.3.3.1.3",     # ...SensorState
    # Raritan readings are unsigned integers scaled by the sensor's own
    # decimal-digits attribute, so a poller MUST be able to read that attribute
    # too — emitting the value alone would leave 23.5 °C indistinguishable
    # from 235 °C.
    "inletDecimals":    "1.3.6.1.4.1.13742.6.3.3.4.1.7",     # inletSensorDecimalDigits
    "inletUnits":       "1.3.6.1.4.1.13742.6.3.3.4.1.6",     # inletSensorUnits
    "outletDecimals":   "1.3.6.1.4.1.13742.6.3.5.4.1.7",     # outletSensorDecimalDigits
    "externalDecimals": "1.3.6.1.4.1.13742.6.3.6.3.1.17",    # externalSensorDecimalDigits
    "externalType":     "1.3.6.1.4.1.13742.6.3.6.3.1.2",     # externalSensorType
    "pduModel":         "1.3.6.1.4.1.13742.6.3.2.1.1.3",     # pduModel
}

# PDU2-MIB SensorTypeEnumeration / SensorStateEnumeration.
RARITAN_SENSOR_TYPE = {
    "current": 1, "voltage": 4, "activePower": 5, "apparentPower": 6,
    "powerFactor": 7, "activeEnergy": 8, "temperature": 10, "humidity": 11,
    "airFlow": 12, "onOff": 14, "trip": 15, "smokeDetection": 18,
    "frequency": 23, "residualCurrent": 26,
}
RARITAN_SENSOR_STATE = {
    "unavailable": -1, "open": 0, "closed": 1, "belowLowerCritical": 2,
    "belowLowerWarning": 3, "normal": 4, "aboveUpperWarning": 5,
    "aboveUpperCritical": 6, "on": 7, "off": 8, "detected": 9,
    "notDetected": 10, "alarmed": 11, "ok": 12, "fail": 14,
}

# Liebert (Vertiv) — LIEBERT-GP-CONDITIONS / -NOTIFICATIONS
LIEBERT = {
    "conditionDescr":   "1.3.6.1.4.1.476.1.42.3.2.3.1.1",    # lgpConditionDescr
    "conditionTime":    "1.3.6.1.4.1.476.1.42.3.2.3.1.3",    # lgpConditionTime
}

# Cisco
CISCO = {
    "cpu5min":          "1.3.6.1.4.1.9.9.109.1.1.1.1.8",     # cpmCPUTotal5minRev
    "cpuRisingThresh":  "1.3.6.1.4.1.9.9.109.1.2.4.1.2",     # cpmCPURisingThresholdValue
    "memUsed":          "1.3.6.1.4.1.9.9.48.1.1.1.5",        # ciscoMemoryPoolUsed
    "envTempDescr":     "1.3.6.1.4.1.9.9.13.1.3.1.2",        # ciscoEnvMonTemperatureStatusDescr
    "envTempValue":     "1.3.6.1.4.1.9.9.13.1.3.1.3",        # ciscoEnvMonTemperatureStatusValue
    "envTempState":     "1.3.6.1.4.1.9.9.13.1.3.1.6",        # ciscoEnvMonTemperatureState
}
# CISCO-ENVMON-MIB CiscoEnvMonState
CISCO_ENV_STATE = {"normal": 1, "warning": 2, "critical": 3,
                   "shutdown": 4, "notPresent": 5, "notFunctioning": 6}

# Dell iDRAC (IDRAC-MIB-SMIv2). Every alert carries the same message varbinds.
DELL = {
    "alertMessageID":   "1.3.6.1.4.1.674.10892.5.3.1.1",
    "alertMessage":     "1.3.6.1.4.1.674.10892.5.3.1.2",
    "alertCurrentStatus": "1.3.6.1.4.1.674.10892.5.3.1.3",
    "alertServiceTag":  "1.3.6.1.4.1.674.10892.5.3.1.4",
}
# DELL alertCurrentStatus: other(1) unknown(2) ok(3) nonCritical(4) critical(5)
DELL_STATUS = {"ok": 3, "warning": 4, "critical": 5}

HPE = {
    "thermalTempStatus":   "1.3.6.1.4.1.232.6.2.6.3",        # cpqHeThermalTempStatus
    "thermalDegradedAct":  "1.3.6.1.4.1.232.6.2.6.2",        # cpqHeThermalDegradedAction
}

LENOVO = {
    "spTxtId":          "1.3.6.1.4.1.19046.11.1.158.5.1.3",  # altSpTxtId (event text)
    "sysSern":          "1.3.6.1.4.1.19046.11.1.158.5.1.6",  # altSysSern
}

# IPMI Platform Event Trap — the cross-vendor BMC path. Supermicro and IBM BMCs
# emit PET rather than a vendor notification MIB. The trap OID is
# <pet>.1.1.0.<specific>; the single varbind is the 47-byte PET event record.
PET = {
    "base":             "1.3.6.1.4.1.3183.1.1",
    "eventData":        "1.3.6.1.4.1.3183.1.1.1",
}


# ── Trap OID maps, per vendor key ─────────────────────────────────────────────
#
# Only TrapTypes with a MIB-verified counterpart appear. Anything absent falls
# through to the synthetic OID in TRAP_DEFINITIONS.

_APC_TRAPS = {
    # PowerNet-MIB rPDU traps (SMIv1 TRAP-TYPE → enterprise 318, generic 6)
    TrapType.PDU_LOAD_HIGH:           "1.3.6.1.4.1.318.0.274",  # rPDUNearOverload
    TrapType.PDU_LOAD_CRITICAL:       "1.3.6.1.4.1.318.0.276",  # rPDUOverload
    TrapType.PDU_LOAD_NORMAL:         "1.3.6.1.4.1.318.0.275",  # rPDUNearOverloadCleared
    TrapType.PDU_OUTLET_ON:           "1.3.6.1.4.1.318.0.268",  # rPDUOutletOn
    TrapType.PDU_OUTLET_OFF:          "1.3.6.1.4.1.318.0.269",  # rPDUOutletOff
    # A tripped branch breaker on an APC rPDU is reported as the bank/phase
    # over-current that caused it — PowerNet has no separate "breaker open" trap.
    TrapType.PDU_BREAKER_TRIPPED:     "1.3.6.1.4.1.318.0.226",  # rPDUBankPhaseOverload
    TrapType.PDU_OUTLET_CURRENT_HIGH: "1.3.6.1.4.1.318.0.634",  # rPDUOutletOverload
    TrapType.PDU_TEMP_HIGH:           "1.3.6.1.4.1.318.0.253",  # envHighTempThresholdViolation
    TrapType.PDU_TEMP_NORMAL:         "1.3.6.1.4.1.318.0.254",  # ...Cleared
    TrapType.PDU_HUMIDITY_HIGH:       "1.3.6.1.4.1.318.0.257",  # envHighHumidityThresholdViolation
    TrapType.PDU_HUMIDITY_NORMAL:     "1.3.6.1.4.1.318.0.258",  # ...Cleared
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     "1.3.6.1.4.1.318.0.253",
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: "1.3.6.1.4.1.318.0.253",
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL:   "1.3.6.1.4.1.318.0.254",
    TrapType.SENSOR_HIGH_HUMIDITY:         "1.3.6.1.4.1.318.0.257",
    TrapType.SENSOR_CRITICAL_HUMIDITY:     "1.3.6.1.4.1.318.0.257",
    TrapType.SENSOR_LOW_HUMIDITY:          "1.3.6.1.4.1.318.0.259",  # envLowHumidity...
    TrapType.SENSOR_HUMIDITY_NORMAL:       "1.3.6.1.4.1.318.0.258",
}

# Raritan: one notification per sensor CLASS, the varbinds say what happened.
_RARITAN_INLET  = "1.3.6.1.4.1.13742.6.0.61"   # inletSensorStateChange
_RARITAN_OUTLET = "1.3.6.1.4.1.13742.6.0.63"   # outletSensorStateChange
_RARITAN_OCP    = "1.3.6.1.4.1.13742.6.0.65"   # overCurrentProtectorSensorStateChange
_RARITAN_EXT    = "1.3.6.1.4.1.13742.6.0.66"   # externalSensorStateChange
_RARITAN_UNIT   = "1.3.6.1.4.1.13742.6.0.60"   # pduSensorStateChange

_RARITAN_TRAPS = {
    TrapType.PDU_LOAD_HIGH:           _RARITAN_INLET,
    TrapType.PDU_LOAD_CRITICAL:       _RARITAN_INLET,
    TrapType.PDU_LOAD_NORMAL:         _RARITAN_INLET,
    TrapType.PDU_VOLTAGE_HIGH:        _RARITAN_INLET,
    TrapType.PDU_VOLTAGE_LOW:         _RARITAN_INLET,
    TrapType.PDU_FREQUENCY_FAULT:     _RARITAN_INLET,
    TrapType.PDU_FREQUENCY_NORMAL:    _RARITAN_INLET,
    TrapType.PDU_POWER_FACTOR_LOW:    _RARITAN_INLET,
    TrapType.PDU_PHASE_IMBALANCE:     _RARITAN_INLET,
    TrapType.PDU_OUTLET_ON:           _RARITAN_OUTLET,
    TrapType.PDU_OUTLET_OFF:          _RARITAN_OUTLET,
    TrapType.PDU_OUTLET_CURRENT_HIGH: _RARITAN_OUTLET,
    TrapType.PDU_OUTLET_FAILURE:      _RARITAN_OUTLET,
    TrapType.PDU_BREAKER_TRIPPED:     _RARITAN_OCP,
    TrapType.PDU_GROUND_FAULT:        _RARITAN_UNIT,
    TrapType.PDU_SMOKE_DETECTED:      _RARITAN_EXT,
    TrapType.PDU_TEMP_HIGH:           _RARITAN_EXT,
    TrapType.PDU_TEMP_NORMAL:         _RARITAN_EXT,
    TrapType.PDU_HUMIDITY_HIGH:       _RARITAN_EXT,
    TrapType.PDU_HUMIDITY_NORMAL:     _RARITAN_EXT,
    # Rack environmental probes (Raritan DPX2/DX2 strings hang off the PDU)
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     _RARITAN_EXT,
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: _RARITAN_EXT,
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL:   _RARITAN_EXT,
    TrapType.SENSOR_MID_TEMP_HIGH:         _RARITAN_EXT,
    TrapType.SENSOR_MID_TEMP_NORMAL:       _RARITAN_EXT,
    TrapType.SENSOR_OUTLET_TEMP_HIGH:      _RARITAN_EXT,
    TrapType.SENSOR_OUTLET_TEMP_NORMAL:    _RARITAN_EXT,
    TrapType.SENSOR_HIGH_HUMIDITY:         _RARITAN_EXT,
    TrapType.SENSOR_CRITICAL_HUMIDITY:     _RARITAN_EXT,
    TrapType.SENSOR_LOW_HUMIDITY:          _RARITAN_EXT,
    TrapType.SENSOR_HUMIDITY_NORMAL:       _RARITAN_EXT,
    TrapType.SENSOR_HIGH_AIRFLOW:          _RARITAN_EXT,
    TrapType.SENSOR_LOW_AIRFLOW:           _RARITAN_EXT,
    TrapType.SENSOR_AIRFLOW_NORMAL:        _RARITAN_EXT,
    TrapType.HUMIDITY_ALERT:               _RARITAN_EXT,
    TrapType.DEWPOINT_ALERT:               _RARITAN_EXT,
    TrapType.AIRFLOW_ALERT:                _RARITAN_EXT,
    TrapType.SENSOR_DEWPOINT_NORMAL:       _RARITAN_EXT,
}

# Liebert / Vertiv — UPS, CRAH (iCOM) and Liebert environmental probes. iCOM
# reports most machine conditions as a condition-table row appearing/clearing.
_LGP_ADDED   = "1.3.6.1.4.1.476.1.42.3.3.0.1"   # lgpEventConditionEntryAdded
_LGP_REMOVED = "1.3.6.1.4.1.476.1.42.3.3.0.2"   # lgpEventConditionEntryRemoved

_LIEBERT_TRAPS = {
    TrapType.UPS_LOW_BATTERY:          "1.3.6.1.4.1.476.1.42.3.3.0.3",   # lgpEventLowBatteryWarning
    TrapType.UPS_BYPASS_ACTIVE:        "1.3.6.1.4.1.476.1.42.3.3.0.4",   # lgpEventLoadTransferedToBypass
    TrapType.UPS_BYPASS_CLEARED:       _LGP_REMOVED,
    TrapType.UPS_OUTPUT_OVERLOAD:      "1.3.6.1.4.1.476.1.42.3.3.0.7",   # lgpEventOutputOverload
    TrapType.UPS_OUTPUT_NORMAL:        _LGP_REMOVED,
    TrapType.UPS_BATTERY_FAILURE:      "1.3.6.1.4.1.476.1.42.3.3.0.11",  # lgpEventBatteryModuleFailure
    TrapType.UPS_BATTERY_LOW_HEALTH:   "1.3.6.1.4.1.476.1.42.3.3.0.14",  # lgpEventBatteryModuleWarning
    TrapType.UPS_BATTERY_HEALTH_RESTORED: _LGP_REMOVED,
    TrapType.UPS_BATTERY_NORMAL:       _LGP_REMOVED,
    TrapType.UPS_RECTIFIER_FAILURE:    "1.3.6.1.4.1.476.1.42.3.3.0.10",  # lgpEventPowerModuleFailure
    TrapType.UPS_CHARGER_FAILURE:      "1.3.6.1.4.1.476.1.42.3.3.0.5",   # lgpEventInternalFault
    TrapType.UPS_FAN_FAILURE:          "1.3.6.1.4.1.476.1.42.3.3.0.5",
    TrapType.UPS_PHASE_FAILURE:        "1.3.6.1.4.1.476.1.42.3.3.0.9",   # lgpEventLostPowerRedundancy
    TrapType.UPS_BATTERY_DISCONNECTED: "1.3.6.1.4.1.476.1.42.3.3.0.11",
    TrapType.UPS_INPUT_VOLTAGE_HIGH:   _LGP_ADDED,
    TrapType.UPS_INPUT_VOLTAGE_LOW:    _LGP_ADDED,
    TrapType.UPS_INPUT_VOLTAGE_NORMAL: _LGP_REMOVED,
    TrapType.UPS_INPUT_VOLTAGE_LOW_CLEARED: _LGP_REMOVED,
    TrapType.UPS_FREQUENCY_OUT_RANGE:  _LGP_ADDED,
    TrapType.UPS_UTILITY_RESTORED:     _LGP_REMOVED,
    # Liebert environmental probes / iCOM machine conditions
    TrapType.TEMPERATURE_ALERT:            _LGP_ADDED,
    TrapType.TEMPERATURE_NORMAL:           _LGP_REMOVED,
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     _LGP_ADDED,
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: _LGP_ADDED,
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL:   _LGP_REMOVED,
    TrapType.SENSOR_MID_TEMP_HIGH:         _LGP_ADDED,
    TrapType.SENSOR_MID_TEMP_NORMAL:       _LGP_REMOVED,
    TrapType.SENSOR_OUTLET_TEMP_HIGH:      _LGP_ADDED,
    TrapType.SENSOR_OUTLET_TEMP_NORMAL:    _LGP_REMOVED,
    TrapType.SENSOR_HIGH_HUMIDITY:         _LGP_ADDED,
    TrapType.SENSOR_CRITICAL_HUMIDITY:     _LGP_ADDED,
    TrapType.SENSOR_LOW_HUMIDITY:          _LGP_ADDED,
    TrapType.SENSOR_HUMIDITY_NORMAL:       _LGP_REMOVED,
    TrapType.SENSOR_HIGH_AIRFLOW:          _LGP_ADDED,
    TrapType.SENSOR_LOW_AIRFLOW:           _LGP_ADDED,
    TrapType.SENSOR_AIRFLOW_NORMAL:        _LGP_REMOVED,
    TrapType.HUMIDITY_ALERT:               _LGP_ADDED,
    TrapType.DEWPOINT_ALERT:               _LGP_ADDED,
    TrapType.AIRFLOW_ALERT:                _LGP_ADDED,
    TrapType.SENSOR_DEWPOINT_NORMAL:       _LGP_REMOVED,
}

_CISCO_TRAPS = {
    TrapType.CPU_HIGH:          "1.3.6.1.4.1.9.9.109.2.0.1",  # cpmCPURisingThreshold
    TrapType.CPU_SUSTAINED:     "1.3.6.1.4.1.9.9.109.2.0.1",
    TrapType.CPU_NORMAL:        "1.3.6.1.4.1.9.9.109.2.0.2",  # cpmCPUFallingThreshold
    TrapType.TEMPERATURE_ALERT: "1.3.6.1.4.1.9.9.13.3.0.3",   # ciscoEnvMonTemperatureNotification
    TrapType.CPU_TEMP_CRITICAL: "1.3.6.1.4.1.9.9.13.3.0.3",
    TrapType.TEMPERATURE_NORMAL:"1.3.6.1.4.1.9.9.13.3.0.3",
}

# Dell iDRAC. NOTE: alertMemoryDeviceWarning is a DIMM fault, NOT a memory
# utilisation alarm — MEMORY_HIGH is deliberately NOT mapped to it.
_DELL_TRAPS = {
    TrapType.TEMPERATURE_ALERT:  "1.3.6.1.4.1.674.10892.5.3.2.1.0.2162",  # alertTemperatureProbeWarning
    TrapType.CPU_TEMP_CRITICAL:  "1.3.6.1.4.1.674.10892.5.3.2.1.0.2161",  # ...Failure
    TrapType.TEMPERATURE_NORMAL: "1.3.6.1.4.1.674.10892.5.3.2.1.0.2163",  # ...Normal
    TrapType.SERVER_POWER_OFF:   "1.3.6.1.4.1.674.10892.5.3.2.4.0.8579",  # alertSystemPowerStateChangeInformation
    TrapType.SERVER_POWER_ON:    "1.3.6.1.4.1.674.10892.5.3.2.4.0.8579",
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     "1.3.6.1.4.1.674.10892.5.3.2.1.0.2162",
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: "1.3.6.1.4.1.674.10892.5.3.2.1.0.2161",
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL:   "1.3.6.1.4.1.674.10892.5.3.2.1.0.2163",
}

_HPE_TRAPS = {
    TrapType.TEMPERATURE_ALERT:  "1.3.6.1.4.1.232.0.6004",  # cpqHeThermalTempDegraded
    TrapType.CPU_TEMP_CRITICAL:  "1.3.6.1.4.1.232.0.6003",  # cpqHeThermalTempFailed
    TrapType.TEMPERATURE_NORMAL: "1.3.6.1.4.1.232.0.6005",  # cpqHeThermalTempOk
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     "1.3.6.1.4.1.232.0.6004",
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: "1.3.6.1.4.1.232.0.6003",
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL:   "1.3.6.1.4.1.232.0.6005",
}

_LENOVO_TRAPS = {
    TrapType.TEMPERATURE_ALERT:  "1.3.6.1.4.1.19046.11.1.158.5.0.12",  # lenovoSpTrapTempN (non-critical)
    TrapType.CPU_TEMP_CRITICAL:  "1.3.6.1.4.1.19046.11.1.158.5.0.0",   # lenovoSpTrapTempC (critical)
    TrapType.SERVER_POWER_OFF:   "1.3.6.1.4.1.19046.11.1.158.5.0.23",  # lenovoSpTrapPoffS
    TrapType.SERVER_POWER_ON:    "1.3.6.1.4.1.19046.11.1.158.5.0.24",  # lenovoSpTrapPonS
    TrapType.SENSOR_AMBIENT_TEMP_HIGH:     "1.3.6.1.4.1.19046.11.1.158.5.0.12",
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: "1.3.6.1.4.1.19046.11.1.158.5.0.0",
}

_F5_TRAPS = {
    TrapType.TEMPERATURE_ALERT: "1.3.6.1.4.1.3375.2.4.0.7",  # bigipChassisTempHigh
    TrapType.CPU_TEMP_CRITICAL: "1.3.6.1.4.1.3375.2.4.0.4",  # bigipCpuTempHigh
}

# Eaton ePDU MIB — used for the Eaton rack/branch metering paths only. Eaton
# switchgear and MCC boards are Modbus/Power Xpert in production, so they stay
# on the synthetic tree rather than borrowing an ePDU notification.
_EATON_TRAPS = {
    TrapType.PDU_VOLTAGE_HIGH:    "1.3.6.1.4.1.534.6.6.7.0.11",  # notifyInputVoltageThStatus
    TrapType.PDU_VOLTAGE_LOW:     "1.3.6.1.4.1.534.6.6.7.0.11",
    TrapType.PDU_LOAD_HIGH:       "1.3.6.1.4.1.534.6.6.7.0.12",  # notifyInputCurrentThStatus
    TrapType.PDU_LOAD_CRITICAL:   "1.3.6.1.4.1.534.6.6.7.0.12",
    TrapType.PDU_LOAD_NORMAL:     "1.3.6.1.4.1.534.6.6.7.0.12",
    TrapType.PDU_FREQUENCY_FAULT: "1.3.6.1.4.1.534.6.6.7.0.13",  # notifyInputFrequencyStatus
    TrapType.PDU_FREQUENCY_NORMAL:"1.3.6.1.4.1.534.6.6.7.0.13",
    TrapType.PDU_BREAKER_TRIPPED: "1.3.6.1.4.1.534.6.6.7.0.23",  # notifyGroupBreakerStatus
    TrapType.PDU_TEMP_HIGH:       "1.3.6.1.4.1.534.6.6.7.0.41",  # notifyTemperatureThStatus
    TrapType.PDU_TEMP_NORMAL:     "1.3.6.1.4.1.534.6.6.7.0.41",
    TrapType.PDU_HUMIDITY_HIGH:   "1.3.6.1.4.1.534.6.6.7.0.42",  # notifyHumidityThStatus
    TrapType.PDU_HUMIDITY_NORMAL: "1.3.6.1.4.1.534.6.6.7.0.42",
}

# IPMI PET, for BMCs with no public notification MIB (Supermicro, IBM). The
# specific-trap number encodes sensor type and event type per the PET spec:
#   specific = (sensor_type << 16) | (event_type << 8) | offset
_PET_TEMP  = f"{PET['base']}.0.{(0x01 << 16) | (0x01 << 8) | 0x01}"   # temperature, upper non-critical
_PET_PWR_OFF = f"{PET['base']}.0.{(0x09 << 16) | (0x6F << 8) | 0x00}" # power unit, state asserted
_PET_PWR_ON  = f"{PET['base']}.0.{(0x09 << 16) | (0x6F << 8) | 0x01}"

_PET_TRAPS = {
    TrapType.TEMPERATURE_ALERT: _PET_TEMP,
    TrapType.CPU_TEMP_CRITICAL: _PET_TEMP,
    TrapType.SERVER_POWER_OFF:  _PET_PWR_OFF,
    TrapType.SERVER_POWER_ON:   _PET_PWR_ON,
}

VENDOR_TRAPS: dict[str, dict[TrapType, str]] = {
    "apc":        _APC_TRAPS,
    "raritan":    _RARITAN_TRAPS,
    "liebert":    _LIEBERT_TRAPS,
    "cisco":      _CISCO_TRAPS,
    "dell":       _DELL_TRAPS,
    "hpe":        _HPE_TRAPS,
    "lenovo":     _LENOVO_TRAPS,
    "f5":         _F5_TRAPS,
    "eaton":      _EATON_TRAPS,
    "supermicro": _PET_TRAPS,
    "ibm":        _PET_TRAPS,
}

# Device types whose SNMP agent is a fiction of the simulator: in production they
# are BACnet/IP or Modbus field devices with no SNMP stack. Their traps stay on
# the synthetic PEN no matter what the vendor field says, so the wire never
# claims Carrier/Grundfos/Belimo publish these as SNMP notifications.
#
# CRAH is deliberately NOT in this set: a Liebert iCOM unit with an IntelliSlot
# Unity card is a real SNMP agent and does emit lgpEventConditionEntryAdded, so
# Vertiv CRAHs get the Liebert tree like the UPS does.
NON_SNMP_DEVICE_TYPES = frozenset({
    # BACnet/IP or Modbus field devices — no SNMP stack of their own
    "chiller", "cooling_tower", "pump", "valve", "utility_feed", "energy_monitor",
    # Modbus + protocol gateway in production (EMCP, ASCO Connect, Power Xpert)
    "generator", "ats", "switchgear", "mcc", "mpp",
})


def trap_oid(trap_type: TrapType, vendor=None, device_type=None,
             default: str | None = None) -> str | None:
    """Resolve the OID a *real* device of this vendor would send for this trap.

    Falls back to ``default`` (the synthetic TRAP_DEFINITIONS OID) whenever the
    vendor is unknown, the trap has no verified vendor counterpart, or the
    device class has no SNMP agent in production.
    """
    dt = getattr(device_type, "value", device_type)
    if dt in NON_SNMP_DEVICE_TYPES:
        return default
    table = VENDOR_TRAPS.get(vendor_key(vendor))
    if not table:
        return default
    return table.get(trap_type, default)


def is_synthetic(oid: str | None) -> bool:
    """True when an OID still lives on the placeholder enterprise tree."""
    return bool(oid) and oid.startswith(SYNTHETIC_BASE)

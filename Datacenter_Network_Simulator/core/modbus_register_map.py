"""Modbus register maps for the facility electrical plane.

This module is to Modbus what `snmprec_generator._electrical_updates` is to SNMP:
a *rendering* of the live `ext` state dict that `device_state_store` already
publishes per device, not a second source of telemetry.  Every point below reads
a key that the store already writes each tick, which is why adding this plane
introduces no new physics and cannot drift from the SNMP view.

    core.device_state_store._get_ext_state(device.name) -> {"util_kw": 812.4, ...}
                    │                                │
          _electrical_updates()                 MODBUS_SPEC
                    │                                │
              SNMP OIDs                      Modbus registers

WHY THESE ADDRESSES ARE NOT REAL
--------------------------------
Vendor register maps (Schneider ION, Eaton PXM/Digitrip, CAT EMCP, ASCO, Vertiv
IS-UNITY) are proprietary documents.  The addresses here are OURS.  They are laid
out the way real maps are — sparse blocks, per-vendor scaling conventions, per-
vendor word order, 32-bit accumulators split across register pairs — so that an
integration written against this simulator exercises the same decoding problems a
real one does.  They are NOT the vendor's addresses, and pointing a real BMS
template at them will (correctly) return exception 02 on most reads.

Every map therefore carries a `map_id` like "SIM-ION9000-v1", which is served
back over FC43 (Read Device Identification) and stamped into the CSV export, so
the distinction survives contact with a user who did not read this docstring.

NO FABRICATED READINGS
----------------------
A point exists here only if the store actually models it.  The CAT EMCP map is
consequently missing engine RPM, oil pressure and coolant temperature: a real
EMCP serves them, this simulator does not model them, and publishing a plausible
number for an unmodelled quantity is the exact failure mode that
`snmprec_generator._probe_oids` was rewritten to prevent (a thermowell in a water
header answering with a dew point).  Absent beats invented.

NO UNSOLICITED MESSAGING
------------------------
Modbus has no traps, no COV, no I-Am — the master polls, and that is the entire
protocol.  Nothing in this plane may be wired to TrapEngine/RuleEngine.  A field
device that appears to raise an alarm on a real site is being polled by a BMS
that raises the alarm on its behalf.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
#  Address spaces
#
#  Modbus has four, and which one a point lives in is a real modelling decision,
#  not a formality: a master that can write to a measurement is a master that can
#  corrupt it, so measurements belong in the read-only spaces.
# ─────────────────────────────────────────────────────────────────────────────
SPACE_COIL     = "coil"      # RW bit    — FC01 read, FC05/15 write  (commands)
SPACE_DISCRETE = "discrete"  # RO bit    — FC02                      (status/alarm)
SPACE_HOLDING  = "holding"   # RW 16-bit — FC03 read, FC06/16 write  (setpoints)
SPACE_INPUT    = "input"     # RO 16-bit — FC04                      (measurements)

BIT_SPACES = (SPACE_COIL, SPACE_DISCRETE)
REG_SPACES = (SPACE_HOLDING, SPACE_INPUT)

# ─────────────────────────────────────────────────────────────────────────────
#  Data types and word order
#
#  Register width in registers (16 bits each). Anything wider than u16/s16 spans
#  a pair, and the order of those two words is the single most common cause of
#  a working Modbus integration reading garbage — so it is per-vendor here, on
#  purpose, exactly as it is in the field.
# ─────────────────────────────────────────────────────────────────────────────
DTYPE_WIDTH = {"u16": 1, "s16": 1, "u32": 2, "s32": 2, "f32": 2}

WORD_BIG    = "big"    # hi word first — the Modbus spec's own byte order
WORD_SWAP   = "swap"   # lo word first — Eaton PXM / many PLC gateways


def _split32(raw: int, word_order: str) -> List[int]:
    hi, lo = (raw >> 16) & 0xFFFF, raw & 0xFFFF
    return [lo, hi] if word_order == WORD_SWAP else [hi, lo]


def _join32(regs: List[int], word_order: str) -> int:
    a, b = regs[0] & 0xFFFF, regs[1] & 0xFFFF
    lo, hi = (a, b) if word_order == WORD_SWAP else (b, a)
    return ((hi << 16) | lo) & 0xFFFFFFFF


# ─────────────────────────────────────────────────────────────────────────────
#  Point definition
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModbusPoint:
    """One addressable quantity in one address space.

    addr        zero-based protocol address (what travels in the PDU — NOT the
                4xxxx "data model" numbering used in vendor documentation).
    key         the `ext` dict key this point renders, or "" for a constant.
    dtype       u16/s16/u32/s32/f32 for register spaces; ignored for bit spaces.
    scale       raw = round(value * scale).  1 = engineering units as-is.
    enum        {ext string value: raw int} for statuses that live as strings in
                `ext` ("energized", "on_battery", ...).  Unlisted -> `enum_default`.
    truthy      for bit spaces: the ext value(s) that mean 1.  A callable gets the
                raw ext value and returns bool.
    writable    holding/coil only.  A write lands in the override channel named by
                `write_action`; it is NEVER written back into `ext`, because the
                ticker owns `ext` and would overwrite it within one second.
    """
    addr: int
    name: str
    key: str = ""
    dtype: str = "u16"
    scale: float = 1.0
    units: str = ""
    enum: Optional[Dict[str, int]] = None
    enum_default: int = 0
    truthy: Optional[Any] = None
    default: float = 0.0
    writable: bool = False
    write_action: str = ""
    const: Optional[int] = None

    @property
    def width(self) -> int:
        return DTYPE_WIDTH.get(self.dtype, 1)


@dataclass(frozen=True)
class ModbusMap:
    """A whole device's map, plus the identity FC43 serves."""
    map_id: str
    vendor: str
    product: str
    word_order: str = WORD_BIG
    points: Dict[str, List[ModbusPoint]] = field(default_factory=dict)
    # Real gear rejects writes unless it is in Remote/Auto, and most sites
    # configure the Modbus plane read-only regardless. Default closed.
    write_enabled: bool = False
    # Some gear demands its configured unit id; plenty ignores it entirely.
    accept_any_unit: bool = True


# ─────────────────────────────────────────────────────────────────────────────
#  Shared enum encodings
# ─────────────────────────────────────────────────────────────────────────────
_ENERGIZED = {"energized": 1, "dead": 0, "fault": 2}
_ATS_POS   = {"normal": 1, "emergency": 2, "none": 0}
_GEN_ST    = {"standby": 1, "running": 2, "fault": 3, "cranking": 4, "cooldown": 5}
_UPS_MODE  = {"online": 1, "battery": 2, "bypass": 3, "eco": 4, "standby": 5}


def _P(addr, name, key, dtype="u16", scale=1.0, units="", **kw) -> ModbusPoint:
    return ModbusPoint(addr=addr, name=name, key=key, dtype=dtype,
                       scale=scale, units=units, **kw)


# ─────────────────────────────────────────────────────────────────────────────
#  MAPS
#
#  Address block convention (ours, consistent across maps so the panel's register
#  browser reads sensibly):
#     input  0x0000..  system-level scalars (V, A, Hz, PF)
#     input  0x0010..  per-phase V then per-phase I
#     input  0x0020..  power (kW/kVAR/kVA) and derived quality
#     input  0x0030..  32-bit accumulators (energy, counters, run time)
#     discrete 0..     status and alarm bits
#     coil     0..     commands
#     holding  0x0100..  setpoints
#  Gaps between blocks are deliberate: a real map is sparse, and a master that
#  blind-reads across a gap must get exception 02, not a zero.
# ─────────────────────────────────────────────────────────────────────────────

MODBUS_MAPS: Dict[str, ModbusMap] = {

    # ── Utility feed — Schneider PowerLogic ION9000 ─────────────────────────
    # Revenue/PQ meter. ION serves measurements as IEEE754 float32, which is why
    # this map is the only one with no scaling factors: the float carries the
    # engineering unit directly. This is the map that proves an integration's
    # float32 decoding, and it is deliberately the odd one out.
    "utility_feed": ModbusMap(
        map_id="SIM-ION9000-v1", vendor="Schneider Electric",
        product="PowerLogic ION9000", word_order=WORD_BIG,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Voltage_LL_Avg",   "util_voltage",         "f32", 1.0, "V"),
                _P(0x0002, "Current_Avg",      "util_current",         "f32", 1.0, "A"),
                _P(0x0004, "Frequency",        "util_frequency",       "f32", 1.0, "Hz"),
                _P(0x0006, "Power_Factor",     "util_power_factor",    "f32", 1.0, ""),
                _P(0x0010, "Voltage_A_N",      "util_va",              "f32", 1.0, "V"),
                _P(0x0012, "Voltage_B_N",      "util_vb",              "f32", 1.0, "V"),
                _P(0x0014, "Voltage_C_N",      "util_vc",              "f32", 1.0, "V"),
                _P(0x0016, "Current_A",        "util_ia",              "f32", 1.0, "A"),
                _P(0x0018, "Current_B",        "util_ib",              "f32", 1.0, "A"),
                _P(0x001A, "Current_C",        "util_ic",              "f32", 1.0, "A"),
                _P(0x0020, "Active_Power",     "util_kw",              "f32", 1.0, "kW"),
                _P(0x0022, "Reactive_Power",   "util_kvar",            "f32", 1.0, "kVAR"),
                _P(0x0024, "Apparent_Power",   "util_kva",             "f32", 1.0, "kVA"),
                _P(0x0026, "Voltage_Imbalance","util_phase_imbalance", "f32", 1.0, "%"),
                _P(0x0028, "THD_Voltage",      "util_thd_v",           "f32", 1.0, "%"),
                _P(0x002A, "THD_Current",      "util_thd_i",           "f32", 1.0, "%"),
                _P(0x002C, "Demand_Peak_kW",   "util_peak_kw",         "f32", 1.0, "kW"),
                # Non-volatile accumulator. A revenue meter that resets on reboot
                # is unmistakably fake, so this is persisted by the controller.
                _P(0x0030, "Energy_Delivered", "util_energy_kwh",      "f32", 1.0, "kWh"),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Service_Healthy", "util_status", truthy="normal"),
            ],
        },
    ),

    # ── Switchgear — Eaton Magnum DS / Digitrip via Power Xpert Gateway ─────
    # Scaled integers, and word-SWAPPED 32-bit values: the PXG presents the trip
    # unit's data with the low word first. Anyone who decodes this map with the
    # ION9000's word order gets energy readings off by a factor of 65536, which
    # is the single most common real Modbus integration bug.
    "switchgear": ModbusMap(
        map_id="SIM-EATON-PXG-MAGNUM-v1", vendor="Eaton",
        product="Magnum DS / Digitrip via Power Xpert Gateway",
        word_order=WORD_SWAP,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Voltage_LL_Avg",   "swgr_voltage",         "u16", 10, "V"),
                _P(0x0001, "Current_Avg",      "swgr_current",         "u16",  1, "A"),
                _P(0x0002, "Frequency",        "swgr_frequency",       "u16", 10, "Hz"),
                _P(0x0003, "Power_Factor",     "swgr_power_factor",    "s16", 100, ""),
                _P(0x0004, "Load_Percent",     "swgr_load_pct",        "u16", 10, "%"),
                _P(0x0010, "Voltage_A_N",      "swgr_va",              "u16", 10, "V"),
                _P(0x0011, "Voltage_B_N",      "swgr_vb",              "u16", 10, "V"),
                _P(0x0012, "Voltage_C_N",      "swgr_vc",              "u16", 10, "V"),
                _P(0x0013, "Current_A",        "swgr_ia",              "u16",  1, "A"),
                _P(0x0014, "Current_B",        "swgr_ib",              "u16",  1, "A"),
                _P(0x0015, "Current_C",        "swgr_ic",              "u16",  1, "A"),
                _P(0x0020, "Active_Power",     "swgr_kw",              "s32",  1, "kW"),
                _P(0x0022, "Reactive_Power",   "swgr_kvar",            "s32",  1, "kVAR"),
                _P(0x0024, "Apparent_Power",   "swgr_kva",             "u32",  1, "kVA"),
                _P(0x0026, "Voltage_Imbalance","swgr_phase_imbalance", "u16", 10, "%"),
                _P(0x0030, "Energy_Delivered", "swgr_energy_kwh",      "u32", 10, "kWh"),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Bus_Energized",   "swgr_bus_status",     truthy="energized"),
                ModbusPoint(1, "Breaker_Closed",  "swgr_breaker_status", truthy="closed"),
                ModbusPoint(2, "Source_Generator", "swgr_source",        truthy="generator"),
            ],
        },
    ),

    # ── MCC — Eaton Freedom 2100 with metered main ──────────────────────────
    "mcc": ModbusMap(
        map_id="SIM-EATON-PXM-MCC-v1", vendor="Eaton",
        product="Freedom 2100 metered main", word_order=WORD_SWAP,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Voltage_LL_Avg",   "mcc_voltage",         "u16", 10, "V"),
                _P(0x0001, "Current_Avg",      "mcc_current",         "u16",  1, "A"),
                _P(0x0002, "Frequency",        "mcc_frequency",       "u16", 10, "Hz"),
                _P(0x0003, "Power_Factor",     "mcc_power_factor",    "s16", 100, ""),
                _P(0x0004, "Load_Percent",     "mcc_load_pct",        "u16", 10, "%"),
                _P(0x0010, "Voltage_A_N",      "mcc_va",              "u16", 10, "V"),
                _P(0x0011, "Voltage_B_N",      "mcc_vb",              "u16", 10, "V"),
                _P(0x0012, "Voltage_C_N",      "mcc_vc",              "u16", 10, "V"),
                _P(0x0013, "Current_A",        "mcc_ia",              "u16",  1, "A"),
                _P(0x0014, "Current_B",        "mcc_ib",              "u16",  1, "A"),
                _P(0x0015, "Current_C",        "mcc_ic",              "u16",  1, "A"),
                _P(0x0020, "Active_Power",     "mcc_kw",              "s32",  1, "kW"),
                _P(0x0022, "Reactive_Power",   "mcc_kvar",            "s32",  1, "kVAR"),
                _P(0x0024, "Apparent_Power",   "mcc_kva",             "u32",  1, "kVA"),
                _P(0x0026, "Voltage_Imbalance","mcc_phase_imbalance", "u16", 10, "%"),
                _P(0x0030, "Energy_Delivered", "mcc_energy_kwh",      "u32", 10, "kWh"),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Bus_Energized", "mcc_status", truthy="energized"),
                ModbusPoint(1, "Tie_Closed",    "mcc_tie",    truthy="closed"),
                ModbusPoint(2, "Source_Tie",    "mcc_source", truthy="tie"),
            ],
        },
    ),

    # ── MPP — Schneider PowerLogic PM5000 panel-main meter ──────────────────
    # A panelboard meter, so no breaker/source bits: it measures, it does not
    # switch. Kept on the ION's big-endian order (same vendor family) while the
    # Eaton gear is word-swapped, which is exactly the heterogeneity a real site
    # forces an integrator to handle.
    "mpp": ModbusMap(
        map_id="SIM-PM5000-v1", vendor="Schneider Electric",
        product="PowerLogic PM5000", word_order=WORD_BIG,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Voltage_LL_Avg",   "mpp_voltage",         "u16", 10, "V"),
                _P(0x0001, "Current_Avg",      "mpp_current",         "u16",  1, "A"),
                _P(0x0002, "Frequency",        "mpp_frequency",       "u16", 10, "Hz"),
                _P(0x0003, "Power_Factor",     "mpp_power_factor",    "s16", 100, ""),
                _P(0x0004, "Load_Percent",     "mpp_load_pct",        "u16", 10, "%"),
                _P(0x0010, "Voltage_A_N",      "mpp_va",              "u16", 10, "V"),
                _P(0x0011, "Voltage_B_N",      "mpp_vb",              "u16", 10, "V"),
                _P(0x0012, "Voltage_C_N",      "mpp_vc",              "u16", 10, "V"),
                _P(0x0013, "Current_A",        "mpp_ia",              "u16",  1, "A"),
                _P(0x0014, "Current_B",        "mpp_ib",              "u16",  1, "A"),
                _P(0x0015, "Current_C",        "mpp_ic",              "u16",  1, "A"),
                _P(0x0020, "Active_Power",     "mpp_kw",              "s32",  1, "kW"),
                _P(0x0022, "Reactive_Power",   "mpp_kvar",            "s32",  1, "kVAR"),
                _P(0x0024, "Apparent_Power",   "mpp_kva",             "u32",  1, "kVA"),
                _P(0x0026, "Voltage_Imbalance","mpp_phase_imbalance", "u16", 10, "%"),
                _P(0x0030, "Energy_Delivered", "mpp_energy_kwh",      "u32", 10, "kWh"),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Panel_Energized", "mpp_status", truthy="energized"),
            ],
        },
    ),

    # ── Generator — Caterpillar EMCP 4.2 ────────────────────────────────────
    # Deliberately thin on engine parameters: see NO FABRICATED READINGS above.
    #
    # NO Remote_Start COIL, though every real EMCP has one. The genset lifecycle
    # here is owned by PowerTransferEngine's transfer state machine (utility_ok /
    # gens_startable -> cranking -> running), which has no external command
    # surface. A coil wired to nothing would answer exception 04 forever, and a
    # coil wired *into* that state machine would let a Modbus master fight the
    # ATS sequence for control of gen_status. Adding the command belongs in
    # PowerTransferEngine, once, for every plane — not as a side effect of adding
    # a protocol. The write path itself is built and guarded; it has no armed
    # points until that exists.
    "generator": ModbusMap(
        map_id="SIM-EMCP42-v1", vendor="Caterpillar",
        product="EMCP 4.2", word_order=WORD_BIG, accept_any_unit=False,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Genset_Load_Percent", "gen_load_pct",   "u16", 10, "%"),
                _P(0x0001, "Fuel_Level",          "gen_fuel_pct",   "u16", 10, "%"),
                _P(0x0002, "Start_Attempts",      "gen_start_attempts", "u16", 1, ""),
                _P(0x0020, "Active_Power",        "gen_kw",         "s32",  1, "kW"),
                # Two accumulators a real EMCP keeps in non-volatile memory:
                # lifetime hours (persisted) and this-run minutes (not).
                _P(0x0030, "Total_Run_Hours",     "gen_run_hours",  "u32", 10, "h"),
                _P(0x0032, "Current_Run_Minutes", "gen_runtime_min","u32",  1, "min"),
            ],
            SPACE_HOLDING: [
                # An enum in a holding register, which is how EMCP presents mode.
                ModbusPoint(0x0100, "Engine_State", "gen_status",
                            enum=_GEN_ST, enum_default=1),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Engine_Running",   "gen_status", truthy="running"),
                ModbusPoint(1, "Alarm_Low_Fuel",   "gen_alarm_low_fuel"),
                ModbusPoint(2, "Alarm_Low_Coolant","gen_alarm_low_coolant"),
                ModbusPoint(3, "Alarm_High_Temp",  "gen_alarm_temp"),
                ModbusPoint(4, "Alarm_Transfer",   "gen_alarm_transfer"),
                ModbusPoint(5, "Battery_Fault",    "gen_battery_status", truthy="failure"),
            ],
        },
    ),

    # ── ATS — ASCO 7000 Series ──────────────────────────────────────────────
    # A switch: position, both source availabilities, both source frequencies,
    # and the two counters an ATS keeps forever. No kW — an ATS does not meter.
    "ats": ModbusMap(
        map_id="SIM-ASCO7000-v1", vendor="ASCO Power Technologies",
        product="7000 Series ATS", word_order=WORD_BIG,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Normal_Voltage",     "ats_normal_voltage",    "u16", 10, "V"),
                _P(0x0001, "Emergency_Voltage",  "ats_emergency_voltage", "u16", 10, "V"),
                _P(0x0002, "Active_Frequency",   "ats_frequency",         "u16", 10, "Hz"),
                _P(0x0003, "Normal_Frequency",   "ats_normal_frequency",  "u16", 10, "Hz"),
                _P(0x0004, "Emergency_Frequency","ats_emergency_frequency","u16",10, "Hz"),
                _P(0x0030, "Transfer_Count",     "ats_transfer_count",    "u32",  1, ""),
                _P(0x0032, "Time_On_Emergency",  "ats_time_on_emergency", "u32",  1, "s"),
            ],
            SPACE_HOLDING: [
                ModbusPoint(0x0100, "Switch_Position", "ats_position",
                            enum=_ATS_POS, enum_default=0),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "Normal_Available",    "ats_normal_available",    truthy="yes"),
                ModbusPoint(1, "Emergency_Available", "ats_emergency_available", truthy="yes"),
                ModbusPoint(2, "On_Emergency",        "ats_position",  truthy="emergency"),
                # Not_In_Auto is the interlock the write path checks: a real ATS
                # in Local/Manual refuses remote commands.
                ModbusPoint(3, "Not_In_Auto",         "ats_not_in_auto"),
                ModbusPoint(4, "Fail_To_Transfer",    "ats_fail_to_transfer"),
            ],
        },
    ),

    # ── UPS — Vertiv Liebert EXL S1 via IS-UNITY-DP ─────────────────────────
    # The one device in this set that genuinely serves SNMP, BACnet and Modbus
    # off a single card at the same time, so a three-plane view of it is not a
    # simulator artefact.
    "ups": ModbusMap(
        map_id="SIM-ISUNITY-EXL-v1", vendor="Vertiv",
        product="Liebert EXL S1 (IS-UNITY-DP)", word_order=WORD_BIG,
        points={
            SPACE_INPUT: [
                _P(0x0000, "Input_Voltage",   "ups_input_voltage",   "u16", 10, "V"),
                _P(0x0001, "Input_Frequency", "ups_input_frequency", "u16", 10, "Hz"),
                _P(0x0002, "Output_Load",     "ups_output_load",     "u16", 10, "%"),
                _P(0x0003, "Battery_Health",  "ups_battery_health",  "u16", 10, "%"),
                _P(0x0004, "Battery_Runtime", "ups_runtime_min",     "u16",  1, "min"),
                _P(0x0020, "Output_Power",    "ups_output_kw",       "s32",  1, "kW"),
                _P(0x0030, "Energy_Delivered","ups_energy_kwh",      "u32", 10, "kWh"),
            ],
            SPACE_HOLDING: [
                ModbusPoint(0x0100, "Operating_Mode", "ups_operating_mode",
                            enum=_UPS_MODE, enum_default=1),
            ],
            SPACE_DISCRETE: [
                ModbusPoint(0, "On_Battery",        "ups_status",           truthy="on_battery"),
                ModbusPoint(1, "Low_Battery",       "ups_status",           truthy="low_battery"),
                ModbusPoint(2, "Battery_Fault",     "ups_battery_status",   truthy="failure"),
                ModbusPoint(3, "Bypass_Active",     "ups_bypass_status",    truthy="on"),
                ModbusPoint(4, "Fan_Fault",         "ups_fan_status",       truthy="failure"),
                ModbusPoint(5, "Charger_Fault",     "ups_charger_status",   truthy="failure"),
                ModbusPoint(6, "Rectifier_Fault",   "ups_rectifier_status", truthy="failure"),
                ModbusPoint(7, "Phase_Fault",       "ups_phase_status",     truthy="failure"),
            ],
        },
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
#  Field transmitters on an RS-485 trunk
#
#  These are the plant header instruments — chilled/condenser water thermowells
#  and the CHW magnetic flow meter. They are NOT native-TCP devices and never
#  appear in MODBUS_MAPS: a transmitter has no IP, it has a 4-20 mA loop or a
#  two-wire RS-485 drop, and it is reached through a gateway by unit id.
#
#  Deliberately tiny — two or three registers. A real Rosemount/Endress
#  transmitter map IS this small, and padding it out with invented diagnostics
#  would be the fabrication this module refuses elsewhere.
#
#  Keyed by the probe role that core.device_state_store._probe_role derives from
#  the device's name prefix (CHWS/CHWR/CWS/CWR/CTB/FLOW).
# ─────────────────────────────────────────────────────────────────────────────
_TEMP_TX = ModbusMap(
    map_id="SIM-RTD-TX-v1", vendor="Generic", product="RTD Temperature Transmitter",
    word_order=WORD_BIG, accept_any_unit=False,
    points={
        SPACE_INPUT: [
            _P(0x0000, "Process_Value", "water_temp", "s16", 10, "degC"),
        ],
        SPACE_DISCRETE: [
            # A transmitter reporting a value it has not acquired is worse than
            # one reporting nothing: the BMS trends the placeholder as real.
            ModbusPoint(0, "Reading_Valid", "water_temp",
                        truthy=lambda v: v is not None),
        ],
    },
)

_FLOW_TX = ModbusMap(
    map_id="SIM-MAGFLOW-TX-v1", vendor="Generic", product="Magnetic Flow Meter",
    word_order=WORD_BIG, accept_any_unit=False,
    points={
        SPACE_INPUT: [
            _P(0x0000, "Flow_Rate", "water_flow_lps", "u16", 100, "l/s"),
        ],
        SPACE_DISCRETE: [
            ModbusPoint(0, "Reading_Valid", "water_flow_lps",
                        truthy=lambda v: v is not None),
        ],
    },
)

PROBE_MAPS: Dict[str, ModbusMap] = {
    "chw_supply": _TEMP_TX, "chw_return": _TEMP_TX,
    "cw_supply":  _TEMP_TX, "cw_return":  _TEMP_TX,
    "ct_basin":   _TEMP_TX, "chw_flow":   _FLOW_TX,
}


def get_probe_map(probe_role: str) -> Optional[ModbusMap]:
    return PROBE_MAPS.get(str(probe_role))


# Device types that get a Modbus server in v1.  CRAH/CDU/PDU are intentionally
# absent: their cards really do speak Modbus, but this simulator already serves
# their values over SNMP and BACnet, and a third rendering of identical numbers
# is maintenance without signal.  Chiller/pump/tower/valve stay BACnet-only
# (a real unit carries one comm card, not two).  RPP has no comms at all and its
# branch currents are already metered by the Verdigris EV2s.
MODBUS_DEVICE_TYPES = frozenset(MODBUS_MAPS.keys())

# Non-volatile registers. A real meter keeps these across a power cycle; the
# controller persists them to disk for the same reason BACnet persists EV2 kWh.
NONVOLATILE_KEYS = frozenset({
    "util_energy_kwh", "swgr_energy_kwh", "mcc_energy_kwh", "mpp_energy_kwh",
    "ups_energy_kwh", "gen_run_hours", "ats_transfer_count",
    "ats_time_on_emergency",
})


# ─────────────────────────────────────────────────────────────────────────────
#  Encoding
# ─────────────────────────────────────────────────────────────────────────────
def _clamp(raw: int, dtype: str) -> int:
    """Saturate rather than wrap.

    A 16-bit register physically cannot carry 70000, and real meters saturate at
    full scale instead of wrapping to 4464 — a wrapped value looks like a live
    reading and will be trended as one, whereas a pegged value reads as an
    obvious out-of-range.
    """
    if dtype == "u16":
        return max(0, min(0xFFFF, raw))
    if dtype == "s16":
        return max(-32768, min(32767, raw)) & 0xFFFF
    if dtype == "u32":
        return max(0, min(0xFFFFFFFF, raw))
    if dtype == "s32":
        return max(-2147483648, min(2147483647, raw)) & 0xFFFFFFFF
    return raw


def encode_point(point: ModbusPoint, value: Any, word_order: str) -> List[int]:
    """Render one ext value as the register word(s) that point occupies."""
    if point.const is not None:
        return _split32(point.const, word_order) if point.width == 2 else [point.const & 0xFFFF]

    if point.enum:
        return [point.enum.get(str(value), point.enum_default) & 0xFFFF]

    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(point.default)

    if point.dtype == "f32":
        raw = struct.unpack(">I", struct.pack(">f", num))[0]
        return _split32(raw, word_order)

    raw = _clamp(int(round(num * point.scale)), point.dtype)
    return _split32(raw, word_order) if point.width == 2 else [raw & 0xFFFF]


def decode_registers(point: ModbusPoint, regs: List[int], word_order: str) -> float:
    """Inverse of encode_point, for write requests. Returns engineering units."""
    if point.dtype == "f32":
        return struct.unpack(">f", struct.pack(">I", _join32(regs, word_order)))[0]

    if point.width == 2:
        raw = _join32(regs, word_order)
        if point.dtype == "s32" and raw >= 0x80000000:
            raw -= 0x100000000
    else:
        raw = regs[0] & 0xFFFF
        if point.dtype == "s16" and raw >= 0x8000:
            raw -= 0x10000

    return raw / (point.scale or 1.0)


def encode_bit(point: ModbusPoint, value: Any) -> int:
    """Render one ext value as a coil/discrete bit.

    `truthy` carries the device's own vocabulary: `ups_status == "on_battery"`,
    `swgr_breaker_status == "closed"`. Without it the value is read as a number,
    which is what the plain 0/1 alarm keys (gen_alarm_low_fuel and friends) are.
    """
    t = point.truthy
    if t is None:
        try:
            return 1 if float(value) else 0
        except (TypeError, ValueError):
            return 1 if str(value).lower() in ("yes", "true", "on", "closed") else 0
    if callable(t):
        return 1 if t(value) else 0
    if isinstance(t, (list, tuple, set, frozenset)):
        return 1 if str(value) in {str(x) for x in t} else 0
    return 1 if str(value) == str(t) else 0


# ─────────────────────────────────────────────────────────────────────────────
#  Lookup helpers
# ─────────────────────────────────────────────────────────────────────────────
def get_map(device_type: str) -> Optional[ModbusMap]:
    return MODBUS_MAPS.get(str(device_type))


def build_address_index(mmap: ModbusMap) -> Dict[str, Dict[int, ModbusPoint]]:
    """{space: {addr: point}} — one entry per *starting* address.

    A 32-bit point occupies addr and addr+1, but only addr is indexed here; the
    slave resolves a read that starts mid-point by walking backwards, which is
    how a real device answers a master that reads the low word alone.
    """
    idx: Dict[str, Dict[int, ModbusPoint]] = {}
    for space, points in mmap.points.items():
        idx[space] = {p.addr: p for p in points}
    return idx


def covered_addresses(mmap: ModbusMap, space: str) -> Dict[int, Tuple[ModbusPoint, int]]:
    """{addr: (point, word_offset)} for every address the space actually covers.

    Anything absent from this dict is a gap, and a read of a gap is exception 02
    (illegal data address) — never a zero. Sparse maps are the norm, and a master
    that blind-scans must be told where the map stops.
    """
    out: Dict[int, Tuple[ModbusPoint, int]] = {}
    for p in mmap.points.get(space, []):
        width = p.width if space in REG_SPACES else 1
        for off in range(width):
            out[p.addr + off] = (p, off)
    return out

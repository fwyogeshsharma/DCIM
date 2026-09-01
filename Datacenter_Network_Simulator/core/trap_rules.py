"""
Default SNMP trap rule definitions and JSON serialization helpers.

All mandatory rules from the specification are included.
Rules can be exported/imported as JSON for runtime reconfiguration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from core.rule_engine import Rule, Condition

log = logging.getLogger(__name__)


# ── Builder helpers ───────────────────────────────────────────────────────────

def _threshold(metric: str, op: str, value: float, duration: float = 0.0) -> Condition:
    return Condition("threshold", metric=metric, operator=op,
                     threshold=value, duration_sec=duration)


def _nameplate(metric: str, op: str, nameplate_metric: str,
               scale: float = 1.0, fallback: float = 0.0) -> Condition:
    """Compare a metric against the DEVICE'S OWN nameplate, scaled.

    `fallback` applies when the device does not carry that nameplate, so a SKU
    missing from the catalog still evaluates rather than silently never firing.
    """
    return Condition("threshold", metric=metric, operator=op,
                     threshold=fallback, threshold_metric=nameplate_metric,
                     threshold_scale=scale)

def _state_change(metric: str, from_s: str | None, to_s: str | None) -> Condition:
    return Condition("state_change", metric=metric, from_state=from_s, to_state=to_s)


def _temporal(event_type: str, count: int, window: float) -> Condition:
    return Condition("temporal", event_type=event_type,
                     event_count=count, window_sec=window)


def _quiet(event_type: str, window: float) -> Condition:
    """True when *event_type* has NOT occurred for *window* seconds.

    The inverse of _temporal, and the only way to express "the burst stopped".
    An episodic alarm has no state to return to and no threshold to re-cross, so
    silence is the only thing that can clear it."""
    return Condition("quiet", event_type=event_type, window_sec=window)


def _composite(*conds: Condition, logic: str = "AND") -> Condition:
    return Condition("composite", logic=logic, conditions=list(conds))


def _rack(min_devices: int) -> Condition:
    return Condition("rack_failure", threshold=float(min_devices))


def _rule(name: str, cond: Condition, oid: str, *,
          severity: str = "major",
          priority: int = 100,
          device_types: list | None = None,
          model_names: list | None = None,
          model_names_exclude: list | None = None,
          recovery: bool = False,
          recovery_of: str = "") -> Rule:
    return Rule(
        rule_name=name,
        condition=cond,
        trap_oid=oid,
        severity=severity,
        priority=priority,
        device_types=device_types or [],
        model_names=model_names or [],
        model_names_exclude=model_names_exclude or [],
        is_recovery=recovery,
        recovery_of=recovery_of,
    )


# The plant's header instruments are DeviceType.SENSOR but are NOT rack air probes
# (see core/device_state_store._PROBE_ROLES). Every cold-aisle rule below carries
# this exclusion so a 35 °C condenser-return thermowell cannot raise a room
# over-temperature alarm and a flow meter cannot raise "humidity low".
_AIR_PROBES_ONLY = ["Plant *"]

# Plant header probe model names, for the water-loop rules that DO belong on them.
_M_CHW_SUPPLY = ["Plant CHW Supply Temp"]
_M_CHW_RETURN = ["Plant CHW Return Temp"]
_M_CHW_FLOW   = ["Plant CHW Flow Meter"]
_M_CW_SUPPLY  = ["Plant CW Supply Temp"]
_M_CW_RETURN  = ["Plant CW Return Temp"]
_M_CT_BASIN   = ["Plant CT Basin Temp"]


# ── Default ruleset ───────────────────────────────────────────────────────────

DEFAULT_RULES: List[Rule] = [

    # ── Standard interface traps (SNMPv2-MIB) ────────────────────────────────

    # Production-layer link traps. Scoped to the link-bearing data-plane device
    # types — oob_switch is intentionally excluded so its management-layer links
    # fire ONLY the dedicated OOBSwitchLinkDown/Up rules (critical severity),
    # not a duplicate generic linkDown on the same interface.
    _rule("LinkDown",
          _state_change("interface_status", "up", "down"),
          "1.3.6.1.6.3.1.1.5.3",
          severity="major", priority=200,
          device_types=["router", "switch", "server", "firewall", "load_balancer"]),

    # LinkUp was already the exact inverse of LinkDown but was not marked as its
    # recovery, so the trap went out while the alert state stayed raised — the link
    # was back, the NMS said it was down, forever. Clearing is per INTERFACE (see
    # RuleEngine._eval_interface_rule): eth3 recovering does not clear a LinkDown
    # still raised on eth7 of the same device.
    _rule("LinkUp",
          _state_change("interface_status", "down", "up"),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=200,
          device_types=["router", "switch", "server", "firewall", "load_balancer"],
          recovery=True, recovery_of="LinkDown"),

    # ── Enterprise: resource threshold traps ─────────────────────────────────

    _rule("HighCPU",
          _threshold("cpu_usage", ">", 90.0),
          "1.3.6.1.4.1.99999.1.1",
          severity="major", priority=150),

    _rule("HighCPUSustained",
          _threshold("cpu_usage", ">", 90.0, duration=300.0),
          "1.3.6.1.4.1.99999.1.20",
          severity="critical", priority=160),

    _rule("HighMemory",
          _threshold("memory_usage", ">", 85.0),
          "1.3.6.1.4.1.99999.1.2",
          severity="major", priority=150),

    # CPU/ASIC die temperature — IT gear only. `temperature` maps to device.cpu_temp
    # (a silicon die/ASIC sensor), so this must NOT evaluate on power/cooling gear
    # (RPP/PDU/UPS/CRAH/chiller): those have no CPU and would emit a nonsensical
    # "CPU over-temperature" trap. Thermal alarms on power kit use their own metric
    # (e.g. the rack-PDU intake probe → pdu_temperature), not this rule.
    # oob_switch is in this list because the console switches have an ASIC and
    # a die sensor like everything else here, and because leaving them out made
    # the simulator inconsistent with itself: APPLICABLE_TRAPS offers
    # TEMPERATURE_ALERT for the type and the Inject Fault menu offers
    # temp_high, so an operator could ramp an OOB switch to 93 C and watch
    # nothing whatsoever happen. It stayed silent through a whole fault
    # campaign for exactly that reason.
    _rule("HighTemperature",
          _threshold("temperature", ">", 90.0),
          "1.3.6.1.4.1.99999.1.3",
          severity="critical", priority=180,
          device_types=["server", "switch", "router", "firewall",
                        "load_balancer", "oob_switch"]),

    # ── Chassis fan faults ────────────────────────────────────────────────────
    #
    # Measured as a % of the chassis's OWN minimum duty (DeviceFact.fan_speed_pct),
    # never raw RPM: 3000 RPM is a healthy 4U at idle and a stalled 1U. The two
    # rules split the way a real BMC's do — a fan dragging below its floor is a
    # degraded-but-cooling condition an operator schedules around, a stopped fan is
    # a lose-the-box-in-minutes condition. Servers only: no other device type in
    # this model carries a chassis fan (a UPS fan has its own ups_fan_status rule).
    #
    # SCOPE, stated plainly: this models the fan BANK, not an individual rotor. A
    # real BMC reads a tach per fan and, when one dies, drives the SURVIVORS to full
    # duty — so a single-rotor failure shows up as a per-fan Critical alongside a
    # RISING chassis average. Modelling that needs per-fan state the simulator does
    # not carry. What fires here is the whole-bank case: failed fan controller,
    # blocked filter, fan power loss, or an operator-forced speed.
    _rule("FanUnderSpeed",
          _threshold("fan_speed_pct", "<", 90.0),
          "1.3.6.1.4.1.99999.1.29",
          severity="major", priority=175,
          device_types=["server"]),

    _rule("FanFailure",
          _threshold("fan_speed_pct", "<", 25.0),
          "1.3.6.1.4.1.99999.1.30",
          severity="critical", priority=185,
          device_types=["server"]),

    # ── Enterprise: link flap (temporal) ─────────────────────────────────────

    _rule("LinkFlap",
          _temporal("linkDown", count=3, window=60.0),
          "1.3.6.1.4.1.99999.1.4",
          severity="critical", priority=170),

    # The one alarm no transition can clear. LinkUp is useless here: a flapping link
    # comes up on every bounce, so clearing on LinkUp would clear the flap alarm in
    # the middle of the flapping. The honest all-clear is silence — hence the quiet
    # condition type, added for exactly this.
    #
    # 120 s = two full detection windows with no further linkDown. One window would
    # be too eager: a link flapping every ~40 s clears and re-raises forever instead
    # of reading as one sustained problem, which is what an operator needs to see to
    # justify replacing the optic.
    _rule("LinkFlapCleared",
          _quiet("linkDown", window=120.0),
          "1.3.6.1.4.1.99999.1.40",
          severity="informational", priority=100,
          recovery=True, recovery_of="LinkFlap"),

    # ── Enterprise: rack failure (cross-device correlation) ───────────────────

    _rule("RackFailure",
          _rack(min_devices=3),
          "1.3.6.1.4.1.99999.1.5",
          severity="critical", priority=190),

    # Clears once FEWER THAN 2 devices in the rack are still impaired — the rack-rule
    # threshold reads as a floor on a recovery (see RuleEngine._eval_rack_rule).
    #
    # 2, not 3, so there is a one-device dead band against the alarm: a rack sitting
    # at exactly 3 impaired with one machine flapping in and out would otherwise
    # raise and clear the correlation alarm on alternate ticks.
    #
    # Not zero either. This alarm exists to say "several machines in one cabinet went
    # away together, suspect the PDU / ToR / breaker rather than the servers". Once
    # only a single device is left down that inference no longer holds — it is an
    # ordinary single-device failure, already reported by its own LinkDown — so the
    # correlation alarm has done its job and should stand down.
    _rule("RackRecovered",
          _rack(min_devices=2),
          "1.3.6.1.4.1.99999.1.39",
          severity="informational", priority=100,
          recovery=True, recovery_of="RackFailure"),

    # ── UPS traps (UPS-MIB) ───────────────────────────────────────────────────

    _rule("UPSOnBattery",
          _state_change("ups_status", "normal", "on_battery"),
          "1.3.6.1.2.1.33.2.0.1",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSLowBattery",
          _state_change("ups_status", None, "low_battery"),
          "1.3.6.1.2.1.33.2.0.2",
          severity="critical", priority=200,
          device_types=["ups"]),

    # low_battery is only ever reached FROM on_battery (the string draining past
    # _UPS_LOW_BATT_FRAC of its autonomy) and only ever leaves it when utility
    # returns and the scrub puts the UPS back to normal. So the clear is that one
    # transition — a separate rule from UPSUtilityRestored because recovery_of names
    # exactly one alarm, and an outage deep enough to hit low battery raises both.
    _rule("UPSLowBatteryCleared",
          _state_change("ups_status", "low_battery", "normal"),
          "1.3.6.1.4.1.99999.2.21",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSLowBattery"),

    # Repurposed as UPSBatteryFailure's clear rather than adding a second rule with
    # an identical condition. It already fired on every transition into "normal" and
    # recovered nothing; as a recovery it is skipped unless the failure alarm is
    # actually raised, so the trap stream loses a spurious informational and gains a
    # working clear. Does NOT cover disconnected -> normal: that is a different
    # alarm, and recovery_of takes one name (see UPSBatteryReconnected).
    _rule("UPSBatteryNormal",
          _state_change("ups_battery_status", "failure", "normal"),
          "1.3.6.1.4.1.99999.2.1",
          severity="informational", priority=200,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBatteryFailure"),

    _rule("UPSUtilityRestored",
          _state_change("ups_status", None, "normal"),
          "1.3.6.1.4.1.99999.2.2",
          severity="informational", priority=200,
          device_types=["ups"],
          recovery=True, recovery_of="UPSOnBattery"),

    _rule("UPSOutputOverload",
          _threshold("ups_output_load", ">", 90.0),
          "1.3.6.1.4.1.99999.2.3",
          severity="critical", priority=195,
          device_types=["ups"]),

    _rule("UPSOutputNormal",
          _threshold("ups_output_load", "<", 70.0),
          "1.3.6.1.4.1.99999.2.4",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSOutputOverload"),

    # The cooling-fan alarm on the UPS itself (UPS-MIB upsAlarmFanFailure), not a
    # server chassis fan — those are FanUnderSpeed/FanFailure above.
    _rule("UPSFanFailure",
          _state_change("ups_fan_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.5",
          severity="critical", priority=190,
          device_types=["ups"]),

    # Without this the alarm LATCHES. The component model already heals a failed UPS
    # fan back to "ok" on its own (the tick's component random-walk), and clearing an
    # injected failure sets it straight back — but rule_engine clears in_alert ONLY
    # from a rule naming the alarm in recovery_of, never on the condition going
    # false. So the UPS stayed lit in the faulted view, and no NMS watching the trap
    # stream ever learned the fan came back, for the rest of the run.
    _rule("UPSFanNormal",
          _state_change("ups_fan_status", "failure", "ok"),
          "1.3.6.1.4.1.99999.2.20",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSFanFailure"),

    _rule("UPSBatteryFailure",
          _state_change("ups_battery_status", "normal", "failure"),
          "1.3.6.1.4.1.99999.2.6",
          severity="critical", priority=200,
          device_types=["ups"]),

    _rule("UPSBatteryDisconnected",
          _state_change("ups_battery_status", None, "disconnected"),
          "1.3.6.1.4.1.99999.2.7",
          severity="critical", priority=200,
          device_types=["ups"]),

    # A disconnected string is the one alarm here a human really does clear — the
    # breaker is racked back in or the cabinet reconnected. Modelled the same way
    # regardless: the state goes back to normal and the alarm must follow it.
    _rule("UPSBatteryReconnected",
          _state_change("ups_battery_status", "disconnected", "normal"),
          "1.3.6.1.4.1.99999.2.22",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBatteryDisconnected"),

    _rule("UPSChargerFailure",
          _state_change("ups_charger_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.8",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSChargerNormal",
          _state_change("ups_charger_status", "failure", "ok"),
          "1.3.6.1.4.1.99999.2.23",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSChargerFailure"),

    # UPS input is the 400 V (L-L) LV bus off its ATS, not a 230 V single-phase
    # feed — alarm at ±10% of nominal (440 / 360 V), the usual vendor input
    # over/under-voltage band. The clears sit ~2.5% inside the alarm points so a
    # sag/swell riding the threshold cannot chatter alarm/clear every tick.
    _rule("UPSInputVoltageHigh",
          _threshold("ups_input_voltage", ">", 440.0),
          "1.3.6.1.4.1.99999.2.9",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSInputVoltageNormal",
          _threshold("ups_input_voltage", "<", 430.0),
          "1.3.6.1.4.1.99999.2.18",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSInputVoltageHigh"),

    _rule("UPSInputVoltageLow",
          _threshold("ups_input_voltage", "<", 360.0),
          "1.3.6.1.4.1.99999.2.10",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSInputVoltageLowCleared",
          _threshold("ups_input_voltage", ">", 370.0),
          "1.3.6.1.4.1.99999.2.19",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSInputVoltageLow"),

    _rule("UPSFrequencyOutOfRange",
          _composite(
              _threshold("ups_input_frequency", "<", 49.0),
              _threshold("ups_input_frequency", ">", 51.0),
              logic="OR",
          ),
          "1.3.6.1.4.1.99999.2.11",
          severity="major", priority=175,
          device_types=["ups"]),

    # Clear is the INVERSE of the alarm — inside the band on both sides, so it has
    # to be AND where the alarm is OR. The 0.2 Hz inset mirrors the voltage rules'
    # hysteresis: a frequency wandering on 49.00 must not chatter alarm/clear.
    _rule("UPSFrequencyNormal",
          _composite(
              _threshold("ups_input_frequency", ">", 49.2),
              _threshold("ups_input_frequency", "<", 50.8),
              logic="AND",
          ),
          "1.3.6.1.4.1.99999.2.26",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSFrequencyOutOfRange"),

    _rule("UPSRectifierFailure",
          _state_change("ups_rectifier_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.12",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSRectifierNormal",
          _state_change("ups_rectifier_status", "failure", "ok"),
          "1.3.6.1.4.1.99999.2.24",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSRectifierFailure"),

    _rule("UPSPhaseFailure",
          _state_change("ups_phase_status", "ok", "failure"),
          "1.3.6.1.4.1.99999.2.13",
          severity="critical", priority=190,
          device_types=["ups"]),

    _rule("UPSPhaseNormal",
          _state_change("ups_phase_status", "failure", "ok"),
          "1.3.6.1.4.1.99999.2.25",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSPhaseFailure"),

    # ── PDU traps ─────────────────────────────────────────────────────────────

    # Already the exact inverse of PDUOutletOff, so it becomes that alarm's clear
    # rather than gaining a duplicate rule beside it (same treatment as
    # UPSBatteryNormal). Gated on the alarm being raised, which also stops it
    # announcing an outlet switching on that was never reported off.
    _rule("PDUOutletOn",
          _state_change("pdu_outlet_status", "off", "on"),
          "1.3.6.1.4.1.99999.6.1",
          severity="informational", priority=150,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUOutletOff"),

    _rule("PDUOutletOff",
          _state_change("pdu_outlet_status", "on", "off"),
          "1.3.6.1.4.1.99999.6.2",
          severity="major", priority=150,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUBreakerTripped",
          _state_change("pdu_breaker_status", "ok", "tripped"),
          "1.3.6.1.4.1.99999.6.3",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    # A real branch breaker is reset by hand at the strip — there is no auto-reclose
    # on a rack PDU. The clear therefore reports an OPERATOR action, not the fault
    # going away by itself, but it must still be reported: an NMS holding a tripped
    # breaker forever after the electrician reset it is how a rack ends up with a
    # permanently red tile nobody trusts.
    _rule("PDUBreakerReset",
          _state_change("pdu_breaker_status", "tripped", "ok"),
          "1.3.6.1.4.1.99999.6.21",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUBreakerTripped"),

    _rule("PDULoadHigh",
          _threshold("pdu_load", ">", 80.0),
          "1.3.6.1.4.1.99999.6.4",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDULoadCritical",
          _threshold("pdu_load", ">", 90.0),
          "1.3.6.1.4.1.99999.6.5",
          severity="critical", priority=185,
          device_types=["pdu", "floor_pdu"]),

    # Separate from PDULoadNormal (<70, clears PDULoadHigh): recovery_of names one
    # alarm, and a strip that crossed 90 raised both. Clears at 85 so dropping out
    # of critical is reported immediately, while the major stays up until 70 — which
    # is what an operator wants to see during a load-shed.
    _rule("PDULoadCriticalCleared",
          _threshold("pdu_load", "<", 85.0),
          "1.3.6.1.4.1.99999.6.29",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDULoadCritical"),

    # Clears sit inside the alarm points on every threshold pair below, so a value
    # sitting ON a threshold cannot chatter alarm/clear each tick. Same convention as
    # the UPS input-voltage rules: roughly 2% of nominal for voltage, and a wider
    # inset where the underlying walk is noisier (phase imbalance).
    _rule("PDUVoltageHigh",
          _threshold("pdu_voltage", ">", 240.0),
          "1.3.6.1.4.1.99999.6.6",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUVoltageNormal",
          _threshold("pdu_voltage", "<", 235.0),
          "1.3.6.1.4.1.99999.6.25",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUVoltageHigh"),

    _rule("PDUVoltageLow",
          _threshold("pdu_voltage", "<", 200.0),
          "1.3.6.1.4.1.99999.6.7",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUVoltageLowCleared",
          _threshold("pdu_voltage", ">", 205.0),
          "1.3.6.1.4.1.99999.6.26",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUVoltageLow"),

    _rule("PDUPhaseImbalance",
          _threshold("pdu_phase_imbalance", ">", 20.0),
          "1.3.6.1.4.1.99999.6.8",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    # Wide inset on purpose: the imbalance walk jumps to 21-35% when it faults and
    # sits at 0-5% when healthy, so anything below 15% is unambiguously recovered.
    _rule("PDUPhaseBalanceRestored",
          _threshold("pdu_phase_imbalance", "<", 15.0),
          "1.3.6.1.4.1.99999.6.27",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUPhaseImbalance"),

    _rule("PDUPowerFactorLow",
          _threshold("pdu_power_factor", "<", 0.70),
          "1.3.6.1.4.1.99999.6.9",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUPowerFactorNormal",
          _threshold("pdu_power_factor", ">", 0.75),
          "1.3.6.1.4.1.99999.6.28",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUPowerFactorLow"),

    _rule("PDUOutletFailure",
          _state_change("pdu_outlet_failure", "ok", "failed"),
          "1.3.6.1.4.1.99999.6.10",
          severity="critical", priority=195,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUOutletRestored",
          _state_change("pdu_outlet_failure", "failed", "ok"),
          "1.3.6.1.4.1.99999.6.22",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUOutletFailure"),

    _rule("PDUSmokeDetected",
          _state_change("pdu_smoke", "no", "yes"),
          "1.3.6.1.4.1.99999.6.11",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    # The one alarm here where latching is arguably CORRECT behaviour: a real smoke
    # head (and the VESDA/fire panel it reports to) latches until a human acknowledges
    # it, precisely so a transient detection cannot be missed. Modelled with a clear
    # anyway, because the sim's smoke state does return to "no" on its own — leaving
    # it latched would mean the trap stream and the device state permanently disagree,
    # which is a worse lie than the missing latch. If acknowledgement is ever modelled,
    # this is the rule that should become ack-driven rather than state-driven.
    _rule("PDUSmokeCleared",
          _state_change("pdu_smoke", "yes", "no"),
          "1.3.6.1.4.1.99999.6.23",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUSmokeDetected"),

    # A strip's overload reference is its OWN input breaker, per phase, so both
    # rules read the rating off the device (pdu_breaker_rating_a, filled from the
    # SKU catalog) instead of a fleet-wide constant. The constants below are only
    # the fallback for a SKU the catalog does not name.
    #
    # This replaces a known miscalibration. pdu_outlet_current used to be a
    # SINGLE-PHASE EQUIVALENT of the whole strip, I = P/(V*PF), while the 32 A it
    # was compared against is a PER-PHASE rating. On the 3-phase AP8886 that
    # over-read by exactly 3x, so a 27%-loaded strip reported 31.9 A against its
    # 32 A breaker and several seeded racks sat permanently over the threshold —
    # the alarm could not clear, because the current could never fall back under
    # the 30 A clear point. The metric is now per phase (see DeviceStateStore) and
    # the mixed fleet is handled properly: 32 A for the 3-phase AP8886, 30 A for
    # the single-phase AP8941 and PX2-5170CR, which one constant could never be.
    #
    # The clear sits at 90% of the rating, not at the rating itself, so a strip
    # hovering on its breaker cannot chatter alarm/clear each tick — the same
    # inset convention as the voltage and imbalance pairs above.
    _rule("PDUOutletCurrentHigh",
          _nameplate("pdu_outlet_current", ">", "pdu_breaker_rating_a",
                     scale=1.0, fallback=32.0),
          "1.3.6.1.4.1.99999.6.12",
          severity="major", priority=170,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUOutletCurrentNormal",
          _nameplate("pdu_outlet_current", "<", "pdu_breaker_rating_a",
                     scale=0.9, fallback=30.0),
          "1.3.6.1.4.1.99999.6.30",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUOutletCurrentHigh"),

    _rule("PDUGroundFault",
          _state_change("pdu_ground_fault", "no", "yes"),
          "1.3.6.1.4.1.99999.6.13",
          severity="critical", priority=200,
          device_types=["pdu", "floor_pdu"]),

    # Like the breaker, a real earth-leakage trip is reset by hand — the clear
    # reports the reset, not the leakage curing itself.
    _rule("PDUGroundFaultCleared",
          _state_change("pdu_ground_fault", "yes", "no"),
          "1.3.6.1.4.1.99999.6.24",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUGroundFault"),

    # ── Routing protocol traps ────────────────────────────────────────────────

    # bgpBackwardTransition. The sim's peer walk only models established <-> idle,
    # but the alarm names the real transition it stands for.
    _rule("BGPSessionDown",
          _state_change("bgp_session", "established", "idle"),
          "1.3.6.1.2.1.15.0.2",
          severity="critical", priority=190,
          device_types=["router", "firewall"]),

    # bgpEstablished (BGP4-MIB) — the standard counterpart to the backward
    # transition above, so an NMS with the stock MIB loaded reads the pair without a
    # vendor extension.
    #
    # from_state is deliberately ANY, not just "idle": a real session climbs back
    # through connect/active/opensent/openconfirm, and if the walk ever models that
    # FSM this rule still clears on the transition that actually matters — reaching
    # established. Nothing spurious follows from the wider match, because a recovery
    # only fires when its alarm is raised for that same peer.
    _rule("BGPSessionUp",
          _state_change("bgp_session", None, "established"),
          "1.3.6.1.2.1.15.0.1",
          severity="informational", priority=190,
          device_types=["router", "firewall"],
          recovery=True, recovery_of="BGPSessionDown"),

    # ── Composite trap: CPU and Temperature both high ─────────────────────────

    _rule("CriticalCPUAndTemp",
          _composite(
              _threshold("cpu_usage", ">", 90.0),
              _threshold("temperature", ">", 90.0),
              logic="AND",
          ),
          "1.3.6.1.4.1.99999.1.21",
          severity="critical", priority=200,
          device_types=["server", "switch", "router", "firewall", "load_balancer"]),

    # OR, not AND — this is the De Morgan inverse of the alarm. The alarm means "CPU
    # and temperature are BOTH critical", so it stops being true the moment EITHER
    # recovers: NOT(A AND B) == NOT A OR NOT B. An AND clear would hold a
    # "both critical" alarm raised on a device whose CPU is back at 50% just because
    # it is still hot — and the heat alone is already HighTemperature's job to report.
    #
    # Thresholds reuse the existing clear points (CPUNormal 70, TemperatureNormal 85)
    # so a device cannot sit in a state that is clear for one rule and alarmed for
    # another, and each leg keeps its own hysteresis gap against the 90/90 alarm.
    _rule("CriticalCPUAndTempCleared",
          _composite(
              _threshold("cpu_usage", "<", 70.0),
              _threshold("temperature", "<", 85.0),
              logic="OR",
          ),
          "1.3.6.1.4.1.99999.1.38",
          severity="informational", priority=100,
          device_types=["server", "switch", "router", "firewall", "load_balancer"],
          recovery=True, recovery_of="CriticalCPUAndTemp"),

    # ── Recovery rules ────────────────────────────────────────────────────────

    _rule("CPUNormal",
          _threshold("cpu_usage", "<", 70.0),
          "1.3.6.1.4.1.99999.1.13",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighCPU"),

    _rule("MemoryNormal",
          _threshold("memory_usage", "<", 70.0),
          "1.3.6.1.4.1.99999.1.14",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighMemory"),

    # HighCPUSustained did not latch — the duration branch dropped its in_alert on
    # its own — but nothing ever told the NMS: CPUNormal clears HighCPU, so the
    # CRITICAL was raised on the wire and never explicitly cleared. Same 70% clear
    # point as CPUNormal, since both track the one metric back to the same "healthy"
    # definition; a separate rule only because recovery_of names one alarm.
    #
    # This rule REQUIRES the duration-branch change in RuleEngine: while an alarm has
    # a registered recovery it must stop self-clearing, or this would be evaluated
    # against an already-cleared alert and never fire.
    _rule("CPUSustainedNormal",
          _threshold("cpu_usage", "<", 70.0),
          "1.3.6.1.4.1.99999.1.37",
          severity="informational", priority=100,
          recovery=True, recovery_of="HighCPUSustained"),

    # Recovery clears at 95, not 90: a fan hovering on the alarm threshold would
    # otherwise flap the trap pair every tick as the duty jitters across it.
    #
    # TWO recovery rules, one per alarm. in_alert is cleared ONLY by a rule naming
    # that alarm in recovery_of (rule_engine never clears it on the condition going
    # false), so a single recovery would leave FanFailure latched forever — the node
    # would stay lit in the faulted view long after the fans came back. Neither fires
    # spuriously: a recovery rule is skipped outright when its target is not in
    # alert, so a bank that only ever dipped below 90 emits FanSpeedNormal alone.
    _rule("FanSpeedNormal",
          _threshold("fan_speed_pct", ">", 95.0),
          "1.3.6.1.4.1.99999.1.31",
          severity="informational", priority=100,
          device_types=["server"],
          recovery=True, recovery_of="FanUnderSpeed"),

    _rule("FanRestored",
          _threshold("fan_speed_pct", ">", 95.0),
          "1.3.6.1.4.1.99999.1.32",
          severity="informational", priority=100,
          device_types=["server"],
          recovery=True, recovery_of="FanFailure"),

    # Same list as HighTemperature, and it has to stay that way: a recovery
    # scoped more narrowly than the raise it clears leaves the excluded type
    # able to raise an alarm and never able to end it.
    _rule("TemperatureNormal",
          _threshold("temperature", "<", 85.0),
          "1.3.6.1.4.1.99999.1.15",
          severity="informational", priority=100,
          device_types=["server", "switch", "router", "firewall",
                        "load_balancer", "oob_switch"],
          recovery=True, recovery_of="HighTemperature"),

    # ── Management layer: OOB switch link events ──────────────────────────────

    _rule("OOBSwitchLinkDown",
          _state_change("interface_status", "up", "down"),
          "1.3.6.1.6.3.1.1.5.3",
          severity="critical", priority=210,
          device_types=["oob_switch"]),

    # Same defect, same fix, on the management plane's own link pair.
    _rule("OOBSwitchLinkUp",
          _state_change("interface_status", "down", "up"),
          "1.3.6.1.6.3.1.1.5.4",
          severity="informational", priority=210,
          device_types=["oob_switch"],
          recovery=True, recovery_of="OOBSwitchLinkDown"),

    # ── Environmental sensor alerts ───────────────────────────────────────────

    # These four are the only cold-aisle rules with no model include-list, so they
    # are the ones that would have swept up the plant's water instruments (a CW
    # return thermowell sits at ~35 °C by design). Excluded explicitly; the water
    # loops get their own rules further down.
    _rule("SensorAmbientTempHigh",
          _threshold("ambient_temp", ">", 32.0),
          "1.3.6.1.4.1.99999.1.22",
          severity="major", priority=180,
          device_types=["sensor"], model_names_exclude=_AIR_PROBES_ONLY),

    _rule("SensorAmbientTempCritical",
          _threshold("ambient_temp", ">", 38.0),
          "1.3.6.1.4.1.99999.1.23",
          severity="critical", priority=185,
          device_types=["sensor"], model_names_exclude=_AIR_PROBES_ONLY),

    # Clears at 35 while SensorAmbientTempNormal holds the major until 28. During a
    # cooling recovery the operator sees the critical drop first and the major persist
    # until the aisle is genuinely back in band, rather than both clearing at once and
    # implying the hall recovered faster than it did.
    _rule("SensorAmbientTempCriticalCleared",
          _threshold("ambient_temp", "<", 35.0),
          "1.3.6.1.4.1.99999.1.33",
          severity="informational", priority=100,
          device_types=["sensor"], model_names_exclude=_AIR_PROBES_ONLY,
          recovery=True, recovery_of="SensorAmbientTempCritical"),

    _rule("SensorAmbientTempNormal",
          _threshold("ambient_temp", "<", 28.0),
          "1.3.6.1.4.1.99999.1.16",
          severity="informational", priority=100,
          model_names_exclude=_AIR_PROBES_ONLY,
          recovery=True, recovery_of="SensorAmbientTempHigh"),

    # ── Humidity alerts (Raritan + Vertiv + APC) ──────────────────────────────

    _rule("SensorHighHumidity",
          _threshold("humidity", ">", 70.0),
          "1.3.6.1.4.1.99999.1.24",
          severity="major", priority=175,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    _rule("SensorCriticalHumidity",
          _threshold("humidity", ">", 80.0),
          "1.3.6.1.4.1.99999.1.25",
          severity="critical", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    # Separate from SensorHumidityNormal (the 30-70 band, which clears the major):
    # recovery_of names one alarm, and crossing 80 raised both. Clearing the critical
    # at 75 reports the drop out of critical immediately while the major stays up
    # until humidity is back inside the ASHRAE band — the same major/critical
    # laddering used for PDU load.
    _rule("SensorCriticalHumidityCleared",
          _threshold("humidity", "<", 75.0),
          "1.3.6.1.4.1.99999.1.34",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorCriticalHumidity"),

    _rule("SensorLowHumidity",
          _threshold("humidity", "<", 30.0),
          "1.3.6.1.4.1.99999.1.26",
          severity="major", priority=175,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"]),

    # Clears at 34, not 35, on purpose: the humidity walk is clamped to a 35-65 band
    # (the CRAC humidifier holds it there), so a released low-humidity injection lands
    # EXACTLY on 35.0. A ">35" clear would then never fire on a sensor sitting at the
    # floor. 34 is comfortably above the 30 alarm and below the band, so it always
    # fires once the value is back under control.
    _rule("SensorLowHumidityCleared",
          _threshold("humidity", ">", 34.0),
          "1.3.6.1.4.1.99999.1.35",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorLowHumidity"),

    _rule("SensorHumidityNormal",
          _composite(
              _threshold("humidity", ">=", 30.0),
              _threshold("humidity", "<=", 70.0),
              logic="AND",
          ),
          "1.3.6.1.4.1.99999.1.17",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1", "APC NetBotz 355", "APC NetBotz 250", "Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorHighHumidity"),

    # ── Dew-point alert (Vertiv Geist GTHD — condensation risk) ──────────────

    _rule("SensorHighDewPoint",
          _threshold("dewpoint", ">", 21.0),
          "1.3.6.1.4.1.99999.1.7",
          severity="critical", priority=185,
          device_types=["sensor"],
          model_names=["Vertiv Geist GTHD"]),

    _rule("SensorDewPointNormal",
          _threshold("dewpoint", "<=", 17.0),
          "1.3.6.1.4.1.99999.1.18",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Vertiv Geist GTHD"],
          recovery=True, recovery_of="SensorHighDewPoint"),

    # ── Airflow alert (APC NetBotz 250 — cooling anomaly) ────────────────────

    _rule("SensorHighAirflow",
          _threshold("airflow", ">", 3.5),
          "1.3.6.1.4.1.99999.1.27",
          severity="major", priority=170,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"]),

    _rule("SensorLowAirflow",
          _threshold("airflow", "<", 0.3),
          "1.3.6.1.4.1.99999.1.28",
          severity="critical", priority=180,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"]),

    # 0.5 m/s, not 0.3: the airflow walk bottoms out at 0.2, so a probe drifting
    # around the alarm point would otherwise chatter alarm/clear every tick. Stalled
    # airflow is the leading indicator of a dead CRAH fan or a blocked floor tile, so
    # it must clear cleanly enough to be trusted.
    _rule("SensorLowAirflowCleared",
          _threshold("airflow", ">", 0.5),
          "1.3.6.1.4.1.99999.1.36",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"],
          recovery=True, recovery_of="SensorLowAirflow"),

    _rule("SensorAirflowNormal",
          _composite(
              _threshold("airflow", ">=", 0.3),
              _threshold("airflow", "<=", 3.5),
              logic="AND",
          ),
          "1.3.6.1.4.1.99999.1.19",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["APC NetBotz 355", "APC NetBotz 250"],
          recovery=True, recovery_of="SensorHighAirflow"),

    # ── UPS extended traps ────────────────────────────────────────────────────

    _rule("UPSBatteryLowHealth",
          _threshold("ups_battery_health", "<", 50.0),
          "1.3.6.1.4.1.99999.2.14",
          severity="major", priority=180,
          device_types=["ups"]),

    _rule("UPSBatteryHealthRestored",
          _threshold("ups_battery_health", ">", 70.0),
          "1.3.6.1.4.1.99999.2.15",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBatteryLowHealth"),

    _rule("UPSBypassActive",
          _state_change("ups_bypass_status", "off", "on"),
          "1.3.6.1.4.1.99999.2.16",
          severity="major", priority=190,
          device_types=["ups"]),

    _rule("UPSBypassCleared",
          _state_change("ups_bypass_status", "on", "off"),
          "1.3.6.1.4.1.99999.2.17",
          severity="informational", priority=100,
          device_types=["ups"],
          recovery=True, recovery_of="UPSBypassActive"),

    # ── PDU environment traps ─────────────────────────────────────────────────

    _rule("PDUFrequencyFault",
          _threshold("pdu_frequency", "<", 49.5),
          "1.3.6.1.4.1.99999.6.14",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUFrequencyNormal",
          _threshold("pdu_frequency", ">", 49.5),
          "1.3.6.1.4.1.99999.6.15",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUFrequencyFault"),

    _rule("PDUTempHigh",
          _threshold("pdu_temperature", ">", 35.0),
          "1.3.6.1.4.1.99999.6.16",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUTempNormal",
          _threshold("pdu_temperature", "<", 30.0),
          "1.3.6.1.4.1.99999.6.17",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUTempHigh"),

    _rule("PDUHumidityHigh",
          _threshold("pdu_humidity", ">", 70.0),
          "1.3.6.1.4.1.99999.6.18",
          severity="major", priority=175,
          device_types=["pdu", "floor_pdu"]),

    _rule("PDUHumidityNormal",
          _threshold("pdu_humidity", "<", 60.0),
          "1.3.6.1.4.1.99999.6.19",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDUHumidityHigh"),

    # ── Sensor mid/outlet temp traps ──────────────────────────────────────────

    _rule("SensorMidTempHigh",
          _threshold("mid_temp", ">", 38.0),
          "1.3.6.1.4.1.99999.1.9",
          severity="major", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"]),

    _rule("SensorMidTempNormal",
          _threshold("mid_temp", "<", 35.0),
          "1.3.6.1.4.1.99999.1.11",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"],
          recovery=True, recovery_of="SensorMidTempHigh"),

    _rule("SensorOutletTempHigh",
          _threshold("outlet_temp", ">", 45.0),
          "1.3.6.1.4.1.99999.1.10",
          severity="major", priority=180,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"]),

    _rule("SensorOutletTempNormal",
          _threshold("outlet_temp", "<", 42.0),
          "1.3.6.1.4.1.99999.1.12",
          severity="informational", priority=100,
          device_types=["sensor"],
          model_names=["Raritan DPX2-T3H1"],
          recovery=True, recovery_of="SensorOutletTempHigh"),

    # ── PDU load recovery (pairs with the Inject Fault "PDU Load High" ramp) ──
    _rule("PDULoadNormal",
          _threshold("pdu_load", "<", 70.0),
          "1.3.6.1.4.1.99999.6.20",
          severity="informational", priority=100,
          device_types=["pdu", "floor_pdu"],
          recovery=True, recovery_of="PDULoadHigh"),

    # ── Facility electrical gear (subtree .3) ────────────────────────────────
    # Everything from the service entrance down to the mechanical panelboards is
    # metered by the sim, but until now only the UPS and the rack PDUs annunciated
    # — so a fleet that grew until the MCC or a hall's mechanical panel was near
    # its rating produced the physics with no alarm attached. These are the load
    # and health rules for the rest of the chain.
    #
    # Thresholds follow NEC 210.20(A)/215.3 practice: a bus feeding a continuous
    # load is planned to 80 % of rating, so 85 % is the "you are out of planning
    # headroom" alarm and 95 % is "you are about to trip a main". Clears sit
    # inside the alarm points so a board riding a threshold cannot chatter.

    _rule("SwitchgearBusOverload",
          _threshold("elec_load_pct", ">", 85.0),
          "1.3.6.1.4.1.99999.3.1",
          severity="major", priority=190,
          device_types=["switchgear"]),

    _rule("SwitchgearBusOverloadCritical",
          _threshold("elec_load_pct", ">", 95.0),
          "1.3.6.1.4.1.99999.3.2",
          severity="critical", priority=195,
          device_types=["switchgear"]),

    _rule("SwitchgearBusOverloadCriticalCleared",
          _threshold("elec_load_pct", "<", 92.0),
          "1.3.6.1.4.1.99999.3.3",
          severity="informational", priority=100,
          device_types=["switchgear"],
          recovery=True, recovery_of="SwitchgearBusOverloadCritical"),

    _rule("SwitchgearBusLoadNormal",
          _threshold("elec_load_pct", "<", 80.0),
          "1.3.6.1.4.1.99999.3.4",
          severity="informational", priority=100,
          device_types=["switchgear"],
          recovery=True, recovery_of="SwitchgearBusOverload"),

    # A dead LV main is the loudest thing a switchboard can say. State-change, not
    # threshold: the bus is energized or it is not.
    _rule("SwitchgearBusDead",
          _state_change("elec_status", "energized", "dead"),
          "1.3.6.1.4.1.99999.3.5",
          severity="critical", priority=205,
          device_types=["switchgear"]),

    _rule("SwitchgearBusEnergized",
          _state_change("elec_status", "dead", "energized"),
          "1.3.6.1.4.1.99999.3.6",
          severity="informational", priority=100,
          device_types=["switchgear"],
          recovery=True, recovery_of="SwitchgearBusDead"),

    _rule("SwitchgearBusFault",
          _state_change("elec_status", None, "fault"),
          "1.3.6.1.4.1.99999.3.7",
          severity="critical", priority=210,
          device_types=["switchgear"]),

    _rule("SwitchgearBusFaultCleared",
          _state_change("elec_status", "fault", "energized"),
          "1.3.6.1.4.1.99999.3.8",
          severity="informational", priority=100,
          device_types=["switchgear"],
          recovery=True, recovery_of="SwitchgearBusFault"),

    # MCC — the mechanical bus. It carries the whole cooling plant, so its loading
    # is the direct electrical shadow of the IT load: grow the fleet, the plant
    # ramps, this climbs. An MCC main-tie-main is rated for both buses, hence the
    # same 85/95 ladder as the switchgear.
    _rule("MCCOverload",
          _threshold("elec_load_pct", ">", 85.0),
          "1.3.6.1.4.1.99999.3.10",
          severity="major", priority=190,
          device_types=["mcc"]),

    _rule("MCCOverloadCritical",
          _threshold("elec_load_pct", ">", 95.0),
          "1.3.6.1.4.1.99999.3.11",
          severity="critical", priority=195,
          device_types=["mcc"]),

    _rule("MCCOverloadCriticalCleared",
          _threshold("elec_load_pct", "<", 92.0),
          "1.3.6.1.4.1.99999.3.12",
          severity="informational", priority=100,
          device_types=["mcc"],
          recovery=True, recovery_of="MCCOverloadCritical"),

    _rule("MCCLoadNormal",
          _threshold("elec_load_pct", "<", 80.0),
          "1.3.6.1.4.1.99999.3.13",
          severity="informational", priority=100,
          device_types=["mcc"],
          recovery=True, recovery_of="MCCOverload"),

    # A dead MCC is a cooling outage in waiting — the chilled-water loop's thermal
    # mass buys about a minute, then the room starts warming.
    _rule("MCCBusDead",
          _state_change("elec_status", "energized", "dead"),
          "1.3.6.1.4.1.99999.3.14",
          severity="critical", priority=205,
          device_types=["mcc"]),

    _rule("MCCBusEnergized",
          _state_change("elec_status", "dead", "energized"),
          "1.3.6.1.4.1.99999.3.15",
          severity="informational", priority=100,
          device_types=["mcc"],
          recovery=True, recovery_of="MCCBusDead"),

    # MPP — a hall's mechanical panelboard, feeding that hall's CRAH fans. Fan
    # power is cube-law, so this board loads up far faster than linearly once a
    # hall gets hot and its fans ramp: it is the earliest electrical warning that
    # a hall's air side is working harder than it should.
    _rule("MPPOverload",
          _threshold("elec_load_pct", ">", 85.0),
          "1.3.6.1.4.1.99999.3.20",
          severity="major", priority=185,
          device_types=["mpp"]),

    _rule("MPPOverloadCritical",
          _threshold("elec_load_pct", ">", 95.0),
          "1.3.6.1.4.1.99999.3.21",
          severity="critical", priority=190,
          device_types=["mpp"]),

    _rule("MPPOverloadCriticalCleared",
          _threshold("elec_load_pct", "<", 92.0),
          "1.3.6.1.4.1.99999.3.22",
          severity="informational", priority=100,
          device_types=["mpp"],
          recovery=True, recovery_of="MPPOverloadCritical"),

    _rule("MPPLoadNormal",
          _threshold("elec_load_pct", "<", 80.0),
          "1.3.6.1.4.1.99999.3.23",
          severity="informational", priority=100,
          device_types=["mpp"],
          recovery=True, recovery_of="MPPOverload"),

    _rule("MPPBusDead",
          _state_change("elec_status", "energized", "dead"),
          "1.3.6.1.4.1.99999.3.24",
          severity="major", priority=195,
          device_types=["mpp"]),

    _rule("MPPBusEnergized",
          _state_change("elec_status", "dead", "energized"),
          "1.3.6.1.4.1.99999.3.25",
          severity="informational", priority=100,
          device_types=["mpp"],
          recovery=True, recovery_of="MPPBusDead"),

    # Generator. A standby set is rated for its standby duty and is NOT meant to
    # run near it — 90 % is the alarm every genset controller (DSE, ComAp, Woodward)
    # ships with, because beyond it the governor loses the headroom to absorb the
    # next block load. Held for 30 s so the transient of an ATS block-load pickup
    # does not annunciate — a genset legitimately overshoots on load acceptance.
    _rule("GeneratorOverload",
          _threshold("elec_load_pct", ">", 90.0, duration=30.0),
          "1.3.6.1.4.1.99999.3.30",
          severity="critical", priority=200,
          device_types=["generator"]),

    _rule("GeneratorLoadNormal",
          _threshold("elec_load_pct", "<", 80.0),
          "1.3.6.1.4.1.99999.3.31",
          severity="informational", priority=100,
          device_types=["generator"],
          recovery=True, recovery_of="GeneratorOverload"),

    _rule("GeneratorFailedToStart",
          _state_change("elec_status", None, "fault"),
          "1.3.6.1.4.1.99999.3.32",
          severity="critical", priority=215,
          device_types=["generator"]),

    _rule("GeneratorRecovered",
          _state_change("elec_status", "fault", "standby"),
          "1.3.6.1.4.1.99999.3.33",
          severity="informational", priority=100,
          device_types=["generator"],
          recovery=True, recovery_of="GeneratorFailedToStart"),

    # ── Chiller-plant water loops (subtree .4) ───────────────────────────────
    # The header instruments. These are the points a plant operator actually
    # watches, and every one of them moves with IT load (see
    # core/device_state_store._compute_chw_loop).

    # CHILLED-WATER SUPPLY is the controlled variable — the plant modulates to hold
    # ~7 °C. So supply off setpoint means the plant has run out of capacity, and it
    # is the single most diagnostic high-load symptom in a chiller plant. Two
    # degrees is the usual control deadband; six means the room is already losing.
    _rule("CHWSupplyTempHigh",
          _threshold("water_temp", ">", 9.0, duration=180.0),
          "1.3.6.1.4.1.99999.4.1",
          severity="major", priority=195,
          device_types=["sensor"], model_names=_M_CHW_SUPPLY),

    _rule("CHWSupplyTempCritical",
          _threshold("water_temp", ">", 13.0, duration=120.0),
          "1.3.6.1.4.1.99999.4.2",
          severity="critical", priority=200,
          device_types=["sensor"], model_names=_M_CHW_SUPPLY),

    _rule("CHWSupplyTempCriticalCleared",
          _threshold("water_temp", "<", 11.0),
          "1.3.6.1.4.1.99999.4.3",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CHW_SUPPLY,
          recovery=True, recovery_of="CHWSupplyTempCritical"),

    _rule("CHWSupplyTempNormal",
          _threshold("water_temp", "<", 8.0),
          "1.3.6.1.4.1.99999.4.4",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CHW_SUPPLY,
          recovery=True, recovery_of="CHWSupplyTempHigh"),

    # CHILLED-WATER RETURN carries the room's heat back. It rides ~5 K above supply
    # by design, so an alarm here means either the supply has drifted up (already
    # alarmed above) or the loop ΔT has opened past design — the coils are pulling
    # more heat than the loop was sized for.
    _rule("CHWReturnTempHigh",
          _threshold("water_temp", ">", 16.0, duration=180.0),
          "1.3.6.1.4.1.99999.4.5",
          severity="major", priority=185,
          device_types=["sensor"], model_names=_M_CHW_RETURN),

    _rule("CHWReturnTempNormal",
          _threshold("water_temp", "<", 14.0),
          "1.3.6.1.4.1.99999.4.6",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CHW_RETURN,
          recovery=True, recovery_of="CHWReturnTempHigh"),

    # CONDENSER-WATER SUPPLY is what the tower bank can hold: wet bulb plus the
    # approach. It legitimately runs in the high 20s / low 30s on a hot, humid
    # afternoon, so the alarm sits where the chillers start unloading on head
    # pressure (see _COND_LIMIT_C in the store) rather than at any room-air number.
    _rule("CWSupplyTempHigh",
          _threshold("water_temp", ">", 36.0, duration=120.0),
          "1.3.6.1.4.1.99999.4.10",
          severity="major", priority=190,
          device_types=["sensor"], model_names=_M_CW_SUPPLY),

    _rule("CWSupplyTempCritical",
          _threshold("water_temp", ">", 41.0, duration=60.0),
          "1.3.6.1.4.1.99999.4.11",
          severity="critical", priority=200,
          device_types=["sensor"], model_names=_M_CW_SUPPLY),

    _rule("CWSupplyTempCriticalCleared",
          _threshold("water_temp", "<", 39.0),
          "1.3.6.1.4.1.99999.4.12",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CW_SUPPLY,
          recovery=True, recovery_of="CWSupplyTempCritical"),

    _rule("CWSupplyTempNormal",
          _threshold("water_temp", "<", 34.0),
          "1.3.6.1.4.1.99999.4.13",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CW_SUPPLY,
          recovery=True, recovery_of="CWSupplyTempHigh"),

    # CONDENSER-WATER RETURN runs a design range (~5 K) above supply.
    _rule("CWReturnTempHigh",
          _threshold("water_temp", ">", 41.0, duration=120.0),
          "1.3.6.1.4.1.99999.4.14",
          severity="major", priority=185,
          device_types=["sensor"], model_names=_M_CW_RETURN),

    _rule("CWReturnTempNormal",
          _threshold("water_temp", "<", 39.0),
          "1.3.6.1.4.1.99999.4.15",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CW_RETURN,
          recovery=True, recovery_of="CWReturnTempHigh"),

    # TOWER BASIN is the cold well the condenser pumps draw from — the same water
    # the CW supply header carries, so it alarms on the same limit.
    _rule("CTBasinTempHigh",
          _threshold("water_temp", ">", 36.0, duration=120.0),
          "1.3.6.1.4.1.99999.4.20",
          severity="major", priority=185,
          device_types=["sensor"], model_names=_M_CT_BASIN),

    _rule("CTBasinTempNormal",
          _threshold("water_temp", "<", 34.0),
          "1.3.6.1.4.1.99999.4.21",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CT_BASIN,
          recovery=True, recovery_of="CTBasinTempHigh"),

    # CHILLED-WATER FLOW. A magnetic flow meter on the main, and a LOW alarm only:
    # high flow is not a fault, it is a busy plant.
    #
    # Deliberately NOT a percent-of-design alarm. Loop flow legitimately falls to
    # the minimum-flow bypass whenever the plant is lightly loaded, so any threshold
    # set proportional to load would cry wolf on a quiet night — and one set to an
    # absolute design fraction would go stale the moment the fleet grows. What this
    # models is the evaporator FLOW SWITCH every chiller is interlocked to: it trips
    # on LOSS of flow, near zero, which is unambiguous at any plant size. Loss of
    # flow is a fault (dead pump, closed valve, air-bound loop), not a load symptom
    # — the load symptom is CHW supply drifting off setpoint, alarmed above.
    _rule("CHWFlowLoss",
          _threshold("water_flow_lps", "<", 1.0, duration=60.0),
          "1.3.6.1.4.1.99999.4.30",
          severity="critical", priority=200,
          device_types=["sensor"], model_names=_M_CHW_FLOW),

    _rule("CHWFlowRestored",
          _threshold("water_flow_lps", ">", 2.0),
          "1.3.6.1.4.1.99999.4.31",
          severity="informational", priority=100,
          device_types=["sensor"], model_names=_M_CHW_FLOW,
          recovery=True, recovery_of="CHWFlowLoss"),

    # ── COOLING-PLANT MACHINES ────────────────────────────────────────────────
    # Chillers, towers, pumps, valves and CRAHs publish live SNMP OIDs but carried
    # no trap rules at all: sensors had 35, PDUs 30, UPSes 28, and every plant
    # device zero. A fault campaign that latched three chillers out on high head
    # pressure produced no chiller trap, and stopping seven CRAHs fired nothing.
    #
    # BACnet COV is the realistic PRIMARY path for plant alarms and it already
    # works — present values change and notifications dispatch. This is the SNMP
    # plane, which the simulator otherwise populates for these same machines, and
    # which is what an NMS (rather than a BMS) would be watching.
    #
    # Written against plant_status, the machine's worst active condition, because
    # the underlying health is a set of BACnet binaries and a trap needs one
    # answer. Mirrors core.device_state_store._PLANT_STATUS_ORDER.
    *[
        r
        for name, status, oid_hi, oid_lo, sev, dtypes in (
            # Chiller safeties. A latched high-pressure trip is the most serious
            # thing a chiller can report: it is off, and it stays off until someone
            # resets it.
            ("ChillerHighPressure", "hp_trip",      40, 41, "critical", ["chiller"]),
            # Evaporator flow switch — the interlock that stops the machine before
            # a frozen barrel splits its tubes.
            ("ChillerFlowLoss",     "flow_loss",    42, 43, "critical",
             ["chiller", "pump"]),
            ("ChillerLowEvapTemp",  "low_evap_temp", 44, 45, "major", ["chiller"]),
            # Tower mechanicals. Vibration is the classic bearing/imbalance fault;
            # a low basin risks losing pump suction entirely.
            ("TowerHighVibration",  "vibration",    46, 47, "major",
             ["cooling_tower"]),
            ("TowerLowBasin",       "low_basin",    48, 49, "major",
             ["cooling_tower"]),
            # Pumps. A unit fault and a low-flow alarm mean different things — a
            # dead motor versus a running pump that is not moving water — so they
            # are annunciated apart.
            ("PumpFault",           "unit_fault",   50, 51, "major", ["pump"]),
            ("PumpLowFlow",         "low_flow",     52, 53, "major", ["pump"]),
            # Header valve actuator: not staged and not redundant, so it throttles
            # every train downstream of it at once.
            ("ValveActuatorFault",  "actuator_fault", 54, 55, "critical", ["valve"]),
            # CRAH. Airflow loss is a total loss for that unit even though its coil
            # is still cold — what the room needs is MOVED air. A dirty filter is
            # partial: still cold, about a fifth less of it.
            ("CRAHAirflowLoss",     "airflow_loss", 56, 57, "major", ["crah"]),
            ("CRAHHighTemp",        "high_temp",    58, 59, "major", ["crah", "cdu"]),
            ("CRAHFilterDirty",     "filter_dirty", 60, 61, "minor", ["crah"]),
            # Any commanded machine reporting stopped. Distinct from an alarm: an
            # alarm fails over in one tick, a silent stop is what the run-status
            # proof timer exists to catch.
            ("PlantUnitStopped",    "stopped",      62, 63, "major",
             ["chiller", "cooling_tower", "pump", "valve", "crah", "cdu"]),
        )
        for r in (
            _rule(name, _state_change("plant_status", None, status),
                  f"1.3.6.1.4.1.99999.4.{oid_hi}",
                  severity=sev, priority=195, device_types=dtypes),
            _rule(f"{name}Cleared", _state_change("plant_status", status, "ok"),
                  f"1.3.6.1.4.1.99999.4.{oid_lo}",
                  severity="informational", priority=100, device_types=dtypes,
                  recovery=True, recovery_of=name),
        )
    ],
]


# Continuous alert rules (threshold/composite, non-recovery) re-notify at most
# once per window instead of firing every tick while the condition holds: one
# trap on breach, a reminder every _ALERT_COOLDOWN, then a recovery trap when it
# clears. Recovery, state-change and temporal rules are exempt — they must fire
# on the exact transition.
_ALERT_COOLDOWN = 300.0
for _r in DEFAULT_RULES:
    if (not _r.is_recovery
            and _r.condition.condition_type in ("threshold", "composite")):
        _r.cooldown_sec = _ALERT_COOLDOWN


# ── JSON serialization ────────────────────────────────────────────────────────

def rules_to_json(rules: List[Rule], indent: int = 2) -> str:
    return json.dumps([r.to_dict() for r in rules], indent=indent)


def rules_from_json(text: str) -> List[Rule]:
    data = json.loads(text)
    return [Rule.from_dict(d) for d in data]


def save_rules(rules: List[Rule], path: str | Path):
    Path(path).write_text(rules_to_json(rules), encoding="utf-8")
    log.info("[TrapRules] Saved %d rules to %s", len(rules), path)


def load_rules(path: str | Path) -> List[Rule]:
    text = Path(path).read_text(encoding="utf-8")
    rules = rules_from_json(text)
    log.info("[TrapRules] Loaded %d rules from %s", len(rules), path)
    return rules
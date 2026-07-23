"""
SNMP Trap Definitions — OIDs, severity levels, and applicable device types.

Enterprise OID tree: 1.3.6.1.4.1.99999
  .1.1-.1.8   general device traps (CPU, memory, temperature, ...)
  .2.1-.2.13  UPS enterprise traps
  .4.1-.4.10  UPS pollable status OIDs
  .5.1-.5.17  PDU pollable status OIDs
  .3.1-.3.7   Generator enterprise traps
  .6.1-.6.13  PDU enterprise traps
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class TrapType(str, Enum):
    # SNMPv2-MIB standard traps
    COLD_START        = "coldStart"
    WARM_START        = "warmStart"
    LINK_DOWN         = "linkDown"
    LINK_UP           = "linkUp"
    AUTH_FAILURE      = "authenticationFailure"
    # Routing protocol traps
    BGP_DOWN          = "bgpSessionDown"
    BGP_UP            = "bgpEstablished"
# UPS-MIB power traps
    UPS_ON_BATTERY    = "upsOnBattery"
    UPS_LOW_BATTERY   = "upsLowBattery"
    # Enterprise resource traps (1.3.6.1.4.1.99999)
    CPU_HIGH          = "cpuHighUsage"
    MEMORY_HIGH       = "memoryHighUsage"
    TEMPERATURE_ALERT = "temperatureAlert"
    LINK_FLAP         = "linkFlap"
    RACK_FAILURE      = "rackFailure"
    # Enterprise resource recovery / clear traps (1.3.6.1.4.1.99999.1.13–1.15).
    # Distinct OIDs so a receiver shows them as their own clear events instead of
    # decoding the generic linkUp OID (1.3.6.1.6.3.1.1.5.4) as "linkUp".
    CPU_NORMAL         = "cpuNormal"
    MEMORY_NORMAL      = "memoryNormal"
    TEMPERATURE_NORMAL = "temperatureNormal"
    # Sensor recovery / clear traps (1.3.6.1.4.1.99999.1.16–1.19) — distinct OIDs
    # so they don't decode as the generic linkUp OID.
    SENSOR_AMBIENT_TEMP_NORMAL = "sensorAmbientTempNormal"
    SENSOR_HUMIDITY_NORMAL     = "sensorHumidityNormal"
    SENSOR_DEWPOINT_NORMAL     = "sensorDewPointNormal"
    SENSOR_AIRFLOW_NORMAL      = "sensorAirflowNormal"
    # Distinct severity/variant alert traps (1.3.6.1.4.1.99999.1.20–1.28) so each
    # variant decodes as its own event instead of sharing the family alert OID.
    CPU_SUSTAINED                = "cpuSustained"
    CPU_TEMP_CRITICAL            = "cpuTempCritical"
    SENSOR_AMBIENT_TEMP_HIGH     = "sensorAmbientTempHigh"
    SENSOR_AMBIENT_TEMP_CRITICAL = "sensorAmbientTempCritical"
    SENSOR_HIGH_HUMIDITY         = "sensorHighHumidity"
    SENSOR_CRITICAL_HUMIDITY     = "sensorCriticalHumidity"
    SENSOR_LOW_HUMIDITY          = "sensorLowHumidity"
    SENSOR_HIGH_AIRFLOW          = "sensorHighAirflow"
    SENSOR_LOW_AIRFLOW           = "sensorLowAirflow"
    # Environmental sensor traps
    HUMIDITY_ALERT    = "humidityAlert"
    DEWPOINT_ALERT    = "dewPointAlert"
    AIRFLOW_ALERT     = "airflowAlert"
    # UPS enterprise traps (1.3.6.1.4.1.99999.2.x)
    UPS_BATTERY_NORMAL        = "batteryNormal"
    UPS_UTILITY_RESTORED      = "utilityPowerRestored"
    UPS_OUTPUT_OVERLOAD       = "outputOverload"
    UPS_OUTPUT_NORMAL         = "outputNormal"
    UPS_FAN_FAILURE           = "fanFailure"
    UPS_BATTERY_FAILURE       = "batteryFailure"
    UPS_BATTERY_DISCONNECTED  = "batteryDisconnected"
    UPS_CHARGER_FAILURE       = "chargerFailure"
    UPS_INPUT_VOLTAGE_HIGH    = "inputVoltageHigh"
    UPS_INPUT_VOLTAGE_LOW     = "inputVoltageLow"
    UPS_FREQUENCY_OUT_RANGE   = "frequencyOutOfRange"
    UPS_RECTIFIER_FAILURE     = "rectifierFailure"
    UPS_PHASE_FAILURE         = "upsPhaseFailure"
    # PDU enterprise traps (1.3.6.1.4.1.99999.6.x)
    PDU_OUTLET_ON             = "outletOn"
    PDU_OUTLET_OFF            = "outletOff"
    PDU_BREAKER_TRIPPED       = "breakerTripped"
    PDU_LOAD_HIGH             = "loadHigh"
    PDU_LOAD_CRITICAL         = "loadCritical"
    PDU_VOLTAGE_HIGH          = "voltageHigh"
    PDU_VOLTAGE_LOW           = "voltageLow"
    PDU_PHASE_IMBALANCE       = "phaseImbalance"
    PDU_POWER_FACTOR_LOW      = "powerFactorLow"
    PDU_OUTLET_FAILURE        = "outletFailure"
    PDU_SMOKE_DETECTED        = "smokeDetected"
    PDU_OUTLET_CURRENT_HIGH   = "outletCurrentHigh"
    PDU_GROUND_FAULT          = "groundFault"
    # PDU environment traps (1.3.6.1.4.1.99999.6.14–6.19)
    PDU_FREQUENCY_FAULT       = "pduFrequencyFault"
    PDU_FREQUENCY_NORMAL      = "pduFrequencyNormal"
    PDU_TEMP_HIGH             = "pduTempHigh"
    PDU_TEMP_NORMAL           = "pduTempNormal"
    PDU_HUMIDITY_HIGH         = "pduHumidityHigh"
    PDU_HUMIDITY_NORMAL       = "pduHumidityNormal"
    PDU_LOAD_NORMAL           = "pduLoadNormal"   # recovery for PDULoadHigh (99999.6.20)
    # UPS extended traps (1.3.6.1.4.1.99999.2.14–2.17)
    UPS_BATTERY_LOW_HEALTH    = "batteryLowHealth"
    UPS_BATTERY_HEALTH_RESTORED = "batteryHealthRestored"
    UPS_BYPASS_ACTIVE         = "bypassActive"
    UPS_BYPASS_CLEARED        = "bypassCleared"
    # UPS input-voltage alarm clears (1.3.6.1.4.1.99999.2.18–2.19)
    UPS_INPUT_VOLTAGE_NORMAL  = "inputVoltageNormal"
    UPS_INPUT_VOLTAGE_LOW_CLEARED = "inputVoltageLowCleared"
    # Sensor mid/outlet temp traps (1.3.6.1.4.1.99999.1.9–1.12)
    SENSOR_MID_TEMP_HIGH      = "sensorMidTempHigh"
    SENSOR_OUTLET_TEMP_HIGH   = "sensorOutletTempHigh"
    SENSOR_MID_TEMP_NORMAL    = "sensorMidTempNormal"
    SENSOR_OUTLET_TEMP_NORMAL = "sensorOutletTempNormal"
    # Server BMC platform-event traps (1.3.6.1.4.1.99999.26.0.x) — sent by
    # the BMC on the mgmt IP; fire even while the chassis is powered off.
    SERVER_POWER_OFF      = "serverPowerOff"
    SERVER_POWER_ON       = "serverPowerOn"
    # Generator enterprise traps (1.3.6.1.4.1.99999.3.x)
    GEN_RUNNING           = "generatorRunning"
    GEN_STOPPED           = "generatorStopped"
    GEN_LOW_FUEL          = "generatorLowFuel"
    GEN_LOW_COOLANT       = "generatorLowCoolant"
    GEN_BATTERY_FAILURE   = "generatorBatteryFailure"
    GEN_TRANSFER_SWITCH   = "generatorTransferSwitch"
    GEN_OVERCRANK         = "generatorOvercrank"
    # ATS enterprise traps (1.3.6.1.4.1.99999.13.x) — ASCO 7000 ACC / Eaton ATC-900.
    # An automatic transfer switch is the one electrical-upstream device that
    # natively speaks SNMP (source-loss, transfer, fail-to-transfer, not-in-auto).
    ATS_TRANSFER_EMERGENCY = "atsTransferEmergency"
    ATS_TRANSFER_NORMAL    = "atsTransferNormal"
    ATS_SOURCE_LOST        = "atsSourceLost"
    ATS_FAIL_TO_TRANSFER   = "atsFailToTransfer"
    ATS_NOT_IN_AUTO        = "atsNotInAuto"
    ATS_ENGINE_START       = "atsEngineStart"
    # Condition clears — a transfer switch asserts an alarm point and later clears
    # it. These are the paired recoveries for the two latching ATS conditions.
    ATS_RETURNED_TO_AUTO       = "atsReturnedToAuto"
    ATS_TRANSFER_FAULT_CLEARED = "atsTransferFaultCleared"


SEVERITY_COLOR = {
    "informational": "#2ecc71",
    "minor":         "#f39c12",
    "major":         "#e67e22",
    "critical":      "#e74c3c",
}


@dataclass(frozen=True)
class TrapDefinition:
    trap_type:    TrapType
    oid:          str
    display_name: str
    description:  str
    severity:     str   # informational | minor | major | critical


TRAP_DEFINITIONS: dict[TrapType, TrapDefinition] = {
    TrapType.COLD_START: TrapDefinition(
        TrapType.COLD_START,
        "1.3.6.1.6.3.1.1.5.1",
        "Cold Start",
        "Device has restarted from a power cycle",
        "informational",
    ),
    TrapType.WARM_START: TrapDefinition(
        TrapType.WARM_START,
        "1.3.6.1.6.3.1.1.5.2",
        "Warm Start",
        "Device has restarted without a power cycle",
        "informational",
    ),
    TrapType.LINK_DOWN: TrapDefinition(
        TrapType.LINK_DOWN,
        "1.3.6.1.6.3.1.1.5.3",
        "Link Down",
        "A network interface has gone operationally down",
        "major",
    ),
    TrapType.LINK_UP: TrapDefinition(
        TrapType.LINK_UP,
        "1.3.6.1.6.3.1.1.5.4",
        "Link Up",
        "A network interface has come operationally up",
        "informational",
    ),
    TrapType.AUTH_FAILURE: TrapDefinition(
        TrapType.AUTH_FAILURE,
        "1.3.6.1.6.3.1.1.5.5",
        "Authentication Failure",
        "SNMP request received with incorrect community string",
        "major",
    ),
    TrapType.BGP_DOWN: TrapDefinition(
        TrapType.BGP_DOWN,
        "1.3.6.1.2.1.15.0.2",
        "BGP Session Down",
        "A BGP peer session has transitioned to Idle/Active",
        "critical",
    ),
    # bgpEstablished. Defined so the recovery carries the SAME bgpPeerIdentifier /
    # bgpPeerState varbinds as the backward transition — an NMS with BGP4-MIB loaded
    # correlates the pair on the peer address, which an untyped raw trap would not
    # carry.
    TrapType.BGP_UP: TrapDefinition(
        TrapType.BGP_UP,
        "1.3.6.1.2.1.15.0.1",
        "BGP Session Established",
        "A BGP peer session has reached the Established state",
        "informational",
    ),
TrapType.UPS_ON_BATTERY: TrapDefinition(
        TrapType.UPS_ON_BATTERY,
        "1.3.6.1.2.1.33.2.0.1",
        "UPS On Battery",
        "UPS has switched to battery power",
        "critical",
    ),
    TrapType.UPS_LOW_BATTERY: TrapDefinition(
        TrapType.UPS_LOW_BATTERY,
        "1.3.6.1.2.1.33.2.0.2",
        "UPS Low Battery",
        "UPS battery level is critically low",
        "critical",
    ),
    TrapType.CPU_HIGH: TrapDefinition(
        TrapType.CPU_HIGH,
        "1.3.6.1.4.1.99999.1.1",
        "CPU High Usage",
        "CPU utilisation has exceeded 90 %",
        "major",
    ),
    TrapType.MEMORY_HIGH: TrapDefinition(
        TrapType.MEMORY_HIGH,
        "1.3.6.1.4.1.99999.1.2",
        "Memory High Usage",
        "Memory utilisation has exceeded 85 %",
        "major",
    ),
    TrapType.TEMPERATURE_ALERT: TrapDefinition(
        TrapType.TEMPERATURE_ALERT,
        "1.3.6.1.4.1.99999.1.3",
        "Temperature Alert",
        "Device chassis temperature has exceeded safe threshold",
        "critical",
    ),
    TrapType.CPU_NORMAL: TrapDefinition(
        TrapType.CPU_NORMAL,
        "1.3.6.1.4.1.99999.1.13",
        "CPU Normal",
        "CPU utilisation has returned below threshold",
        "informational",
    ),
    TrapType.MEMORY_NORMAL: TrapDefinition(
        TrapType.MEMORY_NORMAL,
        "1.3.6.1.4.1.99999.1.14",
        "Memory Normal",
        "Memory utilisation has returned below threshold",
        "informational",
    ),
    TrapType.TEMPERATURE_NORMAL: TrapDefinition(
        TrapType.TEMPERATURE_NORMAL,
        "1.3.6.1.4.1.99999.1.15",
        "Temperature Normal",
        "Device temperature has returned to safe range",
        "informational",
    ),
    TrapType.SENSOR_AMBIENT_TEMP_NORMAL: TrapDefinition(
        TrapType.SENSOR_AMBIENT_TEMP_NORMAL,
        "1.3.6.1.4.1.99999.1.16",
        "Sensor Ambient Temp Normal",
        "Ambient temperature has returned to safe range",
        "informational",
    ),
    TrapType.SENSOR_HUMIDITY_NORMAL: TrapDefinition(
        TrapType.SENSOR_HUMIDITY_NORMAL,
        "1.3.6.1.4.1.99999.1.17",
        "Sensor Humidity Normal",
        "Relative humidity has returned to safe range",
        "informational",
    ),
    TrapType.SENSOR_DEWPOINT_NORMAL: TrapDefinition(
        TrapType.SENSOR_DEWPOINT_NORMAL,
        "1.3.6.1.4.1.99999.1.18",
        "Sensor Dew Point Normal",
        "Dew point has returned to safe range",
        "informational",
    ),
    TrapType.SENSOR_AIRFLOW_NORMAL: TrapDefinition(
        TrapType.SENSOR_AIRFLOW_NORMAL,
        "1.3.6.1.4.1.99999.1.19",
        "Sensor Airflow Normal",
        "Airflow has returned to normal range",
        "informational",
    ),
    TrapType.PDU_LOAD_NORMAL: TrapDefinition(
        TrapType.PDU_LOAD_NORMAL,
        "1.3.6.1.4.1.99999.6.20",
        "PDU Load Normal",
        "PDU load has returned below threshold",
        "informational",
    ),
    TrapType.CPU_SUSTAINED: TrapDefinition(
        TrapType.CPU_SUSTAINED,
        "1.3.6.1.4.1.99999.1.20",
        "CPU Sustained High",
        "CPU utilisation has stayed above 90 % for 5 minutes",
        "critical",
    ),
    TrapType.CPU_TEMP_CRITICAL: TrapDefinition(
        TrapType.CPU_TEMP_CRITICAL,
        "1.3.6.1.4.1.99999.1.21",
        "CPU & Temperature Critical",
        "CPU and chassis temperature are both critically high",
        "critical",
    ),
    TrapType.SENSOR_AMBIENT_TEMP_HIGH: TrapDefinition(
        TrapType.SENSOR_AMBIENT_TEMP_HIGH,
        "1.3.6.1.4.1.99999.1.22",
        "Sensor Ambient Temp High",
        "Ambient temperature has exceeded the high threshold",
        "major",
    ),
    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL: TrapDefinition(
        TrapType.SENSOR_AMBIENT_TEMP_CRITICAL,
        "1.3.6.1.4.1.99999.1.23",
        "Sensor Ambient Temp Critical",
        "Ambient temperature has exceeded the critical threshold",
        "critical",
    ),
    TrapType.SENSOR_HIGH_HUMIDITY: TrapDefinition(
        TrapType.SENSOR_HIGH_HUMIDITY,
        "1.3.6.1.4.1.99999.1.24",
        "Sensor High Humidity",
        "Relative humidity has exceeded the high threshold",
        "major",
    ),
    TrapType.SENSOR_CRITICAL_HUMIDITY: TrapDefinition(
        TrapType.SENSOR_CRITICAL_HUMIDITY,
        "1.3.6.1.4.1.99999.1.25",
        "Sensor Critical Humidity",
        "Relative humidity has exceeded the critical threshold",
        "critical",
    ),
    TrapType.SENSOR_LOW_HUMIDITY: TrapDefinition(
        TrapType.SENSOR_LOW_HUMIDITY,
        "1.3.6.1.4.1.99999.1.26",
        "Sensor Low Humidity",
        "Relative humidity has dropped below the low threshold",
        "major",
    ),
    TrapType.SENSOR_HIGH_AIRFLOW: TrapDefinition(
        TrapType.SENSOR_HIGH_AIRFLOW,
        "1.3.6.1.4.1.99999.1.27",
        "Sensor High Airflow",
        "Airflow has exceeded the high threshold",
        "major",
    ),
    TrapType.SENSOR_LOW_AIRFLOW: TrapDefinition(
        TrapType.SENSOR_LOW_AIRFLOW,
        "1.3.6.1.4.1.99999.1.28",
        "Sensor Low Airflow",
        "Airflow has dropped below the low threshold",
        "critical",
    ),
    TrapType.LINK_FLAP: TrapDefinition(
        TrapType.LINK_FLAP,
        "1.3.6.1.4.1.99999.1.4",
        "Link Flap",
        "Interface has flapped more than 3 times in 60 seconds",
        "critical",
    ),
    TrapType.RACK_FAILURE: TrapDefinition(
        TrapType.RACK_FAILURE,
        "1.3.6.1.4.1.99999.1.5",
        "Rack Failure",
        "Three or more devices in the same rack are unreachable",
        "critical",
    ),
    TrapType.HUMIDITY_ALERT: TrapDefinition(
        TrapType.HUMIDITY_ALERT,
        "1.3.6.1.4.1.99999.1.6",
        "Humidity Alert",
        "Sensor relative humidity is outside the safe 30–70% range",
        "major",
    ),
    TrapType.DEWPOINT_ALERT: TrapDefinition(
        TrapType.DEWPOINT_ALERT,
        "1.3.6.1.4.1.99999.1.7",
        "Dew Point Alert",
        "Dew point exceeds condensation risk threshold (21°C)",
        "critical",
    ),
    TrapType.AIRFLOW_ALERT: TrapDefinition(
        TrapType.AIRFLOW_ALERT,
        "1.3.6.1.4.1.99999.1.8",
        "Airflow Alert",
        "Airflow is outside safe operating range (0.3–3.5 m/s)",
        "major",
    ),
    # ── UPS enterprise traps ──────────────────────────────────────────────────
    TrapType.UPS_BATTERY_NORMAL: TrapDefinition(
        TrapType.UPS_BATTERY_NORMAL, "1.3.6.1.4.1.99999.2.1",
        "Battery Normal", "UPS battery has returned to normal state", "informational"),
    TrapType.UPS_UTILITY_RESTORED: TrapDefinition(
        TrapType.UPS_UTILITY_RESTORED, "1.3.6.1.4.1.99999.2.2",
        "Utility Power Restored", "Utility power has been restored; UPS on mains", "informational"),
    TrapType.UPS_OUTPUT_OVERLOAD: TrapDefinition(
        TrapType.UPS_OUTPUT_OVERLOAD, "1.3.6.1.4.1.99999.2.3",
        "Output Overload", "UPS output load has exceeded 90%", "critical"),
    TrapType.UPS_OUTPUT_NORMAL: TrapDefinition(
        TrapType.UPS_OUTPUT_NORMAL, "1.3.6.1.4.1.99999.2.4",
        "Output Normal", "UPS output load has returned to normal", "informational"),
    TrapType.UPS_FAN_FAILURE: TrapDefinition(
        TrapType.UPS_FAN_FAILURE, "1.3.6.1.4.1.99999.2.5",
        "Fan Failure", "UPS cooling fan has failed", "critical"),
    TrapType.UPS_BATTERY_FAILURE: TrapDefinition(
        TrapType.UPS_BATTERY_FAILURE, "1.3.6.1.4.1.99999.2.6",
        "Battery Failure", "UPS battery fault detected", "critical"),
    TrapType.UPS_BATTERY_DISCONNECTED: TrapDefinition(
        TrapType.UPS_BATTERY_DISCONNECTED, "1.3.6.1.4.1.99999.2.7",
        "Battery Disconnected", "UPS battery has been disconnected", "critical"),
    TrapType.UPS_CHARGER_FAILURE: TrapDefinition(
        TrapType.UPS_CHARGER_FAILURE, "1.3.6.1.4.1.99999.2.8",
        "Charger Failure", "UPS battery charger fault", "critical"),
    TrapType.UPS_INPUT_VOLTAGE_HIGH: TrapDefinition(
        TrapType.UPS_INPUT_VOLTAGE_HIGH, "1.3.6.1.4.1.99999.2.9",
        "Input Voltage High", "UPS input voltage exceeds upper threshold (440 V L-L)", "major"),
    TrapType.UPS_INPUT_VOLTAGE_LOW: TrapDefinition(
        TrapType.UPS_INPUT_VOLTAGE_LOW, "1.3.6.1.4.1.99999.2.10",
        "Input Voltage Low", "UPS input voltage below lower threshold (360 V L-L)", "major"),
    TrapType.UPS_FREQUENCY_OUT_RANGE: TrapDefinition(
        TrapType.UPS_FREQUENCY_OUT_RANGE, "1.3.6.1.4.1.99999.2.11",
        "Frequency Out of Range", "UPS input frequency outside 49–51 Hz band", "major"),
    TrapType.UPS_RECTIFIER_FAILURE: TrapDefinition(
        TrapType.UPS_RECTIFIER_FAILURE, "1.3.6.1.4.1.99999.2.12",
        "Rectifier Failure", "UPS rectifier module fault", "critical"),
    TrapType.UPS_PHASE_FAILURE: TrapDefinition(
        TrapType.UPS_PHASE_FAILURE, "1.3.6.1.4.1.99999.2.13",
        "Phase Failure", "One or more input phases has failed", "critical"),
    # ── PDU enterprise traps ──────────────────────────────────────────────────
    TrapType.PDU_OUTLET_ON: TrapDefinition(
        TrapType.PDU_OUTLET_ON, "1.3.6.1.4.1.99999.6.1",
        "Outlet On", "PDU outlet has been switched on", "informational"),
    TrapType.PDU_OUTLET_OFF: TrapDefinition(
        TrapType.PDU_OUTLET_OFF, "1.3.6.1.4.1.99999.6.2",
        "Outlet Off", "PDU outlet has been switched off", "major"),
    TrapType.PDU_BREAKER_TRIPPED: TrapDefinition(
        TrapType.PDU_BREAKER_TRIPPED, "1.3.6.1.4.1.99999.6.3",
        "Breaker Tripped", "PDU circuit breaker has tripped", "critical"),
    TrapType.PDU_LOAD_HIGH: TrapDefinition(
        TrapType.PDU_LOAD_HIGH, "1.3.6.1.4.1.99999.6.4",
        "Load High", "PDU load has exceeded 80%", "major"),
    TrapType.PDU_LOAD_CRITICAL: TrapDefinition(
        TrapType.PDU_LOAD_CRITICAL, "1.3.6.1.4.1.99999.6.5",
        "Load Critical", "PDU load has exceeded 90%", "critical"),
    TrapType.PDU_VOLTAGE_HIGH: TrapDefinition(
        TrapType.PDU_VOLTAGE_HIGH, "1.3.6.1.4.1.99999.6.6",
        "Voltage High", "PDU input voltage exceeds 240 V", "major"),
    TrapType.PDU_VOLTAGE_LOW: TrapDefinition(
        TrapType.PDU_VOLTAGE_LOW, "1.3.6.1.4.1.99999.6.7",
        "Voltage Low", "PDU input voltage below 200 V", "major"),
    TrapType.PDU_PHASE_IMBALANCE: TrapDefinition(
        TrapType.PDU_PHASE_IMBALANCE, "1.3.6.1.4.1.99999.6.8",
        "Phase Imbalance", "PDU phase load imbalance exceeds 20%", "major"),
    TrapType.PDU_POWER_FACTOR_LOW: TrapDefinition(
        TrapType.PDU_POWER_FACTOR_LOW, "1.3.6.1.4.1.99999.6.9",
        "Power Factor Low", "PDU power factor has dropped below 0.70", "major"),
    TrapType.PDU_OUTLET_FAILURE: TrapDefinition(
        TrapType.PDU_OUTLET_FAILURE, "1.3.6.1.4.1.99999.6.10",
        "Outlet Failure", "PDU outlet hardware fault detected", "critical"),
    TrapType.PDU_SMOKE_DETECTED: TrapDefinition(
        TrapType.PDU_SMOKE_DETECTED, "1.3.6.1.4.1.99999.6.11",
        "Smoke Detected", "Smoke sensor has triggered in the PDU", "critical"),
    TrapType.PDU_OUTLET_CURRENT_HIGH: TrapDefinition(
        TrapType.PDU_OUTLET_CURRENT_HIGH, "1.3.6.1.4.1.99999.6.12",
        "Outlet Current High", "PDU outlet current exceeds 20 A", "major"),
    TrapType.PDU_GROUND_FAULT: TrapDefinition(
        TrapType.PDU_GROUND_FAULT, "1.3.6.1.4.1.99999.6.13",
        "Ground Fault", "PDU ground fault detected", "critical"),
    TrapType.PDU_FREQUENCY_FAULT: TrapDefinition(
        TrapType.PDU_FREQUENCY_FAULT, "1.3.6.1.4.1.99999.6.14",
        "PDU Frequency Fault", "PDU input frequency outside 49.5–50.5 Hz band", "major"),
    TrapType.PDU_FREQUENCY_NORMAL: TrapDefinition(
        TrapType.PDU_FREQUENCY_NORMAL, "1.3.6.1.4.1.99999.6.15",
        "PDU Frequency Normal", "PDU input frequency returned to normal range", "informational"),
    TrapType.PDU_TEMP_HIGH: TrapDefinition(
        TrapType.PDU_TEMP_HIGH, "1.3.6.1.4.1.99999.6.16",
        "PDU Temperature High", "PDU ambient temperature has exceeded 35°C", "major"),
    TrapType.PDU_TEMP_NORMAL: TrapDefinition(
        TrapType.PDU_TEMP_NORMAL, "1.3.6.1.4.1.99999.6.17",
        "PDU Temperature Normal", "PDU ambient temperature returned to normal", "informational"),
    TrapType.PDU_HUMIDITY_HIGH: TrapDefinition(
        TrapType.PDU_HUMIDITY_HIGH, "1.3.6.1.4.1.99999.6.18",
        "PDU Humidity High", "PDU ambient humidity has exceeded 70%", "major"),
    TrapType.PDU_HUMIDITY_NORMAL: TrapDefinition(
        TrapType.PDU_HUMIDITY_NORMAL, "1.3.6.1.4.1.99999.6.19",
        "PDU Humidity Normal", "PDU ambient humidity returned to normal", "informational"),
    TrapType.UPS_BATTERY_LOW_HEALTH: TrapDefinition(
        TrapType.UPS_BATTERY_LOW_HEALTH, "1.3.6.1.4.1.99999.2.14",
        "Battery Low Health", "UPS battery state-of-health has fallen below 50%", "major"),
    TrapType.UPS_BATTERY_HEALTH_RESTORED: TrapDefinition(
        TrapType.UPS_BATTERY_HEALTH_RESTORED, "1.3.6.1.4.1.99999.2.15",
        "Battery Health Restored", "UPS battery health has recovered above 70%", "informational"),
    TrapType.UPS_BYPASS_ACTIVE: TrapDefinition(
        TrapType.UPS_BYPASS_ACTIVE, "1.3.6.1.4.1.99999.2.16",
        "Bypass Active", "UPS has switched to bypass mode", "major"),
    TrapType.UPS_BYPASS_CLEARED: TrapDefinition(
        TrapType.UPS_BYPASS_CLEARED, "1.3.6.1.4.1.99999.2.17",
        "Bypass Cleared", "UPS has exited bypass mode", "informational"),
    TrapType.UPS_INPUT_VOLTAGE_NORMAL: TrapDefinition(
        TrapType.UPS_INPUT_VOLTAGE_NORMAL, "1.3.6.1.4.1.99999.2.18",
        "Input Voltage Normal",
        "UPS input voltage returned below the over-voltage reset point (430 V L-L)",
        "informational"),
    TrapType.UPS_INPUT_VOLTAGE_LOW_CLEARED: TrapDefinition(
        TrapType.UPS_INPUT_VOLTAGE_LOW_CLEARED, "1.3.6.1.4.1.99999.2.19",
        "Input Voltage Low Cleared",
        "UPS input voltage returned above the under-voltage reset point (370 V L-L)",
        "informational"),
    TrapType.SENSOR_MID_TEMP_HIGH: TrapDefinition(
        TrapType.SENSOR_MID_TEMP_HIGH, "1.3.6.1.4.1.99999.1.9",
        "Mid-Rack Temp High", "Mid-rack temperature probe has exceeded 38°C", "major"),
    TrapType.SENSOR_OUTLET_TEMP_HIGH: TrapDefinition(
        TrapType.SENSOR_OUTLET_TEMP_HIGH, "1.3.6.1.4.1.99999.1.10",
        "Exhaust Temp High", "Rack exhaust temperature probe has exceeded 45°C", "major"),
    TrapType.SENSOR_MID_TEMP_NORMAL: TrapDefinition(
        TrapType.SENSOR_MID_TEMP_NORMAL, "1.3.6.1.4.1.99999.1.11",
        "Mid-Rack Temp Normal", "Mid-rack temperature returned to normal", "informational"),
    TrapType.SENSOR_OUTLET_TEMP_NORMAL: TrapDefinition(
        TrapType.SENSOR_OUTLET_TEMP_NORMAL, "1.3.6.1.4.1.99999.1.12",
        "Exhaust Temp Normal", "Rack exhaust temperature returned to normal", "informational"),
    # ── Generator enterprise traps ────────────────────────────────────────────
    TrapType.GEN_RUNNING: TrapDefinition(
        TrapType.GEN_RUNNING, "1.3.6.1.4.1.99999.3.1",
        "Generator Running", "Generator has started; utility power lost, ATS transferred to generator", "critical"),
    TrapType.GEN_STOPPED: TrapDefinition(
        TrapType.GEN_STOPPED, "1.3.6.1.4.1.99999.3.2",
        "Generator Stopped", "Generator has stopped; utility power restored, ATS transferred to mains", "informational"),
    TrapType.GEN_LOW_FUEL: TrapDefinition(
        TrapType.GEN_LOW_FUEL, "1.3.6.1.4.1.99999.3.3",
        "Low Fuel", "Generator fuel tank level below 20%", "major"),
    TrapType.GEN_LOW_COOLANT: TrapDefinition(
        TrapType.GEN_LOW_COOLANT, "1.3.6.1.4.1.99999.3.4",
        "Low Coolant", "Generator coolant level is low", "major"),
    TrapType.GEN_BATTERY_FAILURE: TrapDefinition(
        TrapType.GEN_BATTERY_FAILURE, "1.3.6.1.4.1.99999.3.5",
        "Battery Failure", "Generator starting battery fault detected", "critical"),
    TrapType.GEN_TRANSFER_SWITCH: TrapDefinition(
        TrapType.GEN_TRANSFER_SWITCH, "1.3.6.1.4.1.99999.3.6",
        "Transfer Switch Fault", "Automatic transfer switch relay failure", "critical"),
    TrapType.GEN_OVERCRANK: TrapDefinition(
        TrapType.GEN_OVERCRANK, "1.3.6.1.4.1.99999.3.7",
        "Overcrank", "Generator failed to start after maximum crank attempts", "critical"),
    TrapType.SERVER_POWER_OFF: TrapDefinition(
        TrapType.SERVER_POWER_OFF, "1.3.6.1.4.1.99999.26.0.1",
        "Server Powered Off",
        "Server chassis power turned off (BMC platform event)", "major"),
    TrapType.SERVER_POWER_ON: TrapDefinition(
        TrapType.SERVER_POWER_ON, "1.3.6.1.4.1.99999.26.0.2",
        "Server Powered On",
        "Server chassis power turned on (BMC platform event)", "informational"),
    # ── ATS traps (ASCO 7000 ACC / Eaton ATC-900) — 1.3.6.1.4.1.99999.13.x ──────
    TrapType.ATS_SOURCE_LOST: TrapDefinition(
        TrapType.ATS_SOURCE_LOST, "1.3.6.1.4.1.99999.13.1",
        "Normal Source Lost",
        "ATS normal (utility) source voltage/frequency out of range", "major"),
    TrapType.ATS_ENGINE_START: TrapDefinition(
        TrapType.ATS_ENGINE_START, "1.3.6.1.4.1.99999.13.2",
        "Engine Start Signal",
        "ATS asserted the generator engine-start contact", "informational"),
    TrapType.ATS_TRANSFER_EMERGENCY: TrapDefinition(
        TrapType.ATS_TRANSFER_EMERGENCY, "1.3.6.1.4.1.99999.13.3",
        "Transferred to Emergency",
        "ATS transferred the load to the emergency (generator) source", "critical"),
    TrapType.ATS_TRANSFER_NORMAL: TrapDefinition(
        TrapType.ATS_TRANSFER_NORMAL, "1.3.6.1.4.1.99999.13.4",
        "Transferred to Normal",
        "ATS retransferred the load to the normal (utility) source", "informational"),
    TrapType.ATS_FAIL_TO_TRANSFER: TrapDefinition(
        TrapType.ATS_FAIL_TO_TRANSFER, "1.3.6.1.4.1.99999.13.5",
        "Fail to Transfer",
        "ATS failed to transfer to an available source", "critical"),
    TrapType.ATS_NOT_IN_AUTO: TrapDefinition(
        TrapType.ATS_NOT_IN_AUTO, "1.3.6.1.4.1.99999.13.6",
        "Not in Automatic",
        "ATS control switch is not in the automatic position", "minor"),
    TrapType.ATS_RETURNED_TO_AUTO: TrapDefinition(
        TrapType.ATS_RETURNED_TO_AUTO, "1.3.6.1.4.1.99999.13.7",
        "Returned to Automatic",
        "ATS control switch returned to the automatic position", "informational"),
    TrapType.ATS_TRANSFER_FAULT_CLEARED: TrapDefinition(
        TrapType.ATS_TRANSFER_FAULT_CLEARED, "1.3.6.1.4.1.99999.13.8",
        "Transfer Fault Cleared",
        "ATS fail-to-transfer fault has cleared", "informational"),
}

# Reverse lookup: OID string → TrapType  (used by rule engine to map OIDs)
OID_TO_TRAP_TYPE: dict[str, TrapType] = {
    defn.oid: trap_type
    for trap_type, defn in TRAP_DEFINITIONS.items()
}

# Traps that make sense for each device type
APPLICABLE_TRAPS: dict[str, list[TrapType]] = {
    "router": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.BGP_DOWN, TrapType.BGP_UP,
    ],
    "switch": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.LINK_FLAP,
    ],
    "server": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.MEMORY_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.UPS_ON_BATTERY, TrapType.UPS_LOW_BATTERY,
    ],
    "firewall": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
        TrapType.BGP_DOWN, TrapType.BGP_UP,
    ],
    "load_balancer": [
        TrapType.COLD_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.MEMORY_HIGH, TrapType.TEMPERATURE_ALERT,
    ],
    "oob_switch": [
        TrapType.COLD_START, TrapType.WARM_START,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.AUTH_FAILURE,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
    ],
    "sensor": [
        TrapType.COLD_START,
        TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.HUMIDITY_ALERT,
        TrapType.DEWPOINT_ALERT,
        TrapType.AIRFLOW_ALERT,
        TrapType.SENSOR_MID_TEMP_HIGH, TrapType.SENSOR_MID_TEMP_NORMAL,
        TrapType.SENSOR_OUTLET_TEMP_HIGH, TrapType.SENSOR_OUTLET_TEMP_NORMAL,
    ],
    "ups": [
        TrapType.COLD_START,
        TrapType.AUTH_FAILURE,
        TrapType.UPS_ON_BATTERY, TrapType.UPS_LOW_BATTERY,
        TrapType.UPS_BATTERY_NORMAL, TrapType.UPS_UTILITY_RESTORED,
        TrapType.UPS_OUTPUT_OVERLOAD, TrapType.UPS_OUTPUT_NORMAL,
        TrapType.UPS_FAN_FAILURE,
        TrapType.UPS_BATTERY_FAILURE, TrapType.UPS_BATTERY_DISCONNECTED,
        TrapType.UPS_CHARGER_FAILURE,
        TrapType.UPS_INPUT_VOLTAGE_HIGH, TrapType.UPS_INPUT_VOLTAGE_LOW,
        TrapType.UPS_FREQUENCY_OUT_RANGE,
        TrapType.UPS_RECTIFIER_FAILURE, TrapType.UPS_PHASE_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.UPS_BATTERY_LOW_HEALTH, TrapType.UPS_BATTERY_HEALTH_RESTORED,
        TrapType.UPS_BYPASS_ACTIVE, TrapType.UPS_BYPASS_CLEARED,
        TrapType.UPS_INPUT_VOLTAGE_NORMAL, TrapType.UPS_INPUT_VOLTAGE_LOW_CLEARED,
    ],
    "pdu": [
        TrapType.COLD_START,
        TrapType.AUTH_FAILURE,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.PDU_OUTLET_ON, TrapType.PDU_OUTLET_OFF,
        TrapType.PDU_BREAKER_TRIPPED,
        TrapType.PDU_LOAD_HIGH, TrapType.PDU_LOAD_CRITICAL,
        TrapType.PDU_VOLTAGE_HIGH, TrapType.PDU_VOLTAGE_LOW,
        TrapType.PDU_PHASE_IMBALANCE, TrapType.PDU_POWER_FACTOR_LOW,
        TrapType.PDU_OUTLET_FAILURE, TrapType.PDU_SMOKE_DETECTED,
        TrapType.PDU_OUTLET_CURRENT_HIGH, TrapType.PDU_GROUND_FAULT,
        TrapType.PDU_FREQUENCY_FAULT, TrapType.PDU_FREQUENCY_NORMAL,
        TrapType.PDU_TEMP_HIGH, TrapType.PDU_TEMP_NORMAL,
        TrapType.PDU_HUMIDITY_HIGH, TrapType.PDU_HUMIDITY_NORMAL,
    ],
    "floor_pdu": [
        TrapType.COLD_START,
        TrapType.AUTH_FAILURE,
        TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.PDU_BREAKER_TRIPPED,
        TrapType.PDU_LOAD_HIGH, TrapType.PDU_LOAD_CRITICAL,
        TrapType.PDU_VOLTAGE_HIGH, TrapType.PDU_VOLTAGE_LOW,
        TrapType.PDU_PHASE_IMBALANCE, TrapType.PDU_POWER_FACTOR_LOW,
        TrapType.PDU_SMOKE_DETECTED, TrapType.PDU_GROUND_FAULT,
        TrapType.PDU_FREQUENCY_FAULT, TrapType.PDU_FREQUENCY_NORMAL,
        TrapType.PDU_TEMP_HIGH, TrapType.PDU_TEMP_NORMAL,
        TrapType.PDU_HUMIDITY_HIGH, TrapType.PDU_HUMIDITY_NORMAL,
    ],
    "generator": [
        TrapType.COLD_START,
        TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.GEN_RUNNING, TrapType.GEN_STOPPED,
        TrapType.GEN_LOW_FUEL, TrapType.GEN_LOW_COOLANT,
        TrapType.GEN_BATTERY_FAILURE,
        TrapType.GEN_TRANSFER_SWITCH,
        TrapType.GEN_OVERCRANK,
    ],
}

# Switch model name substrings that indicate BGP support.
_BGP_CAPABLE_SWITCH_MODELS: set[str] = {
    "Nexus",          "Catalyst 9",
    "QFX",            "EX9",
    "Arista",
    "Aruba 6300",     "Aruba 8325",
    "FlexFabric 5940",
    "X695",           "X870",
    "CE6",            "CE8",   "S6730-H",
    "S5248F",         "S5296F", "Z9264F",
}


# Per-model sensor trap sets (only metrics that sensor physically measures)
_SENSOR_MODEL_TRAPS: dict[str, list[TrapType]] = {
    "Raritan DPX2-T3H1": [
        TrapType.COLD_START, TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.HUMIDITY_ALERT,
        TrapType.SENSOR_MID_TEMP_HIGH, TrapType.SENSOR_MID_TEMP_NORMAL,
        TrapType.SENSOR_OUTLET_TEMP_HIGH, TrapType.SENSOR_OUTLET_TEMP_NORMAL,
    ],
    "Raritan DPX2-CC2": [
        TrapType.COLD_START, TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
    ],
    "Vertiv Geist GTHD": [
        TrapType.COLD_START, TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.HUMIDITY_ALERT,
        TrapType.DEWPOINT_ALERT,
    ],
    "APC NetBotz 355": [
        TrapType.COLD_START, TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.HUMIDITY_ALERT,
        TrapType.AIRFLOW_ALERT,
    ],
    "APC NetBotz 250": [
        TrapType.COLD_START, TrapType.AUTH_FAILURE,
        TrapType.TEMPERATURE_ALERT,
        TrapType.HUMIDITY_ALERT,
        TrapType.AIRFLOW_ALERT,
    ],
}


def get_applicable_traps(device_type: str, vendor: str,
                         model_name: str = "") -> list[TrapType]:
    """Return applicable trap types for a device."""
    if device_type == "sensor":
        return list(_SENSOR_MODEL_TRAPS.get(model_name, [
            TrapType.COLD_START, TrapType.AUTH_FAILURE,
            TrapType.TEMPERATURE_ALERT,
        ]))
    base = list(APPLICABLE_TRAPS.get(device_type, [
        TrapType.COLD_START, TrapType.LINK_DOWN, TrapType.LINK_UP,
        TrapType.CPU_HIGH, TrapType.TEMPERATURE_ALERT,
    ]))
    if device_type == "switch" and any(kw in model_name for kw in _BGP_CAPABLE_SWITCH_MODELS):
        if TrapType.BGP_DOWN not in base:
            base.append(TrapType.BGP_DOWN)
        if TrapType.BGP_UP not in base:
            base.append(TrapType.BGP_UP)
    return base
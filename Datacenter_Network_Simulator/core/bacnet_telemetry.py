"""
Verdigris EV2 BACnet Telemetry Engine.

Random-walk simulation for one EV2 panel.  Called every tick_interval
seconds from BACnetController.tick().

Simulates realistic electrical behaviour:
  • Three-phase voltage with minor drift and occasional transients
  • Per-circuit load current with diurnal pattern + random fluctuation
  • Panel-level kW derived from circuit loads
  • Monotonically increasing kWh energy counters
  • Power factor drift (0.85 – 0.99)
  • THD with correlated harmonic content
  • Alarm states triggered by physics (overcurrent, imbalance, high THD…)
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CircuitState:
    """Per-circuit electrical state."""
    current:  float = 0.0   # A
    kw:       float = 0.0   # kW
    kwh:      float = 0.0   # kWh (cumulative)
    pf:       float = 0.95
    thd:      float = 3.0   # %
    # Drift target — circuit slowly wanders toward this load
    _target_current: float = field(default=0.0, repr=False)

    def __post_init__(self):
        self._target_current = random.uniform(1.0, 18.0)


class EV2TelemetryEngine:
    """
    Stateful telemetry generator for one Verdigris EV2 panel.

    Usage::

        engine = EV2TelemetryEngine(circuits=42, frequency_hz=50.0)
        values = engine.tick(30.0)   # dt in seconds
        # values is a dict: {"Panel_Total_kW": 47.3, "Ckt01_Current": 8.2, ...}
    """

    # ── Overcurrent alarm threshold (A per phase) ─────────────────
    OVERCURRENT_THRESHOLD    = 85.0
    VOLTAGE_IMBALANCE_THRESH = 5.0    # V — max phase-to-phase difference
    HIGH_THD_THRESH          = 7.0    # %
    PHASE_LOSS_THRESH        = 10.0   # V — below this = phase lost

    def __init__(
        self,
        circuits: int = 42,
        frequency_hz: float = 50.0,
        nominal_voltage: float = 230.0,
    ):
        self._circuits       = circuits
        self._freq_nominal   = frequency_hz
        self._v_nominal      = nominal_voltage

        # ── Phase voltages (V) ────────────────────────────────────
        self._va = nominal_voltage + random.uniform(-1.0, 1.0)
        self._vb = nominal_voltage + random.uniform(-1.0, 1.0)
        self._vc = nominal_voltage + random.uniform(-1.0, 1.0)

        # ── Panel-level current (A) ───────────────────────────────
        self._ia  = random.uniform(20.0, 60.0)
        self._ib  = random.uniform(20.0, 60.0)
        self._ic  = random.uniform(20.0, 60.0)

        # ── Frequency ─────────────────────────────────────────────
        self._freq = frequency_hz + random.uniform(-0.05, 0.05)

        # ── Power factor ──────────────────────────────────────────
        self._pf = random.uniform(0.92, 0.98)

        # ── THD ───────────────────────────────────────────────────
        self._v_thd   = random.uniform(1.5, 3.0)   # %
        self._i_thd   = random.uniform(2.5, 5.0)   # %
        self._h3       = random.uniform(2.0, 5.0)   # 3rd harmonic %
        self._h5       = random.uniform(1.0, 3.5)   # 5th harmonic %
        self._h7       = random.uniform(0.5, 2.0)   # 7th harmonic %
        self._h9       = random.uniform(0.2, 1.0)   # 9th harmonic %

        # ── Energy accumulators (kWh) ─────────────────────────────
        self._panel_kwh = random.uniform(50_000.0, 500_000.0)

        # ── Per-circuit state ─────────────────────────────────────
        self._circuits_state: List[CircuitState] = [
            CircuitState(
                current=random.uniform(0.5, 16.0),
                pf=random.uniform(0.88, 0.99),
                thd=random.uniform(2.0, 6.0),
                kwh=random.uniform(500.0, 5000.0),
            )
            for _ in range(circuits)
        ]

        # ── Diurnal load multiplier ───────────────────────────────
        # Simulates higher load during business hours
        self._start_time = time.time()

        # ── Alarm state + debounce counters ──────────────────────
        # Each alarm requires ALARM_DEBOUNCE consecutive ticks above
        # threshold before activating — suppresses single-event spikes.
        self._alarm_overcurrent       = False
        self._alarm_voltage_imbalance = False
        self._alarm_high_thd          = False
        self._alarm_phase_loss        = False
        self._alarm_sensor_fault      = False
        self._fault_recovery_timer    = 0.0

        self._ALARM_DEBOUNCE = 2   # ticks above threshold required to latch
        self._cnt_overcurrent       = 0
        self._cnt_voltage_imbalance = 0
        self._cnt_high_thd          = 0
        self._cnt_phase_loss        = 0

    # ─────────────────────────────────────────────────────────────
    #  Diurnal load multiplier (0.3 – 1.0 over 24 h)
    # ─────────────────────────────────────────────────────────────

    def _diurnal(self) -> float:
        """Return a 0.3–1.0 multiplier that peaks at ~14:00 local time."""
        hour = (time.localtime().tm_hour + time.localtime().tm_min / 60.0)
        # Sine curve: peaks at 14:00, troughs at 02:00
        phase = 2.0 * math.pi * (hour - 2.0) / 24.0
        return 0.3 + 0.35 * (1.0 + math.sin(phase))   # 0.3 – 1.0

    # ─────────────────────────────────────────────────────────────
    #  Main tick
    # ─────────────────────────────────────────────────────────────

    def tick(self, dt: float) -> Dict[str, float]:
        """
        Advance simulation by *dt* seconds.

        Returns a flat dict of all object names → new present values.
        Boolean alarm values are returned as 0.0 / 1.0.
        """
        mul = self._diurnal()
        self._step_voltages()
        self._step_frequency()
        self._step_thd()
        self._step_pf()
        self._step_panel_current(mul)
        self._step_circuits(dt, mul)
        self._update_alarms()

        # ── Panel kW ──────────────────────────────────────────────
        # Approximate: P = V_avg × I_avg × PF × sqrt(3) for 3-phase
        v_avg = (self._va + self._vb + self._vc) / 3.0
        i_avg = (self._ia + self._ib + self._ic) / 3.0
        panel_kw = round(v_avg * i_avg * self._pf * math.sqrt(3) / 1000.0, 3)

        # ── Panel kWh accumulation ─────────────────────────────────
        self._panel_kwh += panel_kw * dt / 3600.0

        values: Dict[str, float] = {
            "Panel_Total_kW":     max(0.0, panel_kw),
            "Panel_Total_kWh":    round(self._panel_kwh, 3),
            "Voltage_PhA":        round(self._va, 2),
            "Voltage_PhB":        round(self._vb, 2),
            "Voltage_PhC":        round(self._vc, 2),
            "Current_PhA":        round(self._ia, 2),
            "Current_PhB":        round(self._ib, 2),
            "Current_PhC":        round(self._ic, 2),
            "Line_Frequency":     round(self._freq, 3),
            "Panel_PF":           round(self._pf, 3),
            "Voltage_THD":        round(self._v_thd, 2),
            "Current_THD":        round(self._i_thd, 2),
            "Harmonic_3_Current": round(self._h3, 2),
            "Harmonic_5_Current": round(self._h5, 2),
            "Harmonic_7_Current": round(self._h7, 2),
            "Harmonic_9_Current": round(self._h9, 2),
            # Alarm BIs (float 0/1)
            "Alarm_Overcurrent":        1.0 if self._alarm_overcurrent       else 0.0,
            "Alarm_VoltageImbalance":   1.0 if self._alarm_voltage_imbalance else 0.0,
            "Alarm_HighTHD":            1.0 if self._alarm_high_thd          else 0.0,
            "Alarm_PhaseLoss":          1.0 if self._alarm_phase_loss        else 0.0,
            "Alarm_SensorFault":        1.0 if self._alarm_sensor_fault      else 0.0,
        }

        # ── Per-circuit values ─────────────────────────────────────
        for i, cs in enumerate(self._circuits_state):
            ckt = i + 1
            label = f"Ckt{ckt:02d}"
            values[f"{label}_Current"] = round(cs.current, 2)
            values[f"{label}_kW"]      = round(cs.kw,      3)
            values[f"{label}_kWh"]     = round(cs.kwh,     3)
            values[f"{label}_PF"]      = round(cs.pf,      3)
            values[f"{label}_THD"]     = round(cs.thd,     2)

        return values

    # ─────────────────────────────────────────────────────────────
    #  Step helpers
    # ─────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────
    #  EMA helper — Exponential Moving Average filter
    #  Suppresses high-frequency jitter without hiding real trends.
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _ema(new_val: float, old_val: float, alpha: float) -> float:
        """filtered = alpha × new + (1 − alpha) × old"""
        return alpha * new_val + (1.0 - alpha) * old_val

    def _step_voltages(self):
        """
        Stable signal class — slow random-walk ±0.15 V/tick with EMA α=0.12.
        Occasional voltage transients (sag/swell) still pass through.
        COV cadence: every few minutes at 1.0 V threshold.
        """
        for attr in ('_va', '_vb', '_vc'):
            old = getattr(self, attr)
            raw = old + random.uniform(-0.15, 0.15)
            # Occasional sag/swell (0.2% chance per phase per tick)
            if random.random() < 0.002:
                raw += random.choice([-1, 1]) * random.uniform(3.0, 10.0)
            # Drift back toward nominal
            raw += (self._v_nominal - raw) * 0.02
            # EMA smoothing — α=0.12 for stable signals
            smoothed = self._ema(raw, old, alpha=0.12)
            setattr(self, attr, max(0.0, min(300.0, smoothed)))

    def _step_frequency(self):
        """
        Stable signal class — ±0.008 Hz/tick with EMA α=0.12.
        Grid regulation pulls toward nominal. COV cadence: every 5–10 min.
        """
        raw = self._freq + random.uniform(-0.008, 0.008)
        raw += (self._freq_nominal - raw) * 0.1
        self._freq = self._ema(raw, self._freq, alpha=0.12)
        self._freq = max(45.0, min(65.0, self._freq))

    def _step_thd(self):
        """
        Burst/event signal class — routine jitter ±0.05 %/tick with EMA α=0.15.
        Periodic harmonic events (UPS startup, nonlinear load) spike THD.
        COV cadence: mostly quiet, fires on harmonic events only.
        """
        # Current THD: small routine jitter
        raw_i = self._i_thd + random.uniform(-0.05, 0.05)
        raw_i = max(1.0, min(15.0, raw_i))
        # Harmonic event: 0.3% chance per tick (UPS, VFD, nonlinear load)
        if random.random() < 0.003:
            raw_i = random.uniform(7.5, 12.0)   # spike — bypasses EMA clamp
        self._i_thd = self._ema(raw_i, self._i_thd, alpha=0.15)
        self._i_thd = max(1.0, min(15.0, self._i_thd))

        # Voltage THD: even slower
        raw_v = self._v_thd + random.uniform(-0.03, 0.03)
        self._v_thd = self._ema(raw_v, self._v_thd, alpha=0.15)
        self._v_thd = max(0.5, min(8.0, self._v_thd))

        # Harmonics correlated with current THD; EMA α=0.15
        ratio = self._i_thd / 5.0
        self._h3 = self._ema(
            max(0.1, min(20.0, ratio * random.uniform(3.5, 5.0))), self._h3, 0.15)
        self._h5 = self._ema(
            max(0.1, min(15.0, ratio * random.uniform(2.0, 3.5))), self._h5, 0.15)
        self._h7 = self._ema(
            max(0.1, min(8.0,  ratio * random.uniform(1.0, 2.0))), self._h7, 0.15)
        self._h9 = self._ema(
            max(0.1, min(4.0,  ratio * random.uniform(0.4, 1.0))), self._h9, 0.15)

    def _step_pf(self):
        """
        Stable signal class — ±0.002/tick with EMA α=0.12.
        PF drifts very slowly; COV cadence: every several minutes.
        """
        raw = self._pf + random.uniform(-0.002, 0.002)
        self._pf = self._ema(raw, self._pf, alpha=0.12)
        self._pf = max(0.70, min(0.99, self._pf))

    def _step_panel_current(self, mul: float):
        """
        Operational signal class — ±0.3 A/tick noise with EMA α=0.18.
        Diurnal load curve maintained. Occasional load spikes still visible.
        COV cadence: every 1–3 minutes at 1.0 A threshold.
        """
        base = mul * 60.0   # peak ~60 A/phase at full load
        for attr in ('_ia', '_ib', '_ic'):
            old = getattr(self, attr)
            target = base + random.uniform(-3.0, 3.0)
            raw = old + (target - old) * 0.05 + random.uniform(-0.3, 0.3)
            # Occasional load spike (0.3% chance) — intentionally not smoothed
            if random.random() < 0.003:
                raw += random.uniform(10.0, 25.0)
                setattr(self, attr, max(0.0, min(200.0, raw)))
                continue
            smoothed = self._ema(raw, old, alpha=0.18)
            setattr(self, attr, max(0.0, min(200.0, smoothed)))

    def _step_circuits(self, dt: float, mul: float):
        """
        Operational signal class — circuit current ±0.08 A/tick with EMA α=0.18.
        Load transitions are smooth. kWh accumulates deterministically.
        COV cadence: current/kW every 1–2 min, kWh every few hours.
        """
        v_avg = (self._va + self._vb + self._vc) / 3.0

        for cs in self._circuits_state:
            # Target current drifts slowly
            cs._target_current += random.uniform(-0.15, 0.15)
            cs._target_current  = max(0.1, min(20.0 * mul, cs._target_current))

            # Small routine jitter + mean-reversion toward target
            raw_i = cs.current + (cs._target_current - cs.current) * 0.08
            raw_i += random.uniform(-0.08, 0.08)
            # Occasional load step (0.5% chance per circuit per tick)
            if random.random() < 0.005:
                raw_i += random.choice([-1, 1]) * random.uniform(1.0, 4.0)
                cs.current = max(0.0, raw_i)
            else:
                cs.current = max(0.0, self._ema(raw_i, cs.current, alpha=0.18))

            # Power factor: very slow drift, EMA α=0.12
            raw_pf = cs.pf + random.uniform(-0.002, 0.002)
            cs.pf  = self._ema(raw_pf, cs.pf, alpha=0.12)
            cs.pf  = max(0.70, min(0.99, cs.pf))

            # kW from single-phase: P = V × I × PF
            cs.kw = v_avg * cs.current * cs.pf / 1000.0

            # kWh accumulation (deterministic — no noise on energy counter)
            cs.kwh += cs.kw * dt / 3600.0

            # Circuit THD: burst/event class — small routine jitter, EMA α=0.15
            raw_thd = cs.thd + random.uniform(-0.05, 0.05)
            cs.thd  = self._ema(raw_thd, cs.thd, alpha=0.15)
            cs.thd  = max(1.0, min(12.0, cs.thd))

    def _update_alarms(self):
        """Evaluate alarm conditions with debounce — N consecutive ticks required."""

        def _debounce(condition: bool, counter_attr: str, alarm_attr: str) -> None:
            if condition:
                new_cnt = getattr(self, counter_attr) + 1
                setattr(self, counter_attr, new_cnt)
                if new_cnt >= self._ALARM_DEBOUNCE:
                    setattr(self, alarm_attr, True)
            else:
                setattr(self, counter_attr, 0)
                setattr(self, alarm_attr, False)

        # Overcurrent: any phase > threshold
        _debounce(
            self._ia > self.OVERCURRENT_THRESHOLD or
            self._ib > self.OVERCURRENT_THRESHOLD or
            self._ic > self.OVERCURRENT_THRESHOLD,
            '_cnt_overcurrent', '_alarm_overcurrent',
        )

        # Voltage imbalance: max - min phase voltage > threshold
        v_max = max(self._va, self._vb, self._vc)
        v_min = min(self._va, self._vb, self._vc)
        _debounce(
            (v_max - v_min) > self.VOLTAGE_IMBALANCE_THRESH,
            '_cnt_voltage_imbalance', '_alarm_voltage_imbalance',
        )

        # High THD: current THD exceeds threshold
        _debounce(
            self._i_thd > self.HIGH_THD_THRESH,
            '_cnt_high_thd', '_alarm_high_thd',
        )

        # Phase loss: any phase voltage below threshold
        _debounce(
            self._va < self.PHASE_LOSS_THRESH or
            self._vb < self.PHASE_LOSS_THRESH or
            self._vc < self.PHASE_LOSS_THRESH,
            '_cnt_phase_loss', '_alarm_phase_loss',
        )

        # Sensor fault: random occurrence 0.05% chance per tick, clears after 5 ticks
        if self._alarm_sensor_fault:
            self._fault_recovery_timer -= 1
            if self._fault_recovery_timer <= 0:
                self._alarm_sensor_fault = False
        elif random.random() < 0.0005:
            self._alarm_sensor_fault   = True
            self._fault_recovery_timer = random.randint(3, 8)

    # ─────────────────────────────────────────────────────────────
    #  Convenience: apply values to a BACnetObject dict
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def apply_to_objects(
        values: Dict[str, float],
        object_map: dict,
        name_to_instance: dict,
    ) -> None:
        """
        Write tick values into the BACnetObject present_value fields.

        name_to_instance: {"Panel_Total_kW": 1001, ...}
        object_map: {(obj_type, instance): BACnetObject, ...}
        """
        from core.bacnet_object_model import OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT
        for name, val in values.items():
            inst = name_to_instance.get(name)
            if inst is None:
                continue
            # Try BI first (alarm objects), then AI
            obj = object_map.get((OBJ_BINARY_INPUT, inst)) or \
                  object_map.get((OBJ_ANALOG_INPUT,  inst))
            if obj is not None:
                obj.present_value = float(val)

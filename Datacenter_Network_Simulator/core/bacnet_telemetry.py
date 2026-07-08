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


# ── Load-coupled power-quality curves (active-PFC IT loads) ───────────────────
# Modern server PSUs use active power-factor correction: PF is poor at light load
# and climbs toward unity as the supply loads up, while current distortion (%THD-i)
# is HIGH at light load (the fundamental is small) and falls as the load rises.
# Both are functions of the load fraction, not free-running walks.
# Fixed rack-PDU branch breaker (32 A @ 230 V single-phase), the reference a
# branch's live kW is measured against for its PF/THD load fraction — the SAME
# basis the PDU uses (device_state_store._DEFAULT_PDU_RATED_W), so the EV2 branch
# and the rack PDU it clamps report a consistent power factor.
BRANCH_BREAKER_KW = 7.36


def _pf_from_load(lf: float) -> float:
    """Displacement/true PF vs load fraction: ~0.70 idle → 0.99 near full, with a
    fast-saturating knee typical of active PFC."""
    lf = max(0.0, min(1.0, lf))
    return max(0.70, min(0.99, 0.99 - 0.29 * (1.0 - lf) ** 2))


def _thd_i_from_load(lf: float) -> float:
    """Current THD (%) vs load fraction for a modern ACTIVE-PFC IT supply: ~5 %
    near full, rising only as the branch approaches idle (~18 % at no load). The
    knee is deliberately steep (cubic) so a branch carrying real load stays in the
    active-PFC band — ~5 % at 70 % load, ~7 % at 50 %, ~10 % at 25 % — instead of
    the old quadratic that read ~15 % at a quarter load (passive-PFC territory).
    Very light load still inflates %THD-i against a shrinking fundamental, which is
    physical (and why IEEE 519 measures TDD, not THD, there)."""
    lf = max(0.0, min(1.0, lf))
    return max(4.0, min(22.0, 5.0 + 13.0 * (1.0 - lf) ** 3))


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
    # Low-side power-quality limits (the disturbances real DCs actually see —
    # utility faults, motor starts, transfer-switch operations cause voltage SAGS,
    # and a distressed/overloaded grid or genset DROPS frequency). Symmetric with
    # the UI's high-side colouring. Sag: −10 % of a 230 V nominal; under-freq: −2 %
    # of 50 Hz. Expressed as a fraction of the panel's own nominal so a 208 V or
    # 60 Hz panel scales correctly.
    UNDERVOLTAGE_FRAC        = 0.90   # × nominal_voltage → sag alarm (207 V @ 230)
    UNDERFREQ_FRAC           = 0.98   # × nominal_freq   → under-freq (49 Hz @ 50)

    def __init__(
        self,
        circuits: int = 42,
        frequency_hz: float = 50.0,
        nominal_voltage: float = 230.0,
        active_circuits: int = 0,
        load_scale: float = 1.0,
        rated_kw: float | None = None,
    ):
        # active_circuits: number of circuits with real downstream loads.
        # Circuits beyond this index output zero — they are spare/unused breakers.
        # 0 means all circuits are active (legacy behaviour).
        #
        # Panel magnitude (peak phase current at full diurnal load) is sized one
        # of two ways, in priority order:
        #   1. rated_kw — the real peak kW this panel carries, summed from the
        #      power_draw_w of every device downstream of it on the power graph.
        #      A facility meter on the building feed sums IT + cooling, so its
        #      reading exceeds the IT sub-meters and PUE = facility/IT > 1 falls
        #      straight out of the physics.
        #   2. load_scale — legacy size multiplier vs a nominal 60 A/phase panel,
        #      used only when no rated_kw is supplied (un-populated topology).
        self._circuits       = circuits
        self._active         = active_circuits if active_circuits > 0 else circuits
        self._load_scale     = max(0.1, load_scale)
        if rated_kw and rated_kw > 0:
            # I = P / (V × PF × √3) for a balanced 3-phase load (assume PF≈0.90).
            self._i_nominal = (rated_kw * 1000.0) / (nominal_voltage * 0.90 * math.sqrt(3))
        else:
            self._i_nominal = 60.0 * self._load_scale
        # Peak kW this panel carries at full load (I_nominal). When a live
        # downstream load is supplied to tick(), the load multiplier is live_kw /
        # this peak — so the panel meters the real IT draw instead of a synthetic
        # diurnal curve.
        self._rated_kw_peak = (self._i_nominal * nominal_voltage * 0.90 * math.sqrt(3)) / 1000.0
        # A branch is one rack PDU on a fixed 32 A breaker — its live load over that
        # breaker is the 0..1 load fraction driving its PF and current-THD, matching
        # the PDU's own basis so the two agree.
        self._branch_rated_kw = BRANCH_BREAKER_KW
        # Phase-current ceiling and overcurrent trip reference the panel MAIN
        # BREAKER — a FIXED hardware limit, not the load present when the meter was
        # commissioned. A branch-metering RPP is a `circuits`-pole panel of 32 A
        # (BRANCH_BREAKER_KW) branches, so its main is on the order of its pole
        # capacity. Sizing the trip off the start-time downstream load instead (the
        # old behaviour) made ANY post-commission growth — the fleet clamping more
        # rack PDUs onto a curated RPP as a hall fills — drive the live current past
        # a threshold frozen to the smaller original load, false-tripping a healthy,
        # fully-populated panel. max() keeps aggregate/facility meters (huge
        # rated_kw, few real branches) sized by their real load, not the pole count.
        # 85/60 keeps the legacy trip-to-nominal ratio for standard panels.
        panel_i = (circuits * self._branch_rated_kw * 1000.0) / (nominal_voltage * 0.90 * math.sqrt(3))
        i_cap   = max(self._i_nominal, panel_i)
        self._i_clamp            = max(200.0, i_cap * 1.6)
        self._overcurrent_thresh = i_cap * (self.OVERCURRENT_THRESHOLD / 60.0)
        # Rated per-phase current — the nameplate the DASHBOARD colours a phase
        # against, so the colour tracks the panel size instead of a flat amp limit
        # that turns a healthy 42-pole RPP red just for carrying its normal hundreds
        # of amps. Basis matches the DISPLAYED phase current (branch CTs summed onto
        # 3 phases at ~230 V single-phase, PF≈0.95 near full), NOT the √3 line-current
        # figure used for the alarm: a full panel is (circuits/3) branches per phase,
        # each on its 32 A (BRANCH_BREAKER_KW) breaker. max() with _i_nominal keeps
        # facility/aggregate meters (huge load, few branches) sized by real load.
        branch_i = (self._branch_rated_kw * 1000.0) / (nominal_voltage * 0.95)
        self._rated_phase_current = max((circuits / 3.0) * branch_i, self._i_nominal)
        # Panel rated kW (nameplate) — the dashboard colours Total kW against this
        # instead of a flat 150/180 kW limit that reads a healthy fully-loaded panel
        # red. A `circuits`-pole panel of 32 A branches carries circuits ×
        # BRANCH_BREAKER_KW at full load; max() with the load-based peak keeps
        # facility/aggregate meters sized by their real load.
        self._rated_capacity_kw = max(circuits * self._branch_rated_kw, self._rated_kw_peak)
        self._freq_nominal   = frequency_hz
        self._v_nominal      = nominal_voltage

        # ── Phase voltages (V) ────────────────────────────────────
        self._va = nominal_voltage + random.uniform(-1.0, 1.0)
        self._vb = nominal_voltage + random.uniform(-1.0, 1.0)
        self._vc = nominal_voltage + random.uniform(-1.0, 1.0)

        # ── Panel-level current (A) — start near nominal, converges in ticks ─
        self._ia  = random.uniform(0.55, 0.95) * self._i_nominal
        self._ib  = random.uniform(0.55, 0.95) * self._i_nominal
        self._ic  = random.uniform(0.55, 0.95) * self._i_nominal

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
        # A real Verdigris counts up from 0 at commissioning, so a branch's
        # lifetime kWh ≈ its average load × install age — and the mains kWh equals
        # the sum of the branch kWh. Rather than seed each counter with an
        # independent random number (which made an idle branch show huge kWh and
        # the panel disagree with its circuits), we seed lazily on the first tick
        # from each branch's REAL load × a per-panel install age, and set the
        # panel counter to Σ branch kWh. Coherence then holds forever because both
        # sides accumulate the same per-tick energy.
        self._install_age_h = random.uniform(2160.0, 12960.0)   # ~90–540 days
        self._kwh_seeded    = False
        self._panel_kwh     = 0.0

        # ── Per-circuit state ─────────────────────────────────────
        self._circuits_state: List[CircuitState] = [
            CircuitState(
                current=random.uniform(0.5, 16.0),
                pf=random.uniform(0.88, 0.99),
                thd=random.uniform(2.0, 6.0),
                kwh=0.0,
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
        self._alarm_undervoltage      = False
        self._alarm_underfrequency    = False
        self._fault_recovery_timer    = 0.0

        self._ALARM_DEBOUNCE = 2   # ticks above threshold required to latch
        self._cnt_overcurrent       = 0
        self._cnt_voltage_imbalance = 0
        self._cnt_high_thd          = 0
        self._cnt_phase_loss        = 0
        self._cnt_undervoltage      = 0
        self._cnt_underfrequency    = 0

        # ── Transient power-quality events ────────────────────────
        # A steady sim never leaves nominal, so the low-side alarms would never
        # exercise. Occasionally inject a multi-tick voltage SAG (all phases dip
        # together, as a real feeder fault does) or a FREQUENCY DIP, then recover.
        # Rare — a few events per simulated day — so normal operation stays clean.
        self._sag_ticks       = 0     # >0 while a voltage-sag event is active
        self._freq_dip_ticks  = 0     # >0 while a frequency-dip event is active
        self._sag_target      = 0.0   # V the sag pulls all phases toward
        self._freq_dip_target = 0.0   # Hz the dip pulls frequency toward

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

    def tick(self, dt: float, live_kw: float | None = None,
             circuit_kw: list | None = None) -> Dict[str, float]:
        """
        Advance simulation by *dt* seconds.

        *live_kw* — real downstream load (kW) measured from the power graph. When
        supplied, the panel load multiplier follows the live IT draw instead of
        the synthetic diurnal curve, so a server load change moves this meter.

        *circuit_kw* — per-circuit live load (kW), one entry per branch this panel
        clamps (circuit i → circuit_kw[i]). When supplied, each circuit meters the
        REAL draw of its branch PDU (an empty branch reads ~0, a full one its live
        kW) instead of a panel-scaled random walk, and the circuits sum to the
        panel. Branches beyond the list are spare CTs and read 0.

        Returns a flat dict of all object names → new present values.
        Boolean alarm values are returned as 0.0 / 1.0.
        """
        if live_kw is not None and self._rated_kw_peak > 0:
            mul = max(0.0, min(1.25, live_kw / self._rated_kw_peak))
        else:
            mul = self._diurnal()
        self._step_voltages()
        self._step_frequency()
        self._step_thd(mul)
        self._step_pf()

        live_circuits = circuit_kw is not None
        if live_circuits:
            # The live branch map is the authority on how many CTs are clamped.
            # The fleet can add/remove downstream PDUs long after this engine was
            # built, so the active-circuit count must track the current list
            # length — NOT the value frozen at construction. Without this, any
            # branch added past the original active count trips the spare-CT gate
            # (i >= self._active) and reads 0 even though it carries real load.
            self._active = min(len(circuit_kw), self._circuits)
        if not live_circuits:
            # Legacy/unpopulated mode: the panel current is the driver and the
            # circuits are a panel-scaled random walk beneath it. Step the panel
            # first so the circuits can follow it.
            self._step_panel_current(mul)
        self._step_circuits(dt, mul, circuit_kw)

        # ── Panel kW ──────────────────────────────────────────────
        if live_circuits:
            # Real mode: the mains IS the sum of the branch circuits it meters,
            # exactly as a Verdigris main CT equals the sum of its branch CTs.
            # _derive_panel_from_circuits sets the per-phase currents + panel PF
            # and returns Σ branch kW.
            panel_kw = self._derive_panel_from_circuits()
        else:
            # Approximate: P = V_avg × I_avg × PF × sqrt(3) for 3-phase
            v_avg = (self._va + self._vb + self._vc) / 3.0
            i_avg = (self._ia + self._ib + self._ic) / 3.0
            panel_kw = round(v_avg * i_avg * self._pf * math.sqrt(3) / 1000.0, 3)

        # Alarms read the (now summed) per-phase currents, so evaluate after the
        # panel is resolved.
        self._update_alarms()

        # ── One-time kWh seeding (commissioning baseline) ─────────────
        # Seed each branch's lifetime energy from its real load × install age, and
        # the mains from the sum, so lifetime kWh is load-proportional and the
        # panel counter equals Σ branch counters. Done once, on the first tick when
        # loads are known; both sides then accrue the same per-tick energy.
        if not self._kwh_seeded:
            for i, cs in enumerate(self._circuits_state):
                if live_circuits and circuit_kw is not None:
                    _ck = circuit_kw[i] if i < len(circuit_kw) else None
                    # None = freed/spare channel → no seed energy.
                    base_kw = _ck if (_ck is not None and i < self._active) else 0.0
                else:
                    base_kw = cs.kw if i < self._active else 0.0
                cs.kwh = max(0.0, base_kw) * self._install_age_h
            if live_circuits:
                self._panel_kwh = sum(cs.kwh for cs in self._circuits_state)
            else:
                self._panel_kwh = max(0.0, panel_kw) * self._install_age_h
            self._kwh_seeded = True

        # ── Panel kWh ──────────────────────────────────────────────
        # MONOTONIC mains register: accrue from the live panel kW (both modes),
        # exactly like a real Verdigris main CT whose non-volatile counter only
        # ever climbs. It is deliberately NOT re-derived as Σ branch registers —
        # that tied the mains total to the branches, so removing a branch PDU (its
        # freed channel zeros) made the lifetime kWh step BACKWARD. Branch registers
        # still track their own energy in _step_circuits; the mains just isn't their
        # sum, so a removal lowers instantaneous kW but never the lifetime kWh. (Seed
        # baseline above still sets panel == Σ branch seeds at commissioning; the two
        # then drift only by sub-Wh rounding, as real independent CTs do.)
        self._panel_kwh += max(0.0, panel_kw) * dt / 3600.0

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
            "Alarm_Undervoltage":       1.0 if self._alarm_undervoltage      else 0.0,
            "Alarm_UnderFrequency":     1.0 if self._alarm_underfrequency    else 0.0,
        }

        # ── Per-circuit values ─────────────────────────────────────
        for i, cs in enumerate(self._circuits_state):
            ckt = i + 1
            label = f"Ckt{ckt:02d}"
            if i < self._active:
                values[f"{label}_Current"] = round(cs.current, 2)
                values[f"{label}_kW"]      = round(cs.kw,      3)
                values[f"{label}_kWh"]     = round(cs.kwh,     3)
                values[f"{label}_PF"]      = round(cs.pf,      3)
                values[f"{label}_THD"]     = round(cs.thd,     2)
            else:
                # Spare/unused breaker — no load
                values[f"{label}_Current"] = 0.0
                values[f"{label}_kW"]      = 0.0
                values[f"{label}_kWh"]     = 0.0
                values[f"{label}_PF"]      = 0.0
                values[f"{label}_THD"]     = 0.0

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
        # Voltage-SAG event: a feeder-wide dip that pulls ALL three phases down
        # together (utility fault ride-through, large motor start, ATS transfer).
        # Start rarely; hold the sag band for a few ticks, then recover — this is
        # what trips Alarm_Undervoltage. A sag is a fast transient, so during the
        # event drive the phases toward the target with a HIGH α (not the slow
        # steady-state smoothing, which would never reach the threshold in time).
        if self._sag_ticks <= 0 and random.random() < 0.0006:
            self._sag_ticks  = random.randint(3, 6)
            self._sag_target = self._v_nominal * random.uniform(0.82, 0.88)  # ~189–202 V
        sagging = self._sag_ticks > 0
        if sagging:
            self._sag_ticks -= 1

        for attr in ('_va', '_vb', '_vc'):
            old = getattr(self, attr)
            if sagging:
                tgt = self._sag_target + random.uniform(-1.5, 1.5)   # small per-phase spread
                smoothed = self._ema(tgt, old, alpha=0.7)            # fast transient
            else:
                raw = old + random.uniform(-0.15, 0.15)
                # Occasional shallow sag/swell (0.2% chance per phase per tick)
                if random.random() < 0.002:
                    raw += random.choice([-1, 1]) * random.uniform(3.0, 10.0)
                gap = self._v_nominal - old
                if abs(gap) > 5.0:
                    # Post-event recovery: once a sag/swell clears, real voltage
                    # snaps back within a cycle — pull hard so the alarm de-latches
                    # in a few ticks instead of crawling back over hours.
                    raw += gap * 0.5
                    smoothed = self._ema(raw, old, alpha=0.6)
                else:
                    # Near nominal: gentle stable-signal drift + smoothing.
                    raw += gap * 0.02
                    smoothed = self._ema(raw, old, alpha=0.12)
            setattr(self, attr, max(0.0, min(300.0, smoothed)))

    def _step_frequency(self):
        """
        Stable signal class — ±0.008 Hz/tick with EMA α=0.12.
        Grid regulation pulls toward nominal. COV cadence: every 5–10 min.
        """
        # Frequency-DIP event: a distressed/overloaded grid or genset drops below
        # nominal for a few ticks (load step, generator governor lag), then the
        # regulator recovers it — this is what trips Alarm_UnderFrequency.
        if self._freq_dip_ticks <= 0 and random.random() < 0.0004:
            self._freq_dip_ticks  = random.randint(3, 6)
            self._freq_dip_target = self._freq_nominal * random.uniform(0.960, 0.975)  # ~48.0–48.75 @ 50
        if self._freq_dip_ticks > 0:
            self._freq_dip_ticks -= 1
            self._freq = self._ema(self._freq_dip_target + random.uniform(-0.05, 0.05),
                                   self._freq, alpha=0.6)   # fast transient
        else:
            raw = self._freq + random.uniform(-0.008, 0.008)
            gap = self._freq_nominal - self._freq
            if abs(gap) > 0.3:
                # Post-event recovery — regulator restores frequency quickly once
                # the disturbance clears, so the under-freq alarm de-latches.
                raw += gap * 0.5
                self._freq = self._ema(raw, self._freq, alpha=0.6)
            else:
                raw += gap * 0.1
                self._freq = self._ema(raw, self._freq, alpha=0.12)
        self._freq = max(45.0, min(65.0, self._freq))

    def _step_thd(self, load_frac: float = 1.0):
        """
        Current THD tracks the INVERSE of panel load (high at light load, ~5 % near
        full), with occasional nonlinear-load harmonic bursts on top. Voltage THD
        stays a slow independent walk. EMA α=0.15.
        """
        # Current THD: load-driven target + routine jitter.
        raw_i = _thd_i_from_load(load_frac) + random.uniform(-0.2, 0.2)
        # Harmonic burst: 0.3% chance per tick (UPS, VFD, nonlinear load)
        if random.random() < 0.003:
            raw_i = random.uniform(12.0, 20.0)   # spike — bypasses EMA clamp
        self._i_thd = self._ema(raw_i, self._i_thd, alpha=0.15)
        self._i_thd = max(3.0, min(25.0, self._i_thd))

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
        base = mul * self._i_nominal   # diurnal fraction of peak phase current
        spike_scale = max(1.0, self._i_nominal / 60.0)
        for attr in ('_ia', '_ib', '_ic'):
            old = getattr(self, attr)
            target = base + random.uniform(-3.0, 3.0) * spike_scale
            raw = old + (target - old) * 0.05 + random.uniform(-0.3, 0.3) * spike_scale
            # Occasional load spike (0.3% chance) — intentionally not smoothed
            if random.random() < 0.003:
                raw += random.uniform(10.0, 25.0) * spike_scale
                setattr(self, attr, max(0.0, min(self._i_clamp, raw)))
                continue
            smoothed = self._ema(raw, old, alpha=0.18)
            setattr(self, attr, max(0.0, min(self._i_clamp, smoothed)))

    def _derive_panel_from_circuits(self) -> float:
        """Mains = sum of the branch circuits (real EV2: main CTs == Σ branch CTs).

        Each active branch is a single-phase L-N load assigned to a phase
        round-robin (A/B/C), matching the pole rotation of a real panelboard, so
        the phase imbalance falls out of the actual per-branch loads instead of
        an independent random walk. Per-phase current is the sum of that phase's
        branch currents; panel real power is Σ branch kW; panel PF is the
        current-weighted mean of the branch PFs (so V·ΣI·PF reconciles with Σ kW).

        Note: a 3-phase rack PDU is modelled as one single-phase equivalent branch
        here (one CT per downstream PDU), a deliberate simplification.
        """
        ph = [0.0, 0.0, 0.0]      # phase A / B / C current sums (A)
        tot_kw   = 0.0
        sum_i    = 0.0
        sum_i_pf = 0.0
        for idx in range(self._active):
            cs = self._circuits_state[idx]
            ph[idx % 3] += cs.current
            tot_kw   += cs.kw
            sum_i    += cs.current
            sum_i_pf += cs.current * cs.pf
        self._ia, self._ib, self._ic = ph
        if sum_i > 0.0:
            self._pf = max(0.70, min(0.99, sum_i_pf / sum_i))
        return round(max(0.0, tot_kw), 3)

    # ─────────────────────────────────────────────────────────────
    #  Energy-register persistence (non-volatile meter behaviour)
    # ─────────────────────────────────────────────────────────────

    def get_energy_state(self) -> Dict[str, object]:
        """Snapshot the lifetime energy registers for persistence."""
        return {
            "panel_kwh":   self._panel_kwh,
            "circuit_kwh": [cs.kwh for cs in self._circuits_state],
        }

    def set_energy_state(self, state: dict) -> None:
        """Restore persisted lifetime energy so kWh is CONTINUOUS across restarts,
        like a real meter's non-volatile register. Marks the accumulators seeded so
        the commissioning baseline is NOT re-applied on the first tick."""
        try:
            pk = float(state.get("panel_kwh", 0.0))
            if pk >= 0.0:
                self._panel_kwh = pk
            saved = state.get("circuit_kwh") or []
            for i, cs in enumerate(self._circuits_state):
                if i < len(saved):
                    v = float(saved[i])
                    if v >= 0.0:
                        cs.kwh = v
            self._kwh_seeded = True
        except (TypeError, ValueError):
            pass

    def _step_circuits(self, dt: float, mul: float, circuit_kw: list | None = None):
        """
        Operational signal class — circuit current eases toward its target with
        EMA smoothing. Load transitions are smooth. kWh accumulates deterministically.
        COV cadence: current/kW every 1–2 min, kWh every few hours.

        *circuit_kw* — when given, circuit i's target is the REAL branch load
        circuit_kw[i] (kW → current via P = V·I·PF), so the meter reflects the
        actual PDU it clamps. Branches past the list are spare CTs → 0. Without it,
        each circuit falls back to a panel-scaled random walk (unpopulated topology).
        """
        v_avg = (self._va + self._vb + self._vc) / 3.0

        for i, cs in enumerate(self._circuits_state):
            if i >= self._active:
                # Spare breaker — no load, no energy accrual. Zero it directly so
                # the startup transient never adds phantom kWh to the register.
                cs.current = 0.0
                cs.kw      = 0.0
                cs.thd     = 0.0
                continue
            if circuit_kw is not None and (i >= len(circuit_kw) or circuit_kw[i] is None):
                # Freed/spare CT channel — the branch on this slot was removed (None
                # hole) or is unmapped. Zero the live signals AND the energy register
                # so a vacated channel reads 0 kWh; a future branch that fills this
                # slot starts its accumulator fresh instead of inheriting the old
                # branch's lifetime energy. (A real 0-load branch stays 0.0, not None,
                # so it keeps its accumulator — only true holes are wiped.)
                cs.current = 0.0
                cs.kw      = 0.0
                cs.thd     = 0.0
                cs.pf      = 0.0
                cs.kwh     = 0.0
                continue
            # Power factor: real active-PFC IT loads improve PF as they load up
            # (poor at light load, ~unity near full), so drive it from the branch
            # load fraction instead of a free walk. PF is set first — it turns the
            # target kW into a target current. Legacy mode keeps the slow walk.
            if circuit_kw is not None:
                # Real branch load: I = P / (V × PF). Spare CTs (past the mapped
                # branches, or an energised-but-unloaded rack PDU) target 0.
                tgt_kw = circuit_kw[i] if i < len(circuit_kw) else 0.0
                cs_lf  = (tgt_kw / self._branch_rated_kw) if self._branch_rated_kw > 0 else 0.0
                tgt_pf = _pf_from_load(cs_lf) + random.uniform(-0.004, 0.004)
                cs.pf  = max(0.70, min(0.99, self._ema(tgt_pf, cs.pf, alpha=0.12)))
                denom  = v_avg * cs.pf
                cs._target_current = (tgt_kw * 1000.0 / denom) if denom > 0 else 0.0
            else:
                # Legacy synthetic walk when no live per-circuit load is available.
                cs_lf  = None
                raw_pf = cs.pf + random.uniform(-0.002, 0.002)
                cs.pf  = max(0.70, min(0.99, self._ema(raw_pf, cs.pf, alpha=0.12)))
                cs._target_current += random.uniform(-0.15, 0.15)
                cs._target_current  = max(0.1, min(20.0 * mul, cs._target_current))

            # Mean-reversion toward target + small routine CT jitter.
            raw_i = cs.current + (cs._target_current - cs.current) * 0.20
            raw_i += random.uniform(-0.05, 0.05)
            # Occasional load step only in synthetic mode — real mode follows load.
            if circuit_kw is None and random.random() < 0.005:
                raw_i += random.choice([-1, 1]) * random.uniform(1.0, 4.0)
                cs.current = max(0.0, raw_i)
            else:
                cs.current = max(0.0, self._ema(raw_i, cs.current, alpha=0.18))

            # kW from single-phase: P = V × I × PF
            cs.kw = v_avg * cs.current * cs.pf / 1000.0

            # kWh accumulation (deterministic — no noise on energy counter)
            cs.kwh += cs.kw * dt / 3600.0

            # Circuit current THD: %THD-i is HIGH at light load and falls as the
            # branch loads up, so track the inverse of load; nonlinear bursts on top.
            if cs_lf is not None:
                raw_thd = _thd_i_from_load(cs_lf) + random.uniform(-0.2, 0.2)
                if random.random() < 0.003:
                    raw_thd = random.uniform(12.0, 20.0)
                cs.thd = self._ema(raw_thd, cs.thd, alpha=0.15)
                cs.thd = max(3.0, min(25.0, cs.thd))
            else:
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
            self._ia > self._overcurrent_thresh or
            self._ib > self._overcurrent_thresh or
            self._ic > self._overcurrent_thresh,
            '_cnt_overcurrent', '_alarm_overcurrent',
        )

        # Voltage imbalance: max - min phase voltage > threshold
        v_max = max(self._va, self._vb, self._vc)
        v_min = min(self._va, self._vb, self._vc)
        _debounce(
            (v_max - v_min) > self.VOLTAGE_IMBALANCE_THRESH,
            '_cnt_voltage_imbalance', '_alarm_voltage_imbalance',
        )

        # High THD: alarm on VOLTAGE THD (IEEE-519), not current THD — %THD-i is
        # naturally high at light load, so it is not itself a fault condition.
        _debounce(
            self._v_thd > self.HIGH_THD_THRESH,
            '_cnt_high_thd', '_alarm_high_thd',
        )

        # Phase loss: any phase voltage below threshold
        _debounce(
            self._va < self.PHASE_LOSS_THRESH or
            self._vb < self.PHASE_LOSS_THRESH or
            self._vc < self.PHASE_LOSS_THRESH,
            '_cnt_phase_loss', '_alarm_phase_loss',
        )

        # Undervoltage / sag: any phase below −10 % of nominal but NOT a dead phase
        # (a lost phase near 0 V is covered by Alarm_PhaseLoss above, not double-
        # reported as a sag).
        uv_thresh = self._v_nominal * self.UNDERVOLTAGE_FRAC
        _debounce(
            (self.PHASE_LOSS_THRESH <= self._va < uv_thresh) or
            (self.PHASE_LOSS_THRESH <= self._vb < uv_thresh) or
            (self.PHASE_LOSS_THRESH <= self._vc < uv_thresh),
            '_cnt_undervoltage', '_alarm_undervoltage',
        )

        # Under-frequency: line frequency below −2 % of nominal.
        _debounce(
            self._freq < self._freq_nominal * self.UNDERFREQ_FRAC,
            '_cnt_underfrequency', '_alarm_underfrequency',
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

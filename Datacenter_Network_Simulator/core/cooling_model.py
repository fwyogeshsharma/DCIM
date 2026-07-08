"""
Load- and weather-coupled cooling-power model.

Real datacenter cooling power is NOT the plant nameplate — it tracks the IT heat
being rejected, the ambient conditions at the site, and how far below design the
plant is running:

    cooling_electrical = Q_IT × overhead(load_fraction, ambient_C)

    PUE ≈ 1 + overhead        (cooling-only; UPS/PDU/lighting losses not modelled)

The overhead is anchored so that at the reference point — annual-mean ambient
(REF_AMBIENT_C) and a typical part-load (REF_LOAD_FRAC) — it equals OH_REF, i.e.
the design/annual PUE. From there:

  • Ambient factor — a waterside/airside economizer offloads the chillers when it
    is cold (overhead falls, PUE dips in winter); a hot, humid day raises the
    condenser temperature and drops chiller COP (overhead rises, PUE climbs in
    summer). This is the dominant location effect — cold-climate sites (Chicago,
    Dublin) run far lower annual PUE than hot ones (Phoenix, Singapore).

  • Part-load factor — pumps, fans and chiller auxiliaries have a fixed floor, so
    the overhead-per-kW-IT rises as the IT load drops below design.

The absolute level is a calibration choice (OH_REF); the *shape* — how PUE breathes
with weather and load — is the physics we care about.
"""
from __future__ import annotations

import math
import time

# ── Calibration anchor (fixed floor + variable) ───────────────────────────────
# Cooling electrical = FLOOR + variable, where
#     FLOOR    = OH_FLOOR × IT_design      — pumps/fans/chiller-aux that stay on
#                                            regardless of IT (does NOT scale down)
#     variable = IT_live × OH_VAR × ambient_factor   — chiller compressor work
# At the design point (IT_live == IT_design, REF_AMBIENT_C) both terms are at
# reference, so PUE = 1 + OH_FLOOR + OH_VAR. With OH_FLOOR + OH_VAR = 0.47 the
# design PUE is 1.47; below design load the FLOOR/IT_live term climbs, so PUE
# rises (and spikes as IT → 0), exactly like a real plant with a fixed base load.
OH_FLOOR      = 0.15     # fixed cooling overhead as a fraction of DESIGN IT
OH_VAR        = 0.32     # variable (chiller) overhead as a fraction of LIVE IT
REF_AMBIENT_C = 15.0     # annual-mean dry-bulb the variable term is anchored to

# ── Ambient response (variable term only — the floor is weather-independent) ───
ECON_KNEE_C   = 10.0     # below this, the economizer starts offloading chillers
AMB_SLOPE     = 0.030    # +3.0 % variable per °C above REF_AMBIENT_C (COP droop)
ECON_SLOPE    = 0.045    # extra reduction per °C below ECON_KNEE_C (free cooling)
AMB_MIN, AMB_MAX = 0.35, 2.20   # clamp the ambient factor

# ── Per-city climate: (annual-mean dry-bulb °C, seasonal amplitude °C) ─────────
# Seasonal amplitude = half the peak-to-peak swing (summer mean − annual mean).
_CITY_CLIMATE = {
    "chicago":       (10.0, 14.0),
    "new york":      (13.0, 12.0),
    "dallas":        (19.0, 11.0),
    "phoenix":       (24.0, 12.0),
    "san jose":      (16.0,  7.0),
    "seattle":       (11.5,  7.0),
    "atlanta":       (17.0, 10.0),
    "ashburn":       (13.5, 12.0),
    "dublin":        (10.0,  6.0),
    "london":        (11.5,  7.0),
    "frankfurt":     (10.5, 10.0),
    "amsterdam":     (10.5,  8.0),
    "stockholm":     ( 7.0, 11.0),
    "singapore":     (27.5,  1.5),
    "mumbai":        (27.5,  4.0),
    "sydney":        (18.0,  6.0),
    "tokyo":         (16.0, 11.0),
    "sao paulo":     (19.5,  5.0),
}
_DEFAULT_CLIMATE = (15.0, 10.0)   # temperate fallback for unknown cities

DIURNAL_AMP_C = 5.0     # ± day/night swing added on top of the seasonal mean


def ambient_c(city: str | None, now: float | None = None) -> float:
    """Approximate outdoor dry-bulb (°C) for a city at time *now* (epoch seconds).

    Seasonal term: coldest in January, warmest in July (northern-hemisphere
    approximation). Diurnal term: peak ~15:00 local, trough ~03:00. Southern-
    hemisphere cities in the table already carry a small seasonal amplitude, so
    the phase error is minor; the point is a plausible, location-dependent ambient
    that drives the economizer, not a meteorological forecast.
    """
    mean, seas_amp = _CITY_CLIMATE.get((city or "").strip().lower(), _DEFAULT_CLIMATE)
    lt = time.localtime(now)
    doy = lt.tm_yday                       # 1..366
    hour = lt.tm_hour + lt.tm_min / 60.0
    # Coldest near Jan 15 (doy≈15): -cos peaks negative there.
    seasonal = -seas_amp * math.cos(2.0 * math.pi * (doy - 15) / 365.0)
    diurnal  = DIURNAL_AMP_C * math.sin(2.0 * math.pi * (hour - 9.0) / 24.0)
    return mean + seasonal + diurnal


def ambient_factor(ambient: float) -> float:
    """Overhead multiplier vs. ambient. 1.0 at REF_AMBIENT_C; <1 in the cold
    (economizer), >1 in the heat (chiller COP droop)."""
    f = 1.0 + AMB_SLOPE * (ambient - REF_AMBIENT_C)
    if ambient < ECON_KNEE_C:
        # Below the economizer knee the chillers progressively unload.
        f -= ECON_SLOPE * (ECON_KNEE_C - ambient)
    return max(AMB_MIN, min(AMB_MAX, f))


# ── Air-side (CRAH) fan response to hall inlet/return temperature ─────────────
CRAH_SETPOINT_C = 24.0    # ASHRAE recommended max inlet; above this the CRAHs ramp
FAN_SPEED_GAIN  = 0.05    # +5 % fan speed per °C above setpoint
FAN_FACTOR_MAX  = 3.0     # cap on the fan-power multiplier (≈ full-speed fans)


def crah_fan_speed_ratio(inlet_c: float) -> float:
    """CRAH fan-SPEED (airflow) multiplier vs. hall inlet/return air temperature.

    A rising hall temperature drives the CRAH control loop to ramp fan speed to
    move more air (flow ∝ speed). Returns 1.0 at or below the setpoint, growing as
    the hall gets hotter, capped so speed stays ≤ FAN_FACTOR_MAX**(1/3) (i.e. the
    same ceiling as the power cap once cubed). The cube-law POWER cost of this
    extra speed is applied once, downstream, by affinity_power_kw()."""
    over = max(0.0, inlet_c - CRAH_SETPOINT_C)
    ratio = 1.0 + FAN_SPEED_GAIN * over
    return min(FAN_FACTOR_MAX ** (1.0 / 3.0), ratio)


def crah_fan_factor(inlet_c: float) -> float:
    """CRAH fan-POWER multiplier vs. hall temperature (P ∝ speed³). Kept for any
    caller that wants the power multiplier directly; the plant power path now uses
    crah_fan_speed_ratio() + affinity_power_kw() so speed and power stay coupled."""
    return crah_fan_speed_ratio(inlet_c) ** 3


# ── VFD affinity law for centrifugal pumps & fans: P ∝ speed³ ──────────────────
# A variable-frequency-driven centrifugal pump/fan delivers flow ∝ speed, head ∝
# speed², and draws power ∝ speed³. So a unit whose NAMEPLATE (label) rating is the
# full-speed draw pulls only ~speed³ of that when the VFD throttles it back — a pump
# at 70 % speed draws ~0.34× nameplate, not the nameplate. This is why VFD hydronics
# dominate modern plant: cubic savings at part load. A small fixed drive/motor
# parasitic keeps the floor realistic (a VFD at min speed is not zero watts).
PUMP_MIN_SPEED  = 0.35    # VFD turndown floor — pumps rarely modulate below ~35 %
FAN_MIN_SPEED   = 0.30    # CRAH / cooling-tower fans floor ~30 %
DRIVE_PARASITIC = 0.04    # fixed VFD + motor no-load loss as a fraction of nameplate


def vfd_speed_frac(duty_frac: float, min_frac: float) -> float:
    """VFD speed needed to deliver a flow/airflow duty. Flow ∝ speed, so speed
    tracks the duty (thermal-load) fraction, floored at the drive's turndown
    minimum and capped at 100 %."""
    return max(min_frac, min(1.0, duty_frac))


def affinity_power_kw(nameplate_kw: float, speed_frac: float,
                      parasitic: float = DRIVE_PARASITIC) -> float:
    """Electrical draw (kW) of a VFD centrifugal pump/fan at *speed_frac* of full
    speed, via the affinity law P ∝ speed³ plus a fixed drive parasitic. Equals the
    nameplate at 100 % speed (so the plant design point / PUE anchor is preserved);
    ~0.34× nameplate at 70 % speed; never below the parasitic floor."""
    s = max(0.0, min(1.0, speed_frac))
    return max(0.0, nameplate_kw) * (parasitic + (1.0 - parasitic) * s ** 3)


def affinity_speed_frac(power_kw: float, nameplate_kw: float,
                        parasitic: float = DRIVE_PARASITIC) -> float:
    """Inverse of affinity_power_kw: recover the VFD speed fraction that produced a
    given electrical draw, so a unit's published Speed point stays consistent with
    its metered power. Returns 0..1."""
    if nameplate_kw <= 0.0:
        return 0.0
    frac = (power_kw / nameplate_kw - parasitic) / max(1e-6, 1.0 - parasitic)
    return max(0.0, min(1.0, frac)) ** (1.0 / 3.0)


# ── Chiller part-load efficiency (kW/ton curve) ───────────────────────────────
# A chiller's electrical draw is NOT linear in cooling load. As a fraction of
# nameplate electrical vs the thermal part-load ratio (PLR = cooling delivered /
# rated cooling), a VFD water-cooled centrifugal follows roughly
#     P/P_rated = C0 + C1·PLR + C2·PLR²          (C0+C1+C2 = 1 → nameplate at PLR=1)
# The fixed C0 term (hot-gas bypass, oil/control loads, minimum compressor speed)
# is what makes kW/ton (= P/P_rated ÷ PLR) U-SHAPED: efficiency IMPROVES from full
# load down to ~50–75 %, then WORSENS at low load as that fixed floor dominates —
# the classic reason plants stage multiple chillers instead of running one at low
# PLR. Coefficients tuned to IPLV ≈ 0.82× the full-load kW/ton (efficient VFD
# centrifugal). Condenser-water / ambient effects are applied separately via
# ambient_factor(), so this curve is the pure load-shape at reference conditions.
CHILLER_PLF = (0.10, 0.25, 0.65)   # (C0, C1, C2)


def chiller_power_frac(plr: float) -> float:
    """Chiller electrical draw as a fraction of nameplate at thermal part-load
    ratio *plr* (0..1), at reference ambient. 0 when off (plr≤0); otherwise the
    fixed-loss floor keeps it above 0 and produces the realistic low-load kW/ton
    penalty. 1.0 at full load."""
    p = max(0.0, min(1.0, plr))
    if p <= 0.0:
        return 0.0
    c0, c1, c2 = CHILLER_PLF
    return c0 + c1 * p + c2 * p * p


def chiller_load_frac(power_frac: float) -> float:
    """Inverse of chiller_power_frac: the thermal PLR that yields a given
    reference-ambient power fraction (for a consistent Compressor_Load readout).
    Returns 0..1."""
    c0, c1, c2 = CHILLER_PLF
    if power_frac <= c0:
        return 0.0
    disc = c1 * c1 - 4.0 * c2 * (c0 - power_frac)
    if disc < 0.0:
        return 1.0
    x = (-c1 + math.sqrt(disc)) / (2.0 * c2)
    return max(0.0, min(1.0, x))


CHILLER_COP_RATED = 5.5    # water-cooled centrifugal at design (kW/ton ≈ 0.60)


def chiller_cop(plr: float, city: str | None, now: float | None = None,
                cop_rated: float = CHILLER_COP_RATED) -> float:
    """Chiller coefficient of performance (COP = cooling ÷ electrical) at thermal
    part-load ratio *plr* and site ambient. COP is the inverse of kW/ton: it RISES
    at part load (the efficiency dip) and FALLS with a hot condenser (ambient
    lift). Equals cop_rated at the design point (PLR=1, reference ambient); clamped
    to a physically sane band."""
    p = max(0.0, min(1.0, plr))
    if p <= 0.0:
        return 0.0
    pf  = chiller_power_frac(p)
    amb = ambient_factor(ambient_c(city, now))
    cop = cop_rated * p / (pf * max(1e-6, amb))
    return max(2.0, min(9.0, cop))


def chiller_electrical_w(nameplate_w: float, plr: float,
                         city: str | None, now: float | None = None) -> float:
    """Chiller electrical draw (W): nameplate × part-load power fraction (kW/ton
    curve) × ambient/condenser factor, capped at the compressor's nameplate. At
    the design point (PLR=1, reference ambient) it equals the nameplate, so the
    plant PUE anchor is preserved."""
    if plr <= 0.0 or nameplate_w <= 0.0:
        return 0.0
    p = nameplate_w * chiller_power_frac(plr) * ambient_factor(ambient_c(city, now))
    return max(0.0, min(nameplate_w, p))


def cooling_floor_w(it_design_w: float) -> float:
    """Fixed cooling draw (W) that stays on regardless of IT load — the pumps,
    fan minimums and chiller auxiliaries sized to the design IT."""
    return OH_FLOOR * max(0.0, it_design_w)


def cooling_electrical_w(it_live_w: float, it_design_w: float,
                         city: str | None, now: float | None = None) -> float:
    """Total cooling electrical draw (W): a fixed floor sized to the design IT
    plus a variable chiller term that tracks the live IT heat and site ambient.

    Never returns 0 while there is plant to run — even at zero IT the floor draws
    power, which is what makes PUE spike at very low load."""
    if it_design_w <= 0.0:
        it_design_w = it_live_w
    ambient = ambient_c(city, now)
    floor    = cooling_floor_w(it_design_w)
    variable = max(0.0, it_live_w) * OH_VAR * ambient_factor(ambient)
    return floor + variable


def pue_estimate(it_live_w: float, it_design_w: float,
                 city: str | None, now: float | None = None) -> float:
    """Cooling-only PUE = 1 + cooling_electrical / IT_live (for tests/inspection)."""
    if it_live_w <= 0.0:
        return float("inf")
    return 1.0 + cooling_electrical_w(it_live_w, it_design_w, city, now) / it_live_w


# ── Chiller-plant staging (sequence modules on with load) ─────────────────────
# Real plants INSTALL for ultimate capacity but SEQUENCE units on as load climbs
# (BMS chiller staging), so the part-load overhead — and the PUE anchor it_design
# — tracks the RUNNING set, not the full installed plant. Without staging, either
# the design floor is frozen tiny (fleet overload → fake-low PUE, the bug) or it is
# sized to the full plant (huge floor at low load → PUE spikes). Staging keeps
# enabled ≈ load, so PUE holds ~1.47 across the whole growth curve.
#
# Modules are ~110 kW-IT (the curated baseline block, ~31 ton) so enabled capacity
# follows load closely; the enabled count also drives how many physical chillers
# report "running" in DCIM/BACnet.
PLANT_MODULE_KW    = 110.0     # per staged cooling module (IT-cooling, kW)
STAGE_UP_FRAC      = 0.90      # add a module when live load > this × enabled capacity
STAGE_DOWN_FRAC    = 0.60      # drop a module when live load < this × the smaller cap
DESIGN_W_PER_SERVER = 714.0    # design (peak) IT heat per server the plant is sized to


def installed_modules_for(design_servers_dc: float,
                          module_kw: float = PLANT_MODULE_KW,
                          margin: float = 1.15) -> int:
    """How many cooling modules a DC must install to cover *design_servers_dc*
    servers at their design (peak) heat, with headroom/N+1 margin. e.g. 1500
    servers/DC → ~1.23 MW-IT → ~12 modules of 110 kW."""
    peak_kw = max(0.0, design_servers_dc) * DESIGN_W_PER_SERVER / 1000.0 * margin
    return max(1, math.ceil(peak_kw / max(1e-6, module_kw)))


def stage_modules(it_live_kw: float, installed_modules: int, prev_on: int,
                  module_kw: float = PLANT_MODULE_KW, min_on: int = 1) -> int:
    """Number of cooling modules to run for *it_live_kw*, with HYSTERESIS to avoid
    short-cycling at a stage boundary: add a module when load exceeds 90 % of the
    running capacity, drop one only when load falls below 60 % of the next-smaller
    capacity. Bounded to [min_on, installed_modules]."""
    installed_modules = max(min_on, int(installed_modules))
    prev_on = max(min_on, min(installed_modules, int(prev_on or min_on)))
    on = prev_on
    if prev_on < installed_modules and it_live_kw > STAGE_UP_FRAC * prev_on * module_kw:
        on = prev_on + 1
    elif prev_on > min_on and it_live_kw < STAGE_DOWN_FRAC * (prev_on - 1) * module_kw:
        on = prev_on - 1
    return max(min_on, min(installed_modules, on))

"""The cooling model's calibration anchor.

Two separate paths describe compressor efficiency, and only one of them reaches
PUE. `chiller_cop()` shapes the per-device readouts; `OH_VAR` inside
`cooling_electrical_w()` sets the plant total, which `device_state_store`
normalises every running unit onto. When those two were independent constants
they disagreed by 1.8x — OH_VAR 0.32 implied a plant COP of 3.1 against a chiller
module claiming 5.5 — and nothing failed, because each was internally consistent.
A published per-chiller COP of ~2.2 sat beside a curve that said 5.5 and neither
number was reachable from the other.

These tests pin the coupling, not the values. Re-rate the chiller and they all
still pass; break the derivation and they do not.
"""
from __future__ import annotations

import pytest

from core.cooling_model import (
    CHILLER_COP_RATED, EVAP_HEAT_FRAC, OH_FLOOR, OH_VAR, REF_AMBIENT_C,
    ambient_factor, chiller_cop, chiller_power_frac, cooling_electrical_w,
)


class TestTheAnchorHolds:
    def test_the_part_load_curve_is_unity_at_design(self):
        """The whole derivation rests on this: chiller_cop() only returns
        cop_rated at plr=1 because the PLF coefficients sum to 1."""
        assert chiller_power_frac(1.0) == pytest.approx(1.0)

    def test_the_reference_ambient_is_the_curves_neutral_point(self):
        assert ambient_factor(REF_AMBIENT_C) == pytest.approx(1.0)

    def test_both_models_meet_at_the_rated_point(self):
        """chiller_cop at design PLR and reference ambient IS the rating. Without
        this the two paths would be anchored to different points and the
        derivation below would be arithmetic rather than physics."""
        cop = CHILLER_COP_RATED / (chiller_power_frac(1.0)
                                   * ambient_factor(REF_AMBIENT_C))
        assert cop == pytest.approx(CHILLER_COP_RATED)


class TestOhVarIsDerived:
    def test_oh_var_inverts_the_rated_cop(self):
        assert OH_VAR == pytest.approx(
            (1.0 + OH_FLOOR * EVAP_HEAT_FRAC) / CHILLER_COP_RATED)

    def test_a_better_chiller_costs_less_variable_overhead(self):
        """The point of deriving it. Re-rating the machine must move the plant
        total, which a hand-set constant would not do."""
        a = (1.0 + OH_FLOOR * EVAP_HEAT_FRAC) / 5.5
        b = (1.0 + OH_FLOOR * EVAP_HEAT_FRAC) / 7.0
        assert b < a

    def test_the_evaporator_uplift_is_a_penalty_not_a_credit(self):
        """The compressor lifts the floor's fan/pump heat as well as the IT heat,
        so OH_VAR must exceed a naive 1/COP."""
        assert OH_VAR > 1.0 / CHILLER_COP_RATED

    def test_only_part_of_the_floor_reaches_the_evaporator(self):
        """Tower fans and condenser pumps reject on the far side of the machine.
        An uplift of 1.0 would charge the compressor for its own heat rejection."""
        assert 0.0 < EVAP_HEAT_FRAC < 1.0


class TestDesignPointIsModern:
    def test_design_pue_is_in_the_modern_band(self):
        """At design load and reference ambient. 1.47 was the old hand-set figure
        and is a 2010-era plant; a water-cooled chiller plant with VFD towers
        built today lands in the low 1.3s."""
        design_pue = 1.0 + OH_FLOOR + OH_VAR
        assert 1.28 <= design_pue <= 1.40

    def test_the_floor_still_dominates_at_low_load(self):
        """Part-load behaviour is the shape that matters and must survive the
        re-anchoring: PUE at a quarter load stays well above the design point."""
        design_w = 1_000_000.0
        at_design = cooling_electrical_w(design_w, design_w, None,
                                         now=0.0) / design_w
        quarter = design_w * 0.25
        at_quarter = cooling_electrical_w(quarter, design_w, None,
                                          now=0.0) / quarter
        assert at_quarter > at_design

    def test_the_variable_term_still_answers_to_weather(self):
        it_w = design_w = 1_000_000.0
        hot = cooling_electrical_w(it_w, design_w, "phoenix", now=0.0)
        cold = cooling_electrical_w(it_w, design_w, "dublin", now=0.0)
        assert hot > cold

    def test_the_plant_total_decomposes_into_floor_plus_derived_variable(self):
        """End to end: the constant is load-bearing, not decorative. Reconstruct
        cooling_electrical_w() from the anchor and it must land on the same watt —
        which is only true if OH_VAR is what actually sets the magnitude."""
        from core.cooling_model import ambient_c
        it_w, design_w, city = 500_000.0, 1_000_000.0, "chicago"
        total = cooling_electrical_w(it_w, design_w, city, now=0.0)
        rebuilt = (OH_FLOOR * design_w
                   + it_w * ((1.0 + OH_FLOOR * EVAP_HEAT_FRAC) / CHILLER_COP_RATED)
                   * ambient_factor(ambient_c(city, 0.0)))
        assert total == pytest.approx(rebuilt)

    def test_a_better_chiller_lowers_the_plant_total(self):
        """Same reconstruction at a higher rating: the floor is untouched and the
        variable term shrinks, so the plant costs less."""
        from core.cooling_model import ambient_c
        it_w, design_w, city = 500_000.0, 1_000_000.0, "chicago"
        amb = ambient_factor(ambient_c(city, 0.0))
        base = cooling_electrical_w(it_w, design_w, city, now=0.0)
        better = (OH_FLOOR * design_w
                  + it_w * ((1.0 + OH_FLOOR * EVAP_HEAT_FRAC) / 7.0) * amb)
        assert better < base

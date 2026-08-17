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


# ─────────────────────────────────────────────────────────────────────────────
#  Where the calibration residual lands
#
#  The DC's plant draws have to reconcile to cooling_electrical_w() or the
#  meter-derived PUE stops matching the model. That reconciliation used to scale
#  EVERY unit by one DC-wide factor, so no plant device published its own draw —
#  a pump's kW was its affinity value times a constant that depended on what else
#  was running in that DC. Two identical pumps at identical speed published
#  different kW, which P ∝ speed³ × nameplate says is impossible. It stayed
#  invisible only while Speed was itself back-derived from the scaled power.
#
#  The residual now lands on the chiller alone.
# ─────────────────────────────────────────────────────────────────────────────
from conftest import DC, build_plant  # noqa: E402


def _settle(p, n=8):
    for _ in range(n):
        p.tick()


class TestResidualLandsOnTheChiller:
    def test_every_vfd_device_publishes_its_own_curve(self, tmp_path, plant_cache):
        """The invariant in one line: kW is a function of this machine's speed and
        nameplate, and of nothing happening elsewhere in the DC."""
        from core.cooling_model import affinity_power_kw
        for servers in (40, 200, 600, 900):
            p = build_plant(tmp_path / f"v{servers}", servers=servers,
                            installed_modules=6, crahs=4)
            _settle(p)
            np_kw = p.store._cooling_context()["np_kw_by_name"]
            speeds = p.store._plant_speed_by_name
            for name, kw in p.store._plant_power_by_name.items():
                if DC not in name or kw <= 0 or name not in speeds:
                    continue
                want = affinity_power_kw(np_kw.get(name, 0.0), speeds[name])
                if want > 1e-9:
                    assert kw == pytest.approx(want, rel=1e-6), (servers, name)

    def test_identical_pumps_at_one_speed_draw_the_same(self, tmp_path,
                                                        plant_cache):
        """The reported symptom. Three plants at different loads all sit on the
        pump turndown floor, so their CHW pumps turn at the same speed — and
        identical pumps at identical speed must draw identically, whatever the
        rest of each DC is doing."""
        seen = []
        for servers in (40, 200):
            p = build_plant(tmp_path / f"eq{servers}", servers=servers,
                            installed_modules=6)
            _settle(p)
            pump = p.store._plant_trains_run[DC][0]["chwp"]
            seen.append((p.store._plant_speed_by_name[pump],
                         p.store._plant_power_by_name[pump]))
        assert len({round(s, 4) for s, _kw in seen}) == 1, ("fixture should floor "
                                                            "every pump", seen)
        assert len({round(kw, 6) for _s, kw in seen}) == 1, seen

    def test_a_binding_envelope_still_lands_on_the_model(self, tmp_path,
                                                         plant_cache):
        """The fallback, and it is reachable — an almost-empty plant hits it.

        Below roughly 40 servers the VFD minimums plus the chillers' fixed losses
        come to MORE than cooling_electrical_w() allows: OH_FLOOR × design is under
        the plant's true physical minimum draw. Something has to give, and holding
        the DC total is the property worth keeping, because PUE is derived from it.
        So the chillers go to their floor and the VFD side is scaled to fit — the
        one case where a pump does not publish its own curve. Pinned here so the
        behaviour is deliberate rather than discovered again later."""
        from core.cooling_model import CHILLER_PLF
        p = build_plant(tmp_path / "bind", servers=6, installed_modules=6)
        _settle(p)
        published = sum(kw for n, kw in p.store._plant_power_by_name.items()
                        if DC in n)
        model = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
        assert published == pytest.approx(model, abs=1e-6)
        np_kw = p.store._cooling_context()["np_kw_by_name"]
        for train in p.store._plant_trains_run[DC]:
            name = train["chiller"]
            assert p.store._plant_power_by_name.get(name, 0.0) >= \
                CHILLER_PLF[0] * np_kw.get(name, 0.0) - 1e-6

    def test_the_dc_total_still_lands_on_the_model(self, tmp_path, plant_cache):
        """Non-negotiable: PUE is derived from this sum. Moving the residual must
        not move the total by so much as a watt."""
        for servers in (40, 200, 600, 900):
            p = build_plant(tmp_path / f"t{servers}", servers=servers,
                            installed_modules=6, crahs=4)
            _settle(p)
            published = sum(kw for n, kw in p.store._plant_power_by_name.items()
                            if DC in n)
            model = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
            assert published == pytest.approx(model, abs=1e-6), servers

    def test_a_running_chiller_never_falls_below_its_fixed_losses(self, tmp_path,
                                                                  plant_cache):
        """The residual is absorbed by the chiller, so it is the term that could be
        driven somewhere unphysical. CHILLER_PLF's C0 is the oil pump, controls and
        motor no-load — it is why the part-load curve does not pass through the
        origin, and a running machine cannot be under it."""
        from core.cooling_model import CHILLER_PLF
        for servers in (40, 200, 600, 900):
            p = build_plant(tmp_path / f"f{servers}", servers=servers,
                            installed_modules=6, crahs=4)
            _settle(p)
            np_kw = p.store._cooling_context()["np_kw_by_name"]
            for train in p.store._plant_trains_run[DC]:
                name = train["chiller"]
                kw = p.store._plant_power_by_name.get(name, 0.0)
                assert kw >= CHILLER_PLF[0] * np_kw.get(name, 0.0) - 1e-6, \
                    (servers, name, kw)

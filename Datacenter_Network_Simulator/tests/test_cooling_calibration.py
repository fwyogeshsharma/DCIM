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
        for servers in (1, 6, 40, 120):
            p = build_plant(tmp_path / f"eq{servers}", servers=servers,
                            installed_modules=6)
            _settle(p)
            pump = p.store._plant_trains_run[DC][0]["chwp"]
            seen.append((p.store._plant_speed_by_name[pump],
                         p.store._plant_power_by_name[pump]))
        assert len({round(s, 4) for s, _kw in seen}) == 1, ("fixture should floor "
                                                            "every pump", seen)
        assert len({round(kw, 6) for _s, kw in seen}) == 1, seen

    def test_the_plants_minimum_draw_fits_inside_the_modelled_floor(
            self, tmp_path, plant_cache):
        """An almost-empty plant must still publish honest per-device draws.

        The fallback that scales the VFD side exists for when the devices' physical
        minimum exceeds cooling_electrical_w(), and reaching it means the plant
        cannot be described by the model at all. It fired here once, on nameplates
        that were not physical — a chiller at COP 1.83 has a fixed-loss floor
        (a FRACTION of nameplate) three times what a real machine's would be, and
        that read exactly like OH_FLOOR being too low. It is not: with physical
        nameplates the minimum draw sits well inside the floor at every load."""
        from core.cooling_model import affinity_power_kw
        p = build_plant(tmp_path / "minimum", servers=1, installed_modules=6)
        _settle(p)
        np_kw = p.store._cooling_context()["np_kw_by_name"]
        speeds = p.store._plant_speed_by_name
        for name, kw in p.store._plant_power_by_name.items():
            if DC not in name or kw <= 0 or name not in speeds:
                continue
            want = affinity_power_kw(np_kw.get(name, 0.0), speeds[name])
            if want > 1e-9:
                assert kw == pytest.approx(want, rel=1e-6), (
                    f"{name} was scaled — the envelope bound at near-zero load")

    def test_the_fixtures_chiller_nameplate_is_physical(self, tmp_path,
                                                        plant_cache):
        """Guards the root cause directly, because it is invisible from the tests it
        breaks. The chiller's ELECTRICAL nameplate has to be its cooling capacity
        divided by a believable COP — a fixture whose plant could not exist produces
        findings about the model that are really findings about the fixture."""
        from core.cooling_model import PLANT_MODULE_KW
        installed_mods = 6
        p = build_plant(tmp_path / "np", servers=200,
                        installed_modules=installed_mods)
        _settle(p)
        np_kw = p.store._cooling_context()["np_kw_by_name"]
        chillers = [t["chiller"] for t in p.store._cooling_context()
                    ["trains_by_dc"][DC]]
        cap_each = installed_mods * PLANT_MODULE_KW / len(chillers)
        for name in chillers:
            implied_cop = cap_each / max(1e-6, np_kw.get(name, 0.0))
            assert 3.0 <= implied_cop <= 8.0, (name, implied_cop)

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


class TestRoomLevelGearAnswersToItsRoom:
    """Prerequisites for inverting the top-down power model.

    `lf` — cooling ELECTRICAL over running plant nameplate — used to set CRAH fan
    duty and valve draw. Both are wrong on their own terms: a CRAH is sized to the
    heat in ITS hall and knows nothing about the plant's electrical bill, and an
    actuator draws its rating whenever energised. Both were also circular, because
    `lf` derives from the top-down cooling total that these devices' own draws are
    supposed to add up to. Nothing can be summed bottom-up until they answer to
    something upstream of the total.
    """

    def test_a_hotter_hall_runs_its_own_fans_harder(self, tmp_path, plant_cache):
        """The duty is this room's heat over the cooling its CRAHs are rated for, so
        fan speed has to rise with the hall's load. Under `lf` two rooms on one
        plant got identical duty however their loads differed."""
        seen = []
        for servers in (40, 200, 400, 900):
            p = build_plant(tmp_path / f"crah{servers}", servers=servers,
                            installed_modules=6, crahs=8)
            _settle(p)
            crah = sorted(n for n in p.store._plant_speed_by_name
                          if "CRAH" in n and DC in n)[0]
            seen.append((p.store._it_live_by_dc[DC],
                         p.store._plant_speed_by_name[crah]))
        assert all(b[1] >= a[1] for a, b in zip(seen, seen[1:])), seen
        assert seen[-1][1] > seen[0][1], "fan duty must actually respond to heat"

    def test_crah_duty_is_not_the_plant_electrical_ratio(self, tmp_path,
                                                         plant_cache):
        """Pins the basis, not the value. Room heat ÷ rated room capacity is a
        THERMAL ratio; reconstruct it and the published speed must follow it rather
        than the plant's electrical duty."""
        from core.cooling_model import FAN_MIN_SPEED
        p = build_plant(tmp_path / "basis", servers=900, installed_modules=6,
                        crahs=8)
        _settle(p)
        room_cap_kw = 8 * 100.0                      # 8 × Liebert PCW 100kW
        thermal = p.store._it_live_by_dc[DC] / 1000.0 / room_cap_kw
        crah = sorted(n for n in p.store._plant_speed_by_name
                      if "CRAH" in n and DC in n)[0]
        speed = p.store._plant_speed_by_name[crah]
        # Speed is the thermal ratio times the inlet-temp ramp, floored at the fan
        # turndown and capped at full speed. Deliberately NOT compared against
        # _plant_duty: the electrical ratio is not guaranteed to sit either side of
        # the thermal one, so that comparison would pass or fail by coincidence.
        lo = min(1.0, max(thermal, FAN_MIN_SPEED))
        hi = min(1.0, max(thermal * 3.0, FAN_MIN_SPEED))    # ramp is capped at ×3
        assert lo - 1e-6 <= speed <= hi + 1e-6, (speed, thermal, lo, hi)

    def test_the_fixture_crahs_carry_a_catalog_sku(self, tmp_path, plant_cache):
        """Guards the branch, not the behaviour. cooling_capacity_w() returns 0 for
        an unknown model and the duty falls back to `lf` — so a fixture without a
        real SKU would pass every test above while exercising the legacy path."""
        from core.device_manager import cooling_capacity_w
        p = build_plant(tmp_path / "sku", servers=200, installed_modules=6, crahs=4)
        _settle(p)
        for dev in p.store._dm._devices.values():
            if getattr(dev, "device_type").value == "crah":
                assert cooling_capacity_w(getattr(dev, "model_name", "")) > 0, \
                    dev.name

    def test_a_valve_never_draws_a_share_of_the_plant_total(self, tmp_path,
                                                            plant_cache):
        """An actuator holding position is a fixed load, so its draw is its rating.

        Honest about what this can check: the header valves carry a ZERO nameplate
        in this fixture and in the shipped topology alike, so both the old share and
        the new rating come to 0.0 W and no arithmetic distinguishes them. The change
        is a decoupling one — it removes total_w from the valve branch, so the plant
        sum stops being an input to one of its own terms — and it is a no-op on
        today's numbers. What is testable is that a valve never picks up draw it has
        no nameplate for, which the share form would have given it the moment a valve
        SKU gained a rating."""
        p = build_plant(tmp_path / "valve", servers=400, installed_modules=6,
                        valves=True)
        _settle(p)
        np_kw = p.store._cooling_context()["np_kw_by_name"]
        for dev in p.store._dm._devices.values():
            if dev.device_type.value != "valve":
                continue
            published = p.store._plant_power_by_name.get(dev.name, 0.0)
            assert published == pytest.approx(np_kw.get(dev.name, 0.0), abs=1e-9), \
                dev.name


class TestCoolingIsSummedFromTheDevices:
    """The inversion. A DC's cooling figure is what its plant DRAWS, not a target
    the plant is fitted to.

    cooling_electrical_w() used to set the magnitude and every running unit was
    scaled so the DC summed to it. That is backwards from how a plant meters, and
    it is why a calibration constant could disagree with the device curves for
    months without failing anything — each side was internally consistent on its
    own terms. The envelope survives as a design reference and as the fallback duty
    anchor; it no longer decides what anything draws.
    """

    def test_the_dc_total_is_exactly_the_sum_of_its_devices(self, tmp_path,
                                                            plant_cache):
        for servers in (40, 200, 600, 900):
            p = build_plant(tmp_path / f"sum{servers}", servers=servers,
                            installed_modules=6, crahs=8)
            _settle(p)
            devices = sum(kw for n, kw in p.store._plant_power_by_name.items()
                          if DC in n)
            total = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
            assert total == pytest.approx(devices, abs=1e-9), servers

    def test_stopping_plant_can_only_reduce_cooling(self, tmp_path, plant_cache):
        """Structural, not behavioural. Under the old split the metered figure and
        the model term were independent and had to be taught to collapse together;
        the live campaign caught them diverging, with cooling RISING 130.2 -> 133.6
        kW while every CHW pump was faulted. Summed from the devices, a machine that
        stops drawing cannot raise the total — the failure mode no longer exists to
        be regressed."""
        from core.device_state_store import _plant_state_cache
        for prefix in ("CHWP", "CHL", "CT", "CRAH"):
            # Per ITERATION, not just per test. The cache is module-level, so last
            # round's faults survive into this one — and they do not merely add
            # noise: faulted CHW pumps shed the chillers on the evaporator flow
            # interlock, so the next baseline is measured on an already-collapsed
            # plant and the assertion compares a number with itself.
            _plant_state_cache.clear()
            p = build_plant(tmp_path / f"fault{prefix}", servers=600,
                            installed_modules=6, crahs=8)
            _settle(p, 10)
            before = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
            hit = [n for n in p.store._plant_power_by_name
                   if DC in n and n.upper().startswith(prefix)]
            assert hit, f"fixture should build {prefix} gear"
            for name in hit:
                _plant_state_cache[name] = {"Alarm_Fault": 1.0}
            _settle(p, 6)
            after = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
            assert after < before, (prefix, before, after)

    def test_the_envelope_no_longer_sets_the_total(self, tmp_path, plant_cache):
        """cooling_electrical_w() is a design reference now. If the total still
        tracked it they would be equal, and the inversion would be cosmetic — so
        this asserts they genuinely differ at part load, where the envelope's fixed
        OH_FLOOR term exceeds what throttled VFD gear actually draws."""
        p = build_plant(tmp_path / "env", servers=200, installed_modules=6,
                        crahs=8)
        _settle(p)
        from core.cooling_model import PLANT_MODULE_KW
        itl_w = p.store._it_live_by_dc.get(DC, 0.0)
        itd_w = p.store._plant_stage_on.get(DC, 1) * PLANT_MODULE_KW * 1000.0
        envelope = cooling_electrical_w(itl_w, itd_w, "chicago") / 1000.0
        total = p.store._cool_model_w_by_dc.get(DC, 0.0) / 1000.0
        assert total < envelope, (total, envelope)

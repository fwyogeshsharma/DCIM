"""Lead/lag must not chatter — the condenser limit is not a machine fault.

Found on the live sim while watching a genset restore. With the towers dark the
condenser loop coasts up to ~38 °C, so whichever chiller is lead unloads on head
pressure. That condition was published as Alarm_HighPressure, lead selection read
it as a health fault, and the standby was promoted — into the SAME hot loop, which
raised the same alarm on the new lead one tick later while the demoted machine went
quiet and looked healthy again. Measured on DC2: CHL1↔CHL2 every 2–3 s for the whole
restore, each swap a real compressor start from 0 % load, ending only when the tower
caught up and the condition cleared by itself. Mechanical draw collapsed to the
pump/tower floor on every swap tick.

Two fixes, pinned here:

  * the limit band annunciates on its own point (Alarm_CondPressLimit — Trane
    "Condenser Pressure Limit", York "Discharge Pressure Limit"), exempt from the
    health test, while Alarm_HighPressure keeps the CUTOUT meaning: latched, off,
    manual reset. A shared-loop condition can no longer demote anything.
  * a minimum-run / anti-recycle hold on the lead, so no future condition of that
    shape can chatter the plant either. Health still outranks it, so a machine that
    genuinely dies hands over on the same tick.

The single-train fixture the rest of the suite uses could never see this — with
nothing to swap to, a flap is invisible.
"""
import pytest

from tests.conftest import DC, build_plant
from tests.test_utility_board_fault import _wire_electrical
from core.device_state_store import DeviceStateStore as Store


def _tick(fx, e):
    """The ticker's real order, including the electrical steps build_plant skips."""
    s = fx.store
    s._compute_leak_heat()
    s._compute_cond_loop()
    s._compute_chw_penalty()
    s._step_transfer()
    s._compute_unpowered_loads()
    s._compute_power_flow()
    s._compute_chw_loop()
    for d in e.values():
        s._step_ext_state(d)


def _lead(fx):
    return [t["chiller"] for t in fx.store._plant_trains_run.get(DC, []) if t.get("chiller")]


@pytest.fixture
def plant2(tmp_path, plant_cache):
    """Two complete trains on a live electrical ladder — the smallest plant that
    can flap. installed_modules=6 keeps one train lead and one on standby."""
    fx = build_plant(tmp_path, trains=2, servers=40, crahs=2, tick_interval=1.0)
    e = _wire_electrical(fx)
    for _ in range(5):
        _tick(fx, e)
    return fx, e, plant_cache


# ── The health predicate ─────────────────────────────────────────────────────
class TestCondLimitIsNotAFault:
    def test_limit_is_not_alarmed(self, plant_cache):
        """Running and unloading is a healthy machine — it stays pickable."""
        plant_cache["CHL1"] = {"Chiller_Running": 1.0, "Alarm_CondPressLimit": 1.0}
        assert not Store._is_alarmed("CHL1")

    def test_limit_is_not_a_cooling_loss(self, plant_cache):
        """The shortfall is already booked by the thermal model; counting the
        annunciation too would double-charge it (see _CAPACITY_ALARMS)."""
        plant_cache["CHL1"] = {"Chiller_Running": 1.0, "Alarm_CondPressLimit": 1.0}
        assert not Store._is_faulted("CHL1")

    def test_the_cutout_still_is_a_fault(self, plant_cache):
        """Alarm_HighPressure keeps its meaning: latched out, machine off."""
        plant_cache["CHL1"] = {"Chiller_Running": 0.0, "Alarm_HighPressure": 1.0}
        assert Store._is_alarmed("CHL1")
        assert Store._is_faulted("CHL1")


# ── Lead selection ───────────────────────────────────────────────────────────
class TestLeadDoesNotChatter:
    def test_lead_holds_through_a_condenser_limit(self, plant2):
        """The exact live shape: the lead is in limit, the standby is not."""
        fx, e, cache = plant2
        lead0 = _lead(fx)
        assert lead0, "fixture produced no lead train"

        for _ in range(20):
            for n in lead0:
                cache.setdefault(n, {}).update(
                    {"Chiller_Running": 1.0, "Alarm_CondPressLimit": 1.0})
            _tick(fx, e)
            assert _lead(fx) == lead0, "lead swapped over a shared-loop condition"

    def test_lead_hands_over_on_a_real_trip(self, plant2):
        """The control: a latched cutout must still fail over, and fast."""
        fx, e, cache = plant2
        lead0 = _lead(fx)
        for n in lead0:
            cache.setdefault(n, {}).update(
                {"Chiller_Running": 0.0, "Alarm_HighPressure": 1.0})
        _tick(fx, e)
        assert _lead(fx) and _lead(fx) != lead0, "a tripped chiller kept the lead"

    def test_no_swap_back_when_the_trip_clears(self, plant2):
        """Anti-recycle: the recovered machine does not displace the one that took
        over. Two compressor starts to end up where we already were is exactly what
        the timer exists to prevent."""
        fx, e, cache = plant2
        lead0 = _lead(fx)
        for n in lead0:
            cache.setdefault(n, {}).update(
                {"Chiller_Running": 0.0, "Alarm_HighPressure": 1.0})
        _tick(fx, e)
        lead1 = _lead(fx)
        assert lead1 != lead0

        for n in lead0:                       # operator resets the trip
            cache[n] = {"Chiller_Running": 0.0, "Alarm_HighPressure": 0.0}
        for _ in range(30):
            _tick(fx, e)
            assert _lead(fx) == lead1, "swapped back to the recovered machine"


    def test_a_condition_that_follows_the_lead_cannot_chatter(self, plant2):
        """The general shape, with the condenser limit standing in for any fault a
        machine only shows while it is the one running: alarm whichever chiller
        currently holds the lead and clear it on the peer, every tick.

        One handover is correct — the BMS tries the standby. After that the demoted
        machine is inside its anti-recycle window and is NOT startable, so the plant
        keeps the running machine rather than short-cycling its peer. Without the
        timer this alternates forever, one compressor start per tick.
        """
        fx, e, cache = plant2
        chillers = [t["chiller"] for t in fx.store._cooling_context()["trains_by_dc"][DC]]
        assert len(chillers) >= 2, "need two trains to chatter"

        swaps, prev = [], _lead(fx)
        for i in range(40):
            cur = _lead(fx)
            for n in chillers:                     # the fault follows the lead
                cache.setdefault(n, {}).update(
                    {"Chiller_Running": 1.0,
                     "Alarm_FlowLoss": 1.0 if n in cur else 0.0})
            _tick(fx, e)
            new = _lead(fx)
            if new and prev and new != prev:
                swaps.append((i, prev, new))
            if new:
                prev = new
        assert len(swaps) <= 1, f"lead chattered on a lead-following fault: {swaps}"


class TestTheLimitReachesTheWire:
    """The point has to survive the rest of the tick, not just be computed.

    First cut of this fix put Alarm_CondPressLimit in _CAPACITY_ALARMS only, and
    _compute_chw_loop clears every key in that set at the top of its pass because it
    OWNS the evaporator-side ones. It runs AFTER the condenser pass, so it deleted
    the limit one step after it was published: the alarm existed in the model, the
    BMS behaved correctly, and nothing was ever visible on BACnet.
    """

    def _limit_band(self, fx):
        """Put the live condenser loop inside the limit band without waiting for a
        hot day: drop the threshold under it, keep the cutout far above."""
        fx.store._COND_LIMIT_C, fx.store._COND_LIMIT_MARGIN_C = 5.0, -100.0
        fx.store._COND_TRIP_C, fx.store._COND_TRIP_MARGIN_C = 99.0, 100.0

    def test_limit_is_published_after_a_full_tick(self, plant2):
        fx, e, _cache = plant2
        lead = _lead(fx)[0]
        ip = fx.store._plant_ip_by_name[lead]
        assert not fx.store._plant_auto_points.get(ip, {}).get("Alarm_CondPressLimit")

        self._limit_band(fx)
        _tick(fx, e)
        pts = fx.store._plant_auto_points.get(ip, {})
        assert pts.get("Alarm_CondPressLimit") == 1.0, "limit never reached the wire"
        assert not pts.get("Alarm_HighPressure"), "limit must not assert the cutout"

    def test_the_chw_pass_still_clears_its_own_alarms(self, plant2):
        """The other half of the split: the evaporator alarms must still be owned
        and cleared by the pass that derives them, or a cleared one latches."""
        fx, e, _cache = plant2
        ip = fx.store._plant_ip_by_name[_lead(fx)[0]]
        fx.store._plant_auto_points.setdefault(ip, {})["Alarm_HighCHWSupply"] = 1.0
        _tick(fx, e)
        assert not fx.store._plant_auto_points.get(ip, {}).get("Alarm_HighCHWSupply")


class TestGensetRestore:
    def test_lead_is_stable_across_a_restore(self, plant2):
        """End to end: kill the utility board, ride the transfer, and count lead
        changes through the staged mechanical restart. Before the fix this logged a
        swap every 2–3 s for the whole restore window."""
        fx, e, _cache = plant2
        swaps, prev = [], _lead(fx)
        fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
        for i in range(90):
            _tick(fx, e)
            cur = _lead(fx)
            if cur and prev and cur != prev:
                swaps.append((i, prev, cur))
            if cur:
                prev = cur
        assert fx.store._transfer.status(DC).state == "emergency", "never transferred"
        assert fx.store._transfer.status(DC).mech_blocks_on == 3, "plant never restored"
        assert not swaps, f"lead chattered during the restore: {swaps}"

    def test_restore_does_not_recycle_compressors(self, plant2):
        """Same run, stated as the thing an operator would be billed for: the lead
        machine's continuous-run timer never resets, i.e. it never stopped."""
        fx, e, _cache = plant2
        fx.store.set_swgr_condition(e["swgr"].id, "bus_fault", True)
        for _ in range(90):
            _tick(fx, e)
        lead = _lead(fx)
        assert lead
        held = fx.store._train_lead_s.get(lead[0], 0.0)
        assert held >= 85.0, f"lead run timer reset mid-restore ({held:.0f}s of 90)"

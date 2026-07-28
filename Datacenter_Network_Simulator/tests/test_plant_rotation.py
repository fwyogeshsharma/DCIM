"""Lead/lag rotation and the health predicate that decides who may be picked.

Two live bugs are pinned here:

  * ranking on RAW run-hours swapped the lead every tick (the running unit is the
    only one accruing them, so it instantly falls behind an idle peer), and
  * judging candidate health with `_is_faulted` — which counts "stopped" as a
    fault — disqualified every standby unit for being on standby, so the lead
    could never hand over at all.
"""
import pytest

from core.cooling_model import rotation_rank
from core.device_state_store import DeviceStateStore as Store

ROTATE_H = 168.0
TICK_S = 1.0
UNITS = ["CHL1", "CHL2", "CHL3"]


def order(hours, running, rotate_h=ROTATE_H):
    """Rank the units the way the store does: rotation key, then unit index."""
    return [u for _, u in sorted(
        enumerate(UNITS),
        key=lambda i_u: (*rotation_rank(hours[i_u[1]], i_u[1] in running, rotate_h),
                         i_u[0]))]


def run_ticks(ticks, rotate_h=ROTATE_H, n_run=1):
    """Drive the selection for `ticks`, accruing runtime only for the lead."""
    hours = {u: 0.0 for u in UNITS}
    lead, handovers = set(), []
    for _ in range(ticks):
        new_lead = set(order(hours, lead, rotate_h)[:n_run])
        if lead and new_lead != lead:
            handovers.append(sorted(new_lead))
        lead = new_lead
        for u in lead:
            hours[u] += TICK_S / 3600.0
    return hours, handovers


class TestRotationRank:
    def test_running_unit_outranks_an_idle_peer_in_the_same_period(self):
        assert rotation_rank(10.0, True, ROTATE_H) < rotation_rank(0.0, False, ROTATE_H)

    def test_a_full_period_of_extra_runtime_loses_the_lead(self):
        assert rotation_rank(ROTATE_H, True, ROTATE_H) > rotation_rank(0.0, False, ROTATE_H)

    def test_sub_period_hours_do_not_reorder(self):
        """The chatter bug: any difference in raw hours used to flip the order."""
        assert rotation_rank(0.5, True, ROTATE_H) == rotation_rank(120.0, True, ROTATE_H)

    def test_zero_period_disables_rotation_without_dividing_by_zero(self):
        assert rotation_rank(999.0, True, 0.0) == (0, False)

    def test_negative_hours_are_floored(self):
        assert rotation_rank(-5.0, True, ROTATE_H) == (0, False)


class TestLeadStability:
    def test_boot_lead_is_deterministic(self):
        _hours, _handovers = run_ticks(1)
        assert order({u: 0.0 for u in UNITS}, set())[0] == "CHL1"

    def test_lead_holds_for_a_whole_period(self):
        _hours, handovers = run_ticks(2000)          # well under 168 h of runtime
        assert handovers == []

    def test_duty_stays_on_the_lead_while_it_holds(self):
        hours, _handovers = run_ticks(2000)
        assert hours["CHL1"] > 0
        assert hours["CHL2"] == 0 and hours["CHL3"] == 0


class TestHandover:
    def test_lead_hands_over_after_a_full_period(self):
        ticks = int(ROTATE_H * 3600 / TICK_S) + 5
        _hours, handovers = run_ticks(ticks, rotate_h=ROTATE_H)
        assert handovers, "lead never rotated"
        assert handovers[0] == ["CHL2"]

    def test_duty_spreads_across_the_fleet(self):
        """Short period so several handovers land inside the run."""
        short = 0.02
        ticks = int(short * 3600 / TICK_S) * 3 + 10
        hours, handovers = run_ticks(ticks, rotate_h=short)
        assert len(handovers) >= 2
        assert all(h > 0 for h in hours.values())

    def test_handover_is_not_chatter(self):
        """One swap per period, not one per tick."""
        short = 0.02
        periods = 3
        ticks = int(short * 3600 / TICK_S) * periods + 10
        _hours, handovers = run_ticks(ticks, rotate_h=short)
        assert len(handovers) <= periods + 1


class TestHealthPredicates:
    """`_is_faulted` answers "is this costing us cooling?" — stopped counts.
    `_is_alarmed` answers "can the BMS pick this unit?" — stopped does not.
    Sharing one answer between the two questions broke rotation."""

    def test_alarm_means_both_faulted_and_alarmed(self, plant_cache):
        plant_cache["CHL1"] = {"Chiller_Running": 1.0, "Alarm_HighPressure": 1.0}
        assert Store._is_faulted("CHL1")
        assert Store._is_alarmed("CHL1")

    def test_stopped_is_faulted_but_not_alarmed(self, plant_cache):
        plant_cache["CHL1"] = {"Chiller_Running": 0.0, "Alarm_HighPressure": 0.0}
        assert Store._is_faulted("CHL1"), "a stopped unit is a cooling loss"
        assert not Store._is_alarmed("CHL1"), "a standby unit is healthy and pickable"

    def test_healthy_running_unit_is_neither(self, plant_cache):
        plant_cache["CHL1"] = {"Chiller_Running": 1.0, "Alarm_HighPressure": 0.0}
        assert not Store._is_faulted("CHL1")
        assert not Store._is_alarmed("CHL1")

    def test_unknown_device_is_neither(self, plant_cache):
        assert not Store._is_faulted("NOPE")
        assert not Store._is_alarmed("NOPE")

    def test_stopped_tower_cell_stays_available(self, plant_cache):
        """A cycled-off cell reads Fan_Status 0. Judging it with _is_faulted
        dropped it from the bank, which oscillated every tick."""
        plant_cache["CT2"] = {"Fan_Status": 0.0, "Alarm_HighVibration": 0.0}
        assert not Store._is_alarmed("CT2")

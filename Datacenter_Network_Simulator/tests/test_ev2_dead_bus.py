"""A current transformer on a de-energized bus reads zero.

`through` is what the model says WOULD flow through a panel. On the standby
paralleling board that is the load its transfer switches would pick up if it
were carrying - the whole site. Published to the EV2 raw, the meter on the
generator-room board read 189.6 kW with both gensets stopped, its phase
currents copied from the utility board, while the board's own Digitrip trip
unit a metre away read 0 kW and `swgr_bus_status: dead`.

Two instruments on one bus, disagreeing by the site's entire load, and the
plausible one was the wrong one. A CT measures the field around a conductor:
no current, no field, no reading. That is also what makes it the instrument an
operator uses to prove a board is safe to work on, so a meter that reports the
load an open breaker is NOT passing is worse than a meter that reports nothing.
"""

import pytest

from core.device_state_store import DeviceStateStore


@pytest.fixture()
def store():
    """The panel-kW helper off an otherwise uninitialised store.

    It reads `self._energized` and its arguments and nothing else, which is the
    point of testing it directly: the alternative is a whole plant, a transfer
    sequence and a tick loop to prove a gate.
    """
    s = DeviceStateStore.__new__(DeviceStateStore)
    s._energized = {}
    # `_active_parents` is the cascade's own rule for which feeder is carrying
    # a node right now. Stubbed from a dict so a test can say "this ATS is on
    # its normal source" without standing up a transfer sequence.
    s._active = {}
    s._active_parents = lambda nid, ctx: s._active.get(nid, [])
    return s


THROUGH = {
    "swgr-utility": 189_861.0,     # the live board
    "swgr-parallel": 189_641.0,    # the standby board, gensets stopped
    "rpp-a": 36_459.0,
    "pdu-a": 8_250.0,
}


def test_the_standby_board_reads_nothing(store):
    """The bug, with the numbers it shipped."""
    store._energized = {"swgr-utility": True, "swgr-parallel": False}
    assert store._ev2_panel_kw("swgr-parallel", THROUGH) == 0.0


def test_the_live_board_still_reads(store):
    """The gate must not silence the meter that is doing its job."""
    store._energized = {"swgr-utility": True, "swgr-parallel": False}
    assert store._ev2_panel_kw("swgr-utility", THROUGH) == pytest.approx(189.861)


def test_a_panel_nobody_energized_is_assumed_live(store):
    """Unknown is not dead.

    A panel that never made it into the energization map - one not wired into
    the power graph at all - keeps reading. Defaulting the other way would
    blank every meter in a topology with no modelled electrical upstream, which
    is a regression dressed as a safety check.
    """
    assert store._ev2_panel_kw("rpp-a", THROUGH) == pytest.approx(36.459)


def test_a_dark_branch_reads_zero_while_its_panel_carries_on(store):
    """A tripped branch breaker reads zero on its CT while the rest of the
    panel carries on - which is exactly what the channel is there to show, and
    how the trip is told apart from a panel-wide loss."""
    store._energized = {"rpp-a": True, "pdu-a": False}
    store._active = {"pdu-a": ["rpp-a"]}
    assert store._ev2_branch_kw("rpp-a", "pdu-a", THROUGH, {}) == 0.0


# ------------------------------------------------- the branch a board is not
# carrying
#
# The bug that survived the first fix. The paralleling board's EV2 has its CTs
# on the two transfer switches the board feeds, and each channel reported the
# ATS's WHOLE throughput - 110.2 kW and 77.1 kW - while both switches sat on
# their normal source. The panel line summed them to 187.2 kW, within 0.2 kW
# of the live utility board beside it.


def test_a_branch_carried_by_the_other_source_reads_zero(store):
    """A transfer switch has two sources and draws from exactly one."""
    store._energized = {"swgr-parallel": True, "ats-1": True}
    store._active = {"ats-1": ["swgr-utility"]}        # sitting on normal
    kw = store._ev2_branch_kw("swgr-parallel", "ats-1",
                              {"ats-1": 110_187.0}, {})
    assert kw == 0.0


def test_the_board_that_is_carrying_it_reads_all_of_it(store):
    store._energized = {"swgr-utility": True, "ats-1": True}
    store._active = {"ats-1": ["swgr-utility"]}
    kw = store._ev2_branch_kw("swgr-utility", "ats-1",
                              {"ats-1": 110_187.0}, {})
    assert kw == pytest.approx(110.187)


def test_a_genuinely_dual_fed_branch_splits(store):
    """Two meters on the two sides must not both claim the whole load.

    The cascade halves a dual-corded load between its feeders; a CT on each
    side sees its half, and the two readings add up to the load rather than to
    twice it.
    """
    store._energized = {"rpp-a": True, "rpp-b": True, "pdu-a": True}
    store._active = {"pdu-a": ["rpp-a", "rpp-b"]}
    a = store._ev2_branch_kw("rpp-a", "pdu-a", THROUGH, {})
    b = store._ev2_branch_kw("rpp-b", "pdu-a", THROUGH, {})
    assert a == b == pytest.approx(8.250 / 2)
    assert a + b == pytest.approx(8.250)


def test_a_branch_with_no_modelled_feeders_still_reads(store):
    """Unwired is not un-metered.

    A branch that was never put in the power graph has no active parent to
    consult; blanking it would silence a meter that has no way to know better.
    """
    store._energized = {"rpp-a": True, "pdu-a": True}
    store._active = {}
    assert store._ev2_branch_kw("rpp-a", "pdu-a", THROUGH, {}) == pytest.approx(8.250)


def test_a_live_panel_with_no_load_is_not_a_dead_one(store):
    """Zero from an energized bus is a real reading of no load.

    It arrives at the same number by a different route, and the distinction
    matters upstream: the energy accumulator is still running on a live bus at
    no load, and stopped on a dead one.
    """
    store._energized = {"empty": True}
    assert store._ev2_panel_kw("empty", {}) == 0.0

"""The panel schedule on the CT channels.

A branch-circuit monitor ships with its channels called Ckt01..Ckt42 and
nothing else. Which breaker each CT is clamped to is programmed in at
commissioning, and every real BCM stores it - Veris E30, Packet Power and
Verdigris all do - because that schedule is the only thing that lets a client
turn 42 anonymous numbers into "this rack is drawing 8.2 kW".

Uncommissioned, this meter published `Ckt01_kW` / "Circuit 1 Active Power" and
a DCIM polling it could read every branch and attribute none of them. Worse on
the switchgear meters, where Ckt01 is a transfer switch carrying 110 kW.
"""

import pytest

from core.bacnet_object_model import OBJ_ANALOG_INPUT
from simulator.bacnet_device import EV2BACnetDevice


@pytest.fixture()
def meter():
    """A meter with no socket.

    `__new__` + the object tree: binding a UDP port to check what a
    description says would make this an integration test of the network stack.
    """
    from core.bacnet_ev2_generator import build_ev2_object_tree

    d = EV2BACnetDevice.__new__(EV2BACnetDevice)
    d.circuits = 4
    d._objects = build_ev2_object_tree(40001, "Verdigris_EV2_40001", circuits=4)
    d._channel_labels = []
    return d


def desc(meter, ckt, offset=2):
    """The description of one channel's point (offset 2 = Active Power)."""
    return meter._objects[(OBJ_ANALOG_INPUT, (ckt + 1) * 1000 + offset)].description


def test_a_clamped_channel_names_its_branch(meter):
    meter.commission_channels(["PDUA-DC1-HA-R2-01", "PDUA-DC1-HA-R2-02", None, None])
    assert desc(meter, 1) == "Circuit 1 Active Power — PDUA-DC1-HA-R2-01"
    assert desc(meter, 2) == "Circuit 2 Active Power — PDUA-DC1-HA-R2-02"


def test_a_spare_way_says_so(meter):
    """A blank label is not the same as an unlabelled channel.

    "Spare" is what a panel schedule says about a way with no load on it, and
    it is how a reader tells a spare CT from one whose schedule was never
    written.
    """
    meter.commission_channels(["PDUA-DC1-HA-R2-01", None, None, None])
    assert desc(meter, 2) == "Circuit 2 Active Power — Spare"
    assert desc(meter, 4) == "Circuit 4 Active Power — Spare"


def test_every_quantity_on_a_channel_carries_the_label(meter):
    """Current, kW, kWh, PF and THD are one CT, not five.

    A client that maps on the kW point and reads the current point must not
    find them attributed to different branches.
    """
    meter.commission_channels(["ATS1-DC1-UR", None, None, None])
    for offset in (1, 2, 3, 4, 5):
        assert desc(meter, 1, offset).endswith("— ATS1-DC1-UR")


def test_the_point_name_is_left_alone(meter):
    """The schedule goes in `description`, never in `object-name`.

    The name is the meter's own point identifier and what an integration keys
    on; renaming points at commissioning would break that mapping every time
    an electrician moved a CT.
    """
    meter.commission_channels(["PDUA-DC1-HA-R2-01", None, None, None])
    obj = meter._objects[(OBJ_ANALOG_INPUT, 2002)]
    assert obj.name == "Ckt01_kW"


def test_re_commissioning_with_the_same_schedule_is_a_no_op(meter):
    """So the caller can offer it every tick and log only real changes."""
    labels = ["PDUA-DC1-HA-R2-01", None, None, None]
    assert meter.commission_channels(labels) is True
    assert meter.commission_channels(list(labels)) is False


def test_a_moved_ct_is_re_commissioned(meter):
    """A fleet add/remove re-clamps a channel and nothing else tells the meter."""
    meter.commission_channels(["PDUA-DC1-HA-R2-01", None, None, None])
    assert meter.commission_channels(["PDUB-DC1-HA-R2-01", None, None, None]) is True
    assert desc(meter, 1) == "Circuit 1 Active Power — PDUB-DC1-HA-R2-01"


def test_no_schedule_leaves_the_factory_labels(meter):
    """`None` is "nobody commissioned this", which is not the same as all-spare.

    An uncommissioned meter in the field says Circuit 1 Active Power and
    nothing more, and a simulator that invented a schedule for it would be
    modelling a site that had been commissioned when it had not.
    """
    before = desc(meter, 1)
    assert meter.commission_channels(None) is False
    assert desc(meter, 1) == before


def test_a_short_schedule_leaves_the_rest_spare(meter):
    """Fewer labels than channels is an 84-way meter on a 20-way panel."""
    meter.commission_channels(["PDUA-DC1-HA-R2-01"])
    assert desc(meter, 1) == "Circuit 1 Active Power — PDUA-DC1-HA-R2-01"
    assert desc(meter, 4) == "Circuit 4 Active Power — Spare"

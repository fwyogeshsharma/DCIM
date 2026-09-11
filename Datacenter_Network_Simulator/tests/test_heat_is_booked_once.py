"""A watt of server heat leaves the hall by one path, not by both.

A direct-to-chip server's cold plate carries about 70 % of its heat out through
the coolant; the residual — VRMs, DIMMs, drives, PSU losses — leaves through the
air. The room model was booking the WHOLE server against its CRAHs, and the CDU
loop was booking the whole server again, so a hall of liquid-cooled racks
reported its plant removing far more heat than its servers make.

That is not a rounding argument. It is the number a DCIM reads as delivered
cooling, and two independent measurements of one load that disagree by 2x make
the instrument check on the plant page useless — which is exactly the check that
tells an operator a flow meter has drifted.
"""

from core.rack_capacity import DTC_AIR_FRACTION
from tests.conftest import DC, ROOM, build_plant


def _room_heat(fx):
    fx.store._compute_power_flow()
    return fx.store._room_it_w.get((DC, ROOM), 0.0)


def _loop_heat_kw(fx):
    fx.store._compute_power_flow()
    return sum(fx.store._cdu_loop_heat_kw.values())


def test_the_air_side_books_only_the_air_share(tmp_path):
    """A hall of cold-plate servers is not a hall of air-cooled ones."""
    air = build_plant(tmp_path, servers=8, cdus=0)
    liquid = build_plant(tmp_path, servers=8, cdus=2)

    air_only = _room_heat(air)
    with_plates = _room_heat(liquid)

    assert air_only > 0
    # Every server in the second hall is on a loop, so the room keeps only the
    # residual share of the same load.
    assert with_plates < air_only
    assert abs(with_plates - air_only * DTC_AIR_FRACTION) < 0.05 * air_only


def test_the_liquid_side_books_only_the_cold_plate_share(tmp_path):
    fx = build_plant(tmp_path, servers=8, cdus=2)
    total_w = sum(fx.store._server_live_watts(d)
                  for d in fx.dm.get_all_devices()
                  if d.device_type.value == "server")

    loop_kw = _loop_heat_kw(fx)
    assert abs(loop_kw - total_w * (1.0 - DTC_AIR_FRACTION) / 1000.0) < 0.05 * total_w / 1000.0


def test_the_two_halves_add_up_to_the_servers(tmp_path):
    """The whole point: air + liquid = what the machines are actually making."""
    fx = build_plant(tmp_path, servers=8, cdus=2)
    total_kw = sum(fx.store._server_live_watts(d)
                   for d in fx.dm.get_all_devices()
                   if d.device_type.value == "server") / 1000.0

    fx.store._compute_power_flow()
    air_kw = fx.store._room_it_w.get((DC, ROOM), 0.0) / 1000.0
    liquid_kw = sum(fx.store._cdu_loop_heat_kw.values())

    assert abs((air_kw + liquid_kw) - total_kw) < 0.02 * total_kw


def test_a_hall_with_no_cdus_keeps_all_of_its_heat(tmp_path):
    """No cold plates, no split. An air-cooled hall must be unaffected."""
    fx = build_plant(tmp_path, servers=8, cdus=0)
    total_kw = sum(fx.store._server_live_watts(d)
                   for d in fx.dm.get_all_devices()
                   if d.device_type.value == "server") / 1000.0

    fx.store._compute_power_flow()
    air_kw = fx.store._room_it_w.get((DC, ROOM), 0.0) / 1000.0
    assert abs(air_kw - total_kw) < 0.02 * total_kw
    assert not fx.store._cdu_loop_heat_kw

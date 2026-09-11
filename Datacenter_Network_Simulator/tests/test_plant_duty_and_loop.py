"""What a cooling machine says it is doing has to match what it is doing.

Two published figures were free-running walks with nothing behind them, and both
were the figure a DCIM reads first:

  * a CRAH's Cooling_Capacity - delivered cooling as a share of its rating -
    had no live driver at all, so seven units covering a 50 kW hall each
    published ~65 % of a 100 kW rating. That is 455 kW of cooling for 50 kW of
    servers, and nine times what the chillers on the same plant were moving.

  * a CDU's Heat_Load was pinned near 275 kW on an 80 kW machine. The live
    coupling was wired but could not win: the random walk clamps the stored
    value into [base*mul - amp, base*mul + amp] every tick and the live block
    then EMAs only 30 % of the way toward the truth, so the pair settles at a
    fixed point near the clamp floor. The file already documents this exact
    failure for Compressor_Load and COP; Heat_Load and TCS_Flow never got the
    same exemption.

The loop points also have to agree with each other. Q = flow x deltaT x cp is
not a modelling choice, and a machine publishing 13.8 L/s across 13 K while
reporting 275 kW is contradicting itself in public.
"""

from core.bacnet_plant_generator import PlantTelemetryEngine, apply_stopped

CP = 4.187          # kJ/(kg.K), water


def settle(engine, ticks=60, **kw):
    out = {}
    for _ in range(ticks):
        out = engine.tick(1.0, **kw)
    return out


def loop_kw(values):
    """The heat the published loop points actually add up to."""
    dt = values["TCS_Return_Temp"] - values["TCS_Supply_Temp"]
    return values["TCS_Flow"] * dt * CP


# --- the CRAH's delivered cooling --------------------------------------------

def test_a_crah_publishes_the_duty_the_room_asked_for():
    """7 % of a 100 kW unit on a lightly loaded hall, not a walk around 65 %."""
    crah = PlantTelemetryEngine("crah", rated_kw=6.5, seed=5,
                                rated_cooling_kw=100.0)
    out = settle(crah, live_power=0.43, live_speed=0.30,
                 plant_load_frac=0.07, live_duty=0.072)
    assert abs(out["Cooling_Capacity"] - 7.2) < 0.5


def test_delivered_cooling_is_not_the_fan_speed():
    """They are different quantities and a real unit publishes both.

    A CRAH holds discharge on its chilled-water valve, so a hall at 7 % load can
    have its fans at the 30 % drive floor: the unit is moving air it does not
    need to. Reporting either number as the other hides exactly that.
    """
    crah = PlantTelemetryEngine("crah", rated_kw=6.5, seed=7,
                                rated_cooling_kw=100.0)
    out = settle(crah, live_power=0.43, live_speed=0.30, live_duty=0.072)
    assert out["Fan_Speed"] == 30.0
    assert out["Cooling_Capacity"] < 10.0


def test_the_walk_cannot_pull_the_duty_back():
    """The regression that made this necessary.

    Without the exemption the walk clamps the value back toward 65 % every tick
    and the live block only closes 30 % of the gap, so the published figure
    settles between the two and never reaches the truth.
    """
    crah = PlantTelemetryEngine("crah", rated_kw=6.5, seed=11,
                                rated_cooling_kw=100.0)
    out = settle(crah, ticks=200, live_duty=0.05)
    assert abs(out["Cooling_Capacity"] - 5.0) < 0.3


def test_a_hall_that_loses_units_raises_everybody_elses_duty():
    """Group control: demand is shared across what is RUNNING.

    The store computes the share; this only proves the point carries it, which
    is what makes a unit trip visible on the survivors rather than only on the
    room temperature twenty minutes later.
    """
    crah = PlantTelemetryEngine("crah", rated_kw=6.5, seed=13,
                                rated_cooling_kw=100.0)
    before = settle(crah, live_duty=0.10)["Cooling_Capacity"]
    after = settle(crah, live_duty=0.20)["Cooling_Capacity"]
    assert after > before * 1.8


def test_a_stopped_unit_delivers_nothing():
    crah = PlantTelemetryEngine("crah", rated_kw=6.5, seed=17,
                                rated_cooling_kw=100.0)
    out = settle(crah, live_duty=0.5)
    assert out["Cooling_Capacity"] > 40.0
    apply_stopped(out)
    assert out["Cooling_Capacity"] == 0.0
    assert out["Airflow"] == 0.0


# --- the CDU's loop -----------------------------------------------------------

def test_a_cdu_reports_the_heat_on_its_own_loop():
    """Eight or nine cold-plate servers are about 5 kW, not 275."""
    cdu = PlantTelemetryEngine("cdu", rated_kw=1.8, seed=3,
                               rated_cooling_kw=80.0)
    out = settle(cdu, live_heat=4.8, live_power=1.0, live_speed=0.35)
    assert abs(out["Heat_Load"] - 4.8) < 0.3


def test_the_published_loop_adds_up_to_the_published_heat():
    """Q = flow x deltaT x cp, or the machine is contradicting itself.

    The flow used to be scaled off the ratio of two design bases that were never
    consistent with each other: 18 L/s at 450 kW implies 6 K across the loop,
    while the same spec's supply and return say 13 K. Multiply the published
    numbers together and you got 750 kW on a machine reporting 275.
    """
    cdu = PlantTelemetryEngine("cdu", rated_kw=1.8, seed=19,
                               rated_cooling_kw=80.0)
    out = settle(cdu, live_heat=40.0, live_power=1.4, live_speed=0.6)
    assert abs(loop_kw(out) - out["Heat_Load"]) < 0.05 * out["Heat_Load"]


def test_an_uncoupled_cdu_still_sizes_itself_on_its_nameplate():
    """A 4U in-rack CHx80 walking around 450 kW is not noise, it is fiction.

    Reached only where nothing live drives the loop - a topology with no cooling
    model. The machine should still describe a machine that exists.
    """
    cdu = PlantTelemetryEngine("cdu", rated_kw=1.8, seed=23,
                               rated_cooling_kw=80.0)
    out = settle(cdu, ticks=120)
    assert 0 < out["Heat_Load"] <= 80.0
    assert abs(loop_kw(out) - out["Heat_Load"]) < 0.1 * out["Heat_Load"]


def test_a_facility_cdu_is_not_shrunk_to_an_in_rack_one():
    """The nameplate leads, not the point spec: a 750 kW skid is a real SKU."""
    big = PlantTelemetryEngine("cdu", rated_kw=12.0, seed=29,
                               rated_cooling_kw=750.0)
    out = settle(big, ticks=120)
    assert out["Heat_Load"] > 300.0


def test_a_cdu_with_no_catalog_rating_keeps_the_old_behaviour():
    """An unknown SKU must not divide by a nameplate nobody supplied."""
    cdu = PlantTelemetryEngine("cdu", rated_kw=1.8, seed=31)
    out = settle(cdu, ticks=40)
    assert out["Heat_Load"] > 0.0


def test_a_dead_loop_publishes_no_flow_and_no_heat():
    cdu = PlantTelemetryEngine("cdu", rated_kw=1.8, seed=37,
                               rated_cooling_kw=80.0)
    out = settle(cdu, live_heat=40.0)
    apply_stopped(out)
    assert out["Heat_Load"] == 0.0
    assert out["TCS_Flow"] == 0.0
    # A dead loop equalizes: the two thermowells drift onto the same number.
    assert out["TCS_Supply_Temp"] == out["TCS_Return_Temp"]

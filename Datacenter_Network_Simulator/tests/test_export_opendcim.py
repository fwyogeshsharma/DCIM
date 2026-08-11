"""Planning-side tests for tools/export_to_opendcim.py.

The exporter is the estate's only writer into openDCIM, and everything it publishes
is derived — panel voltages, main-breaker ratings, which instrument meters which
board, which pole a rack PDU lands on. None of that is checkable by looking at
openDCIM afterwards, because a wrong-but-plausible number renders exactly like a
right one: a 480 V panel record over a 400 V bus, a 100 A main on a board that
should match its twin, a meter template pointing at nothing.

These tests cover the PURE planning functions only. Everything that needs the live
REST API (apply(), and the TemplateID assignment inside it) is deliberately out of
scope here and is exercised against the running instance instead.
"""
import pytest

from tools.export_to_opendcim import (PANEL_METER_SPEC, PANEL_VOLTAGE,
                                      panel_main_breaker_a, panel_meter_sql, plan,
                                      plan_breakers, plan_panel_feeds,
                                      plan_panel_meters, pdu_breaker_amps,
                                      pdu_phase_map)


def _dev(name, dtype, **kw):
    d = {"name": name, "device_type": dtype, "datacenter": kw.pop("dc", "DC1"),
         "room": kw.pop("room", "Server Hall A"), "vendor": kw.pop("vendor", "APC"),
         "model_name": kw.pop("model", ""), "mgmt_ip": kw.pop("mgmt_ip", "")}
    d.update(kw)
    return d


def _graph(devices, links):
    """The shape /api/topology/graph returns: node ids are the device names here,
    which keeps the fixtures readable — nothing in the planner parses an id."""
    return {"devices": [{"id": d["name"], "name": d["name"],
                         "device_type": d["device_type"]} for d in devices],
            "links": links}


def _power(src, dst):
    return {"layer": "power", "src_id": src, "dst_id": dst,
            "supply_node": src, "load_node": dst}


# ── main breaker sizing ──────────────────────────────────────────────────────

@pytest.mark.parametrize("model,volts,amps", [
    ("APC Galaxy RPP 125A", 415, 125),
    ("Eaton Magnum DS 4000A", 400, 4000),
    ("Eaton Freedom 2100 MCC 1600A", 400, 1600),
    # kVA converts at the panel's OWN voltage: 1200 kVA / (400 * sqrt3) = 1732 A.
    # The same UPS read 1443 A while the panel record wrongly said 480 V, which is
    # what made the voltage correction visible in the first place.
    ("Vertiv Liebert EXL S1 1200kVA", 400, 1732),
    ("Vertiv Liebert EXL S1 1200kVA", 480, 1443),
    # Nothing in the name to read: 0, which openDCIM treats as "not set" and skips,
    # rather than a fabricated rating on a genset or a meter.
    ("Caterpillar 3516B", 400, 0),
    ("", 400, 0),
])
def test_main_breaker_is_read_off_the_sku(model, volts, amps):
    assert panel_main_breaker_a(model, volts) == amps


def test_estate_is_400v_iec_not_480v():
    """The panel record must match what the meters serve (400 V L-L, 415 V RPP)."""
    assert PANEL_VOLTAGE["rpp"] == 415
    for t in ("utility_feed", "generator", "switchgear", "ats", "mcc", "ups", "mpp"):
        assert PANEL_VOLTAGE[t] == 400, f"{t} is not on the 400 V bus"


# ── panel rows ───────────────────────────────────────────────────────────────

def test_branch_panels_are_odd_even_and_switchgear_is_sequential():
    """A 42-pole board is two columns; a 3-pole entry has no schedule to draw."""
    devices = [_dev("RPPA-DC1-HA-R1-04", "rpp", model="APC Galaxy RPP 160A"),
               _dev("UPSA-DC1-UR", "ups", room="UPS Room",
                    model="Vertiv Liebert EXL S1 1200kVA")]
    rows = {r["PanelLabel"]: r for r in plan(devices)["panels"]}

    assert rows["RPPA-DC1-HA-R1-04"]["NumberScheme"] == "Odd/Even"
    assert rows["RPPA-DC1-HA-R1-04"]["NumberOfPoles"] == 42
    assert rows["UPSA-DC1-UR"]["NumberScheme"] == "Sequential"
    assert rows["UPSA-DC1-UR"]["NumberOfPoles"] == 3


# ── panel meters ─────────────────────────────────────────────────────────────

def _meter_estate():
    """An RPP metered by a SEPARATE EV2, plus boards that meter themselves."""
    devices = [
        _dev("RPPA-DC1-HA-R1-04", "rpp", model="APC Galaxy RPP 160A"),
        _dev("EV21-DC1-HA-R1-04", "energy_monitor", vendor="Verdigris Technologies",
             model="Verdigris EV2-42", mgmt_ip="10.52.11.20"),
        _dev("MPPA-DC1-HA", "mpp", vendor="Eaton", model="Eaton Pow-R-Line 3a 150A",
             mgmt_ip="10.52.11.25"),
        _dev("UPSA-DC1-UR", "ups", room="UPS Room", vendor="Vertiv (Liebert)",
             model="Vertiv Liebert EXL S1 1200kVA", mgmt_ip="10.52.14.45"),
        _dev("ATS1-DC1-UR", "ats", room="UPS Room", vendor="ASCO",
             model="ASCO 7000 4000A", mgmt_ip="10.52.14.10"),
        _dev("GEN1-DC1-GR", "generator", room="Generator Room", vendor="Caterpillar",
             model="Caterpillar 3516B", mgmt_ip="10.52.14.37"),
    ]
    links = [_power("RPPA-DC1-HA-R1-04", "EV21-DC1-HA-R1-04")]
    p = plan(devices)
    meters = plan_panel_meters(_graph(devices, links), devices, p)
    return p, meters, {r["PanelLabel"]: r for r in p["panels"]}


def test_rpp_takes_the_address_of_its_own_branch_meter():
    """An RPP is a passive board — the meter is a separate device on the graph.

    Resolved through the power LINK, never by name: the plant-room boards pair
    RPPA/RPPB with EV21/EV22, which no name rule would get right.
    """
    _, meters, rows = _meter_estate()
    row = rows["RPPA-DC1-HA-R1-04"]

    assert row["PanelIPAddress"] == "10.52.11.20"
    assert row["_meter_model"] == "Verdigris EV2-42"
    # BACnet/IP in this estate, so no SNMP objects are invented for it.
    assert meters["Verdigris EV2-42"]["Managed"] == 0
    assert meters["Verdigris EV2-42"]["OIDs"] == ("", "", "")


def test_boards_that_meter_themselves_keep_their_own_address():
    _, meters, rows = _meter_estate()

    assert rows["MPPA-DC1-HA"]["PanelIPAddress"] == "10.52.11.25"
    assert rows["MPPA-DC1-HA"]["_meter_model"] == "Schneider PowerLogic PM5000"
    assert meters["Schneider PowerLogic PM5000"]["ProcessingProfile"] == "Convert3PhAmperes"
    # The UPS IS the meter, so its template is the UPS's own make and model.
    assert rows["UPSA-DC1-UR"]["_meter_model"] == "Vertiv Liebert EXL S1 1200kVA"
    assert meters["Vertiv Liebert EXL S1 1200kVA"]["ProcessingProfile"] == "SingleOIDWatts"


def test_switches_and_gensets_get_no_meter_template():
    """An ATS has no current object at all and a genset serves no per-phase current.

    A blank-OID template is worse than none: the poller would write a fabricated 0
    instead of skipping the device.
    """
    _, meters, rows = _meter_estate()

    assert rows["ATS1-DC1-UR"]["_meter_model"] == ""
    assert rows["GEN1-DC1-GR"]["_meter_model"] == ""
    assert not any("ASCO" in m or "Caterpillar" in m for m in meters)
    # They keep their addresses — the gear is reachable, it just has nothing to meter.
    assert rows["ATS1-DC1-UR"]["PanelIPAddress"] == "10.52.14.10"


def test_meter_template_voltage_comes_from_the_panel():
    """openDCIM multiplies the meter's amps by THIS voltage, so it must be the bus
    the meter is clamped to — 415 V on an RPP, 400 V on the LV boards."""
    _, meters, _ = _meter_estate()

    assert meters["Verdigris EV2-42"]["Voltage"] == 415
    assert meters["Schneider PowerLogic PM5000"]["Voltage"] == 400


def test_meter_templates_register_themselves_and_their_manufacturers():
    """A CDUTemplate with no matching fac_Manufacturer row is invisible: openDCIM's
    GetTemplateList inner-joins it, so the panel dropdown would not even offer it."""
    p, meters, _ = _meter_estate()

    for model, m in meters.items():
        assert model in p["templates"], f"{model} never registered as a template"
        assert p["templates"][model]["DeviceType"] == "CDU"
        assert m["Manufacturer"] in p["manufacturers"]


def test_meter_spec_covers_every_self_metered_board_type():
    """Guards the table against a new panel type being added with no meter."""
    assert set(PANEL_METER_SPEC) == {"utility_feed", "switchgear", "mcc", "mpp", "ups"}


def test_panel_meter_sql_writes_oids_and_managed_flag():
    _, meters, _ = _meter_estate()
    sql = "\n".join(panel_meter_sql(meters))

    assert "fac_CDUTemplate" in sql
    assert "ct.Managed=1" in sql and "ct.Managed=0" in sql          # SNMP vs BACnet
    assert "ct.ProcessingProfile='Convert3PhAmperes'" in sql
    assert "ct.Voltage=415" in sql and "ct.Voltage=400" in sql
    assert "1.3.6.1.2.1.33.1.4.4.1.4.1" in sql                      # upsOutputPower
    assert sql.count("INSERT IGNORE INTO fac_CDUTemplate") == len(meters)


def test_no_meters_writes_no_sql():
    assert panel_meter_sql({}) == []


# ── feeds ────────────────────────────────────────────────────────────────────

def test_transfer_switch_records_its_second_source():
    """openDCIM holds ONE parent, so the NORMAL source is the parent and the
    alternate goes in ParentBreakerName — dropping it would hide the emergency feed."""
    devices = [_dev("SWGR1-DC1-UR", "switchgear", room="UPS Room"),
               _dev("SWGR2-DC1-GR", "switchgear", room="Generator Room"),
               _dev("ATS1-DC1-UR", "ats", room="UPS Room")]
    links = [_power("SWGR1-DC1-UR", "ATS1-DC1-UR"),
             _power("SWGR2-DC1-GR", "ATS1-DC1-UR")]

    feeds = plan_panel_feeds(_graph(devices, links), {d["name"] for d in devices})

    assert feeds["ATS1-DC1-UR"]["parent"] == "SWGR1-DC1-UR"
    assert feeds["ATS1-DC1-UR"]["alternates"] == ["SWGR2-DC1-GR"]


# ── breakers ─────────────────────────────────────────────────────────────────

def _breaker_estate():
    devices = [
        _dev("RPPA-DC1-HA-R1-04", "rpp", model="APC Galaxy RPP 160A"),
        _dev("RPPB-DC1-HA-R1-13", "rpp", model="APC Galaxy RPP 160A"),
        _dev("PDUA-DC1-HA-R1-01", "pdu", model="APC AP8941"),      # 1-phase, 30 A
        _dev("PDUB-DC1-HA-R1-01", "pdu", model="APC AP8941"),
        _dev("PDUA-DC1-HA-R2-01", "pdu", model="APC AP8886"),      # 3-phase, 32 A
    ]
    links = [_power("RPPA-DC1-HA-R1-04", "PDUA-DC1-HA-R1-01"),
             _power("RPPB-DC1-HA-R1-13", "PDUB-DC1-HA-R1-01"),
             _power("RPPA-DC1-HA-R1-04", "PDUA-DC1-HA-R2-01")]
    rows = plan_breakers(_graph(devices, links), pdu_phase_map(devices),
                         {d["name"] for d in devices if d["device_type"] == "rpp"},
                         pdu_breaker_amps(devices))
    return {r["pdu"]: r for r in rows}


def test_positions_fill_both_columns_lowest_first():
    """Both columns get used, and each panel numbers from 1 independently.

    NOT "A odd, B even": every RPP in this estate feeds one side only, so parity by
    feed would leave the whole even column of every board empty. The A/B split shows
    in which PANEL a PDU lands on. (The function's docstring claimed the old rule
    long after the code stopped doing it, which is what this test is here to stop.)
    """
    rows = _breaker_estate()

    # Two panels, each starting at pole 1 for its own first PDU.
    assert rows["PDUA-DC1-HA-R1-01"]["first_pole"] == 1
    assert rows["PDUB-DC1-HA-R1-01"]["first_pole"] == 1
    assert rows["PDUA-DC1-HA-R1-01"]["panel"] == "RPPA-DC1-HA-R1-04"
    assert rows["PDUB-DC1-HA-R1-01"]["panel"] == "RPPB-DC1-HA-R1-13"
    # The 3-phase strip on the same panel as PDUA-…-R1-01 takes the next free run,
    # so nothing overlaps the pole that one already holds.
    assert 1 not in rows["PDUA-DC1-HA-R2-01"]["poles"]


def test_positions_never_overlap_on_one_panel():
    """The whole point of tracking `taken`: two breakers on one pole is a schedule
    that cannot be built."""
    devices = [_dev("RPPA-DC1-HA-R1-04", "rpp", model="APC Galaxy RPP 160A")]
    links = []
    for i in range(1, 9):
        pdu = f"PDUA-DC1-HA-R{i}-01"
        devices.append(_dev(pdu, "pdu", model="APC AP8886"))     # all 3-phase
        links.append(_power("RPPA-DC1-HA-R1-04", pdu))
    rows = plan_breakers(_graph(devices, links), pdu_phase_map(devices),
                         {"RPPA-DC1-HA-R1-04"}, pdu_breaker_amps(devices))

    seen = [p for r in rows for p in r["poles"]]
    assert len(seen) == len(set(seen)), "two breakers assigned the same pole"
    assert all(p <= 42 for p in seen)


def test_three_phase_pdu_spans_same_parity_poles():
    """A 3-pole breaker straddles the panelboard's phase rotation: 4,6,8 — not
    4,5,6. openDCIM derives the rest from the first pole with an adder of 2."""
    row = _breaker_estate()["PDUA-DC1-HA-R2-01"]

    assert row["phases"] == 3
    assert len(row["poles"]) == 3
    assert row["poles"] == [row["first_pole"], row["first_pole"] + 2,
                            row["first_pole"] + 4]
    assert len({p % 2 for p in row["poles"]}) == 1


def test_breaker_amperage_comes_from_the_strip_not_a_house_standard():
    """A 30 A unit is fed by a 30 A breaker; a 32 A three-phase strip by 32 A."""
    rows = _breaker_estate()

    assert rows["PDUA-DC1-HA-R1-01"]["breaker_a"] == 30
    assert rows["PDUA-DC1-HA-R2-01"]["breaker_a"] == 32
    assert rows["PDUA-DC1-HA-R1-01"]["phases"] == 1

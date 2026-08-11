"""Panelboards are a construction item: identical rooms get identical boards.

The selector used to size every RPP from the load connected TODAY. Two sites built
to one design then diverged on fill alone — DC1's halls, holding 8 compute racks,
were issued 125 A boards while DC2's identical halls (same 5x13 = 65 rack grid, same
drawings) got 100 A ones because they currently hold 7. Nobody orders a smaller
panelboard because two racks have not arrived yet, and nobody swaps it back when
they do.

So peers are sized together, on the worst case among them: same board type, same
kind of room, same side of the 2N pair, at any site. A and B are peers ACROSS sites
and never with each other — on a 2N floor the two sides can legitimately carry
different racks.

These tests pin that rule, the boundaries it must not cross (rack PDUs are bought
per rack and stay independent; the A side must not drag the B side), and the ratchet
that stops the whole thing oscillating as halls fill and empty.
"""
import pytest

from core.power_sizing import (_board_side, _peer_key, _rating_for, rightsize_nodes)

APC = "APC by Schneider Electric"


def _node(nid, name, dtype, *, room="", dc="DC1", vendor=APC, model="", watts=0):
    return {"id": nid, "device": {"name": name, "device_type": dtype, "room": room,
                                  "datacenter": dc, "vendor": vendor,
                                  "model_name": model, "power_draw_w": watts}}


def _rpp_with_load(prefix, dc, room, load_w, model, side="A", n_servers=4):
    """One RPP plus the servers hanging off it, as (nodes, edges).

    Load is spread over several servers because that is how a real board carries it,
    and because a single fat leaf would hide any bug that mishandles fan-out.
    """
    rid = f"{prefix}-{dc}"
    nodes = [_node(rid, f"RPP{side}-{dc}-{prefix}", "rpp", room=room, dc=dc,
                   model=model)]
    edges = []
    each = load_w / n_servers
    for i in range(n_servers):
        sid = f"{rid}-srv{i}"
        nodes.append(_node(sid, f"SRV{i}-{dc}", "server", room=room, dc=dc,
                           watts=each))
        edges.append({"src": rid, "dst": sid, "layer": "power"})
    return nodes, edges


def _model_of(nodes, nid):
    return next(n["device"]["model_name"] for n in nodes if n["id"] == nid)


# ── the key itself ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("RPPA-DC1-HA-R1-04", "A"),
    ("RPPB-DC2-HB-R1-13", "B"),
    ("RPPA-DC1-CP", "A"),
    ("UTIL1-DC1-UR", ""),          # no side in the leading code
    ("MCC1-DC1-MR", ""),
    ("", ""),
])
def test_board_side_reads_the_leading_code(name, expected):
    """Side comes from the FIRST name segment, where this estate puts the role."""
    assert _board_side(name) == expected


def test_peer_key_pairs_sites_and_separates_sides_and_rooms():
    a_dc1 = {"device_type": "rpp", "room": "Server Hall A", "name": "RPPA-DC1-HA-R1-04"}
    a_dc2 = {"device_type": "rpp", "room": "Server Hall A", "name": "RPPA-DC2-HA-R1-04"}
    b_dc1 = {"device_type": "rpp", "room": "Server Hall A", "name": "RPPB-DC1-HA-R1-13"}
    a_hb = {"device_type": "rpp", "room": "Server Hall B", "name": "RPPA-DC1-HB-R1-04"}

    assert _peer_key(a_dc1) == _peer_key(a_dc2), "same room+side at two sites are peers"
    assert _peer_key(a_dc1) != _peer_key(b_dc1), "A and B are not peers with each other"
    assert _peer_key(a_dc1) != _peer_key(a_hb), "different rooms are not peers"


# ── the rule ─────────────────────────────────────────────────────────────────

def test_identical_rooms_get_identical_boards_across_sites():
    """THE REGRESSION. Same room, two sites, different fill — one SKU, sized to the
    heavier site."""
    n1, e1 = _rpp_with_load("HA", "DC1", "Server Hall A", 60_000, "APC Galaxy RPP 80A")
    n2, e2 = _rpp_with_load("HA", "DC2", "Server Hall A", 40_000, "APC Galaxy RPP 80A")
    nodes, edges = n1 + n2, e1 + e2

    rightsize_nodes(nodes, edges)

    dc1, dc2 = _model_of(nodes, "HA-DC1"), _model_of(nodes, "HA-DC2")
    assert dc1 == dc2, f"sites diverged: {dc1} vs {dc2}"
    # 60 kW at the 70% RPP target needs 85.7 kW of board — the 125 A (89.8 kW) frame.
    assert dc1 == "APC Galaxy RPP 125A"


def test_lighter_site_is_lifted_not_the_heavier_one_dropped():
    """Equalisation is a MAX, not an average: the busy site keeps its real size."""
    n1, e1 = _rpp_with_load("HA", "DC1", "Server Hall A", 72_000, "APC Galaxy RPP 80A")
    n2, e2 = _rpp_with_load("HA", "DC2", "Server Hall A", 1_000, "APC Galaxy RPP 80A")
    nodes, edges = n1 + n2, e1 + e2

    rightsize_nodes(nodes, edges)

    # 72 kW / 0.70 = 102.9 kW -> the 160 A (115 kW) frame, and DC2 follows it.
    assert _model_of(nodes, "HA-DC1") == "APC Galaxy RPP 160A"
    assert _model_of(nodes, "HA-DC2") == "APC Galaxy RPP 160A"


def test_a_side_does_not_drag_the_b_side():
    """2N sides are peers across sites, never with each other."""
    a, ea = _rpp_with_load("HA", "DC1", "Server Hall A", 72_000,
                           "APC Galaxy RPP 80A", side="A")
    b, eb = _rpp_with_load("HAB", "DC1", "Server Hall A", 40_000,
                           "APC Galaxy RPP 80A", side="B")
    nodes, edges = a + b, ea + eb

    rightsize_nodes(nodes, edges)

    assert _model_of(nodes, "HA-DC1") == "APC Galaxy RPP 160A"
    assert _model_of(nodes, "HAB-DC1") == "APC Galaxy RPP 80A"   # 40 kW/0.7 = 57.1 kW


def test_different_rooms_size_independently():
    """A hall board and a network-room board are different items in the BOM."""
    hall, eh = _rpp_with_load("HA", "DC1", "Server Hall A", 72_000,
                              "APC Galaxy RPP 80A")
    nr, en = _rpp_with_load("NR", "DC1", "Network Room", 8_000, "APC Galaxy RPP 80A")
    nodes, edges = hall + nr, eh + en

    rightsize_nodes(nodes, edges)

    assert _model_of(nodes, "HA-DC1") == "APC Galaxy RPP 160A"
    assert _model_of(nodes, "NR-DC1") == "APC Galaxy RPP 80A"


def test_rack_pdus_are_not_peer_equalised():
    """A strip is bought per rack. A heavy rack must not re-spec its light neighbour.

    Same room, same A side, wildly different racks — exactly the arrangement that
    would collapse to one SKU if PDUs were peers.
    """
    nodes = [
        _node("heavy", "PDUA-DC1-HA-R2-01", "pdu", room="Server Hall A",
              model="APC AP8941"),
        _node("light", "PDUA-DC1-HA-R2-02", "pdu", room="Server Hall A",
              model="APC AP8941"),
    ]
    edges = []
    for i in range(18):                       # 18 cords, 12 kW: a full compute rack
        nodes.append(_node(f"h{i}", f"SRVH{i}", "server", watts=12_000 / 18))
        edges.append({"src": "heavy", "dst": f"h{i}", "layer": "power"})
    for i in range(4):                        # 4 cords, 0.5 kW: a plant rack
        nodes.append(_node(f"l{i}", f"SRVL{i}", "server", watts=500 / 4))
        edges.append({"src": "light", "dst": f"l{i}", "layer": "power"})

    rightsize_nodes(nodes, edges)

    assert _model_of(nodes, "heavy") == "APC AP8886"     # 22 kW, 42 outlets
    assert _model_of(nodes, "light") == "APC AP8941"     # untouched


# ── the ratchet ──────────────────────────────────────────────────────────────

def test_oversized_board_is_never_written_down():
    """A half-empty hall keeps the board it was built with."""
    nodes, edges = _rpp_with_load("HA", "DC1", "Server Hall A", 5_000,
                                  "APC Galaxy RPP 160A")

    changes = rightsize_nodes(nodes, edges)

    assert changes == []
    assert _model_of(nodes, "HA-DC1") == "APC Galaxy RPP 160A"


def test_undersized_board_is_still_corrected():
    """The ratchet must not mask a real overload."""
    nodes, edges = _rpp_with_load("HA", "DC1", "Server Hall A", 72_000,
                                  "APC Galaxy RPP 80A")

    changes = rightsize_nodes(nodes, edges)

    assert [c["to"] for c in changes] == ["APC Galaxy RPP 160A"]
    assert _rating_for(_model_of(nodes, "HA-DC1")) > _rating_for("APC Galaxy RPP 80A")


def test_second_run_is_a_no_op():
    """Idempotent — or every re-run reports capacity 'changes' that are only churn."""
    n1, e1 = _rpp_with_load("HA", "DC1", "Server Hall A", 60_000, "APC Galaxy RPP 80A")
    n2, e2 = _rpp_with_load("HA", "DC2", "Server Hall A", 40_000, "APC Galaxy RPP 80A")
    nodes, edges = n1 + n2, e1 + e2

    assert rightsize_nodes(nodes, edges)          # first run does the work
    assert rightsize_nodes(nodes, edges) == []    # second finds nothing

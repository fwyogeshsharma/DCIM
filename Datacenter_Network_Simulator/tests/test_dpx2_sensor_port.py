"""Raritan DPX2 probes on a PDU's sensor port.

A DPX2 is an RJ-12 lead with a thermistor on the end: no processor, no IP, no
Ethernet. The PX2 polls it over its SENSOR port and publishes it in the PDU's own
RARITAN-PX2-MIB external-sensor table at a slot. Serving it from a per-probe SNMP
agent — which is what this simulator used to do — invents a network node that
does not exist and puts the Raritan table on the wrong device.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.device_state_store as dss
from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator, _RARITAN_SENSOR
from core.topology_engine import TopologyEngine

TOPOLOGY = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"


@pytest.fixture(scope="module")
def shipped():
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    return [Device.from_dict(n["device"]) for n in data["nodes"]]


def test_no_probe_holds_an_address(shipped):
    probes = [d for d in shipped if d.host_pdu_ip]
    assert len(probes) == 20
    for d in probes:
        assert not d.ip_address and not d.mgmt_ip, d.name
        assert d.interface_count == 0 and not d.interfaces, d.name
        assert d.snmp_port == 0 and not d.metrics_enabled, d.name
        assert d.sensor_slot >= 1


def test_every_probe_points_at_a_real_pdu(shipped):
    pdu_ips = {d.mgmt_ip for d in shipped
               if d.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU) and d.mgmt_ip}
    for d in shipped:
        if d.host_pdu_ip:
            assert d.host_pdu_ip in pdu_ips, d.name


def test_a_probe_is_hosted_by_a_pdu_in_its_own_rack(shipped):
    """An RJ-12 lead does not cross the aisle."""
    by_ip = {d.mgmt_ip: d for d in shipped if d.mgmt_ip}
    for d in shipped:
        if not d.host_pdu_ip:
            continue
        host = by_ip[d.host_pdu_ip]
        assert (host.datacenter, host.room, host.rack_row, host.rack_num) == \
               (d.datacenter, d.room, d.rack_row, d.rack_num), d.name


def test_slots_do_not_overlap_on_a_chain(shipped):
    """A T3H1 occupies four consecutive slots and a CC2 two; two probes sharing a
    slot would silently overwrite each other's readings in the PDU's table."""
    width = {"Raritan DPX2-T3H1": 4, "Raritan DPX2-CC2": 2}
    by_host: dict = {}
    for d in shipped:
        if d.host_pdu_ip:
            by_host.setdefault(d.host_pdu_ip, []).append(d)
    assert by_host
    for host, probes in by_host.items():
        used = set()
        for p in probes:
            for off in range(width.get(p.model_name, 2)):
                slot = p.sensor_slot + off
                assert slot not in used, f"{host}: slot {slot} claimed twice"
                used.add(slot)


def test_chain_membership_matches_the_probes_that_claim_it(shipped):
    hosts = {d.mgmt_ip: d for d in shipped if d.sensor_children}
    claimed: dict = {}
    for d in shipped:
        if d.host_pdu_ip:
            claimed.setdefault(d.host_pdu_ip, set()).add(d.name)
    assert hosts
    for ip, host in hosts.items():
        assert set(host.sensor_children) == claimed.get(ip, set()), host.name


def test_chains_stay_within_the_sensor_ports_limit(shipped):
    """A PX2 sensor port daisy-chains at most 8 DPX2 units."""
    for d in shipped:
        if d.sensor_children:
            assert len(d.sensor_children) <= 8, d.name


# ─────────────────────────────────────────────────────────────────────────────
#  SNMP republish
# ─────────────────────────────────────────────────────────────────────────────
def _publish(name, model, slot, wet="dry"):
    dss._ext_state_cache[name] = {
        "probe_inlet_c": 22.4, "probe_mid_c": 25.1, "probe_outlet_c": 33.8,
        "probe_humidity_pct": 41.5, "probe_dewpoint_c": 9.0,
        "probe_model": model, "probe_slot": slot, "water_detection": wet}


@pytest.fixture
def host_pdu():
    pdu = Device(name="PDUA-DC1-HA-R2-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.30",
                 model_name="Raritan PX2-5170CR")
    pdu.sensor_children = ["LEAK1-DC1-HA-R2-01", "SEN1-DC1-HA-R2-01"]
    _publish("LEAK1-DC1-HA-R2-01", "Raritan DPX2-CC2", 1)
    _publish("SEN1-DC1-HA-R2-01", "Raritan DPX2-T3H1", 3)
    yield pdu
    for n in pdu.sensor_children:
        dss._ext_state_cache.pop(n, None)


def _by_oid(entries):
    return {e[0]: e[2] for e in entries}


def test_pdu_publishes_the_whole_chain(host_pdu):
    v = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    for slot in range(1, 7):
        assert f"{_RARITAN_SENSOR}.4.1.{slot}" in v, f"slot {slot} missing"


def test_slot_types_match_the_model(host_pdu):
    v = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    assert v[f"{_RARITAN_SENSOR}.3.1.1"] == "28"     # CC2 water rope
    assert v[f"{_RARITAN_SENSOR}.3.1.2"] == "10"     # CC2 temperature
    assert v[f"{_RARITAN_SENSOR}.3.1.6"] == "11"     # T3H1 humidity


def test_t3h1_publishes_three_distinct_temperatures(host_pdu):
    """Inlet, mid and exhaust are different points; collapsing them would hide
    the front-to-back rise the rack model exists to produce."""
    v = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    assert v[f"{_RARITAN_SENSOR}.4.1.3"] == "224"    # inlet 22.4
    assert v[f"{_RARITAN_SENSOR}.4.1.4"] == "251"    # mid   25.1
    assert v[f"{_RARITAN_SENSOR}.4.1.5"] == "338"    # exhaust 33.8


def test_water_detection_reaches_the_pdu(host_pdu):
    dry = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    assert dry[f"{_RARITAN_SENSOR}.4.1.1"] == "0"
    _publish("LEAK1-DC1-HA-R2-01", "Raritan DPX2-CC2", 1, wet="wet")
    assert _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))[
        f"{_RARITAN_SENSOR}.4.1.1"] == "1"


def test_a_probe_gets_no_dataset_of_its_own(tmp_path):
    """snmpsim serves any file in the directory, so one written here would be a
    live agent answering for a device with no address at all."""
    probe = Device(name="SEN1-DC1-HA-R2-01", device_type=DeviceType.SENSOR,
                   vendor=Vendor.RARITAN, ip_address="",
                   model_name="Raritan DPX2-T3H1")
    probe.attach_to_sensor_port("10.52.11.30", 3)
    gen = SNMPRecGenerator(str(tmp_path))
    assert gen.generate_device(probe, TopologyEngine()) is None
    assert list(Path(tmp_path).glob("*.snmprec")) == []


def test_trap_from_a_probe_sources_from_its_pdu():
    """A DPX2 has no agent, so it cannot send a trap — the PDU raises it."""
    from core.trap_engine import _trap_source_ip
    probe = Device(name="SEN1-DC1-HA-R2-01", device_type=DeviceType.SENSOR,
                   vendor=Vendor.RARITAN, ip_address="",
                   model_name="Raritan DPX2-T3H1")
    probe.attach_to_sensor_port("10.52.11.30", 3)
    assert _trap_source_ip(probe) == "10.52.11.30"


def test_attach_by_hand_leaves_the_port_behind():
    """Same trap as the MS/TP helper: the portless rule runs in __post_init__."""
    d = Device(name="SEN1-DC1-HA-R2-01", device_type=DeviceType.SENSOR,
               vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.43",
               model_name="Raritan DPX2-T3H1", snmp_port=161)
    d.host_pdu_ip = "10.52.11.30"          # the naive way
    assert d.mgmt_ip and d.interface_count > 0
    d.attach_to_sensor_port("10.52.11.30", 3)
    assert not d.mgmt_ip and d.interface_count == 0 and d.snmp_port == 0


# ─────────────────────────────────────────────────────────────────────────────
#  API surfaces the carrier
# ─────────────────────────────────────────────────────────────────────────────
def test_every_sensor_reports_a_host_in_the_api(shipped):
    """Sensors own no address, so the Live Metrics table has nothing to show in an
    IP column. DeviceInfo carries the carrier instead — if this regresses the UI
    silently renders a column of blanks."""
    from api.routers.devices import _device_to_info
    sensors = [d for d in shipped if d.device_type == DeviceType.SENSOR]
    assert sensors
    for d in sensors:
        info = _device_to_info(d)
        assert info.host_ip, f"{d.name} reports no carrier"
        assert info.host_via in ("sensor port", "modbus", "mstp"), d.name
        assert info.host_index, f"{d.name} has no position on its carrier"


def test_host_ip_resolves_to_a_device_in_the_same_payload(shipped):
    """The UI maps host_ip -> device name from devices[]; an unresolvable IP would
    render as a bare address instead of the PDU/gateway name."""
    from api.routers.devices import _device_to_info
    addrs = {d.mgmt_ip for d in shipped if d.mgmt_ip} | {d.ip_address for d in shipped if d.ip_address}
    for d in shipped:
        if d.device_type != DeviceType.SENSOR:
            continue
        assert _device_to_info(d).host_ip in addrs, d.name


def test_carrier_kind_matches_the_device_class(shipped):
    from api.routers.devices import _device_to_info
    for d in shipped:
        if d.device_type != DeviceType.SENSOR:
            continue
        info = _device_to_info(d)
        if d.host_pdu_ip:
            assert info.host_via == "sensor port" and info.host_index == d.sensor_slot
        elif d.modbus_role == "rtu_slave":
            assert info.host_via == "modbus" and info.host_index == d.modbus_unit_id


def test_addressed_devices_report_no_carrier(shipped):
    """A PDU or gateway owns its address — it must not claim to hang off something."""
    from api.routers.devices import _device_to_info
    for d in shipped:
        if d.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU) and d.mgmt_ip:
            assert _device_to_info(d).host_ip is None, d.name


# ─────────────────────────────────────────────────────────────────────────────
#  Graph wiring
# ─────────────────────────────────────────────────────────────────────────────
def _adjacency():
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    ids = {n["id"]: n["device"] for n in data["nodes"]}
    adj: dict = {i: [] for i in ids}
    for e in data["edges"]:
        adj[e["src"]].append((e["dst"], e.get("layer")))
        adj[e["dst"]].append((e["src"], e.get("layer")))
    return ids, adj


def test_no_portless_device_holds_a_management_edge():
    """A device with no Ethernet port cannot be cabled to an OOB switch. The
    migrations stripped the ports but left these edges, so the graph drew 50
    field devices wired to switches they have no port for."""
    ids, adj = _adjacency()
    bad = [d["name"] for i, d in ids.items()
           if (d.get("host_pdu_ip") or d.get("modbus_gateway_ip") or d.get("mstp_router_ip"))
           and any(layer == "management" for _, layer in adj[i])]
    assert not bad, f"portless devices still on the management plane: {bad}"


def test_every_portless_device_links_to_its_carrier():
    ids, adj = _adjacency()
    by_ip = {d.get("mgmt_ip"): i for i, d in ids.items() if d.get("mgmt_ip")}
    for i, d in ids.items():
        carrier = d.get("host_pdu_ip") or d.get("modbus_gateway_ip") or d.get("mstp_router_ip")
        if not carrier:
            continue
        cid = by_ip.get(carrier)
        assert cid is not None, d["name"]
        assert (cid, "fieldbus") in adj[i], (
            f"{d['name']} has no fieldbus link to its carrier")


def test_carriers_are_connected_and_uplinked():
    """A gateway/router is the one thing on the trunk WITH an Ethernet port, so
    it carries the management uplink its children cannot."""
    ids, adj = _adjacency()
    carriers = [(i, d) for i, d in ids.items()
                if d["device_type"] in ("modbus_gateway", "bacnet_router")]
    assert carriers
    for i, d in carriers:
        assert adj[i], f"{d['name']} is an unconnected node"
        mgmt = [p for p, layer in adj[i] if layer == "management"]
        assert mgmt, f"{d['name']} has no management uplink"
        assert ids[mgmt[0]]["device_type"] == "oob_switch", d["name"]
        assert any(layer == "fieldbus" for _, layer in adj[i]), (
            f"{d['name']} carries nothing")


def test_fieldbus_is_not_an_ethernet_layer():
    """TopologyEngine allocates an interface on both ends of an Ethernet layer.
    Putting a sensor lead or RS-485 drop there would land it on eth0 of a device
    with no ports - the 'lie that reads back as a real termination'."""
    from core.topology_engine import TopologyEngine
    assert "fieldbus" not in TopologyEngine.ETHERNET_LAYERS


def test_fieldbus_edges_carry_no_interface():
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    fb = [e for e in data["edges"] if e.get("layer") == "fieldbus"]
    assert fb, "no fieldbus edges in the topology"
    for e in fb:
        assert e.get("src_iface") is None and e.get("dst_iface") is None, e


def test_every_node_carries_its_own_id():
    """TopologyEngine keys graph nodes by device.id, not by the node id. A device
    dict without an "id" makes Device.from_dict mint a random uuid, so the node
    lands under an id nothing references and EVERY edge to it is silently dropped
    by add_link's node check — no error, the device just floats.

    That shipped: four carriers lost all 34 of their edges on load while looking
    fine in the device list."""
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    bad = [n["id"] for n in data["nodes"] if n["device"].get("id") != n["id"]]
    assert not bad, f"nodes whose device.id != node id: {bad}"


def test_the_loaded_graph_keeps_every_edge():
    """Round-trip the topology through TopologyEngine and confirm no layer loses
    edges. Comparing counts is what exposes a silently-refused add_link."""
    from core.topology_engine import TopologyEngine
    import collections
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    on_disk = collections.Counter(e.get("layer") for e in data["edges"])
    topo = TopologyEngine()
    topo.from_dict(data)
    loaded = collections.Counter(d.get("layer") for _, _, d in topo.graph.edges(data=True))
    assert loaded == on_disk, (
        f"edges lost on load: "
        f"{ {k: on_disk[k] - loaded.get(k, 0) for k in on_disk if loaded.get(k, 0) != on_disk[k]} }")


def test_carriers_are_reachable_in_the_loaded_graph():
    from core.topology_engine import TopologyEngine
    topo = TopologyEngine()
    topo.from_dict(json.loads(TOPOLOGY.read_text(encoding="utf-8")))
    devs = {d.id: d for d in topo.get_all_devices()}
    carriers = [i for i, d in devs.items()
                if d.device_type in (DeviceType.MODBUS_GATEWAY, DeviceType.BACNET_ROUTER)]
    assert carriers
    for i in carriers:
        assert topo.graph.degree(i) > 0, f"{devs[i].name} floats in the loaded graph"

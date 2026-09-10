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
    # 20 on compute racks, 12 on the network racks that used to carry none.
    assert len(probes) == 32
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


def test_two_probes_never_claim_the_same_index(shipped):
    """Two probes sharing an index would overwrite each other in the strip's
    table, and the width of a claim depends on how the strip enumerates.

    A Raritan PX daisy-chains: every channel takes its own consecutive slot,
    so a T3H1 claims four and a CC2 two. An APC AP8000 has discrete sensor
    PORTS: a probe is one row and its temperature and humidity are columns on
    it, so it claims exactly one index however many channels it carries.
    """
    from core.device_manager import probe_channels

    by_host: dict = {}
    for d in shipped:
        if d.host_pdu_ip:
            by_host.setdefault(d.host_pdu_ip, []).append(d)
    assert by_host
    for host, probes in by_host.items():
        used = set()
        for p in probes:
            chained = p.model_name.startswith("Raritan")
            width = len(probe_channels(p.model_name)) if chained else 1
            for off in range(width):
                slot = p.sensor_slot + off
                assert slot not in used, f"{host}: index {slot} claimed twice"
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


def test_the_measurement_row_publishes_state_where_state_belongs(host_pdu):
    """PDU2-MIB's measurement row is isAvailable, timeStamp, STATE, value.

    Column 3 used to carry the sensor TYPE, with the state parked in a column
    5 this table does not have. Column 3 is exactly where a poller - and this
    simulator's own trap plane - reads externalState, so both saw a type code
    where they expected a state enum, and never saw a state at all. Latent
    until a Raritan strip carried a probe, which the network racks now do.
    """
    v = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    for slot in range(1, 7):
        assert v[f"{_RARITAN_SENSOR}.3.1.{slot}"] == "4"     # normal
        assert v[f"{_RARITAN_SENSOR}.1.1.{slot}"] == "1"     # available
    assert f"{_RARITAN_SENSOR}.5.1.1" not in v


def test_the_slot_index_still_identifies_the_sensor(host_pdu):
    """Type is configuration and does not belong in a measurement row; the
    slot is what says which sensor a reading came from, and the chain is
    enumerated in model order so a slot means one thing."""
    v = _by_oid(SNMPRecGenerator._pdu_sensor_entries(host_pdu))
    assert v[f"{_RARITAN_SENSOR}.2.1.1"] == "1"
    assert v[f"{_RARITAN_SENSOR}.2.1.6"] == "6"


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


def test_the_fixture_models_rack_probes_on_a_pdu():
    """The harness must model the plant the simulator actually has. Rack probes
    holding their own IPs was the last place it still described the pre-migration
    world — and a fixture that models a device the code cannot produce is a
    harness that passes while the real thing is broken."""
    import sys
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import build_plant
    p = build_plant(Path(tempfile.mkdtemp()), servers=50, installed_modules=6,
                    rack_probes=3)
    devs = p.dm.get_all_devices()
    probes = [d for d in devs if d.name.startswith("SNS")]
    pdus = [d for d in devs if d.device_type == DeviceType.PDU]
    assert len(probes) == 3 and len(pdus) == 1
    host = pdus[0]
    for d in probes:
        assert not d.ip_address and not d.mgmt_ip, d.name
        assert d.interface_count == 0 and not d.interfaces, d.name
        assert d.snmp_port == 0 and not d.metrics_enabled, d.name
        assert d.host_pdu_ip == host.mgmt_ip
    slots = [d.sensor_slot for d in probes]
    assert len(slots) == len(set(slots)), "two probes share a slot"
    assert set(host.sensor_children) == {d.name for d in probes}


# ─────────────────────────────────────────────────
#  Which probe spoke
# ─────────────────────────────────────────────────
def _probe(name, model, slot):
    from core.device_manager import Vendor

    d = Device(name=name, device_type=DeviceType.SENSOR, vendor=Vendor.RARITAN,
               ip_address="", model_name=model)
    d.attach_to_sensor_port("10.52.11.30", slot)
    return d


def _slot_in_trap(device, trap_type):
    from core import vendor_oids
    from core.trap_engine import TrapEngine

    vbs = {str(o): v for o, v in TrapEngine._vendor_varbinds(device, trap_type)}
    return vbs.get(vendor_oids.RARITAN["externalNumber"])


def test_a_notification_names_the_slot_it_came_from():
    """Intake, mid-rack and exhaust are three temperatures from one strip, on
    one OID, with one sensor type. The slot is the only thing that separates
    them, and it is the index a poller reads them at."""
    from core.trap_definitions import TrapType

    probe = _probe("SEN1-DC1-HA-R2-01", "Raritan DPX2-T3H1", 3)
    assert int(_slot_in_trap(probe, TrapType.SENSOR_AMBIENT_TEMP_HIGH)) == 3
    assert int(_slot_in_trap(probe, TrapType.SENSOR_MID_TEMP_HIGH)) == 4
    assert int(_slot_in_trap(probe, TrapType.SENSOR_OUTLET_TEMP_HIGH)) == 5
    assert int(_slot_in_trap(probe, TrapType.SENSOR_HIGH_HUMIDITY)) == 6
    # A recovery names the same slot as its raise, or the clear would land on
    # a different probe than the alarm it ends.
    assert (_slot_in_trap(probe, TrapType.SENSOR_AMBIENT_TEMP_NORMAL)
            == _slot_in_trap(probe, TrapType.SENSOR_AMBIENT_TEMP_HIGH))


def test_the_slot_is_the_one_the_table_publishes_it_at():
    """The notification and the polled table index off one layout. When they
    drifted, a trap pointed at a reading the table did not hold."""
    from core.trap_definitions import TrapType

    leak = _probe("LEAK1-DC1-HA-R2-01", "Raritan DPX2-CC2", 1)
    _publish("LEAK1-DC1-HA-R2-01", "Raritan DPX2-CC2", 1)
    pdu = Device(name="PDUA-DC1-HA-R2-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.30",
                 model_name="Raritan PX2-5170CR")
    pdu.sensor_children = ["LEAK1-DC1-HA-R2-01"]
    table = _by_oid(SNMPRecGenerator._pdu_sensor_entries(pdu))

    # A CC2 puts its water rope first, so its temperature is the SECOND slot.
    slot = int(_slot_in_trap(leak, TrapType.SENSOR_AMBIENT_TEMP_HIGH))
    assert slot == 2
    assert table[f"{_RARITAN_SENSOR}.3.1.{slot}"] == "10"   # a temperature
    dss._ext_state_cache.pop("LEAK1-DC1-HA-R2-01", None)


def test_a_condition_with_no_channel_sends_no_slot():
    """A dew point is derived from two other readings and a load is not on the
    sensor port at all. Naming a slot for either would invent an index."""
    from core.trap_definitions import TrapType

    probe = _probe("SEN1-DC1-HA-R2-01", "Raritan DPX2-T3H1", 3)
    assert _slot_in_trap(probe, TrapType.DEWPOINT_ALERT) is None
    pdu = Device(name="PDUA-DC1-HA-R2-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.30",
                 model_name="Raritan PX2-5170CR")
    assert _slot_in_trap(pdu, TrapType.PDU_LOAD_HIGH) is None
    # The strip's own probe has no chain slot; it is the one the PDU publishes
    # at slot 1 when nothing is plugged into its sensor port.
    assert int(_slot_in_trap(pdu, TrapType.PDU_TEMP_HIGH)) == 1


def test_a_probe_fits_the_strip_it_is_plugged_into(shipped):
    """There is no cable that puts a Raritan DPX2 on an APC strip.

    An AP8000 takes an AP9335T or AP9335TH on its RJ-45 sensor ports; a
    Raritan PX daisy-chains DPX2 units on its own. The part and the strip
    come from the same vendor because the port only accepts that vendor's
    lead, and the tree the reading is served on follows from it.
    """
    by_ip = {d.mgmt_ip: d for d in shipped if d.mgmt_ip}
    fitted = [d for d in shipped if d.host_pdu_ip]
    assert fitted
    for probe in fitted:
        strip = by_ip[probe.host_pdu_ip]
        assert probe.vendor == strip.vendor, (
            f"{probe.name} ({probe.model_name}) is fitted to "
            f"{strip.name} ({strip.model_name})")


def test_the_row_ends_are_instrumented(shipped):
    """Sampling covers each row's ends, not just the head of it.

    Every probe used to sit in racks one to three of one row, which left the
    far end of every row - where containment leaks and the CRAH throw is
    weakest - with no measurement at all.
    """
    racked = [d for d in shipped if d.host_pdu_ip]
    by_row = {}
    for d in racked:
        by_row.setdefault((d.datacenter, d.room, d.rack_row), set()).add(d.rack_num)
    assert by_row, "no probes are fitted"
    server_racks = {}
    for d in shipped:
        if d.device_type == DeviceType.SERVER and d.rack_num:
            server_racks.setdefault((d.datacenter, d.room, d.rack_row), set()).add(d.rack_num)
    for row, probed in by_row.items():
        racks = server_racks.get(row)
        if not racks:
            continue
        assert min(racks) in probed, f"{row}: the first rack has no probe"
        assert max(racks) in probed, f"{row}: the last rack has no probe"


def test_a_pair_on_one_rack_reads_two_heights(shipped):
    """A single sensor per rack cannot show stratification, which is the
    failure this instrumentation exists to see. Where two are fitted they sit
    at the bottom and the top of the front door, not side by side."""
    by_rack = {}
    for d in shipped:
        if d.host_pdu_ip:
            by_rack.setdefault((d.datacenter, d.room, d.rack_row, d.rack_num), []).append(d)
    pairs = [v for v in by_rack.values() if len(v) > 1]
    assert pairs, "no rack carries a pair"
    for probes in pairs:
        units = sorted(p.rack_unit for p in probes)
        assert units[0] < units[-1], [p.name for p in probes]
        assert units[-1] - units[0] >= 20, "a pair should span the rack"


def test_a_probe_has_no_power_cord(shipped):
    """It is bus-powered off the sensor port it plugs into.

    An AP9335 and a DPX2 have no plug: the strip's own logic supply drives
    them down the same lead that carries the reading. Giving one a cord to
    the B-side strip modelled a second, independent feed the hardware does
    not have - it would read as dual-fed when in truth it dies with its host
    - and it billed a phantom load to whichever strip the cord landed on.
    """
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    by_id = {n["id"]: n["device"] for n in data["nodes"]}
    probes = {n["id"] for n in data["nodes"] if n["device"].get("host_pdu_ip")}
    assert probes
    for e in data["edges"]:
        if e.get("layer") != "power":
            continue
        assert e["src"] not in probes and e["dst"] not in probes, (
            f"{by_id[e['src']]['name']} -> {by_id[e['dst']]['name']}")
    for d in shipped:
        if d.host_pdu_ip:
            assert not d.power_draw_w, f"{d.name} draws its own power"


def test_a_sensor_lead_never_leaves_its_rack(shipped):
    """An RJ-45 sensor lead is a metre long. A fieldbus edge that crosses a
    rack is a cable nobody could run, and one that crosses a room or a
    datacentre is a reading attributed to the wrong floor."""
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    by_id = {n["id"]: n["device"] for n in data["nodes"]}
    probes = {n["id"] for n in data["nodes"] if n["device"].get("host_pdu_ip")}
    seen = 0
    for e in data["edges"]:
        if e.get("layer") != "fieldbus":
            continue
        a, b = by_id[e["src"]], by_id[e["dst"]]
        if e["src"] not in probes and e["dst"] not in probes:
            continue
        seen += 1
        here = (a["datacenter"], a["room"], a["rack_row"], a["rack_num"])
        there = (b["datacenter"], b["room"], b["rack_row"], b["rack_num"])
        assert here == there, f"{a['name']} -> {b['name']}"
    assert seen, "no probe leads in the topology"


def test_nothing_is_wired_across_datacentres(shipped):
    """Two sites share no cable, no bus and no pipe. An edge that crosses
    them is a wiring error that reads back as a real termination."""
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    by_id = {n["id"]: n["device"] for n in data["nodes"]}
    for e in data["edges"]:
        a, b = by_id.get(e["src"]), by_id.get(e["dst"])
        if not a or not b:
            continue
        assert a["datacenter"] == b["datacenter"], (
            f"{e.get('layer')}: {a['name']} -> {b['name']}")

"""Plant header instruments behind a Modbus gateway.

The 12 chilled-/condenser-water instruments used to hold their own management IPs
and their own SNMP agents. No such device exists: a thermowell is an RTD in a pipe
wired to a transmitter, with no processor and no network port. What a real site
puts on the network is the gateway that fronts the RS-485 trunk.

What must survive the change:
  * the instruments keep their identity (_probe_role still resolves them), so the
    cooling model and the 22 plant-probe trap rules are untouched;
  * their readings still reach an NMS over SNMP — as indexed rows in the
    GATEWAY's ENTITY-SENSOR table;
  * an instrument never gets an SNMP agent of its own again, because snmpsim
    serves any file in the dataset directory and an orphan is a live agent
    answering for a device that cannot exist;
  * traps raised on an instrument source from the gateway, since Modbus has no
    unsolicited messaging and a transmitter cannot send one.
"""
from __future__ import annotations

import json
import socket
import struct
from pathlib import Path

import pytest

import core.device_state_store as dss
from core.device_manager import Device, DeviceType, Vendor
from core.modbus_register_map import get_probe_map
from core.snmprec_generator import SNMPRecGenerator, _ENTITY_SENSOR
from simulator.modbus_controller import ModbusController

HOST = "127.0.0.1"
TOPOLOGY = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"

_ENTITY_PHY = "1.3.6.1.2.1.47.1.1.1.1"
PROBE_ORDER = ["CHWS", "CHWR", "FLOW", "CWS", "CWR", "CTB"]


# ─────────────────────────────────────────────────────────────────────────────
#  Shipped topology
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def shipped():
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    return [Device.from_dict(n["device"]) for n in data["nodes"]]


def test_no_plant_instrument_holds_an_address(shipped):
    rtu = [d for d in shipped if d.modbus_role == "rtu_slave"]
    assert len(rtu) == 12
    for d in rtu:
        assert not d.ip_address and not d.mgmt_ip, (
            f"{d.name} still holds an address — a thermowell is not a network node")
        assert d.modbus_gateway_ip
        assert 1 <= d.modbus_unit_id <= 6


def test_each_datacenter_has_one_gateway_fronting_six_instruments(shipped):
    gws = [d for d in shipped if d.device_type == DeviceType.MODBUS_GATEWAY]
    assert len(gws) == 2
    for gw in gws:
        assert gw.mgmt_ip
        assert len(gw.modbus_children) == 6
        # Index order is the ENTITY-SENSOR index order a poller template binds to.
        assert [c.split("-")[0] for c in gw.modbus_children] == PROBE_ORDER


def test_instruments_point_at_a_gateway_that_exists(shipped):
    gw_ips = {d.mgmt_ip for d in shipped if d.device_type == DeviceType.MODBUS_GATEWAY}
    for d in [x for x in shipped if x.modbus_role == "rtu_slave"]:
        assert d.modbus_gateway_ip in gw_ips


def test_migration_released_addresses_without_collisions(shipped):
    seen = {}
    for d in shipped:
        for ip in (d.ip_address, d.mgmt_ip):
            if not ip:
                continue
            assert ip not in seen, f"{ip} claimed by both {seen[ip]} and {d.name}"
            seen[ip] = d.name


def test_an_instrument_has_no_ethernet_port(shipped):
    """A saved topology's empty interface list means 'generation stands', so
    clearing the port in the migration tool alone let it grow straight back. The
    rule has to live in __post_init__ next to the passive-panel rule, or the
    canvas, the port counts and the interface tables all keep showing an eth0
    that an RTD in a pipe does not have."""
    for d in [x for x in shipped if x.modbus_role == "rtu_slave"]:
        assert d.interface_count == 0 and not d.interfaces, d.name
        assert d.snmp_port == 0 and not d.metrics_enabled, d.name


def test_the_gateway_does_have_one(shipped):
    for gw in [d for d in shipped if d.device_type == DeviceType.MODBUS_GATEWAY]:
        assert gw.interface_count == 1
        assert [i.name for i in gw.interfaces] == ["eth0"]


def test_the_rule_survives_a_caller_handing_it_an_address():
    """Same guarantee the passive-panel rule gives: the type wins over the input,
    so nothing can hand a transmitter an IP and an SNMP port it cannot answer on."""
    d = Device(name="CHWS-DC1-CP", device_type=DeviceType.SENSOR,
               vendor=Vendor.VERTIV, model_name="Plant CHW Supply Temp",
               ip_address="10.9.9.9", mgmt_ip="10.9.9.10", snmp_port=161,
               modbus_role="rtu_slave")
    assert not d.ip_address and not d.mgmt_ip
    assert d.snmp_port == 0
    assert d.interface_count == 0


def test_identity_survives_the_loss_of_the_ip(shipped):
    """_probe_role reads the model prefix and the name code, neither of which the
    migration touched — that is why the cooling model and trap rules are unaffected."""
    from core.device_state_store import _probe_role
    roles = {_probe_role(d) for d in shipped if d.modbus_role == "rtu_slave"}
    assert roles == {"chw_supply", "chw_return", "chw_flow",
                     "cw_supply", "cw_return", "ct_basin"}


# ─────────────────────────────────────────────────────────────────────────────
#  SNMP republish
# ─────────────────────────────────────────────────────────────────────────────
def _gateway(tmp_path, children, ip="10.52.14.19"):
    gw = Device(name="MBGW1-DC1-CP", device_type=DeviceType.MODBUS_GATEWAY,
                vendor=Vendor.MOXA, model_name="Moxa MGate MB3480",
                ip_address="", mgmt_ip=ip)
    gw.modbus_role = "gateway"
    gw.modbus_children = list(children)
    return gw


@pytest.fixture
def published():
    """Live readings for one DC's trunk, as the store would publish them."""
    vals = {
        "CHWS-DC1-CP": {"water_temp": 7.2},
        "CHWR-DC1-CP": {"water_temp": 12.4},
        "FLOW-DC1-CP": {"water_flow_lps": 21.5},
        "CWS-DC1-CP":  {"water_temp": 30.6},
        "CWR-DC1-CP":  {"water_temp": 35.4},
        "CTB-DC1-CP":  {"water_temp": 27.1},
    }
    dss._ext_state_cache.update(vals)
    yield vals
    for k in vals:
        dss._ext_state_cache.pop(k, None)


def test_gateway_publishes_every_instrument_as_an_indexed_row(tmp_path, published):
    gw = _gateway(tmp_path, published.keys())
    entries = SNMPRecGenerator._gateway_probe_entries(gw)
    oids = {e[0] for e in entries}
    for idx in range(1, 7):
        assert f"{_ENTITY_SENSOR}.4.{idx}" in oids      # entPhySensorValue
        assert f"{_ENTITY_SENSOR}.5.{idx}" in oids      # operStatus


def test_indexed_rows_carry_names(tmp_path, published):
    """Six anonymous values are unusable — the poller cannot tell index 3 from 5."""
    gw = _gateway(tmp_path, published.keys())
    by_oid = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    assert by_oid[f"{_ENTITY_PHY}.7.1"] == "CHWS-DC1-CP"
    assert by_oid[f"{_ENTITY_PHY}.7.3"] == "FLOW-DC1-CP"
    assert by_oid[f"{_ENTITY_PHY}.7.6"] == "CTB-DC1-CP"


def test_values_and_types_match_the_instrument_kind(tmp_path, published):
    gw = _gateway(tmp_path, published.keys())
    by_oid = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    # Index 1 = CHWS thermowell: celsius (type 8), x10.
    assert by_oid[f"{_ENTITY_SENSOR}.1.1"] == "8"
    assert by_oid[f"{_ENTITY_SENSOR}.4.1"] == "72"
    # Index 3 = flow meter: 'other' (type 12), x100 — a flow meter is not a
    # thermometer, and publishing a temperature on it was the original bug.
    assert by_oid[f"{_ENTITY_SENSOR}.1.3"] == "12"
    assert by_oid[f"{_ENTITY_SENSOR}.4.3"] == "2150"


def test_unpublished_reading_is_unavailable_not_a_plausible_number(tmp_path):
    gw = _gateway(tmp_path, ["CHWS-DC1-CP"])
    dss._ext_state_cache.pop("CHWS-DC1-CP", None)
    by_oid = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    assert by_oid[f"{_ENTITY_SENSOR}.5.1"] == "2"      # operStatus unavailable
    assert by_oid[f"{_ENTITY_SENSOR}.4.1"] == "0"


def test_gateway_rows_track_the_store(tmp_path, published):
    """The patcher only rewrites lines that already exist, so the generated file
    and the live patch must agree on the OID set or values freeze at build time."""
    gw = _gateway(tmp_path, published.keys())
    first = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    dss._ext_state_cache["CHWS-DC1-CP"]["water_temp"] = 9.9
    second = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    assert first.keys() == second.keys()
    assert second[f"{_ENTITY_SENSOR}.4.1"] == "99"


def test_an_rtu_slave_never_gets_its_own_dataset(tmp_path):
    """snmpsim serves any file in the directory, so a file written here would be a
    live agent answering for a device that has no address at all."""
    from core.topology_engine import TopologyEngine
    probe = Device(name="CHWS-DC1-CP", device_type=DeviceType.SENSOR,
                   vendor=Vendor.VERTIV, model_name="Plant CHW Supply Temp",
                   ip_address="", mgmt_ip="")
    probe.modbus_role = "rtu_slave"
    probe.modbus_unit_id = 1
    probe.modbus_gateway_ip = "10.52.14.19"
    gen = SNMPRecGenerator(str(tmp_path))
    assert gen.generate_device(probe, TopologyEngine()) is None
    assert list(Path(tmp_path).glob("*.snmprec")) == []


# ─────────────────────────────────────────────────────────────────────────────
#  Traps
# ─────────────────────────────────────────────────────────────────────────────
def test_trap_from_an_instrument_sources_from_its_gateway():
    """Modbus has no unsolicited messaging — a transmitter cannot raise a trap, so
    the gateway raises it on the instrument's behalf, exactly as a BMS does."""
    from core.trap_engine import _trap_source_ip
    probe = Device(name="CHWR-DC1-CP", device_type=DeviceType.SENSOR,
                   vendor=Vendor.VERTIV, model_name="Plant CHW Return Temp",
                   ip_address="", mgmt_ip="")
    probe.modbus_gateway_ip = "10.52.14.19"
    assert _trap_source_ip(probe) == "10.52.14.19"


def test_an_ordinary_probe_is_unaffected_by_the_fallback():
    from core.trap_engine import _trap_source_ip
    rack = Device(name="ENV1-DC1-SH", device_type=DeviceType.SENSOR,
                  vendor=Vendor.RARITAN, model_name="Raritan DPX2-T3H1",
                  ip_address="", mgmt_ip="10.52.11.7")
    assert _trap_source_ip(rack) == "10.52.11.7"


# ─────────────────────────────────────────────────────────────────────────────
#  Modbus RTU side
# ─────────────────────────────────────────────────────────────────────────────
def test_transmitter_maps_are_tiny():
    """A real transmitter map IS two or three registers. Padding it with invented
    diagnostics would be the fabrication this simulator refuses elsewhere."""
    for role in ("chw_supply", "chw_flow"):
        mm = get_probe_map(role)
        assert sum(len(v) for v in mm.points.values()) <= 3


def test_flow_meter_and_thermowell_get_different_maps():
    assert get_probe_map("chw_flow") is not get_probe_map("chw_supply")
    assert get_probe_map("cw_return") is get_probe_map("chw_supply")


def _free_port() -> int:
    s = socket.socket()
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def trunk(published):
    ctrl = ModbusController()
    port = _free_port()
    devices = [{"ip": HOST, "name": "MBGW1-DC1-CP",
                "device_type": "modbus_gateway", "role": "gateway"}]
    roles = {"CHWS-DC1-CP": "chw_supply", "CHWR-DC1-CP": "chw_return",
             "FLOW-DC1-CP": "chw_flow", "CWS-DC1-CP": "cw_supply",
             "CWR-DC1-CP": "cw_return", "CTB-DC1-CP": "ct_basin"}
    for unit, (name, role) in enumerate(roles.items(), start=1):
        devices.append({"ip": "", "name": name, "device_type": "sensor",
                        "unit_id": unit, "role": "rtu_slave",
                        "gateway_ip": HOST, "probe_role": role})
    assert ctrl.start(devices, port=port)
    ctrl.tick(1.0)
    yield ctrl, port
    ctrl.stop()


def _read(port: int, unit: int, addr: int = 0, count: int = 1) -> bytes:
    pdu = struct.pack(">BHH", 4, addr, count)
    frame = struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
    s = socket.create_connection((HOST, port), timeout=5)
    try:
        s.sendall(frame)
        head = s.recv(7)
        return s.recv(struct.unpack(">H", head[4:6])[0] - 1)
    finally:
        s.close()


def test_each_instrument_answers_on_its_own_unit_id(trunk):
    _, port = trunk
    assert struct.unpack(">h", _read(port, 1)[2:4])[0] == 72       # CHWS 7.2 C x10
    assert struct.unpack(">h", _read(port, 2)[2:4])[0] == 124      # CHWR 12.4 C x10
    assert struct.unpack(">H", _read(port, 3)[2:4])[0] == 2150     # FLOW 21.5 l/s x100
    assert struct.unpack(">h", _read(port, 6)[2:4])[0] == 271      # CTB 27.1 C x10


def test_the_same_number_reaches_both_planes(trunk, published):
    """The Modbus register and the gateway's ENTITY-SENSOR row are two renderings
    of one ext value. If they can disagree, one of them is lying."""
    _, port = trunk
    gw = _gateway(None, published.keys())
    by_oid = {e[0]: e[2] for e in SNMPRecGenerator._gateway_probe_entries(gw)}
    modbus_chws = struct.unpack(">h", _read(port, 1)[2:4])[0]
    assert str(modbus_chws) == by_oid[f"{_ENTITY_SENSOR}.4.1"]


def test_a_dead_instrument_answers_0x0b_over_a_live_gateway(trunk):
    ctrl, port = trunk
    ctrl.tick(1.0, unpowered_names={"CWR-DC1-CP"})
    body = _read(port, 5)
    assert (body[0], body[1]) == (0x84, 0x0B)
    # The trunk is not down — its neighbours still answer.
    assert struct.unpack(">h", _read(port, 1)[2:4])[0] == 72


def test_every_transmitter_point_is_reachable_through_the_gateway(trunk):
    """The trunk tests above read input register 0 only. A transmitter also
    carries a Reading_Valid discrete, and that is served by a different function
    code (FC02, not FC04) — so it exercises a path the value read never touches."""
    import struct as _s
    from core.modbus_register_map import SPACE_DISCRETE, SPACE_INPUT
    _ctrl, port = trunk
    roles = {1: "chw_supply", 2: "chw_return", 3: "chw_flow",
             4: "cw_supply", 5: "cw_return", 6: "ct_basin"}
    for unit, role in roles.items():
        mm = get_probe_map(role)
        for p in mm.points[SPACE_INPUT]:
            body = _read(port, unit, addr=p.addr, count=1)
            assert not (body[0] & 0x80), (
                f"unit {unit} ({role}) {p.name} -> exception 0x{body[1]:02X}")
            raw = _s.unpack(">H", body[2:4])[0]
            if p.dtype == "s16" and raw >= 0x8000:
                raw -= 0x10000
            assert raw / p.scale != 0 or role == "chw_flow", (
                f"unit {unit} {p.name} reads zero — the store published nothing")
        for p in mm.points[SPACE_DISCRETE]:
            pdu = _s.pack(">BHH", 2, p.addr, 1)
            frame = _s.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu
            s = socket.create_connection((HOST, port), timeout=5)
            try:
                s.sendall(frame)
                head = s.recv(7)
                body = s.recv(_s.unpack(">H", head[4:6])[0] - 1)
            finally:
                s.close()
            assert not (body[0] & 0x80), (
                f"unit {unit} ({role}) {p.name} -> exception 0x{body[1]:02X}")
            assert (body[2] & 1) == 1, (
                f"unit {unit} {p.name} reads invalid while the store is publishing")


def test_the_flow_meter_agrees_with_the_loop_it_measures(trunk, published):
    """A magnetic flow meter on the CHW main and the pump pushing that water are
    rendered by two different planes from one ext state. If they can disagree,
    one of them is lying — and a BMS trending both would see a phantom imbalance."""
    import struct as _s
    _ctrl, port = trunk
    body = _read(port, 3, addr=0, count=1)          # FLOW, unit 3
    assert not (body[0] & 0x80)
    modbus_lps = _s.unpack(">H", body[2:4])[0] / 100.0
    assert modbus_lps == pytest.approx(
        float(published["FLOW-DC1-CP"]["water_flow_lps"]), abs=0.01), (
        "the Modbus flow reading does not match the published loop flow")

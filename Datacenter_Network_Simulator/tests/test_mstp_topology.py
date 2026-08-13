"""The shipped topology after pumps and valves moved onto MS/TP trunks.

Their telemetry is deliberately untouched — these devices keep their BACnet
object trees and PlantTelemetryEngine, so the cooling model, _plant_state_cache
and the override channel all carry on unchanged. Only the addressing moved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.device_manager import Device, DeviceType

TOPOLOGY = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"


@pytest.fixture(scope="module")
def shipped():
    data = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    return [Device.from_dict(n["device"]) for n in data["nodes"]]


def test_no_pump_or_valve_holds_an_address(shipped):
    field = [d for d in shipped
             if d.device_type in (DeviceType.PUMP, DeviceType.VALVE)]
    assert len(field) == 18
    for d in field:
        assert not d.ip_address and not d.mgmt_ip, (
            f"{d.name} still holds an address — an MS/TP actuator is two wires "
            f"and a MAC, not a network node")
        assert d.mstp_mac and d.mstp_net and d.mstp_router_ip


def test_field_devices_have_no_ethernet_port(shipped):
    for d in shipped:
        if d.mstp_mac:
            assert d.interface_count == 0 and not d.interfaces, d.name
            assert d.snmp_port == 0 and not d.metrics_enabled, d.name


def test_each_datacenter_has_one_router_carrying_its_trunk(shipped):
    routers = [d for d in shipped if d.device_type == DeviceType.BACNET_ROUTER]
    assert len(routers) == 2
    for r in routers:
        assert r.mgmt_ip and r.mstp_net
        assert len(r.mstp_children) == 9        # 7 pumps + 2 valves
        assert r.interface_count == 1           # the trunk's only Ethernet port


def test_macs_are_unique_within_a_trunk(shipped):
    """Two devices on one MAC is a wiring fault a real trunk cannot express."""
    by_net: dict = {}
    for d in shipped:
        if d.mstp_mac:
            by_net.setdefault(d.mstp_net, []).append(d.mstp_mac)
    assert by_net
    for net, macs in by_net.items():
        assert len(macs) == len(set(macs)), f"duplicate MAC on network {net}"


def test_pumps_and_valves_cannot_collide_on_a_mac(shipped):
    """Separate MAC bases per class is what guarantees it, so pin the split."""
    pumps = {d.mstp_mac for d in shipped if d.device_type == DeviceType.PUMP}
    valves = {d.mstp_mac for d in shipped if d.device_type == DeviceType.VALVE}
    assert pumps and valves and not (pumps & valves)


def test_macs_are_valid_mstp_master_addresses(shipped):
    """MS/TP masters are 0..127; anything above is a slave-only address and no
    client will poll it."""
    for d in shipped:
        if d.mstp_mac:
            assert 0 <= d.mstp_mac <= 127, f"{d.name} MAC {d.mstp_mac}"


def test_each_datacenter_trunk_has_its_own_network_number(shipped):
    nets = {d.mstp_net for d in shipped if d.mstp_net and d.mstp_mac}
    assert len(nets) == 2, "both trunks share a network number"


def test_every_field_device_points_at_a_real_router(shipped):
    router_ips = {d.mgmt_ip for d in shipped
                  if d.device_type == DeviceType.BACNET_ROUTER}
    for d in shipped:
        if d.mstp_mac:
            assert d.mstp_router_ip in router_ips, d.name


def test_router_children_match_the_devices_that_claim_it(shipped):
    by_ip = {d.mgmt_ip: d for d in shipped
             if d.device_type == DeviceType.BACNET_ROUTER}
    claimed: dict = {}
    for d in shipped:
        if d.mstp_mac:
            claimed.setdefault(d.mstp_router_ip, set()).add(d.name)
    for ip, router in by_ip.items():
        assert set(router.mstp_children) == claimed.get(ip, set()), (
            f"{router.name} trunk membership disagrees with the devices on it")


def test_no_duplicate_addresses_after_the_migration(shipped):
    seen = {}
    for d in shipped:
        for ip in (d.ip_address, d.mgmt_ip):
            if not ip:
                continue
            assert ip not in seen, f"{ip} claimed by {seen[ip]} and {d.name}"
            seen[ip] = d.name


def test_the_cooling_model_still_sees_them(shipped):
    """Identity is the name, and nothing about it changed — which is why the
    plant override channel and _plant_state_cache did not have to move."""
    names = {d.name for d in shipped if d.mstp_mac}
    assert "CHWP1-DC1-CP" in names and "VCHW-DC1-CP" in names


# ─────────────────────────────────────────────────────────────────────────────
#  Runtime attach + fixture fidelity
# ─────────────────────────────────────────────────────────────────────────────
def test_setting_mstp_mac_by_hand_is_not_enough():
    """The portless rule runs in __post_init__, so assigning the field afterwards
    leaves the device holding a port and an address it cannot have. This is why
    attach_to_mstp_trunk exists — pinned so nobody 'simplifies' it away."""
    from core.device_manager import Vendor
    d = Device(name="VCHW-DC1-CP", device_type=DeviceType.VALVE,
               vendor=Vendor.BELIMO, ip_address="", model_name="Belimo PR..A-BAC",
               mgmt_ip="10.9.9.9", snmp_port=161)
    d.mstp_mac = 20                       # the naive way
    assert d.mgmt_ip == "10.9.9.9" and d.interface_count > 0

    d.attach_to_mstp_trunk(2001, 20, "10.52.14.15")
    assert not d.mgmt_ip and not d.ip_address
    assert d.interface_count == 0 and not d.interfaces
    assert d.snmp_port == 0 and not d.metrics_enabled
    assert (d.mstp_net, d.mstp_mac, d.mstp_router_ip) == (2001, 20, "10.52.14.15")


def test_the_fixture_models_the_migrated_world():
    """The two-DC harness has already caught two real bugs. It only keeps doing
    that if it models the plant the simulator actually has — pumps and valves on
    a trunk, not holding IPs."""
    import sys, tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import build_plant
    p = build_plant(Path(tempfile.mkdtemp()), servers=200, installed_modules=6,
                    probes=True, valves=True)
    devs = p.dm.get_all_devices()
    field = [d for d in devs if d.device_type in (DeviceType.PUMP, DeviceType.VALVE)]
    routers = [d for d in devs if d.device_type == DeviceType.BACNET_ROUTER]
    assert field and len(routers) == 1
    for d in field:
        assert not d.ip_address and not d.mgmt_ip, d.name
        assert d.interface_count == 0 and not d.interfaces, d.name
        assert d.mstp_mac and d.mstp_router_ip == routers[0].mgmt_ip
    macs = [d.mstp_mac for d in field]
    assert len(macs) == len(set(macs))
    pumps = {d.mstp_mac for d in field if d.device_type == DeviceType.PUMP}
    valves = {d.mstp_mac for d in field if d.device_type == DeviceType.VALVE}
    assert not (pumps & valves)
    assert set(routers[0].mstp_children) == {d.name for d in field}

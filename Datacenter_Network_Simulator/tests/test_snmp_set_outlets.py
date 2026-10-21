"""The SNMP management agent switches PDU outlets, and its thresholds take effect.

Drives the real request handler with real SNMP messages: the bytes a collector
sends are what these tests send.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from pyasn1.codec.ber import decoder as ber_decoder
from pyasn1.codec.ber import encoder as ber_encoder
from pysnmp.proto import api as snmp_api

from core import snmp_set_agent as agent

PMOD = snmp_api.PROTOCOL_MODULES[snmp_api.SNMP_VERSION_2C]
IP = "10.52.12.26"


def _set(oid: str, value: int, community: str = IP) -> bytes:
    pdu = PMOD.SetRequestPDU()
    PMOD.apiPDU.set_defaults(pdu)
    PMOD.apiPDU.set_varbinds(pdu, [(oid, PMOD.Integer(value))])
    msg = PMOD.Message()
    PMOD.apiMessage.set_defaults(msg)
    PMOD.apiMessage.set_community(msg, community)
    PMOD.apiMessage.set_pdu(msg, pdu)
    return ber_encoder.encode(msg)


def _error_status(response: bytes) -> int:
    msg, _ = ber_decoder.decode(response, asn1Spec=PMOD.Message())
    return int(PMOD.apiPDU.get_error_status(PMOD.apiMessage.get_pdu(msg)))


class _Rules:
    """Just enough of the rule engine: one rule, and a record of overrides."""

    def __init__(self) -> None:
        self.set: list[tuple] = []

    def get_rule(self, name):
        return object() if name == "HighTemperature" else None

    def set_device_threshold(self, device_id, rule, field, value):
        self.set.append((device_id, rule, field, value))

    def get_device_threshold(self, *_a):
        return None


def _handler(outlet_cb=None, name="PDUA-DC1-HB-R2-05"):
    device = SimpleNamespace(name=name, ip_address=IP, mgmt_ip=IP)
    rules = _Rules()
    return agent._SnmpHandler(rules, None, lambda ip: device if ip == IP else None, None,
                              outlet_cb), device, rules


APC = "1.3.6.1.4.1.318.1.1.26.9.2.4.1.5"
RARITAN = "1.3.6.1.4.1.13742.6.4.1.2.1.2.1"


# ---------------------------------------------------------------- parsing

def test_the_two_vendors_control_oids_parse_to_an_outlet_and_an_action():
    assert agent.outlet_command(f"{APC}.12", 2) == (12, "off")
    assert agent.outlet_command(f"{APC}.1", 1) == (1, "on")
    assert agent.outlet_command(f"{APC}.7", 3) == (7, "cycle")
    assert agent.outlet_command(f"{RARITAN}.24", 0) == (24, "off")
    assert agent.outlet_command(f"{RARITAN}.24", 1) == (24, "on")
    assert agent.outlet_command(f"{RARITAN}.5", 2) == (5, "cycle")
    assert agent.outlet_command("1.3.6.1.2.1.1.5.0", 1) is None        # sysName: not ours
    assert agent.outlet_command(APC, 1) is None                        # no outlet index


def test_a_value_the_mib_does_not_define_is_refused():
    with pytest.raises(ValueError):
        agent.outlet_command(f"{APC}.3", 9)
    with pytest.raises(ValueError):
        agent.outlet_command(f"{RARITAN}.3", 7)


# ----------------------------------------------------------------- the SET

def test_an_apc_set_switches_the_named_outlet():
    calls = []
    h, device, _ = _handler(lambda d, o, a: calls.append((d.name, o, a)))
    rsp = h.handle(_set(f"{APC}.12", 2))
    assert _error_status(rsp) == 0
    assert calls == [(device.name, 12, "off")]


def test_a_raritan_set_cycles_the_named_outlet():
    calls = []
    h, _, _ = _handler(lambda d, o, a: calls.append((o, a)))
    assert _error_status(h.handle(_set(f"{RARITAN}.5", 2))) == 0
    assert calls == [(5, "cycle")]


def test_a_strip_that_cannot_switch_answers_nosuchname_like_real_gear():
    def refuse(*_):
        raise ValueError("metered, not switched")
    h, _, _ = _handler(refuse)
    assert _error_status(h.handle(_set(f"{APC}.3", 2))) == 2


def test_a_bad_command_value_is_wrongvalue_and_does_nothing():
    calls = []
    h, _, _ = _handler(lambda *a: calls.append(a))
    assert _error_status(h.handle(_set(f"{APC}.3", 9))) == 7
    assert calls == []


def test_an_unknown_community_gets_no_answer():
    h, _, _ = _handler(lambda *a: None)
    assert h.handle(_set(f"{APC}.3", 2, community="public")) is None   # not an IP
    assert _error_status(h.handle(_set(f"{APC}.3", 2, community="10.9.9.9"))) == 2


# ---------------------------------------------------- thresholds take effect

def test_a_threshold_set_is_stored_under_the_name_the_engine_looks_up():
    """Facts carry the device NAME as device_id and overrides are read back by
    it, so a threshold stored under the community (the IP) was answered and then
    never applied."""
    h, device, rules = _handler()
    assert _error_status(h.handle(_set(f"{agent._BASE}.6.0", 88))) == 0
    assert rules.set == [(device.name, "HighTemperature", "threshold", 88.0)]
    assert IP not in {k[0] for k in rules.set}


# ----------------------------------------------------- the shared helper

class _Store:
    def __init__(self) -> None:
        self.ext: dict = {}

    def ext_state_for(self, _dev):
        return self.ext


def _pdu(model="APC AP8941"):
    outlets = [SimpleNamespace(index=i) for i in range(1, 25)]
    from core.device_manager import DeviceType     # the SKU classifier compares the enum
    return SimpleNamespace(name="PDUA", model_name=model, outlets=outlets,
                           device_type=DeviceType.PDU)


def _state(store):
    return SimpleNamespace(state_store=store, trap_engine=None,
                           notify_ui=lambda *a, **k: None)


def test_apply_outlet_opens_and_closes_one_outlet():
    from api.routers.devices import apply_outlet
    store = _Store()
    s, dev = _state(store), _pdu()
    apply_outlet(s, dev, 4, "off")
    apply_outlet(s, dev, 9, "off")
    assert store.ext["pdu_outlets_off"] == [4, 9]
    apply_outlet(s, dev, 4, "on")
    assert store.ext["pdu_outlets_off"] == [9]


def test_apply_outlet_refuses_a_metered_strip_and_a_missing_outlet():
    from api.routers.devices import OutletRefused, apply_outlet
    s = _state(_Store())
    with pytest.raises(OutletRefused, match="metered"):
        apply_outlet(s, _pdu("APC AP8886"), 1, "off")
    with pytest.raises(OutletRefused, match="no outlet 99"):
        apply_outlet(s, _pdu(), 99, "off")
    with pytest.raises(OutletRefused):
        apply_outlet(s, _pdu(), 1, "sideways")


def test_a_cycle_opens_the_outlet_now_and_closes_it_again(monkeypatch):
    from api.routers import devices
    monkeypatch.setattr(devices, "CYCLE_OFF_S", 0.05)
    store = _Store()
    s, dev = _state(store), _pdu()
    devices.apply_outlet_command(s, dev, 6, "cycle")
    assert store.ext["pdu_outlets_off"] == [6]
    time.sleep(0.3)
    assert store.ext["pdu_outlets_off"] == []

"""A switch with no power has no gNMI.

Both PDUs under LF1-DC1-HA-R2-02 were tripped and the leaf went on streaming
telemetry, so the DCIM saw a healthy switch in a dark rack. SNMP and Redfish
already went silent for a dark device; gNMI now does too.
"""

from __future__ import annotations

import types

import grpc
import pytest

import core.device_state_store as dss
from simulator.gnmi_server import GNMIServicer, _import_stubs

IP = "10.0.0.11"
NAME = "LF1-TEST"


class _Aborted(Exception):
    pass


class _Ctx:
    def __init__(self):
        self.code = None
        self.checks = 0

    def abort(self, code, details):
        self.code = code
        raise _Aborted(details)

    def is_active(self):
        self.checks += 1
        return self.checks < 50

    def invocation_metadata(self):
        return []

    def peer(self):
        return "ipv4:127.0.0.1:5555"


class _Store:
    def _find_device(self, ip):
        return types.SimpleNamespace(name=NAME) if ip == IP else None

    def get_metrics(self, ip):
        return None


@pytest.fixture
def servicer(tmp_path):
    s = GNMIServicer(str(tmp_path))
    s._data[IP] = {"target": IP, "device_type": "switch",
                   "openconfig-system:system": {"state": {"hostname": "x"}}}
    s.set_state_store(_Store())
    yield s
    dss._unpowered_cache.discard(NAME)


def _get(servicer, ctx):
    gnmi_pb2, _ = _import_stubs()
    req = gnmi_pb2.GetRequest(prefix=gnmi_pb2.Path(target=IP))
    return servicer.Get(req, ctx)


def _subscribe(mode):
    gnmi_pb2, _ = _import_stubs()
    sl = gnmi_pb2.SubscriptionList(prefix=gnmi_pb2.Path(target=IP), mode=mode)
    sl.subscription.add(sample_interval=int(60e9))
    return iter([gnmi_pb2.SubscribeRequest(subscribe=sl)])


def test_a_powered_switch_answers(servicer):
    resp = _get(servicer, _Ctx())
    assert len(resp.notification) == 1


def test_a_dark_switch_refuses_get_as_unavailable(servicer):
    dss._unpowered_cache.add(NAME)
    ctx = _Ctx()
    with pytest.raises(_Aborted):
        _get(servicer, ctx)
    assert ctx.code == grpc.StatusCode.UNAVAILABLE


def test_a_dark_switch_refuses_subscribe(servicer):
    gnmi_pb2, _ = _import_stubs()
    dss._unpowered_cache.add(NAME)
    ctx = _Ctx()
    with pytest.raises(_Aborted):
        list(servicer.Subscribe(_subscribe(gnmi_pb2.SubscriptionList.ONCE), ctx))
    assert ctx.code == grpc.StatusCode.UNAVAILABLE


def test_a_stream_is_cut_when_the_switch_loses_power(servicer, monkeypatch):
    """Mid-stream, not at the next sample: the session drops when the power
    does, the way a TCP session to a dead box does."""
    gnmi_pb2, _ = _import_stubs()
    monkeypatch.setattr("simulator.gnmi_server.time.sleep", lambda s: None)
    ctx = _Ctx()
    stream = servicer.Subscribe(_subscribe(gnmi_pb2.SubscriptionList.STREAM), ctx)
    next(stream)                                  # initial snapshot arrives
    next(stream)                                  # sync marker
    dss._unpowered_cache.add(NAME)
    with pytest.raises(_Aborted):
        next(stream)
    assert ctx.code == grpc.StatusCode.UNAVAILABLE

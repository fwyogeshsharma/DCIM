"""Colo-provider mock API (docs/26 Phase 10/S) — OAuth2 client-credentials
token exchange, then bearer-authenticated cabinet telemetry.

Exercised over real HTTP via TestClient against the actual FastAPI app,
against a fake device_manager double rather than the real one - only
get_all_devices()/.name/.power_draw_w are ever touched by the router, so a
double is the honest minimum rather than standing up a whole topology.
"""
from __future__ import annotations

import time
import types

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.state import AppState

client = TestClient(app)


class _FakeDeviceManager:
    def __init__(self, devices):
        self._devices = devices

    def get_all_devices(self):
        return self._devices


@pytest.fixture
def provider_state():
    """A clean slate for AppState's provider fields, restored after the test -
    AppState is a process-wide singleton and other tests share it."""
    s = AppState.get()
    saved = (s.device_manager, s.provider_client_id, s.provider_client_secret,
             dict(s.provider_tokens))
    s.device_manager = _FakeDeviceManager([
        types.SimpleNamespace(name="CAB-01", power_draw_w=4200),
        types.SimpleNamespace(name="CAB-02", power_draw_w=0),
    ])
    s.provider_client_id = "dcim-poller"
    s.provider_client_secret = "dcim-poller-dev-secret"
    s.provider_tokens.clear()
    yield s
    s.device_manager, s.provider_client_id, s.provider_client_secret = saved[:3]
    s.provider_tokens.clear()
    s.provider_tokens.update(saved[3])


def _token(client_id="dcim-poller", client_secret="dcim-poller-dev-secret"):
    r = client.post("/api/provider/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": client_id, "client_secret": client_secret})
    return r


def test_token_issued_for_the_configured_credentials(provider_state):
    r = _token()
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0


def test_token_rejects_the_wrong_secret(provider_state):
    r = _token(client_secret="wrong")
    assert r.status_code == 401


def test_token_rejects_an_unsupported_grant_type(provider_state):
    r = client.post("/api/provider/oauth2/token", data={
        "grant_type": "password", "client_id": "dcim-poller",
        "client_secret": "dcim-poller-dev-secret"})
    assert r.status_code == 400


def test_telemetry_returns_the_devices_real_power_draw(provider_state):
    tok = _token().json()["access_token"]
    r = client.get("/api/provider/v1/cabinets/CAB-01/telemetry",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["power_w"] == 4200.0
    assert 15.0 <= body["temperature_c"] <= 30.0
    assert 20.0 <= body["humidity_pct"] <= 65.0


def test_environmentals_are_stable_across_repeated_polls(provider_state):
    """A real steady-state ambient sensor does not jitter every poll - and two
    test runs against the same topology must agree, or a flaky assertion
    elsewhere is blamed on the wrong thing."""
    tok = _token().json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    first = client.get("/api/provider/v1/cabinets/CAB-01/telemetry", headers=headers).json()
    second = client.get("/api/provider/v1/cabinets/CAB-01/telemetry", headers=headers).json()
    assert first["temperature_c"] == second["temperature_c"]
    assert first["humidity_pct"] == second["humidity_pct"]


def test_different_cabinets_get_different_environmentals(provider_state):
    tok = _token().json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    a = client.get("/api/provider/v1/cabinets/CAB-01/telemetry", headers=headers).json()
    b = client.get("/api/provider/v1/cabinets/CAB-02/telemetry", headers=headers).json()
    assert (a["temperature_c"], a["humidity_pct"]) != (b["temperature_c"], b["humidity_pct"])


def test_telemetry_requires_a_bearer_token(provider_state):
    r = client.get("/api/provider/v1/cabinets/CAB-01/telemetry")
    assert r.status_code == 401


def test_telemetry_rejects_an_unknown_token(provider_state):
    r = client.get("/api/provider/v1/cabinets/CAB-01/telemetry",
                   headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


def test_telemetry_rejects_an_expired_token(provider_state):
    tok = _token().json()["access_token"]
    provider_state.provider_tokens[tok] = time.time() - 1  # force expiry
    r = client.get("/api/provider/v1/cabinets/CAB-01/telemetry",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_telemetry_404s_for_a_cabinet_with_no_matching_device(provider_state):
    tok = _token().json()["access_token"]
    r = client.get("/api/provider/v1/cabinets/NO-SUCH-CABINET/telemetry",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


def test_rotating_credentials_invalidates_outstanding_tokens(provider_state):
    tok = _token().json()["access_token"]
    r = client.post("/api/provider/credentials", json={
        "client_id": "dcim-poller", "client_secret": "a-new-secret"})
    assert r.status_code == 200

    stale = client.get("/api/provider/v1/cabinets/CAB-01/telemetry",
                       headers={"Authorization": f"Bearer {tok}"})
    assert stale.status_code == 401

    fresh = _token(client_secret="a-new-secret")
    assert fresh.status_code == 200


def test_provider_mock_is_not_gated_by_this_apps_own_admin_auth(provider_state):
    """The whole point of Phase 10: a collector reaches this API with only
    the PROVIDER's own OAuth2 credentials, never this simulator's own admin
    session - so the token call above (no simulator auth header at all) must
    already have succeeded. This test exists to catch a future accidental
    `dependencies=_AUTH` on the provider router in api/main.py."""
    r = _token()
    assert r.status_code == 200

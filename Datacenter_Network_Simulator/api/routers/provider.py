"""Colo-provider mock API — an Equinix Smart View / API Plus shaped stand-in.

docs/26 Phase 10 (DCIM platform side) needs a real provider tenant to poll a
cabinet's power and environmental readings with no southbound device protocol
at all - only the provider's own REST API. This simulator has no such tenant,
so the acceptance criterion for that phase ("a mock Smart-View-like API in the
simulator drives cabinet power and environmentals into the same UI views as
SNMP-sourced data") depends on this router existing.

DELIBERATELY NOT behind this app's own admin bearer auth (`require_auth` /
`_AUTH` in api/main.py — see this router's registration there). A real colo
tenant authenticates against the PROVIDER's own OAuth2 client-credentials
grant, entirely separate from whatever session token an operator uses to
drive this simulator's own UI. Mixing the two would mean a collector could
only reach this API by also holding this simulator's own admin session,
which is backwards: the whole point of Phase 10 is that a provider tenant
gets telemetry with no access to anything else.

Cabinet identifiers are simulator device names directly - the same names a
Modbus/BACnet/SNMP endpoint would already use - rather than a separate
cabinet registry: a collector's `addressing.cabinet_id` just names whichever
device in this topology stands in for that cabinet's PDU.

power_w comes from that device's real `power_draw_w` (the same nameplate
figure the rest of this simulator already carries per device). temperature_c
and humidity_pct are NOT modelled per device anywhere in this simulator today
(a colo cabinet's own ambient sensor has no equivalent object here) - they
are deterministic, stable-per-device synthetic values in a realistic band,
not read from any real state. That is a real gap, not a hidden one: it is
exactly analogous to the DCIM platform's own provider.go decoding "a
representative shape, not a verified transcription of any real provider's
response" (docs/26 Phase 10) - this side of the same honesty.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import APIRouter, Form, Header, HTTPException

from api.state import AppState
from api.models.schemas import OkResponse, ProviderCredentialsRequest, ProviderTelemetryResponse

router = APIRouter(prefix="/provider", tags=["Colo Provider Mock"])

TOKEN_TTL_SECONDS = 3600


def _state() -> AppState:
    return AppState.get()


def _device_by_name(s: AppState, name: str):
    if not s.device_manager:
        return None
    for d in s.device_manager.get_all_devices():
        if d.name == name:
            return d
    return None


@router.post("/credentials", response_model=OkResponse)
def set_credentials(req: ProviderCredentialsRequest):
    """Change the OAuth2 client_id/client_secret this mock accepts.

    Defaults (dcim-poller / dcim-poller-dev-secret) work out of the box so a
    collector can be pointed at this simulator with no setup; this exists for
    a test that wants to exercise a credential rotation or a rejected secret.
    """
    s = _state()
    s.provider_client_id = req.client_id
    s.provider_client_secret = req.client_secret
    s.provider_tokens.clear()  # old tokens were issued under the old secret
    return OkResponse(message="provider credentials updated")


@router.post("/oauth2/token")
def issue_token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    """RFC 6749 SS4.4 client-credentials grant — the machine-to-machine token
    exchange dcim-platform's provider adapter (internal/adapters/provider/
    oauth.go) sends."""
    s = _state()
    if grant_type != "client_credentials":
        raise HTTPException(400, f"unsupported_grant_type: {grant_type}")
    if not (secrets.compare_digest(client_id, s.provider_client_id)
           and secrets.compare_digest(client_secret, s.provider_client_secret)):
        raise HTTPException(401, "invalid_client")

    token = secrets.token_urlsafe(32)
    s.provider_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL_SECONDS}


def _require_bearer(s: AppState, authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[len("Bearer "):]
    expiry = s.provider_tokens.get(token)
    if expiry is None:
        raise HTTPException(401, "unknown or revoked token")
    if expiry < time.time():
        del s.provider_tokens[token]
        raise HTTPException(401, "token expired")


@router.get("/v1/cabinets/{cabinet_id}/telemetry", response_model=ProviderTelemetryResponse)
def cabinet_telemetry(cabinet_id: str, authorization: str | None = Header(None)):
    s = _state()
    _require_bearer(s, authorization)

    device = _device_by_name(s, cabinet_id)
    if device is None:
        raise HTTPException(404, f"no such cabinet: {cabinet_id}")

    # Stable per-device synthetic environmentals - see module docstring. Seeded
    # off the device name (not random.random()) so repeated polls of a
    # healthy cabinet return the same reading, the way a real steady-state
    # ambient sensor does, and so the same topology always produces the same
    # numbers across test runs.
    seed = int(hashlib.sha256(cabinet_id.encode()).hexdigest(), 16)
    temperature_c = 20.0 + (seed % 400) / 100.0        # 20.0–23.99 °C
    humidity_pct = 35.0 + ((seed // 400) % 2000) / 100.0  # 35.0–54.99 %

    return ProviderTelemetryResponse(
        power_w=float(device.power_draw_w or 0),
        temperature_c=round(temperature_c, 2),
        humidity_pct=round(humidity_pct, 2),
    )

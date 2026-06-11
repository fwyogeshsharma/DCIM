"""
Redfish Device — request router + auth for one simulated server BMC.

One RedfishDevice wraps one Device object. The controller binds an HTTP server
to the device's IP and forwards every request here via ``dispatch()``.

Auth model (MVP, mirrors real BMCs):
  • ServiceRoot (/redfish/v1/) and POST .../Sessions are unauthenticated.
  • Everything else requires HTTP Basic (admin/password) OR a valid session
    token in the ``X-Auth-Token`` header.

Sessions are created with ``POST /redfish/v1/SessionService/Sessions`` carrying
``{"UserName": ..., "Password": ...}`` and torn down with ``DELETE`` on the
session URL.  Tokens live in an in-memory dict on this device.

All resource bodies come from core.redfish_data_generator, built on demand from
the live Device object, so telemetry reflects the latest ticker values.
"""
from __future__ import annotations

import base64
import uuid
from typing import Optional, Tuple, TYPE_CHECKING

from core import redfish_data_generator as rf

if TYPE_CHECKING:
    from core.device_manager import Device

# A dispatch result: (status_code, extra_headers, body_obj_or_None)
Result = Tuple[int, dict, Optional[dict]]


class RedfishDevice:
    """Routes Redfish requests for a single server and manages its sessions."""

    def __init__(self, device: "Device",
                 username: str = "admin", password: str = "password"):
        self.device = device
        self._user = username
        self._pass = password
        # token -> {"id": str, "user": str}
        self._sessions: dict[str, dict] = {}

    # ── identity ───────────────────────────────────────────────────────────
    @property
    def member_id(self) -> str:
        return rf.member_id(self.device)

    def session_list(self) -> list[dict]:
        """Snapshot of active sessions for the UI/REST status."""
        return [{"id": s["id"], "user": s["user"]} for s in self._sessions.values()]

    # ── auth helpers ───────────────────────────────────────────────────────
    def _auth_ok(self, headers) -> bool:
        token = headers.get("X-Auth-Token")
        if token and token in self._sessions:
            return True
        authz = headers.get("Authorization", "")
        if authz.startswith("Basic "):
            try:
                raw = base64.b64decode(authz[6:]).decode("utf-8", "replace")
                user, _, pw = raw.partition(":")
                return user == self._user and pw == self._pass
            except Exception:
                return False
        return False

    def _create_session(self, body: Optional[dict]) -> Result:
        body = body or {}
        user = body.get("UserName")
        pw = body.get("Password")
        if user != self._user or pw != self._pass:
            return self._error(401, "Base.1.0.InsufficientPrivilege",
                               "Invalid credentials.")
        token = uuid.uuid4().hex
        sid = uuid.uuid4().hex[:12]
        self._sessions[token] = {"id": sid, "user": user}
        loc = f"/redfish/v1/SessionService/Sessions/{sid}"
        return (201,
                {"X-Auth-Token": token, "Location": loc},
                rf.session_member(sid, user))

    def _delete_session(self, sid: str) -> Result:
        for token, s in list(self._sessions.items()):
            if s["id"] == sid:
                del self._sessions[token]
                return (204, {}, None)
        return self._error(404, "Base.1.0.ResourceMissingAtURI",
                           "Session not found.")

    # ── error helper ───────────────────────────────────────────────────────
    @staticmethod
    def _error(code: int, ecode: str, msg: str) -> Result:
        return (code, {}, {
            "error": {
                "code": ecode,
                "message": msg,
                "@Message.ExtendedInfo": [{"MessageId": ecode, "Message": msg}],
            }
        })

    # ── main dispatch ──────────────────────────────────────────────────────
    def dispatch(self, method: str, path: str, headers,
                 body: Optional[dict]) -> Result:
        # Normalise: drop query string, collapse trailing slash (keep root).
        path = path.split("?", 1)[0]
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        # /redfish version stub — unauthenticated.
        if path in ("/redfish", "/redfish/"):
            return (200, {}, {"v1": "/redfish/v1/"})

        # ServiceRoot — unauthenticated per spec.
        if path in ("/redfish/v1", "/redfish/v1/"):
            return (200, {}, rf.service_root())

        sessions_url = "/redfish/v1/SessionService/Sessions"

        # Session creation — unauthenticated (this is how you log in).
        if path == sessions_url and method == "POST":
            return self._create_session(body)

        # Everything below requires authentication.
        if not self._auth_ok(headers):
            return self._error(401, "Base.1.0.InsufficientPrivilege",
                               "Authentication required.")

        sid = self.member_id

        if method == "GET":
            routes = {
                "/redfish/v1/Systems":                          lambda: rf.systems_collection(self.device),
                f"/redfish/v1/Systems/{sid}":                   lambda: rf.computer_system(self.device),
                "/redfish/v1/Chassis":                          lambda: rf.chassis_collection(self.device),
                f"/redfish/v1/Chassis/{sid}":                   lambda: rf.chassis(self.device),
                f"/redfish/v1/Chassis/{sid}/Thermal":           lambda: rf.thermal(self.device),
                f"/redfish/v1/Chassis/{sid}/Power":             lambda: rf.power(self.device),
                "/redfish/v1/Managers":                         lambda: rf.managers_collection(),
                f"/redfish/v1/Managers/{rf.MANAGER_ID}":        lambda: rf.manager(self.device),
                "/redfish/v1/SessionService":                   lambda: rf.session_service(),
                sessions_url:                                   lambda: rf.sessions_collection(
                    [s["id"] for s in self._sessions.values()]),
            }
            builder = routes.get(path)
            if builder is not None:
                return (200, {}, builder())
            # Individual session member
            if path.startswith(sessions_url + "/"):
                want = path.rsplit("/", 1)[-1]
                for s in self._sessions.values():
                    if s["id"] == want:
                        return (200, {}, rf.session_member(s["id"], s["user"]))
            return self._error(404, "Base.1.0.ResourceMissingAtURI",
                               f"Resource {path} not found.")

        if method == "DELETE" and path.startswith(sessions_url + "/"):
            return self._delete_session(path.rsplit("/", 1)[-1])

        return self._error(405, "Base.1.0.ActionNotSupported",
                           f"Method {method} not allowed on {path}.")

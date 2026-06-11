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

        # ── mutable server state (driven by Server Operations / actions) ──
        self.power_state = "On"        # On | Off
        self.indicator_led = "Off"     # Off | Lit | Blinking
        self.sel: list[dict] = []      # stored System Event Log entries
        self._sel_seq = 0
        self.log_event("OK", "BMC initialized")
        self.log_event("OK", "System powered on")

    # ── identity ───────────────────────────────────────────────────────────
    @property
    def member_id(self) -> str:
        return rf.member_id(self.device)

    def session_list(self) -> list[dict]:
        """Snapshot of active sessions for the UI/REST status."""
        return [{"id": s["id"], "user": s["user"]} for s in self._sessions.values()]

    # ── event log ───────────────────────────────────────────────────────────
    @staticmethod
    def _now() -> str:
        import time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def log_event(self, severity: str, message: str) -> None:
        self._sel_seq += 1
        self.sel.append({
            "Id": self._sel_seq,
            "Name": f"Event {self._sel_seq}",
            "Severity": severity,
            "Message": message,
            "Created": self._now(),
        })

    # ── server operations (shared by HTTP actions and REST control) ─────────
    def reset(self, reset_type: str):
        rt = reset_type or ""
        if rt in ("On", "ForceOn"):
            self.power_state = "On"; self.log_event("OK", f"Power On ({rt})")
        elif rt in ("ForceOff", "GracefulShutdown"):
            self.power_state = "Off"; self.log_event("OK", f"Power Off ({rt})")
        elif rt in ("GracefulRestart", "ForceRestart", "PowerCycle"):
            self.power_state = "On"; self.log_event("OK", f"Reset ({rt})")
        elif rt == "PushPowerButton":
            self.power_state = "Off" if self.power_state == "On" else "On"
            self.log_event("OK", f"Power button — now {self.power_state}")
        else:
            return False, f"Unsupported ResetType '{rt}'"
        return True, f"power_state={self.power_state}"

    def set_led(self, state: str):
        state = "Lit" if str(state) in ("Lit", "Blinking", "on", "On", "true", "True") else "Off"
        self.indicator_led = state
        self.log_event("OK", f"Indicator LED {state}")
        return True, f"indicator_led={state}"

    def refresh_inventory(self):
        # Data is already live every request — this just records the request.
        self.log_event("OK", "Inventory refreshed")
        return True, "inventory refreshed"

    def clear_log(self):
        n = len(self.sel)
        self.sel.clear()
        self._sel_seq = 0
        self.log_event("OK", "Event log cleared")
        return True, f"cleared {n} entr{'y' if n == 1 else 'ies'}"

    def perform(self, action: str):
        """High-level action dispatch used by the REST control endpoint."""
        fn = {
            "power_on":    lambda: self.reset("On"),
            "power_off":   lambda: self.reset("ForceOff"),
            "reboot":      lambda: self.reset("GracefulRestart"),
            "power_cycle": lambda: self.reset("PowerCycle"),
            "led_on":      lambda: self.set_led("Lit"),
            "led_off":     lambda: self.set_led("Off"),
            "refresh":     self.refresh_inventory,
            "clear_log":   self.clear_log,
        }.get(action)
        if fn is None:
            return False, f"unknown action '{action}'"
        return fn()

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
                f"/redfish/v1/Systems/{sid}":                   lambda: rf.computer_system(
                    self.device, self.power_state, self.indicator_led),
                "/redfish/v1/Chassis":                          lambda: rf.chassis_collection(self.device),
                f"/redfish/v1/Chassis/{sid}":                   lambda: rf.chassis(self.device),
                f"/redfish/v1/Chassis/{sid}/Thermal":           lambda: rf.thermal(self.device),
                f"/redfish/v1/Chassis/{sid}/Power":             lambda: rf.power(self.device),
                f"/redfish/v1/Systems/{sid}/EthernetInterfaces": lambda: rf.ethernet_collection(self.device),
                f"/redfish/v1/Systems/{sid}/Storage":           lambda: rf.storage_collection(self.device),
                f"/redfish/v1/Systems/{sid}/Storage/{rf.STORAGE_ID}": lambda: rf.storage(self.device),
                f"/redfish/v1/Systems/{sid}/LogServices":       lambda: rf.logservices_collection(self.device),
                f"/redfish/v1/Systems/{sid}/LogServices/SEL":   lambda: rf.logservice_sel(self.device, self.sel),
                f"/redfish/v1/Systems/{sid}/LogServices/SEL/Entries": lambda: rf.logservice_entries(self.device, self.sel),
                "/redfish/v1/Managers":                         lambda: rf.managers_collection(),
                f"/redfish/v1/Managers/{rf.MANAGER_ID}":        lambda: rf.manager(self.device),
                "/redfish/v1/SessionService":                   lambda: rf.session_service(),
                sessions_url:                                   lambda: rf.sessions_collection(
                    [s["id"] for s in self._sessions.values()]),
            }
            builder = routes.get(path)
            if builder is not None:
                return (200, {}, builder())
            # Individual EthernetInterface member (NIC.<n>)
            eth_prefix = f"/redfish/v1/Systems/{sid}/EthernetInterfaces/"
            if path.startswith(eth_prefix):
                nic = rf.ethernet_interface(self.device, path[len(eth_prefix):])
                if nic is not None:
                    return (200, {}, nic)
            # Individual session member
            if path.startswith(sessions_url + "/"):
                want = path.rsplit("/", 1)[-1]
                for s in self._sessions.values():
                    if s["id"] == want:
                        return (200, {}, rf.session_member(s["id"], s["user"]))
            return self._error(404, "Base.1.0.ResourceMissingAtURI",
                               f"Resource {path} not found.")

        # ── Server-operation actions ───────────────────────────────────────
        if method == "POST":
            reset_path   = f"/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset"
            refresh_path = f"/redfish/v1/Systems/{sid}/Actions/Oem/Simulator.RefreshInventory"
            clear_path   = f"/redfish/v1/Systems/{sid}/LogServices/SEL/Actions/LogService.ClearLog"
            if path == reset_path:
                ok, msg = self.reset((body or {}).get("ResetType"))
                if not ok:
                    return self._error(400, "Base.1.0.ActionParameterValueNotInList", msg)
                return (204, {}, None)
            if path == refresh_path:
                self.refresh_inventory()
                return (204, {}, None)
            if path == clear_path:
                self.clear_log()
                return (204, {}, None)
            return self._error(404, "Base.1.0.ResourceMissingAtURI",
                               f"No action at {path}.")

        if method == "PATCH":
            if path == f"/redfish/v1/Systems/{sid}":
                b = body or {}
                if "IndicatorLED" in b:
                    self.set_led(b["IndicatorLED"])
                elif "LocationIndicatorActive" in b:
                    self.set_led("Lit" if b["LocationIndicatorActive"] else "Off")
                else:
                    return self._error(400, "Base.1.0.PropertyMissing",
                                       "Expected IndicatorLED or LocationIndicatorActive.")
                return (200, {}, rf.computer_system(
                    self.device, self.power_state, self.indicator_led))
            return self._error(404, "Base.1.0.ResourceMissingAtURI",
                               f"Resource {path} not patchable.")

        if method == "DELETE" and path.startswith(sessions_url + "/"):
            return self._delete_session(path.rsplit("/", 1)[-1])

        return self._error(405, "Base.1.0.ActionNotSupported",
                           f"Method {method} not allowed on {path}.")

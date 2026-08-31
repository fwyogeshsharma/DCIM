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
import json
import threading
import urllib.request
import uuid
from typing import Optional, Tuple, TYPE_CHECKING

from core import redfish_data_generator as rf

if TYPE_CHECKING:
    from core.device_manager import Device

# A dispatch result: (status_code, extra_headers, body_obj_or_None)
Result = Tuple[int, dict, Optional[dict]]


class PowerOff(Exception):
    """Raised when a request reaches a BMC that has no power.

    Its own type rather than a ConnectionError so the server layer can tell it
    apart from a transport fault and answer the way a dead box does - by not
    answering. Anything with a status code in it, 500 included, is a reply, and
    a reply is proof of power.
    """


class RedfishDevice:
    """Routes Redfish requests for a single server and manages its sessions."""

    # Cap concurrent push subscribers per BMC (DoS / runaway-fanout guard).
    MAX_SUBS = 20

    def __init__(self, device: "Device",
                 username: str = "admin", password: str = "password",
                 power_trap_cb=None, topology=None):
        self.device = device
        self._user = username
        self._pass = password
        # Live TopologyEngine, or None when a BMC is built outside a topology.
        # Held (not snapshotted) so a re-corded PSU reports its NEW feed on the
        # next poll — a real BMC reads its input voltage, it does not remember it.
        self._topology = topology
        # cb(device, is_on: bool, reset_type: str) — fired on every chassis
        # power transition (the BMC's platform-event trap).
        self._power_trap_cb = power_trap_cb
        # token -> {"id": str, "user": str}
        self._sessions: dict[str, dict] = {}

        # ── mutable server state (driven by Server Operations / actions) ──
        # Adopt the Device's chassis state so a Redfish restart doesn't
        # silently "power on" servers that were left Off.
        # Chassis power lives on the Device, not here.
        #
        # It used to be copied at construction, and the copy went stale the
        # moment anything else turned the box off: pulling both cords set the
        # Device to Off while this BMC went on reporting On, so the simulator's
        # own API said a de-energised server was running. Read through instead;
        # the setter writes back, so a Redfish power action still works.
        self._power_state_fallback = getattr(device, "power_state", "On") or "On"
        self.indicator_led = "Off"     # Off | Lit | Blinking
        self.sel: list[dict] = []      # stored System Event Log entries
        self._sel_seq = 0
        # ── push-model event subscriptions (EventDestination) ──
        # id -> {id, Destination, Context, EventTypes, RegistryPrefixes,
        #        HttpHeaders, Name}
        self._subs: dict[str, dict] = {}
        self._sub_seq = 0
        self._event_seq = 0            # monotonic id for delivered Events
        # Fault-alarm conditions currently asserted (edge-triggered push).
        # Key = condition label (message text before the ':').
        self._active_alarms: set[str] = set()
        self.log_event("OK", "BMC initialized")
        self.log_event("OK", "System powered on")

    # ── identity ───────────────────────────────────────────────────────────
    @property
    @property
    def power_state(self) -> str:
        return getattr(self.device, "power_state", None) or self._power_state_fallback

    @power_state.setter
    def power_state(self, value: str) -> None:
        self._power_state_fallback = value
        try:
            self.device.power_state = value
        except AttributeError:
            pass          # a BMC built around something that is not a Device

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
        # Fan out to push subscribers (no-op when none registered).
        if self._subs:
            etype = "Alert" if severity in ("Warning", "Critical") else "StatusChange"
            self._deliver(severity, message, etype,
                          f"Simulator.1.0.{etype}",
                          f"/redfish/v1/Systems/{self.member_id}")

    # ── push-model event delivery ───────────────────────────────────────────
    def _deliver(self, severity: str, message: str, event_type: str,
                 message_id: str, origin: Optional[str] = None) -> None:
        """Build an Event and POST it to every matching subscriber (async)."""
        if not self._subs:
            return
        self._event_seq += 1
        seq = self._event_seq
        for sub in list(self._subs.values()):
            types = sub.get("EventTypes")
            if types and event_type not in types:
                continue
            doc = rf.event_record(seq, severity, message, event_type,
                                  message_id, origin, sub.get("Context", ""))
            self._post_async(sub, doc)

    @staticmethod
    def _post_async(sub: dict, doc: dict) -> None:
        dest = sub.get("Destination")
        if not dest:
            return
        headers = {"Content-Type": "application/json"}
        for h in sub.get("HttpHeaders") or []:
            if isinstance(h, dict):
                headers.update({str(k): str(v) for k, v in h.items()})
        data = json.dumps(doc).encode("utf-8")

        def _send():
            try:
                req = urllib.request.Request(dest, data=data,
                                             headers=headers, method="POST")
                urllib.request.urlopen(req, timeout=3)  # noqa: S310 (sim)
            except Exception:
                pass  # best-effort delivery; real BMCs would retry per policy

        threading.Thread(target=_send, daemon=True, name="RedfishEvent").start()

    # Raised by register_subscription so HTTP and programmatic callers can each
    # map the failure to their own error shape.
    class SubscriptionError(Exception):
        def __init__(self, code: int, ecode: str, msg: str):
            super().__init__(msg)
            self.code, self.ecode, self.msg = code, ecode, msg

    def register_subscription(self, destination, context: str = "",
                              event_types=None, registry_prefixes=None,
                              http_headers=None, name=None) -> dict:
        """Validate + store one push subscription. Returns the stored sub dict.

        Raises SubscriptionError on bad input or when the per-BMC cap is hit.
        Shared by the Redfish HTTP path and the controller/REST control path.
        """
        if not destination or not isinstance(destination, str):
            raise self.SubscriptionError(400, "Base.1.0.PropertyMissing",
                                         "Destination is required.")
        if len(self._subs) >= self.MAX_SUBS:
            raise self.SubscriptionError(
                503, "Base.1.0.CreateLimitReachedForResource",
                f"Subscription limit ({self.MAX_SUBS}) reached.")
        self._sub_seq += 1
        sid = uuid.uuid4().hex[:12]
        sub = {
            "id": sid,
            "Destination": destination,
            "Context": context or "",
            "EventTypes": event_types or None,
            "RegistryPrefixes": registry_prefixes or None,
            "HttpHeaders": http_headers or None,
            "Name": name,
        }
        self._subs[sid] = sub
        self.log_event("OK", f"Event subscription created -> {destination}")
        return sub

    def unregister_subscription(self, sid: str) -> bool:
        """Remove a subscription by id. Returns True if it existed."""
        if sid in self._subs:
            self._subs.pop(sid)
            self.log_event("OK", "Event subscription deleted")
            return True
        return False

    def _create_subscription(self, body: Optional[dict]) -> Result:
        body = body or {}
        proto = body.get("Protocol", "Redfish")
        if proto != "Redfish":
            return self._error(400, "Base.1.0.PropertyValueNotInList",
                               f"Unsupported Protocol '{proto}'.")
        try:
            sub = self.register_subscription(
                body.get("Destination"),
                context=body.get("Context", ""),
                event_types=body.get("EventTypes"),
                registry_prefixes=body.get("RegistryPrefixes"),
                http_headers=body.get("HttpHeaders"),
                name=body.get("Name"))
        except self.SubscriptionError as e:
            return self._error(e.code, e.ecode, e.msg)
        loc = f"/redfish/v1/EventService/Subscriptions/{sub['id']}"
        return (201, {"Location": loc}, rf.subscription_member(sub))

    def _delete_subscription(self, sid: str) -> Result:
        if self.unregister_subscription(sid):
            return (204, {}, None)
        return self._error(404, "Base.1.0.ResourceMissingAtURI",
                           "Subscription not found.")

    # ── server operations (shared by HTTP actions and REST control) ─────────
    def reset(self, reset_type: str):
        rt = reset_type or ""
        prev = self.power_state
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
        # Mirror onto the Device so the ticker and REST API see chassis state.
        self.device.power_state = self.power_state
        # BMC platform-event trap on actual transitions (not reboots).
        if self.power_state != prev and self._power_trap_cb:
            try:
                self._power_trap_cb(self.device, self.power_state == "On", rt)
            except Exception:
                pass
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

    def reset_bmc(self, reset_type: str):
        """Reset the management controller itself (Manager.Reset).

        Models real hardware: the BMC's in-RAM state is volatile, so event
        subscriptions and live alarm tracking are LOST across a BMC reboot. The
        SEL is persistent (NVRAM) and survives. A collector must reconcile —
        re-list/re-create its subscriptions after a BMC reset. Host reboots
        (ComputerSystem.Reset) do NOT clear subscriptions.
        """
        rt = reset_type or "GracefulRestart"
        if rt not in ("GracefulRestart", "ForceRestart"):
            return False, f"Unsupported BMC ResetType '{rt}'"
        lost = len(self._subs)
        # Silent loss — clear BEFORE logging so the drop pushes to no one;
        # subscribers only discover it by reconciliation, like real BMCs.
        self._subs.clear()
        self._active_alarms.clear()    # re-detected on the next monitor sweep
        self.log_event("OK", f"BMC reset ({rt}) — {lost} subscription(s) dropped")
        return True, f"bmc reset; {lost} subscription(s) lost"

    # ── fault-alarm evaluation (drives Warning/Critical push events) ─────────
    def evaluate_alerts(self) -> None:
        """Compare live telemetry against thresholds; emit a push event on each
        alarm transition (assert → Warning/Critical, clear → OK). Edge-triggered
        off a stable per-condition key so a sustained fault fires exactly once.
        Called periodically by the controller's monitor thread.
        """
        # Powered-off chassis has no live sensors to alarm on; just clear.
        current: dict[str, tuple[str, str]] = {}
        if getattr(self.device, "power_state", "On") != "Off":
            for severity, message in rf._alarms(self.device):
                label = message.split(":", 1)[0].strip()
                current[label] = (severity, message)
        # Newly asserted conditions.
        for label, (severity, message) in current.items():
            if label not in self._active_alarms:
                self._active_alarms.add(label)
                self.log_event(severity, message)
        # Conditions that have cleared.
        for label in list(self._active_alarms):
            if label not in current:
                self._active_alarms.discard(label)
                self.log_event("OK", f"{label} cleared")

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
            "reboot_bmc":  lambda: self.reset_bmc("GracefulRestart"),
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
        # A BMC runs on standby power drawn from the chassis cords. Pull every
        # cord and it stops answering with everything else - it does not sit
        # there reporting PowerState: Off, because reporting anything requires
        # the power that just went away.
        #
        # This is what makes a de-energised server look de-energised to a DCIM.
        # Before it, cutting both feeds left the BMC serving Redfish happily
        # and the platform saw a healthy machine; a fault campaign cut both
        # cords on a dual-corded server and never raised a single alarm.
        from core.device_state_store import _is_unpowered
        if _is_unpowered(self.device.name):
            raise PowerOff(self.device.name)

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
                f"/redfish/v1/Chassis/{sid}/Power":             lambda: rf.power(self.device, self._topology),
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
                "/redfish/v1/EventService":                     lambda: rf.event_service(
                    list(self._subs.keys())),
                "/redfish/v1/EventService/Subscriptions":       lambda: rf.subscriptions_collection(
                    list(self._subs.keys())),
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
            # Individual event subscription member
            sub_prefix = "/redfish/v1/EventService/Subscriptions/"
            if path.startswith(sub_prefix):
                sub = self._subs.get(path[len(sub_prefix):])
                if sub is not None:
                    return (200, {}, rf.subscription_member(sub))
            return self._error(404, "Base.1.0.ResourceMissingAtURI",
                               f"Resource {path} not found.")

        # ── Server-operation actions ───────────────────────────────────────
        if method == "POST":
            reset_path   = f"/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset"
            refresh_path = f"/redfish/v1/Systems/{sid}/Actions/Oem/Simulator.RefreshInventory"
            clear_path   = f"/redfish/v1/Systems/{sid}/LogServices/SEL/Actions/LogService.ClearLog"
            bmc_reset_path = f"/redfish/v1/Managers/{rf.MANAGER_ID}/Actions/Manager.Reset"
            if path == bmc_reset_path:
                ok, msg = self.reset_bmc((body or {}).get("ResetType"))
                if not ok:
                    return self._error(400, "Base.1.0.ActionParameterValueNotInList", msg)
                return (204, {}, None)
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
            # Create a push subscription.
            if path == "/redfish/v1/EventService/Subscriptions":
                return self._create_subscription(body)
            # Fire a test event to all subscribers.
            if path == "/redfish/v1/EventService/Actions/EventService.SubmitTestEvent":
                b = body or {}
                self._deliver(
                    b.get("Severity", "OK"),
                    b.get("Message", "Test event"),
                    b.get("EventType", "Alert"),
                    b.get("MessageId", "Simulator.1.0.TestEvent"),
                    f"/redfish/v1/Systems/{sid}")
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

        if method == "DELETE":
            if path.startswith(sessions_url + "/"):
                return self._delete_session(path.rsplit("/", 1)[-1])
            sub_prefix = "/redfish/v1/EventService/Subscriptions/"
            if path.startswith(sub_prefix):
                return self._delete_subscription(path[len(sub_prefix):])

        return self._error(405, "Base.1.0.ActionNotSupported",
                           f"Method {method} not allowed on {path}.")

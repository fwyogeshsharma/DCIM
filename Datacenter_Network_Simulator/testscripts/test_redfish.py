"""
Redfish simulator test — exercises the BMC endpoint end to end.

Starts a RedfishController on the loopback with one synthetic server, then:
  1. GET ServiceRoot unauthenticated            → 200
  2. GET Systems collection unauthenticated      → 401
  3. GET Systems collection with HTTP Basic      → 200
  4. POST a Session (login)                       → 201 + X-Auth-Token
  5. GET Chassis Thermal / Power with the token   → live ReadingCelsius / Watts
  6. DELETE the session (logout)                  → 204

Run:  python testscripts/test_redfish.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.device_manager import Device, DeviceType, Vendor
from simulator.redfish_controller import RedfishController

HOST = "127.0.0.1"
PORT = 5443                      # high port — avoids 443 privilege/conflict in test
BASE = f"http://{HOST}:{PORT}"

_pass = 0
_fail = 0


def check(cond: bool, label: str):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {label}")
    else:
        _fail += 1
        print(f"  FAIL  {label}")


def req(method: str, path: str, headers: dict | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            raw = resp.read()
            obj = json.loads(raw) if raw else None
            return resp.status, dict(resp.headers), obj
    except urllib.error.HTTPError as e:
        raw = e.read()
        obj = json.loads(raw) if raw else None
        return e.code, dict(e.headers), obj


def main():
    dev = Device(
        name="WebSrv01",
        device_type=DeviceType.SERVER,
        vendor=Vendor.DELL,
        ip_address=HOST,
        model_name="Dell PowerEdge R750",
        power_draw_w=465,
    )
    dev.cpu_temp = 61.5
    dev.inlet_temp = 24.0

    ctrl = RedfishController()
    ctrl.set_log_callback(lambda m, l: print(f"  [ctrl/{l}] {m}"))
    ok = ctrl.start(devices=[dev], port=PORT, ip_for=lambda d: HOST)
    check(ok, "controller started")
    if not ok:
        sys.exit(1)

    basic = "Basic " + base64.b64encode(b"admin:password").decode()
    try:
        # 1. ServiceRoot, no auth
        st, _, body = req("GET", "/redfish/v1/")
        check(st == 200 and body.get("RedfishVersion"), "ServiceRoot 200 (no auth)")

        sid = dev.id

        # 2. Systems requires auth
        st, _, _ = req("GET", "/redfish/v1/Systems")
        check(st == 401, "Systems 401 without auth")

        # 3. Systems with Basic
        st, _, body = req("GET", "/redfish/v1/Systems", {"Authorization": basic})
        check(st == 200 and body["Members@odata.count"] == 1, "Systems 200 with Basic")

        # 3b. ComputerSystem live cpu util
        st, _, body = req("GET", f"/redfish/v1/Systems/{sid}", {"Authorization": basic})
        check(st == 200 and body["Manufacturer"] == "Dell Technologies",
              "ComputerSystem manufacturer")
        check(body["ProcessorSummary"]["Count"] == 2, "ProcessorSummary count")

        # 4. Session login
        st, hdrs, body = req("POST", "/redfish/v1/SessionService/Sessions",
                             None, {"UserName": "admin", "Password": "password"})
        token = hdrs.get("X-Auth-Token")
        check(st == 201 and token, "Session created (201 + X-Auth-Token)")
        tok_hdr = {"X-Auth-Token": token}

        # 4b. bad creds rejected
        st, _, _ = req("POST", "/redfish/v1/SessionService/Sessions",
                       None, {"UserName": "admin", "Password": "wrong"})
        check(st == 401, "Bad credentials rejected")

        # 5. Thermal via token
        st, _, body = req("GET", f"/redfish/v1/Chassis/{sid}/Thermal", tok_hdr)
        cpu = next(t["ReadingCelsius"] for t in body["Temperatures"]
                   if t["Name"] == "CPU Temp")
        check(st == 200 and cpu == 61.5, f"Thermal live CPU temp = {cpu}")

        # 5b. Power via token
        st, _, body = req("GET", f"/redfish/v1/Chassis/{sid}/Power", tok_hdr)
        watts = body["PowerControl"][0]["PowerConsumedWatts"]
        check(st == 200 and watts == 465, f"Power live watts = {watts}")

        # 5c. Manager (BMC) branding
        st, _, body = req("GET", "/redfish/v1/Managers/BMC", tok_hdr)
        check(st == 200 and body["Model"] == "iDRAC9", "Manager BMC = iDRAC9")

        # 6. logout
        sessions = ctrl.get_sessions()
        check(len(sessions) == 1, "one active session before logout")
        st, _, _ = req("DELETE",
                       f"/redfish/v1/SessionService/Sessions/{sessions[0]['id']}",
                       tok_hdr)
        check(st == 204, "Session deleted (204)")
    finally:
        ctrl.stop()

    print(f"\n  {_pass} passed, {_fail} failed")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()

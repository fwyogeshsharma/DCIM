"""Authenticated HTTP against a RUNNING simulator, for the live plant probes.

The probes exist because one half of the cooling model cannot be reached from the
test fixtures at all. `get_power_summary` takes `max(metered, model)`, and the
metered branch runs through the EV2 meter hierarchy — which the fixture plants do
not carry — so a defect that lives there passes every unit test. F9 did exactly
that: a total loss of chilled water read cooling 132.9 -> 140.3 kW (rising, with
PUE falling) while the whole gate stayed green.

127.0.0.1 rather than localhost: the API binds dual-stack, and resolving localhost
tries IPv6 first, which costs about 20 s per call before falling back.
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8001/api"
USER, PASSWORD = "admin", "admin1234"
DC = "DC1"


def _request(path: str, body=None, token: str | None = None, timeout: float = 300.0):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def connect():
    """Log in and return (get, post). Raises if the simulator is not up."""
    token = _request("/auth/login", {"username": USER, "password": PASSWORD})["token"]

    def get(path):
        return _request(path, None, token)

    def post(path, body=None):
        return _request(path, body if body is not None else {}, token)

    return get, post


def device_ids(get) -> dict:
    """name -> topology device id, for the whole fleet."""
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs
    return {d["name"]: d["id"] for d in devs}


def plant_points(get) -> dict:
    """name -> {point: value} for every plant machine on the BACnet plane."""
    return {r["name"]: r["values"] for r in get("/bacnet/plant/metrics")}


def force(post, ids, name, point, value):
    """Force (or clear, with value=None) one BACnet point on one plant machine.

    This is the same channel the Simulate Fault menu drives. ALWAYS clear what you
    force — a probe that dies with a point still forced leaves the live plant
    faulted, which is why every probe here does its release in a `finally`.
    """
    return post("/bacnet/plant/overrides",
                {"device": ids[name], "point": point, "value": value})

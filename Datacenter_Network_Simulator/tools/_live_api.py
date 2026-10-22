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


def _request(path: str, body=None, token: str | None = None, timeout: float = 300.0,
             method: str | None = None, base: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request((base or BASE) + path, data=data, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def connect():
    """Log in and return (get, post). Raises if the simulator is not up."""
    token = _request("/auth/login", {"username": USER, "password": PASSWORD})["token"]

    def get(path):
        return _request(path, None, token)

    def post(path, body=None):
        return _request(path, body if body is not None else {}, token)

    return get, post


class Client:
    """get / post / delete / patch against one authenticated base URL. The
    anomaly campaign runner (tools/live_campaign_anomaly.py) needs DELETE for
    the fault endpoints, which connect() does not offer."""

    def __init__(self, base: str = BASE, token: str | None = None):
        self.base, self.token = base, token

    @classmethod
    def login(cls, base: str = BASE, user: str = USER, password: str = PASSWORD,
              path: str = "/auth/login") -> "Client":
        out = _request(path, {"username": user, "password": password}, base=base)
        token = (out or {}).get("token") or (out or {}).get("access_token")
        if not token:
            raise RuntimeError(f"login to {base}{path} returned no token")
        return cls(base, token)

    def get(self, path):
        return _request(path, None, self.token, base=self.base)

    def post(self, path, body=None):
        return _request(path, body if body is not None else {}, self.token, base=self.base,
                        method="POST")

    def patch(self, path, body):
        return _request(path, body, self.token, base=self.base, method="PATCH")

    def delete(self, path):
        return _request(path, None, self.token, base=self.base, method="DELETE")


# ── anomaly ground-truth helpers (doc 32 §4.7, §5.4, §6) ─────────────────────

def add_sensor_fault(c: Client, device_id: str, point: str, **params) -> dict:
    return c.post("/faults/sensor", dict(params, device=device_id, point=point))


def add_degradation(c: Client, device_id: str, kind: str, rate_per_h: float,
                    target: float, campaign: str | None = None) -> dict:
    return c.post("/faults/degradation", {"device": device_id, "kind": kind,
                                          "rate_per_h": rate_per_h, "target": target,
                                          "campaign": campaign})


def delete_fault(c: Client, fault_id: str, restore: bool = False) -> dict:
    return c.delete(f"/faults/{fault_id}" + ("?restore=true" if restore else ""))


def active_faults(c: Client) -> list:
    return (c.get("/faults") or {}).get("active", [])


def plant_view(c: Client, view: str = "wire") -> dict:
    """name -> {point: value}, the wire (what BACnet serves) or the truth (what
    physics reads). They differ only on points carrying a sensor fault."""
    return {r["name"]: r["values"] for r in c.get(f"/bacnet/plant/metrics?view={view}")}


def ledger(c: Client, **q) -> list:
    qs = "&".join(f"{k}={v}" for k, v in q.items() if v is not None)
    return (c.get("/faults/ledger" + (f"?{qs}" if qs else "")) or {}).get("entries", [])


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

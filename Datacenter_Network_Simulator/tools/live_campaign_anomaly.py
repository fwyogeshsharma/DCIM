#!/usr/bin/env python3
"""The anomaly-detection fault-campaign runner (doc 32 §13, S3-1 / S3-2).

    python tools/live_campaign_anomaly.py --list
    python tools/live_campaign_anomaly.py crah-filter-loading            # DRY RUN: prints the plan
    python tools/live_campaign_anomaly.py crah-filter-loading --send     # touches the live simulator
    python tools/live_campaign_anomaly.py --calibrate measured.json       # S3-2: show window write-back
    python tools/live_campaign_anomaly.py --calibrate measured.json --write

SAFETY, IN ORDER OF HOW MUCH IT HAS COST BEFORE:

* DRY RUN IS THE DEFAULT. Without ``--send`` this makes NO network call at all —
  not a login, not a GET. A "staging" trap script once fired four CRITICAL UPS
  events into live (feedback_trap_scripts_need_send_flag); the plan printed
  here names the selectors, not resolved devices, for exactly that reason.
* Fire scenarios need ``--allow-fire`` as well as ``--send``, and the user's
  explicit approval for that run (project_fire_system). The flag is the gate
  the tool can enforce; the approval is the operator's.
* With ``--send`` it refuses to start unless ``GET /api/air/influence`` answers
  (the simulator is in spatial air mode), unless the scenario says otherwise.
* Every injection is tagged ``campaign: "<scenario>/<run_id>"`` and cleared in a
  ``finally``: overrides released, sensor faults deleted, degradations deleted
  WITH ``?restore=true`` (a campaign leaves the plant clean; the restore is
  ledgered as maintenance), weather cleared, fire reset, detectors restored.
* Never run two campaigns at once, or one across a deploy, on the 7.7 GB host
  (feedback_batch_deploys_tight_host).

Devices are resolved by TYPE and position on the live fleet, never by a
hard-coded name (the doc's names are 2026-10-21 resolutions, for reading).

ENABLE-FOR-RUN. Scenarios list detectors that are seeded disabled (CT-2, CT-3,
AT-1, PL-1). With ``--platform URL`` and a token (``--platform-token`` or
``DCIM_PLATFORM_TOKEN``; or ``--platform-user``/``--platform-password``) the
runner PATCHes ``/api/v1/anomalies/detectors/{id}`` ``{"enabled": true}``,
ledgers the window as context, and restores the previous value afterwards.
[Uncertain: the platform login path and token field are assumed to be
``POST /api/v1/login`` -> ``token`` (the platform; the simulator itself is /api/auth/login); pass a token to skip
it.] Without a platform it refuses such a scenario unless ``--skip-enable``.

LANDED CHECK. Every injection is read back: an override against the wire, a
sensor fault as wire − truth (``/bacnet/plant/metrics?view=``), a degradation
in ``GET /api/faults``. A scenario whose injection did not land is reported
SUSPECT, never as a result.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
LIBRARY = HERE / "anomaly_campaigns.json"
POLL_S = 15

#: API strings in the library -> how the runner applies, verifies and clears them.
API_OVERRIDE = "POST /api/bacnet/plant/overrides"
API_SENSOR = "POST /api/faults/sensor"
API_DEGRADE = "POST /api/faults/degradation"
API_WEATHER = "POST /api/weather/override"
API_DEVFAULT = "POST /api/devices/{id}/fault"
API_UTILITY = "POST /api/bacnet/electrical/utility"
API_FIRE = "POST /api/modbus/fire/smoke"
KNOWN_APIS = {API_OVERRIDE, API_SENSOR, API_DEGRADE, API_WEATHER, API_DEVFAULT,
              API_UTILITY, API_FIRE}
TIERS = {"abrupt", "gradual", "sensor", "context"}
PENDING = {None, "point_availability", "loop_entity", "s4"}
PICKS = {"all", "first", "first_running", "lead", "non_lead"}
_RUN_PT = ("Chiller_Running", "Run_Status", "Fan_Status", "Unit_Running", "Status_Modulating")


# ── the library ──────────────────────────────────────────────────────────────

def load_library(path: Path = LIBRARY) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scenario(lib: Dict[str, Any], name: str) -> Dict[str, Any]:
    for s in lib["scenarios"]:
        if s["name"] == name:
            return s
    raise KeyError(f"no scenario '{name}' in the library")


def with_variant(scn: Dict[str, Any], variant: Optional[str]) -> Dict[str, Any]:
    """A copy of *scn* with a named variant (e.g. ``realistic``) merged in.
    Injection overrides merge by position: body keys update, hold_s replaces."""
    out = copy.deepcopy(scn)
    if not variant or variant == "fast":
        return out
    v = (scn.get("variants") or {}).get(variant)
    if v is None:
        raise KeyError(f"scenario '{scn['name']}' has no '{variant}' variant")
    for i, patch in enumerate(v.get("injections") or []):
        inj = out["injections"][i]
        inj.setdefault("body", {}).update(patch.get("body") or {})
        if "hold_s" in patch:
            inj["hold_s"] = patch["hold_s"]
    for patch in v.get("expected") or []:
        for e in out["expected"]:
            if e["detector"] == patch["detector"]:
                e.update({k: val for k, val in patch.items() if k != "detector"})
    return out


def validate(lib: Dict[str, Any]) -> List[str]:
    """Structural check of the library (doc 32 §13.1a). Returns the problems."""
    bad: List[str] = []
    if lib.get("version") != 1:
        bad.append("version must be 1")
    names = set()
    for s in lib.get("scenarios", []):
        n = s.get("name", "?")
        if n in names:
            bad.append(f"{n}: duplicate name")
        names.add(n)
        for k in ("tier", "requires", "pending", "runs", "offline_twin", "injections",
                  "expected", "l1", "expected_misses", "must_not", "enable_for_run",
                  "incident"):
            if k not in s:
                bad.append(f"{n}: missing '{k}'")
        if s.get("tier") not in TIERS:
            bad.append(f"{n}: tier {s.get('tier')!r}")
        if s.get("pending") not in PENDING:
            bad.append(f"{n}: pending {s.get('pending')!r}")
        if s.get("tier") == "abrupt" and s.get("runs") != 3:
            bad.append(f"{n}: abrupt scenarios run 3x (3-of-3 recall gate)")
        req = s.get("requires") or {}
        for k in ("air_mode", "sim_tasks", "allow_fire", "fleet_paused"):
            if k not in req:
                bad.append(f"{n}: requires.{k} missing")
        for inj in s.get("injections") or []:
            if inj.get("api") not in KNOWN_APIS:
                bad.append(f"{n}: unknown api {inj.get('api')!r}")
            t = inj.get("target") or {}
            if "device_type" in t and t.get("pick") not in PICKS:
                bad.append(f"{n}: pick {t.get('pick')!r}")
            if inj.get("api") == API_FIRE and not req.get("allow_fire"):
                bad.append(f"{n}: a fire injection must require allow_fire")
        for e in s.get("expected") or []:
            w = e.get("window_s")
            if w is not None and not (isinstance(w, dict) and w.get("min", 0) <= w.get("max", 0)
                                      and "calibrated" in w):
                bad.append(f"{n}: {e.get('detector')} window_s malformed")
    return bad


# ── S3-2: calibrated windows written back ───────────────────────────────────

def apply_calibration(lib: Dict[str, Any], measured: Dict[str, Any]):
    """Write measured detection delays back into the library (S3-2).

    *measured*: ``{"scenarios": {name: {detector: {"delays_s": [..]}}}}``, as the
    platform scorer (doc 33 §4) reports them per run. Each window becomes the
    measured range with its median, ``calibrated: true`` and the run count.
    Returns ``(new_library, changes)``; nothing is written here."""
    new = copy.deepcopy(lib)
    changes = []
    by_name = {s["name"]: s for s in new["scenarios"]}
    for name, dets in (measured.get("scenarios") or {}).items():
        s = by_name.get(name)
        if s is None:
            changes.append((name, None, "unknown scenario - skipped"))
            continue
        for det, m in (dets or {}).items():
            delays = [float(x) for x in (m or {}).get("delays_s") or []]
            e = next((x for x in s["expected"] if x["detector"] == det), None)
            if e is None or not delays:
                changes.append((name, det, "no expectation or no delays - skipped"))
                continue
            old = e.get("window_s")
            e["window_s"] = {"min": int(min(delays)), "max": int(max(delays)),
                             "median": int(statistics.median(delays)),
                             "calibrated": True, "runs": len(delays),
                             "design": ({"min": old["min"], "max": old["max"]}
                                        if old and not old.get("calibrated") else
                                        (old or {}).get("design"))}
            changes.append((name, det, f"{old} -> {e['window_s']}"))
    return new, changes


# ── target resolution (by type and position, never by name) ─────────────────

def _running(points: Dict[str, Any]) -> Optional[bool]:
    for p in _RUN_PT:
        if p in points:
            return float(points[p]) >= 0.5
    return None


def resolve_targets(target: Dict[str, Any], devices: List[dict],
                    plant: Dict[str, dict]) -> List[dict]:
    """Devices matching *target* on the live fleet. ``pick``: all | first |
    first_running | lead (the running chiller) | non_lead (a staged-off one)."""
    dt = target.get("device_type")
    if dt is None:
        return []
    c = [d for d in devices if d.get("device_type") == dt
         and d.get("datacenter") == target.get("datacenter", d.get("datacenter"))
         and str(d.get("name", "")).startswith(target.get("name_prefix", ""))
         and target.get("name_contains", "") in str(d.get("name", ""))]
    c.sort(key=lambda d: d["name"])
    pick = target.get("pick", "first")
    if pick == "all":
        return c
    if pick == "first":
        return c[:1]
    run = [d for d in c if _running(plant.get(d["name"], {}))]
    if pick in ("first_running", "lead"):
        return run[:1]
    if pick == "non_lead":
        return [d for d in c if _running(plant.get(d["name"], {})) is False][:1]
    raise ValueError(f"unknown pick {pick!r}")


def describe(target: Dict[str, Any]) -> str:
    if "device_type" not in target:
        return json.dumps(target)
    bits = [target["device_type"], target.get("datacenter", "")]
    if target.get("name_prefix"):
        bits.append(f"{target['name_prefix']}*")
    if target.get("name_contains"):
        bits.append(f"*{target['name_contains']}*")
    return f"{target.get('pick', 'first')} of " + " ".join(b for b in bits if b)


def plan_lines(scn: Dict[str, Any], hold_scale: float = 1.0) -> List[str]:
    out = [f"### {scn['name']} [{scn['tier']}] runs={scn['runs']} pending={scn['pending']}"]
    if scn.get("manual"):
        out.append(f"   MANUAL: {scn['manual']}")
    for inj in scn["injections"]:
        hold = inj.get("hold_s")
        out.append(f"   {inj['api']}  -> {describe(inj['target'])}  body={json.dumps(inj['body'])}"
                   + (f"  hold={int(hold * hold_scale)}s" if hold else "")
                   + (f"  schedule={json.dumps(inj['schedule'])}" if inj.get("schedule") else ""))
    for e in scn["expected"]:
        out.append(f"   expect {e['detector']} ({e['kind']}, root={e['root']}) "
                   f"window={e.get('window_s')}")
    if scn["must_not"]:
        out.append(f"   must_not: {', '.join(scn['must_not'])}")
    if scn["enable_for_run"]:
        out.append(f"   enable_for_run: {', '.join(scn['enable_for_run'])}")
    return out


# ── the live run ─────────────────────────────────────────────────────────────

def _utc(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts,
                                  tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _exhaustion_helpers():
    """observe / is_clean / wait_clean / clear_trips from live_campaign_exhaustion,
    which parses sys.argv at import — shield it."""
    saved = sys.argv
    sys.argv = [saved[0]]
    try:
        from tools import live_campaign_exhaustion as lce
    finally:
        sys.argv = saved
    return lce


class Refused(Exception):
    """A gate refused the scenario before anything was injected."""


class Runner:
    def __init__(self, sim, platform=None, *, allow_fire=False, skip_enable=False,
                 hold_scale=1.0, sleep=time.sleep, clock=time.time, log=print,
                 check_clean=True):
        self.sim, self.platform = sim, platform
        self.allow_fire, self.skip_enable = allow_fire, skip_enable
        self.hold_scale, self.sleep, self.clock, self.log = hold_scale, sleep, clock, log
        self.check_clean = check_clean

    # -- gates --------------------------------------------------------------
    def gate(self, scn: Dict[str, Any]) -> None:
        req = scn["requires"]
        if req.get("allow_fire") and not self.allow_fire:
            raise Refused(f"{scn['name']} is a FIRE scenario: needs --allow-fire and the "
                          f"user's explicit approval for this run")
        if not scn["injections"] and scn.get("pending") == "s4":
            raise Refused(f"{scn['name']} is pending S4: nothing to inject")
        if scn.get("manual"):
            raise Refused(f"{scn['name']} needs a manual step: {scn['manual']}")
        if scn["enable_for_run"] and self.platform is None and not self.skip_enable:
            raise Refused(f"{scn['name']} enables {scn['enable_for_run']} for the run: "
                          f"pass --platform (and a token), or --skip-enable")
        if req.get("air_mode") == "spatial":
            try:
                self.sim.get("/air/influence")
            except Exception as exc:
                raise Refused(f"GET /api/air/influence did not answer ({exc}): the "
                              f"simulator is not in spatial air mode") from exc

    # -- detectors -----------------------------------------------------------
    def enable_detectors(self, scn, campaign) -> List[tuple]:
        restore = []
        if self.platform is None:
            return restore
        for det in scn["enable_for_run"]:
            path = f"/api/v1/anomalies/detectors/{det}"
            prev = (self.platform.get(path) or {}).get("enabled")
            self.platform.patch(path, {"enabled": True})
            ctx = self.sim.post("/faults/context", {
                "context": "detector_enabled_for_run", "campaign": campaign,
                "params": {"detector": det, "previous": prev}})
            restore.append((det, prev, (ctx or {}).get("id")))
            self.log(f"   enabled {det} for the run (was {prev})")
        return restore

    def restore_detectors(self, restore: List[tuple]) -> None:
        for det, prev, ctx_id in reversed(restore):
            try:
                self.platform.patch(f"/api/v1/anomalies/detectors/{det}",
                                    {"enabled": bool(prev)})
            except Exception as exc:
                self.log(f"   WARNING: could not restore {det} to enabled={prev}: {exc}")
            if ctx_id:
                try:
                    self.sim.post("/faults/context", {"context": "detector_enabled_for_run",
                                                      "close_id": ctx_id})
                except Exception:
                    pass

    # -- one injection -------------------------------------------------------
    def _devices(self):
        devs = self.sim.get("/devices")
        return devs["devices"] if isinstance(devs, dict) else devs

    def _plant(self, view="wire"):
        return {r["name"]: r["values"] for r in self.sim.get(f"/bacnet/plant/metrics?view={view}")}

    def apply(self, inj, campaign, devices, plant) -> List[dict]:
        """POST one injection; returns cleanup records."""
        api, body = inj["api"], dict(inj["body"])
        done = []
        if api in (API_WEATHER, API_UTILITY, API_FIRE):
            if api == API_UTILITY and inj.get("schedule"):
                return [{"api": api, "body": body, "schedule": inj["schedule"], "devices": []}]
            body["campaign"] = campaign
            path = {API_WEATHER: "/weather/override", API_UTILITY: "/bacnet/electrical/utility",
                    API_FIRE: "/modbus/fire/smoke"}[api]
            out = self.sim.post(path, body)
            return [{"api": api, "body": body, "out": out, "devices": []}]
        targets = resolve_targets(inj["target"], devices, plant)
        if not targets:
            raise Refused(f"no device matches {describe(inj['target'])}")
        for d in targets:
            b = dict(body)
            if api == API_OVERRIDE:
                if inj.get("schedule"):
                    done.append({"api": api, "device": d, "body": b, "schedule": inj["schedule"]})
                    continue
                b.update(device=d["id"], campaign=campaign)
                out = self.sim.post("/bacnet/plant/overrides", b)
            elif api == API_SENSOR:
                if "bias_frac" in b:
                    truth = (plant.get(d["name"]) or {}).get(b["point"])
                    if truth is None:
                        raise Refused(f"{d['name']}/{b['point']}: no truth to scale bias_frac")
                    b["bias"] = round(float(truth) * float(b.pop("bias_frac")), 3)
                b.update(device=d["id"], campaign=campaign)
                out = self.sim.post("/faults/sensor", b)
            elif api == API_DEGRADE:
                b.update(device=d["id"], campaign=campaign)
                out = self.sim.post("/faults/degradation", b)
            elif api == API_DEVFAULT:
                out = self.sim.post(f"/devices/{d['id']}/fault",
                                    {"fault": b["fault"], "action": "start"})
            else:
                raise Refused(f"unsupported api {api}")
            done.append({"api": api, "device": d, "body": b, "out": out})
        return done

    def landed(self, rec) -> Dict[str, Any]:
        api, b, d = rec["api"], rec["body"], rec.get("device")
        nm = (d or {}).get("name")
        try:
            if api == API_OVERRIDE and "value" in b and not b.get("ramp") and not b.get("oscillate"):
                got = self._plant().get(nm, {}).get(b["point"])
                ok = got is not None and abs(float(got) - float(b["value"])) <= 0.05
                return {"device": nm, "point": b["point"], "want": b["value"], "got": got, "ok": ok}
            if api in (API_OVERRIDE, API_DEGRADE, API_SENSOR):
                act = (self.sim.get("/faults") or {}).get("active", [])
                hit = [a for a in act if a.get("device") == nm
                       and (api == API_DEGRADE or a.get("point") == b.get("point"))]
                ok = bool(hit)
                if ok and api == API_SENSOR and b.get("bias") and not b.get("stuck"):
                    w = self._plant("wire").get(nm, {}).get(b["point"])
                    t = self._plant("truth").get(nm, {}).get(b["point"])
                    if w is not None and t is not None:
                        ok = abs((float(w) - float(t)) - float(b["bias"])) <= max(0.2, abs(b["bias"]) * 0.1)
                        return {"device": nm, "point": b["point"], "wire": w, "truth": t, "ok": ok}
                return {"device": nm, "point": b.get("point"), "ok": ok}
            if api == API_WEATHER:
                w = (self.sim.get("/weather") or {}).get("weather", [])
                ok = any(x.get("override") for x in w)
                return {"weather": True, "ok": ok}
            if api == API_DEVFAULT:
                got = (self.sim.get(f"/devices/{d['id']}/faults") or {}).get("active", [])
                return {"device": nm, "fault": b["fault"], "ok": b["fault"] in got}
        except Exception as exc:
            return {"device": nm, "ok": False, "error": str(exc)}
        return {"ok": True, "unchecked": api}

    def clear(self, rec) -> None:
        api, b, d = rec["api"], rec["body"], rec.get("device")
        out = rec.get("out") or {}
        try:
            if api == API_OVERRIDE:
                self.sim.post("/bacnet/plant/overrides",
                              {"device": d["id"], "point": b["point"], "value": None})
            elif api == API_SENSOR and out.get("id"):
                self.sim.delete(f"/faults/{out['id']}")
            elif api == API_DEGRADE and out.get("id"):
                self.sim.delete(f"/faults/{out['id']}?restore=true")
            elif api == API_WEATHER:
                dc = b.get("datacenter")
                self.sim.delete("/weather/override" + (f"?datacenter={dc}" if dc else ""))
            elif api == API_DEVFAULT:
                self.sim.post(f"/devices/{d['id']}/fault", {"fault": b["fault"], "action": "clear"})
            elif api == API_UTILITY:
                self.sim.post("/bacnet/electrical/utility",
                              {"datacenter": b["datacenter"], "failed": False})
            elif api == API_FIRE:
                self.sim.post("/modbus/fire/smoke", {"datacenter": b["datacenter"],
                                                     "room": b["room"], "pct_per_m": None})
                self.sim.post("/modbus/fire/reset", {"datacenter": b["datacenter"]})
        except Exception as exc:
            self.log(f"   WARNING: cleanup of {api} on {(d or {}).get('name')} failed: {exc} "
                     f"- CLEAR IT BY HAND")

    def _step_schedules(self, recs, t, campaign):
        for rec in recs:
            sch = rec.get("schedule")
            if not sch or t > float(sch.get("for_s", 0)):
                continue
            k = int(t // float(sch["every_s"]))
            if rec.get("_k") == k:
                continue
            rec["_k"] = k
            step = sch["sequence"][k % len(sch["sequence"])]
            if rec["api"] == API_OVERRIDE:
                self.sim.post("/bacnet/plant/overrides",
                              dict(rec["body"], device=rec["device"]["id"], campaign=campaign,
                                   **step))
            elif rec["api"] == API_UTILITY:
                self.sim.post("/bacnet/electrical/utility",
                              dict(rec["body"], campaign=campaign, **step))

    # -- one scenario run ----------------------------------------------------
    def run(self, scn: Dict[str, Any], run_id: Optional[str] = None) -> Dict[str, Any]:
        self.gate(scn)
        run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        campaign = f"{scn['name']}/{run_id}"
        lce = _exhaustion_helpers() if self.check_clean else None
        pre = clean = None
        if lce is not None:
            pre, clean = lce.wait_clean(self.sim.get, 420, "baseline")
        res = {"scenario": scn["name"], "run_id": run_id, "campaign": campaign,
               "clean_baseline": clean, "start": _utc(), "injections": [], "landed": []}
        restore: List[tuple] = []
        recs: List[dict] = []
        try:
            restore = self.enable_detectors(scn, campaign)
            devices, plant = self._devices(), self._plant("truth")
            for inj in scn["injections"]:
                recs.extend(self.apply(inj, campaign, devices, plant))
            self._step_schedules(recs, 0.0, campaign)
            self.sleep(POLL_S)
            res["landed"] = [self.landed(r) for r in recs if not r.get("schedule")]
            bad = [x for x in res["landed"] if not x.get("ok")]
            self.log(f"   {len(recs)} injection(s); "
                     + (f"{len(bad)} did NOT land: {bad}" if bad else "all landed"))
            hold = max([float(i.get("hold_s") or 0) for i in scn["injections"]]
                       + [float(scn.get("hold_s") or 0)]) * self.hold_scale
            t0 = self.clock()
            while self.clock() - t0 < hold:
                self._step_schedules(recs, self.clock() - t0, campaign)
                self.sleep(min(POLL_S, max(0.0, hold - (self.clock() - t0))))
        finally:
            for rec in reversed(recs):
                self.clear(rec)
            if restore:
                self.restore_detectors(restore)
            res["end"] = _utc()
            res["injections"] = [{"api": r["api"], "device": (r.get("device") or {}).get("name"),
                                  "body": r["body"], "id": (r.get("out") or {}).get("id")}
                                 for r in recs]
        if lce is not None:
            res["trips_cleared"] = lce.clear_trips(self.sim.get, self.sim.post,
                                                   lce.device_ids(self.sim.get), 420)
            _after, res["recovered"] = lce.wait_clean(self.sim.get, 420, "recovery")
        res["all_landed"] = all(x.get("ok") for x in res["landed"])
        return res


# ── CLI ──────────────────────────────────────────────────────────────────────

def _platform_client(args):
    if not args.platform:
        return None
    from tools._live_api import Client
    token = args.platform_token or os.environ.get("DCIM_PLATFORM_TOKEN")
    if token:
        return Client(args.platform.rstrip("/"), token)
    if args.platform_user and args.platform_password:
        return Client.login(args.platform.rstrip("/"), args.platform_user,
                            args.platform_password, path="/api/v1/login")
    raise SystemExit("--platform needs --platform-token (or DCIM_PLATFORM_TOKEN) or "
                     "--platform-user/--platform-password")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scenarios", nargs="*", help="scenario names (default: none)")
    ap.add_argument("--list", action="store_true", help="list the library")
    ap.add_argument("--library", default=str(LIBRARY))
    ap.add_argument("--send", action="store_true",
                    help="ACTUALLY inject into the running simulator (default: dry run)")
    ap.add_argument("--allow-fire", action="store_true",
                    help="permit fire scenarios (also needs the user's explicit approval)")
    ap.add_argument("--variant", default="fast", help="fast (default) | realistic")
    ap.add_argument("--runs", type=int, default=None, help="override the scenario's run count")
    ap.add_argument("--hold-scale", type=float, default=1.0)
    ap.add_argument("--sim", default="http://127.0.0.1:8001/api")
    ap.add_argument("--platform", default=None, help="platform base URL for enable-for-run")
    ap.add_argument("--platform-token", default=None)
    ap.add_argument("--platform-user", default=None)
    ap.add_argument("--platform-password", default=None)
    ap.add_argument("--skip-enable", action="store_true",
                    help="run a scenario without enabling its seeded-disabled detectors")
    ap.add_argument("--out", default=None, help="results JSON (default: tmp)")
    ap.add_argument("--calibrate", default=None,
                    help="S3-2: measured-windows JSON to write back into the library")
    ap.add_argument("--write", action="store_true", help="with --calibrate: save the library")
    args = ap.parse_args(argv)

    lib = load_library(Path(args.library))
    problems = validate(lib)
    if problems:
        print("library problems:\n  " + "\n  ".join(problems))
        return 2

    if args.calibrate:
        measured = json.loads(Path(args.calibrate).read_text(encoding="utf-8"))
        new, changes = apply_calibration(lib, measured)
        for name, det, what in changes:
            print(f"{name} {det or ''}: {what}")
        if args.write:
            Path(args.library).write_text(json.dumps(new, indent=1) + "\n", encoding="utf-8")
            print(f"wrote {args.library}")
        else:
            print("(dry: pass --write to save)")
        return 0

    if args.list or not args.scenarios:
        for s in lib["scenarios"]:
            print(f"{s['name']:28s} {s['tier']:8s} runs={s['runs']} pending={s['pending']}"
                  + ("  FIRE" if s['requires'].get('allow_fire') else ""))
        return 0

    scns = [with_variant(scenario(lib, n), args.variant) for n in args.scenarios]
    if not args.send:
        # DRY RUN: no network call of any kind.
        for s in scns:
            for line in plan_lines(s, args.hold_scale):
                print(line)
            if s["requires"].get("allow_fire") and not args.allow_fire:
                print("   (would be REFUSED: fire scenario without --allow-fire)")
        print("\nDRY RUN - nothing was sent. Re-run with --send to inject.")
        return 0

    for s in scns:
        if s["requires"].get("allow_fire") and not args.allow_fire:
            print(f"REFUSED: {s['name']} is a fire scenario; needs --allow-fire and the "
                  f"user's explicit approval for this run")
            return 3
    from tools._live_api import Client
    sim = Client.login(args.sim)
    runner = Runner(sim, _platform_client(args), allow_fire=args.allow_fire,
                    skip_enable=args.skip_enable, hold_scale=args.hold_scale)
    out = Path(args.out) if args.out else Path(os.environ.get("TEMP", "/tmp")) / \
        f"anomaly_campaign_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    results = []
    for s in scns:
        for k in range(args.runs or s["runs"]):
            print(f"\n### {s['name']} run {k + 1}/{args.runs or s['runs']}", flush=True)
            try:
                results.append(runner.run(s))
            except Refused as exc:
                print(f"   REFUSED: {exc}")
                results.append({"scenario": s["name"], "refused": str(exc)})
                break
            out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")
    suspect = [r["scenario"] for r in results
               if r.get("refused") or not (r.get("clean_baseline") is not False
                                           and r.get("all_landed"))]
    print("SUSPECT rows (refused, dirty baseline or injection did not land):", suspect or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

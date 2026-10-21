"""Injection ledger (doc 32 §7, C3): every injection, from every channel, on disk.

Event-level scoring counts a finding as false when it overlaps no injection. An
injection made through an OLD endpoint but missing from the ledger would score a
correct detection as a false alarm, so every channel writes here, not only the
new ``/api/faults`` router (§7.1).

Format: event-sourced JSONL, one event per line, appended and never rewritten
(§7.2)::

    {"v":1,"op":"open","id":"flt_…","kind":"sensor","device":…,"start":…,…}
    {"v":1,"op":"close","id":"flt_…","end":…,"end_reason":"expired"}
    {"v":1,"op":"mark","id":"flt_…","at":…,"what":"target_reached"}
    {"v":1,"op":"resume","id":"flt_…","at":…,"gap":[from, to]}

Every line is flushed and fsync'd: the rate is a few lines an hour, so the cost
is nil, and a crash can at worst truncate the LAST line, which the fold skips
with one warning.

The file lives in ``datasets/faults/``, OUTSIDE ``datasets/snmp/``: snmpsim
serves every file in that directory and the orphan reaper deletes strays.

Thread-safety: every public method takes ``self._lock``; API threads and the
ticker may both call it.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

LEDGER_V = 1
DEFAULT_DIR = Path("datasets") / "faults"
END_REASONS = ("deleted", "expired", "target_reached", "cleared", "superseded",
               "sim_restart", "restore")
KINDS = ("sensor", "degradation", "override", "ramp", "setpoint", "fault_menu", "pin",
         "electrical", "fire", "link", "power", "weather", "context", "maintenance")


# ── time ─────────────────────────────────────────────────────────────────────

def utc_iso(ts: float) -> str:
    """UTC ISO-8601 with milliseconds and a Z, e.g. 2026-10-21T10:15:00.123Z."""
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_iso(s: Any) -> Optional[float]:
    """Epoch seconds from an ISO-8601 string (Z or offset), or None."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def new_fault_id(now: Optional[float] = None) -> str:
    """``flt_<utc yyyymmddThhmmss>_<6 hex>`` — time-sortable, collision-safe."""
    ts = time.time() if now is None else float(now)
    stamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"flt_{stamp}_{secrets.token_hex(3)}"


# ── simulator identity stamped on every open ─────────────────────────────────

_HEAD_CACHE: Optional[str] = None


def _git_head() -> str:
    """Short commit of the running tree, read from .git without a subprocess."""
    global _HEAD_CACHE
    if _HEAD_CACHE is not None:
        return _HEAD_CACHE
    head = ""
    try:
        here = Path(__file__).resolve().parent
        for d in (here, *here.parents):
            g = d / ".git"
            if g.is_dir():
                ref = (g / "HEAD").read_text(encoding="utf-8").strip()
                if ref.startswith("ref:"):
                    name = ref.split(None, 1)[1].strip()
                    p = g / name
                    if p.exists():
                        head = p.read_text(encoding="utf-8").strip()
                    else:
                        packed = g / "packed-refs"
                        if packed.exists():
                            for ln in packed.read_text(encoding="utf-8").splitlines():
                                if ln.endswith(" " + name):
                                    head = ln.split()[0]
                                    break
                else:
                    head = ref
                break
    except Exception:
        head = ""
    _HEAD_CACHE = head[:7]
    return _HEAD_CACHE


def sim_info() -> Dict[str, Any]:
    from core.sim_seed import GLOBAL_SEED
    mode = os.environ.get("DCIM_SIM_AIR_MODEL", "uniform").strip().lower()
    if mode not in ("uniform", "spatial", "shadow"):
        mode = "uniform"
    return {"seed": GLOBAL_SEED, "air_mode": mode, "head": _git_head()}


# ── the ledger ───────────────────────────────────────────────────────────────

class FaultLedger:
    """Append-only JSONL ledger with an in-memory index of OPEN entries.

    ``key`` (optional on :meth:`open`) is a runtime handle for channels that set
    and clear by address rather than by id — ``("override", name, point)``, say.
    Opening a key that is already open closes the old entry as ``superseded``.
    Keys are not persisted: on restart every open entry that was not resumed is
    closed as ``sim_restart`` (:meth:`close_stale`).
    """

    def __init__(self, path: "str | os.PathLike | None" = None):
        self.path = Path(path) if path is not None else DEFAULT_DIR / "ledger.jsonl"
        self._lock = threading.RLock()
        self._open: Dict[str, Dict[str, Any]] = {}       # id -> open record
        self._by_key: Dict[tuple, str] = {}
        self._key_of: Dict[str, tuple] = {}
        self._last_event_ts: Optional[float] = None
        for e in self._fold():
            if e.get("end") is None:
                self._open[e["id"]] = e

    # -- low level --------------------------------------------------------------
    def _append(self, rec: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False, default=str)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        self._last_event_ts = time.time()

    def _lines(self) -> List[tuple]:
        """[(line_no, record)], corrupt lines skipped with ONE warning."""
        out: List[tuple] = []
        if not self.path.exists():
            return out
        bad = 0
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                    if not isinstance(rec, dict) or "op" not in rec or "id" not in rec:
                        raise ValueError("not a ledger event")
                except ValueError:
                    bad += 1
                    continue
                out.append((i, rec))
        if bad:
            log.warning("fault ledger %s: skipped %d unreadable line(s) (a crash "
                        "mid-write truncates the last one)", self.path, bad)
        return out

    def _fold(self) -> List[Dict[str, Any]]:
        """Fold events into entries, in open order. Each carries ``_line``."""
        entries: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for line_no, rec in self._lines():
            op, fid = rec.get("op"), rec.get("id")
            if op == "open":
                e = {k: v for k, v in rec.items() if k not in ("v", "op")}
                e.setdefault("end", None)
                e.setdefault("end_reason", None)
                e.setdefault("marks", [])
                e.setdefault("resumes", [])
                e["_line"] = line_no
                if fid not in entries:
                    order.append(fid)
                entries[fid] = e
                continue
            e = entries.get(fid)
            if e is None:
                continue                         # close/mark without open: tolerated
            if op == "close":
                e["end"] = rec.get("end")
                e["end_reason"] = rec.get("end_reason")
            elif op == "mark":
                e["marks"].append({k: v for k, v in rec.items() if k not in ("v", "op", "id")})
            elif op == "resume":
                e["resumes"].append({k: v for k, v in rec.items() if k not in ("v", "op", "id")})
        return [entries[i] for i in order]

    # -- writes -----------------------------------------------------------------
    def open(self, kind: str, *, device: Optional[str] = None,
             device_id: Optional[str] = None, point: Optional[str] = None,
             params: Optional[dict] = None, expected_kind: str = "none",
             metric_hint: Optional[dict] = None, source: str = "",
             campaign: Optional[str] = None, key: Optional[tuple] = None,
             start: Optional[float] = None, fault_id: Optional[str] = None,
             extra: Optional[dict] = None) -> Dict[str, Any]:
        ts = time.time() if start is None else float(start)
        with self._lock:
            if key is not None and key in self._by_key:
                self.close(self._by_key[key], "superseded", end=ts)
            rec: Dict[str, Any] = {
                "v": LEDGER_V, "op": "open", "id": fault_id or new_fault_id(ts),
                "kind": kind, "device": device, "device_id": device_id, "point": point,
                "start": utc_iso(ts), "params": dict(params or {}),
                "expected_kind": expected_kind,
            }
            if metric_hint is not None:
                rec["metric_hint"] = metric_hint
            rec["source"] = source
            if campaign:
                rec["campaign"] = campaign
            rec["sim"] = sim_info()
            if extra:
                rec.update(extra)
            self._append(rec)
            entry = {k: v for k, v in rec.items() if k not in ("v", "op")}
            entry.update(end=None, end_reason=None, marks=[], resumes=[])
            self._open[rec["id"]] = entry
            if key is not None:
                self._by_key[key] = rec["id"]
                self._key_of[rec["id"]] = key
            return dict(entry)

    def close(self, fault_id: str, end_reason: str, end: Optional[float] = None
              ) -> Optional[Dict[str, Any]]:
        """Close an open entry. Returns the closed entry, or None if not open."""
        if end_reason not in END_REASONS:
            raise ValueError(f"end_reason {end_reason!r} not in {END_REASONS}")
        ts = time.time() if end is None else float(end)
        with self._lock:
            e = self._open.pop(fault_id, None)
            if e is None:
                return None
            k = self._key_of.pop(fault_id, None)
            if k is not None and self._by_key.get(k) == fault_id:
                self._by_key.pop(k, None)
            self._append({"v": LEDGER_V, "op": "close", "id": fault_id,
                          "end": utc_iso(ts), "end_reason": end_reason})
            e = dict(e)
            e["end"], e["end_reason"] = utc_iso(ts), end_reason
            return e

    def close_key(self, key: tuple, end_reason: str = "cleared",
                  end: Optional[float] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            fid = self._by_key.get(key)
            return self.close(fid, end_reason, end=end) if fid else None

    def instant(self, kind: str, *, end_reason: str = "cleared", **kw) -> Dict[str, Any]:
        """A point-in-time event (a protocol start, a dataset regeneration):
        opened and closed at the same instant."""
        with self._lock:
            ts = time.time() if kw.get("start") is None else float(kw["start"])
            kw["start"] = ts
            kw.pop("key", None)
            e = self.open(kind, **kw)
            return self.close(e["id"], end_reason, end=ts) or e

    def mark(self, fault_id: str, what: str, at: Optional[float] = None, **extra) -> None:
        ts = time.time() if at is None else float(at)
        with self._lock:
            rec = {"v": LEDGER_V, "op": "mark", "id": fault_id, "at": utc_iso(ts), "what": what}
            rec.update(extra)
            self._append(rec)
            if fault_id in self._open:
                self._open[fault_id]["marks"].append(
                    {k: v for k, v in rec.items() if k not in ("v", "op", "id")})

    def resume(self, fault_id: str, gap_from: float, gap_to: Optional[float] = None) -> None:
        ts = time.time() if gap_to is None else float(gap_to)
        with self._lock:
            rec = {"v": LEDGER_V, "op": "resume", "id": fault_id, "at": utc_iso(ts),
                   "gap": [utc_iso(gap_from), utc_iso(ts)]}
            self._append(rec)
            if fault_id in self._open:
                self._open[fault_id]["resumes"].append({"at": rec["at"], "gap": rec["gap"]})

    def adopt(self, fault_id: str, key: Optional[tuple] = None) -> bool:
        """Re-attach a runtime key to an entry that is still open on disk (used
        when a fault is resumed after a restart)."""
        with self._lock:
            if fault_id not in self._open:
                return False
            if key is not None:
                self._by_key[key] = fault_id
                self._key_of[fault_id] = key
            return True

    def close_stale(self, at: Optional[float] = None, keep: Iterable[str] = (),
                    end_reason: str = "sim_restart") -> List[str]:
        """On start: close every entry still open on disk that was not resumed
        (plant overrides, pins, ramps … are in-memory and died with the process).
        *at* is the best known death time (last state save); default now."""
        keep = set(keep)
        with self._lock:
            ids = [i for i in list(self._open) if i not in keep and i not in self._key_of]
            for i in ids:
                self.close(i, end_reason, end=at)
            return ids

    # -- reads ------------------------------------------------------------------
    def open_entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._open.values()]

    def open_id(self, key: tuple) -> Optional[str]:
        with self._lock:
            return self._by_key.get(key)

    def get(self, fault_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for e in self._fold():
                if e["id"] == fault_id:
                    return _public(e)
        return None

    def query(self, since: Any = None, until: Any = None, kind: Optional[str] = None,
              device: Optional[str] = None, campaign: Optional[str] = None,
              limit: int = 500, cursor: Any = None) -> Dict[str, Any]:
        """Entries overlapping [since, until], newest first, paginated by an
        opaque cursor (the open line's offset in the file)."""
        t0, t1 = parse_iso(since), parse_iso(until)
        try:
            cur = int(cursor) if cursor not in (None, "") else None
        except (TypeError, ValueError):
            cur = None
        limit = max(1, min(int(limit or 500), 5000))
        with self._lock:
            folded = self._fold()
        out: List[Dict[str, Any]] = []
        nxt = None
        for e in reversed(folded):
            if cur is not None and e["_line"] >= cur:
                continue
            if kind and e.get("kind") != kind:
                continue
            if device and device not in (e.get("device"), e.get("device_id")):
                continue
            if campaign and e.get("campaign") != campaign:
                continue
            s, en = parse_iso(e.get("start")), parse_iso(e.get("end"))
            if t1 is not None and s is not None and s > t1:
                continue
            if t0 is not None and en is not None and en < t0:
                continue
            if len(out) >= limit:
                nxt = str(out[-1]["_line_ref"])
                break
            pe = _public(e)
            pe["_line_ref"] = e["_line"]
            out.append(pe)
        for pe in out:
            pe.pop("_line_ref", None)
        return {"entries": out, "next_cursor": nxt}


def _public(e: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in e.items() if not k.startswith("_")}


# ── process-wide instance ────────────────────────────────────────────────────

_LEDGER: Optional[FaultLedger] = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> FaultLedger:
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = FaultLedger(os.environ.get("DCIM_FAULT_LEDGER") or None)
        return _LEDGER


def set_ledger(ledger: Optional[FaultLedger]) -> None:
    """Swap the process ledger (tests)."""
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = ledger


def safe(fn, *a, **kw):
    """Call a ledger hook without ever letting a ledger failure break the
    injection it records. Returns the hook's result or None."""
    try:
        return fn(*a, **kw)
    except Exception as exc:          # pragma: no cover - defensive
        log.warning("fault ledger hook failed: %s", exc)
        return None

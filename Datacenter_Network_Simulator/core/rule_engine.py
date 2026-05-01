"""
Rule Engine — lightweight Rete-inspired engine that evaluates SNMP trap rules
against streaming device telemetry facts.

Architecture
------------
Facts (DeviceFact) arrive once per tick per device from DeviceStateStore.
Rules are declared as Rule objects (loaded from trap_rules.py or JSON).

Evaluation flow:
  DeviceFact → metric extraction → rule router → per-type evaluator
             → duration check → TrapAction → action_callback

Rule types supported
--------------------
  threshold   : metric op value [for duration_sec]
  state_change: metric transitions from_state → to_state
  temporal    : event occurs N times within window_sec
  composite   : AND/OR of sub-conditions (scalar metrics only)
  rack_failure: ≥N devices in same rack are operationally impaired
  recovery    : fires when alert condition is no longer met

Performance strategy
--------------------
  • Per-device state kept in nested dicts — O(1) lookup by device_id → rule_name
  • Single global lock protects rule list mutations; state mutations are already
    serialized by the caller (single ticker thread per DeviceStateStore)
  • Sliding windows are deque-based with timestamp-based expiry on access
  • Rule evaluation is O(R) per fact where R = number of enabled rules
  • Interface rules fire once per changed interface (not per rule invocation)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from core.fact_model import DeviceFact

log = logging.getLogger(__name__)

# ── Condition dataclass ───────────────────────────────────────────────────────

@dataclass
class Condition:
    """
    A single evaluable predicate.

    condition_type  Values
    ──────────────  ──────────────────────────────────────────────────────────
    threshold       metric op threshold [duration_sec]
    state_change    metric from_state → to_state  (None = wildcard)
    temporal        event_type occurs ≥ event_count times within window_sec
    composite       logic(AND|OR) of sub-conditions list
    rack_failure    ≥ threshold devices in same rack impaired
    """
    condition_type: str
    metric: str = ""
    operator: str = ">"
    threshold: float = 0.0
    duration_sec: float = 0.0
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    event_type: str = ""
    event_count: int = 1
    window_sec: float = 60.0
    conditions: List["Condition"] = field(default_factory=list)
    logic: str = "AND"   # for composite


# ── Rule dataclass ────────────────────────────────────────────────────────────

@dataclass
class Rule:
    rule_name: str
    condition: Condition
    trap_oid: str
    severity: str = "major"
    enabled: bool = True
    priority: int = 100         # higher value = higher priority (fires first)
    device_types: List[str] = field(default_factory=list)  # empty = all types
    is_recovery: bool = False
    recovery_of: str = ""       # alert rule name this rule recovers from

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "trap_oid": self.trap_oid,
            "severity": self.severity,
            "enabled": self.enabled,
            "priority": self.priority,
            "device_types": self.device_types,
            "is_recovery": self.is_recovery,
            "recovery_of": self.recovery_of,
            "condition": _condition_to_dict(self.condition),
        }

    @staticmethod
    def from_dict(d: dict) -> "Rule":
        return Rule(
            rule_name=d["rule_name"],
            condition=_condition_from_dict(d["condition"]),
            trap_oid=d["trap_oid"],
            severity=d.get("severity", "major"),
            enabled=d.get("enabled", True),
            priority=d.get("priority", 100),
            device_types=d.get("device_types", []),
            is_recovery=d.get("is_recovery", False),
            recovery_of=d.get("recovery_of", ""),
        )


def _condition_to_dict(c: Condition) -> dict:
    return {
        "condition_type": c.condition_type,
        "metric": c.metric,
        "operator": c.operator,
        "threshold": c.threshold,
        "duration_sec": c.duration_sec,
        "from_state": c.from_state,
        "to_state": c.to_state,
        "event_type": c.event_type,
        "event_count": c.event_count,
        "window_sec": c.window_sec,
        "logic": c.logic,
        "conditions": [_condition_to_dict(sub) for sub in c.conditions],
    }


def _condition_from_dict(d: dict) -> Condition:
    return Condition(
        condition_type=d["condition_type"],
        metric=d.get("metric", ""),
        operator=d.get("operator", ">"),
        threshold=float(d.get("threshold", 0)),
        duration_sec=float(d.get("duration_sec", 0)),
        from_state=d.get("from_state"),
        to_state=d.get("to_state"),
        event_type=d.get("event_type", ""),
        event_count=int(d.get("event_count", 1)),
        window_sec=float(d.get("window_sec", 60)),
        logic=d.get("logic", "AND"),
        conditions=[_condition_from_dict(sub) for sub in d.get("conditions", [])],
    )


# ── TrapAction — what the engine wants fired ─────────────────────────────────

@dataclass
class TrapAction:
    rule: Rule
    device_id: str
    extra: dict = field(default_factory=dict)
    # device_ref is injected by RuleEngine before calling the callback
    device_ref: Any = None


# ── Per-device, per-rule mutable state ───────────────────────────────────────

@dataclass
class RuleState:
    condition_true_since: Optional[float] = None
    in_alert: bool = False
    fired_count: int = 0
    last_fire_ts: str = ""


# ── Rule Engine ───────────────────────────────────────────────────────────────

class RuleEngine:
    """
    Evaluates rules against incoming DeviceFact objects.

    Thread safety
    -------------
    _rules mutations are protected by _rules_lock.
    State dicts (_rule_states, _prev_iface, etc.) are only written from the
    single background ticker thread that calls evaluate_fact() — no separate
    lock needed for those.
    """

    def __init__(self):
        self._rules_lock = threading.Lock()
        self._rules: Dict[str, Rule] = {}
        self._action_cb: Optional[Callable[[TrapAction], None]] = None

        # Per-device, per-rule state
        # device_id (or "rack:{rack_id}") → rule_state_key → RuleState
        self._rule_states: Dict[str, Dict[str, RuleState]] = defaultdict(dict)

        # Previous interface oper_status per device
        # device_id → iface_index → oper_status (1=up, 2=down)
        self._prev_iface: Dict[str, Dict[int, int]] = defaultdict(dict)

        # Previous scalar metric values per device
        # device_id → metric_name → value (Any)
        self._prev_metrics: Dict[str, Dict[str, Any]] = defaultdict(dict)

        # Previous BGP session states: device_id → peer_addr → state string
        self._prev_bgp: Dict[str, Dict[str, str]] = defaultdict(dict)

        # Temporal sliding windows: device_id → event_type → deque[timestamp]
        self._event_windows: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(deque)
        )

        # Rack correlation: rack_id → set of device_ids that are impaired
        self._rack_impaired: Dict[str, Set[str]] = defaultdict(set)

        # Manual/test fires not routed through _do_fire (e.g. initial test traps)
        self._manual_fire_counts: Dict[str, int] = defaultdict(int)

        # Tracks (device_id, iface_index) pairs whose LinkDown actually fired.
        # A LinkUp may only fire for a pair present here; it is removed after firing.
        self._link_down_pending_up: Set[tuple] = set()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_action_callback(self, cb: Callable[[TrapAction], None]):
        self._action_cb = cb

    def add_rule(self, rule: Rule):
        with self._rules_lock:
            self._rules[rule.rule_name] = rule
        log.debug("[RuleEngine] Rule added/updated: %s", rule.rule_name)

    def remove_rule(self, name: str):
        with self._rules_lock:
            self._rules.pop(name, None)

    def enable_rule(self, name: str, enabled: bool):
        with self._rules_lock:
            if name in self._rules:
                self._rules[name].enabled = enabled

    def get_rules(self) -> List[Rule]:
        with self._rules_lock:
            return sorted(self._rules.values(), key=lambda r: -r.priority)

    def get_rule_state(self, device_id: str, rule_name: str) -> Optional[RuleState]:
        return self._rule_states.get(device_id, {}).get(rule_name)

    def record_manual_fire(self, rule_name: str):
        """Register a test/manual fire so get_total_fired_count stays authoritative."""
        self._manual_fire_counts[rule_name] += 1

    def get_total_fired_count(self, rule_name: str) -> int:
        """
        Sum fired_count across every device and every sub-key for this rule,
        including any manual/test fires recorded via record_manual_fire().

        Interface/BGP rules store state under compound keys
        (e.g. "LinkDown:if1", "BGPSessionDown:bgp:10.0.0.1") and rack rules
        use "rack:<rack_id>" as the device key, so a simple per-device lookup
        by bare rule_name misses most fires.
        """
        total = self._manual_fire_counts.get(rule_name, 0)
        prefix = f"{rule_name}:"
        for device_states in self._rule_states.values():
            for key, state in device_states.items():
                if key == rule_name or key.startswith(prefix):
                    total += state.fired_count
        return total

    def get_grand_total_fired(self) -> int:
        """Total fires across all rules and all devices, including manual fires."""
        total = sum(self._manual_fire_counts.values())
        for device_states in self._rule_states.values():
            for state in device_states.values():
                total += state.fired_count
        return total

    def get_last_fire_ts(self, rule_name: str) -> str:
        """Return the most recent last_fire_ts string across all state entries for this rule."""
        latest = ""
        prefix = f"{rule_name}:"
        for device_states in self._rule_states.values():
            for key, state in device_states.items():
                if key == rule_name or key.startswith(prefix):
                    if state.last_fire_ts and state.last_fire_ts > latest:
                        latest = state.last_fire_ts
        return latest

    def reset_fired_counts(self):
        """Zero all fired counts and last-fire timestamps across every rule and device."""
        self._manual_fire_counts.clear()
        for device_states in self._rule_states.values():
            for state in device_states.values():
                state.fired_count = 0
                state.last_fire_ts = ""

    def clear_device_state(self, device_id: str):
        self._rule_states.pop(device_id, None)
        self._prev_iface.pop(device_id, None)
        self._prev_metrics.pop(device_id, None)
        self._prev_bgp.pop(device_id, None)
        self._event_windows.pop(device_id, None)
        self._link_down_pending_up = {p for p in self._link_down_pending_up if p[0] != device_id}

    # ── Main evaluation entry point ───────────────────────────────────────────

    def evaluate_fact(self, fact: DeviceFact, device_ref: Any = None):
        """
        Evaluate all enabled rules against `fact`.

        Called by DeviceStateStore's ticker thread once per device per tick.
        Fires action_cb for each rule that triggers.
        """
        if not self._action_cb:
            return

        now = fact.timestamp
        metrics = self._extract_metrics(fact)

        # Rack state updated FIRST so RackFailure rules see the current device's status.
        # Interface/BGP history updated LAST so state-change detectors compare
        # the previous tick's values against this tick's (transition detection).
        self._update_rack_state(fact)

        with self._rules_lock:
            rules = sorted(self._rules.values(), key=lambda r: -r.priority)

        actions: List[TrapAction] = []

        for rule in rules:
            if not rule.enabled:
                continue
            if rule.device_types and fact.device_type not in rule.device_types:
                continue

            cond = rule.condition
            ct = cond.condition_type

            if cond.metric == "interface_status":
                actions.extend(self._eval_interface_rule(rule, fact, now))

            elif cond.metric == "bgp_session":
                actions.extend(self._eval_bgp_rule(rule, fact, now))

            elif ct == "temporal":
                act = self._eval_temporal_rule(rule, fact, now)
                if act:
                    actions.append(act)

            elif ct == "rack_failure":
                act = self._eval_rack_rule(rule, fact, now)
                if act:
                    actions.append(act)

            else:
                act = self._eval_generic_rule(rule, fact, metrics, now)
                if act:
                    actions.append(act)

        self._update_iface_history(fact)
        self._update_bgp_history(fact)
        self._update_scalar_history(fact, metrics)

        for action in actions:
            action.device_ref = device_ref
            try:
                self._action_cb(action)
            except Exception:
                log.exception("[RuleEngine] action_cb raised")

    # ── Metric extraction ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_metrics(fact: DeviceFact) -> Dict[str, Any]:
        return {
            "cpu_usage":    fact.cpu_usage,
            "memory_usage": fact.memory_usage,
            "disk_usage":   fact.disk_usage,
            "temperature":  fact.temperature,
            "humidity":     fact.humidity,
            "ups_status":   fact.ups_status,
        }

    # ── State helpers ─────────────────────────────────────────────────────────

    def _get_state(self, state_key: str, rule_name: str) -> RuleState:
        d = self._rule_states[state_key]
        if rule_name not in d:
            d[rule_name] = RuleState()
        return d[rule_name]

    def _can_fire(self, state: RuleState, rule: Rule, now: float) -> bool:
        return True

    def _do_fire(self, rule: Rule, fact: DeviceFact, state: RuleState, now: float,
                 extra: dict) -> TrapAction:
        state.in_alert = True
        state.fired_count += 1
        state.last_fire_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        log.debug("[RuleEngine] Rule '%s' fired for device %s", rule.rule_name, fact.device_id)
        return TrapAction(rule=rule, device_id=fact.device_id, extra=dict(extra))

    # ── Generic scalar rule evaluator ─────────────────────────────────────────

    def _eval_generic_rule(self, rule: Rule, fact: DeviceFact,
                           metrics: Dict[str, Any], now: float) -> Optional[TrapAction]:
        state = self._get_state(fact.device_id, rule.rule_name)

        if rule.is_recovery:
            return self._eval_recovery_rule(rule, fact, metrics, state, now)

        cond_met = self._eval_condition(rule.condition, metrics, fact.device_id, now)

        # Duration tracking for threshold rules
        if rule.condition.condition_type == "threshold" and rule.condition.duration_sec > 0:
            if cond_met:
                if state.condition_true_since is None:
                    state.condition_true_since = now
                cond_met = (now - state.condition_true_since) >= rule.condition.duration_sec
            else:
                state.condition_true_since = None
                if state.in_alert:
                    state.in_alert = False
                return None

        if cond_met and self._can_fire(state, rule, now):
            ct = rule.condition.condition_type
            if ct == "composite":
                # Extract each sub-condition's metric value individually.
                # rule.condition.metric is "" for composites, so metrics.get("", 0) = 0.
                extra = {sub.metric: metrics.get(sub.metric, 0)
                         for sub in rule.condition.conditions if sub.metric}
            else:
                extra = {"metric_value": metrics.get(rule.condition.metric, 0)}
            return self._do_fire(rule, fact, state, now, extra)

        # NOTE: in_alert is intentionally NOT cleared here when cond_met=False.
        # Only recovery rules clear in_alert so they can detect the cleared state.
        # Cooldown already prevents re-firing; recovery rules handle the "all clear".
        return None

    def _eval_recovery_rule(self, rule: Rule, fact: DeviceFact,
                            metrics: Dict[str, Any], state: RuleState, now: float) -> Optional[TrapAction]:
        # Find alert rule to check if we're currently in alert
        alert_rule_name = rule.recovery_of or rule.rule_name.replace("Recovery", "").replace("Normal", "")
        alert_state = self._rule_states.get(fact.device_id, {}).get(alert_rule_name)
        if alert_state is None or not alert_state.in_alert:
            return None

        # Recovery condition: the recovery rule's condition is the "all clear" predicate
        cond_met = self._eval_condition(rule.condition, metrics, fact.device_id, now)
        if cond_met and self._can_fire(state, rule, now):
            alert_state.in_alert = False
            return self._do_fire(rule, fact, state, now, {
                "metric_value": metrics.get(rule.condition.metric, 0)
            })
        return None

    def _eval_condition(self, cond: Condition, metrics: Dict[str, Any],
                        device_id: str, now: float) -> bool:
        t = cond.condition_type

        if t == "threshold":
            val = metrics.get(cond.metric)
            if val is None:
                return False
            return _compare(val, cond.operator, cond.threshold)

        if t == "state_change":
            curr = metrics.get(cond.metric)
            prev = self._prev_metrics.get(device_id, {}).get(cond.metric)
            # NOTE: do NOT write to _prev_metrics here. Multiple rules may check
            # the same metric (e.g. UPSOnBattery + UPSLowBattery both use
            # "ups_status"). Writing during eval would make the second rule see
            # prev == curr and miss the transition. History is updated once per
            # tick in _update_scalar_history(), after all rules have been evaluated.
            if prev is None or curr is None or prev == curr:
                return False
            from_ok = cond.from_state is None or str(prev).lower() == cond.from_state.lower()
            to_ok = cond.to_state is None or str(curr).lower() == cond.to_state.lower()
            return from_ok and to_ok

        if t == "composite":
            results = [
                self._eval_condition(c, metrics, device_id, now)
                for c in cond.conditions
            ]
            if not results:
                return False
            return all(results) if cond.logic.upper() == "AND" else any(results)

        return False

    # ── Interface state-change rules ──────────────────────────────────────────

    def _eval_interface_rule(self, rule: Rule, fact: DeviceFact,
                             now: float) -> List[TrapAction]:
        actions: List[TrapAction] = []
        cond = rule.condition
        prev = self._prev_iface.get(fact.device_id, {})

        for iface in fact.interfaces:
            prev_status = prev.get(iface.index)
            if prev_status is None or prev_status == iface.oper_status:
                continue

            prev_str = "up" if prev_status == 1 else "down"
            curr_str = "up" if iface.oper_status == 1 else "down"

            from_ok = cond.from_state is None or prev_str == cond.from_state.lower()
            to_ok = cond.to_state is None or curr_str == cond.to_state.lower()
            if not (from_ok and to_ok):
                continue

            # Record event for temporal rules (link flap detection)
            event_name = "linkDown" if iface.oper_status == 2 else "linkUp"
            self._event_windows[fact.device_id][event_name].append(now)

            pair_key = (fact.device_id, iface.index)
            going_up = (iface.oper_status == 1)

            # LinkUp may only fire for an interface whose LinkDown actually fired.
            # LinkUp may only fire for an interface whose LinkDown actually fired.
            # If the LinkDown never fired, the pending set won't contain this pair.
            if going_up and pair_key not in self._link_down_pending_up:
                continue

            state_key = f"{rule.rule_name}:if{iface.index}"
            state = self._get_state(fact.device_id, state_key)
            if self._can_fire(state, rule, now):
                actions.append(self._do_fire(rule, fact, state, now, {
                    "iface_index": iface.index,
                    "iface_name":  iface.name,
                    "iface_oper":  iface.oper_status,
                }))
                if going_up:
                    self._link_down_pending_up.discard(pair_key)
                else:
                    self._link_down_pending_up.add(pair_key)

        return actions

    # ── BGP state-change rules ────────────────────────────────────────────────

    def _eval_bgp_rule(self, rule: Rule, fact: DeviceFact,
                       now: float) -> List[TrapAction]:
        actions: List[TrapAction] = []
        cond = rule.condition
        prev = self._prev_bgp.get(fact.device_id, {})

        for sess in fact.bgp_sessions:
            prev_state = prev.get(sess.peer_addr)
            curr_state = sess.state.lower()
            if prev_state is None or prev_state == curr_state:
                continue
            from_ok = cond.from_state is None or prev_state == cond.from_state.lower()
            to_ok = cond.to_state is None or curr_state == cond.to_state.lower()
            if not (from_ok and to_ok):
                continue
            state_key = f"{rule.rule_name}:bgp:{sess.peer_addr}"
            state = self._get_state(fact.device_id, state_key)
            if self._can_fire(state, rule, now):
                actions.append(self._do_fire(rule, fact, state, now, {
                    "peer_addr": sess.peer_addr,
                    "bgp_state": curr_state,
                }))

        return actions

    # ── Temporal rules (sliding window) ──────────────────────────────────────

    def _eval_temporal_rule(self, rule: Rule, fact: DeviceFact,
                            now: float) -> Optional[TrapAction]:
        cond = rule.condition
        window = self._event_windows[fact.device_id][cond.event_type]
        cutoff = now - cond.window_sec
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= cond.event_count:
            state = self._get_state(fact.device_id, rule.rule_name)
            if self._can_fire(state, rule, now):
                return self._do_fire(rule, fact, state, now, {
                    "flap_count": len(window),
                    "window_sec": cond.window_sec,
                })
        return None

    # ── Rack failure (cross-device correlation) ───────────────────────────────

    def _eval_rack_rule(self, rule: Rule, fact: DeviceFact,
                        now: float) -> Optional[TrapAction]:
        rack_id = fact.rack_id
        if not rack_id:
            return None

        impaired_count = len(self._rack_impaired.get(rack_id, set()))
        threshold = max(2, int(rule.condition.threshold))

        if impaired_count >= threshold:
            state = self._get_state(f"rack:{rack_id}", rule.rule_name)
            if self._can_fire(state, rule, now):
                # Use a synthetic TrapAction; device_id is the triggering device
                action = TrapAction(
                    rule=rule,
                    device_id=fact.device_id,
                    extra={
                        "rack_id":      rack_id,
                        "down_count":   impaired_count,
                        "down_devices": list(self._rack_impaired[rack_id]),
                    },
                )
                state.fired_count += 1
                state.in_alert = True
                state.last_fire_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                return action

        return None

    # ── History updates ───────────────────────────────────────────────────────

    def _update_iface_history(self, fact: DeviceFact):
        self._prev_iface[fact.device_id] = {
            i.index: i.oper_status for i in fact.interfaces
        }

    def _update_bgp_history(self, fact: DeviceFact):
        self._prev_bgp[fact.device_id] = {
            s.peer_addr: s.state.lower() for s in fact.bgp_sessions
        }

    def _update_scalar_history(self, fact: DeviceFact, metrics: Dict[str, Any]):
        """
        Snapshot all scalar metric values for state_change detection next tick.

        Must be called AFTER all rules have been evaluated so that every rule
        in the same tick reads the same prev value (the one from the previous
        tick). Writing prev inside _eval_condition caused rules evaluated later
        in priority order to see prev == curr and miss the transition.
        """
        self._prev_metrics[fact.device_id].update(metrics)

    def _update_rack_state(self, fact: DeviceFact):
        rack_id = fact.rack_id
        if not rack_id:
            return
        # Device is "impaired" if ≥50% of interfaces are operationally down
        ifaces = fact.interfaces
        if not ifaces:
            return
        down_count = sum(1 for i in ifaces if i.oper_status == 2)
        if down_count / len(ifaces) >= 0.5:
            self._rack_impaired[rack_id].add(fact.device_id)
        else:
            self._rack_impaired[rack_id].discard(fact.device_id)


# ── Utility ───────────────────────────────────────────────────────────────────

def _compare(val: Any, op: str, threshold: Any) -> bool:
    try:
        v = float(val)
        t = float(threshold)
        return {
            ">":  v > t,
            ">=": v >= t,
            "<":  v < t,
            "<=": v <= t,
            "==": v == t,
            "!=": v != t,
        }.get(op, False)
    except (TypeError, ValueError):
        return str(val) == str(threshold)
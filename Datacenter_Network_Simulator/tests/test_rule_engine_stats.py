"""The rules table must be built from ONE state snapshot.

`_states_snapshot()` deep-copies every device's rule-state dict under
`_states_lock` — the same lock the tick thread takes in `evaluate_fact` for every
device on every tick. The rules table used to call `get_total_fired_count()` and
`get_last_fire_ts()` once per rule, plus `get_grand_total_fired()`, so a single
GET /rules took (2 x rules + 1) full copies of the fleet's rule state, all
serialised on that lock.

The symptom was not a slow endpoint, it was a STALLED SIMULATOR. Caught with a
py-spy dump of the live app: the ticker sat in `rule_engine.evaluate_fact` while an
API worker sat in `_states_snapshot` under `get_all_rules`. Run_Hours — a counter
that can only ever increase — was frozen, published telemetry was frozen, and any
plant fault injected during the stall silently did nothing: the override was
stored, the API answered ok, and nothing ever read it. That is the mechanism behind
fault injections that "randomly" have no effect, and behind fault-campaign rows
that returned with pre and during identical on every field.

So these tests pin two things: the aggregate is EQUIVALENT to the per-rule calls it
replaced, and it takes exactly one snapshot however many rules are registered.
"""
from core.rule_engine import RuleEngine, RuleState
from core.trap_rules import DEFAULT_RULES


def _engine():
    """A RuleEngine loaded the way the app loads it. A bare engine registers no
    rules at all, so the table would be empty and every assertion vacuous."""
    eng = RuleEngine()
    for rule in DEFAULT_RULES:
        eng.add_rule(rule)
    return eng


def _engine_with_state():
    """An engine carrying rule state for several devices, including the
    'rule:suffix' key shape the per-rule matching has to honour."""
    eng = _engine()
    rules = eng.get_rules()
    assert rules, "DEFAULT_RULES should have registered"

    names = [r.rule_name for r in rules][:3]
    for i, name in enumerate(names, start=1):
        for dev in (f"dev{i}a", f"dev{i}b"):
            st = RuleState()
            st.fired_count = i * 2
            st.last_fire_ts = f"2026-08-04T10:0{i}:00"
            eng._rule_states[dev][name] = st
        # A suffixed key for the same rule — the per-device/per-interface shape.
        st = RuleState()
        st.fired_count = 1
        st.last_fire_ts = f"2026-08-04T11:0{i}:00"
        eng._rule_states[f"dev{i}a"][f"{name}:eth0"] = st
    return eng, names


def test_table_stats_match_the_per_rule_calls():
    """Equivalence. If this drifts, the table silently starts reporting different
    numbers from the per-rule accessors the rest of the code still uses."""
    eng, _names = _engine_with_state()

    fired, last, grand = eng.get_rules_table_stats()

    for rule in eng.get_rules():
        name = rule.rule_name
        assert fired.get(name, 0) == eng.get_total_fired_count(name), name
        assert (last.get(name, "") or "") == (eng.get_last_fire_ts(name) or ""), name
    assert grand == eng.get_grand_total_fired()


def test_the_table_takes_exactly_one_state_snapshot(monkeypatch):
    """The whole point. One snapshot per request, not one per rule — the lock is
    shared with the tick thread, so the count IS the bug."""
    eng, _names = _engine_with_state()
    assert len(eng.get_rules()) > 1, "need several rules for this to mean anything"

    calls = {"n": 0}
    real = eng._states_snapshot

    def counted():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(eng, "_states_snapshot", counted)
    eng.get_rules_table_stats()
    assert calls["n"] == 1, (
        f"expected 1 snapshot for the whole table, took {calls['n']} — this is the "
        f"lock the ticker needs every tick")


def test_suffixed_state_keys_still_roll_up_to_their_rule():
    """A rule's state is keyed 'rule' or 'rule:<something>' per device; both must
    count toward that rule, or the table under-reports every per-interface rule."""
    eng, names = _engine_with_state()
    fired, _last, _grand = eng.get_rules_table_stats()

    name = names[0]
    # dev1a + dev1b at 2 each, plus the ':eth0' entry at 1.
    assert fired[name] == 5, f"{name} should roll up its suffixed keys, got {fired[name]}"


def test_an_engine_with_no_state_reports_zeroes():
    eng = _engine()
    fired, last, grand = eng.get_rules_table_stats()
    assert grand == 0
    assert set(fired.values()) == {0}
    assert set(last.values()) == {""}

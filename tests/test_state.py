"""State store + week maths (SPEC §3, §4.2, §13).

Two concerns live here per the SPEC §11 file list: the atomic, lock-guarded JSON
store, and the pure week computations (quota / credit / banked / rollover) that
the store persists. Async tests drive a real event loop via ``asyncio.run`` so we
don't pull in a pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.state import StateStore
from app.week import (
    banked_out,
    credit_remaining,
    current_iso_week,
    daily_already_logged,
    iso_week,
    jobs_done,
    maybe_rollover,
    resolve_effort,
    spent_effort,
    spent_ick,
    weekly_quota,
    wheel_tasks,
)


# --------------------------------------------------------------------------- #
#  StateStore: atomic save/load                                                #
# --------------------------------------------------------------------------- #
def test_save_load_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    data = {"version": 1, "n": 0, "nested": {"k": [1, 2, 3]}, "uni": "café ✨"}
    store.save_sync(data)
    assert store.load_sync() == data


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save_sync({"a": 1})
    store.save_sync({"a": 2})  # overwrite
    assert json.loads((tmp_path / "state.json").read_text()) == {"a": 2}
    # no half-written .state-*.tmp left behind
    assert list(tmp_path.glob(".state-*.tmp")) == []


def test_exists(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.exists() is False
    store.save_sync({})
    assert store.exists() is True


def test_async_read_and_mutate(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save_sync({"n": 1})

    def bump(s):
        s["n"] += 10
        return s["n"]

    async def scenario():
        assert (await store.read())["n"] == 1
        result = await store.mutate(bump)
        assert result == 11
        return (await store.read())["n"]

    assert asyncio.run(scenario()) == 11


def test_concurrent_mutations_never_lose_updates(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save_sync({"n": 0})

    def inc(s):
        s["n"] += 1

    async def scenario():
        await asyncio.gather(*[store.mutate(inc) for _ in range(200)])

    asyncio.run(scenario())
    assert store.load_sync()["n"] == 200


def test_failed_mutation_leaves_state_untouched(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save_sync({"n": 5})

    def boom(_s):
        raise ValueError("nope")

    async def scenario():
        with pytest.raises(ValueError):
            await store.mutate(boom)

    asyncio.run(scenario())
    assert store.load_sync() == {"n": 5}
    assert list(tmp_path.glob(".state-*.tmp")) == []


# --------------------------------------------------------------------------- #
#  Week maths: ISO labels, effort resolution, quota                            #
# --------------------------------------------------------------------------- #
def test_iso_week_label():
    assert iso_week(date(2026, 6, 1)) == "2026-W23"
    assert current_iso_week(date(2026, 6, 1)) == "2026-W23"


def test_resolve_effort_token_and_passthrough():
    assert resolve_effort("M") == 4
    assert resolve_effort("XS") == 2
    assert resolve_effort(4) == 4  # already-resolved history ints pass through


def _pool():
    return [
        {"id": "t1", "effort": "M", "ick": 1, "in_play": True, "on_wheel": True},   # 4
        {"id": "t2", "effort": "S", "ick": 0, "in_play": True, "on_wheel": True},   # 3
        {"id": "d1", "effort": "S", "ick": 0, "in_play": True, "on_wheel": False},  # off-wheel
        {"id": "p1", "effort": "L", "ick": 0, "in_play": False, "on_wheel": True},  # paused
    ]


def test_wheel_tasks_filters_in_play_and_on_wheel():
    ids = {t["id"] for t in wheel_tasks(_pool())}
    assert ids == {"t1", "t2"}  # daily (off-wheel) and paused excluded


def test_weekly_quota_is_wheel_effort_over_players():
    # (4 + 3) / 2 players = 3.5; off-wheel/paused tasks never count
    assert weekly_quota(_pool(), ["A", "B"]) == pytest.approx(3.5)


def test_spent_credit_banked_from_history():
    history = [
        {"week": "2026-W23", "task_id": "t1", "player": "A", "effort": 4, "ick": 1},
        {"week": "2026-W23", "task_id": "t2", "player": "A", "effort": 3, "ick": 0},
        {"week": "2026-W22", "task_id": "t1", "player": "A", "effort": 4, "ick": 1},
    ]
    assert spent_effort(history, "2026-W23", "A") == 7
    assert spent_ick(history, "2026-W23", "A") == 1
    assert jobs_done(history, "2026-W23", "A") == 2
    quota = 3.5
    assert credit_remaining(quota, 7) == pytest.approx(-3.5)  # ahead
    assert banked_out(quota, 7) == pytest.approx(3.5)         # surplus carried
    assert banked_out(quota, 2) == 0.0                        # behind banks nothing


# --------------------------------------------------------------------------- #
#  Rollover                                                                     #
# --------------------------------------------------------------------------- #
def _rollover_state(current_week):
    return {
        "config": {
            "players": ["A", "B"],
            "effort_scale": {"XS": 2, "S": 3, "M": 4, "L": 5},
        },
        "current_week": current_week,
        "tasks": _pool(),  # quota = 3.5
        "phi_cursor": 0.42,
        "history": [
            {"week": current_week, "task_id": "t1", "player": "A", "effort": 4, "ick": 1},
            {"week": current_week, "task_id": "t2", "player": "A", "effort": 3, "ick": 0},
        ],
        "assignments": {current_week: {"t1": {"player": "A", "status": "done"}}},
        "banked": {"A": 0.0, "B": 0.0},
        "ledger": {},
    }


def test_rollover_when_week_is_stale():
    today = date(2026, 6, 1)  # 2026-W23
    state = _rollover_state("2026-W22")  # one week behind → a single rollover
    rolled = maybe_rollover(state, today=today)

    assert rolled == "2026-W23"
    assert state["current_week"] == "2026-W23"
    # A did 7 effort vs quota 3.5 -> surplus 3.5 carried, streak advances
    assert state["banked"]["A"] == pytest.approx(3.5)
    assert state["ledger"]["A"]["weeks_at_quota"] == 1
    # B did nothing -> no bank, streak stays 0
    assert state["banked"]["B"] == 0.0
    assert state["ledger"]["B"]["weeks_at_quota"] == 0
    # history + φ-cursor are preserved across the boundary
    assert len(state["history"]) == 2
    assert state["phi_cursor"] == 0.42


def test_rollover_accounts_each_skipped_week():
    """A multi-week gap (app sat idle) is walked week-by-week, not collapsed:
    the closing week's surplus head-starts the first idle week, then the bank
    drains and the streak breaks once it's spent (SPEC §4.2, §13)."""
    today = date(2026, 6, 1)  # 2026-W23
    state = _rollover_state("2026-W20")  # three weeks behind: W20 done, W21+W22 idle
    rolled = maybe_rollover(state, today=today)

    assert rolled == "2026-W23"
    # W20 surplus 3.5 covers W21's quota, then W22 has nothing left → bank empty
    assert state["banked"]["A"] == 0.0
    # at-quota W20, carried-covered W21, missed W22 → streak broken
    assert state["ledger"]["A"]["weeks_at_quota"] == 0
    assert state["phi_cursor"] == 0.42  # φ-stream untouched by any number of skips


def test_rollover_at_exactly_quota_counts_as_met():
    """Boundary: spent == quota (not strictly greater) must count as meeting the
    fair share — the streak advances and nothing spills into the bank."""
    state = {
        "config": {"players": ["A"], "effort_scale": {"XS": 2, "S": 3, "M": 4, "L": 5}},
        "current_week": "2026-W22",  # one week behind 'today' → single rollover
        # one M task on the wheel → quota = 4 / 1 player = exactly 4
        "tasks": [{"id": "t1", "kind": "floors", "effort": "M",
                   "freq": "weekly", "in_play": True, "on_wheel": True}],
        "phi_cursor": 0.0,
        "history": [{"week": "2026-W22", "task_id": "t1", "player": "A", "effort": 4, "ick": 0}],
        "assignments": {},
        "banked": {"A": 0.0},
        "ledger": {},
    }
    maybe_rollover(state, today=date(2026, 6, 1))
    assert state["ledger"]["A"]["weeks_at_quota"] == 1  # exactly-met still counts
    assert state["banked"]["A"] == 0.0                  # met, but no surplus


def test_daily_dedup_is_per_utc_day():
    """The /api/daily idempotency predicate: same task same day → already logged;
    the *next* day the same task logs again (the branch a clock-frozen API test
    can't reach)."""
    entries = [{"task_id": "wipe_counters", "at": "2026-06-01T09:00:00+00:00"}]
    assert daily_already_logged(entries, "wipe_counters", "2026-06-01") is True
    assert daily_already_logged(entries, "wipe_counters", "2026-06-02") is False  # new day
    assert daily_already_logged(entries, "load_dishwasher", "2026-06-01") is False  # other task


def test_no_rollover_when_week_is_current():
    today = date(2026, 6, 1)  # 2026-W23
    state = _rollover_state("2026-W23")
    before = json.dumps(state, sort_keys=True)
    assert maybe_rollover(state, today=today) is None
    assert json.dumps(state, sort_keys=True) == before  # untouched


def test_rollover_streak_resets_after_a_miss():
    state = _rollover_state("2026-W20")
    state["ledger"] = {"A": {"weeks_at_quota": 4, "lifetime_effort": 0,
                             "lifetime_ick": 0, "daily_taps": 0}}
    # wipe A's completions so they miss quota this week
    state["history"] = []
    maybe_rollover(state, today=date(2026, 6, 1))
    assert state["ledger"]["A"]["weeks_at_quota"] == 0  # streak broken

"""ISO-week helpers + fair-share/credit/ick maths + week rollover (SPEC §4.2, §13).

Effort is the only currency here. Daily/off-wheel taps never appear — they carry
no effort and live in their own counter (``daily_log``). "In-play tasks" for the
quota means the *wheel* pool (``in_play AND on_wheel``): those are the only tasks
that mint effort into ``history`` and the piles.

Pure functions + in-place ``state`` transforms; no file I/O, no clock except the
explicit ``today`` argument so rollover is testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime

DEFAULT_EFFORT_SCALE = {"XS": 2, "S": 3, "M": 4, "L": 5}


# --------------------------------------------------------------------------- #
#  ISO week labels                                                            #
# --------------------------------------------------------------------------- #
def iso_week(d: date) -> str:
    """Calendar date -> ``"2026-W23"`` (ISO-8601 week, Monday-based)."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def current_iso_week(today: date | None = None) -> str:
    """Today's ISO-week label. ISO weeks start Monday, matching ``week_start``."""
    return iso_week(today or datetime.now().date())


def week_index(week_label: str) -> int:
    """``"2026-W23"`` -> a monotonic integer so the engine can diff weeks for
    cooldown. Uses the Monday-ordinal so the count is exact across year
    boundaries (where ISO years are 52 *or* 53 weeks long)."""
    year, w = week_label.split("-W")
    return date.fromisocalendar(int(year), int(w), 1).toordinal() // 7


# --------------------------------------------------------------------------- #
#  Effort resolution + the wheel pool                                          #
# --------------------------------------------------------------------------- #
def resolve_effort(token, scale: Mapping[str, int] = DEFAULT_EFFORT_SCALE) -> int:
    """Map a friendly effort token (``"M"``) to points (``4``).

    Already-resolved integers pass straight through so history rows and tasks can
    share this helper.
    """
    if isinstance(token, (int, float)):
        return int(token)
    return int(scale[token])


def wheel_tasks(tasks: Iterable[Mapping]) -> list[dict]:
    """The effort-bearing pool: in-play AND on-wheel (SPEC §9)."""
    return [t for t in tasks if t.get("in_play") and t.get("on_wheel")]


def weekly_quota(
    tasks: Iterable[Mapping],
    players: Iterable[str],
    scale: Mapping[str, int] = DEFAULT_EFFORT_SCALE,
) -> float:
    """``sum(effort of in-play wheel tasks) / P`` — per person, in effort points."""
    player_count = max(1, len(list(players)))
    total = sum(resolve_effort(t["effort"], scale) for t in wheel_tasks(tasks))
    return total / player_count


# --------------------------------------------------------------------------- #
#  Per-week, per-player tallies (read straight off the append-only history)    #
# --------------------------------------------------------------------------- #
def spent_effort(history: Iterable[Mapping], week: str, player: str) -> int:
    """Effort points ``player`` completed in ``week`` (history stores resolved ints)."""
    return sum(
        int(row["effort"])
        for row in history
        if row["week"] == week and row["player"] == player
    )


def spent_ick(history: Iterable[Mapping], week: str, player: str) -> int:
    """Ick points ``player`` absorbed in ``week`` — the §4.3 even-share numerator."""
    return sum(
        int(row["ick"])
        for row in history
        if row["week"] == week and row["player"] == player
    )


def jobs_done(history: Iterable[Mapping], week: str, player: str) -> int:
    return sum(1 for row in history if row["week"] == week and row["player"] == player)


def credit_remaining(quota: float, spent: float, banked_in: float = 0.0) -> float:
    """``quota - (spent + banked head-start)``. +ve still owes, 0 done, -ve ahead."""
    return quota - (spent + banked_in)


def banked_out(quota: float, spent: float, banked_in: float = 0.0) -> float:
    """Surplus effort carried into next week (SPEC §4.2)."""
    return max(0.0, (spent + banked_in) - quota)


def daily_already_logged(entries: list[dict], task_id: str, day: str) -> bool:
    """The ``/api/daily`` idempotency predicate: has this off-wheel task already
    been tapped on this (UTC ``YYYY-MM-DD``) day? Factored out so the cross-day
    re-log — which a clock-frozen API test can't reach — is unit-testable."""
    return any(e["task_id"] == task_id and e.get("at", "")[:10] == day for e in entries)


# --------------------------------------------------------------------------- #
#  Rollover                                                                    #
# --------------------------------------------------------------------------- #
def maybe_rollover(state: dict, today: date | None = None) -> str | None:
    """Lazily close out stale week(s) if ``current_week`` is behind (SPEC §6, §13).

    Walks **every** week from the stale ``current_week`` up to the real one, so a
    multi-week gap (the app simply sat idle) is accounted week-by-week instead of
    collapsed into one: the closing week credits its real effort, and each skipped
    week in between counts as a zero-effort week — banked surplus drains by the
    quota and the consecutive at-quota streak resets. Carries banked forward,
    prunes the now-closed ``daily_log`` scratch, repoints ``current_week``, and
    leaves ``history``, ``ledger``, ``phi_cursor`` and past ``assignments``
    untouched — the φ-stream runs continuously across weeks. Returns the new week
    label if it rolled, else ``None``. Mutates ``state`` in place.
    """
    now_week = current_iso_week(today)
    closing = state.get("current_week")
    if closing == now_week:
        return None

    config = state.get("config", {})
    players = config.get("players", [])
    scale = config.get("effort_scale", DEFAULT_EFFORT_SCALE)
    quota = weekly_quota(state.get("tasks", []), players, scale)
    history = state.get("history", [])
    banked = state.setdefault("banked", {})
    ledger = state.setdefault("ledger", {})

    weeks_elapsed = max(1, week_index(now_week) - week_index(closing))
    for player in players:
        carried = float(banked.get(player, 0.0))
        row = ledger.setdefault(
            player,
            {"lifetime_effort": 0, "lifetime_ick": 0, "weeks_at_quota": 0, "daily_taps": 0},
        )
        for i in range(weeks_elapsed):
            # only the first (closing) week saw activity; intervening weeks are empty
            spent = spent_effort(history, closing, player) if i == 0 else 0.0
            if (spent + carried) >= quota and quota > 0:
                row["weeks_at_quota"] = row.get("weeks_at_quota", 0) + 1
            else:
                row["weeks_at_quota"] = 0
            carried = banked_out(quota, spent, carried)
        banked[player] = carried

    # daily_log is per-week scratch (lifetime tallies live in the ledger) — drop
    # the closed weeks so it can't grow without bound.
    state["daily_log"] = {now_week: state.get("daily_log", {}).get(now_week, {})}
    state["banked"] = banked
    state["current_week"] = now_week
    return now_week

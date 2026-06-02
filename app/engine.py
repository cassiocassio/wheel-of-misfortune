"""The fairness + randomness engine (SPEC §4) — the heart of Wheel of Misfortune.

Pure functions only: no I/O, no clock, no global state. Everything here is
deterministic given its arguments so it can be exhaustively unit-tested.

Two quantities, kept strictly separate (SPEC model note / CLAUDE invariants):
  * EFFORT is the currency — accumulated, balanced, drawn as pile height. It is
    computed elsewhere (week.py) from history; the engine does not touch it.
  * ICK is a spread-only constraint. It appears in this module in exactly one
    place: ``ick_balance_factor``. It is never summed into a score.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

PHI_ALPHA = 0.6180339887498949  # 1/φ — the golden-ratio additive recurrence step


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def cooldown_factor(
    weeks_since: int | None,
    cooldown_weeks: int = 3,
    floor: float = 0.05,
) -> float:
    """The "I did that last week" killer (SPEC §4.3).

    ``weeks_since`` is how many whole weeks ago this player last completed this
    task, or ``None`` if they never have. Recovers linearly to full eligibility
    after ``cooldown_weeks``; never drops below ``floor`` so a tiny pool can't
    deadlock.
    """
    if weeks_since is None:
        return 1.0
    return clamp(weeks_since / cooldown_weeks, floor, 1.0)


def ick_balance_factor(
    task_ick: int,
    ick_spent_player: float,
    even_ick: float,
    ick_sensitivity: float = 0.5,
) -> float:
    """The *only* role ick plays (SPEC §4.3): spread grim jobs.

    For a grim task (ick > 0), down-weight it for a player who has already done
    more than the even share of grim work this week. Neutral tasks are untouched.
    """
    if task_ick <= 0:
        return 1.0
    excess = max(0.0, ick_spent_player - even_ick)
    return 1.0 / (1.0 + excess * ick_sensitivity)


def task_weight(
    task: Mapping,
    *,
    weeks_since: int | None,
    ick_spent_player: float,
    even_ick: float,
    cooldown_weeks: int = 3,
    floor: float = 0.05,
    ick_sensitivity: float = 0.5,
) -> float:
    """weight(t, P) = base * cooldown_factor * ick_balance_factor (SPEC §4.3)."""
    base = 1.0
    cd = cooldown_factor(weeks_since, cooldown_weeks, floor)
    ick = ick_balance_factor(
        int(task.get("ick", 0)), ick_spent_player, even_ick, ick_sensitivity
    )
    return base * cd * ick


def build_weights(
    pool: Iterable[Mapping],
    player: str,
    *,
    last_done_index: Mapping[str, Mapping[str, int]],
    ick_spent: Mapping[str, float],
    current_week_index: int,
    cooldown_weeks: int = 3,
    floor: float = 0.05,
    ick_sensitivity: float = 0.5,
) -> dict[str, float]:
    """Weight every task in ``pool`` for ``player``.

    ``pool``             — unclaimed, in-play, on-wheel task dicts.
    ``last_done_index``  — task_id -> {player -> week_index of last completion}.
    ``ick_spent``        — player -> ick points done this week (all players, for
                           the even-share denominator).
    """
    players_with_ick = max(1, len(ick_spent))
    even_ick = sum(ick_spent.values()) / players_with_ick
    my_ick = float(ick_spent.get(player, 0))

    weights: dict[str, float] = {}
    for task in pool:
        task_id = task["id"]
        last = last_done_index.get(task_id, {}).get(player)
        weeks_since = None if last is None else current_week_index - last
        weights[task_id] = task_weight(
            task,
            weeks_since=weeks_since,
            ick_spent_player=my_ick,
            even_ick=even_ick,
            cooldown_weeks=cooldown_weeks,
            floor=floor,
            ick_sensitivity=ick_sensitivity,
        )
    return weights


def draw(
    weights: Mapping[str, float],
    phi_cursor: float,
    phi_alpha: float = PHI_ALPHA,
    exclude: str | None = None,
) -> tuple[str | None, float]:
    """Golden-ratio low-discrepancy draw (SPEC §4.4).

    Advances the cursor **exactly once per call** (so a spin advances it once and
    a reroll advances it once more), then walks the weighted CDF in stable sorted
    order. ``exclude`` drops one task_id from this draw (used by reroll so it can't
    hand back the just-rejected task). Returns ``(task_id, new_cursor)``;
    ``task_id`` is ``None`` only when there is nothing to choose from.
    """
    items = {k: v for k, v in weights.items() if k != exclude}
    phi_cursor = (phi_cursor + phi_alpha) % 1.0

    keys = sorted(items)
    if not keys:
        return None, phi_cursor

    total = sum(items.values())
    if total <= 0:
        # All weights zero (shouldn't happen — FLOOR prevents it) — degrade to a
        # deterministic pick rather than dividing by zero.
        return keys[-1], phi_cursor

    target = phi_cursor * total
    acc = 0.0
    for task_id in keys:
        acc += items[task_id]
        if target < acc:
            return task_id, phi_cursor
    return keys[-1], phi_cursor  # float-safety fallback

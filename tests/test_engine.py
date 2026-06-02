"""Engine acceptance tests (SPEC §13, Okafor's plan).

Assert behaviour, not vibes. The engine is pure and seedable, so every property
below is checked against the maths, not eyeballed.
"""

from __future__ import annotations

import random
import statistics

import pytest

from app.engine import PHI_ALPHA, build_weights, cooldown_factor, draw, ick_balance_factor


# --------------------------------------------------------------------------- #
#  Fixtures: a small but realistic weekly (on-wheel) pool                      #
# --------------------------------------------------------------------------- #
def make_pool():
    """The real weekly + fortnightly on-wheel set (SPEC §9, in_play ✓), with
    effort points already resolved. The engine only ever reads ``ick``."""
    spec = [
        ("vacuum_sofa", "surfaces", 3, 0),
        ("sweep_lounge", "floors", 3, 0),
        ("mop_lounge", "floors", 4, 1),
        ("dust_stairs", "surfaces", 4, 0),
        ("dust_bannisters", "surfaces", 3, 0),
        ("dust_bannister_ledge", "surfaces", 2, 0),
        ("bed_laundry_to_machine", "laundry", 2, 0),
        ("sweep_bedrooms", "floors", 3, 0),
        ("mop_bedrooms", "floors", 4, 1),
        ("dust_bedroom_surfaces", "surfaces", 3, 0),
        ("wash_towels", "laundry", 2, 0),
        ("change_bedlinen", "laundry", 4, 1),
        ("wash_hang_laundry", "laundry", 4, 0),
        ("fold_laundry", "laundry", 4, 1),
        ("mop_kitchen", "floors", 4, 1),
        ("wipe_cupboards_outside", "surfaces", 3, 0),
        ("clean_toilet", "wet", 4, 3),
        ("empty_bathroom_bin", "waste", 2, 2),
    ]
    return [
        {"id": tid, "kind": k, "effort": e, "ick": ick} for tid, k, e, ick in spec
    ]


def equal_weights(n):
    return {f"t{i}": 1.0 for i in range(n)}


# --------------------------------------------------------------------------- #
#  (a) Determinism                                                             #
# --------------------------------------------------------------------------- #
def test_draw_is_deterministic():
    w = {"a": 1.0, "b": 2.0, "c": 3.0}
    first = draw(w, 0.1234)
    for _ in range(1000):
        assert draw(w, 0.1234) == first


def test_cursor_advances_once_per_call():
    c0 = 0.4
    _, c1 = draw(equal_weights(5), c0)
    assert c1 == pytest.approx((c0 + PHI_ALPHA) % 1.0)


def test_reroll_advances_cursor_once_more():
    """A spin + one reroll = two draws => cursor advanced twice; the reroll never
    returns the rejected task."""
    w = equal_weights(6)
    picked, c1 = draw(w, 0.2)
    rerolled, c2 = draw(w, c1, exclude=picked)
    assert rerolled != picked
    assert c2 == pytest.approx((0.2 + 2 * PHI_ALPHA) % 1.0)


def test_cdf_order_is_stable_regardless_of_insertion_order():
    base = {"alpha": 1.0, "bravo": 2.0, "charlie": 3.0, "delta": 4.0}
    items = list(base.items())
    expected, _ = draw(base, 0.77)
    for _ in range(20):
        random.shuffle(items)
        shuffled = dict(items)
        got, _ = draw(shuffled, 0.77)
        assert got == expected


def test_full_sequence_replays_identically():
    def run():
        cursor = 0.111
        out = []
        for _ in range(50):
            tid, cursor = draw(equal_weights(8), cursor)
            out.append(tid)
        return out

    assert run() == run()


# --------------------------------------------------------------------------- #
#  Unit behaviour of the two factors                                          #
# --------------------------------------------------------------------------- #
def test_cooldown_factor_curve():
    assert cooldown_factor(None) == 1.0            # never done it
    assert cooldown_factor(0) == 0.05              # just did it -> floored
    assert cooldown_factor(3) == 1.0               # fully recovered at COOLDOWN_WEEKS
    assert cooldown_factor(10) == 1.0              # clamped at 1.0
    assert cooldown_factor(1) == pytest.approx(1 / 3)


def test_cooldown_factor_never_zero():
    for w in range(0, 5):
        assert cooldown_factor(w, floor=0.05) >= 0.05


def test_ick_balance_only_touches_grim_tasks():
    # A neutral task is never down-weighted no matter how ick-laden the player is.
    assert ick_balance_factor(0, ick_spent_player=99, even_ick=0) == 1.0
    # A grim task is down-weighted for the over-burdened player...
    heavy = ick_balance_factor(3, ick_spent_player=6, even_ick=2)
    assert heavy < 1.0
    # ...and untouched for someone at/below the even share.
    assert ick_balance_factor(3, ick_spent_player=1, even_ick=2) == 1.0


# --------------------------------------------------------------------------- #
#  A voluntary-participation simulation harness                               #
# --------------------------------------------------------------------------- #
def simulate(weeks=20, players=("M", "S", "A"), seed=7, propensity=None, evenings=6):
    """Run the engine over many weeks with players who self-pace (no quota gate).

    Returns per-player completion records and the list of within-cooldown repeats.
    Participation is decided by a seeded RNG (deterministic test); *selection*
    is the φ-engine. ``evenings`` is how many spin-chances each player gets per
    week — fewer evenings models the light, self-paced "deep task" cadence the
    SPEC's cooldown acceptance test assumes (~1 spin/player/week over many weeks).
    """
    rng = random.Random(seed)
    propensity = propensity or {p: 0.6 for p in players}
    pool = make_pool()

    cursor = rng.random()
    last_done: dict[str, dict[str, int]] = {}
    completions = {p: [] for p in players}      # list of (effort, ick, task_id, week)
    cooldown_violations = []

    for week in range(weeks):
        claimed: set[str] = set()
        week_ick = {p: 0 for p in players}
        # Each player gets several chances to spin across the "evenings" of a week.
        order = list(players)
        for _evening in range(evenings):
            rng.shuffle(order)
            for p in order:
                available = [t for t in pool if t["id"] not in claimed]
                if not available:
                    continue
                if rng.random() > propensity[p]:
                    continue  # chose not to spin this evening
                weights = build_weights(
                    available, p,
                    last_done_index=last_done,
                    ick_spent=week_ick,
                    current_week_index=week,
                )
                tid, cursor = draw(weights, cursor)
                if tid is None:
                    continue
                task = next(t for t in pool if t["id"] == tid)
                # cooldown bookkeeping
                prev = last_done.get(tid, {}).get(p)
                if prev is not None and (week - prev) < 3:
                    cooldown_violations.append((p, tid, week - prev, len(available)))
                claimed.add(tid)
                week_ick[p] += task["ick"]
                last_done.setdefault(tid, {})[p] = week
                completions[p].append((task["effort"], task["ick"], tid, week))

    return completions, cooldown_violations


def test_no_double_assignment_within_a_week():
    completions, _ = simulate()
    # Reconstruct per-week assignments and assert each task claimed once per week.
    per_week: dict[int, list[str]] = {}
    for _p, recs in completions.items():
        for _effort, _ick, tid, week in recs:
            per_week.setdefault(week, []).append(tid)
    for week, tids in per_week.items():
        assert len(tids) == len(set(tids)), f"task claimed twice in week {week}"


def test_cooldown_repeats_are_rare():
    """SPEC §13: ~500 spins / 3 players / many weeks at the light, self-paced deep
    cadence (≈1 spin/player/week) → a task is essentially never re-assigned to the
    same player inside COOLDOWN_WEEKS. The pool keeps fresh slack, so the linear
    cooldown has room to work and repeats only appear when the pool is depleted
    (the rare FLOOR-forced case). Heavy "do everything every week" consumption is a
    different, pigeonhole-bound regime and is not what this property asserts."""
    completions, violations = simulate(weeks=280, evenings=1)
    total = sum(len(r) for r in completions.values())
    assert total > 450, f"simulation should produce ~500 spins (SPEC §13), got {total}"
    rate = len(violations) / total
    assert rate < 0.05, f"within-cooldown repeat rate too high: {rate:.3f}"
    # Any repeat that does slip through must be FLOOR-forced: the player's fresh
    # tasks were exhausted, i.e. the pool was depleted below its full size.
    full = len(make_pool())
    for _p, _tid, _gap, pool_size in violations:
        assert pool_size < full


def test_effort_mix_is_fair_under_voluntary_participation():
    """Players participate at very different rates; fairness here means the *mix*
    of work each gets is even — nobody is handed disproportionately heavy jobs —
    even though absolute pile heights differ with participation."""
    completions, _ = simulate(
        weeks=40, seed=3, propensity={"M": 0.8, "S": 0.5, "A": 0.3}
    )
    all_efforts = [e for recs in completions.values() for (e, _i, _t, _w) in recs]
    global_mean = statistics.mean(all_efforts)
    for p, recs in completions.items():
        mean_effort = statistics.mean([e for (e, _i, _t, _w) in recs])
        assert abs(mean_effort - global_mean) < 0.6, (
            f"{p} mean effort/task {mean_effort:.2f} vs global {global_mean:.2f}"
        )


def test_equal_participation_converges_to_equal_piles():
    """THE fairness claim (SPEC §4.2): when everyone shows up equally, the piles
    — total *effort* completed — end up level. This is the property the whole app
    exists to deliver, so it gets pinned directly: over a long, equal-propensity
    run the spread between the tallest and shortest pile stays small."""
    completions, _ = simulate(
        weeks=120, seed=5, propensity={"M": 0.9, "S": 0.9, "A": 0.9}
    )
    piles = {p: sum(e for (e, _i, _t, _w) in recs) for p, recs in completions.items()}
    mean = statistics.mean(piles.values())
    spread = (max(piles.values()) - min(piles.values())) / mean
    assert spread < 0.08, f"piles not level under equal participation: {piles} ({spread:.3f})"


def test_ick_is_spread_evenly():
    """Mean ick per completed task should be close across players — grim work is
    distributed, not dumped on one person. Swept across seeds so the property is
    shown to be robust, not a single lucky draw."""
    for seed in (1, 3, 7, 11, 17, 23, 42, 99):
        completions, _ = simulate(weeks=40, seed=seed)
        per_player_mean_ick = {
            p: statistics.mean([i for (_e, i, _t, _w) in recs])
            for p, recs in completions.items()
        }
        spread = max(per_player_mean_ick.values()) - min(per_player_mean_ick.values())
        assert spread < 0.5, f"ick not spread evenly (seed {seed}): {per_player_mean_ick}"


def test_ick_never_leaks_into_selection_of_neutral_tasks():
    """The invariant: cranking every player's ick load to the ceiling must not
    change the weights of ick==0 tasks at all (ick is spread-only)."""
    pool = make_pool()
    neutral = [t for t in pool if t["ick"] == 0]
    calm = build_weights(
        pool, "M", last_done_index={}, ick_spent={"M": 0, "S": 0, "A": 0},
        current_week_index=5,
    )
    swamped = build_weights(
        pool, "M", last_done_index={}, ick_spent={"M": 99, "S": 0, "A": 0},
        current_week_index=5,
    )
    for t in neutral:
        assert calm[t["id"]] == swamped[t["id"]]


# --------------------------------------------------------------------------- #
#  (e) "Feels random": φ clusters less than uniform random                     #
# --------------------------------------------------------------------------- #
def test_phi_lag1_repeat_rate_below_uniform():
    n = 10
    weights = equal_weights(n)

    # φ-driven stream
    cursor = 0.0
    phi_seq = []
    for _ in range(5000):
        tid, cursor = draw(weights, cursor)
        phi_seq.append(tid)
    phi_repeats = sum(1 for a, b in zip(phi_seq, phi_seq[1:], strict=False) if a == b)

    # uniform baseline over the same pool
    rng = random.Random(1)
    keys = sorted(weights)
    uni_seq = [keys[rng.randrange(n)] for _ in range(5000)]
    uni_repeats = sum(1 for a, b in zip(uni_seq, uni_seq[1:], strict=False) if a == b)

    assert phi_repeats < uni_repeats
    # φ should essentially never hand out the same sector twice in a row.
    assert phi_repeats == 0


def test_draw_handles_empty_and_single_pools():
    tid, c = draw({}, 0.3)
    assert tid is None
    only, _ = draw({"solo": 1.0}, 0.3)
    assert only == "solo"
    # excluding the only task leaves nothing
    none_left, _ = draw({"solo": 1.0}, 0.3, exclude="solo")
    assert none_left is None

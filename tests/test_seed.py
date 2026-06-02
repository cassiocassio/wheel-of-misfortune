"""Seed loader tests (SPEC §5.1, §9, §13).

The headline acceptance criterion: a malformed ``family.yaml`` yields a
line-numbered, human-readable error — never a raw stack trace. We also pin the
on-wheel / off-wheel split and the in-play defaults straight off the §9 dataset.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.seed import SeedError, build_initial_state, parse_seed, reseed, slugify

EXAMPLE = Path(__file__).resolve().parent.parent / "family.example.yaml"


def by_name(tasks, name):
    return next(t for t in tasks if t["name"] == name)


# --------------------------------------------------------------------------- #
#  The shipped example file parses and matches §9                              #
# --------------------------------------------------------------------------- #
def test_example_file_parses():
    parsed = parse_seed(EXAMPLE.read_text())
    assert parsed["config"]["players"] == ["Martin", "Sarah", "Alice"]
    assert set(parsed["config"]["kinds"]) == {
        "surfaces", "floors", "laundry", "waste", "wet"
    }
    assert len(parsed["tasks"]) == 29


def test_wheel_vs_daily_split_matches_spec():
    tasks = parse_seed(EXAMPLE.read_text())["tasks"]
    on_wheel = [t for t in tasks if t["on_wheel"]]
    off_wheel = [t for t in tasks if not t["on_wheel"]]
    # 7 off-wheel: 3 daily + 4 "2-3 weekly"; the remaining 22 are deep wheel tasks.
    assert len(off_wheel) == 7
    assert len(on_wheel) == 22
    # daily and 2-3/week are never on the wheel
    for t in off_wheel:
        assert t["freq"] == "daily" or t["freq"].replace(" ", "").startswith("2-3")


def test_in_play_defaults_pause_monthly_and_quarterly():
    tasks = parse_seed(EXAMPLE.read_text())["tasks"]
    fridge = by_name(tasks, "Clean fridge")  # monthly
    assert fridge["in_play"] is False
    toilet = by_name(tasks, "Disinfect, de-limescale & clean toilet")  # weekly
    assert toilet["in_play"] is True
    assert toilet["on_wheel"] is True
    assert toilet["effort"] == "M"  # token preserved on the task (resolved in history)
    assert toilet["ick"] == 3


def test_ids_are_slugged_and_unique():
    tasks = parse_seed(EXAMPLE.read_text())["tasks"]
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids))
    assert by_name(tasks, "Vacuum sofa")["id"] == "vacuum_sofa"


def test_slugify():
    assert slugify("Vacuum sofa") == "vacuum_sofa"
    assert slugify("Wash & hang laundry (30°)") == "wash_hang_laundry_30"
    assert slugify("!!!") == "task"


# --------------------------------------------------------------------------- #
#  Friendly, line-numbered validation (the adoption criterion)                 #
# --------------------------------------------------------------------------- #
def _seed_with_chore(chore_line: str) -> str:
    """A minimal valid seed whose single chore sits on a known line number."""
    return (
        "players:\n"            # line 1
        "  - { name: Sam }\n"   # line 2
        "kinds:\n"              # line 3
        "  wet: { label: Wet }\n"  # line 4
        "chores:\n"            # line 5
        f"  - {chore_line}\n"  # line 6  <-- the chore under test
    )


def test_unknown_kind_is_friendly_and_line_numbered():
    text = _seed_with_chore("{ name: Loo, kind: bins, freq: weekly, effort: M }")
    with pytest.raises(SeedError) as exc:
        parse_seed(text)
    msg = str(exc.value)
    assert "line 6" in msg
    assert "bins" in msg
    assert "wet" in msg  # suggests the valid kinds


def test_unknown_effort_is_friendly():
    text = _seed_with_chore("{ name: Loo, kind: wet, freq: weekly, effort: HUGE }")
    with pytest.raises(SeedError) as exc:
        parse_seed(text)
    assert "line 6" in str(exc.value)
    assert "effort" in str(exc.value)


def test_bad_ick_is_friendly():
    text = _seed_with_chore("{ name: Loo, kind: wet, freq: weekly, effort: M, ick: 9 }")
    with pytest.raises(SeedError) as exc:
        parse_seed(text)
    assert "line 6" in str(exc.value)
    assert "ick" in str(exc.value)


def test_unknown_freq_is_friendly():
    text = _seed_with_chore("{ name: Loo, kind: wet, freq: hourly, effort: M }")
    with pytest.raises(SeedError) as exc:
        parse_seed(text)
    assert "line 6" in str(exc.value)
    assert "hourly" in str(exc.value)


def test_missing_name_is_friendly():
    text = _seed_with_chore("{ kind: wet, freq: weekly, effort: M }")
    with pytest.raises(SeedError) as exc:
        parse_seed(text)
    assert "line 6" in str(exc.value) and "name" in str(exc.value)


def test_missing_players_is_friendly():
    with pytest.raises(SeedError) as exc:
        parse_seed("chores:\n  - { name: Loo, kind: wet, freq: weekly, effort: M }\n")
    assert "players" in str(exc.value)


def test_missing_chores_is_friendly():
    with pytest.raises(SeedError) as exc:
        parse_seed("players:\n  - { name: Sam }\n")
    assert "chores" in str(exc.value)


def test_yaml_syntax_error_is_caught_not_raw():
    # A raw stack trace would be a yaml.YAMLError; we promise a SeedError instead.
    with pytest.raises(SeedError):
        parse_seed("players: [unterminated\nchores: ]\n")


# --------------------------------------------------------------------------- #
#  Initial state + reseed                                                       #
# --------------------------------------------------------------------------- #
def test_build_initial_state_shape():
    state = build_initial_state(EXAMPLE, phi_cursor=0.5, today=None)
    assert state["version"] == 1
    assert 0.0 <= state["phi_cursor"] < 1.0
    assert state["history"] == [] and state["assignments"] == {}
    assert set(state["ledger"]) == {"Martin", "Sarah", "Alice"}
    assert state["current_week"].count("-W") == 1


def test_seeded_phi_cursor_is_random_in_unit_interval():
    a = build_initial_state(EXAMPLE)["phi_cursor"]
    b = build_initial_state(EXAMPLE)["phi_cursor"]
    assert 0.0 <= a < 1.0 and 0.0 <= b < 1.0
    assert a != b  # seeded random per first run


def test_reseed_preserves_history_and_ledger():
    state = build_initial_state(EXAMPLE, phi_cursor=0.1)
    state["history"].append(
        {"week": "2026-W23", "task_id": "vacuum_sofa", "player": "Martin",
         "effort": 3, "ick": 0, "done_at": "x"}
    )
    state["ledger"]["Martin"]["lifetime_effort"] = 42
    state["phi_cursor"] = 0.777

    reseed(state, EXAMPLE)

    assert len(state["history"]) == 1                 # history untouched
    assert state["ledger"]["Martin"]["lifetime_effort"] == 42
    assert state["phi_cursor"] == 0.777               # φ-stream continues
    assert len(state["tasks"]) == 29                  # definitions rebuilt

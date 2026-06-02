"""``family.yaml`` -> ``state.json`` loader with *friendly* validation (SPEC §5.1).

The single biggest factor in non-technical adoption: when the seed is malformed,
report the **line** and a human fix ("line 47: unknown kind 'bins' — did you mean
one of surfaces/floors/laundry/waste/wet?"), never a raw stack trace.

To do that we keep YAML line numbers: a thin ``SafeLoader`` subclass stamps every
mapping with ``__line__`` (1-based), which we read during validation and strip
before the task ever reaches ``state.json``.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import yaml

from .week import DEFAULT_EFFORT_SCALE, current_iso_week

DEFAULT_KINDS = {
    "surfaces": {"label": "Surfaces & dishes", "hue": "#E0A100", "icon": "sparkle"},
    "floors": {"label": "Floors", "hue": "#1F8A8A", "icon": "broom"},
    "laundry": {"label": "Laundry", "hue": "#3D6FD6", "icon": "shirt"},
    "waste": {"label": "Waste", "hue": "#C2410C", "icon": "trash"},
    "wet": {"label": "Wet & sanitise", "hue": "#A01A58", "icon": "spray"},
}

DEFAULT_TINTS = ["#5B6B7A", "#7A8B5E", "#8A6F8E", "#6E7B8A", "#8A7A5E", "#5E8A7E"]

ON_WHEEL_FREQS = {"weekly", "fortnightly", "monthly", "quarterly"}
OFF_WHEEL_FREQS = {"daily", "2-3 weekly", "2-3/week", "2–3/week", "2-3weekly"}
PAUSED_BY_DEFAULT = {"monthly", "quarterly"}  # SPEC §9: on when due

VALID_ICK = {0, 1, 2, 3}


class SeedError(Exception):
    """A human-readable, line-numbered seed problem — safe to show a non-coder."""


# --------------------------------------------------------------------------- #
#  YAML loader that remembers line numbers                                     #
# --------------------------------------------------------------------------- #
class _LineLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _LineLoader, node):
    mapping = loader.construct_mapping(node)
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _strip_lines(obj):
    if isinstance(obj, dict):
        return {k: _strip_lines(v) for k, v in obj.items() if k != "__line__"}
    if isinstance(obj, list):
        return [_strip_lines(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    """``"Vacuum sofa"`` -> ``"vacuum_sofa"``; drops punctuation, collapses runs."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "task"


def _classify_freq(freq: str, line: int | None) -> tuple[str, bool]:
    """Return (normalised_freq, on_wheel) or raise a friendly error."""
    f = str(freq).strip().lower()
    if f in ON_WHEEL_FREQS:
        return f, True
    if f in OFF_WHEEL_FREQS or f.replace(" ", "").startswith("2-3") or "daily" in f:
        return f, False
    options = "daily / 2-3 weekly / weekly / fortnightly / monthly / quarterly"
    raise SeedError(
        f"line {line}: unknown freq '{freq}' — use one of {options}"
    )


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise SeedError(message)


# --------------------------------------------------------------------------- #
#  Parse + validate                                                            #
# --------------------------------------------------------------------------- #
def parse_seed(text: str) -> dict:
    """Parse + validate seed YAML text into ``{config, tasks}``. Raises SeedError."""
    try:
        raw = yaml.load(text, Loader=_LineLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        problem = getattr(exc, "problem", None) or "could not parse the file"
        raise SeedError(f"{where}{problem}") from None

    _require(isinstance(raw, dict), "the seed file must be a mapping (players:, chores:, …)")

    # ---- players ----
    players_raw = raw.get("players")
    _require(
        isinstance(players_raw, list) and players_raw,
        "no players found — add a 'players:' list with at least one person",
    )
    players: list[str] = []
    tints: dict[str, str] = {}
    for i, entry in enumerate(players_raw):
        _require(
            isinstance(entry, dict) and entry.get("name"),
            f"line {entry.get('__line__') if isinstance(entry, dict) else '?'}: "
            "each player needs a name, e.g. - {{ name: \"Sam\" }}",
        )
        name = str(entry["name"])
        players.append(name)
        tints[name] = str(entry.get("tint") or DEFAULT_TINTS[i % len(DEFAULT_TINTS)])

    # ---- kinds ----
    kinds_raw = raw.get("kinds")
    if kinds_raw is None:
        kinds = {k: dict(v) for k, v in DEFAULT_KINDS.items()}
    else:
        _require(isinstance(kinds_raw, dict), "'kinds:' must be a mapping of kind -> details")
        kinds = {}
        for key, val in kinds_raw.items():
            if key == "__line__":
                continue
            val = val or {}
            kinds[key] = {
                "label": val.get("label", key.title()),
                "hue": val.get("colour") or val.get("hue") or "#888888",
                "icon": val.get("icon", "dot"),
            }
    valid_kinds = sorted(kinds)

    # ---- settings ----
    settings = raw.get("settings") or {}
    effort_scale = settings.get("effort_scale") or DEFAULT_EFFORT_SCALE

    # ---- chores ----
    chores_raw = raw.get("chores")
    _require(
        isinstance(chores_raw, list) and chores_raw,
        "no chores found — add a 'chores:' list",
    )

    tasks: list[dict] = []
    seen_ids: set[str] = set()
    for chore in chores_raw:
        line = chore.get("__line__") if isinstance(chore, dict) else None
        _require(isinstance(chore, dict), f"line {line}: each chore must be a {{ ... }} entry")

        name = chore.get("name")
        _require(bool(name), f"line {line}: a chore is missing its name")

        kind = chore.get("kind")
        if kind not in kinds:
            raise SeedError(
                f"line {line}: unknown kind '{kind}' — did you mean one of "
                f"{'/'.join(valid_kinds)}?"
            )

        effort = chore.get("effort")
        if effort not in effort_scale:
            raise SeedError(
                f"line {line}: unknown effort '{effort}' — use one of "
                f"{'/'.join(effort_scale)}"
            )

        ick = chore.get("ick", 0)
        if ick not in VALID_ICK:
            raise SeedError(
                f"line {line}: ick must be 0–3 (0 fine … 3 truly grim), got '{ick}'"
            )

        freq = chore.get("freq")
        _require(bool(freq), f"line {line}: a chore is missing its freq (e.g. weekly)")
        norm_freq, derived_on_wheel = _classify_freq(freq, line)
        on_wheel = bool(chore.get("wheel", derived_on_wheel))  # explicit override

        if "in_play" in chore:
            in_play = bool(chore["in_play"])
        else:
            in_play = norm_freq not in PAUSED_BY_DEFAULT

        task_id = chore.get("id") or slugify(str(name))
        base_id = task_id
        n = 2
        while task_id in seen_ids:
            task_id = f"{base_id}_{n}"
            n += 1
        seen_ids.add(task_id)

        tasks.append(
            {
                "id": task_id,
                "room": chore.get("room", ""),
                "kind": kind,
                "name": str(name),
                "freq": norm_freq,
                "effort": effort,  # token; resolved to points via effort_scale
                "ick": int(ick),
                "in_play": in_play,
                "on_wheel": on_wheel,
            }
        )

    config = {
        "players": players,
        "player_tints": tints,
        "effort_scale": dict(effort_scale),
        "cooldown_weeks": int(settings.get("cooldown_weeks", 3)),
        "cooldown_floor": float(settings.get("cooldown_floor", 0.05)),
        "ick_sensitivity": float(settings.get("ick_sensitivity", 0.5)),
        "phi_alpha": 0.6180339887498949,
        "rerolls_per_spin": int(settings.get("rerolls_per_spin", 1)),
        "week_start": str(settings.get("week_start", "monday")),
        "kinds": kinds,
    }
    return {"config": config, "tasks": tasks}


def load_seed_file(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise SeedError(f"seed file not found: {p}")
    return parse_seed(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  Build / reseed state                                                        #
# --------------------------------------------------------------------------- #
def _empty_ledger(players) -> dict:
    return {
        p: {"lifetime_effort": 0, "lifetime_ick": 0, "weeks_at_quota": 0, "daily_taps": 0}
        for p in players
    }


def build_initial_state(
    seed_path: str | Path,
    *,
    phi_cursor: float | None = None,
    today=None,
) -> dict:
    """First-run state from a seed file (SPEC §5). φ-cursor seeds random in [0,1)."""
    parsed = load_seed_file(seed_path)
    players = parsed["config"]["players"]
    return {
        "version": 1,
        "config": parsed["config"],
        "phi_cursor": random.random() if phi_cursor is None else float(phi_cursor),
        "current_week": current_iso_week(today),
        "tasks": parsed["tasks"],
        "assignments": {},
        "history": [],
        "daily_log": {},
        "recent_spins": {},
        "banked": {p: 0.0 for p in players},
        "ledger": _empty_ledger(players),
    }


def reseed(state: dict, seed_path: str | Path) -> dict:
    """Rebuild config + task definitions from the seed, **preserving** history,
    ledger, assignments, banked and the φ-cursor (SPEC §5.1 — match on task id).
    Mutates and returns ``state``.
    """
    parsed = load_seed_file(seed_path)
    state["config"] = parsed["config"]
    state["tasks"] = parsed["tasks"]
    # top up the ledger with any newly added players, keep existing tallies
    ledger = state.setdefault("ledger", {})
    for p in parsed["config"]["players"]:
        ledger.setdefault(
            p, {"lifetime_effort": 0, "lifetime_ick": 0, "weeks_at_quota": 0, "daily_taps": 0}
        )
        state.setdefault("banked", {}).setdefault(p, 0.0)
    return state

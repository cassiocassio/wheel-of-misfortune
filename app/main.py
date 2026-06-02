"""FastAPI app: wires the §6 API to the pure engine + the guarded state store.

Single Uvicorn worker (SPEC §3). Every mutating call goes through
``store.mutate`` → one ``asyncio.Lock`` + atomic file replace, so two devices
spinning at once can never double-assign or advance φ twice. The server is the
sole outcome authority: it draws the task before the client's wheel stops.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine
from .models import SpinRequest, TaskAction
from .seed import SeedError, build_initial_state
from .state import StateStore
from .week import (
    credit_remaining,
    current_iso_week,
    daily_already_logged,
    jobs_done,
    maybe_rollover,
    resolve_effort,
    spent_effort,
    spent_ick,
    week_index,
    weekly_quota,
    wheel_tasks,
)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(os.environ.get("WHEEL_STATE", ROOT / "state.json"))
WEB_DIR = ROOT / "web"
RECENT_SPINS_CAP = 32
DAILY_SFX = ["pop", "ding", "boing", "coin", "sparkle"]

store = StateStore(STATE_PATH)


def _seed_path() -> Path:
    custom = os.environ.get("WHEEL_SEED")
    if custom:
        return Path(custom)
    family = ROOT / "family.yaml"
    return family if family.exists() else ROOT / "family.example.yaml"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not store.exists():
        store.save_sync(build_initial_state(_seed_path()))
    yield


app = FastAPI(title="Wheel of Misfortune", lifespan=lifespan)


# --------------------------------------------------------------------------- #
#  Error shape: friendly JSON, never a stack trace                             #
# --------------------------------------------------------------------------- #
@app.exception_handler(SeedError)
async def _seed_error_handler(_req, exc: SeedError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


def _fail(status: int, error: str):
    raise HTTPException(status_code=status, detail={"error": error})


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
#  State helpers (pure: operate on the in-memory state dict)                    #
# --------------------------------------------------------------------------- #
def _knobs(state: dict) -> dict:
    c = state["config"]
    return {
        "cooldown_weeks": c.get("cooldown_weeks", 3),
        "floor": c.get("cooldown_floor", 0.05),
        "ick_sensitivity": c.get("ick_sensitivity", 0.5),
        "rerolls_per_spin": c.get("rerolls_per_spin", 1),
        "scale": c.get("effort_scale", {"XS": 2, "S": 3, "M": 4, "L": 5}),
        "players": c.get("players", []),
        "phi_alpha": c.get("phi_alpha", engine.PHI_ALPHA),
    }


def _task_by_id(state: dict, task_id: str) -> dict | None:
    return next((t for t in state["tasks"] if t["id"] == task_id), None)


def _week_assignments(state: dict) -> dict:
    return state["assignments"].setdefault(state["current_week"], {})


def _available_pool(state: dict) -> list[dict]:
    """Unclaimed in-play on-wheel tasks (the candidate pool for a spin)."""
    claimed = set(_week_assignments(state))
    return [t for t in wheel_tasks(state["tasks"]) if t["id"] not in claimed]


def _last_done_index(history) -> dict[str, dict[str, int]]:
    idx: dict[str, dict[str, int]] = {}
    for row in history:
        wk = week_index(row["week"])
        per = idx.setdefault(row["task_id"], {})
        if wk > per.get(row["player"], -1):
            per[row["player"]] = wk
    return idx


def _ick_spent_map(state: dict) -> dict[str, int]:
    wk = state["current_week"]
    return {p: spent_ick(state["history"], wk, p) for p in _knobs(state)["players"]}


def _player_credit(state: dict, player: str) -> float:
    # SPEC §4.2: credit = quota − spent. Banked surplus is a *separate* head-start
    # badge, never folded back into credit (that would double-count it).
    k = _knobs(state)
    wk = state["current_week"]
    quota = weekly_quota(state["tasks"], k["players"], k["scale"])
    spent = spent_effort(state["history"], wk, player)
    return credit_remaining(quota, spent)


def _validate_player(state: dict, player: str):
    if player not in _knobs(state)["players"]:
        _fail(404, f"unknown player '{player}'")


def _draw_for(state: dict, player: str, *, exclude: str | None = None):
    """Build weights over the live pool, draw one task, persist the advanced
    φ-cursor. Returns (task_id|None, weights, breakdown-inputs)."""
    k = _knobs(state)
    pool = _available_pool(state)
    weights = engine.build_weights(
        pool,
        player,
        last_done_index=_last_done_index(state["history"]),
        ick_spent=_ick_spent_map(state),
        current_week_index=week_index(state["current_week"]),
        cooldown_weeks=k["cooldown_weeks"],
        floor=k["floor"],
        ick_sensitivity=k["ick_sensitivity"],
    )
    task_id, new_cursor = engine.draw(
        weights, state["phi_cursor"], k["phi_alpha"], exclude=exclude
    )
    state["phi_cursor"] = new_cursor
    return task_id, weights


def _breakdown(state: dict, player: str, task: dict, weight: float) -> dict:
    k = _knobs(state)
    wk = state["current_week"]
    last = _last_done_index(state["history"]).get(task["id"], {}).get(player)
    weeks_since = None if last is None else week_index(wk) - last
    ick_map = _ick_spent_map(state)
    even_ick = sum(ick_map.values()) / max(1, len(ick_map))
    return {
        "base": 1.0,
        "cooldown": engine.cooldown_factor(weeks_since, k["cooldown_weeks"], k["floor"]),
        "ick_balance": engine.ick_balance_factor(
            int(task.get("ick", 0)), ick_map.get(player, 0), even_ick, k["ick_sensitivity"]
        ),
        "weight": weight,
        "weeks_since": weeks_since,
    }


# --------------------------------------------------------------------------- #
#  Routes                                                                       #
# --------------------------------------------------------------------------- #
def _apply_rollover(state: dict) -> dict:
    maybe_rollover(state)
    return state


async def _state_rolled() -> dict:
    """Read state, escalating to a write only when the week is actually stale —
    a plain dashboard poll stays a single read, not an fsync+rewrite every time."""
    state = await store.read()
    if state.get("current_week") != current_iso_week():
        state = await store.mutate(_apply_rollover)
    return state


@app.get("/api/state")
async def get_state():
    return await _state_rolled()


@app.get("/api/dashboard")
async def get_dashboard():
    return _build_dashboard(await _state_rolled())


@app.post("/api/spin")
async def spin(req: SpinRequest):
    def fn(state):
        maybe_rollover(state)
        _validate_player(state, req.player)
        recent = state.setdefault("recent_spins", {})
        assigns = _week_assignments(state)

        # idempotent replay: same token still pending for this player
        if req.spin_token and req.spin_token in recent:
            cached = recent[req.spin_token]
            a = assigns.get(cached)
            if a and a["player"] == req.player and a["status"] == "pending":
                return {
                    "task": _task_by_id(state, cached),
                    "weight_breakdown": None,
                    "credit_remaining": _player_credit(state, req.player),
                    "idempotent": True,
                }

        task_id, weights = _draw_for(state, req.player)
        if task_id is None:
            _fail(409, "pool_empty")

        assigns[task_id] = {
            "player": req.player,
            "status": "pending",
            "spun_at": _now(),
            "accepted_at": None,
            "done_at": None,
            "rerolls_used": 0,
        }
        if req.spin_token:
            recent[req.spin_token] = task_id
            while len(recent) > RECENT_SPINS_CAP:
                recent.pop(next(iter(recent)))

        task = _task_by_id(state, task_id)
        return {
            "task": task,
            "weight_breakdown": _breakdown(state, req.player, task, weights[task_id]),
            "credit_remaining": _player_credit(state, req.player),
        }

    return await store.mutate(fn)


@app.post("/api/claim")
async def claim(req: TaskAction):
    """Take a *specific* on-wheel chore without spinning — to log one you've
    already done after the fact, or to deliberately pick one when you're not in
    the mood for random. It is **not** a draw, so φ is untouched and there is no
    reroll; otherwise it joins the same pending → done → pile flow as a spun task,
    so a claimed weekly chore credits effort exactly like any other."""
    def fn(state):
        maybe_rollover(state)
        _validate_player(state, req.player)
        task = _task_by_id(state, req.task_id)
        if task is None:
            _fail(404, f"unknown task '{req.task_id}'")
        if not (task.get("in_play") and task.get("on_wheel")):
            _fail(400, "that's not a wheel chore — log it with the daily button")
        assigns = _week_assignments(state)
        if req.task_id in assigns:
            _fail(409, "that chore is already taken this week")

        assigns[req.task_id] = {
            "player": req.player,
            "status": "pending",
            "spun_at": _now(),
            "accepted_at": None,
            "done_at": None,
            "rerolls_used": 0,
            "claimed": True,
        }
        return {
            "task": task,
            "credit_remaining": _player_credit(state, req.player),
            "claimed": True,
        }

    return await store.mutate(fn)


@app.post("/api/reroll")
async def reroll(req: TaskAction):
    def fn(state):
        maybe_rollover(state)
        _validate_player(state, req.player)
        assigns = _week_assignments(state)
        a = assigns.get(req.task_id)
        if not a or a["player"] != req.player:
            _fail(404, "no such pending task for this player")
        if a["status"] != "pending":
            _fail(403, "can only reroll a task you haven't accepted yet")
        used = a.get("rerolls_used", 0)
        if used >= _knobs(state)["rerolls_per_spin"]:
            _fail(403, "no rerolls left — this one's yours")

        # the rejected task returns to the pool for everyone else…
        del assigns[req.task_id]
        # …and the reroll is a fresh φ draw that excludes it
        new_id, weights = _draw_for(state, req.player, exclude=req.task_id)
        if new_id is None:
            # nothing else to offer: hand the original back, reroll consumed
            assigns[req.task_id] = {**a, "rerolls_used": used + 1}
            _fail(409, "pool_empty")

        assigns[new_id] = {
            "player": req.player,
            "status": "pending",
            "spun_at": _now(),
            "accepted_at": None,
            "done_at": None,
            "rerolls_used": used + 1,
        }
        task = _task_by_id(state, new_id)
        return {
            "task": task,
            "weight_breakdown": _breakdown(state, req.player, task, weights[new_id]),
        }

    return await store.mutate(fn)


@app.post("/api/accept")
async def accept(req: TaskAction):
    def fn(state):
        maybe_rollover(state)
        a = _week_assignments(state).get(req.task_id)
        if not a or a["player"] != req.player:
            _fail(404, "no such task assigned to this player")
        if a["status"] != "pending":
            _fail(409, f"task is already {a['status']}")
        a["status"] = "accepted"
        a["accepted_at"] = _now()
        return {"ok": True}

    return await store.mutate(fn)


@app.post("/api/done")
async def done(req: TaskAction):
    def fn(state):
        maybe_rollover(state)
        a = _week_assignments(state).get(req.task_id)
        if not a or a["player"] != req.player:
            _fail(404, "no such task assigned to this player")
        # "pending" is allowed too: the UI lets you mark a freshly-spun task done
        # without a separate accept tap. Only a *completed* task is refused.
        if a["status"] not in ("accepted", "pending"):
            _fail(409, f"task is already {a['status']}")

        task = _task_by_id(state, req.task_id)
        eff = resolve_effort(task["effort"], _knobs(state)["scale"])
        ick = int(task.get("ick", 0))
        a["status"] = "done"
        a["done_at"] = _now()
        state["history"].append(
            {
                "week": state["current_week"],
                "task_id": req.task_id,
                "player": req.player,
                "effort": eff,
                "ick": ick,
                "done_at": a["done_at"],
            }
        )
        led = state["ledger"].setdefault(
            req.player,
            {"lifetime_effort": 0, "lifetime_ick": 0, "weeks_at_quota": 0, "daily_taps": 0},
        )
        led["lifetime_effort"] += eff
        led["lifetime_ick"] += ick
        return {
            "ok": True,
            "credit_remaining": _player_credit(state, req.player),
            "fireworks": True,
        }

    return await store.mutate(fn)


@app.post("/api/daily")
async def daily(req: TaskAction):
    """Log an off-wheel daily chore: a happy noise + a tally, and **nothing**
    that touches effort, quota, the piles, history or φ (SPEC §7)."""
    def fn(state):
        maybe_rollover(state)
        _validate_player(state, req.player)
        task = _task_by_id(state, req.task_id)
        if task is None:
            _fail(404, f"unknown task '{req.task_id}'")
        if task.get("on_wheel"):
            _fail(400, "that's a wheel task — spin for it instead")

        wk = state["current_week"]
        # UTC to match every stored timestamp (_now); a local date would mis-dedup
        # near midnight where the local day and the stored UTC day disagree.
        today = datetime.now(UTC).date().isoformat()
        log = state.setdefault("daily_log", {}).setdefault(wk, {})
        entries = log.setdefault(req.player, [])

        if not daily_already_logged(entries, req.task_id, today):
            entries.append({"task_id": req.task_id, "at": _now()})
            led = state["ledger"].setdefault(
                req.player,
                {"lifetime_effort": 0, "lifetime_ick": 0, "weeks_at_quota": 0, "daily_taps": 0},
            )
            led["daily_taps"] = led.get("daily_taps", 0) + 1

        count = len(entries)
        return {"ok": True, "daily_count": count, "sfx": DAILY_SFX[count % len(DAILY_SFX)]}

    return await store.mutate(fn)


@app.post("/api/week/rollover")
async def rollover():
    def fn(state):
        maybe_rollover(state)
        return {"week": state["current_week"]}
    return await store.mutate(fn)


# --------------------------------------------------------------------------- #
#  Dashboard builder (SPEC §8)                                                  #
# --------------------------------------------------------------------------- #
def _build_dashboard(state: dict) -> dict:
    k = _knobs(state)
    wk = state["current_week"]
    players = k["players"]
    quota = weekly_quota(state["tasks"], players, k["scale"])
    tints = state["config"].get("player_tints", {})
    daily_log = state.get("daily_log", {}).get(wk, {})

    per_player = {}
    for p in players:
        spent = spent_effort(state["history"], wk, p)
        banked_in = float(state.get("banked", {}).get(p, 0.0))
        led = state["ledger"].get(p, {})
        tiles = [
            {
                "task_id": row["task_id"],
                "name": (_task_by_id(state, row["task_id"]) or {}).get("name", row["task_id"]),
                "kind": (_task_by_id(state, row["task_id"]) or {}).get("kind", ""),
                "effort": row["effort"],
            }
            for row in state["history"]
            if row["week"] == wk and row["player"] == p
        ]
        per_player[p] = {
            "tint": tints.get(p),
            "spent": spent,
            "banked": banked_in,
            "credit_remaining": credit_remaining(quota, spent),
            "jobs": jobs_done(state["history"], wk, p),
            "ick_spent": spent_ick(state["history"], wk, p),
            "daily_taps": len(daily_log.get(p, [])),
            "lifetime_effort": led.get("lifetime_effort", 0),
            "lifetime_ick": led.get("lifetime_ick", 0),
            "streak": led.get("weeks_at_quota", 0),
            "tiles": tiles,
        }

    # The dashed line is the per-person quota — the target each pile (spent) aims
    # for. Piles render `spent`, so the line must be in the same units (SPEC §4.2,
    # §8): the gap between a pile's top and the line *is* that player's credit.
    fair_share_line = quota

    # per-task gaps: "last done by {player}, {n} weeks ago" (SPEC §8)
    last = _last_done_index(state["history"])
    now_idx = week_index(wk)
    gaps = []
    for t in wheel_tasks(state["tasks"]):
        per = last.get(t["id"], {})
        if per:
            who, when = max(per.items(), key=lambda kv: kv[1])
            gaps.append(
                {"task_id": t["id"], "name": t["name"], "kind": t["kind"],
                 "last_player": who, "weeks_ago": now_idx - when}
            )
        else:
            gaps.append(
                {"task_id": t["id"], "name": t["name"], "kind": t["kind"],
                 "last_player": None, "weeks_ago": None}
            )

    return {
        "week": wk,
        "quota": quota,
        "fair_share_line": fair_share_line,
        "players": per_player,
        "gaps": gaps,
    }


# --------------------------------------------------------------------------- #
#  Static PWA client (mounted last so /api/* wins). Built in Phase 4.          #
# --------------------------------------------------------------------------- #
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

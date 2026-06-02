"""API acceptance tests (SPEC §6) — the durable form of the phase-3 curl smoke.

Env is pointed at a throwaway state file + the shipped example seed *before*
``app.main`` is imported (the store captures its path at import time). Each test
gets a freshly-seeded state via the ``client`` fixture.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TMP = tempfile.mkdtemp(prefix="wheel-api-")
os.environ["WHEEL_STATE"] = os.path.join(_TMP, "state.json")
os.environ["WHEEL_SEED"] = str(ROOT / "family.example.yaml")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, store  # noqa: E402
from app.seed import build_initial_state  # noqa: E402


@pytest.fixture
def client():
    # fresh, deterministic state for every test (φ-cursor pinned)
    store.save_sync(build_initial_state(os.environ["WHEEL_SEED"], phi_cursor=0.0))
    with TestClient(app) as c:
        yield c


def _off_wheel_task(c) -> str:
    state = c.get("/api/state").json()
    return next(t["id"] for t in state["tasks"] if t["in_play"] and not t["on_wheel"])


def _on_wheel_task(c) -> str:
    state = c.get("/api/state").json()
    return next(t["id"] for t in state["tasks"] if t["in_play"] and t["on_wheel"])


# --------------------------------------------------------------------------- #
#  State + seeding                                                              #
# --------------------------------------------------------------------------- #
def test_state_is_seeded(client):
    state = client.get("/api/state").json()
    assert len(state["tasks"]) == 29
    assert state["config"]["players"] == ["Martin", "Sarah", "Alice"]


# --------------------------------------------------------------------------- #
#  Spin loop                                                                    #
# --------------------------------------------------------------------------- #
def test_spin_returns_an_on_wheel_task(client):
    r = client.post("/api/spin", json={"player": "Martin"})
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["on_wheel"] is True and body["task"]["in_play"] is True
    assert "credit_remaining" in body
    assert body["weight_breakdown"]["base"] == 1.0


def test_spin_is_idempotent_per_token(client):
    a = client.post("/api/spin", json={"player": "Martin", "spin_token": "tok-1"}).json()
    b = client.post("/api/spin", json={"player": "Martin", "spin_token": "tok-1"}).json()
    assert a["task"]["id"] == b["task"]["id"]
    assert b["idempotent"] is True


def test_spin_then_accept_then_done_credits_effort(client):
    spun = client.post("/api/spin", json={"player": "Sarah"}).json()
    tid = spun["task"]["id"]
    effort = spun["task"]["effort"]  # token

    accepted = client.post("/api/accept", json={"player": "Sarah", "task_id": tid}).json()
    assert accepted == {"ok": True}
    done = client.post("/api/done", json={"player": "Sarah", "task_id": tid}).json()
    assert done["fireworks"] is True

    state = client.get("/api/state").json()
    rows = [h for h in state["history"] if h["task_id"] == tid]
    assert len(rows) == 1
    assert rows[0]["player"] == "Sarah"
    assert rows[0]["effort"] == state["config"]["effort_scale"][effort]  # resolved int
    assert state["ledger"]["Sarah"]["lifetime_effort"] == rows[0]["effort"]


def test_done_credit_remaining_drops_after_completion(client):
    before = client.get("/api/dashboard").json()["players"]["Alice"]["credit_remaining"]
    spun = client.post("/api/spin", json={"player": "Alice"}).json()
    tid = spun["task"]["id"]
    client.post("/api/accept", json={"player": "Alice", "task_id": tid})
    done = client.post("/api/done", json={"player": "Alice", "task_id": tid}).json()
    assert done["credit_remaining"] < before


# --------------------------------------------------------------------------- #
#  Reroll                                                                       #
# --------------------------------------------------------------------------- #
def test_reroll_swaps_task_and_consumes_the_only_reroll(client):
    spun = client.post("/api/spin", json={"player": "Martin"}).json()
    first = spun["task"]["id"]
    rr = client.post("/api/reroll", json={"player": "Martin", "task_id": first})
    assert rr.status_code == 200
    second = rr.json()["task"]["id"]
    assert second != first

    # rerolls_per_spin = 1, so a second reroll on the new task is refused
    again = client.post("/api/reroll", json={"player": "Martin", "task_id": second})
    assert again.status_code == 403


def test_reroll_returns_rejected_task_to_the_pool(client):
    spun = client.post("/api/spin", json={"player": "Martin"}).json()
    first = spun["task"]["id"]
    client.post("/api/reroll", json={"player": "Martin", "task_id": first})
    # the rejected task is unclaimed again — someone else can be handed it
    state = client.get("/api/state").json()
    assert first not in state["assignments"][state["current_week"]]


# --------------------------------------------------------------------------- #
#  No double-assignment / pool exhaustion (SPEC §13 concurrency-adjacent)       #
# --------------------------------------------------------------------------- #
def test_pool_drains_to_409_without_double_assignment(client):
    seen = set()
    while True:
        r = client.post("/api/spin", json={"player": "Martin"})
        if r.status_code == 409:
            assert r.json()["detail"]["error"] == "pool_empty"
            break
        tid = r.json()["task"]["id"]
        assert tid not in seen, "task handed out twice"
        seen.add(tid)
    # 22 on-wheel tasks minus 4 paused (monthly/quarterly in_play:false) = 18
    assert len(seen) == 18


# --------------------------------------------------------------------------- #
#  Claim — take a specific on-wheel chore without spinning (post-hoc / by hand) #
# --------------------------------------------------------------------------- #
def test_claim_assigns_a_specific_task(client):
    tid = _on_wheel_task(client)
    r = client.post("/api/claim", json={"player": "Martin", "task_id": tid})
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["id"] == tid and body["claimed"] is True
    state = client.get("/api/state").json()
    assert state["assignments"][state["current_week"]][tid]["player"] == "Martin"


def test_claim_then_done_lands_on_the_pile(client):
    # the whole point: a claimed weekly chore credits effort just like a spun one,
    # and goes straight to done (no accept step needed)
    tid = _on_wheel_task(client)
    claimed = client.post("/api/claim", json={"player": "Sarah", "task_id": tid}).json()
    effort_token = claimed["task"]["effort"]
    done = client.post("/api/done", json={"player": "Sarah", "task_id": tid}).json()
    assert done["fireworks"] is True

    state = client.get("/api/state").json()
    rows = [h for h in state["history"] if h["task_id"] == tid]
    assert len(rows) == 1 and rows[0]["player"] == "Sarah"
    assert rows[0]["effort"] == state["config"]["effort_scale"][effort_token]
    assert state["ledger"]["Sarah"]["lifetime_effort"] == rows[0]["effort"]


def test_claim_does_not_advance_phi(client):
    before = client.get("/api/state").json()["phi_cursor"]
    tid = _on_wheel_task(client)
    client.post("/api/claim", json={"player": "Martin", "task_id": tid})
    after = client.get("/api/state").json()["phi_cursor"]
    assert after == before  # a deliberate pick is not a random draw


def test_claim_rejects_an_off_wheel_task(client):
    tid = _off_wheel_task(client)
    r = client.post("/api/claim", json={"player": "Martin", "task_id": tid})
    assert r.status_code == 400  # daily chores are logged with the daily button


def test_claim_rejects_a_task_already_taken(client):
    tid = _on_wheel_task(client)
    assert client.post("/api/claim", json={"player": "Martin", "task_id": tid}).status_code == 200
    again = client.post("/api/claim", json={"player": "Sarah", "task_id": tid})
    assert again.status_code == 409


# --------------------------------------------------------------------------- #
#  Daily bonus button (off-wheel, never touches effort/φ/piles)                 #
# --------------------------------------------------------------------------- #
def test_daily_logs_a_tap_with_sfx_and_no_effort(client):
    tid = _off_wheel_task(client)
    r = client.post("/api/daily", json={"player": "Martin", "task_id": tid}).json()
    assert r["ok"] is True and r["daily_count"] == 1 and r["sfx"]

    state = client.get("/api/state").json()
    assert state["history"] == []                       # never enters effort/piles
    assert state["phi_cursor"] == 0.0                   # never advances φ
    assert state["ledger"]["Martin"]["daily_taps"] == 1
    assert state["ledger"]["Martin"]["lifetime_effort"] == 0


def test_daily_is_idempotent_per_day(client):
    tid = _off_wheel_task(client)
    client.post("/api/daily", json={"player": "Martin", "task_id": tid})
    second = client.post("/api/daily", json={"player": "Martin", "task_id": tid}).json()
    assert second["daily_count"] == 1                   # same task, same day → no double
    state = client.get("/api/state").json()
    assert state["ledger"]["Martin"]["daily_taps"] == 1


def test_daily_rejects_a_wheel_task(client):
    state = client.get("/api/state").json()
    wheel_id = next(t["id"] for t in state["tasks"] if t["on_wheel"])
    r = client.post("/api/daily", json={"player": "Martin", "task_id": wheel_id})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
#  Dashboard                                                                    #
# --------------------------------------------------------------------------- #
def test_dashboard_shape(client):
    d = client.get("/api/dashboard").json()
    assert set(d["players"]) == {"Martin", "Sarah", "Alice"}
    assert d["quota"] > 0
    assert "fair_share_line" in d
    assert any(g["task_id"] for g in d["gaps"])


def test_fair_share_line_is_the_quota(client):
    # SPEC §4.2/§8: the dashed line is the per-person quota — the same units the
    # piles (spent) are drawn in, so the gap to the line reads as credit.
    d = client.get("/api/dashboard").json()
    assert d["fair_share_line"] == d["quota"]


def test_dashboard_credit_ignores_banked(client):
    # A banked head-start must NOT reduce this week's credit (SPEC §4.2:
    # credit = quota - spent). Banked stays a separate badge.
    quota = client.get("/api/dashboard").json()["quota"]
    s = store.load_sync()
    s["banked"] = {"Alice": 99.0}
    store.save_sync(s)

    alice = client.get("/api/dashboard").json()["players"]["Alice"]
    assert alice["credit_remaining"] == quota   # spent 0 → owes the full quota
    assert alice["banked"] == 99.0              # shown, but apart from credit


def test_spin_rejects_blank_player(client):
    # StringConstraints(min_length=1, strip_whitespace) → whitespace-only is 422
    r = client.post("/api/spin", json={"player": "   "})
    assert r.status_code == 422

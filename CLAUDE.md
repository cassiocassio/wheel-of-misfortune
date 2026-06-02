# CLAUDE.md — orientation for the agent

You are building **Wheel of Misfortune**, a fair family chore-assignment web app.
Read `SPEC.md` in full before writing any code. This file is the short version of the
conventions you must hold to.

## Before you start
- **The Phase-1 blockers are resolved** (see `OPEN_QUESTIONS.md`): (1) recurring-within-week
  → option (c): the wheel carries **weekly+ "deep"** tasks only; daily/frequent chores come
  off the wheel and are logged via the **daily bonus button** (a separate counter); (2)
  φ-stream → **continuous across weeks**, and a **reroll advances the cursor once more**.
  Also locked: **spin is non-blocking** (no quota gate — players self-pace; falling behind is
  surfaced by the piles, not refused by the API).
- `prototype/wheel-of-misfortune.html` shows the intended **feel** (picker → flick → accept
  → done → piles). It is a client-side throwaway — match its spirit, not its code.

## Non-negotiable invariants
- **Effort vs ick are separate.** Effort is the currency: accumulated, balanced, drawn as
  pile height. Ick is a *spread-only constraint*: it never becomes a score, credit, or pile
  height. There is no combined "burden." If you find yourself adding effort + ick, stop.
- **The server decides spin outcomes**, before the wheel stops. Clients only animate to the
  result. The φ-draw, cooldown, and ick-spread all live server-side, in one place.
- **Single Uvicorn worker.** State is an in-process-guarded JSON file. Running
  `--workers >1` silently breaks the shared-state guarantee. Never do it; document it.
- **LAN-only.** Bind `0.0.0.0`, never port-forward.
- **`state.json` is runtime truth and is gitignored.** `family.yaml` (gitignored, copied
  from `family.example.yaml`) is the human-editable seed, read once. Re-seeding must not
  wipe `history` or `ledger`.
- **Daily bonus is a separate counter.** Off-wheel daily/frequent chores are logged via
  `/api/daily` into `daily_log` (counts only). They **never** touch effort, the piles,
  quota, `history`, cooldown, or the φ-stream. If a daily tap changes any fairness number,
  stop. SFX is named by the server, played by the client.
- **Spin is non-blocking.** `/api/spin` always returns a task while the on-wheel pool is
  non-empty (409 only when truly empty). Quota is informational; never gate a spin on it.

## Build order (see SPEC §12)
Engine + tests → seed + state → API → frontend (spin + piles) → dashboard → PWA + deploy.
**Do not start the frontend before the engine tests pass.** The engine is pure and
deterministic given a seed — keep it that way so it stays testable.

## Style
- Legible over clever; deterministic over magical; fewer knobs over more.
- Friendly errors for the seed loader (line number + suggested fix), never a stack trace —
  this is the make-or-break for non-technical adoption (SPEC §5.1).
- Tests assert behaviour, not vibes (SPEC §13). Prove the maths.

## Tech
Python 3.11+, FastAPI + Uvicorn, PyYAML. Static HTML/CSS/JS frontend (no build step). PWA
via manifest + add-to-home-screen. The app code is OS-neutral; deploy is per-host: **macOS**
via launchd, **Linux** via a systemd `--user` unit (`deploy/install.sh` auto-detects). Works
over the LAN IP with nothing extra; `.local` is built-in on Apple devices and an *optional*
Avahi install on a Linux host — never required.
The host is **not** forced awake (no `caffeinate`) — it serves while awake, sleeps with the
host; always-on is a low-power box's job. Reached over Bonjour/mDNS at `misfortune.local`. Keep any new platform-specific
bits confined to `deploy/` + the Makefile, never in `app/`.

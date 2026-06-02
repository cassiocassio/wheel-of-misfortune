# 🎡 Wheel of Misfortune

A fair, fun, family chore-assignment app. Tap your name, flick the wheel, accept your fate.
Completed jobs stack into a pile per person — **equal-height piles mean an equal share of
the work.** Engineered so nobody gets stuck with the same job two weeks running, and the
grim jobs get spread evenly rather than dumped on one person.

> Runs on a Mac or Linux box at home, reached from any phone or tablet on the Wi-Fi at
> `http://<server-ip>:8000` (or `misfortune.local` on Apple/Windows). Nothing leaves your house.

## How it's fair

- **Effort is the currency.** Every chore has an effort size (XS–L → 2/3/4/5). The app
  shares total effort evenly; the piles make that visible.
- **Grimness is spread, not rewarded.** Nasty jobs ("ick") don't earn extra credit — the
  wheel just stops sending them to you once you've done your share.
- **It feels random but isn't lumpy.** A golden-ratio draw plus a recency cooldown means
  the order is unguessable yet you won't get the loo two weeks running. (The cog-psych
  reasoning is in `SPEC.md` §4.4.)

## Make it your own

Everything about your household lives in one human-editable file:

```bash
cp family.example.yaml family.yaml   # then edit family.yaml
```

Add a chore by copying a line, remove one by deleting it, pause one with `in_play: false`.
Friendly tokens throughout (`effort: M`, `ick: 2`) — no maths, no JSON. Full instructions
are in the file's comments.

## Run it

Same steps on **macOS and Linux** — the app is OS-neutral; only the keep-it-running layer differs.

With `make`:

```bash
make install                          # venv + dependencies
cp family.example.yaml family.yaml    # edit to taste
make run                              # serve on the LAN, port 8000
```

Or by hand:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp family.example.yaml family.yaml
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

**Open it on a phone/tablet.** The address that works on *every* device is the host's IP —
`http://<server-ip>:8000`. Give the box a fixed IP in your router, then bookmark it / Add to
Home Screen. The friendly `http://misfortune.local:8000` also works on Apple devices (built-in)
and Windows 10/11; on Linux it needs Avahi; **Android usually can't resolve `.local` — use the IP**.

### Keep it running (optional)

`make deploy` installs a background service that starts at login/boot and restarts on crash —
`install.sh` auto-detects the OS (a **launchd** agent on macOS, a **systemd `--user`** unit on
Linux; see `deploy/runbook.md`). It serves while the host is awake and sleeps with it — no forced
wakefulness. For always-on, use a low-power box (e.g. a Raspberry Pi), or a `pmset repeat` wake
window on a Mac. `make help` lists every task.

> ⚠️ Run with **one worker only** (`--workers 1`). The app keeps a single shared ledger;
> more workers would each keep their own and break fairness.

## Status

**v0.1.0 — built and green, but unproven.** Engine, API, PWA client and deploy tooling are
all in place and the test suite (`make test`) passes; the Phase-1 design decisions are
resolved (`OPEN_QUESTIONS.md`). It has had **no real-user testing yet** (only dev-browser
checks) — hence the pre-1.0 version. It graduates to 1.0.0 once a household has actually
lived with it for a week or two.

- ✅ `app/` — FastAPI engine, seed + atomic state store, week maths, the §6 API
- ✅ `web/` — the PWA client (wheel, piles, dashboard, daily button, offline shell)
- ✅ `tests/` — engine / seed / state / API suites
- ✅ `deploy/` — launchd plist, installer, runbook, stdlib icon generator
- ✅ `SPEC.md` / `CLAUDE.md` / `OPEN_QUESTIONS.md` — brief, conventions, decisions
- ✅ `prototype/wheel-of-misfortune.html` — the original feel reference

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (`AGPL-3.0-or-later`). Because the
AGPL covers network use, anyone you let reach a *modified* copy over the network is entitled
to its source — fitting for a self-hosted app meant to be tinkered with and shared.

## Backlog (v1 out of scope)

Off-LAN/remote access, multi-household, login/auth, in-app sizing editor, swap-with-consent,
push notifications. See `SPEC.md` §14.

## Layout

```
SPEC.md             full build brief
CLAUDE.md           conventions for the coding agent
OPEN_QUESTIONS.md   decisions: resolved vs open
family.example.yaml human-editable chore/household definition
pyproject.toml      metadata + pytest/ruff config       Makefile   common tasks (make help)
requirements.txt    runtime dependencies
prototype/          reference prototype (the intended feel)
app/ web/ tests/ deploy/   the app — see SPEC §11
```

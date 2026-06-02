# Open Questions & Decisions

A living register. **Resolved** items are locked unless the owner reopens them. **Open**
items still need a call — the two marked 🔴 **block Phase 1** (the engine) and must be
confirmed before building.

---

## Resolved (locked — see SPEC §1)

| Decision | Resolution |
|----------|-----------|
| Core model | Wheel of **tasks**; the spinner is the doer. |
| Accept / out | One reroll per spin, then you own it. |
| Cadence | Weekly accounting period (Mon–Sun). |
| Ick | **Distribution constraint only** — spread evenly, never scored, never in the pile. |
| Framing | Credit-to-spend (positive), counted in **effort**. |
| Colour | Hue = task *kind*; players = muted tint + initial (separated by saturation). |
| Effort scale | Compressed equal-step **XS=2 S=3 M=4 L=5** (hardest = 2.5× easiest). |
| Bathroom floor kind | "Wet & sanitise", not "Floors" (colour follows the bucket/quarantine). |
| Name | Wheel of Misfortune. Repo `wheel-of-misfortune`; host `misfortune.local`; launchd `com.storey.misfortune`. |
| Seed format | Commented YAML (`family.example.yaml` → `family.yaml`), read once into `state.json`. |
| Recurring-within-week | **(c)** — the wheel carries weekly+ "deep" tasks only; daily/2–3×week chores are off-wheel, logged via the **daily bonus button** (separate counter, never effort/quota/pile). |
| φ-stream lifecycle | **Continuous across weeks** (rollover never resets it); a **reroll** is a fresh draw excluding the rejected task and advances the cursor once more. |
| Quota-met behaviour | **No hard stop.** Spin is non-blocking; players self-pace ("3 or nothing"). Falling behind is surfaced by the piles (peer pressure), not refused by the API. |
| Daily bonus + sound | A one-tap off-wheel logger with sound effects; the server names the SFX cue, the client plays it; counts shown as a weekly tally. |
| State backup | Timestamped snapshot of `state.json` on each rollover. |

---

## Phase-1 blockers — RESOLVED ✅

Both former blockers are settled (now in the Resolved table above):

1. **Recurring-within-week → (c).** The wheel carries weekly+ "deep" tasks only. Daily and
   2–3×/week chores are off-wheel, logged via the **daily bonus button** — a separate counter
   that never enters effort, quota, the piles, `history`, cooldown, or the φ-stream. The seed
   loader derives `on_wheel` from `freq`; the wheel pool is `in_play AND on_wheel`.

2. **φ-stream → continuous + reroll-advances.** `phi_cursor` runs continuously across weeks
   (rollover never resets it). A reroll is a fresh `draw()` excluding the rejected task and
   advances the cursor once more. This pins the determinism tests.

---

## Open — non-blocking (have a default; confirm or flip later)

**UX**
- ~~Quota-met behaviour~~ — RESOLVED: no hard stop; keep spinning to **bank** credit (above).
- Stuck after the one reroll: own it (default) vs parent-override / swap-with-consent (v2).
- Undo a mistaken "done": none (append-only) vs an undo path.
- Wheel display when >8 remain: highest-weight 8 (default) vs grouped-by-kind wedges.
- Honour system: no auth (default) vs a lightweight "who marked this" trail.
- Polish: flick feel curve, sound + haptics, portrait-phone vs landscape-iPad layouts.

**Implementation**
- Pending-spin expiry: does a spun-but-unaccepted task time out back into the pool? What
  stops "shopping" for an easy job by re-spinning?
- Live multi-device sync: short-interval polling of `/api/state` (cheapest correct option)
  vs SSE/websockets. *(Default: polling.)*
- State backup: `state.json` is the only source of truth and is gitignored — periodic copy
  to iCloud/Time Machine, or a timestamped snapshot on rollover?
- Schema migration path for `version` bumps.
- Player flexibility: fixed three vs guest mode / two-home-this-week / temporary exclusion.
- Icon source (lucide?) and the PWA / home-screen app icon (needs designing).
- Week-boundary edge cases: DST, exactly when lazy rollover fires, "Monday" for a 00:30 spin.
- Admin view for editing sizings/kinds (v1 = edit `family.yaml` by hand).

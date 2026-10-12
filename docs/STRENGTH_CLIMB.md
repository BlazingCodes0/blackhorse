# The strength climb — intervals are the profiles

_Design session note. This document is the copy of the conversation that produced
the change. It supersedes the older "Profile = risk posture" model documented in
`docs/SNAPSHOT.md`._

## The change in one line

The profile layer is **deleted**. Each agent trades exactly **one entry interval**
and carries that interval's own settings (editable). Above it, the agent may climb a
**ladder of higher intervals** (the strength climb) — each rung must confirm its own
floor/roof before the entry lane is allowed to buy or sell. The library of profiles
is now simply: the five intervals.

## Why

Profiles were a hidden second grid: an interval set (swing/daytrade) *and* a risk
posture (quick/slow/lifecycle/scalp15), merged in the back room. Worse, a single
"small-timeframe rule" hard-coded the 60m lane as a gate for every sub-60m lane.
The owner wanted the machinery visible and self-serve:

- Every interval's defaults **are** the profile.
- The owner edits them per agent in the GUI.
- Special cases (the old `lifecycle` = flat 15m; `scalp15` = off the climb) are no
  longer hard-coded — the owner recreates them by choosing the entry interval and
  turning the climb off.

## The model

- The **smallest interval listed** for an agent is its **entry lane** — the one it
  actually trades.
- Every **other listed interval above it** is a **climb rung**. A rung with no lane
  in the signal snapshot is pulled live from Kraken OHLC.
- Before the entry lane may **buy**, every rung's strength must be **≤ its buy band**.
- Before the entry lane may **sell**, every rung's strength must be **≥ its sell band**.
- **No hidden default.** If only the entry interval is listed, the climb is OFF and
  the lane trades on its own strength alone. (This removes the old default-60m rung.)
- Stored form: `strategy_intervals = [entry] + sorted(active rungs)`.

## Default settings per interval (editable)

| Interval | buy ≤ | sell ≥ | min R:R | stop % | risk % | default climb |
|----------|------|--------|---------|--------|--------|----------------|
| 5m       | 0.25 | 0.75   | 1.2     | 1.5    | 1.0    | 15m, 60m       |
| 15m      | 0.25 | 0.75   | 1.2     | 2.0    | 1.5    | 60m, 240m      |
| 60m      | 0.20 | 0.85   | 1.5     | 3.0    | 2.0    | 240m, 1440m    |
| 240m     | 0.15 | 0.80   | 2.0     | 5.0    | 2.0    | 1440m          |
| 1440m    | 0.15 | 0.80   | 2.0     | 5.0    | 2.0    | — (top)        |

## The GUI (Agents → Settings)

The old Strategy combo + Intervals text box + Profile dropdown are replaced by a
single **entry lane & strength climb** block: five buttons — `5m 15m 60m 240m 1440m`.

- Pressing a button makes it the **entry lane**; it and **everything smaller** grey
  out (disabled). Choosing an interval loads that interval's **default settings**
  into the band fields (buy ≤ / sell ≥ / R:R / stop / risk / max position).
- Buttons **strictly larger** stay clickable and are the **climb toggles**
  (green ✔ = in the climb). Their bands show as `60m rung · buy ≤ … · sell ≥ …`.
- Toggling a rung on/off adds/removes it from the climb only.
- On Save, `strategy_intervals`, `channels` and `strategy` are written from the
  buttons (channels derived, deduped).

## Migration applied

- `agent_1` (Lifecycle): entry **15m**, climb **OFF**, bands 0.30/0.70 — unchanged
  behavior (it never had the 60m gate; the old `lifecycle` profile was flat by design).
- `agent_6` (Slow 6) → **60m entry**, climb **{240m, 1440m}**, bands **0.15 / 0.80**.
- Obsolete `"profile"` keys removed from every agent.

## Files changed

- `trade/agents.py` — `PROFILES` -> `INTERVAL_DEFAULTS`; `profiles()` →
  `interval_defaults()`; `set_profile`/`shadowed_keys` removed; `policy_for` merges
  defaults + overrides only; `create_agent(interval)` seeds that interval's settings +
  default climb; CLI `profiles`/`profile` commands removed (now `intervals`).
- `trade/decider.py` — `_chain_from` no longer invents a 60m rung; removed
  `strength_chain_enabled` / `small_tf_strength_from_60m`; `chain_used = bool(rungs)`.
- `app/agents_ui.py` — the entry/climb button block replaces Strategy+Intervals+Profile;
  removed the four profile methods and the "Strength chain" SETTINGS group.
- `app/main.py` — `_show_profile` and `create_agent` now use `interval_defaults()`.
- `trade/tools.py` — `create_agent` takes an interval, not a profile.
- `config/decider.json` — dropped the chain master switch + per-rung keys; rewrote the
  comment.
- `config/agents.json` — profile keys dropped; agent_6 migrated.

## Verification

- `py_compile` clean on all touched files.
- `decider.decide()`: a single listed interval → `rungs: []`, trades flat on its own
  strength (agent_1 → BUY on the 15m lane, "single entry lane"); agent_6 → entry 60m
  with rungs 240m/1440m, gated.
- GUI offscreen smoke: agent_6 loads entry 60m + climb {240m,1440m}; pressing 15m makes
  it the entry, greys below, loads 15m defaults and the default climb {60m,240m};
  toggling a rung updates the state; Save writes `strategy_intervals`/`channels`/`strategy`.

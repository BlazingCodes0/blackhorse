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
- Before the entry lane may **buy**, the entry lane's own strength must sit in its
  **buy range**, and every rung's strength must sit in **its own buy range**.
- Before the entry lane may **sell**, the entry lane's own strength must sit in its
  **sell range**, and every rung's strength must sit in **its own sell range**.
- A range is derived from the single thresholds unless overridden: `buy = [0, entry_max]`,
  `sell = [exit_min, 1]`. Each rung may carry a custom nested range
  `rung_bands = {"<iv>": {"buy": [lo, hi], "sell": [lo, hi]}}` (any active interval,
  dynamic by label — nothing keyed to 60m/240m/1440m in code).
- **Star alignment** (optional extra gate, per agent, default OFF): when ON the entry
  lane must additionally be **entryEligible** to buy / **exitEligible** to sell (its own
  raw signal), while the rungs only need their strength inside their zone. Ignored when
  the climb is off.
- **No hidden default.** If only the entry interval is listed, the climb is OFF and
  the lane trades on its own strength alone. (This removes the old default-60m rung.)
- Stored form: `strategy_intervals = [entry] + sorted(active rungs)`; rung ranges in
  `rung_bands` (legacy `entry_strength_max_<iv>` / `exit_strength_min_<iv>` still read
  as a fallback, and apply only to rungs — the entry lane uses the agent globals).

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
  (green ✔ = in the climb). Each rung row shows four editable numbers:
  `60m rung · buy [lo–hi] · sell [lo–hi]` — saved as the nested `rung_bands` dict.
- A **Star alignment** checkbox (Strength climb gate group, default OFF) adds the
  per-agent entry-eligibility gate described above.
- Toggling a rung on/off adds/removes it from the climb only.
- On Save, `strategy_intervals` and `strategy` are written from the buttons. No
  `channels` name list is written, and any legacy `channels` override is removed —
  lanes are matched by their `interval` alone, never by channel name.

## Migration applied

- `agent_1` (Lifecycle): entry **15m**, climb **OFF**, bands 0.30/0.70 — unchanged
  behavior (it never had the 60m gate; the old `lifecycle` profile was flat by design).
  Reconcile removed the stray `60m`/`240m` rungs + degenerate band keys.
- `agent_6` (Slow 6) → **60m entry**, climb **{240m, 1440m}**, bands **0.15 / 0.80**.
- Obsolete `"profile"` keys removed from every agent.

## Reconcile (ranges + star alignment)

- Every trading agent's `strategy_intervals` == `[entry] + sorted(active rungs)`.
  Legacy `channels` name lists were **deleted** from every agent: the snapshot
  channels (`ripple`/`wave`/`tide`) are the sidecar's anonymous slots, and each carries
  its own `interval` field. A lane is identified **only** by that interval field, so any
  interval may sit in any slot and nothing is keyed to a channel name.
- Per-rung ranges moved to the nested `rung_bands` dict at the **design defaults**
  (60m [0,0.20]/[0.85,1] · 240m & 1440m [0,0.15]/[0.80,1]); degenerate legacy singles
  (`…_60m: 0.0`, `…_240m: 1.0`) removed. Legacy singles are still read as a fallback.
- `agent_7` renamed `5m 7` → `15m 7` (its entry is 15m). `agent_2/4/5` gained their
  missing `strategy_intervals`.

## Interval-driven update (no name maps)

The name↔interval maps that survived the profile deletion were removed everywhere:

- `trade/decider.py` — `_slots_intervals` replaced by `_lane_for_interval` /
  `_htf_lane` / `_intervals_of`; the HTF-context gate reads the **highest** lane's real
  trend (`marketResult.trendState`) instead of a fixed `tide` channel.
- `trade/agents.py` — `_as_intervals` keeps only `<n>m` labels; `create_agent` writes
  `strategy_intervals` only; `clear_override` added.
- Consumers made interval-driven: `execute.py`, `broker.py`, `exits.py`, `observer.py`,
  `advisor_combo.py`, `signalbot.py` (role ranked by interval), `app/main.py`,
  `trade/loop.py`, `app/agents_ui.py`.
- `trade/signalbot.py` — the dayTrade merge uses neutral `dt_<slot>` keys; the three
  source keys are read as emitted but never treated as fixed intervals.
- `trade/store.py` — `record_decision_changed` re-logs a HOLD/SKIP when the strength
  chain verdict changed, so the GUI's panel gets the trader's live rung read promptly.
- `app/agents_ui.py` — the strength-chain panel merges the trader's logged chain for
  rungs absent from the snapshot (e.g. 1440m), showing real block/buy instead of a bare
  placeholder, and no longer renders a doubled "live live".

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
- Range/star rework: `decide()` unit checks (entry-lane range gates even with a climb,
  rung ranges, `rung_bands` override, star-alignment on/off, legacy single fallback,
  bare config = flat single lane) all pass; GUI `strength_chain`/`_chain_html` render
  ranges + "entry not elig / exit elig" with no crash.
- GUI offscreen smoke: agent_6 loads entry 60m + climb {240m,1440m}; pressing 15m makes
  it the entry, greys below, loads 15m defaults and the default climb {60m,240m};
  toggling a rung updates the state; Save writes `strategy_intervals`/`strategy` and
  clears any legacy `channels` override.

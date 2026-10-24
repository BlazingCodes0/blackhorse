# BlackHorse

A local, autonomous **paper-trading** horse with its own defense layer — a PyQt6
desktop app over a file-driven trading loop and a host monitor, all on your own
machine.

> **Trading is paper-only by default.** BlackHorse is designed to reason, decide
> and record as if it traded, through config-driven exchanges, without moving real
> money unless *you* explicitly wire it up. It is not financial advice.

BlackHorse is one of a family of apps. Its sister project **Mr.Fluffy** reads
BlackHorse's own files to build its market views; BlackHorse itself depends on
neither.

---

## What it is

- **The trade house** — agents are "bodies" with personalities (`quick` scalper,
  `slow` patient HTF). A decider turns signal + verdict + treasury into one sized
  plan; a broker reconciles conflicting agents with evidence; a **heart** referee
  vets every action ALLOW/BLOCK. Spot only — no margin, leverage, futures or
  shorting (derivatives are *read* as information, never traded).
- **The skin** — a defense/monitoring layer that watches connections, processes
  and logs, and reacts **dry-run by default** (log + alert; blocking is opt-in).
- **Memory** — permanent (never deleted) and ephemeral (must be deleted),
  disk-first.
- **Everything is a file** — agents, treasury, decisions, signals, logs and P&L
  live in `config/` and `logs/`; the GUI and the loop re-read them.

---

## Requirements

- Python 3.10+
- Linux (the desktop app and the defense helpers assume a Linux host)
- See `requirements.txt` — core: `PyQt6`, `psutil`; optional: `cryptography`,
  `Pillow`, `qdarkstyle`

```bash
pip install -r requirements.txt
```

---

## First run

```bash
cd ~/blackhorse
pip install -r requirements.txt

# 1) start the desktop app (Dashboard opens; the app lives in your tray)
python3 app/main.py
```

The window opens on **Dashboard**. Closing the window **hides to the tray** — it
does not quit. Use the tray icon to **Show window**, **Toggle master ON/OFF**, or
**Quit BlackHorse**. Only *Quit* actually stops the app.

There is also an optional desktop launcher: `./install_launcher.sh` installs a
`.desktop` entry that runs `python3 $APP_DIR/app/main.py` for you.

Nothing touches real money until you both set **LIVE** and provide exchange
credentials — with no credentials the app treats everything as paper. Read *The
safety layers* below before you turn anything on.

---

## Quick start — the safe path

Set the house up bottom-up. Each layer only matters once the one below it agrees.

| Step | Do this | Where | Default |
|------|---------|-------|---------|
| 1 | Confirm the master bar says **SYSTEM ARMED** | top bar | ON |
| 2 | Create an agent, pick its **intervals**, set its **Capital $** | Agents | no agents |
| 3 | Leave **mode = PAPER** until you trust the decisions | Agents → Where it trades | PAPER |
| 4 | Put money in the **Treasury** and set a **reserve** floor | Trade → Treasury | empty |
| 5 | Leave **Autonomous** off while you watch it decide | Trade → Autonomy | gate-enforced |
| 6 | Start the loop, or press **⚡ Decide now** for exactly one cycle | Trade → loop | stopped |
| 7 | Read **Agents → Decisions → Why it is (or isn't) entering** | Agents | — |

**Budget and caps at a glance**

- An agent may size only from its **Capital $**, and the house never deploys more
  than the real free cash (`min(agent.capital, treasury.free)`).
- The treasury **reserve** is a floor the house refuses to spend below.
- On top of that, Heart enforces hard caps and hard bans on every order
  (`heart/policies/rules.json`):

| Heart cap | Default |
|-----------|---------|
| Max single position | 25% of capital |
| Max open positions | 10 |
| Max daily loss | 3% |
| Max drawdown | 10% |
| Max orders per hour | 12 |
| Max order size | 200 quote |
| Dead-man heartbeat | 600 s → **PARK** |

> Paper is the default for a reason: you can run the whole loop, agents, sizing
> and exits without sending a single real order. Go **LIVE** only after you have
> watched the Decisions tab and you are happy with the reasons it gives.

---

## The safety layers — read this first

BlackHorse has several independent switches. Nothing moves unless **all** relevant
layers say yes. Turn them on deliberately, from the bottom up.

| Layer | Where | Default | What it does |
|-------|-------|---------|--------------|
| **Master ARM** | top bar (every page) | **ON** | The single global ON/OFF for the whole house (engines, whitecells, brain, loop). `master_enabled` in `config/skin_settings.json`. Toggling it writes to disk and re-reads to verify. |
| **Autonomous** | Trade → Autonomy | ON in `config/trading.json` | Lets the loop trade by itself. Does **not** imply live. The switch refuses to arm until credentials + Heart gate + a fee-honest backtest pass. |
| **Money mode** | Trade → Money | **PAPER** | `mode: PAPER` or `LIVE` in `config/trading.json`. PAPER fills are simulated; LIVE sends real orders. This is the only switch that can move real money. |
| **Heart** | Heart page / `heart/policies/rules.json` | enforced | Every single action must pass ALLOW/BLOCK. Cannot be disabled by the brain. Hard-bans margin, leverage, futures, shorting, withdrawals, unknown venues. |
| **Defense** | Skin page | **dry-run** | The skin logs and alerts but does not block until you enable ACTIVE defense. |
| **Stop loss** | Agents → Settings | **OFF** | The house rule is *never sell at a loss*. Stops are opt-in per agent. |

> Two separate switches, deliberately: **autonomous** ("decide by yourself") and
> **mode** ("paper or real money"). Autonomous never implies live, and the app
> will not arm autonomy until its unlocks are satisfied.

To be **truly off**: set Master to DISARM on the top bar, stop the loop (Trade →
Stop loop), and uncheck LIVE. The Dashboard's **Verify OFF now** button checks all
of this for you and tells you what is still live.

---

## Using the app — the five pages

The left sidebar has: **Dashboard · Skin (Defense) · Trade (Exchange) · Agents ·
Heart (Filter)**.

### 1. Dashboard
System overview and the single most useful safety button.

- **Verify OFF now** — audits every switch and reports anything still armed.
- P&L per agent, the autonomous **loop status** (RUNNING / HALTED — it detects a
  stale heartbeat), and a trade summary of what is open.
- The top **master bar** (present on every page) shows **SYSTEM ARMED** /
  **SYSTEM DISARMED** and lists which engines are on (`engines yes · whitecells
  yes · local model OFF · brain OFF`). Click **DISARM** to turn everything off.

### 2. Skin (Defense)
The host-defense monitor.

- **Run sweep now** — one investigation pass; **LOCKDOWN** freezes everything
  (L3) and wakes you.
- **Set up continuous skin monitoring** — installs a user systemd unit
  (`blackhorse-defense.service`, `skin/defense.py --loop 60`) that keeps watching
  in the background; **Disable** removes it.
- **Enable ACTIVE defense** — a one-time opt-in that flips the service from
  dry-run (`BLACKHORSE_DRY_RUN=true`) to real reactions (firewall/process). Until
  then the skin only logs and alerts.
- Threat hits are written to `logs/threats.jsonl`; the page live-refreshes the log
  every 10s. Blocking acts on **this host only**.

### 3. Trade (Exchange)
Connection, money, signal, and the autonomous loop — the main control room.

- **Exchange** — pick a venue, **Set active**, and **Enter credentials…** (stored
  in the gitignored vault). "Expected connection" shows what a private call would
  look like.
- **Autonomy** — `AUTONOMOUS trading ON`. Locked until credentials + Heart gate +
  fee-honest backtest pass; the warning text spells out the unlocks.
- **Money** — the PAPER/LIVE checkbox. LIVE sends real orders; PAPER simulates.
  The label reads `PAPER` (green), `PAPER (no credentials)` if LIVE is set but no
  keys are present, or `LIVE 💰`.
- **Treasury** — the capital agents may deploy. Type an amount and **Allocate**,
  or **Set reserve** (a floor the house will not spend below).
- **Memory / News** — ephemeral vs permanent memory counts, and the market/macro
  headlines that feed the brain.
- **SignalBot** — **▶ Run SignalBot now** runs the *real* SignalBot via its
  sidecar (no reimplementation), then shows the per-interval read as a **table**
  — one column per interval, with action, confidence, strength (▲/▼), trend·RSI,
  entry/exit eligibility, spread, MA10−MA30, macross, range·bias, stop/target,
  advisor and scenario, plus a strength-chain-gate footnote. Set the
  **intervals** and **pair**, then **Save signal setup**
  (`config/signal_setup.json`).
- **Autonomous loop** — **▶ Start loop** / **■ Stop loop**, with an `every N s`
  interval (default 300). This is the timer that runs the whole house. Its
  heartbeat shows on the Dashboard.

### 4. Agents
The heart of the UI. Left: the agent list (name, enabled, mode, what it holds).
Selecting one opens three tabs.

- **Overview** — the agent at a glance: stance, holdings, last decision, settings
  summary.
- **Orders** — its open/resting orders and recent fills; auto-refreshes.
- **Decisions** — *why it is or isn't entering right now.* Shows:
  - **Why it is (or isn't) entering** — the newest decision reason plus the
    decider's gate checklist (`detail.explain`): every gate that passed or blocked.
  - **Strength chain** — the agent's entry lane and every climb rung, with each
    rung's live strength and its buy/sell band, and whether it currently clears.
  - **Decisions it is making** — the last 25 recorded decisions.
  - **Where the market is** — SignalBot's own per-interval read, as a **table**
    (one column per lane): action, strength, trend·RSI, the entry gate (rsi ·
    spread · score → READY/not yet), range map, spread, MA10−MA30 and scenario.
  - **Scenarios** — the six-scenario rubric with the active one marked `◀`.

  The Decisions tab is **read-only** — it reads the `decisions` table and the
  bot's written signal snapshots and never runs the bot or trades; it refreshes
  every 5s while visible.

> The SignalBot readouts and the other timer-refreshed panes **hold your scroll
> position** while they repaint, so you can read a long pane without it snapping
> back to the top every 5–10s.

- **Settings** — per-agent policy, written back to the agent on **Save to agent
  policy** (applies on the trader's next cycle, ≤5 min). See *Agents, intervals &
  the strength climb* and *Stop loss* below. **Revert** discards un-saved edits.

### 5. Heart (Filter)
The referee, in the open.

- **status** — heartbeat age and current gate posture.
- **❤ Heartbeat now** — prove the loop is alive; a missed heartbeat (deadman)
  **PARKS** the house.
- **Caps** — live copy of `heart/policies/rules.json`: hard bans, spot-only
  enforcement, and caps (max position %, max open positions, max daily loss,
  max drawdown, orders/hour, max order quote).
- **Autonomy unlock** — the checklist that must be all-true before autonomy can
  arm (`require_credentials`, `require_heart`, `require_backtest_passed`, …).

---

## The trading loop — what one cycle does

The loop is the autonomous heartbeat (`trade/loop.py`). One cycle:

1. **HEARTBEAT** — prove we're alive; otherwise the Heart parks everything.
2. **REFRESH SIGNAL** — run SignalBot via the sidecar if due.
3. **OBSERVE** — note market behavior into ephemeral memory.
4. **THINK** — brain verdict via the coach, budgeted (ask only when due).
5. **DECIDE** — broker: gather agents → decider → adjudicate conflicts.
6. **GATE** — Heart vets the plan (inside broker/execute).
7. **MARK** — update open positions; realise stops/targets.
8. **ACT** — execute the approved plan (paper fill, or live if wired).
9. **RECORD** — write memory and logs.

It is **fail-closed**: if the Heart blocks or anything throws, nothing moves.
The `--interval` cadence is measured **from the start of each cycle**, so a slow
SignalBot scan eats into the sleep instead of pushing the next run out — `--interval
300` really means every ~5 minutes.

Run it:

```bash
python3 trade/loop.py --once                 # a single cycle, then exit
python3 trade/loop.py --interval 300         # daemon, every 5 minutes
python3 trade/loop.py --once --no-signal     # skip the slow SignalBot step
python3 trade/loop.py --once --no-brain      # skip the brain verdict
python3 trade/loop.py --pair DOT/USD         # limit to one pair
python3 trade/loop.py --night --hours 8      # overnight watchlist shift
```

From the GUI you can drive the same loop with **Trade → Start loop / Stop loop**.
On **SIGTERM/SIGINT** (systemd stop, OS poweroff, Ctrl-C) the loop journals what
was open to `logs/shutdown.json` and checkpoints both SQLite DBs; resting orders
are left in place (the venues own them) — **shutdown never moves money**.

### Loop controls (no restart required)

| Control | Where | What it does |
|---------|-------|--------------|
| **▶ Start loop** | Trade | Starts `blackhorse-trader.service` when systemd is active, otherwise a manual `trade/loop.py` process. |
| **■ Stop loop** | Trade | Stops the systemd unit and/or the manual process. |
| **⚡ Decide now** | Trade | Fires the **real** loop sweep immediately — refresh signal, decide, Heart-gate, execute, record. With the daemon up it stamps a trigger file so the sleeping daemon wakes for one extra sweep. With no daemon it runs one sweep in-process. |
| **every N s** | Trade | Live cycle speed, **15–3600 s** (default 300). The daemon re-reads it every cycle, so the change applies without a restart. |
| **Trade → Loop status** | Trade | `RUNNING` (systemd unit + cadence) or `stopped`. The Dashboard also flags a stale heartbeat as `HALTED`. |

The GUI and the daemon talk through files, so you can also drive them by hand:

| File | Shape | Read by | Purpose |
|------|-------|---------|---------|
| `logs/loop_control.json` | `{"interval_seconds": 300}` | `trade/loop.py` | Live cadence; clamped to a 15 s minimum. CLI `--interval` is only the fallback. |
| `logs/loop_trigger.json` | `{"ts": 1790000000}` | `trade/loop.py` | One-shot "decide now" stamp. The daemon honours each stamp once. |
| `logs/loop_state.json` | heartbeat + cycle info | GUI / Dashboard | Fresh heartbeat = alive; stale = the Dashboard shows `HALTED`. |

The systemd user units are `blackhorse-trader.service` (the loop) and
`app-blackhorse@autostart.service` (the GUI). Speed changes and **Decide now**
never spawn a duplicate loop — the singleton lock makes a second instance exit.

---

## Agents, intervals & the strength climb

There are no hidden "profiles" anymore — **the intervals are the profiles**.

- Each agent trades exactly **one entry interval** — the **smallest interval
  listed**. That lane carries its own editable settings.
- Every **larger interval listed** is a **climb rung**. Before the entry lane may
  **buy**, every rung's strength must be **≤ its buy band**; before it may
  **sell**, every rung must be **≥ its sell band**.
- Listing only the entry interval means the climb is **off** and the lane trades
  on its own strength alone.
- A rung with no lane in the signal snapshot is pulled **live** from Kraken OHLC.

Default settings per interval (editable, per agent):

| Interval | buy ≤ | sell ≥ | min R:R | stop % | risk % | default climb |
|----------|------|--------|---------|--------|--------|----------------|
| 5m       | 0.25 | 0.75   | 1.2     | 1.5    | 1.0    | 15m, 60m       |
| 15m      | 0.25 | 0.75   | 1.2     | 2.0    | 1.5    | 60m, 240m      |
| 60m      | 0.20 | 0.85   | 1.5     | 3.0    | 2.0    | 240m, 1440m    |
| 240m     | 0.15 | 0.80   | 2.0     | 5.0    | 2.0    | 1440m          |
| 1440m    | 0.15 | 0.80   | 2.0     | 5.0    | 2.0    | — (top)        |

**In the GUI** (Agents → Settings) this is one block: five buttons — `5m 15m 60m
240m 1440m`.

- Pressing a button makes it the **entry lane**; the smaller ones stay clickable
  (grey `◂`) so you can always hand the driving lane back down. Choosing an
  interval loads that interval's **defaults** into the band fields (buy ≤ / sell ≥
  / R:R / stop / risk / max position).
- Clicking the entry button itself walks the driving lane **up** to the next rung
  in the climb; clicking a smaller interval walks it **back down** — the lane you
  just left drops into the climb as a rung.
- Buttons strictly larger stay clickable as **climb toggles** (green ✔ = rung in
  the climb). Each rung shows its own `60m rung · buy ≤ … · sell ≥ …`.
- **Save to agent policy** writes `strategy_intervals`, `channels` and `strategy`
  from the buttons. Band overrides are stored as
  `entry_strength_max_<iv>` / `exit_strength_min_<iv>`.

### Interval layouts — what the ladder means

| Intervals listed | Entry lane | Climb rungs | Meaning |
|------------------|------------|-------------|---------|
| `15m` | 15m | — | Climb **off**; the 15m lane trades on its own strength. |
| `15m, 60m` | 15m | 60m | 15m may move only when 60m is also in its buy (or sell) band. |
| `15m, 60m, 240m` | 15m | 60m, 240m | Both higher lanes must clear their own bands. |
| `60m, 240m` | 60m | 240m | Same idea, slower: 60m drives, 240m confirms. |
| `60m` | 60m | — | The proven 60m lane alone. |
| `1440m` | 1440m | — | Daily lane; nothing sits above it. |

**Strategy presets** are just examples of that list (Intervals stay fully
editable):

| Strategy | Intervals filled in | Use it for |
|----------|--------------------|------------|
| `swing` | 15m, 60m, 240m | Slower entries confirmed by higher timeframes. |
| `daytrade` | 5m, 15m, 60m | Faster intraday entries with 5m timing. |
| `custom` | whatever you type | Any interval combination you want. |

### Rungs only — let the climb decide

**Settings → Rungs only (bypass other gates)** flips which gates are allowed to
vote. The **Rung agreement** combo appears only when it is checked.

| Rungs only | Agreement shown | What decides a trade |
|------------|-----------------|----------------------|
| **OFF** (default) | hidden — always **ALL** | Normal gates: entry band + eligibility, star alignment, brain/HTF, proven edge, spread/MA, R:R — and **every** rung must agree. |
| **ON** | **ALL** | Only the climb rungs vote; every rung must agree. |
| **ON** | **ANY** | Only the climb rungs vote; **one** agreeing rung is enough. |

**Bypassed when Rungs only = ON**

| Gate | What it normally does |
|------|------------------------|
| Entry band / eligibility | Entry-lane strength must sit in its buy or sell range. |
| Star alignment | Entry lane must be `entryEligible` / `exitEligible`. |
| Brain stance & risk | `STAND_DOWN` / `EXTREME` can block. |
| MTF / HTF context | Higher-timeframe agreement is required. |
| Proven-edge gate | Refuses intervals without a measured fee-honest edge. |
| Spread / MA cross | Optional 15m (or chosen) spread and MA gates. |
| Trend / RSI extremes | Trend agreement and RSI extreme filters. |
| Min R:R | The reward-to-risk filter. |

**Never bypassed, even with Rungs only = ON**

| Still enforced | Why |
|----------------|-----|
| Heart hard caps & bans | Position/order/daily-loss/drawdown/rate limits, spot-only. |
| Spot-only sells | You must hold inventory; no naked short. |
| Signal + treasury checks | No signal snapshot or no spendable capital → HOLD. |
| Sizing & fee reserve | The order must still be affordable and fee-honest. |
| Entry / stop / target mechanics | A valid limit plan still needs real prices. |

> Rungs only needs **at least one rung**. With the climb off it refuses with
> `rungs only: no climb rungs active`. In normal mode the agreement selector is
> ignored — the house always waits for **ALL** rungs.

### MA/spread gate interval

The optional **Spread verdict** and **MA cross** gates read exactly one interval:
**Settings → Advanced → MA/spread gate interval** (per agent, default **15m**).

| Setting | Values | Default | What it does |
|---------|--------|---------|--------------|
| `ma_gate_interval` | 5m, 15m, 60m, 240m, 1440m | **15m** | Which interval feeds **both** gates. |
| `require_spread_agreement` | on/off | **OFF** | Block BUY on a genuinely weakening spread, SELL on a genuinely improving one. Chop/noise never blocks. |
| `require_ma_cross_agreement` | on/off | **OFF** | Block BUY while fast MA < slow, SELL while fast > slow. |
| `ma_cross_fast` / `ma_cross_slow` | periods | **20 / 50** | MA cross pair. |
| `ma_cross_min_gap_pct` | % | **0.15** | Gaps smaller than this are a coil and never block. |
| `spread_ma_fast` / `spread_ma_slow` | periods | **10 / 30** | Spread-analysis context pair (separate from the cross). |

The entry lane and climb rungs **never** feed these gates. If the chosen interval
has no lane in the snapshot, the spread verdict falls back to the entry lane
(and says so), while the MAs are pulled live from the venue's candles.

Conflicts between agents are reconciled by the **broker**, and every resulting
action still has to pass the **Heart**. See `docs/STRENGTH_CLIMB.md` for the full
design note.

---

## Exits, targets & sell rules

The house rule is **never sell at a loss**. Stops are **OFF by default**; a
position waits for a profit-checked exit. Exits are watched on every loop cycle
and roughly every **15 s** while a position is open (`exit_poll_seconds`).

### Profit exit (default)

| Rule | Default | Meaning |
|------|---------|---------|
| Sell only at a net profit | always | Net of the entry fee already paid **and** the exit fee. |
| `exit_min_net_pct` | **0.25%** | The minimum net profit the exit must clear. |
| `exit_price_sets` | `["average"]` | Which stored SignalBot price sets are candidates (also `perfected`, `median`, `sweet`). |
| Exit order type | **limit only** | Rests a maker sell at `max(profit_price, best ask)`, rounded **up** to the pair tick — it can never cross. |
| `exit_requote_seconds` | **20 s** | An unfilled resting exit is cancelled and re-rested at the current best ask (the maker "bid/ask war"). |

At entry the decider stores an **entry snapshot** (each interval's levels and
advisor prices) with the position; the exit uses those numbers, not today's
memory. Partial fills are booked for the executed part; a cancelled or expired
resting exit releases the position so `exits.py` can retry. Sells are
**spot-only**: the venue balance must actually hold the inventory, otherwise the
Heart blocks it as a naked short.

### Optional stop loss

Enable per agent under **Agents → Settings → Stop loss**. It is an explicit
opt-in because it is the one path that may sell at a loss.

- **stop_mode** — `price_watch` (fixed level) or `trailing` (level rides up with
  price).
- **stop_loss_pct** — a flat stop %; blank uses the decider's structural
  `pos["stop"]`.
- **trail_pct** — trailing distance as % of the **entry price**; blank uses the
  initial stop distance.
- **watch_pct** — how near (%) price must come before the stop limit is armed.

Rules that always hold: both modes rest a **maker LIMIT** (never a market dump);
a trailing level is `max(high_water − entry·trail_pct%, structural_stop)`; and a
still-gated profit exit cancels the resting stop and places the profit-exit limit
instead. Paper fills are honest — if price gaps through the level, the paper fill
books the worse of level/bid, not the idealized level.

### Entry orders, TIF & reconciliation

- Entries default to **limit**, with **GTD** time-in-force and a **600 s**
  expiry. GTC rests until cancelled; IOC/FOK settle immediately; the market order
  type exists but is off by default (maker fees are cheaper).
- The loop **reconciles with the venue** at boot and on every sweep: orders that
  filled or cancelled while BlackHorse was down are settled locally, external
  manual sells close/shrink the local position, and orphaned resting exits are
  cancelled. A balance read that comes back unknown (`{}`) never touches
  positions.
- State lives in `logs/trading.db`, `logs/exit_watch.json` and
  `logs/pending_exits.json`.

---

## Settings map — field → where → default

**House-wide knobs** (GUI first, file second):

| Field | Where | Default | What it does |
|-------|-------|---------|--------------|
| `master_enabled` | top bar / `config/skin_settings.json` | ON | The one global ON/OFF for the house. |
| `autonomous` | Trade → Autonomy / `config/trading.json` | gate-enforced | Let the loop trade by itself. Does not imply live. |
| `mode` | Trade → Money / `config/trading.json` | **PAPER** | Paper fills vs real venue orders. |
| `watchlist` | `config/trading.json` | `["DOT/USD"]` | Pairs the loop sweeps. |
| `interval_seconds` | Trade → loop / `config/trading.json` | 300 | Fallback cadence; `logs/loop_control.json` overrides it live. |
| `exchange_watch_seconds` | `config/trading.json` | 21600 | How often exchange metadata/fees are refreshed (6 h). |
| `hold_log_gap_s` | `config/trading.json` | 0 | `0` logs every HOLD/SKIP; a positive value throttles them. |
| `fee_pct_per_side` | `config/decider.json` | 0.80 | Fee assumption for sizing and the edge gate. |
| `require_proven_edge` | `config/decider.json` | false | Global fallback for the per-agent edge-proof checkbox. |
| `rungs_only` / `rung_require_all` | `config/decider.json` | false / `ALL` | Default rung behavior for new/unspecified agents. |
| `ma_gate_interval` | `config/decider.json` | 15m | Default interval for the spread/MA gates. |
| `entry_order_type` / `entry_time_in_force` / `entry_expiry_seconds` | `config/decider.json` | limit / GTD / 600 | Entry order mechanics. |
| `exit_min_net_pct` | `config/decider.json` | 0.25 | Minimum net profit for a profit exit. |
| `exit_requote_seconds` | `config/decider.json` | 20 | Re-rest an unfilled maker exit after this. |
| `stop_loss_enabled` | `config/decider.json` | false | Stop-loss default (the house rule is no stops). |
| caps & hard bans | `heart/policies/rules.json` | see Quick start | Heart's hard limits; cannot be disabled by the brain. |
| `autonomy_unlock` | `heart/policies/rules.json` | creds + heart + backtest; paper period off | Checklist that must be green before autonomy arms. |

**Per-agent knobs** (Agents → Settings; stored in `config/agents.json` →
`policy_overrides`):

| Field | Group | Default | What it does |
|-------|-------|---------|--------------|
| `pairs` | Where it trades | empty = every pair | Pairs this agent may touch. |
| `mode` | Where it trades | PAPER | Per-agent PAPER/LIVE, independent of the others. |
| `strategy` | Where it trades | inferred | `swing` / `daytrade` / `custom` — fills the interval list. |
| `intervals` | Where it trades | entry interval | Smallest = entry lane; every larger one = climb rung. |
| `capital` | Where it trades | 0 | Sizing budget, bounded by real free cash. |
| `rungs_only` | Rungs only | OFF | Bypass the other signal gates; rungs alone decide. |
| `rung_require_all` | Rungs only | ALL | `ALL` = every rung, `ANY` = one rung is enough. |
| `star_alignment` | Strength climb | OFF | Entry lane must be eligible before it may move. |
| `entry_strength_max` | Entry | 0.25 (per interval) | Top of the buy band (`0` = floor only). |
| `exit_strength_min` | Exit | 0.75 (per interval) | Floor of the sell band (`1` = roof only). |
| `min_reward_risk` | Entry | 1.2–2.0 (per interval) | Minimum target/invalidation distance. |
| `risk_per_trade_pct` | Entry | 1.0–2.0 (per interval) | Risk used for sizing. |
| `max_position_pct` | Entry | 10–30 (per interval) | Largest single position as % of capital. |
| `stop_max_pct` | Entry | 1.5–5.0 (per interval) | Caps the structural stop distance. |
| `entry_order_type` / `entry_time_in_force` / `entry_expiry_seconds` | Entry | limit / GTD / 600 | Entry order mechanics. |
| `exit_min_net_pct` / `exit_price_sets` | Exit | 0.25 / `average` | Profit-exit threshold and price candidates. |
| `stop_loss_enabled`, `stop_mode`, `stop_loss_pct`, `trail_pct`, `watch_pct` | Stop loss | OFF | The opt-in protective exit. |
| `ma_gate_interval`, `require_spread_agreement`, `require_ma_cross_agreement` | Advanced | 15m / OFF / OFF | The spread and MA gates and which interval feeds them. |
| `ma_cross_fast/slow`, `ma_cross_min_gap_pct` | Advanced | 20/50, 0.15 | MA cross pair and coil exemption. |
| `spread_ma_fast/slow` | Advanced | 10/30 | Spread-analysis context pair. |
| `require_proven_edge` | Safety | OFF | Refuse intervals without a measured fee-honest edge. |

> Per-interval defaults (buy/sell bands, R:R, stop, risk) are the table above in
> *Agents, intervals & the strength climb*. Any per-agent value overrides the
> interval default for that agent only.

---

## Files & layout

```
app/        PyQt6 desktop app (main.py + agents_ui.py + ui_util.py)
trade/      the loop, agents, decider, broker, heart, exchanges, portfolio
skin/       defense/monitoring layer (dry-run by default)
heart/      referee policies (rules.json) + playbooks
memory/     permanent + ephemeral memory (disk-first)
config/     agents, treasury, exchanges, decider/heart rules, settings
logs/       signals, exit_watch.json, trading.db, threats.jsonl, shutdown.json
docs/       design notes and snapshots
plugins/    extra abilities (watchlist, imagemagick)
install_launcher.sh   optional desktop launcher
```

Key files:

- `config/agents.json` — the agents and their per-agent `policy_overrides`.
- `config/trading.json` — `autonomous`, `mode`, `watchlist`, interval seconds,
  and `hold_log_gap_s` (0 = log every HOLD/SKIP decision each cycle; omit or set
  a positive number of seconds to throttle them).
- `config/decider.json` — decider policy, band defaults, stop-loss defaults.
- `config/signal_setup.json` — SignalBot pair + channels.
- `config/skin_settings.json` — `master_enabled`, engines, LLM/brain knobs.
- `config/treasury.json` — capital, holdings, reserve.
- `heart/policies/rules.json` — Heart caps, hard bans, autonomy unlock.
- `logs/signal_latest.json`, `logs/signals/<PAIR>.json` — SignalBot output.
- `logs/trading.db` — positions, orders, decisions, P&L.
- `logs/exit_watch.json` — resting exits the loop is watching;
  `logs/pending_exits.json` — live exits awaiting fill/requote.
- `logs/loop_state.json` — loop heartbeat; `logs/shutdown.json` — shutdown journal.
- `logs/loop_control.json` / `logs/loop_trigger.json` — live speed and
  **Decide now** requests from the GUI.
- `logs/exchange_info.json` — cached venue metadata and account fee tier;
  `logs/fee_cache.json` — the last fee read.

Everything is a file, so you can edit config by hand or via the GUI; both re-read
on the next cycle. When the GUI writes config and the loop reads it, the change
lands on the trader's **next cycle** (≤ your loop interval).

---

## Maintenance & CLI

```bash
python3 app/main.py                     # the desktop app
python3 trade/loop.py --once            # one trading cycle
python3 -m py_compile app/*.py trade/*.py   # quick syntax check of touched files
```

There is no formal test suite; changes are verified with `py_compile` plus
headless (offscreen) smoke renders of the GUI against live data.

The old conversational entry point (`./spice`) and the Console page were removed —
BlackHorse is trading-only. A leftover `spice` launcher
file may still sit at the repo root; it is not wired to anything.

---

## Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Agent keeps deciding `HOLD` | No setup cleared every gate | Agents → Decisions → **Why it is (or isn't) entering**. The reason list names each blocking gate. |
| `rungs only: no climb rungs active` | Rungs only is ON but the climb is off | List at least one interval **above** the entry lane, or turn Rungs only off. |
| Rung agreement combo is missing | Rungs only is unchecked | Tick **Settings → Rungs only**; the ALL/ANY combo then appears. |
| Agent_6 seems to read 15m, not its 60m lane | The **MA/spread gate interval** is 15m by default | This is expected. Set **Advanced → MA/spread gate interval** if you want the gates to read another interval. |
| Signal says "entry eligible" but no order | `entry eligible` is SignalBot's raw range read, not BlackHorse's decision | Check the fee target, R:R, rung bands, proven-edge gate and Heart caps in the decision reasons. |
| Loop feels slow | Serial per-pair SignalBot scans + the cycle cadence | Lower **every N s** (15 s minimum), press **⚡ Decide now**, or reduce the watchlist. A full sweep still costs one SignalBot pass per pair. |
| GUI closed but the app is still running | Closing hides to the tray | Use the tray → **Quit BlackHorse**; the loop keeps running independently. |
| Funds look "stuck" in a position | Bot was down or the position was closed manually on the venue | The loop reconciles at boot and every sweep; run `python3 trade/execute.py --reconcile` to force it. |
| Stops never fire | Stops are **OFF** by default (house rule: never sell at a loss) | Enable per agent under **Settings → Stop loss**. Profitable exits still work. |
| Live order refused | Mode, credentials, Heart cap, or spot-only block | Read the refusal text; check the Heart page and the exchange credentials. |
| Decisions have no brain verdict | Brain service is intentionally off / budgeted | The decider still works; brain gates only apply when a verdict exists. |

---

## Secrets

The secret vault is **never** committed. `config/exchange_keys.json` and
`config/.master_key` are gitignored; the optional vault encrypts secrets at rest.
Keep it that way. `config/treasury.json` (money state), `config/agents.json`
(the house roster) and `config/trading.json` (live switches + watchlist) are
**gitignored too** — they are your private house state, not project files.
Fresh clones get sanitized templates: `config/agents.example.json`,
`config/trading.example.json`.

---

## Safety & scope

- Blocking/killing acts on **this host only** — never a remote machine.
- Defense is dry-run until explicitly enabled.
- Agents act only on evidence, and only when the Heart allows it.
- Spot only, fully funded: no margin, leverage, futures, or shorting.
- The loop is fail-closed and shutdown never moves money.

### Sister project

**Mr.Fluffy** (`~/mrfluffy`) reads BlackHorse's files to build market views and
news/readout verdicts; BlackHorse depends on neither. "The wire" and the six market
scenarios originate in Mr.Fluffy (`wiregate.py`) and are mirrored read-only in the
Agents → Decisions tab. If you want a brain/news readout, run it there:
`cd ~/mrfluffy && python3 daemon.py --once`.

---

## Docs

The `docs/` folder holds the design notes: `STRENGTH_CLIMB.md` (intervals as
profiles), `decider_design.md`, `ENTRY_GATE_FINDINGS.md`, `market_signal_extraction.md`,
`IMPATIENCE.md`, `PLAYBOOK_MTF.md`, `SNAPSHOT.md`, `TRADE_PROGRESS.md`,
`WHATS_LEFT.md`, `SESSION_NOTES.md`, `BRAIN_BLUEPRINT.md`, and more.

---

## License

MIT — see [LICENSE](LICENSE). © 2026 Mr.Fluffy.

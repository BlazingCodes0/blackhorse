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
  sidecar (no reimplementation), then shows the per-interval read. Set the
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
  - **Where the market is** — SignalBot's per-interval read.
  - **Scenarios** — the six-scenario rubric with the active one marked `◀`.

  The Decisions tab is **read-only** — it reads the `decisions` table and the
  bot's written signal snapshots and never runs the bot or trades; it refreshes
  every 5s while visible.

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

- Pressing a button makes it the **entry lane**; it and everything smaller grey
  out. Choosing an interval loads that interval's **defaults** into the band
  fields (buy ≤ / sell ≥ / R:R / stop / risk / max position).
- Buttons strictly larger stay clickable as **climb toggles** (green ✔ = rung in
  the climb). Each rung shows its own `60m rung · buy ≤ … · sell ≥ …`.
- **Save to agent policy** writes `strategy_intervals`, `channels` and `strategy`
  from the buttons. Band overrides are stored as
  `entry_strength_max_<iv>` / `exit_strength_min_<iv>`.

Conflicts between agents are reconciled by the **broker**, and every resulting
action still has to pass the **Heart**. See `docs/STRENGTH_CLIMB.md` for the full
design note.

---

## Stop loss (opt-in)

The house rule is **never sell at a loss** — so stops are **OFF by default**.
Enable per agent under **Agents → Settings → Stop loss**.

- **stop_mode** — `price_watch` (fixed level) or `trailing` (level rides up with
  price).
- **stop_loss_pct** — a flat stop %; blank uses the decider's structural
  `pos["stop"]`.
- **trail_pct** — trailing distance as % of **entry**; blank uses the initial
  stop distance.
- **watch_pct** — how near (%) price must come before the stop limit is armed.

Rules that always hold: both modes rest a **maker LIMIT** (never a market dump);
a trailing level is `max(high_water − entry·trail_pct%, structural_stop)`; and a
still-gated profit exit cancels the resting stop and places the profit-exit limit
instead. Paper fills are honest — if price gaps through the level, the paper fill
books the worse of level/bid, not the idealized level.

---

## Files & layout

```
app/        PyQt6 desktop app (main.py + agents_ui.py)
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
- `config/trading.json` — `autonomous`, `mode`, `watchlist`, interval seconds.
- `config/decider.json` — decider policy, band defaults, stop-loss defaults.
- `config/signal_setup.json` — SignalBot pair + channels.
- `config/skin_settings.json` — `master_enabled`, engines, LLM/brain knobs.
- `config/treasury.json` — capital, holdings, reserve.
- `heart/policies/rules.json` — Heart caps, hard bans, autonomy unlock.
- `logs/signal_latest.json`, `logs/signals/<PAIR>.json` — SignalBot output.
- `logs/trading.db` — positions, orders, decisions, P&L.
- `logs/exit_watch.json` — resting exits the loop is watching.
- `logs/loop_state.json` — loop heartbeat; `logs/shutdown.json` — shutdown journal.

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

## Secrets

The secret vault is **never** committed. `config/exchange_keys.json` and
`config/.master_key` are gitignored; the optional vault encrypts secrets at rest.
Keep it that way. `config/treasury.json` (money state) and `config/agents.json`
(runtime policy) are ordinary config and are usually left out of commits too.

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

MIT — see [LICENSE](LICENSE). © 2026 Mustafa Bashiir Ahmed (Mr.Fluffy).

# BRAIN AUTONOMY PLAN — trading + own channel on a small budget

Status: discussion → build. Owner: you. Executor: brain (opencode) under Heart gate.
Target box: N3540, 4 GB RAM. Rule for every choice below: it must run here.

## 0. Mission
Give the brain a small budget, the ability to trade and run its own content
channel, and enough autonomy to grow that budget — while never being able to
hurt the user, break the law, or move money the user did not allocate.

Autonomy is NOT "no rules". Autonomy is **bounded freedom with a referee**.

## 1. Org chart (who is allowed to do what)
| Layer  | Role                              | Can it contradict the layer below? |
|--------|-----------------------------------|------------------------------------|
| Skin   | watch the machine for *threats*   | no                                 |
| Heart  | **filter/veto** every action      | **no — it is the referee**         |
| Brain  | decide, plan, talk, call opencode | yes (proposes)                     |
| Motar  | workers that actually do the task | no (executes only approved work)   |

Hard rule: **nothing reaches the outside world (an order, an upload, a post, a
transfer) without a Heart stamp.** The brain can propose anything; Heart can
block anything; the brain cannot switch Heart off.

## 2. The autonomy loop (the heartbeat)
```
wake (heartbeat)
  -> OBSERVE   : market data, channel stats, threat log, wallet/balance
  -> THINK     : brain (opencode) picks 1 goal + N candidate actions
  -> FILTER    : Heart checks each action vs policy + hard caps + dead-man
  -> ACT       : Motar executes approved actions only
  -> LEDGER    : write every decision + outcome to the hash-chained log
  -> SLEEP     : back under budget (minutes, not seconds)
```
The dead-man switch (in Heart) beats this loop. **If the heartbeat stops, money
loops park themselves. Fail-closed, always.**

## 3. Capability A — Trading
Money-mover of record: **qubitFlowBeta** (Node/Express/pg) stays the executor.
Backtesting/research: **LEAN** before anything is deployed live.

Brain's job is *decision*, not execution:
- pull market data + sentiment (motar/trading)
- propose strategy params / entry-exit / position size
- LEAN backtest the proposal; reject anything that fails its own test
- hand the survivor to Heart, then to qubitFlow to execute

Hard caps live in code, NOT in prompts (LLM cannot override these):
- `max_position_pct`, `max_order_value`, `max_open_positions`
- `max_daily_loss`, `max_drawdown_pct` → breach = auto-halt + human ping
- `max_orders_per_min`, `allowed_venues` whitelist
- loss-streak / API-error / stale-price / latency breakers

Autonomy policy: read + paper-trade + backtest = fully autonomous.
Live orders = autonomous only inside caps; cap breach = hard stop.
Moving funds between accounts / withdrawing = **never autonomous.**

## 4. Capability B — Own channel (start: YouTube; later products)
Pipeline (motar/youtube):
```
idea -> outline -> script -> assets (voice/thumb) -> Heart review -> upload queue -> publish -> stats -> learn
```
Brain picks topics from trends + its own performance history; the channel is a
soft-money engine (ads/affiliate/sponsorships) that also feeds the trading pot.

Heart gates on content specifically:
- no hard financial advice / no "guaranteed returns" (base_rules #1)
- no PII leaks (#3), no copyrighted assets without license (#4)
- disclose AI-generated where platforms require it (#5)
- no spam / ToS-violating upload or outreach rates (#2)
- uncertain = block + human review (#6)

Reversibility rule: uploads/posts are *recoverable*, so they can be autonomous
**inside rate limits**. Irreversible things (delete channel, sign contracts,
spend on ads) need a human tap.

## 5. Capability C — The budget (treasury)
A small budget, tracked as a first-class object, not a vibe.

- `config/treasury.json`: `{allocated, cash, open_risk, realized_pnl, channel_revenue, currency}`
- Every spend debits the ledger; the brain cannot spend more than `cash`.
- Reinvestment policy (deterministic, editable):
  - e.g. 50% of profit compounds into trading, 30% into channel, 20% untouched reserve
  - reserve line = absolute floor the brain may never touch
- `max_daily_loss` and `max_spend_per_day` are dollar caps the brain cannot see
  around. Breach → freeze + notify.
- Start in **paper mode**: treasury is real bookkeeping, trades are simulated,
  channel is private/unlisted. Flip one flag to go live once the loop is proven.

## 6. Heart (the fighter on standby) — build first, not last
Heart needs to exist *before* Motar moves money. Minimal viable Heart:
1. `heart/policies/rules.json` — machine-readable caps + hard bans
2. `heart/filter.py` — `approve(action) -> ALLOW / BLOCK / REVIEW`
3. graded breakers — SOFT (pause a stream) / HARD (stop all money movement)
4. dead-man switch — supervisor heartbeat; missing beat = park the loops
5. `heart/review/` — pending queue for human tap on irreversible actions
6. action ledger — every irreversible action hash-chained (reuse threats.jsonl
   chain pattern from skin/threat_log.py) for replay/audit

Authority separation: Heart runs as its own tiny process. The brain talks to it
through the review queue; it has no API to disable it. This is intentional.

## 7. opencode (brain) usage — do not abuse the expert
Reuse what we just built:
- every opencode call passes `skin/brain_budget.py` (shared, disk-backed)
- periodic sweep (minutes) to reason; escalate only real/ambiguous events
- cheap local tier (qwen via Ollama) for busywork; opencode for judgment
- full brain usage: goal selection, strategy reasoning, channel scripting,
  post-mortems. NOT per-tick market polling.

## 8. Phases (each phase ends in something runnable, on this box)
- **P0 — Guardrails**: Heart policy + filter + breakers + ledger + treasury file.
  (Blocks: nothing may move money until P0 is green.)
- **P1 — Paper trader**: market data ingest, LEAN backtest hook, paper orders,
  ledger everything. No real capital.
- **P2 — Channel v0**: idea→script→asset pipeline, Heart review, private upload.
- **P3 — Small live budget**: tiny real allocation, live caps enforced, daily
  report. Channel public with rate limits.
- **P4 — Autonomy**: brain picks goals across both engines within caps; human
  only handles REVIEW items and cap breaches.
- **P5 — Compound**: reinvestment policy active, performance memory informs next
  cycles (brain/memory).

## 9. Hard constraints (non-negotiable)
- Fail-closed: no heartbeat ⇒ no money movement.
- Brain cannot disable Heart, raise its own caps, or withdraw funds.
- Every irreversible action is ledgered and replayable.
- Uncertain + irreversible ⇒ human review, not a coin flip.
- Local-only decision data where possible; no secrets in prompts or public artifacts.
- Must fit 4 GB RAM: no llama-sized models, no always-on heavy services.

## 10. Open questions for you (decide before P3)
1. Real budget size + absolute reserve floor?
2. Venues: qubitFlow only, or also exchange APIs directly?
3. Channel: YouTube first? niche? post cadence?
4. Live-risk ceiling you're comfortable losing to prove the loop?
5. How much autonomy is "too much" — which actions always need your tap?

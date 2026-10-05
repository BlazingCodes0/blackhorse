# BLACKHORSE SESSION NOTES — full working record

Saved so the discussion survives a dropped conversation. Date: 2026-10-04.
Everything below was actually done or decided in this session, in order.

Companion files written this session:
- `brain/AUTONOMY_PLAN.md`              — trading + channel + budget plan
- `heart/ROGUE_PLAYBOOK.md`             — fighters-on-standby / rogue-bot playbook
- `docs/qubitflow_review.md`            — blunt review of <SignalBot project>
- `docs/market_signal_extraction.md`    — market-data + SignalBot extraction design
- this file                             — the whole session

---

## 1. Stopped BlackHorse (was abusing opencode)
- Only process running: `python3 ~/blackhorse/app/main.py` (PID 2513), a
  PyQt6 GUI. Killed it (SIGTERM ignored → SIGKILL). Left opencode itself alone.
- Root cause of the "rate limit" pain:
  - `whitecells.py` asked LOCAL Ollama FIRST for EVERY alert; rules were fallback.
  - `mastermind.py` daemon piped EVERY ALERT/CRITICAL into `opencode run` with NO
    cooldown, 180s timeout. Logs showed `Rate limit exceeded`, socket closed,
    timeouts.
  - Feedback loop: opencode + LOCKDOWN flag + settings writes land in `config/`,
    which watchdog/integrity watch → new ALERT → LOCKDOWN → CRITICAL → opencode.
  - `max_llm_calls_per_min: 0` was treated as UNLIMITED; counter in-memory only.
  - Wrong model (`mistral`, not installed); bare `opencode` missing from PATH.

## 2. Rebuilt the brain / rate-limit architecture (DONE)
Files added/edited in `skin/`:
- `brain_budget.py` (NEW) — one shared, disk-backed, flock'd budget. Min gap
  (300s) + rolling hourly cap (10). `allow()/status()/reset()`.
- `brain.py` (NEW) — opencode client + periodic sweep + escalation inbox +
  daemon; every call passes brain_budget. Bidirectional (skin→brain and
  brain→skin).
- `mastermind.py` — now a thin shim over `brain.py`.
- `whitecells.py` — RULES FIRST; local model only when confidence < 0.7;
  escalate unresolved to `logs/brain_inbox.jsonl`; self-noise filter kills the
  LOCKDOWN/settings feedback loop; `0` now means OFF (not unlimited).
- `settings.py` — added brain keys, fixed default cap, master gate support.
- `defense.py` — honors master switch.
- `config/skin_settings.json` — sane live values (see file).
Design: rules are primary (instant, free). Local qwen = cheap ambiguous-triage.
opencode = expensive expert tier, minutes-spaced. Never per-event.

## 3. Master ON/OFF + tray (DONE)
- Single master switch `master_enabled` gates engines, whitecells, local model,
  escalations, and the brain. Verified from disk after toggle.
- Closing the window HIDES to a system tray icon; app runs until "Quit" in the
  tray menu. (`setQuitOnLastWindowClosed(False)` + `closeEvent` ignore+hide.)
- GUI: big master bar (green ARMED / red "SYSTEM DISARMED — TRULY OFF"),
  Dashboard "Verify OFF now" panel proving: disk flag, engines, model, brain,
  escalation, systemd service state, stray defense workers, stale LOCKDOWN flag.
- Note: stale `config/LOCKDOWN` flag still present (cosmetic; can clear).

## 4. Autonomous trading + channel + budget (PLANNED → brain/AUTONOMY_PLAN.md)
- Org chart: Skin=threats, HEART=filter/veto (cannot be disabled), Brain=decide,
  Motar=workers. Nothing reaches the outside world without a Heart stamp.
- Loop: wake→observe→think→Heart filter→act→ledger→sleep; dead-man switch;
  fail-closed.
- Trading: qubitFlow stays the money-mover; LEAN backtests; opencode decides.
  Hard caps in CODE (position/order/daily-loss/drawdown/rate/venue whitelist).
- Channel: idea→script→assets→Heart review→upload→stats→learn.
- Budget: `config/treasury.json`; reserve floor; reinvest split; start PAPER.
- Phases P0 guardrails (blocks everything) → P1 paper → P2 channel → P3 live
  budget → P4 autonomy → P5 compound.
- opencode use stays budgeted (skin/brain_budget.py).

## 5. "Fighters on standby?" (PLANNED → heart/ROGUE_PLAYBOOK.md)
- NO AI fighter (a second bot is a second rogue). The fighter is a deterministic
  referee: dead-man switch, cap breaker, behavior-anomaly watcher, ledger
  verifier, + human tap queue. Authority separate from intelligence.
- Rogue is usually behavior (retry storm, stale feed, constraint-gaming, leaked
  key), not malware → needs rate/shape sensors, not signatures (Heart's job).
- Graded response L0 observe → L1 pause stream → L2 halt money → L3 lockdown.

## 6. qubitFlowBeta review (→ docs/qubitflow_review.md)
Scale ~90k lines; Express+pg+vanilla JS. Verdict: strong PRODUCT, not yet SAFE
AUTONOMY. Best parts: AES-256-GCM credential crypto (rejects withdrawal keys),
session/CSRF/2FA, ledger+reconciliation, exchange precision, signal ensemble.
Worst parts:
1. **Kill switch is DEAD CODE** — `getRiskStatusAdapter` computes daily
   loss/drawdown/killSwitchActive but NOTHING calls it; `placeTradingOrder` has
   no risk gate. Most dangerous fact for autonomy.
2. No rate limiting / no idempotency at the API edge.
3. Strategy edge unproven (their own MARKET_STRUCTURE_RESEARCH says no
   fee-adjusted winner).
4. `npm test` BROKEN (references deleted test/marketStructureResearch.test.js).
5. SignalBot black box, default path on a USB mount, ~12MB stateful JSON.
6. Two parallel order systems; 1450-line runtime monolith.

## 7. opencode vs lightweight open-source trading (DISCUSSION)
- Different categories: opencode = reasoning brain; frameworks = deterministic
  plumbing. Use both, LLM on top.
- Frameworks: Freqtrade (GPL-3.0, heavy, best all-round), Jesse (MIT, light),
  NautilusTrader (LGPL-3.0, Rust core, pro), Hummingbot (Apache-2.0, MM/arb),
  OctoBot (GPL-3.0), Backtrader/vectorbt (research), CCXT (MIT, glue).
- qubitFlow ALREADY is Freqtrade-class for execution; what frameworks add is
  battle-tested BACKTESTING and proven risk plumbing.
- Recommendation: keep opencode as brain; don't rewrite on a framework; borrow
  a real backtester (Freqtrade/vectorbt or fix qubitFlow's harness); CCXT is the
  truly lightweight always-on option for a 4GB box; Heart stays the referee.
- License caution for shipping: GPL (Freqtrade/OctoBot) is viral; MIT/Apache
  (Jesse/Hummingbot) friendlier.

## 8. Market-data + SignalBot extraction (→ docs/market_signal_extraction.md)
- REUSE (read-only): `src/services/{kraken,coinbase,binance}Market.service.js`
  (ticker, pair catalog w/ orderMin/costMin/precision, order book);
  `tradingBot/marketData/ohlcHub.js`+`tickerHub.js`; `SignalBot/`.
- DROP: all tradingBot orders/execution/ledger/adapters, *Execution services,
  exchangeRouter, billing, auth, UI, Postgres.
- Signal contract: motar channels (ripple/wave/tide) → decision, factorResult
  (floor/low/high/roof/strength/scores/eligibility), marketStructure, grids.
- THE SEAM: SignalBot honors `global.__QF_MARKET_DATA_OVERRIDES` → feed candles
  ONCE so signal and execution see identical prices.
- Architecture: thin Node sidecar (SignalBot is Node, BlackHorse Python):
  sidecar getMarket()/getSignal(); Python feed.py/signal.py/decide.py/Heart/
  execute.py/ledger.py.
- Gotchas: USB path, stateful JSON, broken npm test, ccxt already installed, two
  market caches.

---

## OPEN DECISIONS (need answers before building)
From autonomy plan:
1. Real budget size + absolute reserve floor?
2. Venues: qubitFlow only, or direct exchange APIs?
3. Channel: YouTube first? niche? cadence?
4. Live-risk ceiling to prove the loop?
5. Which actions ALWAYS need a human tap?

From market/signal extraction:
6. Node sidecar (reuse SignalBot exactly) vs pure-Python rewrite?
7. Permanent location for SignalBot (must leave the USB mount)?
8. Kraken-only first?
9. Consume all six intervals as features, or only traded rows?
10. SignalBot state: persist+version, or stateless per call?

## IMMEDIATE NEXT STEPS (proposed)
- P0: build minimum viable Heart (policy JSON + filter.py + breakers + ledger +
  treasury) — nothing moves money until this is green.
- Extraction slice: move SignalBot into repo, sidecar getMarket/getSignal, print
  a normalized BTC/USD snapshot across all six intervals. No orders.
- Fix qubitFlow `npm test`; add the risk gate into the order path; add
  idempotency keys + edge rate limiting; get a fee-honest out-of-sample result.

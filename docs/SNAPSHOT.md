# BLACKHORSE — CONVERSATION SNAPSHOT

Date: 2026-10-04 (session 2). Full arc of this working session, decisions, the
system as built, and the honest findings. Companion docs: SESSION_NOTES.md,
WHATS_LEFT.md, BRAIN_BLUEPRINT.md, decider_design.md, market_signal_extraction.md,
qubitflow_review.md, TRADE_PROGRESS.md.

---

## 1. What this session set out to do
Turn BlackHorse from a threat-monitor into an autonomous money system whose BRAIN
is opencode, with agents as "bodies" — some trade, some run channels/voices —
that can make real money to fund building the rest ("this is you building lol").
Hard user rules that shaped everything:
  * opencode must NOT be spammed; calls are minutes-spaced and budgeted.
  * config-driven exchanges, nothing hardcoded.
  * GUI for everything; closing the window must NOT stop the app.
  * memory = permanent (never deleted) + ephemeral (must be deleted), DISK-first.
  * agents bring EVIDENCE; stronger logical case wins; otherwise nothing moves.
  * SPOT ONLY — no interest, margin, leverage, borrowing, futures, shorting.
  * Do NOT trade derivatives, but DO read them (OI, funding, long/short) as
    market information.
  * Make conclusions evidence-first and fee-honest ("the beautiful conclusion").

## 2. What was fixed first (brain abuse)
The old system asked opencode per-event with no cooldown and leaked trade
prompts into the user's chat (same session). Fixed:
  * skin/brain_budget.py  — shared, disk-backed, flock'd budget (min gap 300s,
    max 10/hour). ONE budget for every caller.
  * skin/brain.py         — client + sweep + escalation; separate trade session
    (logs/trade_session.id) so trade prompts never enter the chat; kind-tagged log.
  * GUI master ON/OFF (master_enabled gates EVERYTHING), tray (close hides),
    "Verify OFF now" proof, and a stateful active-defense button.

## 3. The agents / brain-house architecture (built)
"BlackHorse is the brain house; agents are the bodies."
  * trade/agents.py — create/enable/capitalize agents; profiles = personalities:
      quick (scalper, ripple/wave, tight stops) and
      slow (HTF/tide 1440m, patient, best average price).
      kinds: trading | youtube (scaffolded).
  * trade/decider.py — signal + brain verdict + treasury -> ONE sized plan.
      Rules in config/decider.json (data, not code). strength~0 = buy zone,
      strength~1 = sell zone. Fee-aware. Emits EVIDENCE per plan.
  * trade/broker.py — reconciles enabled agents. Agreement -> strongest;
      CONFLICT -> each side brings evidence, stronger case (conviction x R:R) WINS
      if it clears conflict_margin, else HOLD "nothing moves". Every action then
      passes HEART.

## 4. The referee (built)
  * heart/policies/rules.json + trade/heart.py — ALLOW/BLOCK; hard bans
    (withdraw/transfer/unknown venue), caps (position, open positions, daily
    loss, drawdown, order rate/value), fail-closed dead-man (no heartbeat ->
    PARK), graded levels, and the autonomy-unlock checklist.
  * SPOT-ONLY enforced: margin/borrow/leverage>1x/FUTURES/PERP/SHORT/BORROW/
    INTEREST = BLOCK; SELL without held inventory (naked short) = BLOCK.
  * engine.governance.autonomous is driven by heart.unlock() (creds+heart+
    backtest+paper). Currently LOCKED.

## 5. Memory (built)
  * memory/index.py + retention.json — disk-first (SQLite FTS5). PERMANENT zone
    (lessons/rules/trades/decisions/digests) never auto-deleted; EPHEMERAL zone
    (observations/snapshots/debug) deleted after TTL. Raw JSONL gzip->delete.
    Measured ~15 MB RAM flat. Runtime db gitignored.

## 6. Capital, execution, loop, console (built)
  * trade/treasury.py — the "100": append-only capital ledger; allocate/debit/
    credit/reserve; refuses spends below the reserve floor.
  * trade/portfolio.py — positions + P&L; honest paper fills (slippage+fees).
  * trade/execute.py — plan -> paper fill; RE-CHECKS Heart (fail-closed); LIVE
    refused in this build.
  * trade/loop.py — autonomous heartbeat: heartbeat->observe->think->decide->
    gate->mark->act->record. Fail-closed. GUI Start/Stop loop.
  * trade/console.py — stateful chat: "I gave it 100" actually allocates;
    "what's up" answers with capital/positions/P&L/market; free text goes to the
    brain WITH full context.

## 7. Market data + SignalBot (built)
  * trade/sidecar/signal.js + trade/signalbot.py — run the REAL SignalBot (Node),
    compact -> logs/signal_latest.json. ~13s, ~18 KB, 3 channels.
  * trade/news.py — F&G, global cap/dominance, prices, headlines.
  * trade/crowd.py — READ-ONLY derivatives sentiment (OI, funding, long/short).
    tradeable:false. Fed to the brain as information, never as a venue.
  * config/exchanges.json + trade/exchanges.py + trade/vault.py — AES-256-GCM
    encrypted keys; exchange registry (Kraken/Coinbase/Binance), no hardcoding.

## 8. THE FINDING (the most important result)
Measured the REAL SignalBot signal's forward return NET of fees+slippage
(edge_scan.py, trade/edge.py, logs/edge_report.json):
  interval   BUY win%  BUY net    SELL win%  SELL net
  1m/5m/15m  1-22%     -0.5..-0.7 1-22%      -0.5..-0.6   LOSING
  60m        36.4%     -0.33%     41.7%      -0.43%       LOSING
  240m       44.3%     -0.04%     50.4%      +0.08%       UNPROVEN
  1440m      42.6%     -1.28%     67.1%      +1.79%       PROVEN (only edge, n=76)
Conclusion: the ONLY proven post-fee edge is 1440m SELL. Everything below 240m is
fee-eaten. The scalper premise is disproven by data.
This is now a STANDING DISCIPLINE: decider's evidence gate (require_proven_edge)
refuses any interval+side not PROVEN; the brain is instructed to reason
evidence-first and quote the measured cell (verified live).

CRITICAL CONSEQUENCE: in a cash spot account you cannot SELL what you don't
hold, so the 1440 SELL edge is unreachable from flat. Resolution direction:
reframe 1440 SELL as a SPOT EXIT rule (accumulate -> exit near the daily roof),
and layer the crowd read (OI/funding/long-short) as the context for where the
move comes from. Not yet built.

## 9. Honest status
  * Packaging: strong. Brains, budget, memory, exchange vault, SignalBot feed,
    news, crowd, agents, decider, broker, Heart, execute, portfolio, loop,
    console, GUI — all built, tested, committed.
  * Truth: still PAPER; autonomy LOCKED; no live orders. The one proven edge
    (1440 SELL) is not yet tradeable under spot-only without the exit reframe.
  * Caveats: edge n=76 is small (worst -52% tail); DOT-only so far; the
    "backtest_passed" flag is not yet wired to the edge gate.

## 10. Commits this session (newest first)
bac2945 crowd: read-only derivatives sentiment
1635f7d heart+decider: spot-only hard ban
f13022d edge: measured post-fee edge as standing discipline
e550458 console: stateful brain chat + GUI loop control
710dfeb paper execution + portfolio/P&L + autonomous loop
4a2c29c broker: evidence-based conflict adjudication
3d21f93 heart: the referee
efa30ce agents: brain house + bodies + broker
9ee33e5 decider
d3e00d1 treasury + observer + memory-aware brain
d03a9a4 memory: disk-first permanent/ephemeral
f18da86 SignalBot sidecar + GUI signal panel
e311297 brain+budget rebuild + master ON/OFF + trade foundation

## 11. Next (proposed, not yet done)
  1. Per-pair edge scan (BTC/ETH/SOL/XRP/DOT +) — is any SPOT-LONG interval proven?
  2. Reframe 1440 SELL as a spot exit rule; measure accumulate->exit as a spot
     strategy; layer crowd context.
  3. Wire backtest_passed to the edge gate so autonomy can arm for proven cells;
     then paper period, then tiny live.
  4. YouTube body (the second engine / voice).
  5. Deeper broker evidence: use observer history as a scored argument.

## 12. The "beautiful conclusion" standard (now enforced)
Every trade and every brain answer should read like:
"Only proven post-fee edge is 1440m SELL (67.1% win, +1.789% net, n=76); 240m SELL
unproven; 1m-60m and 1440m BUY losing; so no entry." — quantified, net of fees,
cites the exact cell, refuses to trade without evidence.

================================================================================
# SESSION 3 & 4 UPDATE (added after the above; nothing removed)
================================================================================

## 13. Proven edge, refined
After re-scanning, the PROVEN cells are:
  * 240m  BUY  — 45.9% win, +1.261% net, n=61   (low-hit, high-payoff)
  * 1440m SELL — 67.1% win, +1.789% net, n=76   (the star)
Still LOSING: 1m-60m (both), 1440m BUY. UNPROVEN: 240m SELL.
The scalper premise remains disproven by data.

## 14. SPOT-ONLY collision (critical, unresolved)
User rule: no margin / leverage / interest / borrowing / derivatives / shorting.
Consequence: in cash spot you cannot SELL what you don't own, so the best edge
(1440m SELL) is UNREACHABLE from flat, and the natural 1440m BUY entry is LOSING.
Proposed remedy = the SPOT LIFECYCLE reframe:
    accumulate on 240m BUY (proven) -> hold -> exit on 1440m SELL (proven)
SELL becomes "distribute inventory", not "open a short". NOT yet built; must be
MEASURED first (do 240m entries find a later 1440m exit, net of fees?).

## 15. Read derivatives, don't trade them
trade/crowd.py — read-only OI / funding / long-short (Binance public, tradeable:
false). Fed to the brain + observer memory as MARKET INFO. Heart hard-bans the
execution side (no futures/perp/short).

## 16. Spice = the owner's JARVIS (built, and made DYNAMIC)
The line: Spice acts on the owner's DIRECT requests; AGENTS stay governed by the
playbook/decider/broker/Heart. Spice never bypasses that.
- trade/spice.py — talks to opencode with persona + live state + tool schema;
  model replies strict JSON {say|ask|tool}; asks for missing side/pair/amount and
  ALWAYS makes paper-vs-live explicit; then acts through gated paths.
- config/spice_persona.md — the character + the IRON RULE: "talking cannot act".
- Dynamic, not curated:
    * trade/tools.py : @tool registry; Spice's tool list is GENERATED from it.
      Adding a capability = adding a function, never editing a prompt.
    * trade/state.py : @provider registry; Spice's awareness is pluggable.
    * learned preferences (remember/recall + growth.search), fed back each turn.
    * self-extending: compose_tool (parameterized MACROS, immediate) and
      propose_tool (Python code -> pending -> OWNER approves; never self-approved).
    * self-discovering: plugins/ dir scanned; trusted loads in-process, untrusted
      runs ONLY through the jail.
- brain.ask(route=False) so Spice owns its routing.
- heart.check(manual=True): owner/Spice direct orders skip the autonomous dead-man
  switch but STILL enforce spot-only, caps, daily-loss.
- CLI: ./spice (JARVIS chat); GUI Console talks to Spice too.

## 17. Real OS jail (bubblewrap) — PROVEN
trade/sandbox.py: bwrap jail — ro-bind only /usr,/lib,/bin,/etc; tmpfs /tmp;
unshare net+pid; die-with-parent; RLIMIT_AS + RLIMIT_CPU. /home is invisible.
Proof (static scan bypassed): malicious plugin trying to list config / read
.master_key / reach network -> FileNotFoundError / FileNotFoundError / OSError.
Trust boundary: untrusted/found abilities run JAILED; money abilities run
IN-PROCESS behind Heart + spot-only + caps. Static AST scan = fast first filter;
the kernel namespace = the real wall.

## 18. The honest correction on "smart like me"
The user kept saying "smart like you". Capability + safety (tools/plugins/jail) is
NOT the same as smart. What makes the assistant useful is FOUR buildable things:
  1. CONTEXT fed in — long-horizon memory, decision journal, outcomes, corrections
  2. an AGENTIC LOOP — think -> act -> observe -> rethink (many steps), not one-shot
  3. LEARNING FROM CORRECTION — every correction becomes a durable rule, auto
  4. SELF-CHECK vs evidence — quantify, cite, refuse to fake, by default
Highest leverage: persistent, growing MEMORY of the owner + the work. That is the
difference between a command router and a mind. TO BUILD.

## 19. Database (not yet built; user wants it)
Orders/positions currently live in JSON files. Need trade/store.py — SQLite
(entity: orders, fills, positions, decisions, heart_verdicts) with IDEMPOTENCY
KEYS, reconciliation-ready, audit/replay, crash-safe. execute.py/portfolio.py
write through it. (SQLite chosen for the N3540; Postgres/Redis available but heavy.)

## 20. Next (agreed order)
DONE in order: snapshot -> DB (trade/store.py) -> Spice smart+autonomous (agentic
loop + long-term memory + learn-from-correction) -> autonomy armed + LIVE.

## 20b. LIVE IS ON (2026-10-04) — proven with real money
- config/trading.json: autonomous=true, mode=LIVE, watchlist=[DOT/USD].
- GUI Trade page: separate PAPER/LIVE toggle (refuses LIVE without credentials).
- execute._live_order(): BUY sizes by quote; SELL sells HELD inventory (balance-
  checked) -> spot-only, never a naked short. _held() feeds Heart the real balance.
- Kraken keys verified working; account real (~$<amount> USD, ~<amount> DOT initially).
- PROOF: live BUY <amount> DOT txid <txid> (USD <amount>-><amount>); live SELL
  <amount> DOT txid <txid> (DOT <amount>-><amount>, USD-><amount>).

## 20c. THE HONEST CORRECTION (session 4, late) — read this before resuming
What the user rightly called out: an earlier turn "validated" a strategy using a
HAND-ROLLED rolling-range-strength proxy (validate_lifecycle.py) and reported its
failure as if it were the system's. That was INVENTING, not observing.
THE TRUTH:
- The live signal was ALWAYS real SignalBot (the sidecar runs the real engine).
- BUG 1 (mine): signalbot.py DROPPED `marketResult` (the real MA context:
  trendState/spreadDirection/maSpreadRun/RSI/intervalRole). Real source, but I
  discarded half of it. NOW FIXED — those fields are carried through.
- BUG 2 (mine): validate_lifecycle.py was my own made-up rule, not the real gate.
  DELETED.
- The real system ALREADY contains the validators. We now RUN THEM:
    <SignalBot project>/.../SignalBot/utils/backtestRangeSignalGate.js  (the real gate)
    <SignalBot project>/.../scripts/bot-strategy-winning-test.js        (fee-honest WF)
- Real verdicts (system's own): bot-strategy-winning-test -> scalp/dayTrade/
  interday/swing ALL "PARTIAL_EDGE", each with **60m** proven. codexGateBacktest
  shows real SELL-side edge across intervals (e.g. 240m SELL horizon5 n=23
  65% win +1.26%).
- import_edge.py: loads the REAL reports into logs/edge_report.json. Proven cells
  now come from the system: **60m BUY (n=7, 100% test win, +1.63%)** and
  **60m SELL (n=16, 56.2% test, +0.32%)**.
- heart._backtest_truth(): verifies the flag hash against the REAL report; now
  reads "verified against edge_report.json".
- decider: added the REAL MA-context gate (refuse BUY on weakening spread, SELL
  on improving spread; optional trend agreement).
- agents 'lifecycle' retargeted to the PROVEN 60m interval (entry<=0.20, exit>=0.85).

## 20d. STATE AT SNAPSHOT TIME (resume point)
- mode=LIVE, autonomous=true, watchlist=[DOT/USD]
- agents: agent_1 Lifecycle $39 (on), agent_2 LiveTest30 $30 (on)
- live balance: USD <amount>, DOT <amount>
- capital pool: shared; free = real cash - committed; broker sizes at
  min(agent.capital, free); execute clips live BUY to capital.free()
- treasury: reconciles from the exchange in LIVE (shows the truth, ~$<amount>)
- gate: backtest=verified (real report); paper_period=FAIL (no closed trades yet)
  -> can_arm=false, shown as an honest WARNING (does not silently block LIVE)
- proven edges: 60m BUY, 60m SELL (from the system's own validators)

## 20e. STILL OPEN (next actions)
1. Run the system's validators per-pair (BTC/ETH/SOL/XRP) to see if 60m SELL/BUY
   holds beyond DOT — only then widen the watchlist.
2. Accumulate a real paper period (>=10 closed trades over >=6h) so paper_period
   passes honestly; then can_arm can be true.
3. Confirm the retargeted 60m lifecycle actually fires on live signal and journals
   properly; verify the MA-context gate behaves on real data.
4. The system's edge is SELL-heavy; in spot-only a SELL needs inventory, so the
   cycle is buy-low (60m BUY proven) -> sell-high (60m SELL proven).

## 21. Commits (session 3/4, newest first)
d016c7c real: import the SYSTEM's validators; restore dropped fields; rebuild on proven 60m
8471ce6 fix caveats: shared capital pool + real (unfakeable) validation gate
4e00e17 truth: live treasury reconciles from exchange; agents cannot over-allocate
b881e86 docs: snapshot — LIVE enabled and proven
c07ce48 LIVE enabled: real Kraken buy+sell proven; GUI PAPER/LIVE switch
c9f8825 autonomy armed
cc2c074 plugin discovery + real OS jail
7b04231 parameterized macros + sandboxed generated tools
7171f7a dynamic all the way down (self-registering state + self-extending tools)
5a821af make it dynamic (self-describing tools + learned preferences)
7f67549 spice: JARVIS conversational layer
1ab0377 Spice persona, growth memory, spice CLI
c2c5144 session 3: proven 240m cell, RSI lane, playbook docs, live LIVE path, router
bac2945 crowd: read-only derivatives sentiment
1635f7d heart+decider: spot-only hard ban
f13022d edge: measured post-fee edge as a standing discipline


---

## 13. Session 3 (2026-10-04, later) — playbook wiring, edge hunt, RSI lanes
Goal: get BlackHorse to real money with the user's range/adaBot playbook.

Artifacts built this session:
- `SignalBot/run_smartbt.js` — standalone smartBackTest runner; dumps
  `logs/smartbt_{15m,60m,240m,1440m}.json` (+ pretty `_dump.json`).
- `SignalBot/dump_candles.js` — Kraken candles per interval to
  `logs/candles_*.json` for RSI computation.
- `trade/edge_rules.py` — train/test scorer of range-strength + maSpread +
  trend rule cells on real SignalBot rows.
- RSI scans (inline python, `logs/sb_rows.json` + `logs/candles_*.json`) —
  added RSI(14) lane: 15m oversold-floor
  buys still fail; best RSI cell = 60m BUY @floor + RSI>=50 (n≈41,
  train +0.118 / test +2.029).
- `trade/advisor_combo.py` — manual blend (deprecated); superseded by one
  adaBot call `runAdaBot({pair, intervals:['60','240','1440']})` which returns
  combined prices + per-interval prices (see `docs/PLAYBOOK_ADVISOR_PRICES.md`).

Verdicts held:
- 15m stays noise (no passing cell). 60m = direction confirmation layer only.
- Best joint cell: **240m BUY at floor/lower zone when smartbot maSignal is
  bullish** — h20 train +0.898% / test +0.844%, n≈68, net of fees.
- Second-tier: 60m BUY @floor + RSI>=50. Both provisional until
  out-of-sample + paper n>=30.
- 1440m roof-SELL cells excluded from spot play (short-side only).
- Playbook docs: `docs/PLAYBOOK_MTF.md` (rules + per-interval status),
  `docs/PLAYBOOK_ADVISOR_PRICES.md` (advisor lanes, combined vs individual).

Known carry-over into session 4:
- Wire the two candidate cells into agents + Heart gate; paper-log them.
- Brain responsiveness: `mastermind.py`/`brain.py` lag while sweeping —
  opencode "promote to brain" promotion is slow; decide timeouts/cadence.

---

## 22. Session 5 (2026-10-04, night) — brain connection resilience + build checklist
Problem reported: opencode brain has died before (Rate limit exceeded, socket
closed, timeouts). Session 1 fix stopped the FLOOD; this adds the RESTORE path.

DONE this session:
- brain_retry_seconds=20 in config/skin_settings.json; brain.ask() now retries
  once on error/timeout before declaring failure.
- logs/brain_health.json: last_ok_ts, last_error_ts, last_error,
  consecutive_failures. 3+ consecutive failures auto-file a CRITICAL into
  logs/brain_inbox.jsonl so the skin/user sees the outage.
- logs/brain_outbox.jsonl: failed brain asks (q, kind, session, err, attempts).
  brain daemon retries up to 1 per sweep (max 3 attempts, then audit-only).
- trade_session() NO LONGER falls back to the guardian session — it returns
  None, and coach.py / console.py / brain.ask(kind=trade|console) refuse loud.
  Trade prompts can never leak into chat again, even with a missing session id.
- Deleted trade/validate_lifecycle.py (the invented validator from session 4;
  doc said deleted, file was not).

## 23. BUILD CHECKLIST (from snapshots, to finish in order)
[x] 1. Brain resilience: retry+backoff, health file, outbox, fail-loud session.
[x] 2. Delete trade/validate_lifecycle.py.
[ ] 3. Verify the brain is REACHABLE right now (ping opencode run) and
       logs/brain_health.json shows a fresh last_ok_ts.
[~] 4. Per-pair edge validation: BTC/USD smartbt dumps captured for 60/240/1440m
       (logs/smartbt_BTC_*.json, node exit 0, 721 rows each, 717 ok). BUT those
       dumps are advisor packets, not the saveRange/saveSmart rows edge_scan needs.
       REAL NEXT STEP: parameterize analyzeLogs3LowCodex SOURCES per pair and dump
       saveRange/saveSmart rows for BTC/ETH/SOL/XRP, then re-run the winning-test
       + edge_scan cells. Watchlist STAYS [DOT/USD] until then.
[ ] 5. Accumulate an honest paper period (>=10 closed trades, >=6h) so
       paper_period.flag passes; only then can_arm can go true.
[ ] 6. Confirm the retargeted 60m lifecycle agent fires on real signal and
       journals; watch MA-context gate behavior on real data.
[ ] 7. Full autonomy armed + LIVE only after 3-6 above are green.
[ ] 8. Later: YouTube body, DB already at trade/store.py — verify all
       execute/portfolio writes go through it, then broker evidence from
       observer history.

Resume point: mode=LIVE, autonomous=true, watchlist [DOT/USD], capital ~$<amount>,
agents agent_1 Lifecycle / agent_2 LiveTest30 both enabled, heartbeat ok,
decision HOLD, can_arm=false (paper_period honest-fail).

### Session 5 late update — re-arm + checklist execution state
- master_enabled flipped back to TRUE by owner; stale config/LOCKDOWN cleared.
- Brain verified reachable (manual ping ok), brain_health.json fresh.
- Checklist 4 attempted: BTC/USD smartbt dumps for 60/240/1440m complete
  (logs/smartbt_BTC_*.json, 721 rows, 717 ok). Findings:
  * new smartbt rows carry analysis/context but NOT the `decision` block or the
    `rangelog` range side that analyzeLogs3LowCodex.loadRows() needs.
  * saveRange*.json artifacts exist for DOT only; no script in repo regenerates
    the range side (strategy "rangeBound") per pair.
  * Therefore NO honest per-pair edge cell exists yet. Watchlist STAYS
    [DOT/USD]; paper_period still FAIL; can_arm stays false.
- NEXT BUILD (resume here): locate/reconstruct the rangeBound row producer
  (last seen under SignalBot/utils smartBbacktester / sendRangeSignalBot) and
  write a per-pair dumper that emits saveRange*.json + saveSmart*.json shaped
  files for BTC/ETH/SOL/XRP, then re-run the winning-test + edge_scan cells.
  Only when those cells show a fee-honest PROVEN edge may the watchlist widen.

### Session 5 — autonomy triage (user Q&A, kept verbatim)
"Spice: short answer — BlackHorse is technically LIVE (it bought and sold DOT
for real on 2026-10-04), but the autonomy gate is honestly RED... The two hard
gates, both failing:
1. paper_period=FAIL — no closed trades accumulated >=10 trades / >=6h ->
   can_arm=false. The system refuses to fully trust itself yet.
2. Watchlist still [DOT/USD] only — BTC edge cell doesn't exist (checklist 4),
   so widening would be guessing.
What remains, ordered:
1. Write the per-pair row dumper -> real 60m BUY/SELL edge cells for
   BTC/ETH/SOL/XRP (only then widen watchlist)
2. Let a real paper period accumulate -> paper_period passes honestly
3. Confirm the 60m lifecycle agent actually fires on live signal
4. Re-arm master, then can_arm=true only after 1-3 are green
5. Later: YouTube body, store.py audit
Can it trade today? It can — Kraken creds, LIVE mode, buy/sell path proven
with real money (~$<amount>). It CANNOT trade autonomously with blessing until
items 1-3. With you watching: manual/Spice-asked orders through Heart. Left
alone: it holds. No automatic watchlist widening; no autonomous arming until
the numbers say so."

---

## 24. Session 6 (2026-10-05) — box hygiene + per-pair edge cells

Box speed/cleanup:
- Kill-stuck `qwen2.5:0.5b` llama-server (was ~200% CPU / ~500MB) and set
  `llm_enabled=false` (whitecells now rules-only; re-enable when wanted).
- Removed `openclaw-gateway.service` (NOT part of BlackHorse; 19% CPU/571MB):
  unit file deleted, all node procs killed, `~/.openclaw` deleted,
  `port_baseline.json` scrubbed.
- `skin/egress.py`: process-name flap on a known ip:port downgraded ALERT->WARN
  (was the whitecell/Ollama churn source).
- `skin/net_check.py`: corrupt/torn port_baseline.json no longer crashes the sweep.
- GUI: five 2s timers -> 5s; active-defense check 3s -> 15s.

Per-pair edge cells (the real answer to "each pair its own"):
- NEW `SignalBot/run_pair_rows.js`: dumps saveRange*/saveSmart* for a pair,
  identical shapes to logs3Low/DOT (smart side = smartBackTest, range side =
  QubitFlow dryRunner with the real rangeBound factor).
- Dumped 721-row histories for BTC/ETH/SOL/XRP x 60/240/1440; copied DOT into
  logs3Low/DOT/ for uniformity.
- NEW `scripts/per-pair-winning-test.js` + `backtestRangeSignalGate.js <PAIR>`:
  per-pair fee-honest winning test + gate backtest.
- `trade/import_edge.py`: builds `by_pair` in logs/edge_report.json from each
  pair's own reports; SELL cells from each pair's gate horizon-5 sell, same
  promotion rule (interval promoted by pair's winning test AND gate sell
  positive) else UNPROVEN/LOSING.
- `trade/edge.py cell(interval, side, pair=...)`, `decider`/`broker`/`loop`
  thread `pair` through; BTC shows its own UNPROVEN/LOSING everywhere.
- `config/trading.json`: watchlist -> [DOT/USD, ETH/USD, SOL/USD, XRP/USD].

Current verdicts (real reports):
| pair | 60m | 240m | 1440m |
| DOT  | BUY PROVEN, SELL PROVEN | UNPROVEN | UNPROVEN/LOSING |
| ETH/SOL/XRP | LOSING/UNPROVEN | BUY PROVEN, SELL LOSING | UNPROVEN |
| BTC  | LOSING/UNPROVEN across the board -> parked |

Fees at buy creation:
- decider plan now carries `expected_net_pct` = target move - round-trip fees -
  round-trip slippage, computed when the BUY plan is created; evidence string
  cites it. Fees AND slippage both filter.

Docs drift warning: session 3/4 lines in this file (240m BUY n=61 +1.26%,
1440m SELL +1.79% n=76) NO LONGER reproduce from the current importer. The
current edge_report.json is authoritative. Treat older numbers as stale.

### Session 6, later blocks (GUI and brain-speed work)
- Console section clarified: still present, not removed. It is Spice/JARVIS; the
  Brain page is the guardian. Both still share the same opencode budget and the
  same Heart gate for any order attempt. Console is kept unless explicitly
  hidden by the owner.
- GUI/console lag root causes addressed:
  * `heart.unlock()` is cached for 30s (used to run credential decrypt every 5s).
  * `exchanges` reload/credential decrypt only when config mtime changes.
  * Brain page no longer reads the whole streaming jsonl every 5s; it tails the
    last 256KB, rebuilds only when the file or pending state changes, caps
    blocks at 500, and preserves the user's scroll unless stick-to-bottom.
  * Exchanges combo is not rebuilt on every timer.
- Brain speed: `skin/brain.py` now uses the running opencode server over HTTP
  (`opencode api POST/GET`) instead of spawning a cold `opencode run --session`
  CLI for every ask. The old CLI path remains as a fallback. Confirmed by
  `mastermind.py ask` round-trip dropping from multi-second CLI judgement to
  about 5.7s total including the user message and log write.
- CPU governor switched from schedutil to performance on all cores as a stopgap
  while the user was on battery. That should remain visible if you inspect
  `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`, but it is live state,
  not a config change.
- Snapshots of this conversation within the same session: openclaw removed,
  whitecells LLM now off, net_check/egress crash + alert storm fixed, per-pair
  edge cells imported.

================================================================================
# SESSION 6 — end-of-chat state (no Wi-Fi/nuisances)
================================================================================
Date: 2026-10-05. Focused on speed/robustness + filling real per-pair edge cells.

## What changed since last snapshot
- OpenClaw gateway removed (not part of BlackHorse; killed service + files).
- Dead llama-server stopped; llm_enabled=false for whitecells.
- egress.py: process-name flap on known ip:port is WARN (was the LOCKDOWN/LLM churn source).
- net_check.py: corrupt port_baseline.json no longer crashes the sweep.
- GUI: five 2s timers -> 5s; active-defense check 3s -> 15s over background.
- Heart.unlock() cached 30s; exchanges/vault creds only re-checked on config change.
- refresh_brain: tails last 256KB of mastermind.jsonl, rebuild only when content
  changes, cap 500 blocks, no undo stack, scroll preserved (stick-to-bottom only
  when already at bottom, no full-page overwrite on every poll).
- Treasury reconcile throttled to once every 5min (was every GUI poll, spamming
  ledger/alert/lockdown events).
- Brain path: skin/brain.py.ask now uses the running opencode HTTP server
  (opencode api POST + poll), falling back to CLI. Empty cold-start of
  opencode run removed as the default path.
- CPU governor was switched to performance live (still a live knob, not config).

##Per-pair edge status (real reports, imported to logs/edge_report.json)
- DOT: 60m BUY PROVEN, 60m SELL PROVEN (gate walk-forward). All others on DOT
  are UNPROVEN or LOSING.
- ETH/SOL/XRP: 240m BUY PROVEN. 240m SELL is LOSING (important: spot exit must
  come as a paired round trip, not a standalone SELL edge).
- BTC: 60/240/1440 UNPROVEN/LOSING -> parked.
- mode LIVE + autonomous=true, watchlist [DOT/USD, ETH/USD, SOL/USD, XRP/USD];
  can_arm=false because paper_period still 0 closed trades / 0h.

## Still unresolved
- paper_period: 0 closed paper trades accumulated yet; can_arm stays false.
- BTC excluded until its walk-forward stops saying UNPROVEN.
- Need to verify any future per-pair SELL PROVEN cell; today none beyond DOT 60m.
- Brain cold-start load: not eliminated, but per-ask cost now mostly LLM work.
- CPU governor at performance is a live admin setting, not a committed config.

## Truth today
Master ON/arm, mode LIVE, autonomous ON, 2 agents enabled, heartbeat ok,
decision HOLD, who has HOLD across 6h? no, 0h, can_arm false. Night-shift runs
in background (pid). Decider path tested DECIDER-broker HOLD with reason
"cannot SELL what we do not hold / fee gate / strength outside buy+sell band";
every cycle currently returns HOLD since no setup met strength/price band.

## Session 6 — last durable add: how Spice should reason
- We ran `trade/edge_rules.py` over existing snapshot rows; it already scores variable cells
  (rangeSignal, trend, maSpread sign/direction, support recency, entry/exit strength) with fee/slippage.
- Observed: no PROVEN-BOTH from raw rules; several high-test but sample-starved (edge-decays/no-edge).
  Count of robust edge still reduces to 60m BUY/SELL only, with ETH/SOL/XRP 240m BUY from per-pair tests.
- Added a durable memory rule to Spice/growth: to justify an edge, every item must show how train→test
  performs net of fees/slippage; small cells n<10 are not proven; hypothesis should be Spice-talkable via
  qb_rows and   edge_rules iteration, not routed into live until after a repeated train/test signature.
- The $<amount> live treasury stands ready to let agents close paper/live evidence once any setup satisfies
  decider; the system still does not duty-overcommit when there is no honest setup.

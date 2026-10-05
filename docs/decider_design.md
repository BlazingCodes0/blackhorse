# BLACKHORSE DECIDER — design (the "trade like me or better" brain)

Discussion draft. Date 2026-10-04. Related: docs/market_signal_extraction.md,
brain/AUTONOMY_PLAN.md, heart/ROGUE_PLAYBOOK.md.

## 0. Blunt truth first
No model — local or opencode — walks in smarter than you at YOUR edge. If one
did, everyone would run it. Humans beat bots only until the bot is *disciplined*;
bots beat humans through consistency, zero emotion, speed, perfect memory, and
broad context. So "like me or better" is NOT a model you download. It is a
PROCESS you build and VALIDATE:

    ensemble signals + regime awareness + position sizing + hard risk
    + context/adaptation (LLM) + relentless journaling + out-of-sample proof

Also: opencode CANNOT be the per-minute trader. It is slow, rate-limited, and can
hallucinate numbers. Roles are split:

    FAST (per candle)  -> deterministic engine      (the "trader")
    SLOW (periodic)    -> opencode strategist        (the "coach/analyst")

## 1. Two brains
### Brain A — the Trader (deterministic, runs every cycle, no LLM)
- Consumes the SignalBot contract + candles + account state.
- Detects regime, selects a playbook, sizes the position, sets entry/exit.
- Fully explainable, reproducible, backtestable. Zero latency, zero cost.
- This is what actually places/refuses trades.

### Brain B — the Strategist (opencode, minutes-spaced, budgeted)
- NOT per-tick. Runs on a rhythm (and on events: big loss, regime flip, novelty).
- Jobs: read news/context, challenge Brain A's read, spot regime shifts and
  correlations, flag "this looks like a trap / this is a new regime", write the
  post-mortem, propose rule tweaks (which a HUMAN approves).
- May VETO (raise caution) but may not invent prices or bypass Heart.
- Uses the existing shared budget (skin/brain_budget.py).

## 2. Inputs (what the decider sees)
From the sidecar (SignalBot compacted output):
- per channel/interval: decision{action,price,reason,confidence},
  factorResult{floor,low,high,roof,strength,strengthDirection,rangeSignal,
  buyScore,sellScore,entryEligible,exitEligible},
  marketResult{trendState,spreadDirection,latestSpread,maSpreadRun}
- grids{level1,level2,level3}: {floor,low,high,roof,maCross,ema,descision}
- marketStructure{aggregate{floor,low,high,roof},buckets}
Plus, gathered ourselves:
- OHLC candles for all six intervals (same override seam -> identical prices)
- ticker/orderbook + pair precision/min (from qubitFlow market services)
- account: balance, open positions, today's PnL, fees
- external context via Brain B: news, funding, correlation

## 3. Feature layer (turn output into numbers)
Build one vector per interval and a cross-interval context:
- strength & strengthDirection; distance to floor/low/high/roof (%)
- buyScore/sellScore and their gap; entryEligible/exitEligible flags
- trendState one-hot (rising/falling/range/about_to_turn)
- spreadDirection sign; maSpreadRun
- recovery-vs-structure agreement (1/5/15 vs 60/240/1440)
- realized volatility, ATR, fee-to-target ratio (is the target worth the fees?)
- multi-timeframe alignment (do the three channels agree?)

## 4. Regime detection (deterministic, first-class)
Classify the market into a regime, because the RIGHT strategy depends on it:
- TREND (HMA slope aligned across 60/240/1440)
- RANGE (price oscillating between stable floor/roof)
- COMPRESSION (volatility contracting -> expect expansion)
- EXPANSION/NEWS (volatility expanding -> breakout/caution)
Reuse SignalBot factors as evidence: trendFollowing, rangeBound, volatilityExpansion.
If regime is unclear -> HOLD. Never trade a coin flip.

## 5. Strategy playbooks (one per regime)
- RANGE  -> mean-reversion: buy near floor, sell near roof/high, tight targets,
           only if fee-to-target ratio is favorable.
- TREND  -> trend-follow: enter on pullback to HMA, trail the exit.
- COMPRESSION -> breakout: enter on confirmed break with buffer, small size.
- NEWS/EXPANSION -> stand down or reduce size; let Brain B assess.
Playbook choice is code. This is where "trade like me" gets encoded (see 8).

## 6. Position sizing + risk (Heart owns the caps)
- risk-per-trade %, stop distance -> size (reuse qubitFlow positionSizer logic).
- Hard caps in code, not prompts: max position, max daily loss, max drawdown,
  max open positions, order rate, venue whitelist.
- Fee-aware: never take a target that doesn't clear entry+exit fees + min profit.
- Heart gate: ALLOW / BLOCK / REVIEW. Brain can propose; it cannot bypass Heart.

## 7. Decision journal + learning loop
- Every decision -> hash-chained ledger: inputs snapshot, regime, playbook,
  proposed action, Heart verdict, execution, outcome.
- Periodic review (Brain B + code stats): win rate, expectancy, per-regime
  performance, slippage, "did the LLM's veto help or hurt?".
- Lessons go to brain/memory. Rule changes require HUMAN approval — the system
  never silently rewrites its own risk posture.

## 8. Encoding "like me" (this is the real work)
"Like me or better" requires capturing YOUR playbook explicitly:
- When do you enter/exit? What makes you SKIP a setup?
- What's your position sizing and max loss tolerance?
- Which intervals/strategies do you trust, and in which regimes?
I need to interview you and write these as rules in
`blackhorse/trade/playbook.json` (or heart/policies). The engine then enforces
your discipline at machine speed, and Brain B extends it with context you can't
watch 24/7.

## 9. Validation gate (BEFORE any real money)
Non-negotiable: a fee-honest, walk-forward, out-of-sample backtest.
- Entry/exit fees + slippage modeled (qubitFlow already exposes these).
- Train/test split, no look-ahead, no survivorship.
- Report expectancy, max drawdown, Sharpe, trade count per regime.
- Must beat buy-and-hold on risk-adjusted return AFTER fees, or we don't go live.
- Then PAPER (weeks of live paper), then TINY live inside caps.
qubitFlow's own research says its default strategy has no proven fee-adjusted
edge -> the decider must EARN the right to trade.

## 10. Architecture (fits prior decisions)
```
blackhorse/trade/
  sidecar/     Node: getMarket()/getSignal() (SignalBot wrapped, candles injected)
  feed.py      candles+ticker+precision
  signal.py    SignalBot output -> normalized schema
  features.py  -> feature vectors + regime
  decider.py   Brain A: playbook -> action + size (deterministic)
  coach.py     Brain B: opencode strategist (budgeted, may veto/annotate)
  heart gate   ALLOW/BLOCK + caps + dead-man
  execute.py   ccxt (one venue first)
  ledger.py    hash-chained decisions + outcomes
  validate/    backtest + walk-forward harness
```

## 11. Milestones
- M0: feed + signal + features + regime, printed per cycle (no trades).
- M1: decider.py emits actions in SHADOW; journal every one.
- M2: backtest harness; only then trust a rule set.
- M3: Heart gate wired; paper live; measure.
- M4: tiny live inside caps; Brain B coaching on.
- M5: learning loop + human-approved rule evolution.

## 12. What I need from you to make it "like you"
1. Your actual entry/exit rules per regime (or "watch me and infer them").
2. Position sizing + max loss you're comfortable with.
3. Trusted intervals/strategies and where they fail.
4. Which pairs/venue first.
5. Risk ceiling to prove the loop.

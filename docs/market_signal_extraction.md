# Market-data + SignalBot extraction → BlackHorse Trade (design, discussion)

Goal: pull ONLY accurate market info and the SignalBot signal out of
qubitFlowBeta. Leave every qubitFlow bot, order path, ledger, billing, auth and
UI behind. Build our own `blackhorse/trade/` that consumes that data.

## 1. The clean cut

REUSE (read-only, no coupling to qubitFlow's trading):
- `src/services/krakenMarket.service.js` (+ coinbase/binance siblings)
  REST public data: ticker, pair catalog (orderMin/costMin/precision), order
  book. TTL-cached, `https`-only, no framework dependency.
- `tradingBot/marketData/ohlcHub.js` + `tickerHub.js`
  WebSocket streaming OHLC/ticker with REST fallback. Closed-candle cache.
- `SignalBot/` — `runRangeSignalBot({config,factor,style})`
  → `compactSignalBotOutput()` gives the stable decisions JSON.

DROP (do not import):
- `tradingBot/*` order/execution/ledger/records/adapters
- `src/services/*Execution*`, `exchangeRouter`, `tradingBotRuntime.service.js`
- billing (Stripe), auth/session, `public/` UI, Postgres storage

## 2. Signal output contract (what the brain consumes)

```
{ <channel ripple|wave|tide>: {
    interval, currPrice,
    decision {action, price, reason, confidence},
    factorResult {floor, low, high, roof, strength, strengthDirection,
                  rangeSignal, buyScore, sellScore, entryEligible, exitEligible},
    marketResult {trendState, spreadDirection, latestSpread, ...} },
  marketStructure {aggregate {floor,low,high,roof}, buckets},
  grids {level1, level2, level3},
  averageTrader {aggregated, decision} }
```

## 3. The seam that prevents divergence (critical)

`SignalBot/core/dataFeed.js` fetches ITS OWN candles, but honors
`global.__QF_MARKET_DATA_OVERRIDES`; the connector passes
`marketDataOverrides`. So BlackHorse can supply candles ONCE and have both
SignalBot and our execution read identical data. Never let the signal and the
order path see different prices.

## 4. Architecture (sidecar, because SignalBot is Node and BlackHorse is Python)

```
blackhorse/trade/
  sidecar/        trimmed Node: krakenMarket.service + ohlcHub + SignalBot wrapper
                  getMarket(pair, intervals) -> {candles, ticker, precision}
                  getSignal(pair, strategy, candles) -> compacted SignalBot JSON
  feed.py         calls sidecar getMarket (or ccxt directly), caches, validates
  signal.py       calls sidecar getSignal, normalizes to our schema
  decide.py       brain (opencode) proposes, combining signal + own indicators
  heart gate      ALLOW/BLOCK + caps + dead-man (the referee)
  execute.py      our order path (start: ccxt, one venue)
  ledger.py       hash-chained actions for replay/audit
```

Rationale: reimplementing SignalBot's `proDecisionEngine` in Python would change
its behavior and lose whatever edge exists. Wrap it; don't rewrite it.

## 5. Gotchas found in the code (must handle)
1. SignalBot default dir is a USB mount `/run/media/a/45FE-7121/SignalBot`.
   Money code cannot depend on removable media → relocate into the repo.
2. SignalBot is STATEFUL: ~12 MB `saveSmart*.json` history. Decide persist+version
   (reproducible) vs stateless-per-call.
3. `npm test` is currently BROKEN (references deleted
   `test/marketStructureResearch.test.js`). Fix before trusting signal CI.
4. `ccxt` is already installed in qubitFlow's node_modules — good lightweight
   venue layer for our own execution.
5. Two market caches exist (krakenMarket.service vs SignalBot sharedMarketCache).
   Pick ONE source of truth and inject into the other.
6. Strategy edge is unproven (per qubitFlow's own research doc). Signals are
   features, not gospel; Heart + our own validation decide.

## 6. Open decisions
1. Sidecar (reuse SignalBot exactly) vs pure-Python reimplementation?
2. Where does SignalBot live permanently (fixed repo path)?
3. First venue: Kraken only?
4. Intervals: all six as features, or only traded strategy rows?
5. SignalBot state: persist+version, or stateless per call?

## 7. Suggested first slice (smallest end-to-end)
- Copy SignalBot into `blackhorse/trade/sidecar/SignalBot`.
- Sidecar exposes `getSignal` + `getMarket` over stdin/stdout JSON.
- `feed.py` + `signal.py` print a normalized snapshot for one pair (BTC/USD),
  Kraken, all six intervals, every N minutes.
- No orders yet. Prove: same candles in and out, signal stable and explained.
- Then add decide.py + Heart gate + ccxt paper execution.

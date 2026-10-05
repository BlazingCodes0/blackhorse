# PLAYBOOK — multi-timeframe range-strength read (DOT/USD)

Date: 2026-10-04. Status: DRAFT — cells marked PROVEN / UNPROVEN / NEEDS-DATA.
Measured on SignalBot's real logged rows (`logs/sb_rows.json`), net of 0.62%
round-trip fees + slippage, DOT/USD on Kraken. Each interval behaves differently;
they do NOT share thresholds — only the shared structure carries over.

## The shared structure (all intervals)
- `strength` 0..1 = position inside the current range.
  - strength → 0 = price near the range floor → BUY zone
  - strength → 1 = price near the range roof  → SELL zone
  - **Enter closer to 0 on the buy side, closer to 1 + higher spread on the sell side.**
- `maSpreadPct` = MA20 vs MA50 spread in % (shortMA/longMA in the rows).
  - maSpread falling → price falling; maSpread rising → price rising.
  - The higher the timeframe, the less accurate/noisier this read gets.
- `rangeSignal` (BUY/SELL/HOLD) + `strengthDirection` + `spreadDirection`
  (improving/weakening) + `trend` (rising/falling/range/about_to_turn).
- Support context: `nearestSupportPct`, `supportRecent`, `supportTouches`, `zone`
  (floor/lower/middle/upper/roof).

## MA20/MA50 cross (length 20, smooth 9)
- A fresh cross on the 15m is the early catch. **15m is the ONLY timeframe
  where the MA20/50 cross is trusted as a trigger.**
- Smaller timeframes (1m/5m) are noise — allowed only as pre-judgment,
  never as a trigger.
- The daily (1440m) MA20/50 cross is NOT used (measured: +3.184% h20 exists
  but it is not part of the active play — too slow, too few samples of trades).
- A 15m cross "keeps going" only when the higher timeframes agree on
  direction via maSpread:
  - 15m cross UP + 60m maSpreadPct > 0 + 240m maSpreadPct > 0 → continuation candidate
  - Same logic inverted for cross DOWN with negative spreads.

## Per-interval measured status (full scan, train | test, net of fees)

## RSI lane (added after 2026-10-04 rework, RSI(14) computed from Kraken candles)
- RSI<40 alone does NOT make a 15m floor BUY work (train +0.354 / test -2.153).
- SELL @roof + RSI>65 (true overbought): every 15m sample lost at h20 — the
  RSI lane's main use is a REFUSAL filter (don't short the roof when RSI is
  already overbought; don't call RSI 55 "oversold").
- Best RSI cell: **60m BUY @floor + RSI >= 50** — h20 train +0.118 / test
  +2.029, n≈41 total. Tier-2 candidate alongside the 240m joint cell.
- Advisor/adaBot lanes (buyAvg/sellAvg/sweet/median/perfected) now come from
  ONE call: `runAdaBot({pair, intervals:['60','240','1440']})` returning the
  combined prices plus per-interval individual prices (see
  `docs/PLAYBOOK_ADVISOR_PRICES.md`).

### 15m — noise floor, UNPROVEN
- No cell passes train+test. strength<.3 floor BUY: -0.94 / -0.96 (h20).
- Aligned 15m cross-up (60m>0 & 240m>0): PROVISIONAL, h20 n=5 win 80%
  avg_net +0.409%. Must reach n>=30 before arming.

### 60m — marginal, UNPROVEN
- RS BUY + floor zone + maSpread<0: h20 train -0.767 / test +1.059 — unstable.
- strength>.7 + weakening SELL: h20 train -1.754 / test +1.99 — unstable.
- Direction confirmation only; not a trigger.

### 240m — the closest to a usable cell
- **strength<.3 + floor/lower BUY: h20 train +1.797 / test +1.031, n~131 —
  both halves POSITIVE. First cell that passes the train/test screen.
- RS BUY + floor + maSpread<0: h20 train +0.855 / test +0.442, n=65 — also both
  positive, second confirmation of the same idea.
- strength>.7 roof SELL: h20 train +2.905 / test +0.493 — both positive too.
- 240m trade zone: price at floor/low range + range structure intact = BUY
  candidate; price at roof/upper + stretched spread = SELL candidate.
- **JOINT CONFIRMATION (range bot + smartbot on same candles):** BUY at
  floor/lower when smartbot also reads `maSignal == bullish`: h20 train +0.898%
  / test +0.844%, n≈68, win ~35–53% — both halves positive. Best joint cell
  found. Still needs n≥30 out-of-sample + paper before it earns capital.

### 1440m — strong cells but no trade path (spot has no short)
- RS SELL + roof zone + maSpread>0: h20 train -14.9 / test +18.3 — edge exists
  only in the test half; excluded until proven.
- strength>.7 roof + weakening SELL: h20 train +13.4 / test +14.6, n=11 both —
  consistent but n=11 too small; becomes a strong basis for the EXIT reframe.

### Advisor (adaBot) prices
- NO backtest data — use as live entry reference only, never as backtest proof.

## Use inside the brain
- The brain may combine ALL intervals for the big picture, but the ACTION cell
  is always the one whose evidence gate passes: a trade may only fire from a
  cell that is PROVEN (train AND test positive, n>=30) in
  `logs/edge_report.json` / `logs/edge_rules.py` output.
- When unsure on 15m, zoom out to 60m/240m for confirmation of direction of
  maSpread only. 1440m is a bias indicator, never a trigger; its MA cross is
  not trusted.

## Loop
1. 15m row update → strength, maSpread + direction, zone, rangeSignal, supports.
2. Fresh MA cross on 15m? → check 60m spread sign, then 240m.
3. All agree + cell is PROVEN → size by treasury/decider, gate via Heart, paper-first.
4. Any disagreement or missing proven cell → HOLD, record observation, log to
   the permanent memory zone.

# ENTRY GATE FINDINGS — why HOLD, and what a real floor BUY needs

Date: 2026-10-07. Source: `SignalBot/signalBots/range/rangeSignalGate.js`.

## The 15m BUY gate requires ALL of:
`entryEligible && buyScore >= 0.60 && buyScore > sellScore && RSI gate && spread not against && advisor lane`

| gate | required | observed (2026-10-07) |
|---|---|---|
| RSI gate | RSI <= ~35 for BUY | 41.08 -> blocked |
| spread mode | not_against | spread weakening -> blocked |
| buyScore | >= 0.60 | 0.59 -> blocked |
| trendMode | any | about_to_turn -> pass |
| advisor lane | price reaches buy zone | price 1.2020 vs buy 1.2037 -> borderline |

Profile note (15m): "Floor entries only held an edge when the MA spread was
improving." -- 27 samples, 70.4% win rate. The thresholds are forward-test tuned.

## Bottom-watch (armed, not a trigger)
- ARMED when: currPrice <= floor/low AND 15m strength < ~0.15.
- FIRES to BUY only when ALL flip: strength rising off 0, RSI <= 35,
  spreadVerdict improving, spreadMA not neutral-negative, buyScore >= 0.60.

## Scenario table (brain read)
1. RSI washout, price holds > low -> BUY (the bread-and-butter setup).
2. RSI <= 35 but spread weakening -> WAIT (knife risk).
3. spreadMA bullish + trend up + above low -> BUY (the "real" one, a few bars
   after the exact low -- accepted tax).
4. Price breaks low/floor -> NO BUY (range invalidated).
5. 15m strength == 0, 240m rising -> use 240m for direction, 15m only for
   entry timing; do not market-buy the zero.
6. 60m/240m score high, 15m weak -> let 15m strength-rising-from-0 be the
   trigger, not the range BUY label.

## smartbot ma10/ma30 alias (same file family)
`out.ma10 = out.maFast`, `out.ma30 = out.maSlow` (default MA20/MA50);
`maSpreadPct = (fast-slow)/slow*100`; signal: bullish >= +3%, bearish <= -3%.
Live SignalBot does NOT run smartbot -- so these are computed fresh per run in
`trade/signalbot.py::_spread_ma()` from `signals.maCrossTripple`.

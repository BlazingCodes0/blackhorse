# PLAYBOOK NOTE — advisor price blend [60,240,1440] (pre-implementation)

Date: 2026-10-04. Status: DEFINED, not yet wired into agents.

## The blend — AUTHORITATIVE SOURCE
adaBot itself accepts the interval array `['60','240','1440']` and returns BOTH
the combined price set and the per-interval breakdown in one call:

```
runAdaBot({ pair:'DOTUSD', intervals:['60','240','1440'] })
→ buyAvg / sellAvg / sweetBuy / sweetSell / medianPrice / medianBuy / medianSell
  / perfectedBuy / perfectedSell / rsiAvg  (combined across the 3 TFs)
→ intervalResults[]  (each interval's own buyInPrice / sellOutPrice / currentPrice)
```

Live output 2026-10-04T10:09Z (DOTUSD, kraken):
```
currPrice      1.1968
buyAvg         1.1829   (60m 1.1832, 240m 1.1826, 1440m null→excluded)
sellAvg        1.1988   (60m 1.1941, 240m 1.2012, 1440m 1.2012)
sweetBuy       1.1449
sweetSell      1.2242
medianBuy      1.1560   medianSell 1.1920   medianPrice 1.1783
perfectedBuy   1.1613   perfectedSell 1.2050
rsiAvg         55.24
```
NOTE: this supersedes the manual perfected/average blend in
`logs/advisor_combo.json` — the manual blend used a different price family
from a stale `signal_latest.json`. Going forward the advisor combo comes
from one adaBot call with the interval array, per-interval prices included.

## How the agents use it
- **Scalper (15m, ripple/wave)**: does NOT trade the blend directly. It may
  only act on the 15m trigger (fresh MA20/50 cross, 60m+240m maSpread
  confirming), and its limit/reference zone is the blended band above.
- **Higher-TF agent (60m/240m/1440m)**: uses the blended buy band as its
  accumulate zone and the blended sell band as its trim/exit zone. Enter
  closer to the blended buy, exit toward the blended sell.
- **Brain (last say)**: every candidate order must pass the evidence gate —
  an interval+side cell must be PROVEN in the edge report before a band is
  actionable. Until then the bands are observations, and the answer to
  "what's up" is HOLD with the band values read out.

## Rules
- Never fabricate a price: missing/0 advisor prices are excluded (n reflects
  how many TFs contributed). If n_buy or n_sell == 0, report UNAVAILABLE.
- The blend is a *reference*, not proof of edge. Backtest data is still
  only valid for the range-strength cells in `docs/PLAYBOOK_MTF.md`.

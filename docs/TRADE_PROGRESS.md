# BlackHorse Trade — build progress

Living log. Date started: 2026-10-04. Companion: docs/decider_design.md,
docs/market_signal_extraction.md, docs/SESSION_NOTES.md.

## Mission (from user)
BlackHorse must:
1. Securely hold exchange API keys/secrets.
2. Be fully autonomous, using the brain (opencode) to DECIDE.
3. Use market NEWS to see where the market will actually go — not rely only on
   SmartBot.

## Done so far
### trade/vault.py — secure key storage ✅
- AES-256-GCM (same standard as qubitFlow), master key file `config/.master_key`
  (0600) or `BLACKHORSE_MASTER_KEY` env for production.
- Vault: `config/exchange_keys.json` (0600), values encrypted, gitignored.
- API: `set_secret/get_secret/delete_secret/list_secrets/selfcheck`.
- CLI: `python3 trade/vault.py set kraken.api_key` (getpass, never argv).
- Verified: round-trip encrypt/decrypt OK.

### config/exchanges.json + trade/exchanges.py — exchange registry ✅
- NOTHING hardcoded. Add an exchange = add a JSON entry.
- Per-exchange credential schema (Kraken key+private; Coinbase key+private;
  Binance apiKey+secret), quote assets, public endpoints, and
  permissions_needed / permissions_forbidden (never enable withdrawals).
- Active exchange is a single field. Credentials stored as
  "<exchange>.<field>" in the vault; only presence is ever shown.
- CLI: list / show / use / connect / status / add-key. Guided `connect` reads
  secrets via getpass. Verified selection + readiness flip.

### trade/news.py — market/macro context ✅
- Fear & Greed (alternative.me), global mcap + BTC/ETH dominance (CoinGecko),
  prices + 24h change, headlines (CoinDesk/CoinTelegraph/Decrypt RSS).
- Best-effort; caches to `logs/news_cache.json`; `get(max_age)` with stale
  fallback; `summary()` one-liner.
- Verified live: F&G 65 Greed, mcap -2.34%, 24 headlines.

### trade/coach.py — the brain as decider ✅
- Assembles a compact packet (news + signal) and asks opencode for a STRICT JSON
  verdict: bias / regime / confidence / risk_level / stance / notes / watch.
- Budgeted via skin/brain_budget.py (minutes-spaced). Logs to
  logs/brain_market.jsonl. Parse hardened (handles messy model output).
- Brain may only ADVISE; can veto caution (STAND_DOWN/EXTREME), never trades.
- Verified: real call returned STAND_DOWN / UNCERTAIN / MEDIUM with sound
  reasoning over live news.

### trade/engine.py — state snapshot ✅
- Single read-only surface: active exchange + descriptor, news, signal
  (if present), brain verdict, governance (autonomous flag, PAPER mode).
- `snapshot/exchanges/news/ask`. No orders, no live-account mutation.

### trade/sidecar/signal.js + trade/signalbot.py — SignalBot bridge ✅
- Node sidecar runs the REAL SignalBot (no reimplementation), paths via env
  (BH_SIGNALBOT_DIR/BH_QUBITFLOW_DIR), pair/strategy/exchange via args.
- Compacts with qubitFlow's own compactor; 13s, ~18KB, 3 channels.
- Python signalbot.py normalizes -> logs/signal_latest.json (brain-friendly):
  per channel action/confidence/strength/direction/trend/spread/stop/take.
- Verified: signal available in engine snapshot AND in coach packet.

### GUI: signal panel ✅
- Trade page: "▶ Run SignalBot now" button (off-thread), live per-channel read
  (ripple/wave/tide action+confidence+strength+regime), status light.
- Applies active exchange from registry (no hardcoding).

### app/main.py — GUI Trade page ✅
- New "Trade (Exchange)" page: exchange dropdown, "Set active", "Enter
  credentials…" dialog (hidden fields from the registry schema), expected-
  connection panel, market news panel, brain-verdict panel, signal panel.
- AUTONOMOUS switch that REFUSES to arm until prerequisites pass (shows why).
- Verified headless: pages build, exchange switch works, arm refused.

## Not yet built
- trade/sidecar/ — Node wrapper exposing SignalBot getMarket()/getSignal().
- feed.py — candles/ticker/precision for our own view.
- signal.py — write logs/signal_latest.json (feeds coach's packet).
- features.py + aggregator — merge ripple/wave/tide into one read.
- decider.py — deterministic trader (regime -> playbook -> action+size).
- heart gate (ALLOW/BLOCK/caps) + execute.py (ccxt) + ledger.py.
- validate/ — fee-honest walk-forward backtest.

## Immediate next steps
1. Node sidecar: getSignal() -> write the compacted summary to
   logs/signal_latest.json so coach.py has the SmartBot half.
2. feed.py: candles via sidecar/ccxt for the pairs we trade.
3. Turn FEES ON in the SignalBot run (currently feePercentPerSide=0).
4. Aggregator: one cross-interval action from the three channels.

## Notes / decisions
- Sidecar chosen (reuse SignalBot exactly; do not reimplement in Python).
- SignalBot is Node; BlackHorse is Python. Sidecar bridges via JSON.
- SmartBot output is huge raw (1.6MB) but 18KB compacted -> easy to ship.
- First venue: Kraken (fully wired in qubitFlow + SignalBot default).
- Brain cadence: minutes, budgeted. Deterministic trader runs per candle.

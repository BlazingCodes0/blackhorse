# TODO - Order reconciliation + TIF dropdowns  [DONE]

## Snapshot (what this fixed)
- Bot thought funds tied up: positions closed OUTSIDE BlackHorse (manual Kraken
  sell) or while bot was down stayed OPEN locally, so capital()/deployed() kept
  counting them. Root cause: no order/position reconciliation vs the exchange.
- Found 26 phantom OPEN sell orders in logs/trading.db (Oct 9-15) = resting maker
  exits that filled/canceled on Kraken but never settled locally.
- API key check: config/exchange_keys.json is .gitignored, untracked, ZERO git
  history. Key was NOT saved to git. (It is plaintext on disk, mode 600.)

## Changes
1. TIF (time-in-force) GTC/IOC/GTD/FOK added end-to-end, SEPARATE from order type
   (limit/market/trailing):
   - store.py: orders.time_in_force column + create_order/live_order params.
   - venues.py: place_order(..., time_in_force=) on base + kraken/binance/coinbase.
     Kraken maps GTC/GTD(expiretm)/IOC/FOK; Binance maps GTC/IOC/FOK; CB maps keys.
   - execute.py: _expiry_for(tif, plan); _live_order reads plan.time_in_force;
     IOC/FOK settle immediately (not tracked as resting); GTC rests (no expiry).
   - decider.py: emits time_in_force (default GTD = old 600s-expiry behaviour).
   - tools.py / manual_order(): optional trailing tif token + arg.
   - app/agents_ui.py: new "Time in force" dropdown + Overview display line.
   - config/decider.json + config/agents.json: entry_time_in_force default.
2. Reconciliation (the real fix):
   - store.live_open_orders(): working LIVE orders with an exchange id.
   - execute._reconcile_orders(): settle local open orders vs exchange status.
   - execute.reconcile(): boots + every sweep; orders + POSITIONS. If exchange
     no longer holds the qty, closes/shrinks the local position at last price
     (external-close) and releases capital; cancels orphaned resting exits.
     Unknown balance ({}) = never touch positions.
   - portfolio.reduce_position(): realizes P&L for a partial external close.
   - loop.py: boot reconcile on daemon start + per-sweep reconcile.
   - CLI: python3 trade/execute.py --reconcile [--no-positions]
3. order_id persistence already at-send (store.live_order right after venue ack,
   synchronous=FULL + WAL); now backed by boot reconcile so a crash can't strand it.

## Verified
- trade/*.py + app/agents_ui.py compile.
- Unit tests: reconcile (full/partial/unknown/paper/noop) PASS; TIF expiry +
  Kraken body mapping PASS; store TIF persistence PASS.
- Real DB inspected read-only; test rows cleaned.

## Settled / follow-up fixes
- Loop was restarted (PID 1907); boot + per-sweep reconcile ran and settled the
  26 phantom orders and the DOT external-close. ledger_mismatch now ok:true and
  the lone DOT position (0.1418573991) equals the exchange balance.
- Kraken TIF mapping corrected: GTD -> timeinforce=gtd (+expiretm); FOK ->
  timeinforce=fok (was sent as a plain limit); IOC -> ioc. (verified against
  Kraken AddOrder docs)
- reconcile balance read hardened: invalidate cache + retry 3x before treating
  the balance as unknown, so a transient blip can't silently skip a sweep.
- Cleaned 7 orphan test positions (agent_test / a1 / a2, no txid) from
  trading.db -> store.verify_against_portfolio() ok:true.
- Verified: py_compile all changed modules; TIF body mapping unit check;
  `python3 trade/execute.py --reconcile` -> {orders:[], positions:[], ok:true}.

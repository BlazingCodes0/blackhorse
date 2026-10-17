# CONVERSATION SNAPSHOT

## User msg 1
"That means those positions were likely closed outside BlackHorse (manual close on
Kraken) or closed while the bot was down... bot never logged the exits and still
thinks funds tied up. No local record of unauthorized new buys at ~1.12... Pending
orders empty." -> "how do solve this and stop it happening, i thought even if i hold
power button everything is saved as soon as order made, AND check if api key was
saved on git. There should be order reconciliation: as soon as order is sent the
order id should be immediately stored."

## User msg 2
"MAKE A TO DO SAVE TO CONVERSATION SNAPSHOT THEN START WITH IT, ALSO ADD DROPDOWN
FOR GTC,IOC,GTD AND FOK ORDER TIMINGS NOT TO BE CONFUSED WITH LIMIT,MARKET,TRAILING
WHICH WE ALREADY HAVE DROPDOWN FOR PUT ANOTHER FOR THE MENTIONED"

## User msg 3
"SAVE CONVERSATION SNAPSHOT AND BE QUICK"

## What was done
1. TIF GTC/IOC/GTD/FOK added end-to-end (store column, venues mapping for
   kraken/binance/coinbase, execute._expiry_for, decider emits it, tools/manual
   parser, app/agents_ui.py new dropdown + Overview line, config defaults GTD).
2. Reconciliation: store.live_open_orders, execute._reconcile_orders,
   execute.reconcile (orders + positions, boot + per-sweep), portfolio.reduce_position,
   loop boot reconcile + per-sweep, CLI `python3 trade/execute.py --reconcile`.
3. Found 26 phantom OPEN sell orders in logs/trading.db (Oct 9-15).
4. API key NOT in git: config/exchange_keys.json .gitignored, untracked, no history.
5. Verified: compile + unit tests (reconcile full/partial/unknown/paper, TIF, store).
6. Loop PID 2019 still running OLD code -> needs restart for boot reconcile.

## Files changed
app/agents_ui.py, config/agents.json, config/decider.json, trade/decider.py,
trade/execute.py, trade/loop.py, trade/portfolio.py, trade/store.py, trade/tools.py,
trade/venues.py

## Next action
Restart loop OR run: python3 trade/execute.py --reconcile

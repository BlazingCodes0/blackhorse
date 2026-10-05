# What's left — from "look" to "I gave it 100, what's up"

Goal sentence (user): "I gave the brain 100, and I ask what's up." Meaning the
brain must (1) remember capital, (2) act on it, (3) know the state, (4) tell you.

## Already built (the brain can LOOK)
- trade/vault.py            encrypted secrets
- trade/exchanges.py + config/exchanges.json   exchange registry (no hardcoding)
- trade/news.py             market/macro + headlines
- trade/sidecar/signal.js + trade/signalbot.py  real SignalBot -> signal_latest.json
- trade/coach.py            opencode verdict (strict JSON, budgeted, advise-only)
- trade/engine.py           read-only state snapshot
- app/main.py Trade page    exchange select, creds dialog, signal, news, verdict
- skin/brain_budget.py      shared rate limit (minutes-spaced) — no opencode spam

## Missing (the brain cannot HOLD / ACT / REMEMBER)
1. trade/treasury.py   — the "100": allocated, cash, open_risk, realized_pnl,
                         reserve_floor; every spend debits. THE first piece.
2. trade/observer.py   — read-only market-behavior journal. Notes direction,
                         advisor buy/sell availability (null vs value), strength,
                         spread, RSI, HTF structure. No rules, notes only.
                         Hypothesis: "falling + buy n/a" = downtrend confirming.
3. trade/decider.py    — signal + brain verdict + treasury -> concrete action
                         (side, entry, size, stop, target, or HOLD). Deterministic.
4. heart gate          — caps: max position, daily loss, drawdown, order rate,
                         venue whitelist, dead-man, REVIEW queue. ALLOW/BLOCK.
                         Also what UNLOCKS the autonomous switch.
5. trade/execute.py + positions/ledger — place/cancel (paper first), fills,
                         positions, P&L, fees. ccxt on active exchange.
6. loop/daemon         — observe->think->decide->gate->act->record on a cadence,
                         budgeted, bidirectional with the skin.
7. brain console       — STATEFUL chat: brain has context (capital, positions,
                         P&L, recent decisions) so "what's up" gets a real answer,
                         and "give it 50 more" moves money.
8. validate/           — fee-honest walk-forward backtest + paper period.
                         Earn the right to real capital.

## Hard rules (unchanged)
- Nothing reaches the outside world without a Heart stamp.
- Fail-closed: no heartbeat -> no money movement.
- Brain advises/vetoes; it never places orders or bypasses Heart.
- null/0/empty advisor price = missing, never a number.
- One side n/a is an OBSERVATION (confirmation), not automatically a block.
- Real money only after Heart + fee-honest out-of-sample validation.

## Build order
treasury -> observer -> decider -> heart -> execute/ledger -> loop -> console -> validate

## Definition of done for "I gave it 100, what's up"
- You type a number; treasury records it.
- Loop runs autonomously inside caps; Heart gates every action; paper first.
- You ask "what's up" and the brain answers with capital, positions, today's P&L,
  market read, and its current stance — then acts when a real setup appears.

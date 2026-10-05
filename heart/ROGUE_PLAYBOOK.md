# FIGHTERS ON STANDBY — the rogue-bot playbook

The question was: when the system trades and runs its own channel autonomously,
do we need fighters on standby in case bots go rogue?

**No AI fighter. An AI fighter is just a second rogue bot.** What you need is a
deterministic referee (Heart) that holds a kill switch the smart layers cannot
disable, plus hard caps enforced in code. This file is the playbook.

## 1. The one rule that makes everything else work
**Authority to halt must be separate from intelligence to act.**
The brain (opencode) may propose anything. It has *zero* ability to turn off
Heart, raise a cap, or stop the dead-man switch. If the thing being watched can
switch off the watcher, the watcher is theater.

## 2. Rogue is usually not malware
Security whitecells (skin) catch *threats*. Rogue autonomy is *behavior*:
- a retry loop that fires 400 orders in a minute
- a stale price feed the strategy happily trades on
- a strategy that finds the one loophole in its own constraints
- a leaked API key pasted into a public script/prompt
- a bot optimizing "engagement" into spam or policy-violating uploads

Signature sensors miss these. You need **rate/shape/limit sensors**, not
signature matching. That is Heart's job, not skin's.

## 3. Fail-closed design (default = stopped)
| Failure                    | Fail-open (bad)      | Fail-closed (required)        |
|----------------------------|----------------------|-------------------------------|
| Heart crashes              | money loops continue | money loops park themselves   |
| Heartbeat missed           | keep trading         | hard stop + notify            |
| Budget/limit unknown       | allow                | block                          |
| Decision uncertain + irreversible | guess        | human review                  |
- Heart beats an N-second heartbeat; the money loops require a fresh beat.
- No beat ⇒ no orders, no uploads, no spends. Period.

## 4. Yes, keep "fighters on standby" — as sensors, not agents
Standby items, each tiny and independent:
1. **Dead-man switch** — watches Heart's pulse; parks loops when it stops.
2. **Cap breaker** — enforces max position / daily loss / drawdown / order rate.
3. **Anomaly watcher** — flags behavior outside the normal *shape* (rate, size,
   timing, error ratio). Not "is this malware" — "is this what we usually do?"
4. **Ledger verifier** — re-runs the hash chain; any gap = freeze + alarm.
5. **Human tap queue** (`heart/review/`) — the ultimate fighter: you, on the
   irreversible stuff. The system waits; it does not proceed.

## 5. Graded response (don't slam the whole system for a blip)
- **L0 observe** — log only
- **L1 soft** — pause the offending stream/universe; everything else runs
- **L2 halt** — stop all new money movement; keep read-only + alert
- **L3 lockdown** — freeze everything, drop to safe state, wake human
Escalation is automatic on cap breach / breaker trip / chain failure.

## 6. Black box
Every irreversible action is written to the hash-chained ledger (reuse
`skin/threat_log.py`). So when something goes rogue you can replay exactly:
what it saw, what it decided, what Heart allowed, what executed. No replay = no
trust. No trust = no live capital.

## 7. What "too much autonomy" means
Tier by reversibility:
- read / backtest / draft         → fully autonomous
- paper trade / private upload    → fully autonomous
- live order (inside caps)        → autonomous
- live order (cap breach)         → hard stop
- move funds / withdraw / delete  → **human tap, always**
- sign contract / buy ads         → **human tap, always**

## 8. Minimum viable Heart (build BEFORE money moves)
```
heart/policies/rules.json   machine-readable caps + hard bans
heart/filter.py             approve(action) -> ALLOW | BLOCK | REVIEW
heart/breakers.py           dead-man + cap breakers + graded levels
heart/review/               pending human-tap queue
heart/ledger.py             hash-chained action log + verifier
```
Heart runs as its own small process. Brain and Motar talk to it; they cannot
edit or disable it. This separation is the whole point.

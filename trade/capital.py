"""capital.py — ONE shared cash ledger so agents cannot plan against the same dollar.

Before: each agent sized against its own `capital` field independently, so two
agents could both plan $39 out of a $69 pool. The exchange balance is the hard
ceiling for LIVE buys, but nothing stopped *planning* double.

Now: a single pool with one rule —
    committed = cost basis of all OPEN BUY positions (cash already deployed)
    free      = real_available - committed        (never negative)
An agent may size at most  min(agent.capital, free). As each BUY fills and becomes
an open position, `committed` rises and `free` falls, so the next agent sees less.
Total planned exposure can never exceed real money.

CLI:  python3 trade/capital.py
"""
import json, sys
from pathlib import Path

BH = Path.home() / "blackhorse"
sys.path.insert(0, str(BH / "trade"))


def _treasury_available() -> float:
    # LIVE: the exchange is the truth — read real quote cash directly.
    try:
        import execute
        if execute._mode() == "LIVE":
            bal = execute.kraken_balance()
            cash = sum(float(v) for k, v in bal.items()
                       if k in ("USD", "USDT", "USDC", "EUR", "CAD", "GBP", "ZUSD"))
            return round(cash, 8)
    except Exception:
        pass
    try:
        import treasury
        return float(treasury.status().get("available", 0) or 0)
    except Exception:
        return 0.0


def committed() -> float:
    """Cash tied up in open BUY positions — only counted in PAPER, where buys do
    not reduce the hand-kept treasury. In LIVE the exchange cash already reflects
    the spend (treasury is reconciled), so subtracting again would double-count."""
    try:
        import execute
        if execute._mode() == "LIVE":
            return 0.0
    except Exception:
        pass
    try:
        import portfolio
        total = 0.0
        for p in portfolio.open_positions():
            if str(p.get("side")).upper() == "BUY":
                total += float(p.get("entry") or 0) * float(p.get("qty") or 0)
        return round(total, 8)
    except Exception:
        return 0.0


def free() -> float:
    """Spendable, uncommitted cash. Never negative."""
    return max(0.0, round(_treasury_available() - committed(), 8))


def agent_available(agent_id: str, agent_capital: float) -> float:
    """What THIS agent may size: its own cap, bounded by the shared free pool."""
    return max(0.0, round(min(float(agent_capital), free()), 8))


def status() -> dict:
    avail = _treasury_available()
    com = committed()
    return {"real_available": round(avail, 2),
            "in_open_positions": round(com, 2),
            "free": free()}


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
